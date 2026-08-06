import requests
import json
import time
import re
import urllib.parse
from seleniumwire import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from cookie_validator import _get_chrome_service

def update_wechat_cookie():
    """
    通过浏览器自动化登录微信公众号平台，获取新的cookie和token
    """
    print("开始微信公众号登录流程...")
    
    # 设置Chrome选项
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    try:
        # 初始化WebDriver
        service = _get_chrome_service()
        if service is None:
            print("无法获取chromedriver，请检查Chrome浏览器是否安装")
            return None
        
        driver = webdriver.Chrome(service=service, options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print("浏览器已启动")
        
        # 访问微信公众号登录页面
        driver.get("https://mp.weixin.qq.com/")
        print("已打开微信公众号平台登录页面")
        
        # 等待页面加载
        time.sleep(3)
        
        # 等待用户扫码登录
        print("请在浏览器中扫码登录微信公众号平台...")
        print("登录成功后，程序将自动提取cookie和token...")
        
        # 等待直到URL中包含token参数，表示登录成功
        WebDriverWait(driver, 300).until(
            lambda driver: 'token' in driver.current_url
        )
        
        print("检测到登录成功!")
        
        # 登录成功后，获取cookie
        selenium_cookies = driver.get_cookies()
        
        # 将selenium cookies转换为requests可用的格式
        cookie_string = ""
        for cookie in selenium_cookies:
            cookie_string += f"{cookie['name']}={cookie['value']}; "
        
        # 获取token参数
        parsed_url = urllib.parse.urlparse(driver.current_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        token = query_params.get('token', [''])[0]
        
        print(f"提取到token: {token}")
        
        # 关闭浏览器
        driver.quit()
        
        # 保存到文件
        cookie_data = {
            "cookie": cookie_string.strip(),
            "token": token
        }
        
        with open("wechat_cookies.json", "w", encoding="utf-8") as f:
            json.dump(cookie_data, f, ensure_ascii=False, indent=4)
        
        print("新的cookie和token已保存到 wechat_cookies.json 文件")
        return cookie_string.strip(), token
        
    except Exception as e:
        print(f"微信登录过程中出现错误: {e}")
        try:
            driver.quit()
        except:
            pass
        return None, None

def check_cookie_validity(cookie, token):
    """
    检查cookie和token是否有效
    
    两步检测：
    1. 检测 home 接口（登录态）
    2. 检测 appmsg 接口（频率限制）—— 即使登录态有效，appmsg 也可能被限流
    """
    try:
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        # 第一步：检测登录态
        home_url = "https://mp.weixin.qq.com/cgi-bin/home"
        params = {
            "t": "home/index",
            "lang": "zh_CN",
            "token": token
        }
        response = requests.get(home_url, headers=headers, params=params, timeout=10)
        if "登录" in response.text or "verify_code" in response.text:
            return False
        
        # 第二步：检测 appmsg 接口是否被频率限制
        appmsg_url = "https://mp.weixin.qq.com/cgi-bin/appmsg"
        appmsg_params = {
            "token": token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": "1",
            "action": "list_ex",
            "begin": "0",
            "count": "1",
            "query": "",
            "type": "9"
        }
        appmsg_resp = requests.get(appmsg_url, headers=headers, params=appmsg_params, timeout=10)
        try:
            resp_json = appmsg_resp.json()
            err_msg = resp_json.get("base_resp", {}).get("err_msg", "")
            if "freq control" in str(err_msg).lower():
                print("    警告: 微信appmsg接口被频率限制（ret=200013），登录态有效但无法获取文章")
                print("    该限流绑定账号，换token无效，需等待24小时恢复")
                return False
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"检查cookie有效性时出错: {e}")
        return False

if __name__ == "__main__":
    # 运行登录流程
    update_wechat_cookie()