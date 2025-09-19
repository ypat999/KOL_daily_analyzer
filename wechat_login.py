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
        service = Service(ChromeDriverManager().install())
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
    """
    try:
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        # 测试请求
        url = "https://mp.weixin.qq.com/cgi-bin/home"
        params = {
            "t": "home/index",
            "lang": "zh_CN",
            "token": token
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        # 如果返回登录页面，说明cookie失效
        if "登录" in response.text and "verify_code" in response.text:
            return False
        return True
    except Exception as e:
        print(f"检查cookie有效性时出错: {e}")
        return False

if __name__ == "__main__":
    # 运行登录流程
    update_wechat_cookie()