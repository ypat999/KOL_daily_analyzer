import requests, math, time, random, json, os, re
from datetime import datetime, date
from tqdm import tqdm
from deepseek_summary import deepseek_summary

LIMIT_HOURS = 18  # 18小时内的视频

# 公众号fakeid列表
account_list = {
    "MzI1NzAwNzY4OQ%3D%3D": "财经旗舰",
    "Mzg2MDc2NzQ3MQ%3D%3D": "表舅是养基大户",
    "MzUxNzE3NzI0NA%3D%3D": "华尔街情报圈",
    "MzIyODU5NTU5Mg%3D%3D": "知识旅行家",
    "MzU4NTkwMDY5MQ%3D%3D": "炒股拌饭",
    "MzU1MDk3Njc3NA%3D%3D": "韭圈儿",
    "MzU4OTg2NTY0OA%3D%3D": "路透财经早报",
    "Mzg2NzcxMjE1NA%3D%3D": "猫笔刀",
    "Mzg4NzUxNjgyMQ%3D%3D": "章叔论市"
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


def get_total_count(fakeid):
    # 获取总数前添加随机延迟
    time.sleep(random.uniform(2, 4))
    data["fakeid"] = fakeid
    content_json = requests.get(url, headers=headers, params=data).json()
    if "app_msg_cnt" not in content_json:
        print(f"获取{fakeid}总数失败")
        raise Exception([content_json['base_resp']['err_msg']])
    count = int(content_json["app_msg_cnt"])
    return count


def is_today_article(article):
    """检查文章是否为18小时内发布"""
    try:
        # 从文章信息中获取发布时间戳
        create_time = article.get("create_time", 0)
        if create_time:
            # 将时间戳转换为datetime对象
            article_datetime = datetime.fromtimestamp(create_time)
            now = datetime.now()
            # 计算时间差（小时）
            time_diff = (now - article_datetime).total_seconds() / 3600
            return time_diff <= LIMIT_HOURS
        return False
    except:
        return False


def get_content_list(fakeid, account_name, per_page=5):
    """获取指定公众号的文章列表，按时间顺序，遇到非当日文章即停止"""
    data["fakeid"] = fakeid
    count = get_total_count(fakeid)
    page = int(math.ceil(count / per_page))
    content_list = []
    today = date.today().strftime("%Y-%m-%d")
    
    print(f"开始获取公众号 '{account_name}' 的文章列表...")
    
    for i in tqdm(range(page), desc=f"获取{account_name}文章"):
        # 请求前添加小的随机延迟
        time.sleep(random.uniform(1, 2))
        
        data["begin"] = i * per_page
        try:
            content_json = requests.get(url, headers=headers, params=data).json()
            if "app_msg_list" in content_json:
                articles = content_json["app_msg_list"]
                
                # 检查本页是否有18小时内发布的文章
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
                    
                    print(f"  第{i+1}页处理了 {len(today_articles)} 篇18小时内文章")
                else:
                    # 本页没有今日文章，说明后面的文章都是更旧的，可以停止了
                    if articles:  # 确保确实获取到了文章列表
                        print(f"  第{i+1}页无18小时内文章，停止获取")
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
            # 正常延迟：4-8秒，更人性化的范围
            delay = random.uniform(4, 8)
            time.sleep(delay)
    
    print(f"  {account_name} 总共获取并保存了 {len(content_list)} 篇18小时内文章")
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
    # 创建存档目录
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


def save_daily_content(all_content):
    """保存当日所有公众号文章内容"""
    today = date.today().strftime("%Y-%m-%d")
    filename = f"daily_content_{today}.json"
    
    # 保存完整内容（仅包含文章列表信息）
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(all_content, f, ensure_ascii=False, indent=4)
    
    print(f"文章列表信息已保存到 {filename}")
    print(f"完整文章内容已保存到 archive_{today} 目录")


def get_all_accounts_daily_content():
    """获取所有公众号的当日文章内容"""
    all_content = {}
    
    for fakeid, account_name in account_list.items():
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
            print(f"获取{account_name}文章时出错: {e}")
            all_content[fakeid] = {
                "account_name": account_name,
                "articles": [],
                "error": str(e),
                "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        
        # 公众号间添加更随机的延迟
        # 20%概率添加较长延迟（模拟切换账号时的操作间隔）
        if random.random() < 0.2:
            delay = random.uniform(15, 25)
            print(f"  公众号切换，添加较长延迟: {delay:.1f}秒")
            time.sleep(delay)
        else:
            # 正常切换延迟：8-12秒
            delay = random.uniform(8, 12)
            time.sleep(delay)
    
    return all_content


def collect_all_articles_content(today):
    """收集当日所有公众号文章内容"""
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
    
    # 调用deepseek进行投资分析
    investment_advice = deepseek_summary(
        all_content,
        sysprompt="你是一个专业的金融分析师，擅长基于多份财经市场分析报告给出投资建议。请结合宏观经济、市场情绪、行业趋势等多个维度进行分析。",
        userprompt="这些是最近18小时内各大财经公众号的文章内容，请基于以下所有文章内容，给出未来几天的投资建议，包括：\n1. 整体市场判断\n2. 重点行业/板块分析\n3. 具体投资策略\n4. 风险提示\n\n请详细分析并给出专业建议：\n\n"
    )
    
    # 保存投资建议
    archive_dir = f"archive_{today}"
    if not os.path.exists(archive_dir):
        os.makedirs(archive_dir)
    
    advice_filename = f"{archive_dir}/wechat_投资建议_{today}.txt"
    with open(advice_filename, 'w', encoding='utf-8') as f:
        f.write(f"投资分析建议 - {today}\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*50 + "\n\n")
        f.write(investment_advice)
    
    print(f"投资建议已保存到: {advice_filename}")
    return investment_advice


def run_wechat_task():
    """运行微信公众号文章分析任务"""
    print("开始获取所有公众号18小时内文章内容...")
    all_daily_content = get_all_accounts_daily_content()
    # save_daily_content(all_daily_content)
    
    # 收集所有文章内容并生成投资建议
    today = date.today().strftime('%Y-%m-%d')
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