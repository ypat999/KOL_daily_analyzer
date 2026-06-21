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
import pandas as pd
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
    
    优先使用新浪实时行情统计涨跌停，东财接口作为备用。
    
    Returns:
        dict: 涨停/跌停家数、封板率等
    """
    if not AKSHARE_AVAILABLE:
        return None
    
    # 方案1：用新浪实时行情统计涨跌停
    try:
        df = ak.stock_zh_a_spot()
        if df is not None and not df.empty:
            # 新浪返回列名可能含"涨跌幅"
            change_col = None
            for col in df.columns:
                if "涨跌幅" in str(col) or "change_pct" in str(col).lower():
                    change_col = col
                    break
            
            if change_col is not None:
                changes = pd.to_numeric(df[change_col], errors='coerce')
                # 涨停：涨幅>=9.8%（考虑科创板20%）
                limit_up_count = int((changes >= 9.8).sum())
                limit_down_count = int((changes <= -9.8).sum())
                
                # 科创板/创业板20%涨跌停
                if "代码" in df.columns:
                    kcb = df[df["代码"].str.startswith("688")]
                    cyb = df[df["代码"].str.startswith("30")]
                    if not kcb.empty:
                        kcb_changes = pd.to_numeric(kcb[change_col], errors='coerce')
                        limit_up_count += int((kcb_changes >= 19.8).sum()) - int((kcb_changes >= 9.8).sum())
                        limit_down_count += int((kcb_changes <= -19.8).sum()) - int((kcb_changes <= -9.8).sum())
                    if not cyb.empty:
                        cyb_changes = pd.to_numeric(cyb[change_col], errors='coerce')
                        limit_up_count += int((cyb_changes >= 19.8).sum()) - int((cyb_changes >= 9.8).sum())
                        limit_down_count += int((cyb_changes <= -19.8).sum()) - int((cyb_changes <= -9.8).sum())
                
                total = limit_up_count + limit_down_count
                limit_up_ratio = limit_up_count / total * 100 if total > 0 else 0
                
                return {
                    "limit_up_count": limit_up_count,
                    "limit_down_count": limit_down_count,
                    "limit_up_ratio": round(limit_up_ratio, 1),
                    "consecutive_board_count": 0,  # 新浪无法获取连板数据
                    "max_consecutive_height": 0,
                }
    except Exception as e:
        print(f"新浪实时行情统计涨跌停失败: {e}")
    
    # 方案2：尝试东财涨停池接口（可能被封）
    try:
        limit_up_df = ak.stock_zt_pool_em(date=datetime.now().strftime("%Y%m%d"))
        limit_up_count = len(limit_up_df) if limit_up_df is not None else 0
        
        limit_down_df = ak.stock_zt_pool_dtgc_em(date=datetime.now().strftime("%Y%m%d"))
        limit_down_count = len(limit_down_df) if limit_down_df is not None else 0
        
        consecutive_board_df = ak.stock_zt_pool_strong_em(date=datetime.now().strftime("%Y%m%d"))
        consecutive_count = len(consecutive_board_df) if consecutive_board_df is not None else 0
        
        total = limit_up_count + limit_down_count
        limit_up_ratio = limit_up_count / total * 100 if total > 0 else 0
        
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
        print(f"东财涨跌停接口失败: {e}")
        return None


def get_north_flow():
    """获取北向资金净流入
    
    注意：北向资金数据仅东财提供，无新浪替代。
    东财不可用时返回None，不影响整体分析。
    
    Returns:
        dict: 净流入金额（亿元）
    """
    if not AKSHARE_AVAILABLE:
        return None
    
    try:
        # 北向资金每日净流入（东财接口，可能被封）
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


def get_dragon_tiger_list():
    """获取龙虎榜数据
    
    注意：龙虎榜数据仅东财提供，无新浪替代。
    东财不可用时返回None，不影响整体分析。
    
    Returns:
        dict: 龙虎榜个股及买卖席位
    """
    if not AKSHARE_AVAILABLE:
        return None
    
    try:
        today = datetime.now().strftime("%Y%m%d")
        # 龙虎榜个股明细
        df = ak.stock_lhb_detail_em(
            start_date=today,
            end_date=today,
        )
        if df is None or df.empty:
            # 尝试最近交易日
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            df = ak.stock_lhb_detail_em(
                start_date=yesterday,
                end_date=yesterday,
            )
        
        if df is None or df.empty:
            return None
        
        # 取前10只
        top_items = []
        for _, row in df.head(10).iterrows():
            item = {
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "reason": str(row.get("上榜原因", "")),
                "net_buy": row.get("龙虎榜净买额", row.get("净买额", 0)),
            }
            if isinstance(item["net_buy"], (int, float)):
                item["net_buy"] = round(float(item["net_buy"]) / 1e8, 2)  # 转为亿元
            top_items.append(item)
        
        return {
            "date": today,
            "count": len(df),
            "top_items": top_items,
        }
    except Exception as e:
        print(f"获取龙虎榜数据失败: {e}")
        return None


def get_limit_up_pool_detail():
    """获取涨停板池详情（含连板信息）
    
    注意：涨停池详情仅东财提供，无新浪替代。
    东财不可用时返回None，不影响整体分析。
    
    Returns:
        dict: 涨停股详情
    """
    if not AKSHARE_AVAILABLE:
        return None
    
    try:
        today = datetime.now().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=today)
        if df is None or df.empty:
            return None
        
        # 按连板数排序
        if "连板数" in df.columns:
            df = df.sort_values("连板数", ascending=False)
        
        top_items = []
        for _, row in df.head(15).iterrows():
            item = {
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "price": row.get("最新价", 0),
                "change_pct": row.get("涨跌幅", 0),
                "consecutive_boards": int(row.get("连板数", 1)),
                "turnover": row.get("成交额", 0),
                "first_limit_time": str(row.get("首次封板时间", "")),
                "final_limit_time": str(row.get("最后封板时间", "")),
                "open_times": row.get("炸板次数", 0),
            }
            top_items.append(item)
        
        return {
            "date": today,
            "total_count": len(df),
            "top_items": top_items,
        }
    except Exception as e:
        print(f"获取涨停板池详情失败: {e}")
        return None


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
    
    # 龙虎榜和涨停板池详情
    dragon_tiger = get_dragon_tiger_list()
    limit_up_detail = get_limit_up_pool_detail()
    
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
    
    # 涨停板池详情
    if limit_up_detail and limit_up_detail.get("top_items"):
        lines.append(f"\n【涨停板池详情（按连板数排序，前15）】")
        for item in limit_up_detail["top_items"]:
            lines.append(
                f"  {item['name']}({item['code']}) | {item['consecutive_boards']}连板 | "
                f"涨幅{item['change_pct']}% | 炸板{item['open_times']}次 | "
                f"首封{item['first_limit_time']}"
            )
    
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
    
    # 龙虎榜
    if dragon_tiger and dragon_tiger.get("top_items"):
        lines.append(f"\n【龙虎榜（前10）】")
        for item in dragon_tiger["top_items"]:
            net_buy_str = f"净买{item['net_buy']}亿" if isinstance(item.get("net_buy"), (int, float)) else ""
            lines.append(
                f"  {item['name']}({item['code']}) | {item['reason']} | {net_buy_str}"
            )
    else:
        lines.append(f"\n【龙虎榜】数据获取失败或无数据")
    
    lines.append("\n" + "=" * 60)
    
    report = "\n".join(lines)
    print("市场宽度分析完成")
    return report


if __name__ == "__main__":
    report = run_market_breadth_analysis()
    print(report)
