import os
import json
import time
import shutil
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


def _get_chrome_service():
    """获取Chrome Service，优先使用本地chromedriver，避免每次下载
    
    Returns:
        Service or None: Chrome服务对象，获取失败返回None
    """
    # 1. 尝试系统PATH中的chromedriver
    chromedriver_path = shutil.which("chromedriver")
    if chromedriver_path:
        try:
            return Service(chromedriver_path)
        except Exception:
            pass
    
    # 2. 尝试常见安装路径
    common_paths = [
        os.path.expanduser("~/.wdm/drivers/chromedriver"),
        r"C:\chromedriver\chromedriver.exe",
        r"C:\tools\chromedriver.exe",
    ]
    for p in common_paths:
        if os.path.exists(p):
            try:
                return Service(p)
            except Exception:
                pass
    
    # 3. 尝试webdriver_manager下载（可能因网络问题卡住）
    try:
        driver_path = ChromeDriverManager().install()
        return Service(driver_path)
    except Exception as e:
        print(f"下载chromedriver失败: {e}")
        return None

COOKIE_FILES = {
    "weibo": "weibo_cookies.json",
    "bili": "bili_cookies.json",
    "wechat": "wechat_cookies.json"
}

def check_cookie_exists(platform: str) -> bool:
    """检查指定平台的cookie文件是否存在且非空"""
    cookie_file = COOKIE_FILES.get(platform)
    if cookie_file and os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    return True
        except:
            pass
    return False

def validate_weibo_cookie() -> tuple:
    """验证微博cookie是否有效
    
    Returns:
        tuple: (is_valid: bool, message: str)
    """
    if not check_cookie_exists("weibo"):
        return False, "微博cookie文件不存在"
    
    try:
        with open(COOKIE_FILES["weibo"], 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        
        if not cookies:
            return False, "微博cookie文件为空"
        
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--ignore-certificate-errors")
        
        service = _get_chrome_service()
        if service is None:
            return False, "无法获取chromedriver，跳过cookie验证"
        
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
        
        try:
            try:
                driver.get("https://weibo.com/")
            except Exception:
                pass  # 超时也继续，页面可能已部分加载
            time.sleep(2)
            
            for cookie in cookies:
                try:
                    driver.add_cookie(cookie)
                except:
                    pass
            
            try:
                driver.refresh()
            except Exception:
                pass  # 超时也继续
            time.sleep(3)
            
            page_source = driver.page_source
            current_url = driver.current_url
            
            login_indicators = [
                "passport-login",
                "请登录",
                "sign-in",
                "login-form",
                "请先登录",
                "重新登录",
                "前方有点拥堵",
                "请登录后使用"
            ]
            
            for indicator in login_indicators:
                if indicator in page_source.lower() or indicator in page_source:
                    return False, f"微博cookie已失效（发现登录指示器: {indicator}）"
            
            if "weibo.com/login" in current_url.lower() or "passport.weibo.com" in current_url.lower():
                return False, "微博cookie已失效（跳转到登录页）"
            
            logged_in_indicators = [
                ('[class*="avatar"]', '头像'),
                ('[class*="user-info"]', '用户信息'),
                ('[class*="nickname"]', '昵称'),
                ('.woo-avatar-img', '微博头像'),
            ]
            
            for selector, name in logged_in_indicators:
                try:
                    elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements and len(elements) > 0:
                        return True, f"微博cookie有效（检测到登录元素: {name}）"
                except:
                    pass
            
            return False, "微博cookie状态未知，可能已失效"
            
        finally:
            driver.quit()
            
    except Exception as e:
        return False, f"验证微博cookie时出错: {str(e)}"

def validate_bili_cookie() -> tuple:
    """验证B站cookie是否有效
    
    Returns:
        tuple: (is_valid: bool, message: str)
    """
    if not check_cookie_exists("bili"):
        return False, "B站cookie文件不存在"
    
    try:
        with open(COOKIE_FILES["bili"], 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        
        if not cookies:
            return False, "B站cookie文件为空"
        
        sessdata = None
        for cookie in cookies:
            if cookie.get('name') == 'SESSDATA':
                sessdata = cookie.get('value')
                break
        
        if not sessdata:
            return False, "B站cookie缺少SESSDATA字段"
        
        api_url = "https://api.bilibili.com/x/web-interface/nav"
        cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        headers = {
            "Cookie": cookie_str,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(api_url, headers=headers, timeout=10)
        data = response.json()
        
        if data.get('code') == 0 and data.get('data', {}).get('isLogin'):
            username = data.get('data', {}).get('uname', '未知用户')
            return True, f"B站cookie有效（用户: {username}）"
        else:
            return False, "B站cookie已失效或未登录"
            
    except Exception as e:
        return False, f"验证B站cookie时出错: {str(e)}"

def validate_wechat_cookie() -> tuple:
    """验证微信cookie是否有效
    
    Returns:
        tuple: (is_valid: bool, message: str)
    """
    if not check_cookie_exists("wechat"):
        return False, "微信cookie文件不存在"
    
    try:
        with open(COOKIE_FILES["wechat"], 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        cookie = data.get("cookie", "")
        token = data.get("token", "")
        
        if not cookie or not token:
            return False, "微信cookie或token为空"
        
        test_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        params = {
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
            "action": "list_ex",
            "begin": "0",
            "count": "5",
            "type": "9",
        }
        
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(test_url, headers=headers, params=params, timeout=10)
        result = response.json()
        
        if result.get('base_resp', {}).get('ret') == 0:
            return True, "微信cookie有效"
        else:
            err_msg = result.get('base_resp', {}).get('err_msg', '未知错误')
            return False, f"微信cookie已失效（{err_msg}）"
            
    except Exception as e:
        return False, f"验证微信cookie时出错: {str(e)}"

def manual_login_weibo() -> bool:
    """手动登录微博并保存cookie
    
    Returns:
        bool: 登录是否成功
    """
    try:
        print("\n>>> 启动微博手动登录流程...")
        
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        
        service = _get_chrome_service()
        if service is None:
            return False, "无法获取chromedriver，跳过cookie验证"
        
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
        driver.set_window_size(1000, 800)
        
        try:
            driver.get("https://weibo.com/")
        except Exception:
            pass
        print("\n" + "="*50)
        print("请在浏览器中手动登录微博...")
        print("登录完成后，请按Enter键继续...")
        print("="*50 + "\n")
        input()
        
        time.sleep(3)
        
        cookies = driver.get_cookies()
        if cookies:
            with open(COOKIE_FILES["weibo"], 'w', encoding='utf-8') as f:
                json.dump(cookies, f)
            print("✓ 微博登录成功，cookie已保存")
            driver.quit()
            return True
        else:
            print("✗ 微博登录失败，未获取到cookie")
            driver.quit()
            return False
            
    except Exception as e:
        print(f"✗ 微博登录出错: {str(e)}")
        return False

def manual_login_bili() -> bool:
    """手动登录B站并保存cookie
    
    Returns:
        bool: 登录是否成功
    """
    try:
        print("\n>>> 启动B站手动登录流程...")
        
        options = Options()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        
        service = _get_chrome_service()
        if service is None:
            return False, "无法获取chromedriver，跳过cookie验证"
        
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(15)
        driver.set_script_timeout(10)
        driver.set_window_size(800, 600)
        
        try:
            driver.get("https://www.bilibili.com")
        except Exception:
            pass
        print("\n" + "="*50)
        print("请在浏览器中手动登录B站...")
        print("登录完成后，请按Enter键继续...")
        print("="*50 + "\n")
        input()
        
        time.sleep(3)
        
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.bili-avatar'))
            )
            cookies = driver.get_cookies()
            if cookies:
                with open(COOKIE_FILES["bili"], 'w', encoding='utf-8') as f:
                    json.dump(cookies, f)
                print("✓ B站登录成功，cookie已保存")
                driver.quit()
                return True
            else:
                print("✗ B站登录失败，未获取到cookie")
                driver.quit()
                return False
        except:
            print("B站登录状态验证失败，但cookie已保存")
            cookies = driver.get_cookies()
            if cookies:
                with open(COOKIE_FILES["bili"], 'w', encoding='utf-8') as f:
                    json.dump(cookies, f)
                driver.quit()
                return True
            driver.quit()
            return False
            
    except Exception as e:
        print(f"✗ B站登录出错: {str(e)}")
        return False

def manual_login_wechat() -> bool:
    """手动登录微信并保存cookie
    
    Returns:
        bool: 登录是否成功
    """
    try:
        print("\n>>> 启动微信手动登录流程...")
        
        try:
            from wechat_login import update_wechat_cookie
            new_cookie, new_token = update_wechat_cookie()
            
            if new_cookie and new_token:
                print("✓ 微信登录成功，cookie已保存")
                return True
            else:
                print("✗ 微信登录失败")
                return False
        except ImportError:
            print("✗ 未找到wechat_login模块，无法自动登录")
            print("请手动运行 wechat_login.py 或手动更新 wechat_cookies.json 文件")
            return False
        
    except Exception as e:
        print(f"✗ 微信登录出错: {str(e)}")
        return False

def validate_all_cookies() -> dict:
    """验证所有平台的cookie
    
    Returns:
        dict: 各平台的验证结果
    """
    results = {}
    
    print("\n" + "="*60)
    print("开始验证所有平台的cookie")
    print("="*60)
    
    print("\n[1/3] 验证微博cookie...")
    is_valid, message = validate_weibo_cookie()
    results["weibo"] = {"valid": is_valid, "message": message}
    print(f"    {message}")
    
    print("\n[2/3] 验证B站cookie...")
    is_valid, message = validate_bili_cookie()
    results["bili"] = {"valid": is_valid, "message": message}
    print(f"    {message}")
    
    print("\n[3/3] 验证微信cookie...")
    is_valid, message = validate_wechat_cookie()
    results["wechat"] = {"valid": is_valid, "message": message}
    print(f"    {message}")
    
    print("\n" + "="*60)
    print("cookie验证完成")
    print("="*60)
    
    return results

def perform_unified_login() -> dict:
    """执行统一登录流程
    
    先验证所有cookie，如果有失效的则提示用户手动登录
    
    Returns:
        dict: 各平台的登录结果
    """
    print("\n" + "="*60)
    print("开始统一登录流程")
    print("="*60)
    
    validation_results = validate_all_cookies()
    
    login_results = {}
    
    for platform, result in validation_results.items():
        login_results[platform] = result["valid"]
    
    need_login = [platform for platform, valid in login_results.items() if not valid]
    
    if need_login:
        print("\n以下平台需要重新登录:")
        for platform in need_login:
            print(f"  - {platform}: {validation_results[platform]['message']}")
        
        print("\n正在自动登录失效平台...")
        for platform in need_login:
            print(f"\n>>> 正在登录 {platform}...")
            
            if platform == "weibo":
                success = manual_login_weibo()
                login_results[platform] = success
            elif platform == "bili":
                success = manual_login_bili()
                login_results[platform] = success
            elif platform == "wechat":
                success = manual_login_wechat()
                login_results[platform] = success
    else:
        print("\n✓ 所有平台cookie均有效，无需重新登录")
    
    print("\n" + "="*60)
    print("统一登录流程完成")
    print("="*60)
    print(f"微博: {'✓ 已登录' if login_results['weibo'] else '✗ 登录失败'}")
    print(f"B站: {'✓ 已登录' if login_results['bili'] else '✗ 登录失败'}")
    print(f"微信: {'✓ 已登录' if login_results['wechat'] else '✗ 登录失败'}")
    
    return login_results

if __name__ == "__main__":
    results = perform_unified_login()
    print("\n最终登录状态:")
    for platform, valid in results.items():
        print(f"  {platform}: {'有效' if valid else '无效'}")
