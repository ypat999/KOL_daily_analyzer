"""
市场宽度分析模块

提供大盘环境判断所需的市场宽度数据：
1. 涨跌停家数与封板率
2. 北向资金净流入
3. 主要指数均线偏离度（超买超卖）
4. 市场情绪温度计

数据源：akshare（优先）+ yfinance（兜底）
"""

import numpy as np
from datetime import datetime, timedelta

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False


def get_limit_up_down_stats():
    """获取涨跌停家数统计
    
    Returns:
        dict: 涨停/跌停家数、封板率等
    """
    if not AKSHARE_AVAILABLE:
        return None
    
    try:
        # 涨停板池
        limit_up_df = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
        limit_up_count = len(limit_up_df) if limit_up_df is not None else 0
        
        # 跌停板池
        limit_down_df = ak.stock_zt_pool_dtgc_em(date=datetime.now().strftime("%Y%m%d"))
        limit_down_count = len(limit_down_df) if limit_down_df is not None else 0
        
        # 连板池
        consecutive_board_df = ak.stock_zt_pool_strong_em(date=datetime.now().strftime("%Y%m%d"))
        consecutive_count = len(consecutive_board_df) if consecutive_board_df is not None else 0
        
        # 计算封板率（涨停家数 / (涨停+跌停)）
        total = limit_up_count + limit_down_count
        limit_up_ratio = limit_up_count / total * 100 if total > 0 else 0
        
        # 最高连板高度
        max_consecutive = 0
        if consecutive_board_df is not None and not consecutive_board_df.empty:
            if "连板数" in consecutive_board_df.columns:
                max_consecutive = int(consecutive_board_df["连板数"].max())
        
        return {
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "limit_up_ratio": round(limit_up_ratio, 1),
            "consecutive_board_count": consecutive_count,
            "max_consecutive_height": max_consecutive,
        }
    except Exception as e:
        print(f"获取涨跌停数据失败: {e}")
        return None


def get_north_flow():
    """获取北向资金净流入
    
    Returns:
        dict: 净流入金额（亿元）
    """
    if not AKSHARE_AVAILABLE:
        return None
    
    try:
        # 北向资金每日净流入
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        if df is None or df.empty:
            return None
        
        # 取最近一条
        latest = df.iloc[-1]
        date_str = str(latest.get("日期", latest.get("date", "")))[:10]
        net_flow = latest.get("当日净流入", latest.get("净流入", 0))
        
        # 转换为亿元
        if net_flow:
            net_flow_yi = float(net_flow) / 1e8
        else:
            net_flow_yi = 0
        
        return {
            "date": date_str,
            "net_flow_yi": round(net_flow_yi, 2),
            "direction": "净流入" if net_flow_yi > 0 else "净流出",
        }
    except Exception as e:
        print(f"获取北向资金数据失败: {e}")
        return None


def get_index_ma_deviation():
    """获取主要指数均线偏离度
    
    计算上证指数、深证成指、创业板指相对20/60日均线的偏离度，
    用于判断大盘超买超卖状态。
    
    Returns:
        list: 各指数偏离度数据
    """
    indices = [
        ("000001", "上证指数"),
        ("399001", "深证成指"),
        ("399006", "创业板指"),
    ]
    
    results = []
    
    for code, name in indices:
        try:
            if AKSHARE_AVAILABLE:
                df = ak.stock_zh_index_daily(symbol=f"sh{code}" if code.startswith("000") else f"sz{code}")
            elif YFINANCE_AVAILABLE:
                yf_code = f"{code}.SS" if code.startswith("000") else f"{code}.SZ"
                df = yf.download(yf_code, period="6mo", progress=False)
                df = df.rename(columns={"Close": "收盘", "Date": "日期"})
            else:
                continue
            
            if df is None or len(df) < 60:
                continue
            
            close = df["收盘"].values.astype(float)
            current = close[-1]
            
            ma20 = np.mean(close[-20:])
            ma60 = np.mean(close[-60:])
            
            dev_ma20 = (current / ma20 - 1) * 100
            dev_ma60 = (current / ma60 - 1) * 100
            
            # 超买超卖判断
            if dev_ma20 > 5:
                status = "超买"
            elif dev_ma20 < -5:
                status = "超卖"
            elif dev_ma20 > 0:
                status = "偏强"
            else:
                status = "偏弱"
            
            results.append({
                "name": name,
                "code": code,
                "current_price": round(current, 2),
                "ma20": round(ma20, 2),
                "ma60": round(ma60, 2),
                "dev_ma20": round(dev_ma20, 2),
                "dev_ma60": round(dev_ma60, 2),
                "status": status,
            })
        except Exception as e:
            print(f"获取{name}均线偏离度失败: {e}")
            continue
    
    return results


def calculate_market_temperature(limit_stats, north_flow, index_deviations):
    """计算市场情绪温度计（0-100）
    
    综合涨跌停比、北向资金、指数偏离度给出市场温度。
    >70 过热，50-70 偏热，30-50 偏冷，<30 过冷。
    
    Returns:
        dict: 温度值和描述
    """
    score = 50  # 基准
    
    # 涨跌停比贡献（±20分）
    if limit_stats:
        ratio = limit_stats.get("limit_up_ratio", 50)
        # 涨停占比 > 70% 加分，< 30% 减分
        if ratio > 70:
            score += 20
        elif ratio > 50:
            score += 10
        elif ratio < 30:
            score -= 20
        elif ratio < 50:
            score -= 10
        
        # 连板高度贡献（±10分）
        height = limit_stats.get("max_consecutive_height", 0)
        if height >= 5:
            score += 10
        elif height >= 3:
            score += 5
        elif height == 0 and limit_stats.get("limit_up_count", 0) < 10:
            score -= 5
    
    # 北向资金贡献（±15分）
    if north_flow:
        flow = north_flow.get("net_flow_yi", 0)
        if flow > 50:
            score += 15
        elif flow > 20:
            score += 8
        elif flow < -50:
            score -= 15
        elif flow < -20:
            score -= 8
    
    # 指数偏离度贡献（±15分）
    if index_deviations:
        avg_dev = np.mean([d["dev_ma20"] for d in index_deviations])
        if avg_dev > 3:
            score += 15
        elif avg_dev > 0:
            score += 5
        elif avg_dev < -3:
            score -= 15
        elif avg_dev < 0:
            score -= 5
    
    score = max(0, min(100, score))
    
    if score >= 70:
        desc = "过热（注意回调风险）"
    elif score >= 50:
        desc = "偏热（可操作）"
    elif score >= 30:
        desc = "偏冷（谨慎操作）"
    else:
        desc = "过冷（观望为主）"
    
    return {
        "temperature": round(score, 1),
        "description": desc,
    }


def run_market_breadth_analysis():
    """运行市场宽度分析
    
    Returns:
        str: 格式化的市场宽度报告
    """
    print("开始市场宽度分析...")
    
    limit_stats = get_limit_up_down_stats()
    north_flow = get_north_flow()
    index_deviations = get_index_ma_deviation()
    temperature = calculate_market_temperature(limit_stats, north_flow, index_deviations)
    
    lines = []
    lines.append("=" * 60)
    lines.append("市场宽度分析报告")
    lines.append(f"分析日期: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append("=" * 60)
    
    # 市场温度
    lines.append(f"\n【市场情绪温度】")
    lines.append(f"  温度: {temperature['temperature']}° - {temperature['description']}")
    
    # 涨跌停
    if limit_stats:
        lines.append(f"\n【涨跌停统计】")
        lines.append(f"  涨停: {limit_stats['limit_up_count']}家 | 跌停: {limit_stats['limit_down_count']}家")
        lines.append(f"  涨停占比: {limit_stats['limit_up_ratio']}%")
        lines.append(f"  连板股: {limit_stats['consecutive_board_count']}家 | 最高连板: {limit_stats['max_consecutive_height']}板")
    else:
        lines.append(f"\n【涨跌停统计】数据获取失败")
    
    # 北向资金
    if north_flow:
        lines.append(f"\n【北向资金】")
        lines.append(f"  {north_flow['date']} {north_flow['direction']} {abs(north_flow['net_flow_yi'])}亿元")
    else:
        lines.append(f"\n【北向资金】数据获取失败")
    
    # 指数偏离度
    if index_deviations:
        lines.append(f"\n【指数均线偏离度】")
        for idx in index_deviations:
            lines.append(
                f"  {idx['name']}: {idx['current_price']} | "
                f"MA20={idx['ma20']}({idx['dev_ma20']:+}%) | "
                f"MA60={idx['ma60']}({idx['dev_ma60']:+}%) | {idx['status']}"
            )
    else:
        lines.append(f"\n【指数均线偏离度】数据获取失败")
    
    lines.append("\n" + "=" * 60)
    
    report = "\n".join(lines)
    print("市场宽度分析完成")
    return report


if __name__ == "__main__":
    report = run_market_breadth_analysis()
    print(report)
