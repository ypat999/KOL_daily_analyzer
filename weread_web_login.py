# -*- coding: utf-8 -*-
"""微信读书 web 端扫码登录，成功后保存 cookie 到 weread_web_cookies.json
（一次性操作：之后程序用 cookie 直连官方接口，不再需要平台/扫码）

注意：setup_browser() 每次都是全新的临时 Chrome profile（没有持久 user-data-dir），
浏览器里不会留有任何历史 cookie。所以本脚本必须**先拿已保存的 cookie 去服务端探测**，
仍有效就直接复用、跳过扫码；否则会落到"等你扫码"，表现为"每天都要重新登录"。
"""
import json, os, time, sys
from bili_summary import setup_browser

COOKIE_FILE = os.path.join("state", "weread_web_cookies.json")
LOGIN_OK = False


def _load_saved_cookies():
    if not os.path.exists(COOKIE_FILE):
        return []
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except Exception as e:
        print(f"读取已保存 cookie 失败(忽略): {e}")
        return []


def _probe_saved_auth():
    """用已保存 cookie 探测服务端登录态

    Returns:
        str: ok 正常 / verify 需人机验证（登录态本身有效）/ invalid 确实失效
             / None 探测异常（无法判断，按需扫码处理）
    """
    try:
        from wechat_weread import load_auth, probe_auth
        return probe_auth(load_auth())
    except Exception as e:
        print(f"登录态探测异常(按需扫码): {e}")
        return None


def _save_cookies(driver):
    """保存 weread 域下的 cookie（保留浏览器给的过期时间），并打印剩余有效期"""
    cookies = []
    for c in driver.get_cookies():
        if "weread.qq.com" not in (c.get("domain") or ""):
            continue
        item = {"name": c["name"], "value": c["value"],
                "domain": c.get("domain"), "path": c.get("path", "/")}
        if c.get("expiry"):
            item["expiry"] = c["expiry"]
        cookies.append(item)
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)
    print(f"已保存 {len(cookies)} 个 cookie 到 {COOKIE_FILE}")
    now = time.time()
    for c in cookies:
        if c.get("expiry"):
            print(f"  {c['name']}: 剩余 {(c['expiry'] - now) / 3600:.1f} 小时")
        else:
            print(f"  {c['name']}: 会话 cookie（浏览器不持久化，本程序按值长期复用）")
    return cookies


# ===== 第一步：已保存凭据仍有效就不必扫码（不启动浏览器）=====
_saved = _load_saved_cookies()
if _saved:
    # wr_skey 只有 ~1.5 小时寿命；先用 wr_rt 后台续期，避免过期就落到扫码
    try:
        from wechat_weread import renew_web_session
        renew_web_session()
    except Exception as e:
        print(f"自动续期异常(忽略): {e}")
_state = _probe_saved_auth() if _saved else None
if _state in ("ok", "verify"):
    print(f"已保存的登录态仍然有效（服务端探测:{_state}），无需扫码")
    sys.exit(0)

print(f"已保存凭据不可用（探测:{_state or '无凭据'}），启动浏览器扫码登录...")

# ===== 第二步：扫码登录 =====
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
    _save_cookies(driver)

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
