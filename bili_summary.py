# 标准库导入
import json
import os
import sys
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
# 注意：selenium-wire 在 Python 3.12+ 环境导入依赖 blinker._saferef 和 pkg_resources。
# 若报 No module named 'blinker._saferef' / 'pkg_resources'，执行:
#   python -m pip install "blinker==1.7.0" "setuptools==80.9.0"
# （与 requirements.txt 保持一致，勿升级这两个包）
import subprocess
import tempfile
import os
import json

from cookie_validator import _get_chrome_service
from extract_subtitle import extract_subtitle_from_url
from deepseek_summary import deepseek_summary
from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend
from prediction_recorder import record_predictions_from_advice
from stage_timer import timed

LIMIT_HOURS = 18  # 平时限定小时内（18小时），周末只收录周五收盘后发布的内容

# 全局锁：faster-whisper 底层是 CTranslate2，多线程并发使用 GPU 会导致 native 层崩溃
# （try/except 无法捕获，Python 进程静默退出）。所有 whisper 转写必须串行执行。
WHISPER_LOCK = threading.Lock()

# 全局锁：seleniumwire 每个实例都会启动独立的 mitmproxy 本地代理后端，
# 多实例并发创建/运行会导致 chromedriver native 崩溃（Stacktrace: GetHandleVerifier）。
# 所有 seleniumwire 浏览器创建必须串行执行。
BROWSER_LOCK = threading.Lock()

# 全局配置（集中管理）
BILI_SPACE = "https://space.bilibili.com/"
BILI_API = "https://api.bilibili.com/x/space/arc/search"
# UP主ID → 昵称：采集时就随视频一起记录，总结/归档据此标明"是谁说的"
# （此前只记录标题，总结阶段只能靠 identify_bili_up 从正文里猜名字）
UP_NAMES = {
            "1609483218": "江浙陈某",
            #"2137589551": "李大霄",  # 暂不采集
            "480472604": "鹰眼看盘",
            "518031546": "财经-沉默的螺旋",
            "1421580803": "九先生笔记",
            "515688213": "连板",
            "471949556": "海螺复盘",
            "3546681834473583": "生炸瓜",
          }
UP_MIDS = list(UP_NAMES.keys())  # B站UP主用户ID
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
    # 稳定性参数：避免 Chrome 启动时 GPU/沙箱/共享内存异常导致 session not created / chrome not reachable
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-background-networking")
    options.add_argument("--log-level=3")
    # eager: driver.get 在 DOMContentLoaded 即返回，避免 B站视频页等全部资源加载
    # 导致 seleniumwire 后端(localhost:port) 120s 超时 (Read timed out)
    options.page_load_strategy = 'eager'
    # 初始化驱动
    # 使用selenium-wire的Chrome驱动
    service = _get_chrome_service()
    if service is None:
        raise RuntimeError("无法获取chromedriver，请检查Chrome浏览器是否安装")

    # 加锁：seleniumwire 多实例并发创建会 native 崩溃，浏览器创建必须串行。
    # 注意：锁只包住创建过程；持有 driver 后的页面操作是独立会话，可并行。
    with BROWSER_LOCK:
        driver = webdriver.Chrome(service=service, options=options)
    # 页面加载兜底超时，防止 driver.get 长时间挂起（元素等待由各处 WebDriverWait 单独控制）
    driver.set_page_load_timeout(30)
    # 设置浏览器窗口尺寸为100x100
    driver.set_window_size(800, 600)
    return driver

# 工具函数：检查时间是否在限定小时内

def is_within_limit_hours(publish_date) -> bool:
    """
    检查发布时间是否在限定小时内
    publish_date 支持两种形态：
      - datetime：API 路径直接传入精确发布时间（保留精度，不再 strftime 后正则反向解析）
      - str：页面相对时间（'今天'、'X小时前'、'昨天'、'X天前'）或日期串（'2025-01-01'、'2025-01-01 22:02'）
    周末运行时，收录周五收盘后所有时间的内容
    周一早上9点前也使用周末逻辑（因为还未开盘）
    """
    now = datetime.now()
    
    # 检查是否为周末 (周六或周日) 或周一早上9点前
    weekday = now.weekday()  # 0=周一, 6=周日
    is_weekend = weekday >= 5  # 5=周六, 6=周日
    
    # 周一早上9点前也使用周末逻辑
    is_monday_early = weekday == 0 and now.hour < 9  # 周一且9点前
    is_weekend_period = is_weekend or is_monday_early

    # API 路径：直接按精确时间判断
    if isinstance(publish_date, datetime):
        return _within_limit_by_datetime(publish_date, now, is_weekend_period)
    
    # 处理日期格式：'YYYY-MM-DD' / 'MM-DD' / 'M-D' / 'M月D日' / 'YYYY年M月D日'，可带时间 'HH:MM'
    # 注意：B站网页卡片常用中文格式（如 '9月23日'），此前只匹配带连字符的写法，
    # 解析失败会落到下面相对时间分支末尾的 return True，导致过期视频被误收录
    date_match = re.match(
        r'(?:(\d{4})[-年])?(\d{1,2})[-月](\d{1,2})日?(?:[ T](\d{1,2}):(\d{2}))?',
        publish_date)
    if date_match:
        try:
            has_year = bool(date_match.group(1))
            video_date = datetime(
                int(date_match.group(1)) if has_year else now.year,
                int(date_match.group(2)),
                int(date_match.group(3)))

            # 没写年份且解析出的日期在未来，说明是去年的（比如1月1日刚过时）
            if not has_year and video_date > now:
                video_date = video_date.replace(year=now.year - 1)

            # 带时间则用真实发布时间（此前丢弃 HH:MM，晚上发的视频会被当成当天
            # 00:00，隔天凌晨运行时误判 >18h 而跳过）
            if date_match.group(4):
                video_date = video_date.replace(
                    hour=int(date_match.group(4)),
                    minute=int(date_match.group(5)))

            return _within_limit_by_datetime(video_date, now, is_weekend_period)
        except ValueError:
            return False

    # 处理相对时间格式（今天、X小时前、X分钟前等）
    if is_weekend_period:
        friday_date = get_friday_date_for_weekend(now)
        friday_close_time = friday_date.replace(hour=15, minute=0, second=0, microsecond=0)
        
        if "分钟" in publish_date or "今天" in publish_date:
            return True
            
        if "小时前" in publish_date:
            match = re.search(r'(\d+)小时前', publish_date)
            if match:
                hours = int(match.group(1))
                publish_time = now - timedelta(hours=hours)
                return publish_time >= friday_close_time
            return True
            
        if "昨天" in publish_date:
            return True
            
        if "天前" in publish_date:
            match = re.search(r'(\d+)天前', publish_date)
            if match:
                days = int(match.group(1))
                return days <= 2
            return True
            
        return True
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


def _within_limit_by_datetime(video_date: datetime, now: datetime, is_weekend_period: bool) -> bool:
    """按精确发布时间判断：周末/周一早收录周五收盘后，平时 18 小时限制"""
    if is_weekend_period:
        friday_date = get_friday_date_for_weekend(now)
        friday_close_time = friday_date.replace(hour=15, minute=0, second=0, microsecond=0)
        return video_date.date() >= friday_close_time.date()
    else:
        delta = now - video_date
        hours_diff = delta.total_seconds() / 3600
        return hours_diff <= LIMIT_HOURS

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
SPACE_CARD_SELECTOR = 'div.upload-video-card.grid-mode'


def _is_space_empty_placeholder(driver) -> bool:
    """空间页是否显示"还没投过视频"占位

    该占位有时是假空：接口被风控/慢一拍时前端直接渲染成"空间主人还没投过视频，
    这里什么也没有..."，重新加载页面内容就会出来。
    """
    try:
        return driver.execute_script(
            "return document.body.innerText.indexOf('还没投过视频') >= 0;")
    except Exception:
        return False


def _get_space_up_name(driver, up_id: str = "") -> str:
    """从UP主空间页提取昵称：DOM → 页面标题 → 配置映射，逐级兜底"""
    for selector in ('.nickname', '#h-name', '.up-name', '.user-name'):
        try:
            txt = (driver.find_element(By.CSS_SELECTOR, selector).text or '').strip()
            if txt:
                return txt
        except Exception:
            continue
    try:
        matched = re.match(r'^(.+?)的个人空间', (driver.title or '').strip())
        if matched:
            return matched.group(1).strip()
    except Exception:
        pass
    return UP_NAMES.get(str(up_id), "")


def get_videos_by_selenium(driver, up_id: str):
    # 步骤1：初始化浏览器
    try:
        # 步骤2：前置登录（现在由调用者确保登录状态）
        # 注意：这个函数现在假设driver已经登录成功
        pass
        # 步骤3：访问UP主空间（假空占位时重新加载，最多 3 次）
        video_page_url = f'{BILI_SPACE}{up_id}/video'
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            driver.get(video_page_url)
            if attempt == 1:
                print(f"访问URL：{video_page_url}")
            else:
                print(f"访问URL：{video_page_url}（第{attempt}次加载）")
            # 注意：不执行 driver.refresh()，eager 策略下 refresh 会触发页面全量加载，
            # 易导致 seleniumwire 后端(localhost:port) 超时/崩溃（空 Message + GetHandleVerifier）；
            # 假空时重新 driver.get 同一地址即可
            try:
                WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, SPACE_CARD_SELECTOR))
                )
                break
            except Exception:
                if attempt < max_attempts and _is_space_empty_placeholder(driver):
                    print(f"UP主 {up_id} 空间页显示“还没投过视频”（疑似假空），重新加载页面...")
                    time.sleep(2)
                    continue
                break
        # 步骤4：先取UP主昵称，随视频一起记录（总结/归档要标明"是谁说的"）
        up_name = _get_space_up_name(driver, up_id)
        print(f"UP主 {up_id} 昵称：{up_name or '未知'}")

        # 步骤5：加载视频列表（无需滚动，直接获取前3个）
        # 直接获取所有视频项（无需滚动加载），取前5个
        items = driver.find_elements(By.CSS_SELECTOR, SPACE_CARD_SELECTOR)[:5]  # 关键修改：限制前3个
        
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
            
            # 检查是否为限定小时内的视频（周末只收录周五收盘后发布的内容）
            if is_within_limit_hours(publish_date):
                videos.append({"title": title, "url": video_url, "date": publish_date,
                               "up_name": up_name, "up_id": up_id})
                print(f"已添加限定时间内视频: {title} ({publish_date})")
            else:
                print(f"跳过非限定时间内视频: {title} ({publish_date})")
        return videos
    except Exception as e:
        print(f'爬取失败：{str(e)}')
        return []

# 多线程版本：获取UP主视频列表
def get_videos_by_selenium_threaded(up_ids: list, max_workers: int = 3):
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
SUBTITLE_BTN_SELECTOR = 'div.bpx-player-ctrl-subtitle'
SUBTITLE_LANG_SELECTOR = 'div.bpx-player-ctrl-subtitle-language-item'


def _select_subtitle_language(driver_video, timeout=8) -> bool:
    """在新版字幕面板里选中「中文（AI）」（data-lan=ai-zh）

    播放器改版后，点字幕按钮只是展开面板，必须在面板里选语言才会真正请求字幕轨；
    旧版没有这个面板（点按钮即加载），选不到就返回 False，由调用方继续扫描请求。
    """
    try:
        WebDriverWait(driver_video, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SUBTITLE_LANG_SELECTOR)))
    except Exception:
        return False
    items = driver_video.find_elements(By.CSS_SELECTOR, SUBTITLE_LANG_SELECTOR)
    target = None
    for it in items:
        if (it.get_attribute('data-lan') or '') == 'ai-zh':
            target = it
            break
    if target is None and items:
        target = items[0]
    if target is None:
        return False
    try:
        # 面板在控制栏里同样可能被判为不可见，统一用 JS 点击
        driver_video.execute_script("arguments[0].click();", target)
        print(f"已选择字幕语言：{(target.get_attribute('innerText') or '').strip()}")
    except Exception as e:
        print(f"选择字幕语言失败：{str(e)[:80]}")
        return False
    return True


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

        # 点击字幕按钮（循环3次，成功就跳出）
        # 注意：控制栏未激活时按钮处于不可见状态，普通 click 会抛
        # ElementClickInterceptedException，这里统一用 JS 点击
        clicked = False
        for attempt in range(3):
            try:
                subtitle_button = driver_video.find_element(By.CSS_SELECTOR, SUBTITLE_BTN_SELECTOR)
                driver_video.execute_script("arguments[0].click();", subtitle_button)
                print(f"已点击字幕按钮（第{attempt + 1}次尝试成功）")
                clicked = True
                break
            except Exception:
                print(f"点击字幕按钮失败（第{attempt + 1}次尝试）")
                time.sleep(3)
        if not clicked:
            print("字幕按钮点击全部失败，直接扫描页面请求")

        # 新版播放器需在面板中选「中文（AI）」才会加载字幕轨
        time.sleep(1.5)
        _select_subtitle_language(driver_video)

        # 字幕轨在播放时才真正拉取，静音播放以触发（找到后立即暂停）
        try:
            driver_video.execute_script(
                "var v=document.querySelector('video');"
                "if(v){v.muted=true;var p=v.play();if(p&&p.catch)p.catch(function(){});}")
        except Exception:
            pass

        # 轮询等待字幕 JSON 请求（aisubtitle.hdslb.com），最多约 25 秒
        for _ in range(50):
            for request in driver_video.requests:
                if 'aisubtitle.hdslb.com' in request.url:
                    print(f"找到字幕请求URL: {request.url}")
                    try:
                        driver_video.execute_script(
                            "var v=document.querySelector('video');if(v)v.pause();")
                    except Exception:
                        pass
                    return request.url
            time.sleep(0.5)
        print(f"未找到字幕请求URL")
        return None
    except Exception as e:
        print(f"获取字幕URL失败：{str(e)}")
        return None
    finally:
        # 只有自己创建的浏览器实例才需要关闭
        if should_quit:
            driver_video.quit()

def run_bili_task(prefer_web: bool = True):
    """运行B站视频分析任务

    Args:
        prefer_web: 是否优先网页（浏览器）方式获取视频列表，默认 True。
            理由：api.bilibili.com 空间投稿接口需要 wbi 签名且风控严格，
            网页/浏览器方式更贴近真人访问、拿不到内容时自动回退 API 兜底。
    """
    pass

    current_date, date_reason, archive_folder = get_current_analysis_date()
    print_date_info()
    
    ensure_archive_folder(archive_folder)

    
    print("开始使用多线程并行获取UP主视频列表...")
    
    max_retries = 3
    all_videos = []
    
    for attempt in range(1, max_retries + 1):
        if prefer_web:
            print(f"使用网页（浏览器）方式获取视频列表（第{attempt}次尝试）")
            all_videos = timed("B站-视频列表(网页)", get_videos_by_selenium_threaded, UP_MIDS,
                               max_workers=1, group="B站-视频列表获取")
        else:
            print(f"使用API方式获取视频列表（第{attempt}次尝试）")
            all_videos = timed("B站-视频列表(API)", get_videos_by_api_threaded, UP_MIDS,
                               max_workers=1, group="B站-视频列表获取")
        
        print(f"总共获取到 {len(all_videos)} 个视频")
        
        if all_videos:
            print(f"视频列表获取成功")
            break
        else:
            if attempt < max_retries:
                print(f"第{attempt}次尝试未获取到视频，等待5秒后重试...")
                import time
                time.sleep(5)
            else:
                print(f"已尝试{max_retries}次，仍未获取到视频列表")

    # 当前方式连续失败时，自动切换另一种方式兜底重试一轮
    if not all_videos:
        print("\n当前方式未获取到视频，自动切换另一种方式兜底重试...")
        if prefer_web:
            print("使用API方式获取视频列表（兜底重试）")
            all_videos = timed("B站-视频列表(API兜底)", get_videos_by_api_threaded, UP_MIDS,
                               max_workers=1, group="B站-视频列表获取")
        else:
            print("使用网页（浏览器）方式获取视频列表（兜底重试）")
            all_videos = timed("B站-视频列表(网页兜底)", get_videos_by_selenium_threaded, UP_MIDS,
                               max_workers=1, group="B站-视频列表获取")
        print(f"兜底重试后总共获取到 {len(all_videos)} 个视频")
    
    if not all_videos:
        print("没有找到任何新视频，程序结束")
        return None
    
    # 使用多线程并行获取所有视频的字幕URL（优先网页/浏览器方式，API 兜底见 get_subtitle_urls_threaded）
    print("开始使用多线程并行获取视频字幕URL（网页优先）...")
    subtitle_results = timed("B站-字幕URL获取(5线程)", get_subtitle_urls_threaded,
                             all_videos, archive_folder, max_workers=5, prefer_web=True)
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
                        subtitle = timed(f"B站-字幕下载 {video['title'][:12]}",
                                         extract_subtitle_from_url, subtitle_url,
                                         group="B站-字幕下载")
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

            # UP主昵称在采集阶段就已随视频记录，缺失时才回头从正文猜
            up_name = video.get("up_name") or ""

            # 检查总结文件是否已存在
            summary_path = os.path.join(archive_folder, f"bili_{video['title']}_summary.txt")
            if os.path.exists(summary_path):
                print(f"视频《{video['title']}》总结已存在，跳过生成")
            else:
                print("使用deepseek总结")
                summary = timed(f"B站-视频总结 {video['title'][:12]}", deepseek_summary,
                    f"UP主：{up_name or '未知'}\n视频标题：{video['title']}\n\n{subtitle}",
                    sysprompt=(
                        "你是一位资深财经内容分析师，专注从B站财经UP主的视频稿中提炼投资价值。\n\n"
                        "分析框架：\n"
                        "1. 识别UP主的核心观点和论证逻辑（多/空/观望的理由是什么？）\n"
                        "2. 提取具体提到的行业板块、指数、个股及操作建议（买入/持有/减仓价位和时机）\n"
                        "3. 捕捉市场情绪信号（乐观/悲观/恐慌/贪婪的表述）\n"
                        "4. 标注信息来源的可信度（是数据驱动还是主观判断）\n\n"
                        "输出要求：\n"
                        "- 重点突出投资操作相关内容，弱化无关闲聊\n"
                        "- 用「核心观点」「行业研判」「操作建议」「风险提示」四大板块组织总结\n"
                        "- 对模糊表述保持审慎，明确指出哪些是确定信息、哪些是推测\n"
                        "- 总结开头标明UP主名字（素材中已给出），便于多UP主横向对比"
                    ),
                    userprompt=(
                        "请分析以下B站财经视频字幕，提炼投资相关信息：\n\n"
                    ),
                    reasoning_effort="medium"
                )
                print(f"视频《{video['title']}》总结：{summary[:100]}...")
                # 采集阶段没拿到名字时，才从总结/字幕正文里猜
                if not up_name:
                    up_name = identify_bili_up(summary) or identify_bili_up(subtitle) or "未知UP主"
                # 保存总结到归档文件夹（带UP主标注，供后续多UP主汇总时区分是谁说的）
                with open(summary_path, "w", encoding="utf-8") as f:
                    f.write(f"【UP主：{up_name}】\n{summary}")
                print(f"总结已保存到: {summary_path}")

                # 提取并保存该视频的预测观点
                record_predictions_from_advice(summary, "bili", up_name, current_date, archive_folder)
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
        sysprompt=(
            "你是一位拥有15年实战经验的宏观策略分析师，现任职于顶级对冲基金，"
            "每日从B站头部财经UP主的内容中提取市场共识与分歧信号。\n\n"
            "分析规则：\n"
            "1. 交叉验证原则：多个UP主共同提及的方向权重加倍，孤立观点需标注风险\n"
            "2. 反一致性原则：当所有UP主一致看多/看空时，必须单独列出反向风险\n"
            "3. 时效性原则：优先采纳基于最新政策/数据（24小时内）的观点\n"
            "4. 可操作性原则：所有建议必须有明确的触发条件（价格/时间/事件）\n\n"
            "输出严禁使用「可能」「或许」「不排除」等模糊词汇超过3次，每个判断必须有明确逻辑支撑。"
        ),
        userprompt=(
            "以下是近期多位B站财经UP主视频内容的分析总结（每条总结开头已标注UP主名字，"
            "请按UP主区分各自观点，不要混淆）：\n\n"
            "请按以下结构输出完整分析报告：\n\n"
            "【一、宏观市场定调】\n"
            "- 综合多位UP主观点，当前市场处于什么阶段（进攻/防守/观望）？依据是什么？\n"
            "- 多空力量对比：看多 vs 看空 vs 观望的UP主比例及核心论据\n\n"
            "【二、行业与板块轮动】\n"
            "- 被提及最多的3个行业板块，按共识度排序\n"
            "- 各板块的核心逻辑和潜在催化剂\n"
            "- 是否存在板块轮动信号？\n\n"
            "【三、具体操作策略】\n"
            "- 明确给出未来1-5个交易日的操作方向\n"
            "- 对每个建议标注：置信度（高/中/低）、触发条件、止损/止盈参考\n"
            "- 仓位建议（激进/中性/保守三种方案）\n\n"
            "【四、风险警示】\n"
            "- 当前最大的3个风险因素\n"
            "- 需要重点关注的日历事件（政策发布、数据公布等）\n"
            "- 什么情况下应果断离场？\n\n"
            "【五、标的清单（JSON）】\n"
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

    # 保存投资建议到归档文件夹
    advice_path = os.path.join(archive_folder, f"bili_投资建议_{current_date}.txt")
    with open(advice_path, "w", encoding="utf-8") as f:
        f.write(investment_advice)
    print(f"投资建议已保存到: {advice_path}")
    
    # 提取并保存B站整体投资建议的预测观点
    record_predictions_from_advice(investment_advice, "bili", "B站综合", current_date, archive_folder)
    
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
def get_subtitle_urls_threaded(videos: list, archive_folder: str, max_workers: int = 3, prefer_web: bool = True):
    """使用多线程并行获取多个视频的字幕URL，默认网页(浏览器)方式优先

    Args:
        videos: 视频列表
        archive_folder: 归档文件夹路径
        max_workers: 最大线程数
        prefer_web: 是否优先网页(浏览器)方式，默认 True。wbi/player 字幕接口
            风控严、需签名，网页方式在真实浏览器内点击字幕按钮抓包更稳；
            网页拿不到字幕时自动回退 yt-dlp+whisper 语音识别。
    """
    subtitle_results = []
    
    def process_video(video):
        """处理单个视频的字幕URL获取"""
        try:
            # 检查字幕文件是否已存在
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
            
            if prefer_web:
                # 网页(浏览器)方式：走模块级 get_subtitle_url_browser_fallback，
                # 该函数内部含完整回退链（登录失败/无字幕/异常 → yt-dlp+whisper）
                return get_subtitle_url_browser_fallback(bvid, video, archive_folder)
            else:
                # API 方式
                subtitle_url = get_subtitle_url_via_api(bvid)
                if subtitle_url:
                    print(f"视频《{video['title']}》API字幕URL获取成功")
                    return {
                        'video': video,
                        'subtitle_url': subtitle_url
                    }
                else:
                    print(f"视频《{video['title']}》API方式无字幕，尝试使用ytdlp+whisper方式")
                    # API方式失败时回退到yt-dlp+whisper方式
                    return generate_subtitle_with_ytdlp_whisper(bvid, video, archive_folder)
                
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
                
                # 时间戳转精确时间：datetime 用于过滤判断（保留时刻精度），字符串仅用于展示/归档
                created_dt = datetime.fromtimestamp(created_timestamp)
                created_date = created_dt.strftime('%Y-%m-%d %H:%M')
                
                # 构建视频URL
                video_url = f"https://www.bilibili.com/video/{bvid}"
                
                # 检查是否为限定小时内的视频（周末只收录周五收盘后发布的内容）
                if is_within_limit_hours(created_dt):
                    videos.append({
                        "title": title,
                        "url": video_url,
                        "date": created_date,
                        # 空间投稿接口的 author 字段即UP主昵称，缺失时用配置映射兜底
                        "up_name": video_data.get('author') or UP_NAMES.get(str(up_id), ""),
                        "up_id": up_id,
                        "bvid": bvid,
                        "aid": video_data.get('aid'),
                        "play": video_data.get('play', 0),
                        "comment": video_data.get('comment', 0)
                    })
                    print(f"已添加限定时间内视频: {title} ({created_date})")
                else:
                    print(f"跳过非限定时间内视频: {title} ({created_date})")
            
            print(f"API获取到 {len(videos)} 个限定时间内视频")
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
            '-x',  # 提取音频
            '--audio-format', 'wav',  # 转换为wav格式
            '--audio-quality', '0',  # 最高质量
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--add-header', 'Referer:https://www.bilibili.com',
            '--add-header', 'Origin:https://www.bilibili.com',
            '--ignore-errors',  # 忽略错误继续下载（处理充电视频）
            '--no-check-certificate',  # 不检查证书
            '--output', os.path.join(output_dir, '%(title)s.%(ext)s'),
        ]
        
        # 加载cookies文件（如果存在）
        if os.path.exists(COOKIE_PATH):
            # 将浏览器cookies转换为Netscape格式（yt-dlp需要的格式）
            try:
                with open(COOKIE_PATH, 'r', encoding='utf-8') as f:
                    cookies = json.load(f)
                
                # 确保输出目录存在
                os.makedirs(output_dir, exist_ok=True)
                
                # 创建临时cookies文件（Netscape格式）
                cookies_txt_path = os.path.join(output_dir, 'cookies.txt')
                with open(cookies_txt_path, 'w', encoding='utf-8') as f:
                    f.write("# Netscape HTTP Cookie File\n")
                    for cookie in cookies:
                        if 'name' in cookie and 'value' in cookie:
                            domain = cookie.get('domain', '.bilibili.com')
                            path = cookie.get('path', '/')
                            secure = 'TRUE' if cookie.get('secure', False) else 'FALSE'
                            # Netscape cookies 格式: domain domain_specified path secure expiration name value
                            # domain_specified: TRUE if domain starts with '.', FALSE otherwise
                            domain_specified = 'TRUE' if domain.startswith('.') else 'FALSE'
                            f.write(f"{domain}\t{domain_specified}\t{path}\t{secure}\t0\t{cookie['name']}\t{cookie['value']}\n")
                
                cmd.extend(['--cookies', cookies_txt_path])
                print("已加载cookies文件用于yt-dlp下载")
            except Exception as e:
                print(f"加载cookies失败: {str(e)}，将使用未登录状态下载")
        else:
            print(f"Cookie文件不存在: {COOKIE_PATH}，将使用未登录状态下载")
        
        cmd.append(video_url)
        
        # 尝试检测ffmpeg位置，如果失败则不指定路径
        try:
            import shutil
            if shutil.which('ffmpeg'):
                # ffmpeg在系统PATH中，无需额外配置
                pass
            else:
                # 尝试常见路径
                common_ffmpeg_paths = [
                    "D:\\Program Files\\MediaCoder\\codecs64\\ffmpeg.exe",
                    "C:\\ffmpeg\\bin\\ffmpeg.exe",
                    "C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe"
                ]
                for ffmpeg_path in common_ffmpeg_paths:
                    if os.path.exists(ffmpeg_path):
                        cmd.insert(1, '--ffmpeg-location')
                        cmd.insert(2, os.path.dirname(ffmpeg_path))
                        break
        except:
            pass
        
        print(f"开始下载音频: {video_url}")
        print(f"yt-dlp命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # 30分钟超时
        
        print(f"yt-dlp返回码: {result.returncode}")
        print(f"yt-dlp标准输出: {result.stdout[:500]}")  # 只显示前500字符避免日志过长
        if result.stderr:
            print(f"yt-dlp错误输出: {result.stderr[:500]}")
        
        if result.returncode != 0:
            # 如果是HTTP 412错误，尝试下载较低质量的版本（可能是预览版本）
            if 'HTTP Error 412' in result.stderr or 'HTTP Error 412' in result.stdout:
                print("检测到HTTP 412错误，尝试下载较低质量版本...")
                cmd_retry = cmd.copy()
                # 添加格式选择，优先选择较低质量
                cmd_retry.insert(1, '-f')
                cmd_retry.insert(2, 'worst[ext=mp4]/worst')
                
                print(f"重试命令: {' '.join(cmd_retry)}")
                result_retry = subprocess.run(cmd_retry, capture_output=True, text=True, timeout=1800)
                
                print(f"重试返回码: {result_retry.returncode}")
                if result_retry.stdout:
                    print(f"重试标准输出: {result_retry.stdout[:500]}")
                if result_retry.stderr:
                    print(f"重试错误输出: {result_retry.stderr[:500]}")
                
                if result_retry.returncode == 0:
                    # 解析重试输出获取文件路径
                    lines = result_retry.stdout.split('\n')
                    for line in lines:
                        if '[ExtractAudio] Destination:' in line:
                            file_path = line.split('Destination:')[-1].strip()
                            if os.path.exists(file_path):
                                print(f"音频下载成功（低质量版本）: {file_path}")
                                return file_path
            
            print(f"yt-dlp下载失败，返回码: {result.returncode}")
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

TRANSCRIBE_TIMEOUT = 1800  # 转写子进程最长等待秒数（2 小时音频实测约 105 秒）

# 转写模型挂在模块级全局上：CT2 模型析构会 abort（长音频必现），
# 放局部变量会在函数返回时立刻触发析构，所以必须持有到进程末尾由 os._exit 退出
_WHISPER_MODEL = None


def _load_whisper_model(model_cls, device: str, compute_type: str):
    """加载并持有 faster-whisper 模型（每个转写子进程只加载一次）"""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        print(f"加载faster-whisper模型 (设备: {device}, 计算类型: {compute_type})...")
        _WHISPER_MODEL = model_cls("small", device=device, compute_type=compute_type)
    return _WHISPER_MODEL


def _transcribe_worker(audio_path: str, output_dir: str) -> str:
    """真正执行 whisper 转写（只由子进程调用，入口见 transcribe_audio_with_whisper）

    Args:
        audio_path: 音频文件路径
        output_dir: 输出目录
        
    Returns:
        str: 生成的字幕文件路径
    """
    try:
        # 生成SRT字幕路径
        srt_path = os.path.join(output_dir, os.path.basename(audio_path).replace('.wav', '.srt'))
        
        # 检查字幕文件是否已存在
        if os.path.exists(srt_path):
            print(f"字幕文件已存在，跳过生成: {srt_path}")
            return srt_path
        
        # 检查是否安装了faster-whisper
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            print("未安装faster-whisper，请运行: pip install faster-whisper")
            return None
        
        # 检测GPU可用性并选择设备
        # 用 ctranslate2 自身探测：不引入 torch —— torch 会加载第二套 CUDA/cuDNN/OpenMP
        # 运行时（与 ctranslate2 各自的 libiomp5md/cudnn 同进程共存），多线程下争抢 GPU
        # 会触发原生崩溃（Windows 事件日志：python3.11.exe ucrtbase.dll 0xc0000409 直接 abort，无 Python 堆栈）
        try:
            import ctranslate2
            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            has_cuda = False
        if has_cuda:
            device = "cuda"
            compute_type = "float16"  # GPU上使用float16以获得更好性能
            print("检测到GPU可用，使用CUDA设备进行语音识别")
        else:
            device = "cpu"
            compute_type = "int8"
            print("未检测到GPU，使用CPU进行语音识别")
        
        # 加全局锁：faster-whisper/CT2 不支持多线程并发使用 GPU，会 native 崩溃
        with WHISPER_LOCK:
            model = _load_whisper_model(WhisperModel, device, compute_type)

            print(f"开始语音识别: {audio_path}")
            segments, info = model.transcribe(audio_path, beam_size=5, language="zh")

            with open(srt_path, 'w', encoding='utf-8') as f:
                for i, segment in enumerate(segments, 1):
                    # 转换时间格式
                    start_time = format_time(segment.start)
                    end_time = format_time(segment.end)

                    f.write(f"{i}\n")
                    f.write(f"{start_time} --> {end_time}\n")
                    f.write(f"{segment.text}\n\n")

            print(f"字幕生成成功: {srt_path}")

            # 只关闭生成器，不再显式销毁模型：del model + gc.collect() 会在 CUDA
            # 释放阶段触发原生 abort（长音频必现），交给 os._exit 跳过析构更安全
            try:
                close = getattr(segments, "close", None)
                if close:
                    close()
            except Exception:
                pass
            del segments, info

        return srt_path
        
    except Exception as e:
        print(f"语音识别异常: {e}")
        return None


def transcribe_audio_with_whisper(audio_path: str, output_dir: str) -> str:
    """使用faster-whisper生成字幕（在隔离子进程中执行转写）

    CT2 在 Windows 上销毁 CUDA 模型时会抛未处理 C++ 异常直接 abort 整个进程
    （事件日志 0xe06d7363 → ucrtbase 0xc0000409，无 Python 堆栈；长音频必现）。
    放到子进程里跑：最坏只损失这一个视频的字幕，不会中断整个分析任务。

    Args:
        audio_path: 音频文件路径
        output_dir: 输出目录

    Returns:
        str: 生成的字幕文件路径，失败返回 None
    """
    srt_path = os.path.join(output_dir, os.path.basename(audio_path).replace('.wav', '.srt'))
    if os.path.exists(srt_path):
        print(f"字幕文件已存在，跳过生成: {srt_path}")
        return srt_path
    if not os.path.exists(audio_path):
        print(f"音频文件不存在，跳过转写: {audio_path}")
        return None

    # 全局锁：同一时刻只允许一个转写子进程占用 GPU（多个 CT2 CUDA 上下文并发会崩）
    with WHISPER_LOCK:
        module_path = os.path.abspath(__file__)
        # -X utf8：强制子进程用 UTF-8 输出，避免管道按 GBK 编码导致日志乱码/解码异常
        cmd = [sys.executable, "-X", "utf8", "-u", module_path,
               "--transcribe", audio_path, output_dir]
        print(f"启动转写子进程: {os.path.basename(audio_path)}")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=os.path.dirname(module_path))
        except Exception as e:
            print(f"启动转写子进程失败: {e}")
            return None

        watchdog = threading.Timer(TRANSCRIBE_TIMEOUT, proc.kill)
        watchdog.start()
        finished = False
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    print(line)
                if "字幕生成成功" in line:
                    finished = True
            proc.wait()
        except Exception as e:
            print(f"读取转写子进程输出异常: {e}")
        finally:
            watchdog.cancel()
            try:
                proc.stdout.close()
            except Exception:
                pass

        # 子进程可能在写完字幕后的析构阶段崩溃退出，只要字幕已生成就算成功
        if finished and os.path.exists(srt_path):
            print(f"转写完成: {srt_path}（子进程退出码 {proc.returncode}）")
            return srt_path
        if os.path.exists(srt_path):
            print(f"转写子进程异常退出（退出码 {proc.returncode}），已删除不完整字幕")
            try:
                os.remove(srt_path)
            except Exception:
                pass
        else:
            print(f"转写失败（子进程退出码 {proc.returncode}），该视频按无字幕处理")
        return None

def format_time(seconds: float) -> str:
    """将秒数格式化为SRT时间格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}".replace('.', ',')

def extract_text_from_srt(srt_content: str) -> str:
    """从SRT字幕内容中提取纯文本，去除时间标签等无关元素
    
    Args:
        srt_content: SRT格式的字幕内容
        
    Returns:
        str: 提取的纯文本内容
    """
    lines = srt_content.strip().split('\n')
    text_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # 跳过空行
        if not line:
            i += 1
            continue
            
        # 跳过序号行（纯数字）
        if line.isdigit():
            i += 1
            continue
            
        # 跳过时间标签行（包含 -->）
        if '-->' in line:
            i += 1
            continue
            
        # 添加文本内容
        if line:
            text_lines.append(line)
            
        i += 1
    
    # 用换行符连接所有文本行
    return '\n'.join(text_lines)

def generate_subtitle_with_ytdlp_whisper(bvid: str, video: dict, archive_folder: str) -> dict:
    """使用yt-dlp+faster-whisper方式生成字幕
    
    Args:
        bvid: 视频BV号
        video: 视频信息字典
        archive_folder: 归档文件夹路径
        
    Returns:
        dict: 包含视频和字幕URL的信息
    """
    try:
        print(f"视频《{video['title']}》使用yt-dlp+whisper方式生成字幕")
        
        # 直接使用归档文件夹，不再使用临时目录
        audio_filename = f"bili_{video['title']}.wav"
        # 清理文件名中的非法字符
        audio_filename = "".join(c for c in audio_filename if c.isalnum() or c in (' ', '-', '_', '.')).rstrip()
        audio_path = os.path.join(archive_folder, audio_filename)
        
        # 下载音频到归档文件夹
        audio_path = download_video_with_ytdlp(video['url'], archive_folder)
        if not audio_path or not os.path.exists(audio_path):
            print(f"音频下载失败: {video['title']}")
            return None
        
        print(f"音频文件已保存到: {audio_path}")
        
        # 语音识别生成字幕
        srt_path = transcribe_audio_with_whisper(audio_path, archive_folder)
        if not srt_path or not os.path.exists(srt_path):
            print(f"字幕生成失败: {video['title']}")
            # 删除音频文件
            try:
                if os.path.exists(audio_path):
                    os.remove(audio_path)
                    print(f"已删除音频文件: {audio_path}")
            except Exception as delete_error:
                print(f"删除音频文件失败: {delete_error}")
            return None
        
        # 读取字幕内容
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
        
        # 提取纯文本内容，去除时间标签等无关元素
        subtitle_content = extract_text_from_srt(srt_content)
        
        print(f"字幕文件已保存到: {srt_path}")
        
        # 删除SRT文件（已提取内容，不再需要）
        try:
            if os.path.exists(srt_path):
                os.remove(srt_path)
                print(f"已删除SRT字幕文件: {srt_path}")
        except Exception as delete_error:
            print(f"删除SRT字幕文件失败: {delete_error}")
        
        # 删除音频文件，保留字幕文件
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
                print(f"处理完成，已删除音频文件: {audio_path}")
        except Exception as delete_error:
            print(f"删除音频文件失败: {delete_error}")
            
        # 返回结果（这里返回字幕内容而不是URL，因为是通过语音识别生成的）
        return {
            'video': video,
            'subtitle_content': subtitle_content,
            'subtitle_type': 'whisper_generated'
        }
            
    except Exception as e:
        print(f"yt-dlp+whisper字幕生成异常: {e}")
        # 确保在异常情况下也尝试删除音频文件
        try:
            if 'audio_path' in locals() and os.path.exists(audio_path):
                os.remove(audio_path)
                print(f"异常处理中已删除音频文件: {audio_path}")
        except Exception as cleanup_error:
            print(f"异常清理音频文件失败: {cleanup_error}")
        return None

def get_subtitle_url_browser_fallback(bvid, video, archive_folder: str = None):
    """浏览器方式获取字幕URL（备用方案）
    如果浏览器方式也失败，则使用yt-dlp+whisper方式生成字幕
    
    Args:
        bvid: 视频BV号
        video: 视频信息字典
        archive_folder: 归档文件夹路径
    """
    try:
        # 创建独立的浏览器实例
        driver = setup_browser()
        logged_in = login_and_save_cookie(driver)
        
        if not logged_in:
            print(f"视频 {video['title']} 登录失败")
            driver.quit()
            # 登录失败时尝试使用yt-dlp+whisper方式
            return generate_subtitle_with_ytdlp_whisper(bvid, video, archive_folder)
        
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
            return generate_subtitle_with_ytdlp_whisper(bvid, video, archive_folder)
    except Exception as e:
        print(f"视频《{video['title']}》浏览器方式获取字幕失败：{str(e)}，尝试yt-dlp+whisper方式")
        # 浏览器方式失败时使用yt-dlp+whisper方式
        return generate_subtitle_with_ytdlp_whisper(bvid, video, archive_folder)

if __name__ == "__main__":
    # 转写子进程入口：python bili_summary.py --transcribe <音频> <输出目录>
    if len(sys.argv) >= 4 and sys.argv[1] == "--transcribe":
        _transcribe_worker(sys.argv[2], sys.argv[3])
        sys.stdout.flush()
        sys.stderr.flush()
        # 直接退出，跳过解释器清理：CT2 销毁 CUDA 模型时会 abort（长音频必现），
        # 字幕此时已落盘，没必要冒崩溃风险
        os._exit(0)
    run_bili_task()
    
