# -*- coding: utf-8 -*-
"""A股标的代码 → 市场/数据源 symbol 的统一定义

沪深归属判断此前散落在 momentum_analyzer / backtest_analyzer / market_breadth 等
多处各自实现，曾因写错（如 512880 沪市ETF被拼成 sz）导致查询反复失败。

统一后的规则（约定）：
- 【规范代码】= 带交易所前缀的 6 位数字，如 sh512880 / sz000001 / sh600519。
  系统内持久化与跨模块传递一律用规范代码，不再裸存 6 位、也不再现场猜前缀。
- 指数(index)：399xxx 深证系 → sz；其余(000001上证/000300沪深300/000688科创50等) → sh
- ETF(etf)：5xxxxx 沪市 → sh；15/16/18 深市 → sz
- 股票(stock)：6/9 开头沪市 → sh；0/3 开头深市 → sz；4/8 开头北交所 → bj

注意：指数与股票代码可重复（000001 既是上证指数又是平安银行），
仅凭 6 位数字无法区分，asset_type 需要由调用方给出或来自数据自带类型。
"""
import re

_PREFIX_RE = re.compile(r"^(sh|sz|bj)(\d{6})$", re.I)
_SUFFIX_RE = re.compile(r"^(\d{6})\.(SS|SZ|BJ)$", re.I)
_SUFFIX_MAP = {"ss": "sh", "sz": "sz", "bj": "bj"}


def strip_prefix(code):
    """规范代码/任意输入 → 6位数字；非6位(如 HSI) 原样返回 None-safe 处理"""
    s = str(code or "").strip()
    m = _PREFIX_RE.match(s)
    if m:
        return m.group(2)  # sh512880 → 512880
    m = _SUFFIX_RE.match(s)
    if m:
        return m.group(1)  # 512880.SS → 512880
    return s if s.isdigit() else (None if not s else s.upper())


def infer_kind(code6):
    """纯6位数字、无显式类型时的默认判断（指数/个股歧义代码如000001按行情个体处理）"""
    c = strip_prefix(code6) or ""
    if not (c.isdigit() and len(c) == 6):
        return None
    if is_bj_code(c):
        return "bj"
    if is_etf_code(c):
        return "etf"
    return "stock"


def normalize(code, kind=None):
    """任意表示 → 规范代码（小写前缀+6位，如 sh512880 / sz399001 / sh600519）

    兼容输入：6位纯数字、sh512880/sz399001、512880.SS/399001.SZ、
    带空格的富文本等。HSI 等非6位返回大写字样原样。
    kind: index/etf/stock/bj；纯数字缺省时按 infer_kind 推断。
    """
    s = str(code or "").strip()
    if not s:
        return None
    m = _PREFIX_RE.match(s)
    if m:
        return f"{m.group(1).lower()}{m.group(2)}"
    m = _SUFFIX_RE.match(s)
    if m:
        p = _SUFFIX_MAP[m.group(2).lower()]
        return f"{p}{m.group(1)}"
    c = s.lstrip("^")
    if not c.isdigit():
        return c.upper()  # HSI 等非数字代码
    if len(c) != 6:
        return None
    k = kind or infer_kind(c)
    p = market_prefix(c, k) if k else None
    if not p:
        return None
    return f"{p}{c}"


def to_yf(code):
    """规范代码 → yfinance 代码（sh512880→512880.SS、sz399001→399001.SZ）"""
    s = str(code or "").strip()
    m = _PREFIX_RE.match(s)
    if m:
        p, c6 = m.group(1).lower(), m.group(2)
        suf = {"sh": "SS", "sz": "SZ", "bj": "BJ"}[p]
        return f"{c6}.{suf}"
    return s.upper()  # HSI 等


def market_prefix(code, asset_type):
    """返回交易所前缀 sh/sz/bj；无法判定返回 None。asset_type: stock/etf/index"""
    code = str(code)
    if asset_type == "index":
        return "sz" if code.startswith("399") else "sh"
    if asset_type == "etf":
        if code.startswith("5"):
            return "sh"
        if code.startswith(("15", "16", "18")):
            return "sz"
        return None
    # stock
    if code.startswith(("6", "9")):
        return "sh"
    if code.startswith(("0", "3")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return None


def sina_symbol(code, asset_type):
    """新浪接口 symbol（sh/sz + 6位，如 sh512880 / sz399001）；非6位数字(如HSI)返回None"""
    code = str(code)
    if not (code.isdigit() and len(code) == 6):
        return None  # 新浪仅覆盖沪深6位数字代码，HSI 等走 yfinance
    p = market_prefix(code, asset_type)
    if not p or p == "bj":
        return None  # 新浪不覆盖北交所
    return f"{p}{code}"


def yf_symbol(code, asset_type):
    """yfinance 代码（如 512880.SS / 399001.SZ）"""
    p = market_prefix(code, asset_type)
    if not p:
        return None
    if p == "bj":
        return f"{code}.BJ"
    return f"{code}.SS" if p == "sh" else f"{code}.SZ"


def is_etf_code(code):
    """6位数字是否为 ETF（5 沪 / 15/16/18 深）"""
    code = str(code)
    return code.startswith("5") or code.startswith(("15", "16", "18"))


def is_bj_code(code):
    """6位数字是否为北交所（43/83/87/92 等 4/8 开头）"""
    code = str(code)
    return code.startswith(("4", "8")) and code[:2] in ("43", "83", "87", "92")
