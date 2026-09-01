"""板块放量监控模块

扫描同花顺行业板块，识别显著放量（今日成交量 vs 20日均量）的板块，
配合 MA20/MA60 均线位置判断属于"底部放量"（机会）还是"顶部放量"（风险），
分别取最显著的前 N 个板块提示机会和风险。

- 底部放量：价格在 MA20/MA60 下方（空头趋势低位区）+ 放量 → 资金进场迹象，机会
- 顶部放量：价格在 MA20/MA60 上方（多头趋势高位区）+ 放量 → 出货迹象，风险

数据源：akshare（同花顺行业板块接口；东财板块接口当前网络不可用，作为备用）
"""

import time
import math
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from stage_timer import stage

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False

# 放量判定阈值：今日成交量 / 20日均量 >= 该值视为放量
VOLUME_RATIO_THRESHOLD = 1.5
# 每类取最显著的板块数
TOP_N = 5
# 历史K线回看的日历天数（约 100 个交易日，足够计算 MA60）
LOOKBACK_DAYS = 150
# 并发抓取线程数
MAX_WORKERS = 5


def _safe_num(v, default=0.0):
    """安全转 float，None/nan/异常返回 default"""
    if v is None:
        return default
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def get_industry_board_names():
    """获取行业板块名称列表

    优先同花顺（东财接口当前网络不通），失败返回 None。

    Returns:
        list: 板块名称列表
    """
    if not AKSHARE_AVAILABLE:
        return None

    # 方案1：同花顺行业板块
    try:
        df = ak.stock_board_industry_name_ths()
        if df is not None and not df.empty and "name" in df.columns:
            return df["name"].astype(str).tolist()
    except Exception as e:
        print(f"同花顺行业板块列表获取失败: {e}")

    # 方案2：东财行业板块
    try:
        df = ak.stock_board_industry_name_em()
        if df is not None and not df.empty and "板块名称" in df.columns:
            return df["板块名称"].astype(str).tolist()
    except Exception as e:
        print(f"东财行业板块列表获取失败: {e}")

    return None


def _fetch_board_hist(name, start_date, end_date, max_retries=2):
    """获取单个板块日K线（含成交量）

    Returns:
        DataFrame 或 None
    """
    for attempt in range(max_retries):
        try:
            df = ak.stock_board_industry_index_ths(
                symbol=name, start_date=start_date, end_date=end_date
            )
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1.0)
                continue
            print(f"  获取板块 {name} K线失败: {e}")
    return None


def _classify_volume_surge(close, ma20, ma60, pos_60):
    """根据均线位置与60日区间位置判断放量类型

    Args:
        close: 最新收盘价
        ma20: MA20
        ma60: MA60
        pos_60: 价格在60日区间的相对位置（0=最低，1=最高）

    Returns:
        str: "bottom"(底部放量) / "top"(顶部放量) / None(位置不明，不提示)
    """
    # 明确空头趋势低位区：价格同时在 MA20/MA60 下方
    if close < ma20 and close < ma60:
        return "bottom"
    # 明确多头趋势高位区：价格同时在 MA20/MA60 上方
    if close > ma20 and close > ma60:
        return "top"
    # 均线夹层：结合 60 日区间位置辅助判断
    if pos_60 <= 0.4:
        return "bottom"
    if pos_60 >= 0.6:
        return "top"
    return None


def analyze_sector_volume(board_names, lookback_days=LOOKBACK_DAYS, top_n=TOP_N):
    """分析各板块放量情况，分类排序

    Args:
        board_names: 板块名称列表
        lookback_days: 历史K线回看的日历天数
        top_n: 每类取最显著的数量

    Returns:
        dict: {total_scanned, bottom: [...], top: [...]}，失败返回 None
    """
    if not board_names:
        return None
    import numpy as np

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_board_hist, name, start_date, end_date): name
            for name in board_names
        }
        for fut in as_completed(futures):
            name = futures[fut]
            df = fut.result()
            if df is None or len(df) < 25:
                continue
            try:
                # 兼容不同数据源的收盘列名
                close_col = "收盘价" if "收盘价" in df.columns else (
                    "收盘" if "收盘" in df.columns else None)
                vol_col = "成交量" if "成交量" in df.columns else None
                if close_col is None or vol_col is None:
                    continue

                df = df.sort_values("日期").reset_index(drop=True)
                close = df[close_col].values.astype(float)
                volume = df[vol_col].values.astype(float)
                if len(close) < 25 or volume[-1] <= 0:
                    continue

                # 20日均量（不含今日）
                avg_vol_20 = np.mean(volume[-21:-1])
                if avg_vol_20 <= 0:
                    continue
                vol_ratio = volume[-1] / avg_vol_20

                # 均线
                ma20 = np.mean(close[-20:])
                ma60 = np.mean(close[-60:]) if len(close) >= 60 else ma20

                # 60日区间位置
                lo60 = np.min(close[-60:]) if len(close) >= 60 else np.min(close)
                hi60 = np.max(close[-60:]) if len(close) >= 60 else np.max(close)
                pos_60 = (close[-1] - lo60) / (hi60 - lo60) if hi60 > lo60 else 0.5

                # 涨跌幅
                change_pct = (close[-1] / close[-2] - 1) * 100 if len(close) >= 2 else 0.0
                ret_20 = (close[-1] / close[-21] - 1) * 100 if len(close) >= 21 else None
                ret_60 = (close[-1] / close[-61] - 1) * 100 if len(close) >= 61 else None

                results.append({
                    "name": name,
                    "vol_ratio": round(float(vol_ratio), 2),
                    "change_pct": round(float(change_pct), 2),
                    "close": round(float(close[-1]), 2),
                    "ma20": round(float(ma20), 2),
                    "ma60": round(float(ma60), 2),
                    "dev_ma20": round((close[-1] / ma20 - 1) * 100, 2),
                    "dev_ma60": round((close[-1] / ma60 - 1) * 100, 2),
                    "pos_60": round(float(pos_60), 3),
                    "ret_20": ret_20,
                    "ret_60": ret_60,
                })
            except Exception as e:
                print(f"  分析板块 {name} 失败: {e}")
                continue

    # 过滤放量板块并分类
    bottom, top = [], []
    for r in results:
        if r["vol_ratio"] < VOLUME_RATIO_THRESHOLD:
            continue
        t = _classify_volume_surge(r["close"], r["ma20"], r["ma60"], r["pos_60"])
        if t == "bottom":
            bottom.append(r)
        elif t == "top":
            top.append(r)

    # 按量比降序取最显著
    bottom.sort(key=lambda x: x["vol_ratio"], reverse=True)
    top.sort(key=lambda x: x["vol_ratio"], reverse=True)

    return {
        "total_scanned": len(results),
        "bottom": bottom[:top_n],
        "top": top[:top_n],
    }


def format_sector_volume_report(data):
    """格式化板块放量监控报告

    Args:
        data: analyze_sector_volume 的返回结果

    Returns:
        str 或 None
    """
    if not data:
        return None

    def _fmt_board(r):
        ret60 = f"{r['ret_60']:+.1f}%" if r["ret_60"] is not None else "N/A"
        pos = r["pos_60"] * 100
        return (
            f"{r['name']} | 今日{r['change_pct']:+.2f}% | 量比{r['vol_ratio']} | "
            f"偏离MA20:{r['dev_ma20']:+.1f}% 偏离MA60:{r['dev_ma60']:+.1f}% | "
            f"60日区间位置:{pos:.0f}% | 60日涨跌:{ret60}"
        )

    lines = []
    lines.append("=" * 60)
    lines.append("板块放量监控报告")
    lines.append(f"分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"扫描板块数: {data['total_scanned']}（同花顺行业板块）")
    lines.append(f"放量标准: 今日成交量 ≥ 20日均量的 {VOLUME_RATIO_THRESHOLD} 倍")
    lines.append("=" * 60)

    lines.append("\n【底部放量 · 机会板块】")
    lines.append("  （价格在MA20/MA60下方的低位放量，资金进场迹象）")
    if data["bottom"]:
        for i, r in enumerate(data["bottom"], 1):
            lines.append(f"{i}. {_fmt_board(r)}")
    else:
        lines.append("  今日无显著底部放量板块")

    lines.append("\n【顶部放量 · 风险板块】")
    lines.append("  （价格在MA20/MA60上方的高位放量，出货迹象）")
    if data["top"]:
        for i, r in enumerate(data["top"], 1):
            lines.append(f"{i}. {_fmt_board(r)}")
    else:
        lines.append("  今日无显著顶部放量板块")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def run_sector_volume_analysis(top_n=TOP_N):
    """运行板块放量监控，返回格式化报告

    Args:
        top_n: 每类取最显著的数量

    Returns:
        str 或 None
    """
    print("开始板块放量监控...")
    if not AKSHARE_AVAILABLE:
        print("akshare 不可用，跳过板块放量监控")
        return None

    with stage("板块放量-行业板块列表"):
        names = get_industry_board_names()
    if not names:
        print("获取行业板块列表失败，跳过板块放量监控")
        return None

    print(f"扫描 {len(names)} 个行业板块...")
    with stage(f"板块放量-{len(names)}板块K线扫描"):
        data = analyze_sector_volume(names, top_n=top_n)
    if not data:
        print("板块放量分析无结果")
        return None

    print(
        f"板块放量分析完成: 扫描{data['total_scanned']}个，"
        f"底部放量{len(data['bottom'])}个，顶部放量{len(data['top'])}个"
    )
    return format_sector_volume_report(data)


if __name__ == "__main__":
    report = run_sector_volume_analysis()
    if report:
        print(report)
