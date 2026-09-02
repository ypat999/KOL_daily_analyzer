# -*- coding: utf-8 -*-
"""指数/ETF 全量日K 本地持久缓存（新浪 stock_zh_index_daily）

背景：新浪该接口不支持按日期区间查询，每次请求都返回全量历史（数千行）。
此前回填/复盘对同一指数跨多个预测日查询时反复全量下载，是"39分钟/16分钟"
长耗时的主因之一。

策略：
- 落盘缓存到 data_cache/kline/{code}.csv，记录抓取日期（_meta.json）；
- 当日 15:30 收盘后复用本地文件（同日内多次运行/多次查询不再重抓）；
- 盘中或跨日首次使用 → 重新抓取全量覆盖文件（新浪无增量接口，只能全量刷新，
  单次约 1-3 秒）；
- 进程内再叠一层内存缓存，杜绝同进程内重复抓取。

本模块同时服务 momentum_analyzer（盘后强弱）与 backtest_analyzer（收益回填/复盘）。
"""
import json
import os
import threading
import time
from datetime import date, datetime

import pandas as pd

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except Exception:
    ak = None
    AKSHARE_AVAILABLE = False

from market_symbols import is_etf_code, sina_symbol, strip_prefix

CACHE_DIR = os.path.join("data_cache", "kline")
META_FILE = os.path.join(CACHE_DIR, "_meta.json")
REUSE_AFTER = (15, 30)  # 15:30 收盘后当日缓存可复用
MAX_RETRIES = 3
_lock = threading.Lock()
_mem_cache = {}  # code -> (df, fetched_date_str)

# 与 momentum_analyzer 保持一致的全局限速
_last_request_time = 0.0


def _wait_for_rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_request_time = time.time()


def _clean(df):
    """新浪英文列 → 中文列；日期转 datetime；按日期升序"""
    if df is None or len(df) == 0:
        return None
    col_map = {'date': '日期', 'open': '开盘', 'high': '最高', 'low': '最低',
               'close': '收盘', 'volume': '成交量', 'amount': '成交额'}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if '日期' not in df.columns or '收盘' not in df.columns:
        return None
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.dropna(subset=['收盘'])
    if len(df) == 0:
        return None
    df = df.sort_values('日期').reset_index(drop=True)
    return df


def _read_meta():
    if not os.path.exists(META_FILE):
        return {}
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_meta(meta):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)


def _now():
    return datetime.now()


def _can_reuse_local(meta_code):
    """本地缓存是否新鲜：当日 15:30 后抓取过 → 直接复用"""
    info = meta_code or {}
    if info.get("date") != date.today().isoformat():
        return False
    t = _now()
    return (t.hour, t.minute) >= REUSE_AFTER


def get_kline_full(code, force_refresh=False):
    """返回指数/ETF 全量日K df（中文列、日期datetime、按日期升序），失败返回 None

    code 参数：6位数字或带前缀规范代码(sh512880/sz399001)均可；缓存 key 统一用 6 位。
    HSI 等新浪不支持的代码返回 None（调用方自行走 yfinance）。
    """
    code = strip_prefix(code) or str(code)  # 兼容 sh512880 等规范代码入参
    sina = sina_symbol(code, "etf" if is_etf_code(code) else "index")
    if sina is None or not AKSHARE_AVAILABLE:
        return None

    today = date.today().isoformat()
    with _lock:
        if code in _mem_cache and _mem_cache[code][1] == today and not force_refresh:
            return _mem_cache[code][0]

    path = os.path.join(CACHE_DIR, f"{code}.csv")
    if not force_refresh and os.path.exists(path) and _can_reuse_local(_read_meta().get(code)):
        try:
            df = pd.read_csv(path)
            df['日期'] = pd.to_datetime(df['日期'])
            with _lock:
                _mem_cache[code] = (df, today)
            return df
        except Exception:
            pass  # 文件损坏则重新抓取

    # 盘中或跨日：重新抓全量覆盖
    df = None
    for attempt in range(MAX_RETRIES):
        try:
            _wait_for_rate_limit()
            df = _clean(ak.stock_zh_index_daily(symbol=sina))
            if df is not None:
                break
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep((attempt + 1) * 2)
            continue
    if df is None:
        print(f"  指数K线 {sina} 抓取失败({MAX_RETRIES}次重试)")
        return None

    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    meta = _read_meta()
    meta[code] = {"date": today, "time": _now().strftime("%H:%M:%S")}
    _write_meta(meta)
    with _lock:
        _mem_cache[code] = (df, today)
    return df


def get_kline_since(code, start_date=None, force_refresh=False):
    """取指数/ETF 自 start_date(YYYY-MM-DD 或 None=全量)起的日K df"""
    df = get_kline_full(code, force_refresh=force_refresh)
    if df is None:
        return None
    if start_date:
        return df[df['日期'] >= pd.Timestamp(start_date)].reset_index(drop=True)
    return df
