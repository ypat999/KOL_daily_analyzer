# -*- coding: utf-8 -*-
"""指数/个股/ETF 日K 本地持久缓存（与历史K线下载工具共用同一份 csv 数据）

数据目录与文件格式完全对齐 D:/work/quant/gpt时代的量化交易/code/data_feed
（data_feed_new.py → data_feed_stock.py / data_feed_fund.py，以及同源的 data_feed.py）：
  - 目录：D:/work/quant/stock_data/csv
  - 个股：daily_{6位}.csv        新浪 stock_zh_a_daily(adjust='qfq')，前复权
  - 基金/ETF：fund_daily_{6位}.csv   新浪 fund_etf_hist_sina
  - 指数：index_{6位}.csv        新浪 stock_zh_index_daily（该工具未覆盖指数，本模块补上，
          列格式沿用同一风格：date,open,close,high,low,volume,amount）
统一 utf-8-sig、index=False、英文列名、date 为 'YYYY-MM-DD'，新数据追加在文件尾部。

增量策略（对齐 data_feed_stock._get_generic_history_data，保证首尾衔接、绝不留缺口）：
  - 本地已覆盖所需日期(need_until) → 直接读盘切片返回，完全不联网：回填/复盘由
    "每次全量重下" 变为 "只读本地"，耗时从分钟级降到秒级；
  - 未覆盖 → 起点恒为 "本地最后一个交易日的下一自然日"，抓取后与本地 concat、按日期去重
    排序再整体回写。新增段与旧数据严丝合缝，不会在中间漏掉任何一天——因为现有的回填
    判断逻辑不会回补中间缺失的日期，一旦出现空洞就永久错算。
  - 新浪日K无服务端增量接口，start/end 只是客户端切片，单次请求仍会下全量；但只在
    "确实缺数据" 时才发请求，且只回写一次。
  - 另叠进程内内存缓存 + 覆盖标记，同进程内同标的只处理一次。

对外返回仍是中文列 df（日期/开盘/最高/最低/收盘/成交量/成交额），与旧接口保持一致，
调用方（backtest_analyzer / momentum_analyzer）无需改动取值方式。
"""
import os
import threading
import time
from datetime import date, datetime, timedelta

import pandas as pd

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except Exception:
    ak = None
    AKSHARE_AVAILABLE = False

from market_symbols import is_etf_code, sina_symbol, strip_prefix

# 与历史K线下载工具共用的数据目录（务必与 data_feed_stock.py / data_feed_fund.py 保持一致）
DATA_DIR = r"D:\work\quant\stock_data\csv"

MAX_RETRIES = 3
REQUEST_INTERVAL = 1.0  # 联网请求最小间隔（秒），避免被新浪封 IP

# 每类标的落盘文件的规范列顺序。已有文件保持其自身表头顺序（concat 时旧列在前），
# 仅在新建文件时按此顺序补齐缺失列，使整个目录的表头统一。
_CANON_COLS = {
    "stock": ["date", "code", "open", "close", "high", "low", "volume", "amount",
              "amplitude", "pct_change", "change", "turnover", "outstanding_share"],
    "etf": ["date", "open", "close", "high", "low", "volume", "amount",
            "amplitude", "pct_change", "change", "turnover", "postVol", "postAmt"],
    "index": ["date", "open", "close", "high", "low", "volume", "amount"],
}
_FILE_PREFIX = {"stock": "daily_", "etf": "fund_daily_", "index": "index_"}

_EN_TO_CN = {"date": "日期", "open": "开盘", "high": "最高", "low": "最低",
             "close": "收盘", "volume": "成交量", "amount": "成交额"}

_lock = threading.RLock()
_locks = {}          # cache_key -> Lock，避免同标的并发重复抓取
_mem_cache = {}      # cache_key -> 全量 df（英文列）
_mem_covered = {}    # cache_key -> date，已知本地数据覆盖到的日期（上限）
_last_request_time = 0.0


# ---------------------------------------------------------------- 基础工具

def _cache_key(code, kind):
    return f"{kind}:{code}"


def _file_path(code, kind):
    return os.path.join(DATA_DIR, f"{_FILE_PREFIX[kind]}{code}.csv")


def _get_lock(key):
    with _lock:
        lk = _locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _locks[key] = lk
        return lk


def _wait_for_rate_limit():
    global _last_request_time
    while True:
        with _lock:
            wait = _last_request_time + REQUEST_INTERVAL - time.time()
            if wait <= 0:
                _last_request_time = time.time()
                return
        time.sleep(min(wait, 0.5))


def _norm_date(value):
    """'YYYY-MM-DD' / date / datetime / None → datetime.date"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _df_max_date(df):
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    try:
        return pd.to_datetime(df["date"], errors="coerce").max().date()
    except Exception:
        return None


def _is_cache_fresh(path):
    """文件修改时间是否落在"最近一次收盘(15:00)"之后 → 数据已是最新可得

    与 data_feed_stock._is_cache_fresh 同规则：当日 15:00 后更新算新鲜；
    15:00 前则要求昨日 15:00 后更新（此时最新可得数据仍是昨日收盘）。
    """
    if not os.path.exists(path):
        return False
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        now = datetime.now()
        cutoff = now.replace(hour=15, minute=0, second=0, microsecond=0)
        if now >= cutoff:
            return mtime >= cutoff
        prev_cutoff = (now - timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        return mtime >= prev_cutoff
    except Exception:
        return False


# ---------------------------------------------------------------- 读写本地 csv

def _load_local(path):
    """读取本地 csv（英文列，date 统一为 'YYYY-MM-DD' 字符串，按日期升序去重）

    该目录同时被历史K线下载工具批量更新，读到的可能是"正在写入中"的文件：
    行残缺会解析出 NaT 日期，此时返回 None（视为不可用、由调用方全量重抓覆盖），
    绝不以残缺历史为基准做增量——否则会把中间缺失的日期永久固化下来。
    """
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    df = df.copy()
    dt = pd.to_datetime(df["date"], errors="coerce")
    if dt.isna().any():
        return None
    df["date"] = dt.dt.strftime("%Y-%m-%d")
    df = df.drop_duplicates(subset=["date"], keep="last")
    return df.sort_values("date").reset_index(drop=True)


def _canonicalize(df, kind):
    """新建文件时按规范列顺序补齐缺失列，保证目录内表头统一"""
    canon = _CANON_COLS[kind]
    for col in canon:
        if col not in df.columns:
            df[col] = ""
    return df.reindex(columns=canon)


def _save_local(path, df):
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


# ---------------------------------------------------------------- 抓取与清洗

def _normalize_fetched(df, kind, code, start=None):
    """新浪原始返回 → 落盘列格式（英文列、date 字符串、按规范列序、去重升序）

    返回 None 表示抓取失败；返回空 DataFrame 表示请求成功但该区间无数据。
    """
    if df is None:
        return None
    canon = _CANON_COLS[kind]
    if len(df) == 0:
        return pd.DataFrame(columns=canon)
    df = df.copy()
    if "date" not in df.columns:
        df = df.reset_index()
    if "date" not in df.columns:
        return pd.DataFrame(columns=canon)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["date"])
    if "close" in df.columns:
        df = df[pd.to_numeric(df["close"], errors="coerce").notna()]
    if start is not None:
        df = df[df["date"] >= start]
    if kind == "stock":
        df["code"] = code
    ordered = [c for c in canon if c in df.columns] + [c for c in df.columns if c not in canon]
    df = df[ordered]
    df = df.drop_duplicates(subset=["date"], keep="last")
    return df.sort_values("date").reset_index(drop=True)


def _fetch_stock(sina, start, end):
    """个股日K（前复权优先）。全部异常 → None；请求成功但无数据 → 空 DataFrame"""
    got = None
    for adj in ("qfq", ""):
        try:
            df = ak.stock_zh_a_daily(symbol=sina, start_date=start, end_date=end, adjust=adj)
        except Exception:
            continue
        if df is not None and not df.empty:
            return df
        got = df
    return got


def _fetch(code, kind, start, end):
    """增量抓取 [start, end]；失败返回 None，成功返回（可能为空的）原始 df"""
    sina = sina_symbol(code, kind)
    if sina is None or not AKSHARE_AVAILABLE:
        return None
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_rate_limit()
            if kind == "stock":
                df = _fetch_stock(sina, start, end)
                if df is None:
                    raise RuntimeError("sina 请求全部失败")
            elif kind == "etf":
                df = ak.fund_etf_hist_sina(symbol=sina)
            else:
                df = ak.stock_zh_index_daily(symbol=sina)
            return _normalize_fetched(df, kind, code, start=start)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES - 1:
                time.sleep((attempt + 1) * 2)
    print(f"  K线 {sina}({kind}) 抓取失败({MAX_RETRIES}次重试): {last_err}")
    return None


# ---------------------------------------------------------------- 增量更新主流程

def _store_mem(key, df, covered):
    with _lock:
        _mem_cache[key] = df
        _mem_covered[key] = covered


def _load_or_update(code, kind, need_until=None, force_refresh=False):
    """确保本地 csv 覆盖到 need_until（含），返回全量 df（英文列）；失败 None"""
    key = _cache_key(code, kind)
    path = _file_path(code, kind)

    today = date.today()
    want = _norm_date(need_until) or today
    if want > today:
        want = today  # 未来日期的数据还不存在

    if not force_refresh:
        with _lock:
            cached = _mem_cache.get(key)
            covered = _mem_covered.get(key)
        if cached is not None and covered is not None and want <= covered:
            return cached

    with _get_lock(key):
        if not force_refresh:
            with _lock:
                cached = _mem_cache.get(key)
                covered = _mem_covered.get(key)
            if cached is not None and covered is not None and want <= covered:
                return cached

        local = None if force_refresh else _load_local(path)
        local_max = _df_max_date(local)

        if local is not None:
            # 1) 本地已覆盖所需日期 → 直接用，不联网
            if local_max is not None and local_max >= want:
                _store_mem(key, local, local_max)
                return local
            # 2) 文件在最近一次收盘后更新过 → 已是最新可得数据，无需联网
            if _is_cache_fresh(path):
                _store_mem(key, local, max(local_max or today, today))
                return local

        # 3) 缺口起点：本地最后一个交易日的下一自然日（保证首尾衔接、不留空洞）
        if local_max is not None:
            start = (datetime.combine(local_max, datetime.min.time()) + timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            start = "1990-01-01"
        end = today.strftime("%Y-%m-%d")

        new_df = _fetch(code, kind, start, end)
        if new_df is None:
            # 抓取失败：退回本地数据（不改文件），不标记覆盖
            if local is not None:
                _store_mem(key, local, local_max)
            return local

        if len(new_df) == 0:
            # 请求成功但区间内无新数据
            if local is None:
                return None
            _store_mem(key, local, max(local_max or today, today))
            return local

        if local is not None:
            merged = pd.concat([local, new_df], ignore_index=True, sort=False)
            merged = merged.drop_duplicates(subset=["date"], keep="last")
            merged = merged.sort_values("date").reset_index(drop=True)
        else:
            merged = _canonicalize(new_df, kind)

        _save_local(path, merged)
        merged_max = _df_max_date(merged)
        _store_mem(key, merged, max(merged_max or today, today))
        return merged


# ---------------------------------------------------------------- 对外接口

def _to_cn(df):
    """落盘列 → 中文列 df（日期 datetime、按日期升序），保持旧接口返回格式"""
    if df is None or len(df) == 0:
        return None
    df = df.rename(columns={k: v for k, v in _EN_TO_CN.items() if k in df.columns})
    if "日期" not in df.columns or "收盘" not in df.columns:
        return None
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"])
    df = df.dropna(subset=["收盘"])
    if len(df) == 0:
        return None
    return df.sort_values("日期").reset_index(drop=True)


def get_kline_full(code, kind=None, need_until=None, force_refresh=False):
    """返回 指数/个股/ETF 全量日K df（中文列、日期datetime、按日期升序），失败返回 None

    code：6位数字或带前缀规范代码(sh512880/sz399001/sh600519)均可。
    kind：index / stock / etf（纯数字 ETF 缺省自动判为 etf；个股须显式传 stock，
          因 000001 等指数/股票歧义代码无法从数字判定）。落盘文件名带 kind 前缀，
          天然隔离 000001（上证指数 / 平安银行）串数据。
          HSI 等新浪不支持的代码返回 None（调用方自行走 yfinance）。
    need_until：需要本地数据覆盖到的日期（'YYYY-MM-DD'/date/None=今天）。仅用于判断
          是否需要增量刷新，返回的仍是全量；已覆盖则完全不联网。
    force_refresh：忽略本地缓存，强制全量重抓覆盖。
    """
    code = strip_prefix(code) or str(code)
    kind = (kind or ("etf" if is_etf_code(code) else "index")).lower()
    if kind not in _FILE_PREFIX:
        return None
    if sina_symbol(code, kind) is None or not AKSHARE_AVAILABLE:
        return None
    df = _load_or_update(code, kind, need_until=need_until, force_refresh=force_refresh)
    return _to_cn(df)


def get_kline_since(code, start_date=None, need_until=None, force_refresh=False):
    """取 指数/个股/ETF 自 start_date(YYYY-MM-DD 或 None=全量)起的日K df（中文列）"""
    df = get_kline_full(code, need_until=need_until, force_refresh=force_refresh)
    if df is None:
        return None
    if start_date:
        return df[df["日期"] >= pd.Timestamp(start_date)].reset_index(drop=True)
    return df
