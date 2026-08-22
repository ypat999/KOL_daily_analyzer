# -*- coding: utf-8 -*-
"""微信读书桥接：用普通微信号抓取公众号文章（替代 mp 后台 appmsg 接口）

原理：个人微信扫码登录"微信读书"（we-wrss 同款中转平台 weread.111965.xyz），
平台按公众号解析（MP_WXS_xxx id）拉取文章列表，正文直接从 mp.weixin.qq.com/s/ 抓取。
与 mp 后台的 appmsg freq control 完全隔离。

配置文件 wechat_weread_accounts.json:
{
  "accounts": [
    {"name": "公众号名", "example_link": "https://mp.weixin.qq.com/s/xxx"}
  ]
}
example_link 仅在首次解析 mp id 时使用（wxs2mp），解析结果缓存到 weread_mpids.json。
"""
import json, os, re, sys, time, random
import requests
from datetime import datetime

from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend
from deepseek_summary import deepseek_summary
from prediction_recorder import record_predictions_from_advice

# wewe-rss 官方微信读书中转平台（加速域名已下线，用主域名）
PLATFORM = "https://weread.111965.xyz"

# ===== 微信任务总开关（微信读书桥接版）=====
# 新链路基于普通微信号+微信读书平台，与 mp 后台 appmsg 接口(freq control)完全隔离。
# 不需要 mp 后台 cookie。改为 False 可整体禁用微信任务。
WECHAT_ENABLED = True

AUTH_FILE = "weread_auth.json"          # 扫码登录凭据 {vid, token, username}
MPID_CACHE_FILE = "weread_mpids.json"   # 公众号名 -> mp id 缓存
CONFIG_FILE = "wechat_weread_accounts.json"
TIMEOUT = 30
LIMIT_HOURS = 18  # 平时限定 18 小时（与 wechat_get.py 一致）
# 每页文章数由平台返回（实测约50篇），只拉前几页即可覆盖当日文章
MAX_PAGES = 2


def load_config():
    """读取公众号配置（公众号名 -> 示例文章链接）"""
    if not os.path.exists(CONFIG_FILE):
        print(f"错误: 未找到配置文件 {CONFIG_FILE}")
        return []
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("accounts", [])


def load_auth():
    """读取已保存的登录凭据"""
    if not os.path.exists(AUTH_FILE):
        return None
    with open(AUTH_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_auth(auth):
    with open(AUTH_FILE, "w", encoding="utf-8") as f:
        json.dump(auth, f, ensure_ascii=False, indent=2)


def _platform_request(method, path, headers=None, timeout=TIMEOUT, **kwargs):
    url = f"{PLATFORM}{path}"
    try:
        r = requests.request(method, url, timeout=timeout, headers=headers, **kwargs)
        return r
    except Exception as e:
        print(f"  平台请求异常: {e}")
        return None


def login_weread():
    """扫码登录微信读书，返回凭据。二维码保存为 weread_qr.png 并自动打开。"""
    print("开始微信读书扫码登录...")
    r = _platform_request("GET", "/api/v2/login/platform")
    if r is None or r.status_code != 200:
        print("获取登录二维码失败")
        return None
    data = r.json()
    uid = data.get("uuid")
    scan_url = data.get("scanUrl")
    if not uid:
        print(f"平台返回异常: {data}")
        return None

    import qrcode, os as _os
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(scan_url or f"{PLATFORM}/login?uuid={uid}")
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save("weread_qr.png")
    print(f"二维码已保存 weread_qr.png，请用手机微信扫码登录微信读书")
    if _os.name == "nt":
        _os.startfile("weread_qr.png")

    for i in range(100):
        time.sleep(3)
        r = _platform_request("GET", f"/api/v2/login/platform/{uid}", timeout=120)
        if r is None:
            continue
        try:
            d = r.json()
        except Exception:
            continue
        if d.get("token") and d.get("vid"):
            auth = {"vid": d.get("vid"), "token": d.get("token"), "username": d.get("username")}
            save_auth(auth)
            print(f"登录成功: {auth.get('username')} (vid={auth.get('vid')})")
            return auth
        if i % 10 == 0:
            print(f"  等待扫码... {d.get('message', '')}")
    print("登录等待超时")
    return None


def get_mp_id(auth, account_name, example_link):
    """用示例文章链接解析公众号 mp id（优先使用缓存）"""
    # 查缓存
    cache = {}
    if os.path.exists(MPID_CACHE_FILE):
        with open(MPID_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    if account_name in cache:
        return cache[account_name]

    headers = {"xid": str(auth["vid"]), "Authorization": f"Bearer {auth['token']}"}
    r = _platform_request("POST", "/api/v2/platform/wxs2mp", headers=headers, json={"url": example_link})
    if r is None or r.status_code != 200:
        print(f"  解析公众号失败: HTTP {r.status_code if r else 'None'}")
        return None
    try:
        mps = r.json()
        if isinstance(mps, dict):
            mps = mps.get("data", [])
    except Exception as e:
        print(f"  解析响应异常: {e}")
        return None
    if not mps:
        print(f"  未解析到公众号（示例链接可能失效）: {example_link}")
        return None
    mp = mps[0]
    mp_id = mp.get("id")
    cache[account_name] = mp_id
    with open(MPID_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"  公众号 {mp.get('name')} 解析成功: {mp_id}")
    return mp_id


def get_mp_articles(auth, mp_id, page=1, max_retries=3):
    """拉取公众号文章列表 [{id, title, picUrl, publishTime}]
    平台对连续请求有限流（HTTP 200 但 data 空），空结果自动重试
    """
    headers = {"xid": str(auth["vid"]), "Authorization": f"Bearer {auth['token']}"}
    for attempt in range(max_retries):
        r = _platform_request("GET", f"/api/v2/platform/mps/{mp_id}/articles?page={page}", headers=headers)
        if r is not None and r.status_code == 200:
            try:
                arts = r.json()
                if isinstance(arts, dict):
                    arts = arts.get("data", [])
                if isinstance(arts, list) and arts:
                    return arts
                # 空结果：可能被限流，等待后重试
                if attempt < max_retries - 1:
                    print(f"    平台返回空列表（可能限流），{8*(attempt+1)}秒后重试({attempt+2}/{max_retries})...")
                    time.sleep(8 * (attempt + 1))
            except Exception as e:
                print(f"  文章列表解析异常: {e}")
                return []
        else:
            if r is not None and r.status_code == 401:
                print("  微信读书凭据已失效（HTTP 401），需重新扫码登录")
                return []
            print(f"  拉取文章列表失败: HTTP {r.status_code if r else 'None'}")
            if attempt < max_retries - 1:
                time.sleep(8 * (attempt + 1))
    return []


def verify_auth(auth):
    """校验微信读书登录凭据是否有效

    Returns:
        bool: True 有效 / False token 失效 / None 网络异常无法判断
    """
    headers = {"xid": str(auth["vid"]), "Authorization": f"Bearer {auth['token']}"}
    r = _platform_request("GET", "/api/v2/platform/mps/0/articles?page=1", headers=headers, timeout=15)
    if r is None:
        return None
    return r.status_code != 401


def is_today_article_ts(ts):
    """按发布时间戳判断是否限定时间内（与 wechat_get.is_today_article 规则一致）"""
    try:
        article_datetime = datetime.fromtimestamp(ts)
        now = datetime.now()
        if now.weekday() >= 5:  # 周末：只收录周五收盘后
            friday_date = get_friday_date_for_weekend(now)
            friday_close_time = friday_date.replace(hour=15, minute=0, second=0, microsecond=0)
            return article_datetime >= friday_close_time
        else:
            return (now - article_datetime).total_seconds() / 3600 <= LIMIT_HOURS
    except Exception:
        return False


def fetch_article_content(url):
    """从 mp.weixin.qq.com/s/ 抓取文章正文纯文本"""
    try:
        time.sleep(random.uniform(2, 4))
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            print(f"  抓取正文失败: HTTP {r.status_code}")
            return None
        html = r.text
        m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', html, re.S)
        if not m:
            m = re.search(r'id="js_content"[^>]*>(.*?)</div>', html, re.S)
        if not m:
            print("  未找到正文节点 js_content")
            return None
        content = re.sub(r'<script.*?</script>', '', m.group(1), flags=re.S)
        content = re.sub(r'<style.*?</style>', '', content, flags=re.S)
        content = re.sub(r'<[^>]+>', '', content)
        for k, v in [('&nbsp;', ' '), ('&lt;', '<'), ('&gt;', '>'), ('&amp;', '&'),
                     ('&quot;', '"'), ('&#39;', "'")]:
            content = content.replace(k, v)
        content = re.sub(r'\s+', ' ', content).strip()
        return content or None
    except Exception as e:
        print(f"  抓取正文异常: {e}")
        return None


def save_single_article(account_name, art, content, today):
    """保存单篇文章到归档（格式与 wechat_get.save_single_article 一致）"""
    archive_dir = f"archive_{today}"
    os.makedirs(archive_dir, exist_ok=True)
    account_filename = f"{archive_dir}/wechat_{account_name}_{today}.txt"
    url = f"https://mp.weixin.qq.com/s/{art.get('id')}"
    with open(account_filename, "a", encoding="utf-8") as f:
        f.write("文章:\n")
        f.write(f"标题: {art.get('title', '无标题')}\n")
        f.write(f"链接: {url}\n")
        f.write(f"发布时间: {datetime.fromtimestamp(art.get('publishTime', 0)).strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("-" * 30 + "\n")
        f.write("完整内容:\n")
        f.write(content or "")
        f.write("\n" + "=" * 80 + "\n\n")
    if content and len(content.strip()) > 50:
        record_predictions_from_advice(content, "wechat", account_name, today, archive_dir)


def collect_all_articles_content(today):
    """收集当日所有公众号文章内容（与 wechat_get.collect_all_articles_content 一致）"""
    archive_dir = f"archive_{today}"
    all_articles_content = []
    if not os.path.exists(archive_dir):
        return ""
    for filename in os.listdir(archive_dir):
        if filename.startswith('wechat_') and filename.endswith('.txt') and not filename.endswith('_summary.txt'):
            filepath = os.path.join(archive_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 文件名形如 wechat_章叔论市_2026-08-06.txt，去掉前后缀取公众号名
                    account_name = filename[len('wechat_'):-len(f'_{today}.txt')]
                    all_articles_content.append(f"=== 公众号: {account_name} ===\n{content}")
            except Exception as e:
                print(f"读取文件失败 {filepath}: {e}")
    return '\n\n'.join(all_articles_content)


def generate_investment_advice(all_content, today):
    """基于所有文章内容生成投资建议（prompt 与 wechat_get 一致）"""
    print("开始生成投资分析建议...")
    archive_dir = f"archive_{today}"

    investment_advice = deepseek_summary(
        all_content,
        sysprompt=(
            "你是一位资深基本面研究分析师，专注于从微信公众号深度文章中提取机构级投资洞见。"
            "微信公众号文章通常比短视频更有深度和逻辑性，你需要深度挖掘其分析框架和价值判断。\n\n"
            "分析规则：\n"
            "1. 逻辑链追溯：识别每篇文章的「核心假设→推理过程→最终结论」，评估逻辑严密性\n"
            "2. 数据认证：区分「有数据支撑的结论」vs「纯观点输出」，前者赋予更高权重\n"
            "3. 产业链联动：当多篇文章分别提及同一产业链上下游时，标注产业链共振信号\n"
            "4. 政策敏感性：重点标注涉及政策变化、监管动向的文章，这些往往有超前信号价值\n"
            "5. 估值锚定：提取文章中关于估值水平（高估/合理/低估）的判断及其参照系\n\n"
            "输出风格：深度、结构化、逻辑严密，每个结论附支撑论据，避免空泛表述。"
        ),
        userprompt=(
            "以下是近期头部财经公众号的文章合集，请基于这些深度内容完成专业研判：\n\n"
            "请按以下结构输出完整分析报告：\n\n"
            "【一、宏观驱动因子拆解】\n"
            "- 当前影响市场的核心宏观变量有哪些？（利率/汇率/政策/外盘/地缘等）\n"
            "- 各因子的边际变化方向及影响权重\n"
            "- 机构主流预期的共识与分歧点\n\n"
            "【二、产业链深度研判】\n"
            "- 按产业链上下游梳理被提及最多的赛道\n"
            "- 各赛道所处生命周期阶段（导入/成长/成熟/衰退）\n"
            "- 关键催化剂和业绩验证时间节点\n\n"
            "【三、资金面与市场结构】\n"
            "- 增量资金来源分析（北向/两融/ETF/公募发行等）\n"
            "- 市场风格判断（大盘vs小盘、价值vs成长、防御vs进攻）\n"
            "- 筹码分布和关键阻力/支撑位\n\n"
            "【四、操作策略矩阵】\n"
            "- 短期（1-5日）：交易性机会和事件驱动策略\n"
            "- 中期（1-4周）：趋势跟踪和行业配置建议\n"
            "- 给出明确的持仓结构建议：核心仓位+卫星仓位\n"
            "- 每项建议标注置信度和最大回撤容忍度\n\n"
            "【五、尾部风险清单】\n"
            "- 被市场忽视但可能爆发的3个黑天鹅风险\n"
            "- 灰犀牛风险（已被认知但未充分定价的）\n"
            "- 风险对冲建议\n\n"
            "【六、标的清单（JSON）】\n"
            "将所有涉及的重点指数和股票以严格JSON格式输出：\n"
            "```json\n"
            "{\n"
            '    "indices": [\n'
            '        {"code": "000001", "name": "上证指数", "reason": "关注原因"}\n'
            "    ],\n"
            '    "stocks": [\n'
            '        {"code": "600519", "name": "贵州茅台", "reason": "关注原因"}\n'
            "    ]\n"
            "}\n"
            "```\n"
            "指数代码：上证000001/深证成指399001/创业板399006/科创50-000688/沪深300-000300\n"
            "股票代码：6位纯数字，只列明确推荐或强烈暗示的标的\n\n"
            "=== 以下为分析素材 ===\n\n"
        )
    )

    os.makedirs(archive_dir, exist_ok=True)
    advice_filename = f"{archive_dir}/wechat_投资建议_{today}.txt"
    with open(advice_filename, 'w', encoding='utf-8') as f:
        f.write(f"投资分析建议 - {today}\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 50 + "\n\n")
        f.write(investment_advice)
    print(f"投资建议已保存到: {advice_filename}")

    record_predictions_from_advice(investment_advice, "wechat", "微信综合", today, archive_dir)
    return investment_advice


def run_wechat_task(generate_advice=True):
    """运行微信公众号文章分析任务（微信读书桥接版）"""
    print("\n" + "=" * 50)
    print("开始执行微信公众号文章分析任务（微信读书桥接）")
    print("=" * 50)

    today, date_reason, archive_folder = get_current_analysis_date()
    ensure_archive_folder(archive_folder)

    auth = load_auth()
    if not auth:
        print("未找到微信读书登录凭据，开始扫码登录...")
        auth = login_weread()
        if not auth:
            print("登录失败，微信任务中止")
            return None
    else:
        auth_status = verify_auth(auth)
        if auth_status is False:
            print("微信读书凭据已失效（token过期），自动重新扫码登录...")
            auth = login_weread()
            if not auth:
                print("重新登录失败，微信任务中止")
                return None
        elif auth_status is None:
            print("微信读书凭据有效性校验失败（网络异常），按有效继续执行...")

    accounts = load_config()
    if not accounts:
        print(f"配置文件 {CONFIG_FILE} 为空，请先添加公众号（名称+示例文章链接）")
        return None

    total_saved = 0
    random.shuffle(accounts)
    failed_accounts = []  # 首轮返回空（可能限流）的账号，末轮统一重试

    for idx, acc in enumerate(accounts):
        name = acc.get("name", "")
        link = acc.get("example_link", "")
        if not name or not link:
            continue
        # 账号间延迟，避免连续请求触发平台限流
        if idx > 0:
            delay = random.uniform(5, 10)
            print(f"  账号间等待 {delay:.1f}秒...")
            time.sleep(delay)
        print(f"\n处理公众号: {name}")
        got = 0
        try:
            mp_id = get_mp_id(auth, name, link)
            if not mp_id:
                continue
            today_articles = []
            for page in range(1, MAX_PAGES + 1):
                arts = get_mp_articles(auth, mp_id, page=page)
                if not arts:
                    break
                page_today = [a for a in arts if is_today_article_ts(a.get("publishTime", 0))]
                today_articles.extend(page_today)
                if len(page_today) < len(arts):
                    break  # 本页已含非今日文章，无需继续翻页
                time.sleep(random.uniform(2, 4))
            got = len(today_articles)
            print(f"  {name} 获取到 {got} 篇限定时间内文章")
            for art in today_articles:
                try:
                    url = f"https://mp.weixin.qq.com/s/{art.get('id')}"
                    print(f"    抓取正文: {art.get('title')}")
                    content = fetch_article_content(url)
                    if content:
                        save_single_article(name, art, content, today)
                        total_saved += 1
                    else:
                        print(f"    正文获取失败: {art.get('title')}")
                except Exception as e:
                    print(f"    处理文章异常: {e}")
                    continue
        except Exception as e:
            print(f"  处理 {name} 异常: {e}")
        if got == 0:
            failed_accounts.append(acc)

    # 末轮重试：首轮因限流返回空的账号，等待后重新尝试
    if failed_accounts:
        print(f"\n首轮有 {len(failed_accounts)} 个账号未获取到文章，等待60秒后统一重试...")
        time.sleep(60)
        for acc in failed_accounts:
            name = acc.get("name", "")
            link = acc.get("example_link", "")
            print(f"\n重试公众号: {name}")
            time.sleep(random.uniform(5, 10))
            try:
                mp_id = get_mp_id(auth, name, link)
                if not mp_id:
                    continue
                got = 0
                for page in range(1, MAX_PAGES + 1):
                    arts = get_mp_articles(auth, mp_id, page=page)
                    if not arts:
                        break
                    page_today = [a for a in arts if is_today_article_ts(a.get("publishTime", 0))]
                    today_articles = page_today
                    for art in today_articles:
                        url = f"https://mp.weixin.qq.com/s/{art.get('id')}"
                        print(f"    抓取正文: {art.get('title')}")
                        content = fetch_article_content(url)
                        if content:
                            save_single_article(name, art, content, today)
                            total_saved += 1
                            got += 1
                    if len(page_today) < len(arts):
                        break
                print(f"  {name} 重试后获取到 {got} 篇")
            except Exception as e:
                print(f"  {name} 重试异常: {e}")

    print(f"\n共保存 {total_saved} 篇今日文章内容")

    all_articles_content = collect_all_articles_content(today)
    if all_articles_content.strip():
        print(f"已收集文章内容，总长度：{len(all_articles_content)}字符")
        if generate_advice:
            investment_advice = generate_investment_advice(all_articles_content, today)
            print("投资分析建议生成完成！")
            return investment_advice
        print("跳过投资建议生成（generate_advice=False）")
        return None
    else:
        print("未找到任何文章内容，跳过投资建议生成")
        return None


if __name__ == "__main__":
    # 用法: python wechat_weread.py [login|run]
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "login":
        login_weread()
    else:
        run_wechat_task()
