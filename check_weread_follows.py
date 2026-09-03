#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查微信读书账号对配置中各公众号的「已关注」状态（哪些能拉到文章，哪些 -2041 未关注）

用法: python check_weread_follows.py
在手机微信读书 App 关注一批公众号后运行，可确认还差哪些。
"""
import json
import sys
import time

import requests

WEB_COOKIE_FILE = "weread_web_cookies.json"
CONFIG_FILE = "wechat_weread_accounts.json"
MPID_FILE = "weread_mpids.json"

cookies = json.load(open(WEB_COOKIE_FILE, encoding="utf-8"))
jar = {c["name"]: c["value"] for c in cookies}
hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
       "Referer": "https://weread.qq.com/"}

accounts = json.load(open(CONFIG_FILE, encoding="utf-8")).get("accounts", [])
mpids = json.load(open(MPID_FILE, encoding="utf-8"))

print("检查公众号关注状态（-2041 = 未关注/无权限）...\n")
for acc in accounts:
    name = acc.get("name", "")
    book = mpids.get(name, "")
    if not book:
        print(f"[跳过] {name}: 无 bookId")
        continue
    try:
        r = requests.get("https://weread.qq.com/web/mp/articles",
                         params={"bookId": book, "offset": 0},
                         headers=hdr, cookies=jar, timeout=15)
        d = r.json()
        n = len(d.get("reviews") or [])
        code = d.get("errCode")
        if code == -2041:
            print(f"[未关注] {name}  <- 手机微信读书 App 关注后再跑")
        elif code:
            print(f"[异常]   {name}: errCode={code} {d.get('errMsg')}")
        else:
            print(f"[已关注] {name}: 文章数={n}")
    except Exception as e:
        print(f"[异常]   {name}: {e!r}")
    time.sleep(1)
print("\n完成")
