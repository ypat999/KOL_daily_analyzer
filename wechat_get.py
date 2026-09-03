import requests, math, time, random, json, os, re
from datetime import datetime, date, timedelta
from tqdm import tqdm
from deepseek_summary import deepseek_summary

from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend
from prediction_recorder import record_predictions_from_advice
# 导入自动登录模块
try:
    from wechat_login import update_wechat_cookie, check_cookie_validity
    HAS_WECHAT_LOGIN = True
except ImportError:
    print("警告: 未找到wechat_login模块，将无法自动更新微信cookie")
    HAS_WECHAT_LOGIN = False

LIMIT_HOURS = 18  # 平时限定小时内（18小时），周末只收录周五收盘后发布的内容

# 公众号fakeid列表
account_list = {
    "MzI1NzAwNzY4OQ%3D%3D": "财经旗舰",
    "Mzg2MDc2NzQ3MQ%3D%3D": "表舅是养基大户",
    "MzUxNzE3NzI0NA%3D%3D": "华尔街情报圈",
    "MzIyODU5NTU5Mg%3D%3D": "知识旅行家",
    "MzU4NTkwMDY5MQ%3D%3D": "炒股拌饭",
    "MzU1MDk3Njc3NA%3D%3D": "韭圈儿",
    "MzU4OTg2NTY0OA%3D%3D": "路透财经早报",
    "MzE5ODk2NjUwOA%3D%3D": "猫笔刀",
    "Mzg4NzUxNjgyMQ%3D%3D": "章叔论市",
    "Mzg4MzY5NDY4OA%3D%3D": "韭研公社",
    "MzAwNzA0MTkxOQ%3D%3D": "集思录",
    "MzAwNjY4MjQwMA%3D%3D": "安静拆主线"
}

# 目标url
url = "https://mp.weixin.qq.com/cgi-bin/appmsg"

def load_cookie_from_file():
    """从wechat_cookies.json文件以JSON格式读取cookie和token值"""
    try:
        with open("wechat_cookies.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            if not data:
                print("警告: wechat_cookies.json文件为空")
                return "", ""
            cookie = data.get("cookie", "")
            token = data.get("token", "")
            return cookie, token
    except FileNotFoundError:
        print("错误: 未找到wechat_cookies.json文件")
        return "", ""
    except json.JSONDecodeError:
        print("错误: wechat_cookies.json文件格式不是有效的JSON")
        return "", ""
    except Exception as e:
        print(f"读取cookie文件时出错: {e}")
        return "", ""

# 从文件读取cookie和token
cookie, token = load_cookie_from_file()

def check_and_update_cookie():
    """检查cookie有效性，如果失效则尝试更新
    
    统一登录阶段可能已更新cookie文件，先重新读取文件
    """
    global cookie, token, headers, data
    
    # 先重新从文件读取cookie（统一登录阶段可能已更新）
    new_cookie, new_token = load_cookie_from_file()
    if new_cookie and new_token:
        cookie = new_cookie
        token = new_token
        headers["Cookie"] = cookie
        data["token"] = token
    
    # 检查cookie是否有效
    if HAS_WECHAT_LOGIN and 'check_cookie_validity' in globals():
        if not check_cookie_validity(cookie, token):
            print("检测到微信cookie已失效，正在尝试自动更新...")
            
            # 尝试自动更新cookie
            new_cookie, new_token = update_wechat_cookie()
            
            if new_cookie and new_token:
                print("微信cookie更新成功!")
                cookie = new_cookie
                token = new_token
                
                # 更新全局变量
                headers["Cookie"] = cookie
                data["token"] = token
                
                # 保存到文件
                try:
                    cookie_data = {
                        "cookie": cookie,
                        "token": token
                    }
                    with open("wechat_cookies.json", "w", encoding="utf-8") as f:
                        json.dump(cookie_data, f, ensure_ascii=False, indent=4)
                    print("新的cookie和token已保存到 wechat_cookies.json 文件")
                except Exception as e:
                    print(f"保存cookie到文件时出错: {e}")
                
                return True
            else:
                print("微信cookie更新失败，请手动更新wechat_cookies.json文件")
                return False
        else:
            print("微信cookie有效")
            return True
    else:
        # 如果没有自动登录模块，检查cookie是否为空
        if not cookie or not token:
            print("错误: 微信cookie或token为空，请运行微信登录程序或手动更新wechat_cookies.json文件")
            return False
        print("无法自动检查cookie有效性（缺少检查函数），请确保cookie有效")
        return True

headers = {
    "Cookie": cookie,
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/77.0.3865.75 Mobile Safari/537.36",
}

data = {
    "token": token,
    "lang": "zh_CN",
    "f": "json",
    "ajax": "1",
    "action": "list_ex",
    "begin": "0",
    "count": "5",
    "query": "",
    "type": "9",
}


# 全局频率控制状态
_last_request_time = 0.0  # 上次请求时间戳
_freq_control_cooldown_until = 0.0  # freq control冷却结束时间戳
_MIN_REQUEST_INTERVAL = 5.0  # 任意两次请求间最小间隔（秒）
_FREQ_COOLDOWN_SECONDS = 90.0  # 遇到freq control后的全局冷却时间（秒）


def _throttle_request():
    """全局请求节流：确保请求间隔不小于_MIN_REQUEST_INTERVAL，若处于冷却期则等待"""
    global _last_request_time, _freq_control_cooldown_until
    now = time.time()
    # 若处于freq control冷却期，等待至冷却结束
    if now < _freq_control_cooldown_until:
        wait = _freq_control_cooldown_until - now
        print(f"  频率限制冷却中，等待{wait:.0f}秒...")
        time.sleep(wait)
    # 确保最小请求间隔
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _trigger_freq_cooldown():
    """触发freq control全局冷却"""
    global _freq_control_cooldown_until
    _freq_control_cooldown_until = time.time() + _FREQ_COOLDOWN_SECONDS


def _is_freq_control(content_json):
    """检测响应是否为freq control频率限制"""
    try:
        err_msg = content_json.get('base_resp', {}).get('err_msg', '')
        if 'freq control' in str(err_msg).lower():
            return True
    except Exception:
        pass
    return 'freq control' in str(content_json).lower()


def get_total_count(fakeid, max_retries=3):
    """获取公众号文章总数，遇到freq control自动重试并触发全局冷却"""
    data["fakeid"] = fakeid
    for attempt in range(max_retries):
        _throttle_request()
        content_json = requests.get(url, headers=headers, params=data).json()
        if "app_msg_cnt" in content_json:
            return int(content_json["app_msg_cnt"])
        if _is_freq_control(content_json):
            print(f"获取{fakeid}总数遇到频率限制，触发全局冷却并重试({attempt+1}/{max_retries})...")
            _trigger_freq_cooldown()
            continue
        err_msg = ""
        try:
            err_msg = content_json.get('base_resp', {}).get('err_msg', '')
        except Exception:
            pass
        print(f"获取{fakeid}总数失败: {content_json}")
        raise Exception([err_msg or content_json])
    print(f"获取{fakeid}总数失败: 多次重试后仍被频率限制")
    raise Exception(["freq control: 重试耗尽"])


def is_today_article(article):
    """检查文章是否为限定时间内发布（周末只收录周五收盘后发布的内容）"""
    try:
        # 从文章信息中获取发布时间戳
        create_time = article.get("create_time", 0)
        if create_time:
            # 将时间戳转换为datetime对象
            article_datetime = datetime.fromtimestamp(create_time)
            now = datetime.now()
            
            # 检查是否为周末
            weekday = now.weekday()  # 0=周一, 6=周日
            is_weekend = weekday >= 5  # 5=周六, 6=周日
            
            if is_weekend:
                # 使用date_utils模块的函数计算最近的周五日期
                friday_date = get_friday_date_for_weekend(now)
                # 设置周五收盘时间为15:00
                friday_close_time = friday_date.replace(hour=15, minute=0, second=0, microsecond=0)
                # 只收录周五收盘后发布的内容
                return article_datetime >= friday_close_time
            else:
                # 平时使用18小时限制
                time_diff = (now - article_datetime).total_seconds() / 3600
                return time_diff <= LIMIT_HOURS
        return False
    except:
        return False


def get_content_list(fakeid, account_name, per_page=5):
    """获取指定公众号的文章列表，按时间顺序，遇到非当日文章即停止"""
    # 检查并更新cookie
    if not check_and_update_cookie():
        print("无法获取有效的微信cookie，程序将退出")
        return []
    
    data["fakeid"] = fakeid
    count = get_total_count(fakeid)
    page = int(math.ceil(count / per_page))
    content_list = []
    
    # 使用统一的日期工具获取当前分析日期
    today, date_reason, archive_folder = get_current_analysis_date()
    print_date_info()
    
    print(f"开始获取公众号 '{account_name}' 的文章列表...")
    
    for i in tqdm(range(page), desc=f"获取{account_name}文章"):
        data["begin"] = i * per_page
        try:
            # 翻页请求使用全局节流和freq control冷却重试
            content_json = None
            for retry in range(3):
                _throttle_request()
                content_json = requests.get(url, headers=headers, params=data).json()
                if "app_msg_list" in content_json:
                    break
                if _is_freq_control(content_json):
                    print(f"  第{i+1}页遇到频率限制，触发全局冷却并重试({retry+1}/3)...")
                    _trigger_freq_cooldown()
                    continue
                break
            if content_json is None or "app_msg_list" not in content_json:
                print(f"  第{i+1}页未获取到文章列表，跳过")
                continue
            articles = content_json["app_msg_list"]
            
            # 检查本页是否有限定时间内发布的文章
            today_articles = [article for article in articles if is_today_article(article)]
            
            if today_articles:
                # 有今日文章，逐篇获取完整内容并保存
                for article in today_articles:
                    title = article.get('title', '无标题')
                    article_url = article.get('link', '')
                    
                    # 检查文章是否已经保存过
                    if is_article_saved(account_name, title, today):
                        print(f"  文章已存在，跳过: {title}")
                        continue
                    
                    # 获取文章完整内容
                    print(f"  正在获取文章内容: {title}")
                    content = get_article_content(article_url, title)
                    
                    if content:
                        # 保存单篇文章
                        save_single_article(account_name, article, content, today)
                        # 添加到结果列表
                        content_list.append(article)
                    else:
                        print(f"  获取文章内容失败: {title}")
                
                print(f"  第{i+1}页处理了 {len(today_articles)} 篇限定时间内文章")
            else:
                # 本页没有今日文章，说明后面的文章都是更旧的，可以停止了
                if articles:  # 确保确实获取到了文章列表
                    print(f"  第{i+1}页无限定时间内文章，停止获取")
                    break
                else:
                    # 如果没有获取到文章，继续下一页
                    continue
                    
        except Exception as e:
            print(f"获取{account_name}第{i+1}页文章时出错: {e}")
            continue
        
        # 请求后添加更随机的延迟
        # 15%概率添加较长延迟（模拟人类思考或休息）
        if random.random() < 0.15:
            delay = random.uniform(8, 15)
            print(f"  添加较长延迟: {delay:.1f}秒")
            time.sleep(delay)
        else:
            # 正常延迟：6-10秒（已由全局节流保底5秒，此处补充额外间隔）
            delay = random.uniform(6, 10)
            time.sleep(delay)
    
    print(f"  {account_name} 总共获取并保存了 {len(content_list)} 篇限定时间内文章")
    return content_list


def clean_html_content(html_content):
    """清理HTML内容，提取纯文本"""
    try:
        # 移除script和style标签及其内容
        html_content = re.sub(r'<script.*?>.*?</script>', '', html_content, flags=re.DOTALL)
        html_content = re.sub(r'<style.*?>.*?</style>', '', html_content, flags=re.DOTALL)
        
        # 移除HTML注释
        html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)
        
        # 移除所有HTML标签
        html_content = re.sub(r'<[^>]+>', '', html_content)
        
        # 处理HTML实体
        html_content = html_content.replace('&nbsp;', ' ')
        html_content = html_content.replace('&lt;', '<')
        html_content = html_content.replace('&gt;', '>')
        html_content = html_content.replace('&amp;', '&')
        html_content = html_content.replace('&quot;', '"')
        html_content = html_content.replace('&#39;', "'")
        
        # 清理多余的空白字符
        html_content = re.sub(r'\s+', ' ', html_content)
        html_content = html_content.strip()
        
        # 按段落重新组织
        paragraphs = [p.strip() for p in html_content.split('\n') if p.strip()]
        clean_text = '\n\n'.join(paragraphs)
        
        return clean_text
    except Exception as e:
        print(f"清理HTML内容时出错: {e}")
        return html_content  # 如果清理失败，返回原始内容


def get_article_content(article_url, title):
    """从文章链接获取完整内容"""
    try:
        # 添加随机延迟防止被封
        time.sleep(random.uniform(2, 4))
        
        response = requests.get(article_url, headers=headers, timeout=30)
        response.encoding = 'utf-8'
        
        if response.status_code == 200:
            html_content = response.text
            # 清理HTML内容，提取纯文本
            clean_content = clean_html_content(html_content)
            return clean_content
        else:
            print(f"获取文章内容失败，状态码: {response.status_code}")
            return None
    except Exception as e:
        print(f"获取文章内容时出错: {e}")
        return None


def is_article_saved(account_name, title, today):
    """检查文章是否已经保存过"""
    # 确保使用传入的today参数作为归档目录名
    archive_dir = f"archive_{today}"
    account_filename = f"{archive_dir}/wechat_{account_name}_{today}.txt"
    
    if not os.path.exists(account_filename):
        return False
    
    try:
        with open(account_filename, "r", encoding="utf-8") as f:
            content = f.read()
            # 检查文章标题是否已存在
            return f"标题: {title}" in content
    except Exception as e:
        print(f"检查文章是否存在时出错: {e}")
        return False


def save_single_article(account_name, article, content, today):
    """保存单篇文章内容"""
    # 确保使用传入的today参数作为归档目录名
    archive_dir = f"archive_{today}"
    if not os.path.exists(archive_dir):
        os.makedirs(archive_dir)
    
    account_filename = f"{archive_dir}/wechat_{account_name}_{today}.txt"
    
    # 追加模式写入文章
    with open(account_filename, "a", encoding="utf-8") as f:
        f.write(f"文章:\n")
        f.write(f"标题: {article.get('title', '无标题')}\n")
        f.write(f"链接: {article.get('link', '')}\n")
        f.write(f"发布时间: {datetime.fromtimestamp(article.get('create_time', 0)).strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("-"*30 + "\n")
        f.write(f"内容摘要: {article.get('digest', '无摘要')}\n")
        f.write("="*30 + "\n")
        f.write("完整内容:\n")
        f.write(content)
        f.write("\n" + "="*80 + "\n\n")
    
    # 输出文章保存信息，包含URL
    title = article.get('title', '无标题')
    url = article.get('link', '')
    print(f"  已保存文章: {title}")
    print(f"  文章URL: {url}")
    
    # 提取并保存该公众号文章的预测观点
    if content and len(content.strip()) > 50:
        record_predictions_from_advice(content, "wechat", account_name, today, f"archive_{today}")


def save_daily_content(all_content):
    """保存当日所有公众号文章内容"""
    # 使用统一的日期工具获取当前分析日期
    today, date_reason, archive_folder = get_current_analysis_date()
    print_date_info()
    
    filename = f"daily_content_{today}.json"
    
    # 保存完整内容（仅包含文章列表信息）
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_content, f, ensure_ascii=False, indent=4)
    
    print(f"文章列表信息已保存到 {filename}")
    print(f"完整文章内容已保存到 archive_{today} 目录")


def get_all_accounts_daily_content():
    """获取所有公众号的当日文章内容
    
    遇到freq control失败的账号会在首轮结束后重试，避免单个账号阻塞整个流程
    """
    all_content = {}
    # 随机化账号处理顺序，避免固定模式触发频率检测
    accounts = list(account_list.items())
    random.shuffle(accounts)
    
    failed_accounts = []  # 记录首轮失败的账号，用于后续重试
    
    for fakeid, account_name in accounts:
        print(f"\n{'='*60}")
        print(f"正在处理公众号: {account_name}")
        print(f"{'='*60}")
        
        try:
            articles = get_content_list(fakeid, account_name)
            all_content[fakeid] = {
                "account_name": account_name,
                "articles": articles,
                "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            print(f"{account_name} 获取到 {len(articles)} 篇今日文章")
        except Exception as e:
            err_str = str(e)
            print(f"获取{account_name}文章时出错: {err_str}")
            all_content[fakeid] = {
                "account_name": account_name,
                "articles": [],
                "error": err_str,
                "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            # 记录失败账号用于重试（freq control或重试耗尽类错误）
            if 'freq control' in err_str.lower() or '重试耗尽' in err_str:
                failed_accounts.append((fakeid, account_name))
        
        # 公众号间添加更随机的延迟
        # 20%概率添加较长延迟（模拟切换账号时的操作间隔）
        if random.random() < 0.2:
            delay = random.uniform(15, 25)
            print(f"  公众号切换，添加较长延迟: {delay:.1f}秒")
            time.sleep(delay)
        else:
            # 正常切换延迟：10-15秒（由全局节流保底，此处延长账号切换间隔）
            delay = random.uniform(10, 15)
            time.sleep(delay)
    
    # 重试首轮因freq control失败的账号
    if failed_accounts:
        print(f"\n{'='*60}")
        print(f"首轮有{len(failed_accounts)}个账号因频率限制失败，等待60秒后重试...")
        print(f"{'='*60}")
        time.sleep(60)
        # 重置全局冷却状态，给重试一个干净的环境
        global _freq_control_cooldown_until
        _freq_control_cooldown_until = 0.0
        
        for fakeid, account_name in failed_accounts:
            print(f"\n重试公众号: {account_name}")
            try:
                articles = get_content_list(fakeid, account_name)
                if articles:
                    all_content[fakeid] = {
                        "account_name": account_name,
                        "articles": articles,
                        "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "retried": True
                    }
                    print(f"{account_name} 重试成功，获取到 {len(articles)} 篇今日文章")
                else:
                    print(f"{account_name} 重试后仍无文章")
            except Exception as e:
                print(f"{account_name} 重试仍失败: {e}")
            
            # 重试账号间也加延迟
            time.sleep(random.uniform(12, 18))
    
    return all_content


def collect_all_articles_content(today):
    """收集当日所有公众号文章内容"""
    # 确保使用传入的today参数作为归档目录名
    archive_dir = f"archive_{today}"
    all_articles_content = []
    
    if not os.path.exists(archive_dir):
        print(f"存档目录不存在: {archive_dir}")
        return ""
    
    # 遍历存档目录中的所有文件
    for filename in os.listdir(archive_dir):
        if filename.startswith('wechat_') and filename.endswith('.txt') and not filename.endswith('_summary.txt'):
            filepath = os.path.join(archive_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 提取公众号名称和文章标题
                    account_name = filename.replace(f'wechat_{today}.txt', '').replace(f'_{today}.txt', '')
                    all_articles_content.append(f"=== 公众号: {account_name} ===\n{content}")
            except Exception as e:
                print(f"读取文件失败 {filepath}: {e}")
    
    return '\n\n'.join(all_articles_content)


def generate_investment_advice(all_content, today):
    """基于所有文章内容生成投资建议"""
    print("开始生成投资分析建议...")
    
    # 确保使用传入的today参数作为归档目录名
    archive_dir = f"archive_{today}"
    
    # 调用deepseek进行投资分析
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
    
    # 确保归档目录存在
    if not os.path.exists(archive_dir):
        os.makedirs(archive_dir)
    
    advice_filename = f"{archive_dir}/wechat_投资建议_{today}.txt"
    with open(advice_filename, 'w', encoding='utf-8') as f:
        f.write(f"投资分析建议 - {today}\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*50 + "\n\n")
        f.write(investment_advice)
    
    print(f"投资建议已保存到: {advice_filename}")
    
    # 提取并保存微信整体投资建议的预测观点
    record_predictions_from_advice(investment_advice, "wechat", "微信综合", today, archive_dir)
    
    return investment_advice


def run_wechat_task():
    """运行微信公众号文章分析任务"""
    print("开始获取所有公众号限定时间内文章内容...")
    all_daily_content = get_all_accounts_daily_content()
    # save_daily_content(all_daily_content)
    
    # 使用统一的日期工具获取当前分析日期
    today, date_reason, archive_folder = get_current_analysis_date()
    print_date_info()
    
    print("\n收集所有文章内容...")
    all_articles_content = collect_all_articles_content(today)
    
    if all_articles_content.strip():
        print(f"已收集文章内容，总长度：{len(all_articles_content)}字符")
        investment_advice = generate_investment_advice(all_articles_content, today)
        print("\n投资分析建议生成完成！")
        return investment_advice
    else:
        print("未找到任何文章内容，跳过投资建议生成")
        return None


if __name__ == "__main__":
    run_wechat_task()