#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盯盘监控主程序（模板）
====================================================================

读取当天生成的盯盘参数文件（monitor_params_YYYY-MM-DD.json，由每天的综合投资建议
附带生成，或手工维护），在交易时段按固定间隔轮询实时行情，逐条判断参数文件中的
条件；任一条件满足时，前台弹窗提醒用户执行对应操作，并写入日志。

用法：
    python market_monitor.py                      # 正常盯盘（按 config 轮询）
    python market_monitor.py --test               # 测试：抓一次数据并评估全部条件后退出
    python market_monitor.py --list               # 仅打印参数文件中的条件清单
    python market_monitor.py --params 文件路径     # 指定参数文件
    python market_monitor.py --interval 15        # 轮询间隔（秒），覆盖配置文件
    python market_monitor.py --popup none         # 关闭弹窗，仅写日志
    python market_monitor.py --popup tk           # 强制 tkinter 弹窗

数据源：
    - 实时行情：新浪 hq.sinajs.cn（与项目偏好一致，需 Referer 头）
    - 日K线/技术指标：akshare 新浪源（stock_zh_a_daily / stock_zh_index_daily）
    - 历史两市成交额（连续N日条件）：akshare 东财源（stock_zh_index_daily_em），盘后使用

弹窗：
    - 默认 tkinter（标准库）前台置顶弹窗 + winsound 提示音
    - 可配置为 win10toast / plyer / none（见 monitor_config.json）

====================================================================
盯盘条件类型（condition.type）速查（完整定义见下方 CONDITION_TYPES）：
    价格类:  price_in_range  price_above  price_below
             cross_above    cross_below
    幅度类:  pct_change_ge  pct_change_le  open_pct_ge  open_pct_le
    成交类:  amount_ge  amount_le  amount_ratio_ge  amount_ratio_le
             volume_ratio_ge  volume_ratio_le  half_day_amount_le
             consecutive_amount_le
    技术类:  rsi_ge  rsi_le  kdj_j_ge  kdj_j_le
             price_above_ma  price_below_ma  ma_cross_above  ma_cross_below
             new_20d_high  new_20d_low  macd_golden_cross  macd_dead_cross
    时间类:  at_open  at_time  at_close
    组合类:  all_of  any_of  count_of
====================================================================
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

try:
    import requests
except ImportError:
    requests = None

# 新浪实时行情
SINA_QUOTE_URL = "https://hq.sinajs.cn/list={codes}"
SINA_REFERER = "https://finance.sina.com.cn"

try:
    from momentum_analyzer import get_stock_kline, get_index_kline, _calculate_kdj
except Exception:
    get_stock_kline = get_index_kline = None
    _calculate_kdj = None

CONFIG_PATH = "monitor_config.json"
DEFAULT_CONFIG = {
    "poll_interval_seconds": 20,
    "popup_method": "tk",
    "beep": True,
    "popup_sticky_seconds": 0,
    "log_dir": "monitor_logs",
    "params_file": "",
    "run_window": ["09:15", "16:00"],
    "two_market_codes": ["sh000001", "sz399106"],
    "max_retry_quotes": 3,
}

# 时间常量（分钟）
OPEN_MIN = 9 * 60 + 30        # 09:30
MORNING_END = 11 * 60 + 30    # 11:30
AFTERNOON_START = 13 * 60     # 13:00
CLOSE_MIN = 15 * 60           # 15:00

# 各条件类型所需的数据字段说明（用于校验/告警）
CONDITION_TYPES = {
    # 价格类
    "price_in_range": {"params": ["min", "max"], "need": "quote"},
    "price_above": {"params": ["value"], "need": "quote"},
    "price_below": {"params": ["value"], "need": "quote"},
    "cross_above": {"params": ["value"], "need": "quote_prev"},
    "cross_below": {"params": ["value"], "need": "quote_prev"},
    # 幅度类（相对昨收）
    "pct_change_ge": {"params": ["pct"], "need": "quote"},
    "pct_change_le": {"params": ["pct"], "need": "quote"},
    "open_pct_ge": {"params": ["pct"], "need": "quote"},
    "open_pct_le": {"params": ["pct"], "need": "quote"},
    # 成交类
    "amount_ge": {"params": ["amount"], "need": "amount"},
    "amount_le": {"params": ["amount"], "need": "amount"},
    "amount_ratio_ge": {"params": ["ratio"], "need": "amount_hist"},
    "amount_ratio_le": {"params": ["ratio"], "need": "amount_hist"},
    "volume_ratio_ge": {"params": ["ratio"], "need": "daily"},
    "volume_ratio_le": {"params": ["ratio"], "need": "daily"},
    "half_day_amount_le": {"params": ["amount"], "need": "half_snapshot"},
    "consecutive_amount_le": {"params": ["amount", "days"], "need": "amount_hist"},
    # 技术类（日K + 当日实时合成）
    "rsi_ge": {"params": ["value"], "need": "tech"},
    "rsi_le": {"params": ["value"], "need": "tech"},
    "kdj_j_ge": {"params": ["value"], "need": "tech"},
    "kdj_j_le": {"params": ["value"], "need": "tech"},
    "price_above_ma": {"params": ["ma"], "need": "tech"},
    "price_below_ma": {"params": ["ma"], "need": "tech"},
    "ma_cross_above": {"params": ["ma"], "need": "tech"},
    "ma_cross_below": {"params": ["ma"], "need": "tech"},
    "new_20d_high": {"params": [], "need": "tech"},
    "new_20d_low": {"params": [], "need": "tech"},
    "macd_golden_cross": {"params": [], "need": "tech"},
    "macd_dead_cross": {"params": [], "need": "tech"},
    # 时间类
    "at_open": {"params": [], "need": "time"},
    "at_time": {"params": ["time"], "need": "time"},
    "at_close": {"params": [], "need": "time"},
    # 组合类
    "all_of": {"params": ["conditions"], "need": "sub"},
    "any_of": {"params": ["conditions"], "need": "sub"},
    "count_of": {"params": ["conditions", "min_count"], "need": "sub"},
}


# ============================================================
# 小工具
# ============================================================

def _f(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def now_minute(dt=None):
    dt = dt or datetime.now()
    return dt.hour * 60 + dt.minute


def is_trading_day(dt=None):
    dt = dt or datetime.now()
    return dt.weekday() < 5


def open_minutes(dt=None):
    """已开盘分钟数（剔除午休 11:30-13:00）"""
    m = now_minute(dt)
    if m < OPEN_MIN:
        return 0
    morning = min(max(m - OPEN_MIN, 0), MORNING_END - OPEN_MIN)
    afternoon = max(m - AFTERNOON_START, 0) if m > AFTERNOON_START else 0
    return morning + afternoon


def parse_time_range(s):
    """解析 '9:30-10:00' / '09:30-10:00' → (start_min, end_min)，失败返回 None"""
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*[-~至]\s*(\d{1,2}):(\d{2})\s*", str(s))
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
    return None


def window_range(window):
    """把参数文件中的 window 字段映射为 (start_min, end_min)"""
    if not window:
        return OPEN_MIN, CLOSE_MIN
    r = parse_time_range(window)
    if r:
        return r
    w = str(window).lower()
    if w in ("intraday", "全天", "盘中"):
        return OPEN_MIN, CLOSE_MIN
    if w in ("open", "开盘"):
        return 9 * 60 + 25, 9 * 60 + 45
    if w in ("half", "午前", "上午"):
        return OPEN_MIN, MORNING_END
    if w in ("afternoon", "午后", "下午"):
        return AFTERNOON_START, CLOSE_MIN
    if w in ("close", "尾盘"):
        return 14 * 60 + 30, CLOSE_MIN
    if w in ("after_close", "盘后"):
        return CLOSE_MIN, 24 * 60 - 1
    if w in ("all", "全天"):
        return 0, 24 * 60 - 1
    return OPEN_MIN, CLOSE_MIN


def in_window(now_dt, window):
    m = now_minute(now_dt)
    s, e = window_range(window)
    return s <= m <= e


# ============================================================
# 实时行情（新浪）
# ============================================================

def normalize_code(code, kind):
    """任意代码 → 新浪代码（sh/sz + 6位）"""
    if not code:
        return None
    code = str(code).strip()
    if re.fullmatch(r"(sh|sz)\d{6}", code):
        return code
    if not re.fullmatch(r"\d{6}", code):
        return None
    if kind == "index":
        return ("sz" if code.startswith("399") else "sh") + code
    if code.startswith(("5", "6")):
        return "sh" + code
    return "sz" + code


def fetch_quotes(codes):
    """批量获取实时行情

    返回 {sina_code: {name, open, prev_close, price, high, low, volume, amount, date, time}}
    volume: 股票/ETF为股，指数为手；amount 单位元。
    """
    if not requests:
        print("[行情] 缺少 requests 库")
        return {}
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    out = {}
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(SINA_QUOTE_URL.format(codes=",".join(codes)),
                             headers={"Referer": SINA_REFERER}, timeout=8)
            r.encoding = "gbk"
            break
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt)
    else:
        print(f"[行情] 获取失败: {last_err}")
        return {}
    for line in r.text.strip().splitlines():
        m = re.match(r'var hq_str_(\w+)="([^"]*)"', line)
        if not m:
            continue
        code, body = m.group(1), m.group(2).split(",")
        if len(body) < 32:
            continue
        try:
            quote = {
                "name": body[0],
                "open": _f(body[1], 0.0),
                "prev_close": _f(body[2], 0.0),
                "price": _f(body[3], 0.0),
                "high": _f(body[4], 0.0),
                "low": _f(body[5], 0.0),
                "volume": _f(body[8], 0.0),
                "amount": _f(body[9], 0.0),
                "date": body[30],
                "time": body[31],
            }
            out[code] = quote
        except (ValueError, IndexError):
            continue
    return out


def fetch_market_amount(quotes, market_codes):
    """两市总成交额（亿元），市场代码缺一不可"""
    if not quotes or not market_codes:
        return None
    total = 0.0
    for c in market_codes:
        q = quotes.get(c)
        if not q or q["amount"] <= 0:
            return None
        total += q["amount"]
    return round(total / 1e8, 2)


# ============================================================
# 日K线 / 技术指标（akshare 新浪源）
# ============================================================

_daily_cache = {}
_daily_cache_day = None


def get_daily(code, kind, days=80):
    """获取日K线并缓存（复用 momentum_analyzer，新浪源）

    code 为新浪代码（sh/sz 前缀），内部转成 6 位代码后调用。
    """
    global _daily_cache_day
    today = datetime.now().strftime("%Y-%m-%d")
    if _daily_cache_day != today:
        _daily_cache.clear()
        _daily_cache_day = today
    key = (code, kind)
    if key in _daily_cache:
        return _daily_cache[key]
    raw = re.sub(r"^(sh|sz)", "", code)
    df = None
    if get_index_kline and kind == "index":
        df = get_index_kline(raw, days=days)
    elif get_stock_kline:
        df = get_stock_kline(raw, days=days)
    _daily_cache[key] = df
    return df


def _ema(data, period):
    ema = [data[0]]
    k = 2 / (period + 1)
    for x in data[1:]:
        ema.append(x * k + ema[-1] * (1 - k))
    return ema


def _rsi_wilder(close, period=14):
    if len(close) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(close)):
        d = close[i] - close[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def technical_snapshot(code, kind, quote, daily_df):
    """把当日实时价追加到日K后计算技术指标（近似盘中值）"""
    if daily_df is None or len(daily_df) < 25:
        return {}
    close = [_f(x) for x in daily_df["收盘"].tolist()]
    high = [_f(x) for x in daily_df["最高"].tolist()]
    low = [_f(x) for x in daily_df["最低"].tolist()]
    if quote is None or quote.get("price") is None or quote["price"] <= 0:
        return {}
    s_close = close + [quote["price"]]
    s_high = high + [max(quote["high"], quote["price"])]
    s_low = low + [min(quote["low"], quote["price"])]
    out = {}
    out["rsi"] = _rsi_wilder(s_close)
    if _calculate_kdj:
        try:
            k, d, j = _calculate_kdj(s_high, s_low, s_close)
            out["kdj_j"] = j
        except Exception:
            out["kdj_j"] = None
    ma20 = sum(s_close[-20:]) / 20
    ma5 = sum(s_close[-5:]) / 5
    out["ma5"] = ma5
    out["ma20"] = ma20
    out["prev_close"] = close[-1]
    out["high20"] = max(high[-20:]) if len(high) >= 20 else max(high)
    out["low20"] = min(low[-20:]) if len(low) >= 20 else min(low)
    # MACD(12,26,9)
    if len(s_close) >= 35:
        dif = [a - b for a, b in zip(_ema(s_close, 12), _ema(s_close, 26))]
        dea = _ema(dif, 9)
        out["dif"] = dif[-1]
        out["dea"] = dea[-1]
        out["dif_prev"] = dif[-2]
        out["dea_prev"] = dea[-2]
    return out


def volume_ratio(code, kind, quote, daily_df):
    """盘中量比 = (当日累计量/已开分钟数) / (5日均量/240)

    注意：指数实时 volume 单位为手（×100 折算为股），日K量单位为股；
    股票/ETF 实时与日K单位均为股。
    """
    if quote is None or quote.get("volume") is None or quote["volume"] <= 0:
        return None
    minutes = open_minutes()
    if minutes <= 0:
        return None
    if daily_df is None or "成交量" not in daily_df.columns or len(daily_df) < 6:
        return None
    mult = 100 if kind == "index" else 1
    vols = [_f(x) for x in daily_df["成交量"].iloc[-5:].tolist()]
    vols = [v for v in vols if v and v > 0]
    if not vols:
        return None
    avg5 = sum(vols) / len(vols)
    if avg5 <= 0:
        return None
    per_min_today = quote["volume"] * mult / minutes
    per_min_avg5 = avg5 / 240
    if per_min_avg5 <= 0:
        return None
    return round(per_min_today / per_min_avg5, 2)


# ============================================================
# 历史两市成交额（东财，盘后/连续N日条件用）
# ============================================================

def market_amount_history(days=10):
    """最近 N 个交易日两市成交额（亿元），按日期升序

    [{date: "2026-08-21", amount_yi: 18899.0}, ...]
    """
    try:
        import akshare as ak
    except ImportError:
        print("[历史成交额] akshare 不可用")
        return []
    for attempt in range(3):
        try:
            sh = ak.stock_zh_index_daily_em(symbol="sh000001")
            sz = ak.stock_zh_index_daily_em(symbol="sz399106")
            if sh is None or sz is None or sh.empty or sz.empty:
                return []
            sh["date"] = sh["date"].astype(str)
            sz["date"] = sz["date"].astype(str)
            merged = sh[["date", "amount"]].merge(sz[["date", "amount"]], on="date", suffixes=("_sh", "_sz"))
            merged = merged.sort_values("date").tail(days)
            out = []
            for _, row in merged.iterrows():
                try:
                    out.append({
                        "date": row["date"],
                        "amount_yi": round((float(row["amount_sh"]) + float(row["amount_sz"])) / 1e8, 2),
                    })
                except (ValueError, TypeError):
                    continue
            return out
        except Exception as e:
            if attempt < 2:
                time.sleep(2 + attempt * 2)
            else:
                print(f"[历史成交额] 获取失败: {e}")
    return []


AMOUNT_LOG_FILE = os.path.join("monitor_logs", "daily_amount.json")


def load_amount_log():
    """读取盯盘自记的两市成交额日志 {date: 亿元}（东财不可用时兜底）"""
    try:
        if os.path.exists(AMOUNT_LOG_FILE):
            with open(AMOUNT_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {str(k): float(v) for k, v in data.items() if v}
    except Exception as e:
        print(f"[成交额日志] 读取失败: {e}")
    return {}


def save_amount_log_entry(entry):
    """记录当天两市成交额（仅收盘后写入一次）"""
    try:
        os.makedirs(os.path.dirname(AMOUNT_LOG_FILE), exist_ok=True)
        data = load_amount_log()
        data.update(entry)
        with open(AMOUNT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception as e:
        print(f"[成交额日志] 写入失败: {e}")


def merged_market_history(market_hist, amount_log):
    """东财历史 + 本地日志合并（日志优先），返回 [{date, amount_yi}, ...]"""
    merged = {}
    for x in market_hist:
        merged[x["date"]] = x["amount_yi"]
    merged.update(amount_log)
    out = [{"date": d, "amount_yi": v} for d, v in sorted(merged.items())]
    return out[-12:]


# ============================================================
# 条件评估
# ============================================================

class Context:
    """一次轮询的数据上下文"""

    def __init__(self, now_dt, quotes, market_codes, market_amount_yi,
                 market_hist, amount_log, prev_price, daily, tech, half_snapshot):
        self.now = now_dt
        self.quotes = quotes            # {sina_code: quote}
        self.market_codes = market_codes
        self.market_amount_yi = market_amount_yi
        self.market_hist = market_hist  # list [{date, amount_yi}]（东财）
        self.amount_log = amount_log    # {date: 亿元}（本地自记）
        self.prev_price = prev_price    # {sina_code: price}
        self.daily = daily              # {key: df}
        self.tech = tech                # {sina_code: snapshot}
        self.half_snapshot = half_snapshot  # 11:30 两市成交额快照（亿）

    def combined_hist(self):
        """东财历史 + 本地日志合并后的两市成交额序列"""
        return merged_market_history(self.market_hist, self.amount_log)


def _amount_yi(cond, alert, ctx):
    """取条件的成交额口径：kind=market 用两市，否则用标的实时成交额"""
    if cond.get("kind") == "market" or alert.get("kind") == "market":
        return ctx.market_amount_yi
    code = normalize_code(cond.get("code") or alert.get("code"), cond.get("kind") or alert.get("kind"))
    q = ctx.quotes.get(code) if code else None
    if not q or not q["amount"]:
        return None
    return round(q["amount"] / 1e8, 4)


def _yesterday_amount_yi(cond, alert, ctx):
    """昨日成交额（亿）：市场用合并历史（本地日志优先），标的用日K最后一根"""
    if cond.get("kind") == "market" or alert.get("kind") == "market":
        hist = ctx.combined_hist()
        # 取今天之前最近的一天
        today = ctx.now.strftime("%Y-%m-%d")
        past = [x for x in hist if x["date"] < today]
        return past[-1]["amount_yi"] if past else (ctx.market_hist[-1]["amount_yi"] if ctx.market_hist else None)
    code = normalize_code(cond.get("code") or alert.get("code"), cond.get("kind") or alert.get("kind"))
    kind = cond.get("kind") or alert.get("kind")
    df = ctx.daily.get((code, kind))
    if df is None:
        df = get_daily(code, kind)
        ctx.daily[(code, kind)] = df
    if df is not None and "成交额" in df.columns and len(df) > 0:
        return round(_f(df["成交额"].iloc[-1], 0.0) / 1e8, 4)
    return None


def _quote(cond, alert, ctx):
    code = normalize_code(cond.get("code") or alert.get("code"), cond.get("kind") or alert.get("kind"))
    if not code:
        return None, None, None
    return ctx.quotes.get(code), code, cond.get("kind") or alert.get("kind")


def evaluate_condition(cond, alert, ctx):
    """评估单条条件，返回 (是否满足, 说明)"""
    if not isinstance(cond, dict):
        return False, f"条件格式错误: {cond}"
    ctype = cond.get("type")
    if ctype not in CONDITION_TYPES:
        return False, f"未知条件类型: {ctype}"
    need = CONDITION_TYPES[ctype]["need"]
    code_override = cond.get("code") or alert.get("code")

    # ---- 价格类 ----
    price_field = cond.get("price", "now")  # now/open/high/low
    if need in ("quote", "quote_prev", "amount", "daily", "tech", "amount_hist"):
        q, code, kind = _quote(cond, alert, ctx)
    else:
        q = code = kind = None

    def P(field=None):
        if q is None:
            return None
        f = field or price_field
        if f == "now":
            f = "price"
        return q.get(f)

    if ctype == "price_in_range":
        p = P()
        if p is None:
            return False, "无行情"
        lo, hi = _f(cond.get("min")), _f(cond.get("max"))
        if lo is None or hi is None:
            return False, "缺少 min/max"
        if lo <= p <= hi:
            return True, f"{q['name']}现价 {p} 落入 [{lo}, {hi}]"
        return False, f"现价 {p}（区间 {lo}-{hi}）"

    if ctype == "price_above":
        p, v = P(), _f(cond.get("value"))
        if p is None or v is None:
            return False, "无行情/缺value"
        return (p >= v, f"现价 {p} ≥ {v}") if p >= v else (False, f"现价 {p} < {v}")

    if ctype == "price_below":
        p, v = P(), _f(cond.get("value"))
        if p is None or v is None:
            return False, "无行情/缺value"
        return (p <= v, f"现价 {p} ≤ {v}") if p <= v else (False, f"现价 {p} > {v}")

    if ctype in ("cross_above", "cross_below"):
        v = _f(cond.get("value"))
        p = P()
        if p is None or v is None:
            return False, "无行情/缺value"
        prev = ctx.prev_price.get(code)
        if prev is None:
            prev = q.get("open") or q.get("prev_close")
        if prev is None:
            return False, "无前值"
        if ctype == "cross_above":
            if prev < v <= p:
                return True, f"上穿 {v}（前值 {prev} → 现价 {p}）"
            return False, f"未上穿 {v}（前值 {prev}，现价 {p}）"
        else:
            if prev >= v > p:
                return True, f"下穿 {v}（前值 {prev} → 现价 {p}）"
            return False, f"未下穿 {v}（前值 {prev}，现价 {p}）"

    # ---- 幅度类 ----
    if ctype in ("pct_change_ge", "pct_change_le"):
        p, pc = P(), _f(q["prev_close"] if q else None)
        thr = _f(cond.get("pct"))
        if p is None or pc is None or pc <= 0 or thr is None:
            return False, "无行情/缺pct"
        chg = (p / pc - 1) * 100
        if ctype == "pct_change_ge":
            return (chg >= thr, f"涨幅 {chg:+.2f}% ≥ {thr}%")
        return (chg <= -thr, f"跌幅 {chg:+.2f}% ≥ {thr}%")

    if ctype in ("open_pct_ge", "open_pct_le"):
        op, pc = q["open"] if q else None, q["prev_close"] if q else None
        thr = _f(cond.get("pct"))
        if not op or not pc or pc <= 0 or thr is None:
            return False, "无行情/缺pct"
        chg = (op / pc - 1) * 100
        if ctype == "open_pct_ge":
            return (chg >= thr, f"开盘涨幅 {chg:+.2f}% ≥ {thr}%")
        return (chg <= -thr, f"开盘跌幅 {chg:+.2f}% ≥ {thr}%")

    # ---- 成交类 ----
    if ctype == "amount_ge":
        a, v = _amount_yi(cond, alert, ctx), _f(cond.get("amount"))
        if a is None or v is None:
            return False, "无成交额/缺amount"
        return (a >= v, f"成交额 {a}亿 ≥ {v}亿") if a >= v else (False, f"成交额 {a}亿 < {v}亿")

    if ctype == "amount_le":
        a, v = _amount_yi(cond, alert, ctx), _f(cond.get("amount"))
        if a is None or v is None:
            return False, "无成交额/缺amount"
        return (a <= v, f"成交额 {a}亿 ≤ {v}亿") if a <= v else (False, f"成交额 {a}亿 > {v}亿")

    if ctype in ("amount_ratio_ge", "amount_ratio_le"):
        a = _amount_yi(cond, alert, ctx)
        ya = _yesterday_amount_yi(cond, alert, ctx)
        ratio = _f(cond.get("ratio"))
        if a is None or ya is None or ya <= 0 or ratio is None:
            return False, "缺昨日成交额/ratio"
        r = a / ya
        if ctype == "amount_ratio_ge":
            return (r >= ratio, f"成交额 {a}亿 / 昨日 {ya}亿 = {r:.2f} ≥ {ratio}") if r >= ratio else (False, f"量能比 {r:.2f} < {ratio}")
        return (r <= ratio, f"成交额 {a}亿 / 昨日 {ya}亿 = {r:.2f} ≤ {ratio}") if r <= ratio else (False, f"量能比 {r:.2f} > {ratio}")

    if ctype in ("volume_ratio_ge", "volume_ratio_le"):
        if code is None:
            return False, "缺标的"
        df = ctx.daily.get((code, kind))
        if df is None:
            df = get_daily(code, kind)
            ctx.daily[(code, kind)] = df
        vr = volume_ratio(code, kind, q, df)
        thr = _f(cond.get("ratio"))
        if vr is None or thr is None:
            return False, "量比不可用/缺ratio"
        if ctype == "volume_ratio_ge":
            return (vr >= thr, f"量比 {vr} ≥ {thr}") if vr >= thr else (False, f"量比 {vr} < {thr}")
        return (vr <= thr, f"量比 {vr} ≤ {thr}") if vr <= thr else (False, f"量比 {vr} > {thr}")

    if ctype == "half_day_amount_le":
        a = ctx.half_snapshot
        v = _f(cond.get("amount"))
        if a is None or v is None:
            return False, "半日快照未取得"
        return (a <= v, f"半日成交额 {a}亿 ≤ {v}亿") if a <= v else (False, f"半日成交额 {a}亿 > {v}亿")

    if ctype == "consecutive_amount_le":
        v, days = _f(cond.get("amount")), int(cond.get("days") or 1)
        hist = ctx.combined_hist()
        if v is None or not hist:
            return False, "缺历史成交额"
        today = ctx.now.strftime("%Y-%m-%d")
        if now_minute(ctx.now) >= CLOSE_MIN:
            recent = [x for x in hist if x["date"] <= today][-days:]
        else:
            recent = [x for x in hist if x["date"] < today][-days:]
        if len(recent) < days:
            return False, f"历史数据不足（{len(recent)}/{days}）"
        vals = [x["amount_yi"] for x in recent]
        if all(x <= v for x in vals):
            return True, f"连续{days}日两市成交额 {vals} ≤ {v}亿"
        return False, f"近{days}日成交额 {vals}（阈值 {v}亿）"

    # ---- 技术类 ----
    if need == "tech":
        if code is None:
            return False, "缺标的"
        df = ctx.daily.get((code, kind))
        if df is None:
            df = get_daily(code, kind)
            ctx.daily[(code, kind)] = df
        snap = ctx.tech.get(code)
        if snap is None:
            snap = technical_snapshot(code, kind, q, df)
            ctx.tech[code] = snap
        p = q["price"] if q else None
        v = _f(cond.get("value"))

        if ctype in ("rsi_ge", "rsi_le"):
            rsi = snap.get("rsi")
            if rsi is None or v is None:
                return False, "RSI不可用"
            return (rsi >= v, f"RSI {rsi:.1f} ≥ {v}") if ctype == "rsi_ge" and rsi >= v else \
                   (False, f"RSI {rsi:.1f}（阈值 {v}）") if ctype == "rsi_ge" else \
                   (rsi <= v, f"RSI {rsi:.1f} ≤ {v}") if rsi <= v else (False, f"RSI {rsi:.1f}（阈值 {v}）")

        if ctype in ("kdj_j_ge", "kdj_j_le"):
            j = snap.get("kdj_j")
            if j is None or v is None:
                return False, "KDJ不可用"
            return (j >= v, f"KDJ J {j:.1f} ≥ {v}") if ctype == "kdj_j_ge" and j >= v else \
                   (False, f"KDJ J {j:.1f}（阈值 {v}）") if ctype == "kdj_j_ge" else \
                   (j <= v, f"KDJ J {j:.1f} ≤ {v}") if j <= v else (False, f"KDJ J {j:.1f}（阈值 {v}）")

        if ctype in ("price_above_ma", "price_below_ma"):
            ma = _f(cond.get("ma")) or 20
            mv = snap.get(f"ma{int(ma)}") or snap.get("ma20")
            if p is None or mv is None:
                return False, "均线不可用"
            if ctype == "price_above_ma":
                return (p >= mv, f"现价 {p} ≥ MA{int(ma)} {mv:.2f}") if p >= mv else (False, f"现价 {p} < MA{int(ma)} {mv:.2f}")
            return (p <= mv, f"现价 {p} ≤ MA{int(ma)} {mv:.2f}") if p <= mv else (False, f"现价 {p} > MA{int(ma)} {mv:.2f}")

        if ctype in ("ma_cross_above", "ma_cross_below"):
            ma = _f(cond.get("ma")) or 20
            mv = snap.get(f"ma{int(ma)}") or snap.get("ma20")
            pc = snap.get("prev_close")
            if p is None or mv is None or pc is None:
                return False, "均线不可用"
            if ctype == "ma_cross_above":
                return (pc <= mv < p, f"上穿 MA{int(ma)} {mv:.2f}（昨收 {pc} → 现价 {p}）") if pc <= mv < p else (False, f"未上穿 MA{int(ma)}")
            return (pc >= mv > p, f"下穿 MA{int(ma)} {mv:.2f}（昨收 {pc} → 现价 {p}）") if pc >= mv > p else (False, f"未下穿 MA{int(ma)}")

        if ctype == "new_20d_high":
            h20 = snap.get("high20")
            hp = q["high"] if q else None
            if h20 is None or hp is None:
                return False, "数据不足"
            return (hp >= h20, f"高点 {hp} 创20日新高（前高 {h20}）") if hp >= h20 else (False, f"高点 {hp} < 20日高 {h20}")

        if ctype == "new_20d_low":
            l20 = snap.get("low20")
            lp = q["low"] if q else None
            if l20 is None or lp is None:
                return False, "数据不足"
            return (lp <= l20, f"低点 {lp} 创20日新低（前低 {l20}）") if lp <= l20 else (False, f"低点 {lp} > 20日低 {l20}")

        if ctype in ("macd_golden_cross", "macd_dead_cross"):
            dif, dea = snap.get("dif"), snap.get("dea")
            d1, e1 = snap.get("dif_prev"), snap.get("dea_prev")
            if None in (dif, dea, d1, e1):
                return False, "MACD数据不足"
            if ctype == "macd_golden_cross":
                return (d1 <= e1 < dif > dea, f"MACD金叉（DIF {dif:.3f} 上穿 DEA {dea:.3f}）") if d1 <= e1 and dif > dea else (False, "MACD未金叉")
            return (d1 >= e1 > dif < dea, f"MACD死叉（DIF {dif:.3f} 下穿 DEA {dea:.3f}）") if d1 >= e1 and dif < dea else (False, "MACD未死叉")

    # ---- 时间类 ----
    if ctype == "at_open":
        m = now_minute(ctx.now)
        return (OPEN_MIN <= m <= OPEN_MIN + 15, "开盘提醒时段（9:30-9:45）") if OPEN_MIN <= m <= OPEN_MIN + 15 else (False, "未到/已过开盘提醒时段")

    if ctype == "at_time":
        t = cond.get("time")
        m = now_minute(ctx.now)
        hm = re.fullmatch(r"(\d{1,2}):(\d{2})", str(t))
        if not hm:
            return False, f"at_time 时间格式错误: {t}"
        target = int(hm.group(1)) * 60 + int(hm.group(2))
        return (m >= target, f"到达定时点 {t}") if m >= target else (False, f"未到 {t}")

    if ctype == "at_close":
        return (now_minute(ctx.now) >= CLOSE_MIN + 5, "已到收盘后时段") if now_minute(ctx.now) >= CLOSE_MIN + 5 else (False, "未收盘")

    # ---- 组合类 ----
    if ctype in ("all_of", "any_of", "count_of"):
        subs = cond.get("conditions", [])
        if not isinstance(subs, list) or not subs:
            return False, "组合缺少 conditions"
        results = [evaluate_condition(s, alert, ctx) for s in subs]
        if ctype == "all_of":
            if all(r[0] for r in results):
                return True, "；".join(r[1] for r in results if r[0]) or "全部子条件满足"
            return False, " | ".join(r[1] for r in results if not r[0])[:120]
        if ctype == "any_of":
            hits = [r for r in results if r[0]]
            if hits:
                return True, hits[0][1]
            return False, " | ".join(r[1] for r in results)[:120]
        # count_of
        mc = int(cond.get("min_count") or 1)
        hits = [r for r in results if r[0]]
        if len(hits) >= mc:
            return True, f"{len(hits)}/{len(subs)} 个子条件满足（要求≥{mc}）: " + "；".join(r[1] for r in hits)
        return False, f"{len(hits)}/{len(subs)} 子条件满足（要求≥{mc}）"

    return False, f"条件类型 {ctype} 未实现"


def evaluate_alert(alert, ctx):
    """评估一条告警，返回 (是否触发, 说明)。window 由调用方控制。"""
    if not isinstance(alert.get("condition"), dict):
        return False, "缺少 condition"
    try:
        return evaluate_condition(alert["condition"], alert, ctx)
    except Exception as e:
        return False, f"评估异常: {e}"


# ============================================================
# 前台弹窗
# ============================================================

class PopupManager:
    def __init__(self, method="tk", beep=True, sticky=0):
        self.method = method
        self.beep = beep
        self.sticky = sticky
        self._root = None
        self._queue = []
        self._toast = None
        if method in ("auto", "tk"):
            self._init_tk()
            if self._root is None and method == "auto":
                self._try_toast()
        elif method == "toast":
            self._try_toast()

    def _init_tk(self):
        try:
            import tkinter as tk
            self._root = tk.Tk()
            self._root.withdraw()
            self.method = "tk"
        except Exception as e:
            print(f"[弹窗] tkinter 不可用: {e}")
            self._root = None
            self.method = "print"

    def _try_toast(self):
        try:
            from win10toast import ToastNotifier
            self._toast = ToastNotifier()
            self.method = "toast"
        except Exception:
            try:
                from plyer import notification  # noqa
                self.method = "plyer"
            except Exception:
                self.method = "print"

    def notify(self, title, message):
        self._queue.append((title, message))

    def pump(self):
        """主线程调用：处理队列中的弹窗"""
        if not self._queue:
            return
        items, self._queue = self._queue, []
        for title, msg in items:
            if self.beep:
                try:
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                except Exception:
                    pass
            if self.method == "tk" and self._root is not None:
                self._show_tk(title, msg)
            elif self.method == "toast" and self._toast is not None:
                try:
                    self._toast.show_toast(title, msg, duration=10, threaded=True)
                except Exception as e:
                    print(f"[弹窗] toast 失败: {e}")
            elif self.method == "plyer":
                try:
                    from plyer import notification
                    notification.notify(title=title, message=msg, timeout=10)
                except Exception as e:
                    print(f"[弹窗] plyer 失败: {e}")
            else:
                print(f"\n*** 条件触发提醒 ***\n{title}\n{msg}\n")

    def _show_tk(self, title, msg):
        try:
            import tkinter as tk
            top = tk.Toplevel(self._root)
            top.title(title)
            top.attributes("-topmost", True)
            top.geometry("+100+100")
            tk.Label(top, text=msg, justify="left", font=("Microsoft YaHei", 11),
                     padx=16, pady=12, wraplength=520).pack()
            tk.Button(top, text="知道了，去执行", font=("Microsoft YaHei", 10),
                      command=top.destroy).pack(pady=(0, 12))
            top.lift()
            top.focus_force()
            self._root.update()
        except Exception as e:
            print(f"[弹窗] tk 窗口失败: {e}")

    def close(self):
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass


# ============================================================
# 参数文件加载
# ============================================================

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print(f"[配置] 读取失败，使用默认: {e}")
    return cfg


def resolve_params_file(cfg, today):
    """定位参数文件：--params > config > 根目录/归档目录"""
    if cfg.get("params_file") and os.path.exists(cfg["params_file"]):
        return cfg["params_file"]
    cands = [
        f"monitor_params_{today}.json",
        os.path.join(f"archive_{today}", f"monitor_params_{today}.json"),
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    # 回退：找最新的 monitor_params_*.json
    import glob
    hits = glob.glob("monitor_params_*.json") + glob.glob(os.path.join("archive_*", "monitor_params_*.json"))
    if hits:
        hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return hits[0]
    return None


def load_params(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_message(alert, detail):
    parts = []
    prio = alert.get("priority", "中")
    parts.append(f"[{prio}优先级] {alert.get('group', '')} {alert.get('name', '')} ({alert.get('code', '')})")
    if detail:
        parts.append(f"触发依据: {detail}")
    if alert.get("action"):
        parts.append(f"→ 操作: {alert['action']}")
    if alert.get("message"):
        parts.append(alert["message"])
    return "\n".join(parts)


# ============================================================
# 主流程
# ============================================================

def plan_summary(params):
    alerts = params.get("alerts", [])
    meta = params.get("meta", {})
    lines = []
    lines.append("=" * 60)
    lines.append(f"盯盘计划: {meta.get('date', '?')}  {meta.get('strategy', '')}")
    lines.append(f"条件总数: {len(alerts)} 条")
    by_group = {}
    for a in alerts:
        by_group.setdefault(a.get("group", "未分组"), []).append(a)
    for g, items in by_group.items():
        lines.append(f"  - {g}: {len(items)} 条")
    lines.append("=" * 60)
    return "\n".join(lines)


def run(params, cfg, args):
    today = datetime.now().strftime("%Y-%m-%d")
    meta_date = params.get("meta", {}).get("date", "")
    if meta_date and meta_date != today:
        print(f"[提示] 参数日期 {meta_date} 与今天 {today} 不一致，按测试模式继续运行")

    alerts = params.get("alerts", [])
    if not alerts:
        print("[警告] 参数文件中没有 alerts")
        return 1

    # 校验条件类型
    unknown = {a.get("id", "?") for a in alerts if a.get("condition", {}).get("type") not in CONDITION_TYPES}
    if unknown:
        print(f"[警告] 以下条件类型未知将被跳过: {unknown}")

    market_codes = cfg.get("two_market_codes") or ["sh000001", "sz399106"]
    interval = args.interval or int(cfg.get("poll_interval_seconds", 20))

    os.makedirs(cfg.get("log_dir", "monitor_logs"), exist_ok=True)
    log_path = os.path.join(cfg.get("log_dir", "monitor_logs"), f"monitor_log_{today}.txt")

    popup = PopupManager(method=args.popup or cfg.get("popup_method", "tk"),
                         beep=cfg.get("beep", True))

    # 预收集所有需要的行情代码
    all_codes = []
    for a in alerts:
        c = normalize_code(a.get("code"), a.get("kind"))
        if c:
            all_codes.append(c)
    all_codes += market_codes
    all_codes = [c for c in dict.fromkeys(all_codes) if c]

    fired = set()          # 当日已触发（once）的 alert id
    last_fired = {}        # alert id -> 时间戳（cooldown）
    prev_price = {}
    daily, tech = {}, {}
    half_snapshot = None
    amount_log = load_amount_log()

    window_start, window_end = cfg.get("run_window") or ["09:15", "16:00"]

    def parse_hhmm(s):
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(s))
        return int(m.group(1)) * 60 + int(m.group(2)) if m else None

    run_s = parse_hhmm(window_start) or (9 * 60 + 15)
    run_e = parse_hhmm(window_end) or (16 * 60)

    print(plan_summary(params))
    if args.test:
        print("[测试模式] 单次抓取并评估，不写入当日触发状态\n")

    # 盘前预取历史成交额（连续N日条件用）
    market_hist = market_amount_history(days=12)
    if market_hist:
        print(f"[历史] 近{len(market_hist)}日两市成交额（亿）: {[x['amount_yi'] for x in market_hist[-5:]]}")

    def evaluate_pass(now_dt, log_f, quiet=False):
        nonlocal half_snapshot, daily, tech, amount_log
        m = now_minute(now_dt)
        quotes = fetch_quotes(all_codes)
        if not quotes:
            return
        market_amount = fetch_market_amount(quotes, market_codes)
        if not quiet:
            if market_amount is not None:
                print(f"[{now_dt:%H:%M:%S}] 两市成交额 {market_amount} 亿，行情 {len(quotes)} 条")
            else:
                print(f"[{now_dt:%H:%M:%S}] 行情 {len(quotes)} 条（两市成交额暂缺）")

        # 收盘后记录当天两市成交额（供连续N日条件使用，东财不可用时兜底）
        if market_amount is not None and m >= CLOSE_MIN:
            today_key = now_dt.strftime("%Y-%m-%d")
            if amount_log.get(today_key) != market_amount:
                amount_log[today_key] = market_amount
                save_amount_log_entry({today_key: market_amount})
                print(f"[收盘] 记录 {today_key} 两市成交额 {market_amount} 亿")

        # 半日成交额快照（11:30-13:00 取一次）
        if half_snapshot is None and market_amount is not None:
            if MORNING_END <= m < AFTERNOON_START:
                half_snapshot = market_amount
                print(f"[半日] 11:30 两市成交额快照 {half_snapshot} 亿")
            elif m > AFTERNOON_START + 5:
                half_snapshot = market_amount  # 错过午盘，用当前值近似
                print(f"[半日] 错过午盘快照，用当前成交额近似 {half_snapshot} 亿")

        ctx = Context(now_dt, quotes, market_codes, market_amount, market_hist,
                      amount_log, prev_price, daily, tech, half_snapshot)

        for alert in alerts:
            aid = alert.get("id") or f"{alert.get('group')}|{alert.get('name')}|{alert.get('condition')}"
            ctype = alert.get("condition", {}).get("type")
            if ctype not in CONDITION_TYPES:
                continue
            # 时间窗过滤（时间类条件不受 window 限制，once 控制）
            if ctype not in ("at_open", "at_time", "at_close") and not in_window(now_dt, alert.get("window", "intraday")):
                continue
            # once 去重
            if alert.get("once", False) and aid in fired:
                continue
            # cooldown 去重
            cd = int(alert.get("cooldown") or 0)
            if cd > 0 and aid in last_fired and (time.time() - last_fired[aid]) < cd:
                continue

            fired_now, detail = evaluate_alert(alert, ctx)
            if args.test and not quiet:
                mark = "✅ 触发" if fired_now else "· 未触发"
                print(f"  {mark} [{aid}] {alert.get('name', '')} -> {detail}")
            if fired_now:
                msg = build_message(alert, detail)
                print(f"\n*** 触发 [{now_dt:%H:%M:%S}] {aid} ***\n{msg}\n")
                if log_f:
                    log_f.write(f"[{now_dt:%Y-%m-%d %H:%M:%S}] 触发 {aid}: {msg}\n")
                    log_f.flush()
                popup.notify(f"盯盘提醒 - {alert.get('name', '')}", msg)
                fired.add(aid)
                last_fired[aid] = time.time()

        # 更新前值（用于 cross_* 判断）
        for c, q in quotes.items():
            if q["price"] and q["price"] > 0:
                prev_price[c] = q["price"]

    with open(log_path, "a", encoding="utf-8") as log_f:
        log_f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 启动盯盘 =====\n")
        log_f.write(plan_summary(params) + "\n")

        if args.test:
            evaluate_pass(datetime.now(), log_f, quiet=False)
            popup.pump()
            popup.close()
            return 0

        while True:
            now_dt = datetime.now()
            m = now_minute(now_dt)
            if m < run_s:
                print(f"[等待] {now_dt:%H:%M:%S} 未到开盘时间 {window_start}，每 60s 检查一次")
                time.sleep(60)
                continue
            if m >= run_e:
                print(f"[结束] 已过运行窗口 {window_end}，执行收盘后条件并退出")
                # 收盘后条件：consecutive / at_close / at_time 晚些时段
                mhist = market_amount_history(days=12)
                if mhist:
                    market_hist[:] = mhist
                evaluate_pass(now_dt, log_f, quiet=False)
                popup.pump()
                break

            evaluate_pass(now_dt, log_f)
            popup.pump()
            time.sleep(interval)

    popup.close()
    print(f"[完成] 日志: {log_path}")
    return 0


def cmd_list(params):
    alerts = params.get("alerts", [])
    print(plan_summary(params))
    for i, a in enumerate(alerts, 1):
        print(f"{i:>2}. [{a.get('priority', '中')}] {a.get('group', '')} | "
              f"{a.get('name', '')} ({a.get('code', '')}) | {a.get('condition', {}).get('type', '?')} | {a.get('action', '')}")


def main():
    ap = argparse.ArgumentParser(description="盯盘监控（读取 monitor_params_*.json 条件并弹窗提醒）")
    ap.add_argument("--params", help="盯盘参数文件路径")
    ap.add_argument("--list", action="store_true", help="仅打印条件清单")
    ap.add_argument("--test", action="store_true", help="单次抓取评估后退出")
    ap.add_argument("--interval", type=int, default=0, help="轮询间隔（秒）")
    ap.add_argument("--popup", choices=["auto", "tk", "toast", "none", "print"], default="", help="弹窗方式")
    args = ap.parse_args()

    cfg = load_config()
    if args.params:
        cfg["params_file"] = args.params
    if args.popup == "none":
        args.popup = "print"

    today = datetime.now().strftime("%Y-%m-%d")
    path = resolve_params_file(cfg, today)
    if not path:
        print(f"[错误] 未找到盯盘参数文件。请先生成（generate_monitor_params.py）或指定 --params。")
        return 1
    print(f"[参数] 使用文件: {path}")

    params = load_params(path)
    if args.list:
        cmd_list(params)
        return 0
    return run(params, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
