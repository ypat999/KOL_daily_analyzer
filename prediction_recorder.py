import json
import os
import re
from datetime import datetime
from deepseek_summary import deepseek_summary


PREDICTION_FILENAME = "predictions_{channel}_{date}.json"

CHANNEL_LABELS = {
    "bili": "B站UP主",
    "weibo": "微博大V",
    "wechat": "微信公众号",
    "merged": "综合分析",
}


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
