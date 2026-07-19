"""资金面分析模块

提供个股/行业ETF决策所需的资金面信号：
1. 行业资金流向 TOP（指导买哪个行业ETF）
2. 概念资金流向 TOP（发现热点主线）
3. 个股主力资金流向排名（全市场 TOP + 持仓股近5日趋势）
4. 两融余额趋势（杠杆情绪，温度计替代北向资金）

数据源：akshare（东财资金流向接口）
注意：资金面数据仅交易日盘后更新，盘中为实时估算。
"""

import time
from datetime import datetime, timedelta

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


def _safe_num(v, default=0.0):
    """安全转 float，None/nan/异常返回 default"""
    if v is None:
        return default
    try:
        f = float(v)
        import math
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def get_sector_fund_flow(top_n=8, flow_type="行业资金流"):
    """获取行业/概念资金流向 TOP

    Args:
        top_n: 返回前/后各 top_n 条
        flow_type: "行业资金流" 或 "概念资金流"

    Returns:
        dict: {inflow_top: [...], outflow_top: [...]}
    """
    if not AKSHARE_AVAILABLE:
        return None
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type=flow_type)
        if df is None or df.empty:
            return None

        col_net = "今日主力净流入-净额"
        col_pct = "今日主力净流入-净占比"
        col_chg = "今日涨跌幅"
        col_top = "今日主力净流入最大股"

        inflow = df.head(top_n)
        outflow = df.tail(top_n).iloc[::-1]

        def _fmt(row):
            return {
                "name": str(row.get("名称", "")),
                "change_pct": _safe_num(row.get(col_chg)),
                "net_inflow": _safe_num(row.get(col_net)),
                "net_pct": _safe_num(row.get(col_pct)),
                "top_stock": str(row.get(col_top, "")),
            }

        return {
            "inflow_top": [_fmt(r) for _, r in inflow.iterrows()],
            "outflow_top": [_fmt(r) for _, r in outflow.iterrows()],
        }
    except Exception as e:
        print(f"获取{flow_type}资金流向失败: {e}")
        return None


def get_individual_fund_flow_rank(top_n=10, direction="inflow"):
    """获取个股主力资金流向排名

    Args:
        top_n: 返回条数
        direction: "inflow" 流入TOP / "outflow" 流出TOP
    """
    if not AKSHARE_AVAILABLE:
        return None
    import pandas as pd
    for attempt in range(2):
        try:
            df = ak.stock_individual_fund_flow_rank(indicator="今日")
            if df is None or df.empty:
                return None

            col_net = "今日主力净流入-净额"
            col_pct = "今日主力净流入-净占比"
            col_chg = "今日涨跌幅"

            # 东财偶发返回 '-' 字符串，强制转 numeric 再排序
            for c in (col_net, col_pct, col_chg, "最新价"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=[col_net])

            if direction == "outflow":
                df = df.sort_values(col_net, ascending=True).head(top_n)
            else:
                df = df.sort_values(col_net, ascending=False).head(top_n)

            items = []
            for _, row in df.iterrows():
                items.append({
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "price": _safe_num(row.get("最新价")),
                    "change_pct": _safe_num(row.get(col_chg)),
                    "net_inflow": _safe_num(row.get(col_net)),
                    "net_pct": _safe_num(row.get(col_pct)),
                })
            return items
        except Exception as e:
            if attempt < 1:
                time.sleep(2)
                continue
            print(f"获取个股资金流排名失败: {e}")
            return None


def get_position_stock_fund_flow(positions, days=5):
    """获取持仓股最近N天的主力资金流向趋势

    Args:
        positions: load_positions() 返回的字典
        days: 回看天数
    """
    if not AKSHARE_AVAILABLE:
        return None

    stocks = positions.get("stocks", []) if positions else []
    if not stocks:
        return None

    results = []
    for stock in stocks:
        code = stock.get("code", "")
        name = stock.get("name", "")
        if not code:
            continue
        # 跳过现金条目和非标准股票代码（如 000000 现金、02050 录入错误）
        if code == "000000" or "现金" in name or len(code) != 6 or not code.isdigit():
            continue
        try:
            df = ak.stock_individual_fund_flow(stock=code)
            if df is None or df.empty:
                continue
            recent = df.tail(days)
            net_col = "主力净流入-净额"
            if net_col not in recent.columns:
                continue

            daily_nets = [_safe_num(v) for v in recent[net_col].tolist()]
            total_net = sum(daily_nets)

            # 趋势判断：近3日净流入是否单调
            trend = "unknown"
            if len(daily_nets) >= 3:
                tail3 = daily_nets[-3:]
                if all(tail3[i] > tail3[i - 1] for i in range(1, len(tail3))):
                    trend = "rising"
                elif all(tail3[i] < tail3[i - 1] for i in range(1, len(tail3))):
                    trend = "declining"

            results.append({
                "code": code,
                "name": name,
                "daily_nets": daily_nets,
                "total_net": total_net,
                "trend": trend,
                "latest_net": daily_nets[-1] if daily_nets else 0,
            })
        except Exception as e:
            print(f"  获取{name}({code})资金流失败: {e}")
            continue
        time.sleep(0.5)  # 限流

    return results


def get_margin_balance(days=5):
    """获取两融余额最近N天趋势（融资余额反映杠杆看多情绪）

    Returns:
        dict: 含 dates/rq_balance/latest_total/trend
    """
    if not AKSHARE_AVAILABLE:
        return None
    try:
        df = ak.stock_margin_account_info()
        if df is None or df.empty:
            return None

        recent = df.tail(days)
        rq_col = "融资余额"

        rq_vals = [_safe_num(v) for v in recent[rq_col].tolist()] if rq_col in recent.columns else []
        total_balance = rq_vals[-1] if rq_vals else 0

        # 趋势：连升/连降
        trend = "stable"
        if len(rq_vals) >= 3:
            if all(rq_vals[i] > rq_vals[i - 1] for i in range(1, len(rq_vals))):
                trend = "rising"
            elif all(rq_vals[i] < rq_vals[i - 1] for i in range(1, len(rq_vals))):
                trend = "declining"

        return {
            "dates": [str(d)[:10] for d in recent["日期"].tolist()] if "日期" in recent.columns else [],
            "rq_balance": rq_vals,
            "latest_total": total_balance,
            "trend": trend,
        }
    except Exception as e:
        print(f"获取两融余额失败: {e}")
        return None


def run_fund_flow_analysis(positions=None):
    """运行资金面分析，返回格式化报告

    Args:
        positions: load_positions() 返回的字典，用于持仓股资金流分析

    Returns:
        str: 格式化的资金面报告
    """
    print("开始资金面分析...")

    sector_flow = get_sector_fund_flow(top_n=8, flow_type="行业资金流")
    concept_flow = get_sector_fund_flow(top_n=8, flow_type="概念资金流")
    inflow_rank = get_individual_fund_flow_rank(top_n=10, direction="inflow")
    outflow_rank = get_individual_fund_flow_rank(top_n=10, direction="outflow")
    position_flow = get_position_stock_fund_flow(positions) if positions else None
    margin = get_margin_balance(days=5)

    lines = []
    lines.append("=" * 60)
    lines.append("资金面分析报告")
    lines.append(f"分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)

    # 行业资金流（指导行业ETF选择）
    if sector_flow:
        lines.append(f"\n【行业资金流向 TOP8】")
        lines.append(f"  ▲ 流入（可关注相关行业ETF）:")
        for i, s in enumerate(sector_flow["inflow_top"], 1):
            net_yi = s["net_inflow"] / 1e8
            lines.append(
                f"    {i}. {s['name']} | 涨{s['change_pct']:+.2f}% | "
                f"主力净流入{net_yi:+.2f}亿 ({s['net_pct']:+.2f}%) | 龙头:{s['top_stock']}"
            )
        lines.append(f"  ▼ 流出（回避相关行业ETF）:")
        for i, s in enumerate(sector_flow["outflow_top"], 1):
            net_yi = s["net_inflow"] / 1e8
            lines.append(
                f"    {i}. {s['name']} | 涨{s['change_pct']:+.2f}% | "
                f"主力净流出{net_yi:-.2f}亿 ({s['net_pct']:+.2f}%) | 龙头:{s['top_stock']}"
            )
    else:
        lines.append(f"\n【行业资金流向】数据获取失败")

    # 概念资金流（发现热点主线）
    if concept_flow:
        lines.append(f"\n【概念资金流向 TOP8（热点主线）】")
        lines.append(f"  ▲ 流入:")
        for i, s in enumerate(concept_flow["inflow_top"], 1):
            net_yi = s["net_inflow"] / 1e8
            lines.append(
                f"    {i}. {s['name']} | 涨{s['change_pct']:+.2f}% | "
                f"主力净流入{net_yi:+.2f}亿 ({s['net_pct']:+.2f}%) | 龙头:{s['top_stock']}"
            )
    else:
        lines.append(f"\n【概念资金流向】数据获取失败")

    # 个股资金流排名
    if inflow_rank:
        lines.append(f"\n【个股主力资金流入 TOP10】")
        for i, s in enumerate(inflow_rank, 1):
            net_yi = s["net_inflow"] / 1e8
            lines.append(
                f"    {i}. {s['name']}({s['code']}) | 价{s['price']} | "
                f"涨{s['change_pct']:+.2f}% | 净流入{net_yi:+.2f}亿 ({s['net_pct']:+.2f}%)"
            )

    if outflow_rank:
        lines.append(f"\n【个股主力资金流出 TOP10（回避）】")
        for i, s in enumerate(outflow_rank, 1):
            net_yi = s["net_inflow"] / 1e8
            lines.append(
                f"    {i}. {s['name']}({s['code']}) | 价{s['price']} | "
                f"涨{s['change_pct']:+.2f}% | 净流出{net_yi:-.2f}亿 ({s['net_pct']:+.2f}%)"
            )

    # 持仓股资金流
    if position_flow:
        lines.append(f"\n【持仓股主力资金动向（近5日）】")
        trend_label = {
            "rising": "主力加仓（净流入递增）",
            "declining": "主力减仓（净流入递减）",
            "unknown": "趋势不明",
        }
        for s in position_flow:
            total_yi = s["total_net"] / 1e8
            latest_yi = s["latest_net"] / 1e8
            lines.append(
                f"    {s['name']}({s['code']}) | 近5日合计{total_yi:+.2f}亿 | "
                f"今日{latest_yi:+.2f}亿 | {trend_label.get(s['trend'], s['trend'])}"
            )
    else:
        lines.append(f"\n【持仓股主力资金动向】无持仓或数据获取失败")

    # 两融余额（杠杆情绪，替代失效的北向资金）
    if margin:
        trend_label = {
            "rising": "连升（杠杆情绪升温，看多）",
            "declining": "连降（去杠杆，谨慎）",
            "stable": "平稳",
        }
        lines.append(f"\n【两融余额（近5日，杠杆情绪）】")
        lines.append(
            f"    最新融资余额: {margin['latest_total']:.2f}亿元 | "
            f"趋势: {trend_label.get(margin['trend'], margin['trend'])}"
        )
        for i, d in enumerate(margin["dates"]):
            rq = margin["rq_balance"][i]
            lines.append(f"      {d}: {rq:.2f}亿")
    else:
        lines.append(f"\n【两融余额】数据获取失败")

    lines.append("\n" + "=" * 60)
    print("资金面分析完成")
    return "\n".join(lines)


if __name__ == "__main__":
    from position_manager import load_positions
    positions = load_positions()
    report = run_fund_flow_analysis(positions)
    print(report)
