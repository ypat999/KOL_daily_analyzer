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


from extract_subtitle import extract_subtitle_from_url
from deepseek_summary import deepseek_summary

# 全局配置（集中管理）
BILI_SPACE = "https://space.bilibili.com/"
BILI_API = "https://api.bilibili.com/x/space/arc/search"
UP_MIDS = [
            #"1609483218",  #江浙陈某
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
    return driver

# 工具函数：检查时间是否在18小时内
def is_within_18_hours(publish_date: str) -> bool:
    """
    检查发布时间是否在18小时内
    支持格式：'今天'、'X小时前'、'昨天'、'X天前'等
    """
    now = datetime.now()
    
    if "今天" in publish_date:
        return True
    elif "小时前" in publish_date:
        # 提取小时数
        match = re.search(r'(\d+)小时前', publish_date)
        if match:
            hours = int(match.group(1))
            return hours <= 18
        return True  # 如果无法提取小时数，默认包含
    elif "昨天" in publish_date:
        # 昨天的视频可能在18小时内，也可能不在
        # 需要检查具体时间，但B站通常只显示'昨天'，所以保守估计包含
        return True
    elif "天前" in publish_date:
        # 提取天数
        match = re.search(r'(\d+)天前', publish_date)
        if match:
            days = int(match.group(1))
            return days == 0  # 只有0天前（即今天）才包含
        return False
    else:
        # 其他格式（如具体日期），尝试解析
        try:
            # 尝试解析具体日期格式
            if "-" in publish_date:
                # 假设格式为 MM-DD 或 YYYY-MM-DD
                if len(publish_date.split("-")) == 2:
                    # MM-DD 格式，假设是今年
                    month, day = map(int, publish_date.split("-"))
                    publish_datetime = datetime(now.year, month, day)
                else:
                    # YYYY-MM-DD 格式
                    year, month, day = map(int, publish_date.split("-"))
                    publish_datetime = datetime(year, month, day)
                
                # 计算时间差
                time_diff = now - publish_datetime
                return time_diff.total_seconds() <= 18 * 3600
        except:
            pass
        
        # 无法解析的格式，默认不包含
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
        WebDriverWait(driver, 10).until(
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
def get_subtitle_urls_threaded(videos: list, max_workers: int = 5):
    """使用多线程并行获取多个视频的字幕URL"""
    subtitle_results = []
    
    def process_video(video):
        """处理单个视频的字幕URL获取"""
        try:
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

def run_bili_task():
    """运行B站视频分析任务"""
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
    all_videos = get_videos_by_selenium_threaded(UP_MIDS, max_workers=5)
    print(f"总共获取到 {len(all_videos)} 个视频")
    
    if not all_videos:
        print("没有找到任何新视频，程序结束")
        return None
    
    # 使用多线程并行获取所有视频的字幕URL
    print("开始使用多线程并行获取视频字幕URL...")
    subtitle_results = get_subtitle_urls_threaded(all_videos, max_workers=5)
    print(f"成功获取到 {len(subtitle_results)} 个视频的字幕URL")
    
    # 处理获取到字幕的视频
    for result in subtitle_results:
        video = result['video']
        subtitle_url = result['subtitle_url']
        
        try:
            # 从subtitle_url提取字幕
            subtitle = extract_subtitle_from_url(subtitle_url)
            if not subtitle:
                print(f"视频《{video['title']}》字幕提取失败")
                continue
            else:
                print(f"视频《{video['title']}》字幕提取成功,字幕长度:{len(subtitle)}")
                # 以视频名称保存字幕文件
                # 保存字幕到归档文件夹
                subtitle_path = os.path.join(archive_folder, f"bili_{video['title']}.txt")
                with open(subtitle_path, "w", encoding="utf-8") as f:
                    f.write(subtitle)
                print(f"字幕已保存到: {subtitle_path}")

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


if __name__ == "__main__":
    run_bili_task()
    
