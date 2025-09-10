# 标准库导入
import json
import os
import time
import shutil
from datetime import datetime, timedelta
import concurrent.futures
import threading

import random
import re

# 第三方库导入
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from seleniumwire import webdriver  # 替换原生webdriver
import subprocess
import tempfile
import os
import json


from extract_subtitle import extract_subtitle_from_url
from deepseek_summary import deepseek_summary

LIMIT_HOURS = 18  # 18小时内的视频

# 全局配置（集中管理）
BILI_SPACE = "https://space.bilibili.com/"
BILI_API = "https://api.bilibili.com/x/space/arc/search"
UP_MIDS = [
            "1609483218",  #江浙陈某
            "2137589551", #李大霄
            "480472604",  #鹰眼看盘
            "518031546", #财经-沉默的螺旋
            "1421580803", #九先生笔记
          ]  # B站UP主用户ID
COOKIE_PATH = "bili_cookies.json"  # 统一cookie路径配置
# 工具函数：浏览器初始化（反爬配置集中管理）
# 修改setup_browser函数使用selenium-wire
def setup_browser():
    options = webdriver.ChromeOptions()
    # 反指纹配置
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    # 禁用GCM等服务
    options.add_experimental_option("prefs", {
        "gcm": {"enabled": False},
        "push_messaging": {"enabled": False},
        "service_worker": {"enabled": False}
    })
    # 证书/日志配置
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--ignore-ssl-errors")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    # 初始化驱动
    # 使用selenium-wire的Chrome驱动
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )
    # 设置浏览器窗口尺寸为100x100
    driver.set_window_size(800, 600)
    return driver

# 工具函数：检查时间是否在18小时内
def is_within_18_hours(publish_date: str) -> bool:
    """
    检查发布时间是否在18小时内
    支持格式：'今天'、'X小时前'、'昨天'、'X天前'等
    """
    now = datetime.now()
    if "分钟" in publish_date:
        return True
    if "今天" in publish_date:
        return True
    if "小时前" in publish_date:
        # 提取小时数
        match = re.search(r'(\d+)小时前', publish_date)
        if match:
            hours = int(match.group(1))
            return hours <= LIMIT_HOURS
        return True  # 如果无法提取小时数，默认包含
        
    return False

# 工具函数：登录与cookie管理（提前到主流程前定义）
def login_and_save_cookie(driver) -> bool:
    try:
        driver.get("https://www.bilibili.com")
        # 加载已保存的cookie
        with open(COOKIE_PATH, 'r', encoding='utf-8') as f:
            for cookie in json.loads(f.read()):
                driver.add_cookie(cookie)
        # 验证登录状态
        driver.refresh()
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.bili-avatar'))
        )
        print("登录状态验证成功，使用已保存的cookie")
        return True
    except (FileNotFoundError, Exception):
        print("出错：", Exception)
        # 执行登录流程
        print("未找到有效cookie，执行登录...")
        driver.get("https://passport.bilibili.com/login")
        # （需补充验证码处理逻辑）
        WebDriverWait(driver, 120).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '.bili-avatar'))
        )
        # 保存新cookie
        with open(COOKIE_PATH, 'w', encoding='utf-8') as f:
            json.dump(driver.get_cookies(), f)
        print("登录成功，已保存新cookie")
        return True
    return False

# 主流程函数：获取UP主视频列表（逻辑清晰化）
def get_videos_by_selenium(driver, up_id: str):
    # 步骤1：初始化浏览器
    try:
        # 步骤2：前置登录（现在由调用者确保登录状态）
        # 注意：这个函数现在假设driver已经登录成功
        pass
        # 步骤3：访问UP主空间
        video_page_url = f'{BILI_SPACE}{up_id}/video'
        driver.get(video_page_url)
        print(f"访问URL：{video_page_url}")
        driver.refresh()
        # 步骤4：加载视频列表（无需滚动，直接获取前3个）
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'div.upload-video-card.grid-mode'))
        )
        # 直接获取所有视频项（无需滚动加载），取前5个
        items = driver.find_elements(By.CSS_SELECTOR, 'div.upload-video-card.grid-mode')[:5]  # 关键修改：限制前3个
        
        # 提取每个视频的标题、URL和发布时间
        videos = []
        for item in items:  # 遍历前5个视频项
            # 提取视频标题（保持原有逻辑）
            title_elem = item.find_element(By.CSS_SELECTOR, '.bili-video-card__title a')
            title = title_elem.text.strip()
            
            # 提取视频地址（保持原有逻辑）
            link_elem = item.find_element(By.CSS_SELECTOR, 'a.bili-cover-card')
            video_href = link_elem.get_attribute('href')
            if video_href.startswith('//'):
                video_url = f'https:{video_href}'
            else:
                video_url = video_href
            
            # 提取发布时间（保持原有逻辑）
            date_elem = item.find_element(By.CSS_SELECTOR, '.bili-video-card__subtitle span')
            publish_date = date_elem.text.strip()
            
            # 检查是否为18小时内的视频
            if is_within_18_hours(publish_date):
                videos.append({"title": title, "url": video_url, "date": publish_date})
                print(f"已添加18小时内视频: {title} ({publish_date})")
            else:
                print(f"跳过非18小时内视频: {title} ({publish_date})")
        return videos
    except Exception as e:
        print(f'爬取失败：{str(e)}')
        return []

# 多线程版本：获取UP主视频列表
def get_videos_by_selenium_threaded(up_ids: list, max_workers: int = 10):
    """使用多线程并行获取多个UP主的视频列表"""
    all_videos = []
    
    def process_up_id(up_id):
        """处理单个UP主的视频列表获取"""
        try:
            # 为每个线程创建独立的浏览器实例
            driver = setup_browser()
            logged_in = login_and_save_cookie(driver)
            
            if not logged_in:
                print(f"UP主 {up_id} 登录失败")
                driver.quit()
                return []
            
            videos = get_videos_by_selenium(driver, up_id)
            driver.quit()
            return videos
        except Exception as e:
            print(f"UP主 {up_id} 处理失败：{str(e)}")
            return []
    
    # 使用线程池并行处理
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_up_id = {executor.submit(process_up_id, up_id): up_id for up_id in up_ids}
        
        # 收集结果
        for future in concurrent.futures.as_completed(future_to_up_id):
            up_id = future_to_up_id[future]
            try:
                videos = future.result()
                if videos:
                    all_videos.extend(videos)
                    print(f"UP主 {up_id} 获取到 {len(videos)} 个视频")
                else:
                    print(f"UP主 {up_id} 无新视频")
            except Exception as e:
                print(f"UP主 {up_id} 处理异常：{str(e)}")
    
    return all_videos

# 主功能函数：获取字幕URL（复用现有浏览器实例）
def get_subtitle_url(bvid: str, driver_video=None) -> str:
    # 如果没有传入driver实例，则创建新的
    if driver_video is None:
        driver_video = setup_browser()  # 初始化浏览器
        LOGGED_IN_video  = login_and_save_cookie(driver_video)
        should_quit = True  # 需要自己关闭浏览器
    else:
        LOGGED_IN_video = True  # 复用已登录的浏览器
        should_quit = False  # 不需要自己关闭浏览器
    try:
        # 登录前置（复用登录函数）
        if not LOGGED_IN_video :
            return None
        # 访问视频页面
        video_page_url = f'https://www.bilibili.com/video/{bvid}'
        driver_video.get(video_page_url)
        print(f"访问视频页面：{video_page_url}")

        # 等待页面加载完成
        time.sleep(10)
        print("等待10秒后准备点击字幕按钮")

        # 点击字幕按钮（循环6次，成功就跳出）
        for attempt in range(6):
            try:
                subtitle_button = driver_video.find_element(By.CLASS_NAME, 'bpx-player-ctrl-subtitle')
                subtitle_button.click()
                print(f"已点击字幕按钮（第{attempt + 1}次尝试成功）")
                time.sleep(2)
                # 提取第一个匹配的请求URL
                for request in driver_video.requests:
                    if 'aisubtitle.hdslb.com' in request.url :
                        print(f"找到字幕请求URL: {request.url}")
                        # 直接返回请求URL
                        return request.url
                break
            except Exception as e:
                print(f"点击字幕按钮失败（第{attempt + 1}次尝试）")
                if attempt < 4:  # 如果不是最后一次尝试，等待2秒后重试
                    time.sleep(10)
                else:
                    print("字幕按钮点击尝试已达5次上限")
        print(f"未找到字幕请求URL")
        return None
    except Exception as e:
        print(f"获取字幕URL失败：{str(e)}")
        # 打印所有请求URL以便调试
        # print("所有请求URL:")
        # for request in driver.requests:
        #     print(f"- {request.url}")
        return None
    finally:
        # 只有自己创建的浏览器实例才需要关闭
        if should_quit:
            driver_video.quit()

# 多线程版本：获取多个视频的字幕URL
def get_subtitle_urls_threaded(videos: list, max_workers: int = 3):
    """使用多线程并行获取多个视频的字幕URL"""
    subtitle_results = []
    
    def process_video(video):
        """处理单个视频的字幕URL获取"""
        try:
            # 检查字幕文件是否已存在
            current_date = datetime.now().strftime('%Y-%m-%d')
            archive_folder = f'archive_{current_date}'
            subtitle_path = os.path.join(archive_folder, f"bili_{video['title']}.txt")
            
            if os.path.exists(subtitle_path):
                print(f"视频《{video['title']}》字幕文件已存在，跳过获取字幕URL")
                return {
                    'video': video,
                    'subtitle_url': 'local_file_exists'  # 特殊标记表示本地文件已存在
                }
            
            # 从video['url']中提取BVID
            url = video['url']
            match = re.search(r'/video/(BV[^/?]+)', url)
            if match:
                bvid = match.group(1)
            else:
                print(f'警告：未从URL中提取到BVID，URL：{url}')
                return None
            
            # 创建独立的浏览器实例
            driver = setup_browser()
            logged_in = login_and_save_cookie(driver)
            
            if not logged_in:
                print(f"视频 {video['title']} 登录失败")
                driver.quit()
                return None
            
            subtitle_url = get_subtitle_url(bvid, driver)
            driver.quit()
            
            if subtitle_url:
                print(f"视频《{video['title']}》字幕URL获取成功")
                return {
                    'video': video,
                    'subtitle_url': subtitle_url
                }
            else:
                print(f"视频《{video['title']}》无字幕")
                return None
        except Exception as e:
            print(f"视频《{video['title']}》字幕URL获取失败：{str(e)}")
            return None
    
    # 使用线程池并行处理
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_video = {executor.submit(process_video, video): video for video in videos}
        
        # 收集结果
        for future in concurrent.futures.as_completed(future_to_video):
            video = future_to_video[future]
            try:
                result = future.result()
                if result:
                    subtitle_results.append(result)
            except Exception as e:
                print(f"视频《{video['title']}》处理异常：{str(e)}")
    
    return subtitle_results

def run_bili_task(use_api_for_videos: bool = True):
    """运行B站视频分析任务
    
    Args:
        use_api_for_videos: 是否使用API方式获取视频列表，默认为True
    """
    # 配置参数
    pass  # 多线程处理不需要请求间隔

    # 创建按日期命名的归档文件夹
    current_date = datetime.now().strftime('%Y-%m-%d')
    archive_folder = f'archive_{current_date}'
    if not os.path.exists(archive_folder):
        os.makedirs(archive_folder)
        print(f"已创建日期归档文件夹: {archive_folder}")
    else:
        print(f"日期归档文件夹已存在: {archive_folder}")

    
    # 使用多线程并行获取所有UP主的视频列表
    print("开始使用多线程并行获取UP主视频列表...")
    
    if use_api_for_videos:
        print("使用API方式获取视频列表")
        all_videos = get_videos_by_api_threaded(UP_MIDS, max_workers=1)
    else:
        print("使用浏览器方式获取视频列表")
        all_videos = get_videos_by_selenium_threaded(UP_MIDS, max_workers=5)
    
    print(f"总共获取到 {len(all_videos)} 个视频")
    
    if not all_videos:
        print("没有找到任何新视频，程序结束")
        return None
    
    # 使用多线程并行获取所有视频的字幕URL（优先使用API方式）
    print("开始使用多线程并行获取视频字幕URL（API方式）...")
    subtitle_results = get_subtitle_urls_threaded(all_videos, max_workers=5, use_api=True)
    print(f"成功获取到 {len(subtitle_results)} 个视频的字幕URL")
    
    # 处理获取到字幕的视频
    for result in subtitle_results:
        video = result['video']
        
        try:
            # 检查字幕文件是否已存在
            subtitle_path = os.path.join(archive_folder, f"bili_{video['title']}.txt")
            if os.path.exists(subtitle_path):
                print(f"视频《{video['title']}》字幕已存在，跳过提取")
                # 读取已存在的字幕文件内容
                with open(subtitle_path, "r", encoding="utf-8") as f:
                    subtitle = f.read()
                print(f"已读取现有字幕，长度:{len(subtitle)}")
            else:
                # 处理不同类型的字幕结果
                if 'subtitle_url' in result:
                    subtitle_url = result['subtitle_url']
                    # 检查是否为本地文件已存在的标记
                    if subtitle_url == 'local_file_exists':
                        print(f"视频《{video['title']}》使用本地字幕文件")
                    else:
                        # 从subtitle_url提取字幕
                        subtitle = extract_subtitle_from_url(subtitle_url)
                        if not subtitle:
                            print(f"视频《{video['title']}》字幕提取失败")
                            continue
                        else:
                            print(f"视频《{video['title']}》字幕提取成功,字幕长度:{len(subtitle)}")
                            # 保存字幕到归档文件夹
                            with open(subtitle_path, "w", encoding="utf-8") as f:
                                f.write(subtitle)
                            print(f"字幕已保存到: {subtitle_path}")
                elif 'subtitle_content' in result:
                    # 使用yt-dlp+whisper生成的字幕内容
                    subtitle = result['subtitle_content']
                    print(f"视频《{video['title']}》使用语音识别生成字幕,字幕长度:{len(subtitle)}")
                    # 保存字幕到归档文件夹
                    with open(subtitle_path, "w", encoding="utf-8") as f:
                        f.write(subtitle)
                    print(f"字幕已保存到: {subtitle_path}")
                else:
                    print(f"视频《{video['title']}》无有效字幕信息")
                    continue

            # 检查总结文件是否已存在
            summary_path = os.path.join(archive_folder, f"bili_{video['title']}_summary.txt")
            if os.path.exists(summary_path):
                print(f"视频《{video['title']}》总结已存在，跳过生成")
            else:
                print("使用deepseek总结")
                summary = deepseek_summary(subtitle,
                                            sysprompt = "你是一个专业的财经内容总结助手，擅长对视频字幕进行简洁明了的总结，并特别关注其中对于投资操作建议的内容。", 
                                            userprompt = "请总结以下字幕内容：")
                print(f"视频《{video['title']}》总结：{summary[:100]}...")
                # 保存总结到归档文件夹
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write(summary)
                print(f"总结已保存到: {summary_path}")
        except Exception as e:
            print(f"视频《{video['title']}》处理失败：{str(e)}")
    
    #将所有总结一起给deepseek，让其给出后续投资建议
    print("收集所有总结文件...")
    all_summaries = []
    files = os.listdir(archive_folder)
    for file in files:
        if file.endswith('_summary.txt') and file.startswith('bili_'):
            filepath = os.path.join(archive_folder, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                all_summaries.append(f.read())

    # 合并所有总结
    combined_summary = '\n\n'.join(all_summaries)
    print(f"已收集{len(all_summaries)}个总结，总长度：{len(combined_summary)}字符")

    # 调用deepseek获取投资建议
    print("发送所有总结给deepseek，获取投资建议...")
    investment_advice = deepseek_summary(
        combined_summary,
        sysprompt="你是一个专业的金融分析师，擅长基于多份市场分析报告给出投资建议。",
        userprompt="这些是最近一两天的财经博主内容总结，请基于以下所有总结内容，给出未来几天的投资建议：\n\n"
    )

    # 保存投资建议到归档文件夹
    advice_path = os.path.join(archive_folder, f"bili_投资建议_{current_date}.txt")
    with open(advice_path, "w", encoding="utf-8") as f:
        f.write(investment_advice)
    print(f"投资建议已保存到: {advice_path}")
    
    print("B站任务完成")
    return investment_advice
































# 工具函数：加载cookie用于API请求
def load_cookies_for_api():
    """从cookie文件加载cookie，用于API请求"""
    try:
        if os.path.exists(COOKIE_PATH):
            with open(COOKIE_PATH, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
                
            # 将cookie转换为requests库可用的格式
            cookie_dict = {}
            for cookie in cookies:
                if 'name' in cookie and 'value' in cookie:
                    cookie_dict[cookie['name']] = cookie['value']
            
            return cookie_dict
        else:
            print("Cookie文件不存在，API请求将使用未登录状态")
            return {}
    except Exception as e:
        print(f"加载cookie失败: {str(e)}")
        return {}

def get_video_info_via_api(bvid: str):
    """通过B站API获取视频信息，包括aid和cid"""
    url = f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}'
    
    # 添加合适的请求头来避免412错误
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'https://www.bilibili.com/video/{bvid}',
        'Origin': 'https://www.bilibili.com',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
    }
    
    # 加载cookie用于API请求
    cookies = load_cookies_for_api()
    
    try:
        response = requests.get(url, headers=headers, cookies=cookies, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('code') == 0:
            video_data = data['data']
            aid = video_data.get('aid')
            cid = video_data.get('cid')
            title = video_data.get('title', '')
            
            print(f"API获取视频信息成功 - aid: {aid}, cid: {cid}, 标题: {title}")
            return {
                'aid': aid,
                'cid': cid,
                'title': title,
                'data': video_data
            }
        else:
            print(f"API获取视频信息失败: {data.get('message', '未知错误')}")
            return None
            
    except Exception as e:
        print(f"API获取视频信息异常: {str(e)}")
        return None

def get_subtitle_url_via_api(bvid: str):
    """通过B站API获取字幕URL"""
    # 首先获取视频信息
    video_info = get_video_info_via_api(bvid)
    if not video_info:
        return None
    
    aid = video_info['aid']
    cid = video_info['cid']
    
    # 使用获取到的aid和cid请求字幕信息
    subtitle_url = f'https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}'
    
    # 添加合适的请求头
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': f'https://www.bilibili.com/video/{bvid}',
        'Origin': 'https://www.bilibili.com',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
    }
    
    # 加载cookie用于API请求
    cookies = load_cookies_for_api()
    
    try:
        response = requests.get(subtitle_url, headers=headers, cookies=cookies, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('code') == 0:
            subtitle_data = data.get('data', {})
            subtitle_info = subtitle_data.get('subtitle', {})
            
            # 查找AI生成的中文字幕 (lan: "ai-zh")
            subtitles = subtitle_info.get('subtitles', [])
            
            print(f"找到 {len(subtitles)} 个字幕选项")
            for i, subtitle in enumerate(subtitles):
                print(f"  字幕 {i+1}: lan={subtitle.get('lan')}, lan_doc={subtitle.get('lan_doc')}")
            
            for subtitle in subtitles:
                if subtitle.get('lan') == 'ai-zh':
                    subtitle_url = subtitle.get('subtitle_url')
                    if subtitle_url:
                        # 确保URL是完整的
                        if subtitle_url.startswith('//'):
                            subtitle_url = f'https:{subtitle_url}'
                        elif not subtitle_url.startswith('http'):
                            subtitle_url = f'https://{subtitle_url}'
                        
                        print(f"找到AI中文字幕URL: {subtitle_url}")
                        return subtitle_url
            
            # 如果没有找到ai-zh字幕，尝试其他中文字幕
            for subtitle in subtitles:
                if subtitle.get('lan_doc') == '中文' or subtitle.get('lan') == 'zh':
                    subtitle_url = subtitle.get('subtitle_url')
                    if subtitle_url:
                        # 确保URL是完整的
                        if subtitle_url.startswith('//'):
                            subtitle_url = f'https:{subtitle_url}'
                        elif not subtitle_url.startswith('http'):
                            subtitle_url = f'https://{subtitle_url}'
                        
                        print(f"找到中文字幕URL: {subtitle_url}")
                        return subtitle_url
            
            print(f"视频 {bvid} 没有找到可用的字幕")
            return None
        else:
            print(f"API获取字幕信息失败: {data.get('message', '未知错误')}")
            return None
            
    except Exception as e:
        print(f"API获取字幕信息异常: {str(e)}")
        return None

# 改进的多线程版本：获取多个视频的字幕URL（支持API方式）
def get_subtitle_urls_threaded(videos: list, max_workers: int = 3, use_api: bool = True):
    """使用多线程并行获取多个视频的字幕URL，可选择使用API或浏览器方式"""
    subtitle_results = []
    
    def process_video(video):
        """处理单个视频的字幕URL获取"""
        try:
            # 检查字幕文件是否已存在
            current_date = datetime.now().strftime('%Y-%m-%d')
            archive_folder = f'archive_{current_date}'
            subtitle_path = os.path.join(archive_folder, f"bili_{video['title']}.txt")
            
            if os.path.exists(subtitle_path):
                print(f"视频《{video['title']}》字幕文件已存在，跳过获取字幕URL")
                return {
                    'video': video,
                    'subtitle_url': 'local_file_exists'  # 特殊标记表示本地文件已存在
                }
            
            # 从video['url']中提取BVID
            url = video['url']
            match = re.search(r'/video/(BV[^/?]+)', url)
            if match:
                bvid = match.group(1)
            else:
                print(f'警告：未从URL中提取到BVID，URL：{url}')
                return None
            
            if use_api:
                # 使用API方式获取字幕URL
                subtitle_url = get_subtitle_url_via_api(bvid)
                if subtitle_url:
                    print(f"视频《{video['title']}》API字幕URL获取成功")
                    return {
                        'video': video,
                        'subtitle_url': subtitle_url
                    }
                else:
                    print(f"视频《{video['title']}》API方式无字幕，尝试使用ytdlp+whisper方式")
                    # API方式失败时回退到浏览器方式
                    return generate_subtitle_with_ytdlp_whisper(bvid, video)
            else:
                # 使用浏览器方式获取字幕URL
                return get_subtitle_url_browser_fallback(bvid, video)
                
        except Exception as e:
            print(f"视频《{video['title']}》字幕URL获取失败：{str(e)}")
            return None
    
    def get_subtitle_url_browser_fallback(bvid, video):
        """浏览器方式获取字幕URL（备用方案）"""
        try:
            # 创建独立的浏览器实例
            driver = setup_browser()
            logged_in = login_and_save_cookie(driver)
            
            if not logged_in:
                print(f"视频 {video['title']} 登录失败")
                driver.quit()
                return None
            
            subtitle_url = get_subtitle_url(bvid, driver)
            driver.quit()
            
            if subtitle_url:
                print(f"视频《{video['title']}》浏览器字幕URL获取成功")
                return {
                    'video': video,
                    'subtitle_url': subtitle_url
                }
            else:
                print(f"视频《{video['title']}》无字幕")
                return None
        except Exception as e:
            print(f"视频《{video['title']}》浏览器方式获取字幕失败：{str(e)}")
            return None
    
    # 使用线程池并行处理
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_video = {executor.submit(process_video, video): video for video in videos}
        
        # 收集结果
        for future in concurrent.futures.as_completed(future_to_video):
            video = future_to_video[future]
            try:
                result = future.result()
                if result:
                    subtitle_results.append(result)
            except Exception as e:
                print(f"视频《{video['title']}》处理异常：{str(e)}")
    
    return subtitle_results
    

def get_videos_by_api(up_id: str, page: int = 1, page_size: int = 10, max_retries: int = 3):
    """使用API获取UP主视频列表
    
    Args:
        up_id: UP主ID
        page: 页码，默认为1
        page_size: 每页数量，默认为30
        max_retries: 最大重试次数，默认为3
    """
    for attempt in range(max_retries):
        try:
            # 加载cookie
            cookies = load_cookies_for_api()
            if not cookies:
                print(f"UP主 {up_id} API请求失败：无法加载cookie")
                return []
            
            # 构建API URL
            api_url = f"https://api.bilibili.com/x/space/arc/search?mid={up_id}&pn={page}&ps={page_size}"
            
            # 设置请求头
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Referer': f'https://space.bilibili.com/{up_id}/video',
                'Origin': 'https://space.bilibili.com'
            }
            
            print(f"API请求UP主 {up_id} 的视频列表，页码: {page} (尝试 {attempt + 1}/{max_retries})")
            
            # 发送API请求
            response = requests.get(api_url, headers=headers, cookies=cookies, timeout=10)
            
            if response.status_code != 200:
                print(f"API请求失败，状态码: {response.status_code}")
                if attempt < max_retries - 1:
                    time.sleep(2)  # 等待2秒后重试
                    continue
                return []
            
            data = response.json()
            
            if data.get('code') != 0:
                error_msg = data.get('message', '未知错误')
                print(f"API返回错误: {error_msg}")
                
                # 如果是频率限制错误，等待后重试
                if "频繁" in error_msg or "频率" in error_msg:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 3  # 递增等待时间
                        print(f"频率限制，等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                        continue
                return []
            
            # 解析视频列表
            videos = []
            vlist = data.get('data', {}).get('list', {}).get('vlist', [])
            
            for video_data in vlist:
                title = video_data.get('title', '')
                bvid = video_data.get('bvid', '')
                created_timestamp = video_data.get('created', 0)
                
                # 转换时间戳为日期字符串
                created_date = datetime.fromtimestamp(created_timestamp).strftime('%Y-%m-%d %H:%M')
                
                # 构建视频URL
                video_url = f"https://www.bilibili.com/video/{bvid}"
                
                # 检查是否为18小时内的视频
                if is_within_18_hours(created_date):
                    videos.append({
                        "title": title,
                        "url": video_url,
                        "date": created_date,
                        "bvid": bvid,
                        "aid": video_data.get('aid'),
                        "play": video_data.get('play', 0),
                        "comment": video_data.get('comment', 0)
                    })
                    print(f"已添加18小时内视频: {title} ({created_date})")
                else:
                    print(f"跳过非18小时内视频: {title} ({created_date})")
            
            print(f"API获取到 {len(videos)} 个18小时内视频")
            return videos
            
        except requests.exceptions.RequestException as e:
            print(f"API网络请求异常: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return []
        except Exception as e:
            print(f"API处理异常: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return []
    
    return []

# 多线程版本：获取UP主视频列表（API方式）
def get_videos_by_api_threaded(up_ids: list, max_workers: int = 1):
    """使用多线程并行获取多个UP主的视频列表（API方式）"""
    all_videos = []
    
    def process_up_id(up_id):
        """处理单个UP主的视频列表获取"""
        try:
            videos = get_videos_by_api(up_id)
            return videos
        except Exception as e:
            print(f"UP主 {up_id} API处理失败：{str(e)}")
            return []
    
    # 使用线程池并行处理
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_up_id = {executor.submit(process_up_id, up_id): up_id for up_id in up_ids}
        
        # 收集结果
        for future in concurrent.futures.as_completed(future_to_up_id):
            up_id = future_to_up_id[future]
            try:
                videos = future.result()
                if videos:
                    all_videos.extend(videos)
                    print(f"UP主 {up_id} API获取到 {len(videos)} 个视频")
                else:
                    print(f"UP主 {up_id} API无新视频")
            except Exception as e:
                print(f"UP主 {up_id} API处理异常：{str(e)}")
    
    return all_videos


    













def download_video_with_ytdlp(video_url: str, output_dir: str) -> str:
    """使用yt-dlp下载视频音频
    
    Args:
        video_url: 视频URL
        output_dir: 输出目录
        
    Returns:
        str: 下载的音频文件路径
    """
    try:
        # 构建yt-dlp命令
        cmd = [
            'yt-dlp',
            '--ffmpeg-location', "D:\Program Files\MediaCoder\codecs64",
            '-x',  # 提取音频
            '--audio-format', 'wav',  # 转换为wav格式
            '--audio-quality', '0',  # 最高质量
            '--output', os.path.join(output_dir, '%(title)s.%(ext)s'),
            video_url
        ]
        
        print(f"开始下载音频: {video_url}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30分钟超时
        
        if result.returncode != 0:
            print(f"yt-dlp下载失败: {result.stderr}")
            return None
        
        # 解析输出获取文件路径
        lines = result.stdout.split('\n')
        for line in lines:
            if '[ExtractAudio] Destination:' in line:
                file_path = line.split('Destination:')[-1].strip()
                if os.path.exists(file_path):
                    print(f"音频下载成功: {file_path}")
                    return file_path
        
        # 如果无法从输出中解析，尝试在输出目录中查找
        for file in os.listdir(output_dir):
            if file.endswith('.wav'):
                file_path = os.path.join(output_dir, file)
                print(f"找到音频文件: {file_path}")
                return file_path
        
        print("无法找到下载的音频文件")
        return None
        
    except subprocess.TimeoutExpired:
        print("yt-dlp下载超时")
        return None
    except Exception as e:
        print(f"yt-dlp下载异常: {e}")
        return None

def transcribe_audio_with_whisper(audio_path: str, output_dir: str) -> str:
    """使用faster-whisper进行语音识别
    
    Args:
        audio_path: 音频文件路径
        output_dir: 输出目录
        
    Returns:
        str: 生成的字幕文件路径
    """
    try:
        # 检查是否安装了faster-whisper
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            print("未安装faster-whisper，请运行: pip install faster-whisper")
            return None
        
        # 检测GPU可用性并选择设备
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
                compute_type = "float16"  # GPU上使用float16以获得更好性能
                print("检测到GPU可用，使用CUDA设备进行语音识别")
            else:
                device = "cpu"
                compute_type = "int8"
                print("未检测到GPU，使用CPU进行语音识别")
        except ImportError:
            device = "cpu"
            compute_type = "int8"
            print("torch未安装，使用CPU进行语音识别")
        
        # 初始化模型（使用small模型，速度较快）
        print(f"加载faster-whisper模型 (设备: {device}, 计算类型: {compute_type})...")
        model = WhisperModel("small", device=device, compute_type=compute_type)
        
        print(f"开始语音识别: {audio_path}")
        segments, info = model.transcribe(audio_path, beam_size=5, language="zh")
        
        # 生成SRT字幕
        srt_path = os.path.join(output_dir, os.path.basename(audio_path).replace('.wav', '.srt'))
        
        with open(srt_path, 'w', encoding='utf-8') as f:
            for i, segment in enumerate(segments, 1):
                # 转换时间格式
                start_time = format_time(segment.start)
                end_time = format_time(segment.end)
                
                f.write(f"{i}\n")
                f.write(f"{start_time} --> {end_time}\n")
                f.write(f"{segment.text}\n\n")
        
        print(f"字幕生成成功: {srt_path}")
        return srt_path
        
    except Exception as e:
        print(f"语音识别异常: {e}")
        return None

def format_time(seconds: float) -> str:
    """将秒数格式化为SRT时间格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}".replace('.', ',')

def generate_subtitle_with_ytdlp_whisper(bvid: str, video: dict) -> dict:
    """使用yt-dlp+faster-whisper方式生成字幕
    
    Args:
        bvid: 视频BV号
        video: 视频信息字典
        
    Returns:
        dict: 包含视频和字幕URL的信息
    """
    try:
        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"视频《{video['title']}》使用yt-dlp+whisper方式生成字幕")
            
            # 下载音频
            audio_path = download_video_with_ytdlp(video['url'], temp_dir)
            if not audio_path or not os.path.exists(audio_path):
                print(f"音频下载失败: {video['title']}")
                return None
            
            # 语音识别生成字幕
            srt_path = transcribe_audio_with_whisper(audio_path, temp_dir)
            if not srt_path or not os.path.exists(srt_path):
                print(f"字幕生成失败: {video['title']}")
                return None
            
            # 读取字幕内容
            with open(srt_path, 'r', encoding='utf-8') as f:
                subtitle_content = f.read()
            
            # 返回结果（这里返回字幕内容而不是URL，因为是通过语音识别生成的）
            return {
                'video': video,
                'subtitle_content': subtitle_content,
                'subtitle_type': 'whisper_generated'
            }
            
    except Exception as e:
        print(f"yt-dlp+whisper字幕生成异常: {e}")
        return None

def get_subtitle_url_browser_fallback(bvid, video):
    """浏览器方式获取字幕URL（备用方案）
    如果浏览器方式也失败，则使用yt-dlp+whisper方式生成字幕
    """
    try:
        # 创建独立的浏览器实例
        driver = setup_browser()
        logged_in = login_and_save_cookie(driver)
        
        if not logged_in:
            print(f"视频 {video['title']} 登录失败")
            driver.quit()
            # 登录失败时尝试使用yt-dlp+whisper方式
            return generate_subtitle_with_ytdlp_whisper(bvid, video)
        
        subtitle_url = get_subtitle_url(bvid, driver)
        driver.quit()
        
        if subtitle_url:
            print(f"视频《{video['title']}》浏览器字幕URL获取成功")
            return {
                'video': video,
                'subtitle_url': subtitle_url
            }
        else:
            print(f"视频《{video['title']}》无字幕，尝试yt-dlp+whisper方式")
            # 浏览器方式无字幕时使用yt-dlp+whisper方式
            return generate_subtitle_with_ytdlp_whisper(bvid, video)
    except Exception as e:
        print(f"视频《{video['title']}》浏览器方式获取字幕失败：{str(e)}，尝试yt-dlp+whisper方式")
        # 浏览器方式失败时使用yt-dlp+whisper方式
        return generate_subtitle_with_ytdlp_whisper(bvid, video)

if __name__ == "__main__":
    run_bili_task(use_api_for_videos=False)
    
