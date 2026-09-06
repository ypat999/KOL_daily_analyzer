# -*- coding: utf-8 -*-
"""微信读书桥接：用普通微信号抓取公众号文章（替代 mp 后台 appmsg 接口）

原理：个人微信扫码登录"微信读书"web 端（weread.qq.com），官方接口
/web/mp/articles 按公众号 bookId（MP_WXS_xxx）拉取文章列表，
正文优先用真实 Chrome 打开 mp.weixin.qq.com/s/ 抓取（网页优先，requests 兜底）。
与 mp 后台的 appmsg freq control 完全隔离。
此前依赖的 wewe-rss 中转平台 weread.111965.xyz 已下线，不再使用。

前置要求：
- 待抓公众号必须先在该微信读书账号中「关注」（手机 App：书架 → 公众号 → 添加，
  且该公众号须先在微信中关注过）。未订阅时文章接口返回 -2041（无权限），
  网页端同一接口同样失败，非登录/抓取方式问题，无法用浏览器绕过。
- -2041 的另一种情形：bookId 已在书架仍短暂返回 -2041，属瞬时风控
  （集中请求/登录风暴后触发，静置可自动恢复）——程序按书架订阅列表区分这两种情况，
  未订阅才跳过，已订阅则等待退避重试。
- 凭据：运行 weread_web_login.py 扫码登录一次，cookie 保存到 weread_web_cookies.json 长期复用。
- mp id：公众号名 → bookId（MP_WXS_xxx）缓存在 weread_mpids.json，需手动维护
  （平台 wxs2mp 解析接口已下线，新公众号把 id 手动加入该文件）。

配置文件 wechat_weread_accounts.json:
{
  "accounts": [
    {"name": "公众号名", "example_link": "https://mp.weixin.qq.com/s/xxx"}
  ]
}
关注状态核对：python check_weread_follows.py（列出哪些账号仍未关注/可正常拉取）。
"""
import json, os, re, sys, time, random
import requests
from datetime import datetime

from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend
from deepseek_summary import deepseek_summary
from prediction_recorder import record_predictions_from_advice
from stage_timer import stage, timed

# 微信读书官方 web 端直连（替代 wewe-rss 中转平台，平台 weread.111965.xyz 已下线）
# 凭据：运行 weread_web_login.py 扫码登录一次，cookie 保存到 weread_web_cookies.json 长期复用
WEB_COOKIE_FILE = "weread_web_cookies.json"
WEREAD_BASE = "https://weread.qq.com"

# ===== 微信任务总开关（微信读书桥接版）=====
# 新链路基于普通微信号+微信读书平台，与 mp 后台 appmsg 接口(freq control)完全隔离。
# 不需要 mp 后台 cookie。改为 False 可整体禁用微信任务。
WECHAT_ENABLED = True

AUTH_FILE = "weread_auth.json"          # 扫码登录凭据 {vid, token, username}
MPID_CACHE_FILE = "weread_mpids.json"   # 公众号名 -> mp id 缓存
CONFIG_FILE = "wechat_weread_accounts.json"
TIMEOUT = 30
LIMIT_HOURS = 18  # 平时限定 18 小时（与 wechat_get.py 一致）
# 每页 20 篇（官方接口固定），MAX_PAGES 覆盖当日文章即可
MAX_PAGES = 2

# 进程内只自动重登一次（每次 run_wechat_task 开始重置），避免循环弹扫码窗
_AUTO_RELOGINED = False
# 已确认「未关注/无权限」(-2041) 的 mp_id 集合：本轮跳过该账号，不进重试轮（关注前重试无意义）
_DENIED_MP_IDS = set()


def _is_login_error(data):
    """判断微信读书接口错误是否因登录失效（-2010 登录超时/未登录）。

    -2041 为「公众号未关注/无权限」，非登录失效，由 get_mp_articles 单独处理。
    """
    code = data.get("errCode")
    msg = str(data.get("errMsg", "") or "")
    return code in (-2010,) or any(k in msg for k in ("登录", "超时", "未登录", "登录态"))


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
    """微信读书官方接口请求（带 web 登录 cookie）"""
    cookies = load_web_cookies()
    if not cookies:
        return None
    jar = {c["name"]: c["value"] for c in cookies}
    url = f"{WEREAD_BASE}{path}"
    hdr = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "Referer": "https://weread.qq.com/",
    }
    if headers:
        hdr.update(headers)
    try:
        r = requests.request(method, url, timeout=timeout, headers=hdr, cookies=jar, **kwargs)
        return r
    except Exception as e:
        print(f"  微信读书请求异常: {e}")
        return None


def load_web_cookies():
    """读取微信读书 web 端登录 cookie（weread_web_login.py 扫码生成）"""
    if not os.path.exists(WEB_COOKIE_FILE):
        return None
    with open(WEB_COOKIE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def login_weread():
    """扫码登录微信读书 web 端（替代已下线的 wewe-rss 平台扫码）

    调用 weread_web_login.py 弹出 Chrome 窗口扫码，成功后 cookie 保存到 weread_web_cookies.json。
    """
    print("开始微信读书 web 端扫码登录（弹出 Chrome 窗口，请用手机微信扫码）...")
    try:
        import subprocess
        subprocess.run([sys.executable, "weread_web_login.py"], check=True)
    except Exception as e:
        print(f"扫码登录流程异常: {e}")
        return None
    cookies = load_web_cookies()
    if not cookies:
        print("扫码登录后未获得凭据")
        return None
    vid = next((c.get("value", "") for c in cookies if c.get("name") == "wr_vid"), "")
    # wr_skey 即微信读书 web 端凭据 token（wr_token 旧链路的等价物），校验逻辑依赖它非空
    wr_skey = next((c.get("value", "") for c in cookies if c.get("name") == "wr_skey"), "")
    auth = {"vid": vid, "token": wr_skey, "username": wr_skey[:6]}
    save_auth(auth)
    print(f"登录成功 (vid={vid})")
    return auth


def get_mp_id(auth, account_name, example_link):
    """公众号 mp id：优先使用 weread_mpids.json 缓存

    平台 wxs2mp 解析接口已随中转平台下线；新公众号需手动把 id 加入 weread_mpids.json。
    """
    cache = {}
    if os.path.exists(MPID_CACHE_FILE):
        with open(MPID_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    if account_name in cache:
        return cache[account_name]
    print(f"  {account_name} 无 mp id 缓存（wxs2mp 平台已下线），请手动补充 {MPID_CACHE_FILE}")
    return None


def get_mp_articles(auth, mp_id, page=1, max_retries=2):
    """拉取公众号文章列表 [{id, title, picUrl, publishTime}]

    直连微信读书官方 web 接口 /web/mp/articles（cookie 认证，见 _platform_request）。
    响应结构: reviews[].subReviews[].review.mpInfo -> {originalId, title, pic_url, time}
    分页: offset = (page-1) * 20，每页最多 20 篇。
    未登录/无 cookie 时返回 HTTP 200 + errCode(-2010)，须按 body 判断。
    """
    global _AUTO_RELOGINED
    offset = (page - 1) * 20
    for attempt in range(max_retries):
        r = _platform_request("GET", "/web/mp/articles",
                              params={"bookId": mp_id, "offset": offset})
        if r is not None and r.status_code == 200:
            try:
                data = r.json()
                if data.get("errCode"):
                    err = data.get("errMsg") or data.get("errCode")
                    # -2041：文章接口无权限，两种成因——
                    #  a) bookId 不在书架：公众号真未订阅（web 端无关注入口，需手机 App 添加），跳过
                    #  b) bookId 已订阅仍 -2041：瞬时风控（登录风暴/集中请求后触发，静置可恢复），退避重试
                    # 以书架订阅列表区分，避免把已订阅号误判为"永久未关注"而整轮跳过。
                    if data.get("errCode") == -2041:
                        if _is_unsubscribed(mp_id):
                            _DENIED_MP_IDS.add(mp_id)
                            print("  接口返回错误: -2041（该公众号未在微信读书书架中订阅/收录："
                                  "请先在微信读书 App 关注；核对: python check_weread_follows.py）")
                            return []
                        print(f"  接口返回错误: -2041（已订阅仍限频），{20 * (attempt + 1)}秒后重试"
                              f"({attempt + 2}/{max_retries})...")
                        if attempt < max_retries - 1:
                            time.sleep(20 * (attempt + 1))
                            continue
                        return []
                    # 登录中途失效（开头 verify_auth 通过、跑到一半 cookie 过期）：
                    # 自动重新扫码登录一次，用新凭据重试当前页
                    if _is_login_error(data) and not _AUTO_RELOGINED:
                        _AUTO_RELOGINED = True
                        print(f"  接口返回登录失效({err})，自动重新扫码登录一次...")
                        login_weread()
                        if load_web_cookies():
                            print("  已更新登录凭据，重试当前请求...")
                            continue
                    print(f"  接口返回错误: {err}")
                    return []
                result = []
                for rev in data.get("reviews") or []:
                    for sub in rev.get("subReviews") or []:
                        mp = (sub.get("review") or {}).get("mpInfo") or {}
                        if not mp:
                            continue
                        result.append({
                            "id": mp.get("originalId", ""),
                            "title": mp.get("title", ""),
                            "picUrl": mp.get("pic_url", ""),
                            "publishTime": mp.get("time", rev.get("createTime", 0)),
                        })
                if result:
                    return result
                # 空结果：可能被限流，等待后重试
                if attempt < max_retries - 1:
                    print(f"    返回空列表（可能限流），{5*(attempt+1)}秒后重试({attempt+2}/{max_retries})...")
                    time.sleep(5 * (attempt + 1))
            except Exception as e:
                print(f"  文章列表解析异常: {e}")
                return []
        else:
            # 网络层失败（None/非200）重试 1 次、短等待即可
            print(f"  拉取文章列表失败: HTTP {r.status_code if r else 'None'}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
    return []


def _pick_probe_mp_id():
    """取任一已配置公众号 bookId 用于启动鉴权探测（严格校验需要真实公众号请求）"""
    try:
        if os.path.exists(MPID_CACHE_FILE):
            with open(MPID_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            for v in cache.values():
                if isinstance(v, str) and v.startswith("MP_"):
                    return v
    except Exception:
        pass
    return None


# 书架公众号订阅缓存：{ts, ids}，区分 -2041 是「真未订阅」还是「已订阅但瞬时风控」
_SUBSCRIBED_CACHE = {"ts": 0.0, "ids": None}


def _fetch_subscribed_mp_ids():
    """拉取当前账号书架中的公众号订阅 bookId 集合（/web/shelf/sync，type=3），300秒缓存"""
    now = time.time()
    cache = _SUBSCRIBED_CACHE
    if cache["ids"] is not None and now - cache["ts"] < 300:
        return cache["ids"]
    ids = None
    try:
        r = _platform_request("GET", "/web/shelf/sync",
                              params={"synckey": 0, "listType": 1}, timeout=15)
        if r is not None and r.status_code == 200:
            d = r.json()
            ids = {b.get("bookId") for b in (d.get("books") or []) if b.get("type") == 3}
    except Exception:
        pass
    if ids is not None:
        cache["ids"], cache["ts"] = ids, now
    return ids


def _is_unsubscribed(mp_id):
    """mp_id 是否确认为「未订阅」。书架拉取失败时保守返回 False（按已订阅处理，避免误跳过）"""
    subscribed = _fetch_subscribed_mp_ids()
    return subscribed is not None and mp_id not in subscribed


def verify_auth(auth):
    """校验微信读书 web 登录 cookie 是否有效

    探测接口与抓取同源同强度（/web/mp/articles，严格校验 wr_skey）：
    未登录/登录失效返回 errCode=-2010（errMsg 登录超时/未登录），判定无效；
    -2041（已登录但探测号未关注）与成功列表同样证明登录态有效。
    旧实现走 /api/user/notify 只是"半有效"探测——会话 cookie 在但 wr_skey
    已过期时它仍返回 success=1，导致"开始检测有效、跑一半才报登录失效"。

    Returns:
        bool: True 有效 / False cookie 失效 / None 网络异常无法判断
    """
    mp_id = _pick_probe_mp_id()
    if mp_id:
        r = _platform_request("GET", "/web/mp/articles",
                              params={"bookId": mp_id, "offset": 0}, timeout=15)
        if r is None:
            return None
        try:
            data = r.json()
            if data.get("success") == 1 or "reviews" in data:
                return True
            err = data.get("errCode")
            if err == -2041:  # 登录态有效，只是该探测号未关注
                return True
            if err and _is_login_error(data):
                return False
            # 其它业务错误（参数类）也算登录态有效
            return err is None or err == 0
        except Exception:
            return False
    # 无可用 bookId 时退化为 notify 半有效检测
    r = _platform_request("GET", "/api/user/notify", timeout=15)
    if r is None:
        return None
    try:
        return r.json().get("success") == 1
    except Exception:
        return False


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
    """从 mp.weixin.qq.com/s/ 抓取文章正文纯文本（requests 兜底通道）"""
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


def _create_content_browser():
    """创建抓正文用的真实 Chrome（复用 bili 反爬配置；懒加载避免拖慢纯 API 流程）"""
    try:
        from bili_summary import setup_browser
        return setup_browser()
    except Exception as e:
        print(f"  创建浏览器失败: {e}")
        return None


def _fetch_article_content_web(url, driver):
    """网页(浏览器)优先抓正文：真实 Chrome 打开 mp.weixin.qq.com/s/ 读 js_content。

    返回 None 时由调用方回退 requests 通道（页面可能触发微信安全校验/验证码/无正文）。
    """
    import random as _random
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    try:
        time.sleep(_random.uniform(2, 4))
        driver.get(url)
        try:
            content_el = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#js_content")))
        except Exception:
            # 区分：微信安全校验页 vs 真无正文
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text[:200]
            except Exception:
                body_text = ""
            if any(k in body_text for k in ("环境异常", "验证", "防骚扰", "去验证")):
                print("  页面触发微信安全校验，改走 requests 兜底")
            else:
                print("  页面未渲染出正文节点 js_content")
            return None
        content = content_el.text.strip()
        if len(content) < 20:
            print("  网页正文过短，视为未渲染完整，改走 requests 兜底")
            return None
        return re.sub(r'\s+', ' ', content).strip()
    except Exception as e:
        print(f"  浏览器抓正文异常: {e}")
        return None


def save_single_article_content(account_name, art, today, driver=None, prefer_web=True):
    """抓取单篇正文并落库（网页优先 → requests 兜底）。返回是否保存成功。"""
    url = f"https://mp.weixin.qq.com/s/{art.get('id')}"
    title = art.get('title', '无标题')
    print(f"    抓取正文: {title}")
    content = None
    if prefer_web and driver is not None:
        content = timed(f"微信-正文(网页) {str(title)[:12]}", _fetch_article_content_web, url, driver,
                        group="微信-正文抓取")
        if not content:
            print(f"    网页方式未取到正文，requests 兜底")
    if not content:
        content = timed(f"微信-正文 {str(title)[:12]}", fetch_article_content, url,
                        group="微信-正文抓取")
    if content:
        save_single_article(account_name, art, content, today)
        return True
    print(f"    正文获取失败: {title}")
    return False


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


def run_wechat_task(generate_advice=True, prefer_web_content=True):
    """运行微信公众号文章分析任务（微信读书桥接版）

    Args:
        generate_advice: 是否生成投资建议
        prefer_web_content: 正文抓取优先网页(真实Chrome)，requests 仅兜底。
            说明：公众号文章列表接口(网页端与直连为同一 /web/mp/articles)，-2041 为
            「未关注/无权限」的账号级错误，浏览器无法绕过，需先在微信读书 App 关注。
    """
    global _AUTO_RELOGINED
    _AUTO_RELOGINED = False  # 每轮任务重置"已自动重登"标记
    _DENIED_MP_IDS.clear()  # 每轮任务重置"未关注/无权限"记录（关注后重跑即可被清除）

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

    driver = None  # 正文网页抓取复用浏览器（懒创建）

    def ensure_driver():
        """按需创建正文抓取浏览器，失败时正文自动全走 requests"""
        nonlocal driver
        if driver is None:
            print("准备正文网页抓取浏览器...")
            driver = _create_content_browser()
            if driver is None:
                print("浏览器创建失败，正文将全部走 requests 兜底")
        return driver

    for idx, acc in enumerate(accounts):
        name = acc.get("name", "")
        link = acc.get("example_link", "")
        if not name or not link:
            continue
        # 账号间延迟，避免连续请求触发平台限流
        if idx > 0:
            delay = random.uniform(5, 10)
            print(f"  账号间等待 {delay:.1f}秒...")
            with stage(f"微信-账号间限流等待 {name}", group="微信-限流等待"):
                time.sleep(delay)
        print(f"\n处理公众号: {name}")
        got = 0
        try:
            mp_id = timed(f"微信-账号定位 {name}", get_mp_id, auth, name, link,
                          group="微信-账号定位(bizid)")
            if not mp_id:
                continue
            today_articles = []
            for page in range(1, MAX_PAGES + 1):
                arts = timed(f"微信-文章列表 {name} p{page}", get_mp_articles, auth, mp_id, page=page,
                             group="微信-文章列表翻页")
                if not arts:
                    break
                page_today = [a for a in arts if is_today_article_ts(a.get("publishTime", 0))]
                today_articles.extend(page_today)
                if len(page_today) < len(arts):
                    break  # 本页已含非今日文章，无需继续翻页
                with stage(f"微信-翻页限流等待 {name}", group="微信-限流等待"):
                    time.sleep(random.uniform(2, 4))
            got = len(today_articles)
            print(f"  {name} 获取到 {got} 篇限定时间内文章")
            for art in today_articles:
                try:
                    if prefer_web_content:
                        ensure_driver()
                    if save_single_article_content(name, art, today, driver=driver,
                                                   prefer_web=prefer_web_content):
                        total_saved += 1
                except Exception as e:
                    print(f"    处理文章异常: {e}")
                    continue
        except Exception as e:
            print(f"  处理 {name} 异常: {e}")
        if got == 0:
            failed_accounts.append(acc)

    # 末轮重试：首轮未获取到文章的账号（-2041 未关注的已在 get_mp_articles 中记录并跳过）
    if failed_accounts:
        print(f"\n首轮有 {len(failed_accounts)} 个账号未获取到文章，等待60秒后统一重试...")
        with stage("微信-重试轮等待60秒", group="微信-限流等待"):
            time.sleep(60)
        if prefer_web_content:
            ensure_driver()
        for acc in failed_accounts:
            name = acc.get("name", "")
            link = acc.get("example_link", "")
            print(f"\n重试公众号: {name}")
            with stage(f"微信-重试间隔等待 {name}", group="微信-限流等待"):
                time.sleep(random.uniform(5, 10))
            try:
                mp_id = get_mp_id(auth, name, link)
                if not mp_id:
                    continue
                if mp_id in _DENIED_MP_IDS:
                    print(f"  {name} 已确认为未关注(-2041)，跳过重试（请在微信读书 App 关注后再跑）")
                    continue
                got = 0
                for page in range(1, MAX_PAGES + 1):
                    arts = get_mp_articles(auth, mp_id, page=page)
                    if not arts:
                        break
                    page_today = [a for a in arts if is_today_article_ts(a.get("publishTime", 0))]
                    for art in page_today:
                        if save_single_article_content(name, art, today, driver=driver,
                                                       prefer_web=prefer_web_content):
                            total_saved += 1
                            got += 1
                    if len(page_today) < len(arts):
                        break
                print(f"  {name} 重试后获取到 {got} 篇")
            except Exception as e:
                print(f"  {name} 重试异常: {e}")

    if driver is not None:
        try:
            driver.quit()
            print("已关闭正文抓取浏览器")
        except Exception:
            pass

    print(f"\n共保存 {total_saved} 篇今日文章内容")

    all_articles_content = collect_all_articles_content(today)
    if all_articles_content.strip():
        print(f"已收集文章内容，总长度：{len(all_articles_content)}字符")
        if generate_advice:
            investment_advice = timed("微信-投资建议LLM", generate_investment_advice,
                                      all_articles_content, today, group="平台-投资建议LLM")
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
