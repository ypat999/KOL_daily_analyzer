import json
import re
from datetime import datetime, timedelta
from openai import OpenAI
import akshare as ak
import pandas as pd
import numpy as np


def load_api_key_from_file():
    """从deepseek_api_key.txt文件读取key值"""
    try:
        with open("deepseek_api_key.txt", "r", encoding="utf-8") as f:
            api_key = f.read().strip()
            if not api_key:
                print("警告: deepseek_api_key.txt文件为空")
                return ""
            return api_key
    except FileNotFoundError:
        print("错误: 未找到deepseek_api_key.txt文件")
        return ""
    except Exception as e:
        print(f"读取key文件时出错: {e}")
        return ""


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
                        if 'reason' not in idx:
                            idx['reason'] = ''
                    for stock in result.get('stocks', []):
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
    
    DEEPSEEK_API_KEY = load_api_key_from_file()
    if not DEEPSEEK_API_KEY:
        print("无法获取DeepSeek API Key，跳过标的提取")
        return {"indices": [], "stocks": []}
    
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    
    system_prompt = """你是一个专业的金融分析师，擅长从投资建议中提取关键投资标的。
你的任务是从给定的投资建议文本中提取出重点关注的指数和股票。

请严格按照以下JSON格式输出，不要添加任何其他内容：
{
    "indices": [
        {"code": "代码", "name": "名称", "reason": "关注原因"}
    ],
    "stocks": [
        {"code": "代码", "name": "名称", "reason": "关注原因"}
    ]
}

注意事项：
1. 指数代码格式：上证指数用"000001"，深证成指用"399001"，创业板指用"399006"，科创50用"000688"等
2. 股票代码格式：6位数字代码，如"000001"、"600519"等
3. 只提取明确提到或强烈暗示值得关注的标的
4. 如果没有明确的标的，返回空列表
5. reason字段简要说明为什么关注该标的"""

    user_prompt = f"""请从以下投资建议中提取重点关注的指数和股票：

{investment_advice}

请严格按照JSON格式输出："""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            stream=False
        )
        
        result_text = response.choices[0].message.content.strip()
        
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result = json.loads(json_match.group())
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
            code = idx.get("code", "")
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
            code = stock.get("code", "")
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


def get_index_kline(code, days=150):
    """获取指数日K线数据
    
    Args:
        code: 指数代码
        days: 获取的天数
    
    Returns:
        DataFrame: K线数据
    """
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        
        df = ak.index_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date
        )
        
        if df is not None and len(df) > 0:
            df['日期'] = pd.to_datetime(df['日期'])
            df = df.sort_values('日期')
            return df
        return None
    except Exception as e:
        print(f"获取指数 {code} K线数据失败: {e}")
        return None


def get_stock_kline(code, days=150):
    """获取股票日K线数据
    
    Args:
        code: 股票代码
        days: 获取的天数
    
    Returns:
        DataFrame: K线数据
    """
    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"
        )
        
        if df is not None and len(df) > 0:
            df['日期'] = pd.to_datetime(df['日期'])
            df = df.sort_values('日期')
            return df
        return None
    except Exception as e:
        print(f"获取股票 {code} K线数据失败: {e}")
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
    
    current_price = close[-1]
    current_high = high[-1]
    
    period_high = np.max(high[-window-1:-1])
    period_low = np.min(close[-window-1:-1])
    
    is_new_high = current_price >= period_high
    is_new_low = current_price <= period_low
    
    distance_to_high = round((current_price / period_high - 1) * 100, 2)
    distance_to_low = round((current_price / period_low - 1) * 100, 2)
    
    days_since_high = 0
    for i in range(len(close) - 2, max(len(close) - window - 1, -1), -1):
        if high[i] >= period_high:
            days_since_high = len(close) - 1 - i
            break
    
    breakout_signal = None
    if is_new_high:
        breakout_signal = "20日新高突破"
    
    return {
        "is_new_high": is_new_high,
        "is_new_low": is_new_low,
        "period_high": round(period_high, 2),
        "period_low": round(period_low, 2),
        "distance_to_high": distance_to_high,
        "distance_to_low": distance_to_low,
        "days_since_high": days_since_high,
        "breakout_signal": breakout_signal
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
        df = get_index_kline(code)
        
        if df is not None:
            factors = calculate_momentum_factors(df)
            if factors:
                results["indices"].append({
                    "code": code,
                    "name": name,
                    "reasons": reasons,
                    "latest_price": float(df['收盘'].iloc[-1]),
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
        df = get_stock_kline(code)
        
        if df is not None:
            factors = calculate_momentum_factors(df)
            if factors:
                results["stocks"].append({
                    "code": code,
                    "name": name,
                    "reasons": reasons,
                    "latest_price": float(df['收盘'].iloc[-1]),
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
                if bo['is_new_high']:
                    report_lines.append(f"  ★★★ 突破信号: {bo['breakout_signal']} ★★★")
                    breakout_items.append({
                        "type": "指数",
                        "name": idx['name'],
                        "code": idx['code'],
                        "price": idx['latest_price'],
                        "signal": bo['breakout_signal']
                    })
            if idx['reasons']:
                report_lines.append(f"  关注原因: {'; '.join(idx['reasons'])}")
    
    if results["stocks"]:
        report_lines.append("\n【股票分析】")
        report_lines.append("-" * 40)
        for stock in results["stocks"]:
            report_lines.append(f"\n{stock['name']}({stock['code']})")
            report_lines.append(f"  最新价格: {stock['latest_price']:.2f}")
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
                if bo['is_new_high']:
                    report_lines.append(f"  ★★★ 突破信号: {bo['breakout_signal']} ★★★")
                    breakout_items.append({
                        "type": "股票",
                        "name": stock['name'],
                        "code": stock['code'],
                        "price": stock['latest_price'],
                        "signal": bo['breakout_signal']
                    })
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
