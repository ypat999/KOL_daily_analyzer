import os
import re
import json
import time
from datetime import datetime, timedelta
from collections import defaultdict

import akshare as ak
import pandas as pd
import numpy as np

from deepseek_summary import deepseek_summary
from momentum_analyzer import parse_targets_from_text
from prediction_recorder import load_predictions


BILI_UP_KEYWORDS = {
    "江浙陈某": ["江浙陈", "江泽辰", "江浙陈某"],
    "鹰眼看盘": ["鹰眼看盘"],
    "财经-沉默的螺旋": ["沉默的螺旋"],
    "九先生笔记": ["九先生"],
    "连板": ["连板"],
    "李大霄": ["李大霄"],
}

WEIBO_USER_MAP = {
    "2014433131": "唐史主任司马迁",
    "2453509265": "Degg_GlobalMacroFin",
    "3330457880": "王一平_见素抱朴",
    "1769173661": "付鹏的财经世界",
    "1236135807": "空空道人",
}

WECHAT_ACCOUNT_LIST = [
    "财经旗舰", "表舅是养基大户", "华尔街情报圈", "知识旅行家",
    "炒股拌饭", "韭圈儿", "路透财经早报", "猫笔刀",
    "章叔论市", "韭菜公社", "集思录", "看懂龙头股",
]

EVAL_HORIZON_DAYS = 5

_last_request_time = 0
_request_interval = 1.0


def _wait_for_rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _request_interval:
        time.sleep(_request_interval - elapsed)
    _last_request_time = time.time()


_EN_COLUMNS = {'date', 'open', 'high', 'low', 'close', 'volume', 'amount'}
_CN_COLUMNS = {'日期', '开盘', '最高', '最低', '收盘', '成交量', '成交额'}
_EN_TO_CN = {'date': '日期', 'open': '开盘', 'high': '最高', 'low': '最低',
             'close': '收盘', 'volume': '成交量', 'amount': '成交额'}


def _normalize_akshare_columns(df):
    """统一akshare返回DataFrame的列名为中文
    
    新浪/东财等不同数据源列名可能为英文或中文，统一转成中文。
    """
    if df is None or len(df) == 0:
        return df
    
    # 判断列名语言
    col_set = set(str(c) for c in df.columns)
    if col_set & _CN_COLUMNS:
        # 已经是中文列名，只需补齐映射
        return df
    elif col_set & _EN_COLUMNS:
        # 英文列名，映射为中文
        rename_map = {k: v for k, v in _EN_TO_CN.items() if k in col_set}
        return df.rename(columns=rename_map)
    else:
        # 无法识别的列名
        return None


def find_archive_dirs(base_dir=".", months=2):
    dirs = []
    cutoff = datetime.now() - timedelta(days=months * 30)
    for entry in os.listdir(base_dir):
        full = os.path.join(base_dir, entry)
        if os.path.isdir(full) and entry.startswith("archive_"):
            try:
                date_str = entry.replace("archive_", "")
                dir_date = datetime.strptime(date_str, "%Y-%m-%d")
                if dir_date >= cutoff:
                    dirs.append((dir_date, full, date_str))
            except ValueError:
                continue
    dirs.sort(key=lambda x: x[0])
    return dirs


def read_file_safe(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def identify_bili_up(content):
    for up_name, keywords in BILI_UP_KEYWORDS.items():
        for kw in keywords:
            if kw in content:
                return up_name
    return None


def identify_bili_up_via_deepseek(content):
    result_text = deepseek_summary(
        content[:2000],
        sysprompt=(
            "你是一个B站UP主识别引擎。从视频字幕或总结文本中，"
            "识别出该视频的UP主名称。\n\n"
            "已知UP主列表：江浙陈某、李大霄、鹰眼看盘、财经-沉默的螺旋、九先生笔记、连板\n\n"
            "输出严格JSON格式：\n"
            '{"up_name":"UP主名称"}\n\n'
            "如果无法识别，输出：{\"up_name\":\"unknown\"}"
        ),
        userprompt="请识别以下B站视频内容的UP主：\n\n",
        thinking={"type": "disabled"},
        response_format={"type": "json_object"},
        temperature=0.05,
        max_tokens=256,
    )
    try:
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            data = json.loads(json_match.group())
            name = data.get("up_name", "unknown")
            if name != "unknown":
                return name
    except Exception:
        pass
    return None


def scan_bili_sources(archive_dir, date_str):
    sources = []
    for fname in os.listdir(archive_dir):
        if fname.startswith("bili_") and fname.endswith("_summary.txt"):
            content = read_file_safe(os.path.join(archive_dir, fname))
            if not content or len(content.strip()) < 50:
                continue

            up_name = identify_bili_up(content)

            if not up_name:
                subtitle_fname = fname.replace("_summary.txt", ".txt")
                subtitle_path = os.path.join(archive_dir, subtitle_fname)
                if os.path.exists(subtitle_path):
                    subtitle = read_file_safe(subtitle_path)
                    if subtitle:
                        up_name = identify_bili_up(subtitle)

            if not up_name:
                up_name = identify_bili_up_via_deepseek(content)

            if not up_name:
                up_name = "未知UP主"

            sources.append({
                "channel": "bili",
                "blogger": up_name,
                "content": content,
                "date": date_str,
                "filepath": os.path.join(archive_dir, fname),
            })
    return sources


def scan_weibo_sources(archive_dir, date_str):
    sources = []
    for fname in os.listdir(archive_dir):
        if fname.startswith("weibo_") and fname.endswith(".txt") and "投资建议" not in fname:
            content = read_file_safe(os.path.join(archive_dir, fname))
            if not content:
                continue
            blogger = fname.replace("weibo_", "").replace(".txt", "")
            sources.append({
                "channel": "weibo",
                "blogger": blogger,
                "content": content,
                "date": date_str,
                "filepath": os.path.join(archive_dir, fname),
            })
    return sources


def scan_wechat_sources(archive_dir, date_str):
    sources = []
    for fname in os.listdir(archive_dir):
        if fname.startswith("wechat_") and fname.endswith(".txt") and "投资建议" not in fname:
            content = read_file_safe(os.path.join(archive_dir, fname))
            if not content:
                continue
            match = re.match(r"wechat_(.+?)_\d{4}-\d{2}-\d{2}\.txt", fname)
            if match:
                blogger = match.group(1)
            else:
                blogger = fname.replace("wechat_", "").replace(f"_{date_str}", "").replace(".txt", "")
            sources.append({
                "channel": "wechat",
                "blogger": blogger,
                "content": content,
                "date": date_str,
                "filepath": os.path.join(archive_dir, fname),
            })
    return sources


def extract_predictions_from_text(content, blogger, channel):
    channel_label = {"bili": "B站UP主", "weibo": "微博大V", "wechat": "微信公众号"}.get(channel, "博主")

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
            "6. target_type字段只能是: index / sector / stock 之一\n\n"
            "输出严格JSON格式：\n"
            '{{"predictions":[{{"direction":"bullish","target":"创业板指","target_type":"index",'
            '"reason":"技术面回踩企稳后有望反弹","confidence":"high"}}]}}'
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
            return data.get("predictions", [])
    except (json.JSONDecodeError, Exception) as e:
        print(f"  解析预测失败 [{blogger}]: {e}")

    return []


def extract_predictions_from_structured(content, blogger, channel):
    parsed = parse_targets_from_text(content)
    predictions = []

    if parsed:
        for idx in parsed.get("indices", []):
            predictions.append({
                "direction": "bullish",
                "target": idx.get("name", ""),
                "target_type": "index",
                "reason": idx.get("reason", ""),
                "confidence": "medium",
            })
        for stock in parsed.get("stocks", []):
            predictions.append({
                "direction": "bullish",
                "target": stock.get("name", ""),
                "target_type": "stock",
                "reason": stock.get("reason", ""),
                "confidence": "medium",
            })

    if not predictions:
        predictions = extract_predictions_from_text(content, blogger, channel)

    return predictions


STOCK_CODE_MAP = {}


def _resolve_stock_code(target_name):
    if not target_name:
        return None
    if re.match(r'^\d{6}$', target_name):
        return target_name
    if target_name in STOCK_CODE_MAP:
        return STOCK_CODE_MAP[target_name]
    try:
        _wait_for_rate_limit()
        # 优先使用新浪实时行情
        df = ak.stock_zh_a_spot()
        if df is None or df.empty:
            df = ak.stock_zh_a_spot_em()
        for _, row in df.iterrows():
            name = str(row.get("名称", ""))
            code = str(row.get("代码", ""))
            if name == target_name:
                STOCK_CODE_MAP[target_name] = code
                return code
    except Exception:
        pass
    return None


INDEX_NAME_MAP = {
    "上证指数": "000001", "深证成指": "399001", "创业板指": "399006",
    "科创50": "000688", "沪深300": "000300", "恒生指数": "HSI",
    "上证": "000001", "深证": "399001", "创业板": "399006",
}


def get_actual_performance(target, target_type, date_str, horizon=EVAL_HORIZON_DAYS):
    try:
        pred_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None

    end_date_dt = pred_date + timedelta(days=horizon + 5)
    start_date_dt = pred_date + timedelta(days=1)

    start_str = start_date_dt.strftime("%Y%m%d")
    end_str = end_date_dt.strftime("%Y%m%d")

    try:
        _wait_for_rate_limit()

        if target_type == "index":
            code = INDEX_NAME_MAP.get(target, "")
            if not code:
                for name, c in INDEX_NAME_MAP.items():
                    if name in target or target in name:
                        code = c
                        break
            if not code:
                return None

            # 新浪数据源：代码需加sh/sz前缀
            sina_code = f"sh{code}" if code.startswith("000") else f"sz{code}"
            df = ak.stock_zh_index_daily(symbol=sina_code)
            df = _normalize_akshare_columns(df)
            # 新浪返回全量数据，手动过滤日期范围（统一转Timestamp避免类型不一致）
            if df is not None and len(df) > 0 and '日期' in df.columns:
                df['日期'] = pd.to_datetime(df['日期'])
                start_ts = pd.Timestamp(start_date_dt)
                end_ts = pd.Timestamp(end_date_dt)
                df = df[(df['日期'] >= start_ts) & (df['日期'] <= end_ts)]
        elif target_type == "stock":
            code_match = re.search(r'(\d{6})', target)
            if code_match:
                code = code_match.group(1)
            else:
                code = _resolve_stock_code(target)
            if not code:
                return None
            # 新浪数据源：代码需加sh/sz前缀
            sina_code = f"sh{code}" if code.startswith("6") else f"sz{code}"
            df = ak.stock_zh_a_daily(symbol=sina_code, start_date=start_str,
                                     end_date=end_str, adjust="qfq")
            df = _normalize_akshare_columns(df)
        else:
            return None

        if df is None or len(df) == 0 or '日期' not in df.columns:
            return None

        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期')

        pred_day = pd.Timestamp(pred_date)
        future_df = df[df['日期'] > pred_day].head(horizon)

        if len(future_df) < 2:
            return None

        open_price = float(future_df.iloc[0]['开盘'])
        close_price = float(future_df.iloc[-1]['收盘'])

        if open_price == 0:
            return None

        return_pct = round((close_price / open_price - 1) * 100, 2)
        max_price = float(future_df['最高'].max())
        min_price = float(future_df['最低'].min())
        max_return = round((max_price / open_price - 1) * 100, 2)
        min_return = round((min_price / open_price - 1) * 100, 2)

        return {
            "return_pct": return_pct,
            "max_return": max_return,
            "min_return": min_return,
            "days_held": len(future_df),
        }

    except Exception as e:
        print(f"  获取{target}实际数据失败: {e}")
        return None


def score_prediction(prediction, actual):
    if not actual:
        return None

    direction = prediction.get("direction", "neutral")
    ret = actual["return_pct"]
    max_ret = actual["max_return"]
    min_ret = actual["min_return"]

    direction_score = 0
    if direction == "bullish" and ret > 0:
        direction_score = 40
    elif direction == "bearish" and ret < 0:
        direction_score = 40
    elif direction == "neutral" and abs(ret) < 2:
        direction_score = 30
    elif direction == "bullish" and ret <= 0:
        direction_score = max(0, 20 + ret * 2)
    elif direction == "bearish" and ret >= 0:
        direction_score = max(0, 20 - ret * 2)
    else:
        direction_score = 10

    magnitude_score = 0
    if direction == "bullish":
        magnitude_score = min(30, max(0, ret * 3))
    elif direction == "bearish":
        magnitude_score = min(30, max(0, -ret * 3))
    else:
        magnitude_score = max(0, 15 - abs(ret) * 3)

    risk_score = 0
    if direction == "bullish":
        risk_score = max(0, 30 + min_ret * 2)
    elif direction == "bearish":
        risk_score = max(0, 30 - max_ret * 2)
    else:
        risk_score = 20

    total = round(direction_score + magnitude_score + risk_score, 1)

    return {
        "total": total,
        "direction_score": round(direction_score, 1),
        "magnitude_score": round(magnitude_score, 1),
        "risk_score": round(risk_score, 1),
        "actual_return": ret,
        "actual_max_return": max_ret,
        "actual_min_return": min_ret,
    }


def run_backtest(months=2, eval_horizon=EVAL_HORIZON_DAYS):
    print("=" * 70)
    print("KOL博主准确性回测系统")
    print(f"回测范围: 近{months}个月 | 评估窗口: {eval_horizon}个交易日")
    print("=" * 70)

    dirs = find_archive_dirs(".", months)
    if not dirs:
        print("未找到归档目录，退出")
        return

    print(f"\n找到 {len(dirs)} 个归档目录:")
    for d, path, ds in dirs:
        print(f"  {ds}")

    channel_label = {"bili": "B站", "weibo": "微博", "wechat": "微信", "merged": "综合"}

    # 优先从结构化预测记录文件加载
    print("\n" + "=" * 70)
    print("第一步：加载结构化预测记录")
    print("=" * 70)

    blogger_predictions = defaultdict(list)
    structured_count = 0

    for dir_date, dir_path, date_str in dirs:
        preds = load_predictions(dir_path)
        if preds:
            print(f"  {date_str}: 加载到 {len(preds)} 条结构化预测")
            structured_count += len(preds)
            for pred in preds:
                channel = pred.get("channel", "")
                blogger = pred.get("blogger", "")
                if not channel or not blogger:
                    continue
                key = f"{channel}::{blogger}"
                pred["_date"] = pred.get("date", date_str)
                pred["_channel"] = channel
                pred["_blogger"] = blogger
                blogger_predictions[key].append(pred)

    if structured_count > 0:
        print(f"\n从结构化记录加载到 {structured_count} 条预测，跳过文本提取步骤")
    else:
        print("\n未找到结构化预测记录，回退到从非结构化文本提取")

        all_sources = []
        for dir_date, dir_path, date_str in dirs:
            print(f"\n扫描 {date_str} ...")
            all_sources.extend(scan_bili_sources(dir_path, date_str))
            all_sources.extend(scan_weibo_sources(dir_path, date_str))
            all_sources.extend(scan_wechat_sources(dir_path, date_str))

        print(f"\n共扫描到 {len(all_sources)} 个博主内容源")

        channel_counts = defaultdict(int)
        blogger_set = defaultdict(set)
        for src in all_sources:
            channel_counts[src["channel"]] += 1
            blogger_set[src["channel"]].add(src["blogger"])

        for ch, cnt in channel_counts.items():
            bloggers = blogger_set[ch]
            print(f"  {channel_label.get(ch, ch)}: {cnt}条内容, {len(bloggers)}位博主")
            for b in sorted(bloggers):
                print(f"    - {b}")

        for i, src in enumerate(all_sources):
            channel = src["channel"]
            blogger = src["blogger"]
            content = src["content"]
            date_str = src["date"]

            key = f"{channel}::{blogger}"
            print(f"\n[{i+1}/{len(all_sources)}] 提取预测: [{channel_label.get(channel, channel)}] {blogger} ({date_str})")

            if len(content.strip()) < 50:
                print("  内容过短，跳过")
                continue

            predictions = extract_predictions_from_structured(content, blogger, channel)

            if not predictions:
                print("  未提取到预测观点")
                continue

            print(f"  提取到 {len(predictions)} 条预测")
            for pred in predictions:
                pred["_date"] = date_str
                pred["_channel"] = channel
                pred["_blogger"] = blogger
                blogger_predictions[key].append(pred)

    # 统计预测概况
    print(f"\n共加载 {sum(len(v) for v in blogger_predictions.values())} 条预测，覆盖 {len(blogger_predictions)} 个博主")
    for key in sorted(blogger_predictions.keys()):
        channel, blogger = key.split("::", 1)
        count = len(blogger_predictions[key])
        print(f"  [{channel_label.get(channel, channel)}] {blogger}: {count}条")

    print("\n" + "=" * 70)
    print("开始验证预测准确性（获取实际行情数据）")
    print("=" * 70)

    all_results = []

    for key, preds in blogger_predictions.items():
        channel, blogger = key.split("::", 1)
        print(f"\n验证 [{channel_label.get(channel, channel)}] {blogger}: {len(preds)} 条预测")

        for pred in preds:
            target = pred.get("target", "")
            target_type = pred.get("target_type", "")
            direction = pred.get("direction", "")
            date_str = pred.get("_date", "")

            if not target or target_type not in ("index", "stock", "sector"):
                print(f"  跳过无效标的: {target} (type={target_type})")
                continue

            if target_type == "sector":
                print(f"  跳过板块类预测（无法量化验证）: {target}")
                continue

            dir_label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(direction, direction)
            print(f"  验证: {dir_label} {target} ({date_str}) ...", end=" ")
            actual = get_actual_performance(target, target_type, date_str, eval_horizon)

            if actual is None:
                print("无数据")
                continue

            score = score_prediction(pred, actual)
            if score is None:
                print("评分失败")
                continue

            print(f"实际收益={actual['return_pct']}% 评分={score['total']}")
            all_results.append({
                "channel": channel,
                "blogger": blogger,
                "date": date_str,
                "target": target,
                "target_type": target_type,
                "direction": direction,
                "reason": pred.get("reason", ""),
                "confidence": pred.get("confidence", ""),
                "actual_return": actual["return_pct"],
                "actual_max_return": actual["max_return"],
                "actual_min_return": actual["min_return"],
                "total_score": score["total"],
                "direction_score": score["direction_score"],
                "magnitude_score": score["magnitude_score"],
                "risk_score": score["risk_score"],
            })

            time.sleep(0.5)

    print("\n" + "=" * 70)
    print("回测结果汇总")
    print("=" * 70)

    if not all_results:
        print("无有效回测结果")
        return

    df = pd.DataFrame(all_results)

    blogger_stats = df.groupby(["channel", "blogger"]).agg(
        prediction_count=("total_score", "count"),
        avg_score=("total_score", "mean"),
        median_score=("total_score", "median"),
        avg_direction_score=("direction_score", "mean"),
        avg_magnitude_score=("magnitude_score", "mean"),
        avg_risk_score=("risk_score", "mean"),
        avg_actual_return=("actual_return", "mean"),
        hit_rate=("direction_score", lambda x: (x >= 30).sum() / len(x) * 100 if len(x) > 0 else 0),
    ).reset_index()

    blogger_stats = blogger_stats.sort_values("avg_score", ascending=False)

    print(f"\n{'排名':<4} {'渠道':<6} {'博主':<20} {'预测数':<6} {'平均分':<8} "
          f"{'中位分':<8} {'方向分':<8} {'幅度分':<8} {'风控分':<8} {'命中率':<8} {'平均收益':<10}")
    print("-" * 100)

    for rank, (_, row) in enumerate(blogger_stats.iterrows(), 1):
        ch = channel_label.get(row["channel"], row["channel"])
        print(f"{rank:<4} {ch:<6} {row['blogger']:<20} {int(row['prediction_count']):<6} "
              f"{row['avg_score']:<8.1f} {row['median_score']:<8.1f} "
              f"{row['avg_direction_score']:<8.1f} {row['avg_magnitude_score']:<8.1f} "
              f"{row['avg_risk_score']:<8.1f} {row['hit_rate']:<8.1f}% "
              f"{row['avg_actual_return']:<10.2f}%")

    channel_stats = df.groupby("channel").agg(
        prediction_count=("total_score", "count"),
        avg_score=("total_score", "mean"),
        hit_rate=("direction_score", lambda x: (x >= 30).sum() / len(x) * 100 if len(x) > 0 else 0),
        avg_actual_return=("actual_return", "mean"),
    ).reset_index()

    print(f"\n{'渠道':<10} {'预测总数':<10} {'渠道均分':<10} {'命中率':<10} {'平均收益':<10}")
    print("-" * 50)
    for _, row in channel_stats.iterrows():
        ch = channel_label.get(row["channel"], row["channel"])
        print(f"{ch:<10} {int(row['prediction_count']):<10} {row['avg_score']:<10.1f} "
              f"{row['hit_rate']:<10.1f}% {row['avg_actual_return']:<10.2f}%")

    report_path = f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("KOL博主准确性回测报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"回测范围: 近{months}个月 | 评估窗口: {eval_horizon}个交易日\n")
        f.write("=" * 100 + "\n\n")

        f.write("【博主排名】\n")
        f.write(f"{'排名':<4} {'渠道':<6} {'博主':<20} {'预测数':<6} {'平均分':<8} "
                f"{'中位分':<8} {'方向分':<8} {'幅度分':<8} {'风控分':<8} {'命中率':<8} {'平均收益':<10}\n")
        f.write("-" * 100 + "\n")
        for rank, (_, row) in enumerate(blogger_stats.iterrows(), 1):
            ch = channel_label.get(row["channel"], row["channel"])
            f.write(f"{rank:<4} {ch:<6} {row['blogger']:<20} {int(row['prediction_count']):<6} "
                    f"{row['avg_score']:<8.1f} {row['median_score']:<8.1f} "
                    f"{row['avg_direction_score']:<8.1f} {row['avg_magnitude_score']:<8.1f} "
                    f"{row['avg_risk_score']:<8.1f} {row['hit_rate']:<8.1f}% "
                    f"{row['avg_actual_return']:<10.2f}%\n")

        f.write(f"\n【渠道对比】\n")
        f.write(f"{'渠道':<10} {'预测总数':<10} {'渠道均分':<10} {'命中率':<10} {'平均收益':<10}\n")
        f.write("-" * 50 + "\n")
        for _, row in channel_stats.iterrows():
            ch = channel_label.get(row["channel"], row["channel"])
            f.write(f"{ch:<10} {int(row['prediction_count']):<10} {row['avg_score']:<10.1f} "
                    f"{row['hit_rate']:<10.1f}% {row['avg_actual_return']:<10.2f}%\n")

        f.write(f"\n【逐条预测明细】\n")
        f.write(f"{'日期':<12} {'渠道':<6} {'博主':<16} {'方向':<6} {'标的':<12} "
                f"{'实际收益':<10} {'总分':<8} {'方向分':<8} {'幅度分':<8} {'风控分':<8}\n")
        f.write("-" * 110 + "\n")
        for _, row in df.iterrows():
            ch = channel_label.get(row["channel"], row["channel"])
            dir_label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(row["direction"], row["direction"])
            f.write(f"{row['date']:<12} {ch:<6} {row['blogger']:<16} {dir_label:<6} {row['target']:<12} "
                    f"{row['actual_return']:<10.2f}% {row['total_score']:<8.1f} "
                    f"{row['direction_score']:<8.1f} {row['magnitude_score']:<8.1f} {row['risk_score']:<8.1f}\n")

    print(f"\n详细报告已保存到: {report_path}")

    csv_path = f"backtest_detail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"明细数据已保存到: {csv_path}")

    return blogger_stats, df


def load_latest_backtest_stats():
    """加载最近一次回测的博主命中率统计
    
    从最新的 backtest_detail_*.csv 文件中聚合计算每个博主的命中率，
    供合并投资建议时动态调整权重使用。
    
    Returns:
        dict: {channel::blogger: {"hit_rate": float, "avg_score": float, "count": int}}
    """
    import glob
    
    csv_files = sorted(glob.glob("backtest_detail_*.csv"), reverse=True)
    if not csv_files:
        return {}
    
    try:
        df = pd.read_csv(csv_files[0], encoding="utf-8-sig")
    except Exception as e:
        print(f"加载回测明细失败: {e}")
        return {}
    
    if df.empty:
        return {}
    
    stats = {}
    grouped = df.groupby(["channel", "blogger"])
    for (channel, blogger), group in grouped:
        hit_rate = (group["direction_score"] >= 30).sum() / len(group) * 100 if len(group) > 0 else 0
        avg_score = group["total_score"].mean()
        stats[f"{channel}::{blogger}"] = {
            "hit_rate": round(hit_rate, 1),
            "avg_score": round(avg_score, 1),
            "count": len(group),
            "avg_return": round(group["actual_return"].mean(), 2),
        }
    
    return stats


def format_backtest_summary_for_prompt(stats):
    """将回测命中率格式化为可注入LLM prompt的文本
    
    Args:
        stats: load_latest_backtest_stats() 返回的字典
    
    Returns:
        str: 格式化的博主表现摘要文本
    """
    if not stats:
        return ""
    
    channel_label = {"bili": "B站", "weibo": "微博", "wechat": "微信", "merged": "综合"}
    
    lines = ["【各博主历史预测命中率（来自最近回测）】"]
    lines.append("-" * 50)
    
    # 按命中率排序
    sorted_stats = sorted(stats.items(), key=lambda x: x[1]["hit_rate"], reverse=True)
    
    for key, s in sorted_stats:
        parts = key.split("::", 1)
        if len(parts) != 2:
            continue
        channel, blogger = parts
        ch_label = channel_label.get(channel, channel)
        
        # 根据命中率给出权重调整建议
        if s["hit_rate"] >= 60:
            rating = "★★★ 高可信"
        elif s["hit_rate"] >= 40:
            rating = "★★ 中等可信"
        else:
            rating = "★ 低可信，观点需打折"
        
        lines.append(
            f"  {ch_label}「{blogger}」: 命中率{s['hit_rate']}% | "
            f"平均分{s['avg_score']} | 样本{s['count']}条 | 平均收益{s['avg_return']}% | {rating}"
        )
    
    lines.append("-" * 50)
    lines.append("提示：请根据上述命中率动态调整各博主观点的权重，低命中率博主的观点需更多交叉验证。")
    
    return "\n".join(lines)


if __name__ == "__main__":
    run_backtest(months=2, eval_horizon=5)
