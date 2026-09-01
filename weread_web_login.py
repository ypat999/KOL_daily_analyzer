# -*- coding: utf-8 -*-
"""微信读书 web 端扫码登录，成功后保存 cookie 到 weread_web_cookies.json
（一次性操作：之后程序用 cookie 直连官方接口，不再需要平台/扫码）"""
import json, time, sys
from bili_summary import setup_browser

COOKIE_FILE = "weread_web_cookies.json"
LOGIN_OK = False

driver = setup_browser()
try:
    driver.get("https://weread.qq.com")
    time.sleep(5)

    def has_skey():
        return any(c.get("name") == "wr_skey" and c.get("value") for c in driver.get_cookies())

    if has_skey():
        print("已检测到登录态（wr_skey 存在），无需扫码")
        LOGIN_OK = True
    else:
        # 尝试点击"登录"按钮弹出扫码框
        try:
            btns = driver.find_elements("xpath", "//*[contains(text(),'登录') or contains(text(),'扫码')]")
            for b in btns:
                try:
                    if b.is_displayed() and b.is_enabled():
                        b.click()
                        print("已点击登录入口，请在 Chrome 窗口弹出的二维码用手机微信扫码")
                        break
                except Exception:
                    continue
        except Exception as e:
            print(f"点击登录入口异常(忽略): {e}")

        print("等待扫码登录（最长 5 分钟）...")
        for i in range(300):
            if has_skey():
                LOGIN_OK = True
                break
            if i % 10 == 0 and i > 0:
                print(f"  ...已等待 {i * 2}s")
            time.sleep(2)

    if not LOGIN_OK:
        print("扫码超时，未获得登录态")
        sys.exit(1)

    # 等页面完全进入登录态
    time.sleep(5)
    cookies = []
    for c in driver.get_cookies():
        if "weread.qq.com" in c.get("domain", ""):
            cookies.append({
                "name": c["name"], "value": c["value"],
                "domain": c.get("domain"), "path": c.get("path", "/"),
            })
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print(f"登录成功！已保存 {len(cookies)} 个 cookie 到 {COOKIE_FILE}")
    print("cookie 名称:", [c["name"] for c in cookies])

    # 顺带记录首页可点击的导航项，帮助确认公众号入口
    try:
        time.sleep(3)
        navs = driver.find_elements("xpath", "//a | //div[@role='tab'] | //li")
        text = set()
        for n in navs:
            try:
                t = n.text.strip()
                if t and len(t) <= 8 and any(k in t for k in ["公众号", "书架", "书城", "我的", "发现"]):
                    text.add(t)
            except Exception:
                continue
        print("页面导航关键词:", sorted(text)[:20])
        print("当前URL:", driver.current_url)
    except Exception as e:
        print(f"导航探测异常: {e}")

    driver.quit()
except Exception as e:
    print("ERR:", e)
    try:
        driver.quit()
    except Exception:
        pass
    sys.exit(1)
