import os
import time
import random
import re
from datetime import datetime, timedelta
import json
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from deepseek_summary import deepseek_summary
from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend

LIMIT_HOURS = 18  # 平时限定小时内（18小时），周末只收录周五收盘后发布的内容

# 用户ID列表
WEIBO_USER_IDS = ["2014433131", "2453509265"]

WEIBO_URL = "https://weibo.com/"
WEIBO_COOKIE_PATH = "weibo_cookies.json"

def setup_browser():
    """初始化浏览器"""
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    driver.set_window_size(1000, 800)
    return driver

def login_and_save_cookie(driver):
    """登录微博并保存cookie"""
    try:
        # 加载已保存的cookie
        if os.path.exists(WEIBO_COOKIE_PATH):
            driver.get(WEIBO_URL)
            time.sleep(2)
            try:
                with open(WEIBO_COOKIE_PATH, 'r', encoding='utf-8') as f:
                    cookies = json.loads(f.read())
                if cookies:
                    for cookie in cookies:
                        driver.add_cookie(cookie)
                    driver.refresh()
                    time.sleep(3)
                    print("已加载保存的cookie")
                    return True
                else:
                    print("cookie文件为空，需要重新登录")
            except Exception as e:
                print(f"加载cookie时出错: {str(e)}")
                print("需要重新登录")
        
        # 执行登录流程
        driver.get(WEIBO_URL)
        print("请在浏览器中手动登录微博...")
        print("登录完成后，请按Enter键继续...")
        input()
        
        # 等待页面加载
        time.sleep(5)
        
        # 保存新cookie（无论是否找到特定元素，只要用户确认登录完成就保存）
        try:
            cookies = driver.get_cookies()
            if cookies:
                with open(WEIBO_COOKIE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(cookies, f)
                print("登录成功，已保存新cookie")
                return True
            else:
                print("未获取到cookie，登录失败")
                return False
        except Exception as e:
            print(f"保存cookie时出错: {str(e)}")
            return False
    except Exception as e:
        print(f"登录失败: {str(e)}")
        return False

def is_within_limit_hours(publish_date: str) -> bool:
    """检查发布时间是否在限定小时内"""
    now = datetime.now()
    
    # 检查是否为周末 (周六或周日) 或周一早上9点前
    weekday = now.weekday()  # 0=周一, 6=周日
    is_weekend = weekday >= 5  # 5=周六, 6=周日
    is_monday_early = weekday == 0 and now.hour < 9  # 周一且9点前
    is_weekend_period = is_weekend or is_monday_early
    
    # 处理日期格式：'YYYY-MM-DD' 或 'MM-DD' 或 'M-D'
    date_match = re.match(r'(\d{4}-)?(\d{1,2}-\d{1,2})', publish_date)
    if date_match:
        try:
            date_part = date_match.group(2)  # 获取MM-DD部分
            
            # 如果有年份，直接使用；如果没有，使用当前年份
            if date_match.group(1):
                full_date_str = f"{date_match.group(1)}{date_part}"
                weibo_date = datetime.strptime(full_date_str, '%Y-%m-%d')
            else:
                # 只有月日，使用当前年份
                current_year = now.year
                full_date_str = f"{current_year}-{date_part}"
                weibo_date = datetime.strptime(full_date_str, '%Y-%m-%d')
                
                # 如果解析出的日期在未来，说明是去年的
                if weibo_date > now:
                    weibo_date = weibo_date.replace(year=current_year - 1)
            
            # 周末逻辑：收录周五收盘后所有内容
            if is_weekend_period:
                friday_date = get_friday_date_for_weekend(now)
                friday_close_time = friday_date.replace(hour=15, minute=0, second=0, microsecond=0)
                return weibo_date.date() >= friday_close_time.date()
            else:
                # 平时使用18小时限制
                delta = now - weibo_date
                hours_diff = delta.total_seconds() / 3600
                return hours_diff <= LIMIT_HOURS
        except ValueError:
            return False

    # 处理相对时间格式（今天、X小时前、X分钟前等）
    if is_weekend_period:
        # 周末时，收录周五15:00后发布的所有内容
        friday_date = get_friday_date_for_weekend(now)
        friday_close_time = friday_date.replace(hour=15, minute=0, second=0, microsecond=0)
        
        if "分钟" in publish_date or "今天" in publish_date:
            # "今天"或"X分钟前"发布的内容，检查当前时间是否已过周五15:00
            return now >= friday_close_time
            
        if "小时前" in publish_date:
            match = re.search(r'(\d+)小时前', publish_date)
            if match:
                hours = int(match.group(1))
                publish_time = now - timedelta(hours=hours)
                return publish_time >= friday_close_time
            return True  # 无法解析时默认包含
            
        if "昨天" in publish_date:
            # "昨天"的内容在周末总是包含（因为昨天是周五或周六）
            return True
            
        if "天前" in publish_date:
            match = re.search(r'(\d+)天前', publish_date)
            if match:
                days = int(match.group(1))
                # 周末时，1-2天前的内容也包含（可能是周五或周四）
                return days <= 2  # 包含最多2天前的内容
            return True
            
        return True  # 其他情况默认包含
    else:
        # 平时的处理逻辑（周一至周五）
        if "分钟" in publish_date:
            return True
        if "今天" in publish_date:
            return True
        if "小时前" in publish_date:
            match = re.search(r'(\d+)小时前', publish_date)
            if match:
                hours = int(match.group(1))
                return hours <= LIMIT_HOURS
            return True  # 如果无法提取小时数，默认包含
        if "昨天" in publish_date:
            return True  # 昨天的内容总是包含
        if "天前" in publish_date:
            match = re.search(r'(\d+)天前', publish_date)
            if match:
                days = int(match.group(1))
                return days <= 1  # 平时只包含昨天和今天的内容
            return False
            
    return False  # 默认不包含

def get_weibo_content(driver, user_id):
    """获取指定用户的微博内容"""
    try:
        # 访问用户主页
        user_url = f"https://weibo.com/u/{user_id}"
        driver.get(user_url)
        print(f"访问用户ID {user_id} URL：{user_url}")
        time.sleep(8)  # 增加等待时间
        
        # 尝试从页面上提取用户名
        username = f"用户{user_id}"
        try:
            # 尝试多种可能的用户名选择器
            username_selectors = [
                '.username',
                '.nickname',
                '.screen_name',
                '.profile-header .name',
                '.WB_info .name',
                '.user-name',
                '.name',
                '.m-text-cut',
                '.PCD_header .name',
                '.weibo-profile-header .name',
                '.WB_name',
                '.fn',
                '.username_txt',
                '.name_txt',
                '.nick',
                '.userinfo .name',
                '.profile .name',
                '.header .name',
                '.top .name',
                '.main-header .name',
                '.user-profile .name'
            ]
            
            print("尝试从页面提取用户名...")
            for selector in username_selectors:
                try:
                    username_elem = driver.find_element(By.CSS_SELECTOR, selector)
                    if username_elem:
                        username = username_elem.text.strip()
                        if username:
                            print(f"从页面提取到用户名: {username}")
                            break
                except:
                    continue
            
            # 如果没有找到，尝试使用XPath
            if username == f"用户{user_id}":
                print("尝试使用XPath提取用户名...")
                xpath_selectors = [
                    '//*[contains(@class, "username")]',
                    '//*[contains(@class, "nickname")]',
                    '//*[contains(@class, "screen_name")]',
                    '//*[contains(@class, "name")]',
                    '//h1',
                    '//h2',
                    '//*[text()][@class]'
                ]
                
                for xpath in xpath_selectors:
                    try:
                        username_elem = driver.find_element(By.XPATH, xpath)
                        if username_elem:
                            username = username_elem.text.strip()
                            if username and len(username) < 50:
                                print(f"从XPath提取到用户名: {username}")
                                break
                    except:
                        continue
            
            # 如果没有找到，尝试从标题中提取
            if username == f"用户{user_id}":
                try:
                    title = driver.title
                    if title:
                        print(f"页面标题: {title}")
                        # 尝试从标题中提取用户名
                        if "-" in title:
                            parts = title.split("-")
                            for part in parts:
                                part = part.strip()
                                if part and not part in ["微博", "个人主页", "主页"]:
                                    username = part
                                    print(f"从标题提取到用户名: {username}")
                                    break
                        elif "_" in title:
                            parts = title.split("_")
                            for part in parts:
                                part = part.strip()
                                if part and not part in ["微博", "个人主页", "主页"]:
                                    username = part
                                    print(f"从标题提取到用户名: {username}")
                                    break
                        else:
                            print("标题格式不符合预期")
                except Exception as e:
                    print(f"获取页面标题失败: {str(e)}")
            
            # 如果没有找到，尝试从页面URL中提取用户名
            if username == f"用户{user_id}":
                try:
                    # 微博URL可能包含用户名，例如 https://weibo.com/username
                    current_url = driver.current_url
                    print(f"当前URL: {current_url}")
                    if "weibo.com/" in current_url:
                        parts = current_url.split("/")
                        for part in parts:
                            if part and not part in ["https:", "", "weibo.com", "u", "home", "profile"]:
                                if not part.isdigit():
                                    username = part
                                    print(f"从URL提取到用户名: {username}")
                                    break
                except Exception as e:
                    print(f"从URL提取用户名失败: {str(e)}")
        except Exception as e:
            print(f"提取用户名失败: {str(e)}")
            username = f"用户{user_id}"
        
        print(f"最终使用的用户名: {username}")
        
        # 不使用特定元素等待，改为简单的时间等待
        # 滚动加载更多内容
        print("滚动加载更多内容...")
        for i in range(3):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            print(f"滚动 {i+1}/3")
            time.sleep(4)  # 增加滚动后的等待时间
        
        # 尝试使用不同的选择器获取微博列表
        print("尝试获取微博列表...")
        weibo_items = []
        
        # 尝试多种可能的选择器
        selectors = [
            '.WB_feed_type',  # 传统选择器
            '.card-wrap',  # 可能的新选择器
            '.feed-card',  # 可能的新选择器
            '.list-item',   # 通用选择器
            '.WB_cardwrap',  # 微博卡片选择器
            '.WB_article',  # 微博文章选择器
            '.article-card'  # 文章卡片选择器
        ]
        
        for selector in selectors:
            try:
                items = driver.find_elements(By.CSS_SELECTOR, selector)
                if items:
                    weibo_items = items
                    print(f"使用选择器 '{selector}' 找到 {len(items)} 条微博")
                    break
            except:
                continue
        
        if not weibo_items:
            print("未找到微博内容，尝试获取页面全部文本...")
            # 如果找不到微博元素，尝试获取页面全部文本
            try:
                page_text = driver.find_element(By.TAG_NAME, 'body').text
                if page_text:
                    print(f"页面文本长度: {len(page_text)} 字符")
                    # 保存页面文本作为微博内容
                    return {
                        "contents": [{
                            "content": page_text[:5000],  # 限制长度
                            "date": datetime.now().strftime('%Y-%m-%d %H:%M'),
                            "link": user_url
                        }],
                        "username": username
                    }
                else:
                    print("页面文本为空")
                    return {
                        "contents": [],
                        "username": username
                    }
            except Exception as e:
                print(f"获取页面文本失败: {str(e)}")
                return {
                    "contents": [],
                    "username": username
                }
        
        weibo_contents = []
        print("开始提取微博内容...")
        
        for i, item in enumerate(weibo_items[:10]):  # 限制处理数量
            try:
                # 尝试提取发布时间
                publish_date = "未知"
                time_selectors = ['.WB_from.S_txt2 a', '.time', '.from', '.date', '.WB_time']
                for time_selector in time_selectors:
                    try:
                        time_elem = item.find_element(By.CSS_SELECTOR, time_selector)
                        publish_date = time_elem.text.strip()
                        break
                    except:
                        continue
                
                # 检查是否为限定时间内的微博/文章
                if not is_within_limit_hours(publish_date):
                    print(f"跳过非限定时间内内容: {publish_date}")
                    continue
                
                # 检查是否为微博文章
                is_article = False
                article_link = None
                
                # 尝试检测文章链接
                article_selectors = ['.WB_article a', '.article-title a', '.title a', '.WB_text a[href*="article"]']
                for selector in article_selectors:
                    try:
                        article_elem = item.find_element(By.CSS_SELECTOR, selector)
                        article_link = article_elem.get_attribute('href')
                        if article_link:
                            is_article = True
                            print(f"发现微博文章: {article_link}")
                            break
                    except:
                        continue
                
                # 尝试提取内容
                content = ""
                
                if is_article and article_link:
                    # 处理微博文章
                    try:
                        # 打开文章详情页
                        driver.execute_script(f"window.open('{article_link}', '_blank');")
                        # 切换到新窗口
                        driver.switch_to.window(driver.window_handles[-1])
                        time.sleep(5)  # 等待页面加载
                        
                        # 尝试提取文章内容
                        article_content_selectors = ['.articalContent', '.article-content', '.WB_artical', '.content']
                        for selector in article_content_selectors:
                            try:
                                content_elem = driver.find_element(By.CSS_SELECTOR, selector)
                                content = content_elem.text.strip()
                                break
                            except:
                                continue
                        
                        # 如果没有找到，尝试获取页面全部文本
                        if not content:
                            try:
                                content = driver.find_element(By.TAG_NAME, 'body').text.strip()
                            except:
                                pass
                        
                        # 关闭文章详情页
                        driver.close()
                        # 切换回原窗口
                        driver.switch_to.window(driver.window_handles[0])
                        
                        print(f"成功获取文章内容，长度: {len(content)}")
                    except Exception as e:
                        print(f"获取文章内容失败: {str(e)}")
                        # 确保切换回原窗口
                        try:
                            driver.close()
                        except:
                            pass
                        try:
                            driver.switch_to.window(driver.window_handles[0])
                        except:
                            pass
                else:
                    # 处理普通微博
                    content_selectors = ['.WB_text.W_f14', '.text', '.content', '.weibo-text', '.WB_text']
                    for content_selector in content_selectors:
                        try:
                            content_elem = item.find_element(By.CSS_SELECTOR, content_selector)
                            content = content_elem.text.strip()
                            break
                        except:
                            continue
                    
                    if not content:
                        try:
                            # 尝试获取元素的全部文本
                            content = item.text.strip()
                        except:
                            pass
                
                if not content:
                    print(f"跳过无内容的内容 {i+1}")
                    continue
                
                # 提取链接
                link = user_url
                if is_article and article_link:
                    link = article_link
                else:
                    link_selectors = ['.WB_from.S_txt2 a', '.link', '.weibo-link', '.WB_text a']
                    for link_selector in link_selectors:
                        try:
                            link_elem = item.find_element(By.CSS_SELECTOR, link_selector)
                            link = link_elem.get_attribute('href')
                            break
                        except:
                            continue
                
                weibo_contents.append({
                    "content": content,
                    "date": publish_date,
                    "link": link,
                    "is_article": is_article
                })
                content_type = "文章" if is_article else "微博"
                print(f"已添加{content_type} {i+1}: {publish_date} (内容长度: {len(content)})")
                
            except Exception as e:
                print(f"提取内容 {i+1} 失败: {str(e)}")
                continue
        
        print(f"成功提取 {len(weibo_contents)} 条微博")
        return {
            "contents": weibo_contents,
            "username": username
        }
    except Exception as e:
        # 尝试从页面上提取用户名
        error_username = f"用户{user_id}"
        try:
            title = driver.title
            if title and "的微博_微博" in title:
                error_username = title.replace("的微博_微博", "")
        except:
            pass
        
        print(f"获取用户 {error_username} 微博内容失败: {str(e)}")
        # 尝试获取页面截图用于调试
        try:
            screenshot_path = f"weibo_error_{error_username}.png"
            driver.save_screenshot(screenshot_path)
            print(f"已保存错误截图到: {screenshot_path}")
        except:
            pass
        return {
            "contents": [],
            "username": error_username
        }

def save_weibo_content(user_id, username, weibo_contents, archive_folder):
    """保存微博内容到归档文件夹"""
    if not weibo_contents:
        return
    
    # 确保归档文件夹存在
    ensure_archive_folder(archive_folder)
    
    # 保存微博内容
    filename = os.path.join(archive_folder, f"weibo_{username}.txt")
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"微博用户: {username}\n")
        f.write(f"用户ID: {user_id}\n")
        f.write(f"保存时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*50 + "\n\n")
        
        for i, content_item in enumerate(weibo_contents, 1):
            content_type = "文章" if content_item.get('is_article', False) else "微博"
            f.write(f"{content_type} {i}:\n")
            f.write(f"发布时间: {content_item['date']}\n")
            f.write(f"链接: {content_item['link']}\n")
            f.write("-"*30 + "\n")
            f.write(f"内容:\n{content_item['content']}\n")
            f.write("="*50 + "\n\n")
    
    print(f"微博内容已保存到: {filename}")

def collect_all_weibo_content(archive_folder):
    """收集所有微博内容"""
    all_weibo_content = []
    
    if not os.path.exists(archive_folder):
        print(f"存档目录不存在: {archive_folder}")
        return ""
    
    # 遍历存档目录中的所有微博文件
    for filename in os.listdir(archive_folder):
        if filename.startswith('weibo_') and filename.endswith('.txt') and not filename.endswith('_summary.txt'):
            filepath = os.path.join(archive_folder, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 从文件名中提取用户名
                    username = filename.replace('weibo_', '').replace('.txt', '')
                    all_weibo_content.append(f"=== 微博用户: {username} ===\n{content}")
            except Exception as e:
                print(f"读取文件失败 {filepath}: {e}")
    
    return '\n\n'.join(all_weibo_content)

def generate_weibo_investment_advice(all_content, archive_folder, current_date):
    """基于所有微博内容生成投资建议"""
    print("开始生成微博投资分析建议...")
    
    # 调用deepseek进行投资分析
    investment_advice = deepseek_summary(
        all_content,
        sysprompt="你是一个专业的金融分析师，擅长基于多份财经市场分析报告给出投资建议。请结合宏观经济、市场情绪、行业趋势等多个维度进行分析。",
        userprompt='''这些是最近限定时间内微博用户的内容，请基于以下所有内容，给出未来几天的投资建议，包括：\n1. 整体市场判断\n2. 重点行业/板块分析\n3. 具体投资策略\n4. 风险提示\n请详细分析并以自然文本格式给出专业建议。之后以json格式，将所有涉及到的股票和指数等标的信息附加在最后，样例格式如下：
        {
            "股票或指数名称": [
                 "平安银行",
                 "上证指数"
            ]
        }：\n\n'''
    )
    
    # 保存投资建议
    advice_filename = os.path.join(archive_folder, f"weibo_投资建议_{current_date}.txt")
    with open(advice_filename, 'w', encoding='utf-8') as f:
        f.write(f"投资分析建议 - {current_date}\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*50 + "\n\n")
        f.write(investment_advice)
    
    print(f"投资建议已保存到: {advice_filename}")
    return investment_advice

def run_weibo_task():
    """运行微博分析任务"""
    print("\n" + "="*50)
    print("开始执行微博分析任务")
    print("="*50)
    
    # 使用统一的日期工具获取当前分析日期
    current_date, date_reason, archive_folder = get_current_analysis_date()
    print_date_info()
    
    # 确保归档文件夹存在
    ensure_archive_folder(archive_folder)
    
    # 检查微博投资建议文件是否已存在
    weibo_advice_path = os.path.join(archive_folder, f"weibo_投资建议_{current_date}.txt")
    if os.path.exists(weibo_advice_path):
        print(f"微博投资建议文件已存在: {weibo_advice_path}")
        print("跳过微博任务执行")
        # 读取已存在的投资建议
        try:
            with open(weibo_advice_path, "r", encoding="utf-8") as f:
                weibo_advice = f.read()
            print(f"已读取现有微博投资建议，长度: {len(weibo_advice)}字符")
            return weibo_advice
        except Exception as e:
            print(f"读取现有微博投资建议失败: {str(e)}")
            return None
    
    # 初始化浏览器
    driver = setup_browser()
    
    try:
        # 登录微博
        if not login_and_save_cookie(driver):
            print("登录失败，无法继续执行微博任务")
            return None
        
        # 获取所有用户的微博内容
        all_weibo_collected = False
        user_ids = ["2014433131", "2453509265"]
        for user_id in user_ids:
            print(f"\n处理用户ID: {user_id}")
            result = get_weibo_content(driver, user_id)
            weibo_contents = result["contents"]
            username = result["username"]
            
            if weibo_contents:
                save_weibo_content(user_id, username, weibo_contents, archive_folder)
                all_weibo_collected = True
            else:
                print(f"未获取到用户 {username} 的微博内容")
        
        if not all_weibo_collected:
            print("未获取到任何微博内容，跳过投资建议生成")
            return None
        
        # 收集所有微博内容
        print("\n收集所有微博内容...")
        all_articles_content = collect_all_weibo_content(archive_folder)
        
        if all_articles_content.strip():
            print(f"已收集微博内容，总长度：{len(all_articles_content)}字符")
            investment_advice = generate_weibo_investment_advice(all_articles_content, archive_folder, current_date)
            print("\n微博任务完成")
            return investment_advice
        else:
            print("未找到任何微博内容，跳过投资建议生成")
            return None
            
    except Exception as e:
        print(f"微博任务执行失败: {str(e)}")
        return None
    finally:
        driver.quit()

if __name__ == "__main__":
    run_weibo_task()