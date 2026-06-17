"""
建议历史追踪模块

维护 advice_history.json，记录每日综合投资建议的核心观点，
让 LLM 看到过去 N 天自己的观点演变，避免"今天看多明天看空"的反复。

数据结构：
    advice_history.json
    {
        "history": [
            {
                "date": "2025-06-17",
                "direction": "bullish",
                "position_advice": "70%",
                "core_targets": ["创业板指", "半导体"],
                "key_view": "技术面回踩企稳，建议做多创业板",
                "go_no_go": "Go",
                "summary": "..."
            }
        ]
    }
"""

import json
import os
import re
from datetime import datetime, timedelta


HISTORY_FILE = "advice_history.json"
MAX_HISTORY_DAYS = 30  # 保留最近30天


def load_history():
    """加载建议历史
    
    Returns:
        dict: 历史数据
    """
    if not os.path.exists(HISTORY_FILE):
        return {"history": []}
    
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"加载建议历史失败: {e}")
        return {"history": []}


def save_history(history):
    """保存建议历史
    
    Args:
        history: 历史数据
    """
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存建议历史失败: {e}")


def extract_advice_summary(advice_text):
    """从综合投资建议文本中提取核心观点
    
    Args:
        advice_text: 综合投资建议文本
    
    Returns:
        dict: 提取的核心观点
    """
    summary = {
        "direction": "neutral",
        "position_advice": "",
        "core_targets": [],
        "key_view": "",
        "go_no_go": "",
        "summary": "",
    }
    
    # 提取方向
    if re.search(r"做多|看多|Go\b|加仓|买入", advice_text):
        summary["direction"] = "bullish"
    elif re.search(r"做空|看空|No-Go|减仓|卖出|空仓", advice_text):
        summary["direction"] = "bearish"
    
    # 提取Go/No-Go
    go_match = re.search(r"(Go|No-Go)", advice_text, re.IGNORECASE)
    if go_match:
        summary["go_no_go"] = go_match.group(1)
    
    # 提取仓位建议
    position_match = re.search(r"总仓位[^0-9]*(\d+)\s*%", advice_text)
    if position_match:
        summary["position_advice"] = position_match.group(1) + "%"
    else:
        position_match = re.search(r"仓位[^0-9]*(\d+)\s*%", advice_text)
        if position_match:
            summary["position_advice"] = position_match.group(1) + "%"
    
    # 提取核心标的（做多/做空 XXX）
    target_patterns = [
        r"做多\s*([^\s，。、,]+)",
        r"做空\s*([^\s，。、,]+)",
        r"关注\s*([^\s，。、,]+)",
    ]
    targets = set()
    for pattern in target_patterns:
        matches = re.findall(pattern, advice_text)
        for m in matches:
            # 清理
            target = re.sub(r"[（(].*?[)）]", "", m).strip()
            if target and len(target) <= 10:
                targets.add(target)
    summary["core_targets"] = list(targets)[:5]
    
    # 提取关键观点（第一段非标题文字）
    lines = advice_text.split("\n")
    key_view_lines = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("━") or line.startswith("★") or line.startswith("第"):
            continue
        if len(line) > 10:
            key_view_lines.append(line)
        if len(key_view_lines) >= 2:
            break
    summary["key_view"] = " | ".join(key_view_lines)[:200]
    
    # 摘要（前500字）
    summary["summary"] = advice_text[:500]
    
    return summary


def record_advice(date_str, advice_text):
    """记录当日建议到历史
    
    Args:
        date_str: 日期 'YYYY-MM-DD'
        advice_text: 综合投资建议文本
    
    Returns:
        bool: 是否记录成功
    """
    history = load_history()
    
    # 提取核心观点
    summary = extract_advice_summary(advice_text)
    summary["date"] = date_str
    summary["record_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 移除同日旧记录
    history["history"] = [h for h in history["history"] if h.get("date") != date_str]
    
    # 添加新记录
    history["history"].append(summary)
    
    # 按日期排序
    history["history"].sort(key=lambda x: x.get("date", ""))
    
    # 保留最近 MAX_HISTORY_DAYS 天
    if len(history["history"]) > MAX_HISTORY_DAYS:
        history["history"] = history["history"][-MAX_HISTORY_DAYS:]
    
    save_history(history)
    print(f"已记录 {date_str} 建议到历史（共{len(history['history'])}条）")
    return True


def get_recent_history(days=7):
    """获取最近N天的建议历史
    
    Args:
        days: 天数
    
    Returns:
        list: 建议历史列表（按日期升序）
    """
    history = load_history()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    recent = [h for h in history["history"] if h.get("date", "") >= cutoff]
    return recent


def format_history_for_prompt(days=7):
    """格式化建议历史，供注入LLM prompt
    
    让LLM看到过去N天的观点演变，避免反复。
    
    Args:
        days: 回溯天数
    
    Returns:
        str: 格式化的历史摘要文本
    """
    recent = get_recent_history(days)
    if not recent:
        return ""
    
    lines = []
    lines.append(f"【过去{days}天建议历史（避免观点反复）】")
    lines.append("-" * 50)
    
    dir_label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
    
    for h in recent:
        date = h.get("date", "")
        direction = dir_label.get(h.get("direction", "neutral"), "中性")
        position = h.get("position_advice", "未明确")
        go = h.get("go_no_go", "")
        targets = h.get("core_targets", [])
        key_view = h.get("key_view", "")
        
        targets_str = "/".join(targets[:3]) if targets else "无"
        go_str = f" | {go}" if go else ""
        
        lines.append(
            f"  {date}: {direction} | 仓位{position}{go_str} | 标的: {targets_str}"
        )
        if key_view:
            lines.append(f"    观点: {key_view[:100]}")
    
    # 检测观点反复
    if len(recent) >= 3:
        directions = [h.get("direction") for h in recent[-3:]]
        if len(set(directions)) >= 3:
            lines.append("-" * 50)
            lines.append("  ⚠️ 警告：过去3天方向频繁变化，今日判断需特别谨慎，避免追涨杀跌。")
        elif directions[-1] != directions[-2] and directions[-2] != directions[-3]:
            lines.append("-" * 50)
            lines.append("  ⚠️ 警告：方向连续两日反转，请确认是否有充分理由，避免情绪化决策。")
    
    lines.append("-" * 50)
    lines.append("提示：请保持观点连续性，如需转向必须有充分理由并明确说明。")
    
    return "\n".join(lines)


if __name__ == "__main__":
    # 打印最近7天历史
    history_text = format_history_for_prompt(7)
    if history_text:
        print(history_text)
    else:
        print("暂无建议历史")
