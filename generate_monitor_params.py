#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
盯盘参数生成器
====================================================================

从每天的综合投资建议（综合投资建议_YYYY-MM-DD.txt）中提取可执行的盘中
监控条件，生成 market_monitor.py 读取的 monitor_params_YYYY-MM-DD.json。

两种提取方式：
  1. 规则解析（默认，确定性、零成本）：解析"价格警报清单"表格 + IF-THEN 条件句
  2. LLM 提炼（--llm，更完整）：调用 DeepSeek 按 JSON schema 提炼复杂/组合条件

用法：
    python generate_monitor_params.py --advice archive_2026-08-21/综合投资建议_2026-08-21.txt
    python generate_monitor_params.py --advice ... --date 2026-08-25
    python generate_monitor_params.py --advice ... --llm
    python generate_monitor_params.py --advice ... --out monitor_params_2026-08-25.json

在 kol_analyzer.py 中已自动接入：综合投资建议保存后自动生成盯盘参数。
规则解析输出为"保底清单"（通常 10-30 条），复杂条件建议定期用 --llm 补充。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

# 常用标的表：名称 → (6位代码, kind)。规则解析时优先于此表，其次取条件文本内代码
SYMBOL_TABLE = {
    # 指数
    "上证指数": ("000001", "index"), "上证综指": ("000001", "index"),
    "深证成指": ("399001", "index"), "创业板指": ("399006", "index"),
    "沪深300": ("000300", "index"), "科创50": ("000688", "index"),
    "中证1000": ("000852", "index"), "中证500": ("000905", "index"),
    "深证综指": ("399106", "index"), "上证50": ("000016", "index"),
    "北证50": ("899050", "index"),
    # 近期建议中出现过的个股/ETF
    "中船特气": ("688146", "stock"), "江丰电子": ("300666", "stock"),
    "雅克科技": ("002409", "stock"), "通鼎互联": ("002491", "stock"),
    "键凯科技": ("688356", "stock"), "旭光电子": ("600353", "stock"),
    "中瓷电子": ("003031", "stock"), "中际旭创": ("300308", "stock"),
    "贵州茅台": ("600519", "stock"), "北大荒": ("600598", "stock"),
    "盛达资源": ("000603", "stock"), "白银有色": ("000960", "stock"),
    "三花智控": ("002050", "stock"), "宁德时代": ("300750", "stock"),
    "宇树科技": ("688712", "stock"), "沃森生物": ("300142", "stock"),
    "智飞生物": ("300122", "stock"), "百克生物": ("688276", "stock"),
    "中芯国际": ("688981", "stock"), "海光信息": ("688041", "stock"),
    "黄金ETF": ("518880", "etf"),
    # 国内金价/黄金持仓：无A股代码，仅识别名称用于人工提醒，code 留空
    "黄金": ("", "market"),
    # 08-24 建议新增
    "招商银行": ("600036", "stock"), "紫金矿业": ("601899", "stock"),
    "融捷股份": ("002192", "stock"), "赣锋锂业": ("002460", "stock"),
    "恒瑞医药": ("600276", "stock"),
}

DEFAULT_OUT_TEMPLATE = "monitor_params_{date}.json"


# ============================================================
# 通用工具
# ============================================================

def _num(text):
    """提取文本中的数字（首个），失败返回 None"""
    m = re.search(r"-?\d+(?:\.\d+)?", str(text))
    return float(m.group()) if m else None


def find_target(text):
    """在文本中找标的，返回 (code, kind, name)；找不到返回 (None, None, None)"""
    m = re.search(r"(?<!\d)([036]\d{5})(?!\d)", text)
    if m:
        return m.group(1), "stock", None
    for name, (code, kind) in SYMBOL_TABLE.items():
        if name in text:
            return code, kind, name
    # 简称别名兜底（须在全名匹配之后，避免"上证50"被"上证"抢占）
    for short, full in SHORT_ALIAS.items():
        if short in text:
            code, kind = SYMBOL_TABLE[full]
            return code, kind, full
    return None, None, None


SHORT_ALIAS = {
    "上证": "上证指数", "沪指": "上证指数", "沪市": "上证指数",
    "深成指": "深证成指", "深证": "深证成指",
    "创业板": "创业板指", "中小板指": "创业板指",
}


def classify_action(action_text):
    """根据操作动作+监控原因判断条件类型（价格类）。

    方向优先看"阻力/支撑"语义（监控原因列），再回退到动作关键词，
    避免"触及即大幅减仓"（阻力在上方）被误判为跌破、"关注缺口支撑"被误判为上穿。
    """
    a = action_text or ""
    cross = any(k in a for k in ("放量", "有效"))
    # 阻力/上方语义：价格上行触及（20日高点、整数阻力等）
    if any(k in a for k in ("阻力", "高点", "高位")):
        return "cross_above" if cross else "price_above"
    # 支撑/下方语义：价格下行触及（缺口支撑、波段低点、下沿等）
    if any(k in a for k in ("支撑", "下沿", "低点", "低位")):
        return "cross_below" if cross else "price_below"
    # 动作关键词兜底（保持原逻辑）
    if any(k in a for k in ("跌破", "破位", "下穿", "清仓", "止损", "防御", "减仓", "回避", "转弱")):
        return "cross_below" if cross else "price_below"
    if any(k in a for k in ("突破", "站上", "上穿", "止盈", "加仓", "转强", "攻上")):
        return "cross_above" if any(k in a for k in ("放量",)) else "price_above"
    if any(k in a for k in ("低吸", "建仓", "回踩", "反抽")):
        return "price_below"
    return "price_above"


def _window_from_cond_text(text):
    """从条件文本识别时间窗"""
    if re.search(r"开盘|高开|低开", text):
        return "9:25-9:45"
    if re.search(r"前30分钟", text):
        return "9:30-10:00"
    if re.search(r"半日", text):
        return "half"
    if re.search(r"尾盘", text):
        return "close"
    return "intraday"


# ============================================================
# 规则解析一：价格警报清单表格
# ============================================================

def parse_price_table(text):
    """解析 '价格警报清单' 类表格：| 标的 | 警报价 | 触发动作 | 监控原因 |"""
    lines = text.splitlines()
    alerts = []
    in_section = False
    for ln in lines:
        if re.search(r"价格警报|价格预警|盘中监控|警报清单", ln):
            in_section = True
            continue
        if in_section:
            if ln.strip().startswith("=") and alerts:
                break
            if "|" not in ln:
                # 表格结束（空行放行，其余非表格行退出，避免误解析后续无关表格）
                if alerts and ln.strip():
                    break
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) < 3 or cells[0] in ("标的", "名称") or "警报价" in cells[1]:
                continue
            target, price_cell = cells[0], cells[1]
            action = cells[2] if len(cells) > 2 else ""
            reason = cells[3] if len(cells) > 3 else ""
            price = _num(price_cell)
            if not price or not target:
                continue
            code, kind, name = find_target(target)
            if not code:
                code, kind, name = find_target(reason)
            if not code:
                continue
            ctype = classify_action(action + reason)
            cond = {"type": ctype, "value": price}
            alerts.append({
                "id": f"T{len(alerts) + 1}",
                "group": "价格警报清单",
                "name": name or target,
                "code": code,
                "kind": kind,
                "priority": "high" if any(k in (action + reason) for k in ("清仓", "止损", "防御", "破位")) else "medium",
                "window": "intraday",
                "condition": cond,
                "action": action or reason,
                "once": True,
            })
    return alerts


# ============================================================
# 规则解析二：IF-THEN 条件句
# ============================================================

def _parse_or(phrase, cond_text):
    """把含 '或/OR' 的短语解析为 any_of（无 '或' 则退回单条件）"""
    ors = [x.strip() for x in re.split(r"\s*\bOR\b\s*|\s*或\s*", phrase)]
    ors = [x for x in ors if x]
    if len(ors) <= 1:
        return _parse_single_cond(phrase, cond_text)
    conds = [c for c in (_parse_single_cond(x, cond_text) for x in ors) if c]
    if not conds:
        return None
    if len(conds) == 1:
        return conds[0]
    return {"type": "any_of", "conditions": conds}


def parse_cond_phrase(phrase, cond_text):
    """把一条条件短语解析为 condition dict（可含组合）"""
    subs = []
    # 切成 AND 子条件（AND 优先级高于 或）
    parts = [p.strip() for p in re.split(r"\s*\bAND\b\s*|\s*且\s*|\s*并且\s*", phrase)]
    for p in parts:
        if not p:
            continue
        cond = _parse_or(p, cond_text)
        if cond:
            subs.append(cond)
    if not subs:
        return None
    if len(subs) == 1:
        return subs[0]
    return {"type": "all_of", "conditions": subs}


def _parse_single_cond(p, cond_text):
    """解析单条条件子句"""
    # 否定/禁止语义：无法量化为正向触发，跳过（避免"不跌破846"被误判为"跌破846"）
    if re.search(r"不(跌破|破|回落|收复|回补|收回)", p):
        return None

    # 涨幅/跌幅（"收盘涨幅<0.5%"、"跌幅>3%"）
    m = re.search(r"(涨幅|跌幅)\s*([<>≥≤])\s*([\d.]+)\s*%?", p)
    if m:
        pct = float(m.group(3))
        if m.group(1) == "涨幅":
            return {"type": "pct_change_lt" if m.group(2) in "<≤" else "pct_change_ge", "pct": pct}
        return {"type": "pct_change_le" if m.group(2) in ">≥" else "pct_change_ge", "pct": pct}

    # 成交额相对昨日/前一日同期比例（"成交额≥前一日同期的110%"）
    m = re.search(r"成交额\s*([<≤>≥])\s*[前昨]一日?\s*同期?\s*的?\s*([\d.]+)\s*%", p)
    if m:
        ratio = float(m.group(2)) / 100
        return {"type": "amount_ratio_le" if m.group(1) in "<≤" else "amount_ratio_ge",
                "ratio": ratio, "kind": "market"}

    # 成交额环比放大/萎缩 X%（"成交额环比放大>30%" → 相对昨日 130%）
    m = re.search(r"成交额[^\d]*?(放大|增加|增长|上升|扩大)\s*[>≥]?\s*([\d.]+)\s*%", p)
    if m:
        ratio = 1 + float(m.group(2)) / 100
        return {"type": "amount_ratio_ge", "ratio": ratio, "kind": "market"}
    m = re.search(r"成交额[^\d]*?(萎缩|减少|下降|缩小|回落)\s*[<≤]?\s*([\d.]+)\s*%", p)
    if m:
        ratio = 1 - float(m.group(2)) / 100
        return {"type": "amount_ratio_le", "ratio": ratio, "kind": "market"}

    # 连续N日成交额
    m = re.search(r"连续\s*(\d+)\s*日.*?成交额.*?[<≤]\s*([\d.]+)\s*万亿?", p)
    if m:
        days = int(m.group(1))
        amount = _num(m.group(2)) * 10000 if "万亿" in m.group(0) else _num(m.group(2))
        return {"type": "consecutive_amount_le", "amount": amount, "days": days, "kind": "market"}

    # 半日成交额
    if "半日" in p and ("成交额" in p or "成交" in p):
        m = re.search(r"[<≤]\s*([\d.]+)\s*(万亿|亿)?", p)
        if m:
            v = float(m.group(1))
            if m.group(2) == "万亿":
                v *= 10000
            return {"type": "half_day_amount_le", "amount": v, "kind": "market"}

    # 成交额（两市/市场）
    if "成交额" in p or "量能" in p:
        m = re.search(r"([<≤>≥])\s*([\d.]+)\s*(万亿|亿)?", p)
        if not m:
            # 无比较符但有方向词：成交额突破/超过/达到/低于/不足 X万亿
            m = re.search(r"(突破|超过|高于|大于|达到|站上|收复)\s*([\d.]+)\s*(万亿|亿)", p)
            if m:
                v = float(m.group(2))
                if m.group(3) == "万亿":
                    v *= 10000
                return {"type": "amount_ge", "amount": v, "kind": "market"}
            m = re.search(r"(低于|不足|小于|萎缩至|回落至|跌破)\s*([\d.]+)\s*(万亿|亿)", p)
            if m:
                v = float(m.group(2))
                if m.group(3) == "万亿":
                    v *= 10000
                return {"type": "amount_le", "amount": v, "kind": "market"}
        if m:
            v = float(m.group(2))
            if m.group(3) == "万亿":
                v *= 10000
            return {"type": "amount_le" if m.group(1) in "<≤" else "amount_ge",
                    "amount": v, "kind": "market"}

    # 量比
    m = re.search(r"量比\s*([<≤>≥])\s*([\d.]+)", p)
    if m:
        return {"type": "volume_ratio_le" if m.group(1) in "<≤" else "volume_ratio_ge", "ratio": float(m.group(2))}

    # 高开/低开（容忍中间出现"幅度"等词）
    m = re.search(r"(高开|低开)[^\d]*?([\d.]+)\s*%?", p)
    if m:
        pct = float(m.group(2))
        return {"type": "open_pct_ge" if m.group(1) == "高开" else "open_pct_le", "pct": pct}

    # 回踩区间 A-B
    m = re.search(r"回踩\s*([\d.]+)\s*[-—~至]\s*([\d.]+)", p)
    if m:
        return {"type": "price_in_range", "min": float(m.group(1)), "max": float(m.group(2))}

    # 均线（"跌破5日均线"等，须在单点价格规则之前，避免"5"被当作价格）
    m = re.search(r"(跌破|站上|上穿|下穿|突破)\s*(\d+)\s*日\s*(均线|线|MA)?", p)
    if m:
        ma = int(m.group(2))
        kw = m.group(1)
        return {"type": "price_below_ma" if kw in ("跌破", "下穿") else "price_above_ma", "ma": ma}

    # 回踩/跌破/站上 单点
    code, kind, _ = find_target(p)
    m = re.search(r"(跌破|破位|下穿|站上|攻上|上穿|触及|冲上|反弹至|回踩|突破)\s*([\d.]+)", p)
    if m:
        v = float(m.group(2))
        kw = m.group(1)
        if kw in ("跌破", "破位", "下穿", "回踩"):
            t = "cross_below" if kw in ("跌破", "下穿") and ("放量" in p or "有效" in p) else "price_below"
            if kw == "回踩":
                t = "price_in_range"  # 无上界时降级为下沿观察
                return {"type": "price_below", "value": v}
            return {"type": t, "value": v}
        if kw in ("站上", "攻上", "上穿", "突破", "触及", "冲上", "反弹至"):
            t = "cross_above" if ("放量" in p or "有效" in p) else "price_above"
            return {"type": t, "value": v}

    # 技术指标
    m = re.search(r"RSI.*?[>≥]\s*([\d.]+)", p, re.I)
    if m:
        return {"type": "rsi_ge", "value": float(m.group(1))}
    m = re.search(r"KDJ.*?J\s*[>≥]\s*([\d.]+)", p, re.I)
    if m:
        return {"type": "kdj_j_ge", "value": float(m.group(1))}
    if "20日新高" in p or "创新高" in p:
        return {"type": "new_20d_high"}
    if "20日新低" in p or "创新低" in p:
        return {"type": "new_20d_low"}

    return None


def parse_if_then(text):
    """解析 'IF ... THEN ...' 条件句（支持 IF/AND/THEN 跨行）"""
    alerts = []
    for m in re.finditer(r"(?:IF|如果)\s+(.+?)\s+THEN\s+([^\n]+)", text, re.S):
        cond_text, then_text = m.group(1).strip(), m.group(2).strip()
        then_text = re.split(r"[。；;]", then_text)[0].strip()
        # 外生事件/无法量化前置：跳过（英伟达财报结果需人工判断）
        if re.search(r"英伟达|财报|盘后", cond_text):
            continue
        code, kind, name = find_target(cond_text)
        # 板块/概念级条件：无具体标的代码，规则解析无法盯，跳过
        if not code and re.search(r"板块|概念|行业", cond_text):
            continue
        if not code:
            code, kind, name = find_target(then_text)
        cond = parse_cond_phrase(cond_text, cond_text)
        if not cond:
            continue
        alerts.append({
            "id": f"I{len(alerts) + 1}",
            "group": "IF-THEN条件单",
            "name": name or (code or "市场"),
            "code": code or "",
            "kind": kind or ("market" if not code else "stock"),
            "priority": "high" if any(k in then_text for k in ("清仓", "止损", "防御", "作废", "回避")) else "medium",
            "window": _window_from_cond_text(cond_text),
            "condition": cond,
            "action": then_text,
            "once": True,
        })
    return alerts


# ============================================================
# LLM 提炼（可选）
# ============================================================

LLM_SCHEMA_PROMPT = """你是量化盯盘参数提取器。从投资建议中提取所有可机器执行的盘中监控条件，输出严格JSON。

输出格式（只输出JSON）：
{"alerts":[{"id":"B1-1","group":"分组名","name":"标的名称","code":"6位代码","kind":"stock|index|etf|market","priority":"high|medium|low","window":"intraday|open|9:30-10:00|half|afternoon|close|after_close|all","condition":{...},"action":"用户看到后执行的操作","once":true}]}

condition.type 只允许以下值及参数：
- price_in_range{min,max,price?}: 现价落在区间（price可选now/open）
- price_above{value} / price_below{value}
- cross_above{value} / cross_below{value}: 上穿/下穿
- pct_change_ge{pct} / pct_change_le{pct}: 现价相对昨收涨幅/跌幅≥pct%
- open_pct_ge{pct} / open_pct_le{pct}: 开盘涨幅/跌幅≥pct%
- amount_ge{amount} / amount_le{amount}: 成交额(亿)，kind=market 时指两市
- amount_ratio_ge{ratio} / amount_ratio_le{ratio}: 成交额/昨日成交额
- volume_ratio_ge{ratio} / volume_ratio_le{ratio}: 量比
- half_day_amount_le{amount}: 半日(11:30)两市成交额≤amount亿
- consecutive_amount_le{amount,days}: 连续N日两市成交额≤amount亿
- rsi_ge{value} / rsi_le{value} / kdj_j_ge{value} / kdj_j_le{value}
- price_above_ma{ma} / price_below_ma{ma} / ma_cross_above{ma} / ma_cross_below{ma}
- new_20d_high / new_20d_low / macd_golden_cross / macd_dead_cross
- at_open / at_time{time:"HH:MM"} / at_close
- all_of{conditions:[...]} / any_of{conditions:[...]} / count_of{conditions:[...],min_count:N}

规则：
1. 只提取可量化的盘中条件；纯观点/无法量化的跳过
2. code 必须真实存在于文本或常用指数代码（上证000001/创业板399006/沪深300/中证1000/科创50等）
3. 组合条件（含AND/且）用 all_of；"任一"用 any_of；"至少N个"用 count_of
4. 止损/止盈/清仓等价格位也作为独立 alert 提取（type=price_below/price_above）
5. window：开盘判断用open，前30分钟用"9:30-10:00"，半日用half，盘后用after_close，其余intraday
6. 不确定的标的宁可跳过，不要编造代码
7. action 用简洁中文写明"用户该执行什么" """


def extract_with_llm(advice_text):
    """调用 DeepSeek 提炼条件"""
    try:
        from deepseek_summary import deepseek_summary
        result = deepseek_summary(
            advice_text,
            sysprompt=LLM_SCHEMA_PROMPT,
            userprompt="以下是今日综合投资建议，请提取盯盘条件JSON：\n\n",
            thinking={"type": "disabled"},
            response_format={"type": "json_object"},
            temperature=0.05,
            max_tokens=8192,
        )
        m = re.search(r"\{[\s\S]*\}", result)
        if not m:
            print(f"[LLM] 无法解析JSON响应: {str(result)[:200]}")
            return []
        data = json.loads(m.group())
        return data.get("alerts", [])
    except Exception as e:
        print(f"[LLM] 提取失败: {e}")
        return []


# ============================================================
# 主流程
# ============================================================

def _fmt(x, dec=2):
    """数字格式化：去掉无意义尾零；非数字原样返回"""
    try:
        s = f"{float(x):.{dec}f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    except (TypeError, ValueError):
        return str(x)


def _amount_text(v):
    """成交额(亿) → 可读文本（>=1万亿显示万亿）"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v >= 10000:
        return f"{_fmt(v / 10000)}万亿"
    return f"{_fmt(v, 0)}亿"


def condition_to_text(cond):
    """把 condition dict 转成人类可读中文描述（覆盖全部盯盘条件类型）"""
    if not isinstance(cond, dict):
        return str(cond)
    t = cond.get("type", "")
    v = cond.get("value")

    # 组合类型（递归）
    if t == "all_of":
        return " 且 ".join(condition_to_text(c) for c in cond.get("conditions", []))
    if t == "any_of":
        return "（任一）" + " 或 ".join(condition_to_text(c) for c in cond.get("conditions", []))
    if t == "count_of":
        subs = condition_to_text(cond.get("conditions", [{}])[0]) if cond.get("conditions") else "?"
        n = cond.get("min_count", 1)
        return f"（至少{n}个满足：{subs}）"

    price_tag = {"open": "开盘价", "now": "现价", "high": "最高价", "low": "最低价"}.get(
        cond.get("price", "now"), cond.get("price", "现价"))
    ma = cond.get("ma")
    m = {
        "price_in_range": f"{price_tag}在 {_fmt(cond.get('min'))} - {_fmt(cond.get('max'))}",
        "price_above": f"{price_tag}>{_fmt(v)}",
        "price_below": f"{price_tag}<{_fmt(v)}",
        "cross_above": f"{price_tag}上穿 {_fmt(v)}",
        "cross_below": f"{price_tag}下穿 {_fmt(v)}",
        "pct_change_ge": f"现价涨幅≥{_fmt(cond.get('pct'))}%",
        "pct_change_le": f"现价跌幅≥{_fmt(cond.get('pct'))}%",
        "pct_change_lt": f"现价涨幅<{_fmt(cond.get('pct'))}%",
        "open_pct_ge": f"开盘高开≥{_fmt(cond.get('pct'))}%",
        "open_pct_le": f"开盘低开≥{_fmt(cond.get('pct'))}%",
        "amount_ge": f"成交额≥{_amount_text(cond.get('amount'))}",
        "amount_le": f"成交额≤{_amount_text(cond.get('amount'))}",
        "amount_ratio_ge": f"成交额≥昨日{_fmt(cond.get('ratio'))}倍",
        "amount_ratio_le": f"成交额≤昨日{_fmt(cond.get('ratio'))}倍",
        "volume_ratio_ge": f"量比≥{_fmt(cond.get('ratio'))}",
        "volume_ratio_le": f"量比≤{_fmt(cond.get('ratio'))}",
        "half_day_amount_le": f"半日两市成交额≤{_amount_text(cond.get('amount'))}",
        "consecutive_amount_le": f"连续{cond.get('days', 'N')}日两市成交额≤{_amount_text(cond.get('amount'))}",
        "rsi_ge": f"RSI≥{_fmt(v)}",
        "rsi_le": f"RSI≤{_fmt(v)}",
        "kdj_j_ge": f"KDJ J值≥{_fmt(v)}",
        "kdj_j_le": f"KDJ J值≤{_fmt(v)}",
        "price_above_ma": f"价格站上MA{ma}",
        "price_below_ma": f"价格跌破MA{ma}",
        "ma_cross_above": f"MA{ma}上穿（金叉）",
        "ma_cross_below": f"MA{ma}下穿（死叉）",
        "new_20d_high": "创20日新高",
        "new_20d_low": "创20日新低",
        "macd_golden_cross": "MACD金叉",
        "macd_dead_cross": "MACD死叉",
        "at_open": "开盘时提醒",
        "at_time": f"定时 {cond.get('time')}",
        "at_close": "收盘时提醒",
    }
    return m.get(t, t)


def format_params_markdown(payload):
    """把盯盘参数 payload 转成可读 Markdown（用于微信推送附文）"""
    meta = payload.get("meta", {})
    alerts = payload.get("alerts", [])
    lines = []
    lines.append("")
    lines.append("---")
    lines.append(f"## 今日盯盘参数（{meta.get('date', '?')}）")
    if meta.get("strategy"):
        lines.append(f"策略：{meta['strategy']}")
    lines.append(f"共 {len(alerts)} 条条件，满足时盯盘程序将弹窗提醒：")
    by_group = {}
    for a in alerts:
        by_group.setdefault(a.get("group", "未分组"), []).append(a)
    for g, items in by_group.items():
        lines.append(f"\n### {g}（{len(items)}条）")
        for a in items:
            prio = {"high": "高", "medium": "中", "low": "低"}.get(a.get("priority"), str(a.get("priority", "")))
            target = a.get("name", "")
            if a.get("code"):
                target += f"({a['code']})"
            cond_text = condition_to_text(a.get("condition", {}))
            lines.append(f"- [{prio}] {target}：{cond_text}")
            if a.get("action"):
                lines.append(f"  → {a['action']}")
    return "\n".join(lines)


def _dedup(alerts):
    """按 (code, condition 指纹) 去重，保留先出现的"""
    seen = set()
    out = []
    for a in alerts:
        cond = a.get("condition", {})
        key = (a.get("code", ""), json.dumps(cond, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def generate_monitor_params(advice_text, date, archive_folder=None, use_llm=False,
                            out_path=None, meta_extra=None):
    """从综合投资建议生成盯盘参数 JSON

    Args:
        advice_text: 综合投资建议全文
        date: 兜底目标交易日（YYYY-MM-DD）。盯盘参数服务于"下一交易日"，
              因此优先取建议文本中"作战计划（YYYY-MM-DD）"的日期，取不到才用此值。
        archive_folder: 归档目录（生成文件写到这里 + 项目根目录）
        use_llm: 是否调用 DeepSeek 补充复杂条件
        out_path: 自定义输出路径（优先于 archive_folder）
        meta_extra: 附加 meta 字段

    Returns:
        str: 生成的参数文件路径；失败返回 None
    """
    # 目标交易日：文本"作战计划（YYYY-MM-DD）"优先（参数是给下一交易日用的，不能定在总结当天）
    m = re.search(r"作战计划\s*[（(]\s*(\d{4}-\d{2}-\d{2})", advice_text)
    if m:
        date = m.group(1)
    alerts = []
    alerts += parse_price_table(advice_text)
    alerts += parse_if_then(advice_text)
    if use_llm:
        llm_alerts = extract_with_llm(advice_text)
        if llm_alerts:
            print(f"[LLM] 提炼到 {len(llm_alerts)} 条条件")
            alerts = alerts + llm_alerts
    alerts = _dedup(alerts)

    meta = {
        "date": date,
        "generated_from": f"综合投资建议_{date}.txt",
        "mode": "trading",
        "strategy": "",
        "yesterday_amount_yi": None,
        "position_limit_pct": None,
    }
    if meta_extra:
        meta.update(meta_extra)

    # 从建议文本提取策略概要/日期（容错）
    m = re.search(r"(条件\s*GO|NO-GO|No-Go|GO|看多|看空|中性|防守|进攻)[^。\n]*", advice_text)
    if m and not meta.get("strategy"):
        meta["strategy"] = m.group(0).strip().rstrip("*").strip()[:60]

    payload = {"_说明": "由 generate_monitor_params.py 生成", "meta": meta, "alerts": alerts}

    paths = []
    if out_path:
        paths.append(out_path)
    else:
        if archive_folder:
            paths.append(os.path.join(archive_folder, f"monitor_params_{date}.json"))
        paths.append(DEFAULT_OUT_TEMPLATE.format(date=date))

    saved = None
    for p in paths:
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"[参数] 已生成 {p}（{len(alerts)} 条条件）")
            saved = p
        except Exception as e:
            print(f"[参数] 写入失败 {p}: {e}")
    return saved


def main():
    ap = argparse.ArgumentParser(description="从综合投资建议生成盯盘参数 JSON")
    ap.add_argument("--advice", required=True, help="综合投资建议 txt 路径")
    ap.add_argument("--date", default="", help="目标交易日 YYYY-MM-DD（缺省取建议中'明日'或今天）")
    ap.add_argument("--out", default="", help="输出路径")
    ap.add_argument("--llm", action="store_true", help="调用 DeepSeek 补充复杂条件")
    args = ap.parse_args()

    if not os.path.exists(args.advice):
        print(f"[错误] 找不到建议文件: {args.advice}")
        return 1
    with open(args.advice, "r", encoding="utf-8") as f:
        advice_text = f.read()

    # 目标日期：--date > 建议文本中"作战计划（YYYY-MM-DD）" > 文件名日期
    date = args.date
    if not date:
        m = re.search(r"作战计划\s*[（(]\s*(\d{4}-\d{2}-\d{2})", advice_text)
        if m:
            date = m.group(1)
        else:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(args.advice))
            date = m.group(1) if m else datetime.now().strftime("%Y-%m-%d")
    print(f"[参数] 目标交易日: {date}")

    archive_folder = os.path.dirname(os.path.abspath(args.advice))
    generate_monitor_params(advice_text, date, archive_folder=archive_folder,
                            use_llm=args.llm, out_path=args.out or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
