import os
import json
import time
import shutil
import subprocess
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ===== 微信任务总开关 =====
# 微信抓取已切换到微信读书桥接版（wechat_weread.py，普通微信号扫码，
# 与 mp 后台 appmsg 接口隔离）。开关统一由 wechat_weread.py 的 WECHAT_ENABLED 控制，
# 本文件不再硬编码，避免与主流程开关不一致。
from wechat_weread import WECHAT_ENABLED


def _get_chrome_major_version():
    """获取Chrome浏览器的主版本号
    
    Returns:
        int or None: Chrome主版本号，获取失败返回None
    """
    import winreg
    for hive, key_path in [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
    ]:
        try:
            key = winreg.OpenKey(hive, key_path)
            version, _ = winreg.QueryValueEx(key, "version")
            winreg.CloseKey(key)
            return int(version.split(".")[0])
        except Exception:
            pass
    return None


def _cleanup_orphan_chrome():
    """清理自动化残留的chrome进程（scoped_dir临时配置目录）

    多次崩溃后 chromedriver 会残留 chrome 实例，堆积过多会干扰新会话创建
    （session not created / chrome not reachable）。只清理 scoped_dir 临时
    配置的实例，不影响用户正常浏览器。
    """
    try:
        subprocess.run(
            "powershell -NoProfile -Command "
            "\"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -like '*scoped_dir*' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }\"",
            timeout=30, capture_output=True
        )
    except Exception:
        pass


def _get_chrome_service():
    """获取Chrome Service，优先使用本地chromedriver，避免每次下载

    Returns:
        Service or None: Chrome服务对象，获取失败返回None
    """
    _cleanup_orphan_chrome()
    chrome_major = _get_chrome_major_version()
    if chrome_major:
        print(f"检测到Chrome浏览器版本: {chrome_major}")
    
    # 1. 尝试系统PATH中的chromedriver
    chromedriver_path = shutil.which("chromedriver")
    if chromedriver_path:
        try:
            return Service(chromedriver_path)
        except Exception:
            pass
    
    # 2. 搜索webdriver_manager缓存目录（.wdm），优先匹配Chrome主版本号
    wdm_dir = os.path.join(os.path.expanduser("~"), ".wdm", "drivers", "chromedriver", "win64")
    if os.path.exists(wdm_dir):
        # 按版本号降序排列，优先使用最新版
        try:
            versions = sorted(os.listdir(wdm_dir), reverse=True)
        except Exception:
            versions = []
        for ver in versions:
            # 检查缓存版本与Chrome浏览器主版本是否匹配
            if chrome_major:
                try:
                    cached_major = int(ver.split(".")[0])
                    if cached_major != chrome_major:
                        continue  # 跳过版本不匹配的缓存
                except Exception:
                    continue
            # 优先找子目录中的chromedriver.exe
            ver_dir = os.path.join(wdm_dir, ver)
            for root, dirs, files in os.walk(ver_dir):
                for f in files:
                    if f.lower() == "chromedriver.exe":
                        exe_path = os.path.join(root, f)
                        try:
                            return Service(exe_path)
                        except Exception:
                            pass
    
    # 3. 尝试常见安装路径
    common_paths = [
        r"C:\chromedriver\chromedriver.exe",
        r"C:\tools\chromedriver.exe",
    ]
    for p in common_paths:
        if os.path.exists(p):
            try:
                return Service(p)
            except Exception:
                pass
    
    # 4. 尝试webdriver_manager下载（可能因网络问题卡住）
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
        # 稳定性参数：避免 Chrome 启动时 GPU/沙箱/共享内存异常导致 session not created
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-background-networking")
        options.add_argument("--log-level=3")
        
        service = _get_chrome_service()
        if service is None:
            return False, "无法获取chromedriver，跳过cookie验证"
        
        # 会话创建偶发 chrome not reachable，重试一次
        driver = None
        for _attempt in range(2):
            try:
                driver = webdriver.Chrome(service=service, options=options)
                break
            except Exception as e:
                print(f"  创建Chrome会话失败({_attempt+1}/2): {str(e)[:120]}")
                time.sleep(3)
        if driver is None:
            return False, "创建Chrome会话失败（chrome not reachable）"
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

def validate_weread_auth() -> tuple:
    """验证微信读书登录凭据（微信任务新链路，替代旧 mp 后台 cookie）

    实际请求平台校验 token 有效性，而非仅检查文件存在：
    失效时返回 False，触发 perform_unified_login 自动重新扫码登录。

    Returns:
        tuple: (is_valid: bool, message: str)
    """
    try:
        from wechat_weread import load_auth, verify_auth, AUTH_FILE
    except ImportError:
        return False, "未找到wechat_weread模块"
    if not os.path.exists(AUTH_FILE):
        return False, "微信读书凭据文件不存在，需运行 wechat_weread 扫码登录"
    try:
        auth = load_auth()
    except Exception as e:
        return False, f"读取微信读书凭据出错: {e}"
    # 真实鉴权走与抓取同源同强度的 /web/mp/articles 接口（严格校验 wr_skey）；
    # 老凭据文件 token 可能为空串，只看 vid
    if not auth or not auth.get("vid"):
        return False, "微信读书凭据无效（缺少 vid）"
    status = verify_auth(auth)
    if status is False:
        return False, "微信读书凭据已失效（token过期），需重新扫码登录"
    if status is None:
        return True, "微信读书凭据存在（网络异常，有效性待验证）"
    return True, "微信读书凭据有效"

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

def manual_login_weread() -> bool:
    """微信读书扫码登录（微信任务新链路，替代旧 mp 后台登录）

    Returns:
        bool: 登录是否成功
    """
    try:
        from wechat_weread import login_weread
    except ImportError:
        print("✗ 未找到wechat_weread模块，无法自动登录")
        return False
    try:
        auth = login_weread()
        # 真伪最终由 notify 接口校验；vid 缺失才视为登录失败
        if auth and auth.get("vid"):
            print("✓ 微信读书登录成功，凭据已保存")
            return True
        print("✗ 微信读书登录失败")
        return False
    except Exception as e:
        print(f"✗ 微信读书登录出错: {str(e)}")
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
    if not WECHAT_ENABLED:
        results["wechat"] = {"valid": True, "message": "微信任务已禁用，跳过验证"}
        print("    微信任务已禁用，跳过验证")
    else:
        is_valid, message = validate_weread_auth()
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
                success = manual_login_weread()
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
