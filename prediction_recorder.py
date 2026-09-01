import json
import os
import re
from datetime import datetime, timedelta
from deepseek_summary import deepseek_summary


PREDICTION_FILENAME = "predictions_{channel}_{date}.json"

CHANNEL_LABELS = {
    "bili": "B站UP主",
    "weibo": "微博大V",
    "wechat": "微信公众号",
    "merged": "综合分析",
}


def find_previous_archive_folder(current_date_str, base_dir="."):
    """查找上一个有预测记录的归档目录
    
    Args:
        current_date_str: 当前分析日期 'YYYY-MM-DD'
        base_dir: 基础目录
    
    Returns:
        tuple: (archive_folder, date_str) 或 None
    """
    try:
        current_date = datetime.strptime(current_date_str, "%Y-%m-%d")
    except ValueError:
        return None
    
    # 向前查找最多14天
    for i in range(1, 15):
        check_date = current_date - timedelta(days=i)
        date_str = check_date.strftime("%Y-%m-%d")
        folder = os.path.join(base_dir, f"archive_{date_str}")
        if os.path.isdir(folder):
            # 检查是否有预测文件
            has_preds = any(f.startswith("predictions_") and f.endswith(".json") 
                          for f in os.listdir(folder))
            if has_preds:
                return folder, date_str
    
    return None


def generate_yesterday_review(current_date_str, base_dir="."):
    """生成昨日预测复盘报告
    
    加载上一个交易日的预测记录，获取实际行情数据，对照复盘。
    
    Args:
        current_date_str: 当前分析日期 'YYYY-MM-DD'
        base_dir: 基础目录
    
    Returns:
        str: 复盘报告文本，无数据时返回空字符串
    """
    prev = find_previous_archive_folder(current_date_str, base_dir)
    if not prev:
        print("未找到上一个有预测记录的归档目录，跳过昨日复盘")
        return ""
    
    prev_folder, prev_date = prev
    print(f"加载昨日预测记录: {prev_date}")
    
    preds = load_predictions(prev_folder)
    if not preds:
        print("昨日无预测记录，跳过复盘")
        return ""
    
    # 延迟导入避免循环依赖
    from backtest_analyzer import get_actual_performance, score_prediction

    # 先并行批量抓取行情（去重 + 多线程），避免逐条串行导致复盘长达几十分钟。
    # get_actual_performance 内部有当日缓存，这里传入 bypass_rate_limit 跳过全局
    # 1s 限速锁，让并发真正生效。
    from concurrent.futures import ThreadPoolExecutor

    unique_preds = [p for p in preds
                    if p.get("target") and p.get("target_type") in ("index", "stock")]
    seen = set()
    tasks = []
    for p in unique_preds:
        key = (p.get("target_type"), p.get("target"))
        if key in seen:
            continue
        seen.add(key)
        tasks.append(p)

    perf_map = {}
    if tasks:
        print(f"  并行抓取 {len(tasks)} 个唯一标的的实际行情（去重后）...")
        def _fetch(p):
            t = p.get("target")
            tt = p.get("target_type")
            return (tt, t), get_actual_performance(t, tt, prev_date, horizon=1,
                                                   bypass_rate_limit=True)
        with ThreadPoolExecutor(max_workers=6) as ex:
            for key, val in ex.map(_fetch, tasks):
                perf_map[key] = val

    channel_label = {"bili": "B站", "weibo": "微博", "wechat": "微信", "merged": "综合"}
    
    review_lines = []
    review_lines.append("=" * 60)
    review_lines.append(f"昨日预测复盘（{prev_date}）")
    review_lines.append("=" * 60)
    
    correct_count = 0
    wrong_count = 0
    no_data_count = 0
    reviewed = 0
    
    for pred in preds:
        target = pred.get("target", "")
        target_type = pred.get("target_type", "")
        direction = pred.get("direction", "")
        channel = pred.get("channel", "")
        blogger = pred.get("blogger", "")
        reason = pred.get("reason", "")
        
        if not target or target_type not in ("index", "stock"):
            continue
        
        # 计算从预测日到今天的表现（优先用并行预抓结果，未命中再现抓）
        actual = perf_map.get((target_type, target)) or get_actual_performance(
            target, target_type, prev_date, horizon=1)
        
        if actual is None:
            no_data_count += 1
            continue
        
        reviewed += 1
        score = score_prediction(pred, actual)
        ret = actual["return_pct"]
        
        dir_label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(direction, direction)
        ch_label = channel_label.get(channel, channel)
        
        # 判断对错
        is_correct = False
        if direction == "bullish" and ret > 0:
            is_correct = True
        elif direction == "bearish" and ret < 0:
            is_correct = True
        elif direction == "neutral" and abs(ret) < 1:
            is_correct = True
        
        if is_correct:
            correct_count += 1
            mark = "✓ 正确"
        else:
            wrong_count += 1
            mark = "✗ 错误"
        
        review_lines.append(
            f"  {mark} | {ch_label}「{blogger}」{dir_label} {target} | "
            f"预测理由: {reason[:30]} | 实际: {ret:+.2f}%"
        )
    
    if reviewed == 0:
        review_lines.append("  无可复盘的预测（均为板块类或无数据）")
    else:
        total = correct_count + wrong_count
        hit_rate = correct_count / total * 100 if total > 0 else 0
        review_lines.append("-" * 60)
        review_lines.append(
            f"  复盘统计: {reviewed}条已验证 | 正确{correct_count} | 错误{wrong_count} | "
            f"无数据{no_data_count} | 命中率{hit_rate:.0f}%"
        )
        review_lines.append("  提示：请根据昨日预测对错情况，修正今日判断方向。")
    
    review_lines.append("=" * 60)
    
    return "\n".join(review_lines)


def extract_predictions(content, channel, blogger, date_str):
    channel_label = CHANNEL_LABELS.get(channel, "博主")

    result_text = deepseek_summary(
        content,
        sysprompt=(
            f"你是一个金融信息结构化提取引擎。你的任务是从{channel_label}的内容中，"
            "提取出所有可验证的投资预测观点。\n\n"
            "提取规则：\n"
            "1. 只提取明确的、可事后验证的预测（如'XX板块将上涨'、'XX股票值得关注'、'市场将调整'等）\n"
            "2. 忽略纯信息陈述、数据引用、历史回顾等不含预测性的内容\n"
            "3. 每个预测必须包含：方向（看多/看空/中性）、标的（具体指数/板块/个股）、理由摘要\n"
            "4. 如果内容中确实没有明确预测，返回空列表\n"
            "5. direction字段只能是: bullish / bearish / neutral 之一\n"
            "6. target_type字段只能是: index / sector / stock 之一\n"
            "7. 对于股票标的，尽可能同时提供6位数字代码（code字段）\n"
            "8. 对于指数标的，使用标准代码（上证000001/深证成指399001/创业板399006/科创50-000688/沪深300-000300）\n\n"
            "输出严格JSON格式：\n"
            '{{"predictions":[{{"direction":"bullish","target":"创业板指","target_type":"index",'
            '"code":"399006","reason":"技术面回踩企稳后有望反弹","confidence":"high"}}]}}'
        ),
        userprompt=f"请从以下{channel_label}「{blogger}」的内容中提取所有可验证的投资预测观点：\n\n",
        thinking={"type": "disabled"},
        response_format={"type": "json_object"},
        temperature=0.05,
        max_tokens=4096,
    )

    try:
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            data = json.loads(json_match.group())
            predictions = data.get("predictions", [])
            for pred in predictions:
                pred["channel"] = channel
                pred["blogger"] = blogger
                pred["date"] = date_str
                pred["record_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return predictions
    except (json.JSONDecodeError, Exception) as e:
        print(f"  解析预测失败 [{blogger}]: {e}")

    return []


def save_predictions(predictions, channel, date_str, archive_folder):
    if not predictions:
        print(f"  无预测记录可保存 [{channel}]")
        return

    filename = PREDICTION_FILENAME.format(channel=channel, date=date_str)
    filepath = os.path.join(archive_folder, filename)

    existing = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.extend(predictions)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"  预测记录已保存: {filepath} (共{len(existing)}条)")


def load_predictions(archive_folder, channel=None, date_str=None):
    all_preds = []

    if not os.path.exists(archive_folder):
        return all_preds

    for fname in os.listdir(archive_folder):
        if not fname.startswith("predictions_") or not fname.endswith(".json"):
            continue

        if channel and not fname.startswith(f"predictions_{channel}_"):
            continue

        if date_str and date_str not in fname:
            continue

        filepath = os.path.join(archive_folder, fname)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                preds = json.load(f)
                all_preds.extend(preds)
        except Exception as e:
            print(f"  读取预测文件失败 {fname}: {e}")

    return all_preds


def record_predictions_from_advice(advice_text, channel, blogger, date_str, archive_folder):
    if not advice_text or len(advice_text.strip()) < 50:
        print(f"  内容过短，跳过预测提取 [{channel}/{blogger}]")
        return []

    print(f"  提取预测观点: [{channel}] {blogger}")
    predictions = extract_predictions(advice_text, channel, blogger, date_str)

    if predictions:
        print(f"  提取到 {len(predictions)} 条预测观点")
        save_predictions(predictions, channel, date_str, archive_folder)
    else:
        print(f"  未提取到预测观点 [{channel}/{blogger}]")

    return predictions
