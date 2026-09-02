import json
import re
import time
from datetime import datetime, timedelta
from deepseek_summary import deepseek_summary, FLASH_MODEL
from stage_timer import stage
from market_symbols import (sina_symbol, normalize, strip_prefix,
                            is_etf_code as _shared_is_etf)
import pandas as pd
import numpy as np
from urllib3.exceptions import HTTPError
import requests

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False

_last_request_time = 0
_request_interval = 1.0

INDEX_CODE_MAP = {
    "000001": "000001.SS",
    "399001": "399001.SZ",
    "399006": "399006.SZ",
    "000688": "000688.SS",
    "000300": "000300.SS",
    "HSI": "^HSI",
}

def _convert_to_yfinance_code(code, is_index=False):
    if is_index:
        return INDEX_CODE_MAP.get(code, f"{code}.SS")
    if code.startswith("6"):
        return f"{code}.SS"
    elif code.startswith("0") or code.startswith("3"):
        return f"{code}.SZ"
    elif code.startswith("68"):
        return f"{code}.SS"
    elif code.startswith("5"):
        return f"{code}.SS"
    elif code.startswith("1"):
        return f"{code}.SZ"
    return f"{code}.SS"

def _wait_for_rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _request_interval:
        time.sleep(_request_interval - elapsed)
    _last_request_time = time.time()

def _clean_yfinance_df(df):
    """清洗yfinance返回的DataFrame，去除NaN行，统一列名"""
    if df is None or len(df) == 0:
        return None
    
    df = df.reset_index()
    df = df.rename(columns={
        'Date': '日期',
        'Open': '开盘',
        'High': '最高',
        'Low': '最低',
        'Close': '收盘',
        'Volume': '成交量'
    })
    
    df['日期'] = pd.to_datetime(df['日期'])
    
    df = df.dropna(subset=['收盘'])
    
    if len(df) == 0:
        return None
    
    df = df.sort_values('日期').reset_index(drop=True)
    return df

def _clean_akshare_df(df):
    """清洗akshare返回的DataFrame，兼容东财和新浪数据源"""
    if df is None or len(df) == 0:
        return None
    
    # 新浪数据源列名映射（英文→中文）
    sina_column_map = {
        'date': '日期',
        'open': '开盘',
        'high': '最高',
        'low': '最低',
        'close': '收盘',
        'volume': '成交量',
        'amount': '成交额',
        'turnover': '换手率',
    }
    
    for eng, chn in sina_column_map.items():
        if eng in df.columns and chn not in df.columns:
            df = df.rename(columns={eng: chn})
    
    # 新浪没有涨跌幅，用收盘价计算
    if '涨跌幅' not in df.columns and '收盘' in df.columns:
        df['涨跌幅'] = df['收盘'].pct_change() * 100
    
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.dropna(subset=['收盘'])
    df = df.sort_values('日期').reset_index(drop=True)
    return df


def parse_targets_from_text(text):
    """直接从文本中解析JSON格式的标的信息
    
    Args:
        text: 包含JSON格式标的信息的文本
    
    Returns:
        dict: 包含指数和股票列表的字典，解析失败返回None
    """
    if not text:
        return None
    
    json_patterns = [
        r'```json\s*([\s\S]*?)\s*```',
        r'```\s*([\s\S]*?)\s*```',
        r'\{[\s\S]*?"indices"[\s\S]*?"stocks"[\s\S]*?\}'
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                if pattern.startswith(r'\{'):
                    json_str = match if isinstance(match, str) else match
                else:
                    json_str = match
                
                result = json.loads(json_str)
                
                if 'indices' in result or 'stocks' in result:
                    if 'indices' not in result:
                        result['indices'] = []
                    if 'stocks' not in result:
                        result['stocks'] = []
                    
                    for idx in result.get('indices', []):
                        idx['code'] = normalize(idx.get('code', ''), 'index') or idx.get('code', '')
                        if 'reason' not in idx:
                            idx['reason'] = ''
                    for stock in result.get('stocks', []):
                        stock['code'] = normalize(stock.get('code', ''), 'stock') or stock.get('code', '')
                        if 'reason' not in stock:
                            stock['reason'] = ''
                    
                    return result
            except (json.JSONDecodeError, TypeError):
                continue
    
    return None


def extract_key_targets(investment_advice, source_name=""):
    """从投资建议中提取重点关注的指数和股票
    
    优先尝试直接解析文本中的JSON格式标的信息，
    如果失败则调用DeepSeek进行提取。
    
    Args:
        investment_advice: 投资建议文本
        source_name: 来源名称（如"B站"、"微信"、"微博"）
    
    Returns:
        dict: 包含指数和股票列表的字典
    """
    parsed = parse_targets_from_text(investment_advice)
    if parsed:
        if source_name:
            print(f"[{source_name}] 直接解析到 {len(parsed.get('indices', []))} 个指数, {len(parsed.get('stocks', []))} 只股票")
        return parsed
    
    print(f"[{source_name}] 未找到结构化标的信息，尝试使用DeepSeek提取...")
    
    try:
        result_text = deepseek_summary(
            investment_advice,
            sysprompt=(
                "你是一个金融数据NLP解析引擎，职责是从投资分析文本中精确提取出所有被提及的"
                "指数和股票标的，输出严格的机器可读JSON格式。\n\n"
                "提取规则：\n"
                "1. 只提取被明确提到名称或代码的指数和股票\n"
                "2. 对于指数：使用标准代码（上证000001/深证成指399001/创业板399006/科创50-000688/沪深300-000300/恒生HSI）\n"
                "3. 对于股票：必须是6位数字代码，从文本中查找或根据名称推断标准代码\n"
                "4. reason字段必须简要写明该标的在文本中被关注的理由（15字以内）\n"
                "5. 如果文本中确实没有明确提及任何标的，返回空列表\n\n"
                "输出格式（严格遵守，不要输出任何JSON之外的内容）：\n"
                '{"indices":[{"code":"000001","name":"上证指数","reason":"突破关键阻力位"}],"stocks":[{"code":"600519","name":"贵州茅台","reason":"业绩超预期"}]}'
            ),
            userprompt="请从以下投资分析中提取所有被提及的指数和股票标的，输出严格JSON格式：\n\n",
            thinking={"type": "disabled"},
            response_format={"type": "json_object"},
            temperature=0.05,
            max_tokens=4096,
            model=FLASH_MODEL,  # 标的提取为结构化轻量任务，用 flash
        )
        
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result = json.loads(json_match.group())
            for idx in result.get('indices', []):
                idx['code'] = normalize(idx.get('code', ''), 'index') or idx.get('code', '')
            for stock in result.get('stocks', []):
                stock['code'] = normalize(stock.get('code', ''), 'stock') or stock.get('code', '')
            if source_name:
                print(f"[{source_name}] DeepSeek提取到 {len(result.get('indices', []))} 个指数, {len(result.get('stocks', []))} 只股票")
            return result
        else:
            print(f"无法从响应中解析JSON: {result_text[:100]}...")
            return {"indices": [], "stocks": []}
            
    except json.JSONDecodeError as e:
        print(f"JSON解析错误: {e}")
        return {"indices": [], "stocks": []}
    except Exception as e:
        print(f"提取标的时出错: {e}")
        return {"indices": [], "stocks": []}


def merge_targets(all_targets):
    """合并多个来源的标的，去重
    
    Args:
        all_targets: 多个来源的标的列表
    
    Returns:
        dict: 合并后的标的
    """
    merged = {"indices": {}, "stocks": {}}
    
    for targets in all_targets:
        if not targets:
            continue
            
        for idx in targets.get("indices", []):
            # code 统一为规范代码（带前缀），避免后续模块各自猜 sh/sz
            code = normalize(idx.get("code", ""), 'index') or idx.get("code", "")
            if code:
                if code not in merged["indices"]:
                    merged["indices"][code] = {
                        "code": code,
                        "name": idx.get("name", ""),
                        "reasons": []
                    }
                if idx.get("reason"):
                    merged["indices"][code]["reasons"].append(idx.get("reason"))
        
        for stock in targets.get("stocks", []):
            code = normalize(stock.get("code", ""), 'stock') or stock.get("code", "")
            if code:
                if code not in merged["stocks"]:
                    merged["stocks"][code] = {
                        "code": code,
                        "name": stock.get("name", ""),
                        "reasons": []
                    }
                if stock.get("reason"):
                    merged["stocks"][code]["reasons"].append(stock.get("reason"))
    
    result = {
        "indices": list(merged["indices"].values()),
        "stocks": list(merged["stocks"].values())
    }
    
    print(f"合并后共 {len(result['indices'])} 个指数, {len(result['stocks'])} 只股票")
    return result


def _is_etf_code(code):
    """判断代码是否为ETF代码（沪深统一规则，见 market_symbols）"""
    return _shared_is_etf(code)

def get_index_kline(code, days=150, max_retries=3):
    """获取指数/ETF日K线数据（akshare/sina优先，yfinance备用）

    Args:
        code: 指数或ETF代码（6位或带 sh/sz 前缀的规范代码）
        days: 获取的天数
        max_retries: 最大重试次数

    Returns:
        DataFrame: K线数据
    """
    code = strip_prefix(code) or code  # 兼容规范代码(sh512880)入参
    is_etf = _is_etf_code(code)

    # 优先使用 akshare（新浪数据源，国内稳定），底层走 index_kline 本地持久缓存：
    # 当日15:30后直接读盘复用，跨日/盘中才重抓全量（新浪无按日期的增量接口）
    if AKSHARE_AVAILABLE:
        try:
            from index_kline import get_kline_since
            start_date = datetime.now() - timedelta(days=days)
            df = get_kline_since(code, start_date=start_date.strftime("%Y-%m-%d"))
            if df is not None and len(df) > 0:
                source_type = "ETF" if is_etf else "指数"
                print(f"akshare 获取{source_type} {code} 数据成功 ({len(df)}条)")
                return df
            print(f"akshare 获取指数 {code} 数据为空")
        except Exception as e:
            print(f"akshare 获取指数 {code} K线数据失败: {e}")

    # yfinance 备用（海外接口，偶发 database disk image is malformed）
    if YFINANCE_AVAILABLE:
        print(f"akshare 失败，尝试 yfinance 获取指数 {code} 数据...")
        for attempt in range(max_retries):
            try:
                yf_code = _convert_to_yfinance_code(code, is_index=(not is_etf))
                ticker = yf.Ticker(yf_code)
                end_date = datetime.now()
                start_date = end_date - timedelta(days=days)
                df = ticker.history(start=start_date, end=end_date)

                df = _clean_yfinance_df(df)
                if df is not None and len(df) > 0:
                    source_type = "ETF" if is_etf else "指数"
                    print(f"yfinance 获取{source_type} {code} 数据成功 ({len(df)}条)")
                    return df

                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"yfinance 获取指数 {code} 数据为空，{wait_time}秒后重试 ({attempt + 1}/{max_retries})...")
                    time.sleep(wait_time)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"yfinance 获取指数 {code} 失败，{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {e}")
                    time.sleep(wait_time)
                else:
                    print(f"yfinance 获取指数 {code} 数据失败: {e}")

    print(f"所有数据源均无法获取指数 {code} 数据")
    return None


def get_stock_kline(code, days=150, max_retries=3):
    """获取股票日K线数据（akshare/sina优先，yfinance备用）

    Args:
        code: 股票代码（6位或带 sh/sz 前缀的规范代码）
        days: 获取的天数
        max_retries: 最大重试次数

    Returns:
        DataFrame: K线数据
    """
    code = strip_prefix(code) or code  # 兼容规范代码(sh600519)入参
    # 跳过无效代码（现金持仓000000、非6位数字等）
    if not code or code == "000000" or len(code) != 6 or not code.isdigit():
        print(f"跳过无效股票代码: {code}")
        return None

    # 优先使用 akshare（新浪数据源，国内稳定）
    if AKSHARE_AVAILABLE:
        for attempt in range(max_retries):
            try:
                _wait_for_rate_limit()

                end_date = datetime.now().strftime("%Y%m%d")
                start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

                # 新浪 symbol 前缀走统一规则（market_symbols），北交所等不支持时转 yfinance
                sina_code = sina_symbol(code, "stock")
                if sina_code is None:
                    print(f"新浪不支持代码 {code}，转 yfinance 备用")
                    break
                df = ak.stock_zh_a_daily(
                    symbol=sina_code,
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq"
                )

                df = _clean_akshare_df(df)
                if df is not None and len(df) > 0:
                    print(f"akshare 获取股票 {code} 数据成功 ({len(df)}条)")
                    return df

                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"akshare 获取股票 {code} 数据为空，{wait_time}秒后重试 ({attempt + 1}/{max_retries})...")
                    time.sleep(wait_time)
            except Exception as e:
                error_msg = str(e)
                if 'Connection aborted' in error_msg or 'RemoteDisconnected' in error_msg:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2
                        print(f"akshare 获取股票 {code} 连接失败，{wait_time}秒后重试 ({attempt + 1}/{max_retries})...")
                        time.sleep(wait_time)
                        continue
                print(f"akshare 获取股票 {code} K线数据失败: {e}")
                break

    # yfinance 备用（海外接口，偶发 database disk image is malformed）
    if YFINANCE_AVAILABLE:
        print(f"akshare 失败，尝试 yfinance 获取股票 {code} 数据...")
        for attempt in range(max_retries):
            try:
                yf_code = _convert_to_yfinance_code(code, is_index=False)
                ticker = yf.Ticker(yf_code)
                end_date = datetime.now()
                start_date = end_date - timedelta(days=days)
                df = ticker.history(start=start_date, end=end_date)

                df = _clean_yfinance_df(df)
                if df is not None and len(df) > 0:
                    print(f"yfinance 获取股票 {code} 数据成功 ({len(df)}条)")
                    return df

                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"yfinance 获取股票 {code} 数据为空，{wait_time}秒后重试 ({attempt + 1}/{max_retries})...")
                    time.sleep(wait_time)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"yfinance 获取股票 {code} 失败，{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {e}")
                    time.sleep(wait_time)
                else:
                    print(f"yfinance 获取股票 {code} 数据失败: {e}")

    print(f"所有数据源均无法获取股票 {code} 数据")
    return None


def check_breakout(df, window=20):
    """检测突破信号
    
    检测当前价格是否突破N日新高
    
    Args:
        df: K线数据
        window: 突破窗口期，默认20日
    
    Returns:
        dict: 突破信号结果
    """
    if df is None or len(df) < window:
        return None
    
    close = df['收盘'].values
    high = df['最高'].values if '最高' in df.columns else close
    low = df['最低'].values if '最低' in df.columns else close
    volume = df['成交量'].values if '成交量' in df.columns else None

    current_price = close[-1]
    current_high = high[-1]
    current_low = low[-1]

    period_high = np.max(high[-window-1:-1])
    period_low = np.min(low[-window-1:-1])

    is_new_high = current_price >= period_high
    is_new_low = current_price <= period_low

    distance_to_high = round((current_price / period_high - 1) * 100, 2)
    distance_to_low = round((current_price / period_low - 1) * 100, 2)

    days_since_high = 0
    for i in range(len(close) - 2, max(len(close) - window - 1, -1), -1):
        if high[i] >= period_high:
            days_since_high = len(close) - 1 - i
            break

    days_since_low = 0
    for i in range(len(close) - 2, max(len(close) - window - 1, -1), -1):
        if low[i] <= period_low:
            days_since_low = len(close) - 1 - i
            break

    # 量能配合判断：当前成交量 vs 过去20日平均成交量
    volume_ratio = None
    volume_confirmed = False
    if volume is not None and len(volume) >= window + 1:
        avg_volume = np.mean(volume[-window-1:-1])
        if avg_volume > 0:
            volume_ratio = round(float(volume[-1]) / float(avg_volume), 2)
            # 量比 > 1.5 视为放量配合
            volume_confirmed = volume_ratio >= 1.5

    breakout_signal = None
    if is_new_high:
        breakout_signal = "20日新高突破" + ("（放量配合）" if volume_confirmed else "（量能不足）")
    elif is_new_low:
        breakout_signal = "20日新低突破" + ("（放量下杀）" if volume_confirmed else "（缩量下杀）")

    return {
        "is_new_high": is_new_high,
        "is_new_low": is_new_low,
        "period_high": round(period_high, 2),
        "period_low": round(period_low, 2),
        "distance_to_high": distance_to_high,
        "distance_to_low": distance_to_low,
        "days_since_high": days_since_high,
        "days_since_low": days_since_low,
        "volume_ratio": volume_ratio,
        "volume_confirmed": volume_confirmed,
        "breakout_signal": breakout_signal
    }


def calculate_technical_indicators(df):
    """计算常用技术指标
    
    包含：RSI(14)、MACD(12,26,9)、ATR(14)、量比、换手率代理指标
    
    Args:
        df: K线数据DataFrame
    
    Returns:
        dict: 技术指标结果
    """
    if df is None or len(df) < 35:
        return None
    
    close = df['收盘'].values.astype(float)
    high = df['最高'].values.astype(float) if '最高' in df.columns else close
    low = df['最低'].values.astype(float) if '最低' in df.columns else close
    volume = df['成交量'].values.astype(float) if '成交量' in df.columns else None
    
    indicators = {}
    
    # 1. RSI(14)
    if len(close) >= 15:
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        # 使用Wilder平滑法
        avg_gain = np.mean(gains[:14])
        avg_loss = np.mean(losses[:14])
        
        for i in range(14, len(deltas)):
            avg_gain = (avg_gain * 13 + gains[i]) / 14
            avg_loss = (avg_loss * 13 + losses[i]) / 14
        
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        indicators['rsi_14'] = round(rsi, 2)
        if rsi >= 70:
            indicators['rsi_signal'] = "超买"
        elif rsi <= 30:
            indicators['rsi_signal'] = "超卖"
        else:
            indicators['rsi_signal'] = "中性"
    
    # 2. MACD(12,26,9)
    if len(close) >= 35:
        ema12 = _calculate_ema(close, 12)
        ema26 = _calculate_ema(close, 26)
        dif = ema12 - ema26
        dea = _calculate_ema(dif, 9)
        macd_hist = (dif - dea) * 2
        
        indicators['macd_dif'] = round(float(dif[-1]), 4)
        indicators['macd_dea'] = round(float(dea[-1]), 4)
        indicators['macd_hist'] = round(float(macd_hist[-1]), 4)
        
        if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
            indicators['macd_signal'] = "金叉（看多）"
        elif dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
            indicators['macd_signal'] = "死叉（看空）"
        elif dif[-1] > dea[-1]:
            indicators['macd_signal'] = "多头排列"
        else:
            indicators['macd_signal'] = "空头排列"
    
    # 3. ATR(14) - 用于止损建议
    if len(close) >= 15 and len(high) == len(close) and len(low) == len(close):
        tr_list = []
        for i in range(1, len(close)):
            tr = max(
                high[i] - low[i],
                abs(high[i] - close[i-1]),
                abs(low[i] - close[i-1])
            )
            tr_list.append(tr)
        
        # Wilder平滑
        atr = np.mean(tr_list[:14])
        for i in range(14, len(tr_list)):
            atr = (atr * 13 + tr_list[i]) / 14
        
        indicators['atr_14'] = round(float(atr), 4)
        indicators['atr_pct'] = round(float(atr / close[-1] * 100), 2)
        # 建议止损距离 = 1.5倍ATR
        indicators['suggested_stop_distance'] = round(float(atr * 1.5), 2)
        indicators['suggested_stop_pct'] = round(float(atr * 1.5 / close[-1] * 100), 2)
    
    # 4. 量比 = 今日成交量 / 过去20日平均成交量
    if volume is not None and len(volume) >= 21:
        avg_vol_20 = np.mean(volume[-21:-1])
        if avg_vol_20 > 0:
            volume_ratio = float(volume[-1]) / avg_vol_20
            indicators['volume_ratio'] = round(volume_ratio, 2)
            if volume_ratio >= 2.0:
                indicators['volume_signal'] = "显著放量"
            elif volume_ratio >= 1.5:
                indicators['volume_signal'] = "放量"
            elif volume_ratio >= 0.7:
                indicators['volume_signal'] = "正常"
            else:
                indicators['volume_signal'] = "缩量"
    
    # 5. KDJ(9,3,3)
    if len(close) >= 9 and len(high) == len(close) and len(low) == len(close):
        k_value, d_value, j_value = _calculate_kdj(high, low, close, 9, 3, 3)
        if k_value is not None:
            indicators['kdj_k'] = round(k_value, 2)
            indicators['kdj_d'] = round(d_value, 2)
            indicators['kdj_j'] = round(j_value, 2)
            if j_value > 100:
                indicators['kdj_signal'] = "超买"
            elif j_value < 0:
                indicators['kdj_signal'] = "超卖"
            elif k_value > d_value:
                indicators['kdj_signal'] = "金叉偏多"
            else:
                indicators['kdj_signal'] = "死叉偏空"
    
    return indicators


def _calculate_ema(data, period):
    """计算指数移动平均"""
    data = np.array(data, dtype=float)
    ema = np.zeros_like(data)
    multiplier = 2 / (period + 1)
    ema[0] = data[0]
    for i in range(1, len(data)):
        ema[i] = data[i] * multiplier + ema[i-1] * (1 - multiplier)
    return ema


def _calculate_kdj(high, low, close, n=9, m1=3, m2=3):
    """计算KDJ指标"""
    if len(close) < n:
        return None, None, None
    
    k_values = []
    d_values = []
    
    for i in range(n - 1, len(close)):
        period_high = max(high[i-n+1:i+1])
        period_low = min(low[i-n+1:i+1])
        if period_high == period_low:
            rsv = 50
        else:
            rsv = (close[i] - period_low) / (period_high - period_low) * 100
        
        if i == n - 1:
            k = 50
            d = 50
        k = (m1 - 1) / m1 * k + 1 / m1 * rsv
        d = (m2 - 1) / m2 * d + 1 / m2 * k
        k_values.append(k)
        d_values.append(d)
    
    if not k_values:
        return None, None, None
    
    j = 3 * k_values[-1] - 2 * d_values[-1]
    return k_values[-1], d_values[-1], j


def calculate_support_resistance(df, lookback=60, swing_window=5):
    """计算支撑/阻力位
    
    综合三类关键价位：
    1. 近N日 swing high/low（波段高低点）
    2. 整数关口（心理价位）
    3. 近端缺口（跳空未回补）
    
    Args:
        df: K线数据
        lookback: 回溯天数，默认60日
        swing_window: 波段点识别窗口，默认5日
    
    Returns:
        dict: 支撑/阻力位结果
    """
    if df is None or len(df) < swing_window * 2 + 1:
        return None
    
    close = df['收盘'].values
    high = df['最高'].values if '最高' in df.columns else close
    low = df['最低'].values if '最低' in df.columns else close
    current_price = close[-1]
    
    lookback = min(lookback, len(close))
    recent_high = high[-lookback:]
    recent_low = low[-lookback:]
    
    # 1. 识别波段高低点（Fractal Swing Points）
    swing_highs = []
    swing_lows = []
    for i in range(swing_window, len(recent_high) - swing_window):
        is_swing_high = all(recent_high[i] >= recent_high[i-j] for j in range(1, swing_window+1)) and \
                        all(recent_high[i] >= recent_high[i+j] for j in range(1, swing_window+1))
        is_swing_low = all(recent_low[i] <= recent_low[i-j] for j in range(1, swing_window+1)) and \
                       all(recent_low[i] <= recent_low[i+j] for j in range(1, swing_window+1))
        if is_swing_high:
            swing_highs.append(round(float(recent_high[i]), 2))
        if is_swing_low:
            swing_lows.append(round(float(recent_low[i]), 2))
    
    # 去重并排序
    swing_highs = sorted(set(swing_highs), reverse=True)
    swing_lows = sorted(set(swing_lows))
    
    # 2. 整数关口（最近的整数价位）
    def nearest_round_numbers(price, count=3):
        levels = []
        step = 1 if price < 100 else (10 if price < 1000 else (100 if price < 10000 else 1000))
        base = int(price / step) * step
        for offset in range(-count, count+1):
            level = base + offset * step
            if level > 0 and level != price:
                levels.append(level)
        return sorted(levels)
    
    round_levels = nearest_round_numbers(current_price)
    
    # 3. 近端缺口（最近20日内未回补的跳空）
    gaps = []
    gap_lookback = min(20, len(close) - 1)
    for i in range(-gap_lookback, -1):
        idx = len(close) + i
        prev_high = high[idx-1]
        prev_low = low[idx-1]
        curr_low = low[idx]
        curr_high = high[idx]
        # 向上缺口：今日最低 > 昨日最高
        if curr_low > prev_high:
            gaps.append({
                "type": "向上缺口",
                "date": str(df['日期'].iloc[idx]) if '日期' in df.columns else "",
                "upper": round(float(prev_high), 2),
                "lower": round(float(curr_low), 2)
            })
        # 向下缺口：今日最高 < 昨日最低
        elif curr_high < prev_low:
            gaps.append({
                "type": "向下缺口",
                "date": str(df['日期'].iloc[idx]) if '日期' in df.columns else "",
                "upper": round(float(curr_high), 2),
                "lower": round(float(prev_low), 2)
            })
    
    # 阻力位：高于当前价的波段高点 + 整数关口 + 缺口上沿
    resistance_levels = []
    for sh in swing_highs:
        if sh > current_price:
            resistance_levels.append({"price": sh, "type": "波段高点"})
    for level in round_levels:
        if level > current_price:
            resistance_levels.append({"price": level, "type": "整数关口"})
    for gap in gaps:
        if gap["type"] == "向下缺口" and gap["upper"] > current_price:
            resistance_levels.append({"price": gap["upper"], "type": f"缺口阻力({gap['date'][:10]})"})
    
    # 支撑位：低于当前价的波段低点 + 整数关口 + 缺口下沿
    support_levels = []
    for sl in swing_lows:
        if sl < current_price:
            support_levels.append({"price": sl, "type": "波段低点"})
    for level in round_levels:
        if level < current_price:
            support_levels.append({"price": level, "type": "整数关口"})
    for gap in gaps:
        if gap["type"] == "向上缺口" and gap["lower"] < current_price:
            support_levels.append({"price": gap["lower"], "type": f"缺口支撑({gap['date'][:10]})"})
    
    # 去重并按距离当前价排序，取最近的各3个
    seen_r = set()
    unique_resistance = []
    for r in sorted(resistance_levels, key=lambda x: x["price"]):
        if r["price"] not in seen_r:
            seen_r.add(r["price"])
            unique_resistance.append(r)
    unique_resistance = unique_resistance[:3]
    
    seen_s = set()
    unique_support = []
    for s in sorted(support_levels, key=lambda x: x["price"], reverse=True):
        if s["price"] not in seen_s:
            seen_s.add(s["price"])
            unique_support.append(s)
    unique_support = unique_support[:3]
    
    return {
        "current_price": round(float(current_price), 2),
        "resistance_levels": unique_resistance,
        "support_levels": unique_support,
        "recent_gaps": gaps[-3:] if gaps else []
    }


def calculate_momentum_factors(df):
    """计算动量因子
    
    Args:
        df: K线数据DataFrame，需包含'收盘'列
    
    Returns:
        dict: 动量因子结果
    """
    if df is None or len(df) < 20:
        return None
    
    close = df['收盘'].values
    
    factors = {}
    
    if len(close) >= 20:
        ret_20 = (close[-1] / close[-21] - 1) * 100
        factors['return_20d'] = round(ret_20, 2)
    else:
        factors['return_20d'] = None
    
    if len(close) >= 60:
        ret_60 = (close[-1] / close[-61] - 1) * 100
        factors['return_60d'] = round(ret_60, 2)
    else:
        factors['return_60d'] = None
    
    if len(close) >= 120:
        ret_120 = (close[-1] / close[-121] - 1) * 100
        factors['return_120d'] = round(ret_120, 2)
    else:
        factors['return_120d'] = None
    
    factors['trend_strength'] = calculate_trend_strength(df)
    
    factors['breakout'] = check_breakout(df, window=20)
    
    factors['support_resistance'] = calculate_support_resistance(df)
    
    factors['technical_indicators'] = calculate_technical_indicators(df)
    
    return factors


def calculate_trend_strength(df, window=20):
    """计算趋势强度
    
    使用多个指标综合评估：
    1. 价格相对于均线的位置
    2. 均线多头/空头排列
    3. 价格连续上涨/下跌天数
    4. 波动率
    
    Args:
        df: K线数据
        window: 计算窗口
    
    Returns:
        dict: 趋势强度评估结果
    """
    if df is None or len(df) < window:
        return None
    
    close = df['收盘'].values
    
    ma5 = np.mean(close[-5:])
    ma10 = np.mean(close[-10:])
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:]) if len(close) >= 60 else ma20
    current_price = close[-1]
    
    ma_position_score = 0
    if current_price > ma5:
        ma_position_score += 1
    if current_price > ma10:
        ma_position_score += 1
    if current_price > ma20:
        ma_position_score += 1
    if current_price > ma60:
        ma_position_score += 1
    ma_position_score = ma_position_score / 4 * 100
    
    ma_alignment_score = 0
    if ma5 > ma10:
        ma_alignment_score += 1
    if ma10 > ma20:
        ma_alignment_score += 1
    if ma20 > ma60:
        ma_alignment_score += 1
    ma_alignment_score = ma_alignment_score / 3 * 100
    
    consecutive_days = 0
    for i in range(len(close) - 1, 0, -1):
        if close[i] > close[i-1]:
            if consecutive_days >= 0:
                consecutive_days += 1
            else:
                break
        elif close[i] < close[i-1]:
            if consecutive_days <= 0:
                consecutive_days -= 1
            else:
                break
        else:
            break
    
    returns = np.diff(close[-window:]) / close[-window:-1]
    volatility = np.std(returns) * np.sqrt(252) * 100
    
    trend_direction = "上涨" if consecutive_days > 0 else "下跌" if consecutive_days < 0 else "震荡"
    
    overall_strength = (ma_position_score * 0.4 + ma_alignment_score * 0.4 + min(abs(consecutive_days) * 10, 100) * 0.2)
    
    if overall_strength >= 70:
        trend_level = "强势"
    elif overall_strength >= 50:
        trend_level = "中等"
    elif overall_strength >= 30:
        trend_level = "弱势"
    else:
        trend_level = "无明显趋势"
    
    return {
        "trend_direction": trend_direction,
        "trend_level": trend_level,
        "overall_strength": round(overall_strength, 1),
        "ma_position_score": round(ma_position_score, 1),
        "ma_alignment_score": round(ma_alignment_score, 1),
        "consecutive_days": consecutive_days,
        "volatility": round(volatility, 2)
    }


def analyze_targets(targets):
    """分析所有标的的动量因子
    
    Args:
        targets: 包含指数和股票的字典
    
    Returns:
        dict: 分析结果
    """
    results = {
        "indices": [],
        "stocks": [],
        "analysis_date": datetime.now().strftime("%Y-%m-%d")
    }
    
    for idx in targets.get("indices", []):
        code = idx.get("code", "")
        name = idx.get("name", "")
        reasons = idx.get("reasons", [])
        
        print(f"正在分析指数: {name}({code})")
        with stage(f"动量K线 指数 {name}", group="动量-指数K线抓取"):
            df = get_index_kline(code)
        
        if df is not None:
            factors = calculate_momentum_factors(df)
            if factors:
                # 当日涨跌幅和成交量
                daily_change = 0.0
                volume = None
                if len(df) >= 2:
                    close_vals = df['收盘'].values
                    daily_change = float((close_vals[-1] - close_vals[-2]) / close_vals[-2] * 100)
                if '成交量' in df.columns and len(df) > 0:
                    volume = float(df['成交量'].iloc[-1])
                
                results["indices"].append({
                    "code": code,
                    "name": name,
                    "reasons": reasons,
                    "latest_price": float(df['收盘'].iloc[-1]),
                    "daily_change_pct": round(daily_change, 2),
                    "volume": volume,
                    "momentum_factors": factors
                })
                print(f"  - 20日收益率: {factors['return_20d']}%")
                print(f"  - 60日收益率: {factors['return_60d']}%")
                print(f"  - 120日收益率: {factors['return_120d']}%")
                if factors['trend_strength']:
                    print(f"  - 趋势强度: {factors['trend_strength']['trend_level']} ({factors['trend_strength']['overall_strength']})")
                if factors.get('breakout') and factors['breakout'].get('is_new_high'):
                    print(f"  ★★★ 20日新高突破! 当前价: {df['收盘'].iloc[-1]:.2f}, 20日高点: {factors['breakout']['period_high']}")
        else:
            print(f"  - 无法获取数据")
    
    for stock in targets.get("stocks", []):
        code = stock.get("code", "")
        name = stock.get("name", "")
        reasons = stock.get("reasons", [])
        
        print(f"正在分析股票: {name}({code})")
        with stage(f"动量K线 {name}({code})", group="动量-个股K线抓取"):
            df = get_stock_kline(code)
        
        if df is not None:
            factors = calculate_momentum_factors(df)
            if factors:
                # 当日涨跌幅和成交量
                daily_change = 0.0
                volume = None
                if len(df) >= 2:
                    close_vals = df['收盘'].values
                    daily_change = float((close_vals[-1] - close_vals[-2]) / close_vals[-2] * 100)
                if '成交量' in df.columns and len(df) > 0:
                    volume = float(df['成交量'].iloc[-1])
                
                results["stocks"].append({
                    "code": code,
                    "name": name,
                    "reasons": reasons,
                    "latest_price": float(df['收盘'].iloc[-1]),
                    "daily_change_pct": round(daily_change, 2),
                    "volume": volume,
                    "momentum_factors": factors
                })
                print(f"  - 20日收益率: {factors['return_20d']}%")
                print(f"  - 60日收益率: {factors['return_60d']}%")
                print(f"  - 120日收益率: {factors['return_120d']}%")
                if factors['trend_strength']:
                    print(f"  - 趋势强度: {factors['trend_strength']['trend_level']} ({factors['trend_strength']['overall_strength']})")
                if factors.get('breakout') and factors['breakout'].get('is_new_high'):
                    print(f"  ★★★ 20日新高突破! 当前价: {df['收盘'].iloc[-1]:.2f}, 20日高点: {factors['breakout']['period_high']}")
        else:
            print(f"  - 无法获取数据")
    
    return results


def format_momentum_report(results):
    """格式化动量分析报告
    
    Args:
        results: 分析结果
    
    Returns:
        str: 格式化的报告文本
    """
    report_lines = []
    report_lines.append("=" * 60)
    report_lines.append("重点关注标的动量分析报告")
    report_lines.append(f"分析日期: {results['analysis_date']}")
    report_lines.append("=" * 60)
    
    breakout_items = []
    
    if results["indices"]:
        report_lines.append("\n【指数分析】")
        report_lines.append("-" * 40)
        for idx in results["indices"]:
            report_lines.append(f"\n{idx['name']}({idx['code']})")
            report_lines.append(f"  最新价格: {idx['latest_price']:.2f}")
            report_lines.append(f"  当日涨跌幅: {idx['daily_change_pct']:+.2f}%")
            if idx.get('volume') is not None:
                vol = idx['volume']
                if vol >= 1e8:
                    vol_str = f"{vol/1e8:.2f}亿"
                elif vol >= 1e4:
                    vol_str = f"{vol/1e4:.2f}万"
                else:
                    vol_str = f"{vol:.0f}"
                report_lines.append(f"  成交量: {vol_str}")
            factors = idx['momentum_factors']
            report_lines.append(f"  20日收益率: {factors['return_20d']}%")
            report_lines.append(f"  60日收益率: {factors['return_60d']}%")
            report_lines.append(f"  120日收益率: {factors['return_120d']}%")
            if factors['trend_strength']:
                ts = factors['trend_strength']
                report_lines.append(f"  趋势方向: {ts['trend_direction']}")
                report_lines.append(f"  趋势强度: {ts['trend_level']} (综合评分: {ts['overall_strength']})")
                report_lines.append(f"  均线位置评分: {ts['ma_position_score']}")
                report_lines.append(f"  均线排列评分: {ts['ma_alignment_score']}")
                report_lines.append(f"  波动率: {ts['volatility']}%")
            if factors.get('breakout'):
                bo = factors['breakout']
                report_lines.append(f"  20日高点: {bo['period_high']}")
                report_lines.append(f"  20日低点: {bo['period_low']}")
                report_lines.append(f"  距离20日高点: {bo['distance_to_high']}%")
                report_lines.append(f"  距离20日低点: {bo['distance_to_low']}%")
                if bo.get('volume_ratio'):
                    report_lines.append(f"  量比: {bo['volume_ratio']}")
                if bo['is_new_high'] or bo['is_new_low']:
                    report_lines.append(f"  ★★★ 突破信号: {bo['breakout_signal']} ★★★")
                    breakout_items.append({
                        "type": "指数",
                        "name": idx['name'],
                        "code": idx['code'],
                        "price": idx['latest_price'],
                        "signal": bo['breakout_signal']
                    })
            if factors.get('support_resistance'):
                sr = factors['support_resistance']
                if sr.get('resistance_levels'):
                    _resist = ', '.join(f"{r['price']}({r['type']})" for r in sr['resistance_levels'])
                    report_lines.append(f"  阻力位: {_resist}")
                if sr.get('support_levels'):
                    _support = ', '.join(f"{s['price']}({s['type']})" for s in sr['support_levels'])
                    report_lines.append(f"  支撑位: {_support}")
                if sr.get('recent_gaps'):
                    for gap in sr['recent_gaps']:
                        report_lines.append(f"  {gap['type']}: {gap['lower']}-{gap['upper']} ({gap['date'][:10]})")
            if factors.get('technical_indicators'):
                ti = factors['technical_indicators']
                if 'rsi_14' in ti:
                    report_lines.append(f"  RSI(14): {ti['rsi_14']} ({ti['rsi_signal']})")
                if 'macd_dif' in ti:
                    report_lines.append(f"  MACD: DIF={ti['macd_dif']} DEA={ti['macd_dea']} ({ti['macd_signal']})")
                if 'atr_14' in ti:
                    report_lines.append(f"  ATR(14): {ti['atr_14']} ({ti['atr_pct']}%) | 建议止损距离: {ti['suggested_stop_distance']} ({ti['suggested_stop_pct']}%)")
                if 'volume_ratio' in ti:
                    report_lines.append(f"  量比: {ti['volume_ratio']} ({ti['volume_signal']})")
                if 'kdj_k' in ti:
                    report_lines.append(f"  KDJ: K={ti['kdj_k']} D={ti['kdj_d']} J={ti['kdj_j']} ({ti['kdj_signal']})")
            if idx['reasons']:
                report_lines.append(f"  关注原因: {'; '.join(idx['reasons'])}")
    
    if results["stocks"]:
        report_lines.append("\n【股票分析】")
        report_lines.append("-" * 40)
        for stock in results["stocks"]:
            report_lines.append(f"\n{stock['name']}({stock['code']})")
            report_lines.append(f"  最新价格: {stock['latest_price']:.2f}")
            report_lines.append(f"  当日涨跌幅: {stock['daily_change_pct']:+.2f}%")
            if stock.get('volume') is not None:
                vol = stock['volume']
                if vol >= 1e8:
                    vol_str = f"{vol/1e8:.2f}亿"
                elif vol >= 1e4:
                    vol_str = f"{vol/1e4:.2f}万"
                else:
                    vol_str = f"{vol:.0f}"
                report_lines.append(f"  成交量: {vol_str}")
            factors = stock['momentum_factors']
            report_lines.append(f"  20日收益率: {factors['return_20d']}%")
            report_lines.append(f"  60日收益率: {factors['return_60d']}%")
            report_lines.append(f"  120日收益率: {factors['return_120d']}%")
            if factors['trend_strength']:
                ts = factors['trend_strength']
                report_lines.append(f"  趋势方向: {ts['trend_direction']}")
                report_lines.append(f"  趋势强度: {ts['trend_level']} (综合评分: {ts['overall_strength']})")
                report_lines.append(f"  均线位置评分: {ts['ma_position_score']}")
                report_lines.append(f"  均线排列评分: {ts['ma_alignment_score']}")
                report_lines.append(f"  波动率: {ts['volatility']}%")
            if factors.get('breakout'):
                bo = factors['breakout']
                report_lines.append(f"  20日高点: {bo['period_high']}")
                report_lines.append(f"  20日低点: {bo['period_low']}")
                report_lines.append(f"  距离20日高点: {bo['distance_to_high']}%")
                report_lines.append(f"  距离20日低点: {bo['distance_to_low']}%")
                if bo.get('volume_ratio'):
                    report_lines.append(f"  量比: {bo['volume_ratio']}")
                if bo['is_new_high'] or bo['is_new_low']:
                    report_lines.append(f"  ★★★ 突破信号: {bo['breakout_signal']} ★★★")
                    breakout_items.append({
                        "type": "股票",
                        "name": stock['name'],
                        "code": stock['code'],
                        "price": stock['latest_price'],
                        "signal": bo['breakout_signal']
                    })
            if factors.get('support_resistance'):
                sr = factors['support_resistance']
                if sr.get('resistance_levels'):
                    _resist = ', '.join(f"{r['price']}({r['type']})" for r in sr['resistance_levels'])
                    report_lines.append(f"  阻力位: {_resist}")
                if sr.get('support_levels'):
                    _support = ', '.join(f"{s['price']}({s['type']})" for s in sr['support_levels'])
                    report_lines.append(f"  支撑位: {_support}")
                if sr.get('recent_gaps'):
                    for gap in sr['recent_gaps']:
                        report_lines.append(f"  {gap['type']}: {gap['lower']}-{gap['upper']} ({gap['date'][:10]})")
            if factors.get('technical_indicators'):
                ti = factors['technical_indicators']
                if 'rsi_14' in ti:
                    report_lines.append(f"  RSI(14): {ti['rsi_14']} ({ti['rsi_signal']})")
                if 'macd_dif' in ti:
                    report_lines.append(f"  MACD: DIF={ti['macd_dif']} DEA={ti['macd_dea']} ({ti['macd_signal']})")
                if 'atr_14' in ti:
                    report_lines.append(f"  ATR(14): {ti['atr_14']} ({ti['atr_pct']}%) | 建议止损距离: {ti['suggested_stop_distance']} ({ti['suggested_stop_pct']}%)")
                if 'volume_ratio' in ti:
                    report_lines.append(f"  量比: {ti['volume_ratio']} ({ti['volume_signal']})")
                if 'kdj_k' in ti:
                    report_lines.append(f"  KDJ: K={ti['kdj_k']} D={ti['kdj_d']} J={ti['kdj_j']} ({ti['kdj_signal']})")
            if stock['reasons']:
                report_lines.append(f"  关注原因: {'; '.join(stock['reasons'])}")
    
    if breakout_items:
        report_lines.append("\n" + "=" * 60)
        report_lines.append("【突破信号汇总】")
        report_lines.append("-" * 40)
        for item in breakout_items:
            report_lines.append(f"  ★ {item['type']}: {item['name']}({item['code']}) - {item['signal']} - 当前价: {item['price']:.2f}")
    
    report_lines.append("\n" + "=" * 60)
    return "\n".join(report_lines)


def run_momentum_analysis(bili_advice=None, wechat_advice=None, weibo_advice=None):
    """运行完整的动量分析流程
    
    Args:
        bili_advice: B站投资建议
        wechat_advice: 微信投资建议
        weibo_advice: 微博投资建议
    
    Returns:
        tuple: (动量分析报告文本, 分析结果字典)
    """
    print("\n" + "=" * 50)
    print("开始提取重点关注标的")
    print("=" * 50)
    
    all_targets = []
    
    if bili_advice:
        targets = extract_key_targets(bili_advice, "B站")
        all_targets.append(targets)
    
    if wechat_advice:
        targets = extract_key_targets(wechat_advice, "微信")
        all_targets.append(targets)
    
    if weibo_advice:
        targets = extract_key_targets(weibo_advice, "微博")
        all_targets.append(targets)
    
    merged_targets = merge_targets(all_targets)
    
    if not merged_targets["indices"] and not merged_targets["stocks"]:
        print("未提取到任何关注标的，跳过动量分析")
        return None, None
    
    print("\n" + "=" * 50)
    print("开始动量因子分析")
    print("=" * 50)
    
    results = analyze_targets(merged_targets)
    
    report = format_momentum_report(results)
    
    return report, results


# ============================================================
# 个股 vs 所属行业 相对强弱（Relative Strength）
# ============================================================

# 巨潮证监会行业分类名 → 板块名 桥接（两者无子串关系，模糊匹配无法覆盖）
# 目标名取自同花顺行业板块（已验证存在）；东财不可用时由 get_industry_kline 降级同花顺
_INDUSTRY_ALIAS = {
    "住宿业": "旅游及酒店",
    "住宿和餐饮业": "旅游及酒店",
    "货币金融服务": "银行",
    "资本市场服务": "证券",
    "保险业": "保险",
    "软件和信息技术服务业": "软件开发",
    "互联网和相关服务": "互联网电商",
    "铁路运输业": "公路铁路运输",
    "航空运输业": "机场航运",
    "水上运输业": "港口航运",
    "零售业": "零售",
}


def _match_industry(industry, all_names):
    """在行业名列表中做匹配，返回最具体匹配名（未匹配返回原行业名）

    匹配顺序：
    1. 巨潮证监会行业名桥接别名（如 "住宿业" → "旅游及酒店"）
    2. 精确匹配
    3. 行业名包含输入子串（如 "农" → "种植业"、"农产品加工"）
    4. 输入包含行业名子串
    多个匹配时取最短名（最具体）。
    """
    if not industry:
        return industry
    # 1. 桥接别名（优先）
    if industry in _INDUSTRY_ALIAS:
        return _INDUSTRY_ALIAS[industry]
    # 2. 精确匹配
    if industry in all_names:
        return industry
    # 3. 子串匹配（双向）
    candidates = []
    for name in all_names:
        if not name:
            continue
        if industry in name or name in industry:
            candidates.append(name)
    if candidates:
        # 取最短名（最具体）
        return min(candidates, key=len)
    return industry


def get_stock_industry_em(industry):
    """将任意来源的行业名映射为板块名（优先同花顺，东财备用）

    巨潮行业名（如"农业"）与板块名（如"种植业"）不一致会导致
    K线查询失败。本函数用全行业列表做模糊匹配。

    Args:
        industry: 任意来源的行业名

    Returns:
        str: 匹配到的行业名（未匹配返回原 industry）
    """
    if not AKSHARE_AVAILABLE or not industry:
        return industry

    # 方案1：同花顺行业列表（东财接口不可用，同花顺优先）
    try:
        df = ak.stock_board_industry_name_ths()
        if df is not None and not df.empty and "name" in df.columns:
            return _match_industry(industry, df["name"].astype(str).tolist())
    except Exception as e:
        print(f"  同花顺行业匹配失败: {e}")

    # 方案2：东财行业列表
    try:
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty and "板块名称" in df.columns:
            return _match_industry(industry, df["板块名称"].astype(str).tolist())
    except Exception as e:
        print(f"  东财行业匹配失败: {e}")

    return industry


def get_industry_kline(industry_name, days=30):
    """获取行业板块日K线（优先同花顺，东财备用）

    Args:
        industry_name: 行业名称（如 "电力"、"种植业"），需与板块分类一致
        days: 获取的日历日天数（实际交易日约2/3）

    Returns:
        DataFrame 或 None，列含 '日期' '收盘' 等
    """
    if not AKSHARE_AVAILABLE:
        return None
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    # 方案1：同花顺行业K线（东财接口不可用，同花顺优先）
    try:
        df = ak.stock_board_industry_index_ths(
            symbol=industry_name, start_date=start_date, end_date=end_date
        )
        if df is not None and not df.empty:
            # 归一化列名：同花顺用"收盘价"，后续逻辑统一用"收盘"
            if "收盘价" in df.columns and "收盘" not in df.columns:
                df = df.rename(columns={"收盘价": "收盘"})
            return df
    except Exception as e:
        print(f"  同花顺获取行业 {industry_name} K线失败: {e}")

    # 方案2：东财行业K线（接口偶发超时，3次指数退避重试）
    for attempt in range(3):
        try:
            df = ak.stock_board_industry_hist_em(
                symbol=industry_name,
                period="日k",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            if df is not None and not df.empty:
                return df
            return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 2
                print(f"  获取行业 {industry_name} K线失败（第{attempt+1}次），{wait}秒后重试: {e}")
                time.sleep(wait)
            else:
                print(f"  东财获取行业 {industry_name} K线失败: {e}")
    return None


def _calc_period_pct(df, days):
    """计算最近N日涨跌幅（基于收盘价）"""
    if df is None or len(df) < days + 1:
        return None
    close = df["收盘"].values.astype(float)
    end = close[-1]
    start = close[-1 - days]
    if start == 0:
        return None
    return round((end / start - 1) * 100, 2)


def calculate_relative_strength(stock_code, industry_name, periods=(5, 20)):
    """计算个股相对所属行业的相对强弱

    RS = 个股N日涨幅 - 行业N日涨幅

    Args:
        stock_code: 股票代码
        industry_name: 行业名称
        periods: 计算的周期列表（默认5日和20日）

    Returns:
        dict 或 None
    """
    # 加大到周期3倍+10，确保能算出最长周期数据（日历日→交易日约2/3）
    fetch_days = max(periods) + 15  # 20交易日≈30日历日，+15余量
    # 行业名归一化到东财行业分类
    matched_industry = get_stock_industry_em(industry_name)
    stock_df = get_stock_kline(stock_code, days=fetch_days)
    industry_df = get_industry_kline(matched_industry, days=fetch_days)

    if stock_df is None or industry_df is None:
        return None

    result = {
        "code": stock_code,
        "industry": matched_industry,
        "periods": {},
    }
    for p in periods:
        s_pct = _calc_period_pct(stock_df, p)
        i_pct = _calc_period_pct(industry_df, p)
        if s_pct is None or i_pct is None:
            continue
        result["periods"][p] = {
            "stock_pct": s_pct,
            "industry_pct": i_pct,
            "rs": round(s_pct - i_pct, 2),
        }

    # 综合判断（以最长周期为主）
    if result["periods"]:
        longest_p = max(result["periods"].keys())
        rs_long = result["periods"][longest_p]["rs"]
        if rs_long > 2:
            result["status"] = "强于行业"
        elif rs_long < -2:
            result["status"] = "弱于行业"
        else:
            result["status"] = "持平"
    else:
        result["status"] = "数据不足"

    return result


def run_position_relative_strength(position_f10_data, periods=(5, 20)):
    """批量计算持仓股相对所属行业的相对强弱

    Args:
        position_f10_data: fetch_position_f10_and_news() 返回的列表，复用其中的行业归属
        periods: 计算周期

    Returns:
        dict: {results: [...], analysis_date: ...}
    """
    if not position_f10_data:
        return None

    print("\n" + "=" * 50)
    print("持仓股相对所属行业强弱分析")
    print("=" * 50)

    # 行业K线缓存，避免同行业多次请求
    industry_cache = {}
    results = []

    for i, entry in enumerate(position_f10_data):
        code = entry.get("code", "")
        name = entry.get("name", "")
        f10 = entry.get("f10") or {}
        profile = f10.get("profile") if f10 else None
        industry = profile.get("industry", "") if profile else ""

        # 跳过现金/无效代码（持仓 code 为规范代码，先剥 sh/sz 前缀再作 6 位校验）
        if not code or "现金" in name:
            continue
        code6 = strip_prefix(code) or code
        # 长度不为6或非数字（如 02050 录入错误）也跳过
        if code6 == "000000" or len(code6) != 6 or not code6.isdigit():
            continue
        code = code6

        # 优先使用东财行业归属，避免巨潮行业名（如"农业"）与东财（如"种植业"）不一致
        em_industry = get_stock_industry_em(industry)
        if em_industry:
            industry = em_industry

        if not industry:
            print(f"[{i+1}] {name}({code}) 无行业归属，跳过")
            continue

        print(f"[{i+1}] {name}({code}) 行业:{industry}")

        # 加大到周期3倍+10，确保能算出最长周期数据
        fetch_days = max(periods) + 15  # 20交易日≈30日历日，+15余量

        # 复用行业缓存
        if industry not in industry_cache:
            industry_cache[industry] = get_industry_kline(
                industry, days=fetch_days
            )

        stock_df = get_stock_kline(code, days=fetch_days)
        industry_df = industry_cache[industry]

        if stock_df is None or industry_df is None:
            print(f"  数据获取失败")
            continue

        item = {
            "code": code,
            "name": name,
            "industry": industry,
            "periods": {},
        }
        for p in periods:
            s_pct = _calc_period_pct(stock_df, p)
            i_pct = _calc_period_pct(industry_df, p)
            if s_pct is None or i_pct is None:
                continue
            item["periods"][p] = {
                "stock_pct": s_pct,
                "industry_pct": i_pct,
                "rs": round(s_pct - i_pct, 2),
            }

        if item["periods"]:
            longest_p = max(item["periods"].keys())
            rs_long = item["periods"][longest_p]["rs"]
            if rs_long > 2:
                item["status"] = "强于行业"
            elif rs_long < -2:
                item["status"] = "弱于行业"
            else:
                item["status"] = "持平"
            print(f"  RS{longest_p}日: {rs_long:+.2f}% | {item['status']}")
        else:
            item["status"] = "数据不足"
            print(f"  数据不足")

        results.append(item)

    return {
        "results": results,
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
    }


def format_relative_strength_report(rs_data):
    """格式化相对强弱报告"""
    if not rs_data or not rs_data.get("results"):
        return ""

    lines = []
    lines.append("\n" + "=" * 60)
    lines.append("持仓股相对所属行业强弱分析")
    lines.append(f"分析日期: {rs_data.get('analysis_date', '')}")
    lines.append("=" * 60)
    lines.append("  说明: RS = 个股涨幅 - 行业涨幅；正值表示强于行业")
    lines.append("  " + "-" * 56)

    for item in rs_data["results"]:
        code = item.get("code", "")
        name = item.get("name", "")
        industry = item.get("industry", "N/A")
        status = item.get("status", "")
        periods = item.get("periods", {})

        status_emoji = {
            "强于行业": "★",
            "弱于行业": "▽",
            "持平": "=",
            "数据不足": "?",
        }.get(status, "")
        lines.append(f"\n  {status_emoji} {name}({code}) | 行业: {industry} | {status}")

        for p in sorted(periods.keys()):
            d = periods[p]
            lines.append(
                f"    {p}日: 个股{d['stock_pct']:+.2f}% vs 行业{d['industry_pct']:+.2f}% "
                f"| RS {d['rs']:+.2f}%"
            )

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


if __name__ == "__main__":
    test_advice = """
    今日市场分析：
    1. 上证指数突破3100点，建议关注后续走势
    2. 创业板指表现强势，科技股活跃
    3. 个股方面，贵州茅台、宁德时代值得关注
    4. 半导体板块中芯国际走势良好
    """
    
    report, results = run_momentum_analysis(bili_advice=test_advice)
    if report:
        print(report)
