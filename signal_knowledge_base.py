"""
信号-胜率知识库模块

长期统计"动量信号 + KOL共识 + 市场环境 → 实际涨跌"的组合胜率，
为未来决策提供历史经验参考。

数据结构：
    signal_kb.jsonl  - 每行一个信号事件记录
    {
        "date": "2025-06-18",
        "target": "创业板指",
        "target_type": "index",
        "code": "399006",
        "signals": {
            "momentum": {"trend": "上涨", "breakout": "20日新高突破", "rsi": 65, "macd": "金叉"},
            "kol_consensus": {"bullish": 3, "bearish": 1, "neutral": 0, "consensus_direction": "bullish"},
            "market_env": {"temperature": 65, "limit_up": 50, "north_flow": 30}
        },
        "advice_direction": "bullish",
        "actual_return_1d": 1.2,
        "actual_return_5d": 3.5,
        "is_correct_1d": true,
        "is_correct_5d": true
    }

使用方式：
    1. record_signal_event() - 每日分析后记录信号事件
    2. update_signal_outcomes() - 回填历史信号的实际收益
    3. get_signal_winrate_stats() - 查询特定信号组合的历史胜率
    4. format_kb_summary_for_prompt() - 格式化知识库摘要注入LLM
"""

import json
import os
from datetime import datetime, timedelta


KB_FILE = "signal_kb.jsonl"


def record_signal_event(date_str, target, target_type, code, signals, advice_direction):
    """记录一个信号事件
    
    Args:
        date_str: 日期 'YYYY-MM-DD'
        target: 标的名称
        target_type: index/sector/stock
        code: 标的代码
        signals: 信号字典，包含 momentum/kol_consensus/market_env
        advice_direction: 最终建议方向 bullish/bearish/neutral
    
    Returns:
        bool: 是否记录成功
    """
    event = {
        "date": date_str,
        "target": target,
        "target_type": target_type,
        "code": code,
        "signals": signals,
        "advice_direction": advice_direction,
        "actual_return_1d": None,
        "actual_return_5d": None,
        "is_correct_1d": None,
        "is_correct_5d": None,
        "record_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    
    try:
        with open(KB_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print(f"记录信号事件失败: {e}")
        return False


def load_all_events():
    """加载所有信号事件
    
    Returns:
        list: 信号事件列表
    """
    if not os.path.exists(KB_FILE):
        return []
    
    events = []
    try:
        with open(KB_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"加载信号知识库失败: {e}")
    
    return events


def update_signal_outcomes():
    """回填历史信号的实际收益
    
    遍历所有未回填的信号事件，获取实际收益并更新。
    
    Returns:
        int: 更新的事件数量
    """
    events = load_all_events()
    if not events:
        return 0
    
    # 延迟导入避免循环依赖
    from backtest_analyzer import get_actual_performance
    
    updated = 0
    today = datetime.now().strftime("%Y-%m-%d")
    
    for event in events:
        # 跳过已回填的
        if event.get("is_correct_5d") is not None:
            continue
        
        pred_date = event.get("date", "")
        target = event.get("target", "")
        target_type = event.get("target_type", "")
        code = event.get("code", "")
        
        if not target or target_type not in ("index", "stock"):
            continue
        
        # 计算1日收益
        days_since = (datetime.now() - datetime.strptime(pred_date, "%Y-%m-%d")).days
        if days_since >= 1:
            actual_1d = get_actual_performance(target, target_type, pred_date, horizon=1)
            if actual_1d:
                event["actual_return_1d"] = actual_1d["return_pct"]
                event["is_correct_1d"] = _check_correct(event["advice_direction"], actual_1d["return_pct"])
        
        # 计算5日收益
        if days_since >= 5:
            actual_5d = get_actual_performance(target, target_type, pred_date, horizon=5)
            if actual_5d:
                event["actual_return_5d"] = actual_5d["return_pct"]
                event["is_correct_5d"] = _check_correct(event["advice_direction"], actual_5d["return_pct"])
        
        if event["is_correct_1d"] is not None or event["is_correct_5d"] is not None:
            updated += 1
    
    # 重写文件
    if updated > 0:
        try:
            with open(KB_FILE, "w", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
            print(f"已更新 {updated} 条信号事件的实际收益")
        except Exception as e:
            print(f"保存信号知识库失败: {e}")
    
    return updated


def _check_correct(advice_direction, actual_return):
    """判断预测是否正确"""
    if advice_direction == "bullish" and actual_return > 0:
        return True
    elif advice_direction == "bearish" and actual_return < 0:
        return True
    elif advice_direction == "neutral" and abs(actual_return) < 1:
        return True
    return False


def get_signal_winrate_stats(signal_type=None, signal_value=None, lookback_days=90):
    """查询特定信号组合的历史胜率
    
    Args:
        signal_type: 信号类型，如 'momentum.breakout' / 'kol_consensus.consensus_direction' / 'market_env.temperature'
        signal_value: 信号值，如 '20日新高突破' / 'bullish' / None（表示不筛选）
        lookback_days: 回溯天数
    
    Returns:
        dict: 胜率统计
    """
    events = load_all_events()
    if not events:
        return None
    
    cutoff_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    
    matched = []
    for event in events:
        if event.get("date", "") < cutoff_date:
            continue
        
        if signal_type:
            # 解析嵌套key
            keys = signal_type.split(".")
            value = event.get("signals", {})
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k)
                else:
                    value = None
                    break
            
            if value is None:
                continue
            if signal_value is not None and str(value) != str(signal_value):
                continue
        
        # 只统计已回填1日收益的
        if event.get("is_correct_1d") is not None:
            matched.append(event)
    
    if not matched:
        return None
    
    correct_1d = sum(1 for e in matched if e["is_correct_1d"])
    correct_5d = [e for e in matched if e.get("is_correct_5d") is not None]
    correct_5d_count = sum(1 for e in correct_5d if e["is_correct_5d"])
    
    avg_return_1d = sum(e["actual_return_1d"] for e in matched) / len(matched)
    avg_return_5d = sum(e["actual_return_5d"] for e in correct_5d) / len(correct_5d) if correct_5d else 0
    
    return {
        "total_count": len(matched),
        "winrate_1d": round(correct_1d / len(matched) * 100, 1),
        "winrate_5d": round(correct_5d_count / len(correct_5d) * 100, 1) if correct_5d else None,
        "avg_return_1d": round(avg_return_1d, 2),
        "avg_return_5d": round(avg_return_5d, 2) if correct_5d else None,
        "sample_5d": len(correct_5d),
    }


def format_kb_summary_for_prompt(lookback_days=90):
    """格式化信号知识库摘要，供注入LLM prompt
    
    统计各类信号组合的历史胜率，让LLM知道哪些信号更可靠。
    
    Args:
        lookback_days: 回溯天数
    
    Returns:
        str: 格式化的知识库摘要文本
    """
    events = load_all_events()
    if not events or len(events) < 5:
        return ""
    
    lines = []
    lines.append("【信号-胜率知识库摘要（历史经验参考）】")
    lines.append("-" * 50)
    
    # 1. 整体胜率
    overall = get_signal_winrate_stats(lookback_days=lookback_days)
    if overall:
        lines.append(f"  整体统计（近{lookback_days}天）: 样本{overall['total_count']}条 | "
                    f"1日胜率{overall['winrate_1d']}% | 5日胜率{overall['winrate_5d']}% | "
                    f"平均1日收益{overall['avg_return_1d']}%")
    
    # 2. 按建议方向统计
    for direction in ["bullish", "bearish", "neutral"]:
        dir_events = [e for e in events if e.get("advice_direction") == direction 
                     and e.get("is_correct_1d") is not None
                     and e.get("date", "") >= (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")]
        if dir_events:
            correct = sum(1 for e in dir_events if e["is_correct_1d"])
            winrate = correct / len(dir_events) * 100
            dir_label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}[direction]
            lines.append(f"  {dir_label}建议: 样本{len(dir_events)}条 | 1日胜率{winrate:.1f}%")
    
    # 3. 按突破信号统计
    breakout_signals = set()
    for e in events:
        bo = e.get("signals", {}).get("momentum", {}).get("breakout")
        if bo:
            breakout_signals.add(bo)
    
    for bo in list(breakout_signals)[:5]:
        stats = get_signal_winrate_stats("momentum.breakout", bo, lookback_days)
        if stats and stats["total_count"] >= 3:
            lines.append(f"  突破信号「{bo}」: 样本{stats['total_count']}条 | 1日胜率{stats['winrate_1d']}%")
    
    # 4. 按KOL共识方向统计
    for direction in ["bullish", "bearish", "neutral"]:
        stats = get_signal_winrate_stats("kol_consensus.consensus_direction", direction, lookback_days)
        if stats and stats["total_count"] >= 3:
            dir_label = {"bullish": "看多共识", "bearish": "看空共识", "neutral": "中性共识"}[direction]
            lines.append(f"  KOL{dir_label}: 样本{stats['total_count']}条 | 1日胜率{stats['winrate_1d']}%")
    
    # 5. 按市场温度统计
    temp_events = [e for e in events if e.get("signals", {}).get("market_env", {}).get("temperature") is not None
                  and e.get("is_correct_1d") is not None
                  and e.get("date", "") >= (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")]
    if temp_events:
        hot = [e for e in temp_events if e["signals"]["market_env"]["temperature"] >= 60]
        cold = [e for e in temp_events if e["signals"]["market_env"]["temperature"] < 40]
        if hot:
            hot_correct = sum(1 for e in hot if e["is_correct_1d"])
            lines.append(f"  高温环境(>=60°): 样本{len(hot)}条 | 1日胜率{hot_correct/len(hot)*100:.1f}%")
        if cold:
            cold_correct = sum(1 for e in cold if e["is_correct_1d"])
            lines.append(f"  低温环境(<40°): 样本{len(cold)}条 | 1日胜率{cold_correct/len(cold)*100:.1f}%")
    
    lines.append("-" * 50)
    lines.append("提示：请参考上述历史胜率数据，对当前信号组合的可靠性进行评估。")
    
    return "\n".join(lines)


def extract_signals_from_analysis(momentum_results, kol_predictions, market_breadth_data, advice_direction):
    """从分析结果中提取信号，用于记录到知识库
    
    Args:
        momentum_results: 动量分析结果
        kol_predictions: KOL预测列表
        market_breadth_data: 市场宽度数据
        advice_direction: 最终建议方向
    
    Returns:
        list: 信号事件列表，每个事件包含 target/signals/advice_direction
    """
    events = []
    
    # 提取KOL共识
    kol_consensus = {"bullish": 0, "bearish": 0, "neutral": 0}
    for pred in kol_predictions:
        direction = pred.get("direction", "neutral")
        if direction in kol_consensus:
            kol_consensus[direction] += 1
    
    # 共识方向
    if kol_consensus["bullish"] > kol_consensus["bearish"] and kol_consensus["bullish"] > kol_consensus["neutral"]:
        kol_consensus["consensus_direction"] = "bullish"
    elif kol_consensus["bearish"] > kol_consensus["bullish"] and kol_consensus["bearish"] > kol_consensus["neutral"]:
        kol_consensus["consensus_direction"] = "bearish"
    else:
        kol_consensus["consensus_direction"] = "neutral"
    
    # 提取市场环境
    market_env = {}
    if market_breadth_data:
        market_env["temperature"] = market_breadth_data.get("temperature")
        market_env["limit_up"] = market_breadth_data.get("limit_up_count")
        market_env["north_flow"] = market_breadth_data.get("north_flow_yi")
    
    # 从动量结果中提取每个标的的信号
    if momentum_results:
        date_str = momentum_results.get("analysis_date", datetime.now().strftime("%Y-%m-%d"))
        
        for stock in momentum_results.get("stocks", []):
            factors = stock.get("momentum_factors", {})
            signals = {
                "momentum": {
                    "trend": factors.get("trend_strength", {}).get("trend_direction") if factors.get("trend_strength") else None,
                    "breakout": factors.get("breakout", {}).get("breakout_signal") if factors.get("breakout") else None,
                    "rsi": factors.get("technical_indicators", {}).get("rsi_14") if factors.get("technical_indicators") else None,
                    "macd": factors.get("technical_indicators", {}).get("macd_signal") if factors.get("technical_indicators") else None,
                },
                "kol_consensus": kol_consensus,
                "market_env": market_env,
            }
            
            events.append({
                "date": date_str,
                "target": stock.get("name", ""),
                "target_type": "stock",
                "code": stock.get("code", ""),
                "signals": signals,
                "advice_direction": advice_direction,
            })
        
        for idx in momentum_results.get("indices", []):
            factors = idx.get("momentum_factors", {})
            signals = {
                "momentum": {
                    "trend": factors.get("trend_strength", {}).get("trend_direction") if factors.get("trend_strength") else None,
                    "breakout": factors.get("breakout", {}).get("breakout_signal") if factors.get("breakout") else None,
                    "rsi": factors.get("technical_indicators", {}).get("rsi_14") if factors.get("technical_indicators") else None,
                    "macd": factors.get("technical_indicators", {}).get("macd_signal") if factors.get("technical_indicators") else None,
                },
                "kol_consensus": kol_consensus,
                "market_env": market_env,
            }
            
            events.append({
                "date": date_str,
                "target": idx.get("name", ""),
                "target_type": "index",
                "code": idx.get("code", ""),
                "signals": signals,
                "advice_direction": advice_direction,
            })
    
    return events


def record_signals_from_analysis(momentum_results, kol_predictions, market_breadth_data, advice_direction):
    """从分析结果中提取并记录信号到知识库
    
    Args:
        momentum_results: 动量分析结果
        kol_predictions: KOL预测列表
        market_breadth_data: 市场宽度数据
        advice_direction: 最终建议方向
    
    Returns:
        int: 记录的事件数量
    """
    events = extract_signals_from_analysis(
        momentum_results, kol_predictions, market_breadth_data, advice_direction
    )
    
    count = 0
    for event in events:
        if record_signal_event(
            event["date"], event["target"], event["target_type"],
            event["code"], event["signals"], event["advice_direction"]
        ):
            count += 1
    
    if count > 0:
        print(f"已记录 {count} 条信号事件到知识库")
    
    return count


if __name__ == "__main__":
    # 更新历史信号的实际收益
    updated = update_signal_outcomes()
    print(f"更新了 {updated} 条信号事件")
    
    # 打印知识库摘要
    summary = format_kb_summary_for_prompt()
    if summary:
        print(summary)
    else:
        print("知识库为空或样本不足")
