#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查微信读书账号对配置中各公众号的拉取状态（基于书架订阅列表归类）

用法: python check_weread_follows.py
- 未订阅     → 该号不在当前账号书架里，需先在手机微信读书 App 关注
- 限频/异常  → 书架已订阅但接口暂不可用（瞬时风控），等几分钟重跑
- 已订阅     → 正常，能拉到文章列表
"""
import json
import time

import requests

WEB_COOKIE_FILE = "weread_web_cookies.json"
CONFIG_FILE = "wechat_weread_accounts.json"
MPID_FILE = "weread_mpids.json"

cookies = {c["name"]: c["value"] for c in json.load(open(WEB_COOKIE_FILE, encoding="utf-8"))}
hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
       "Referer": "https://weread.qq.com/"}

accounts = json.load(open(CONFIG_FILE, encoding="utf-8")).get("accounts", [])
mpids = json.load(open(MPID_FILE, encoding="utf-8"))

# 书架订阅列表（区分 -2041 的两种成因）
try:
    r = requests.get("https://weread.qq.com/web/shelf/sync",
                     params={"synckey": 0, "listType": 1}, headers=hdr, cookies=cookies, timeout=15)
    subscribed = {b.get("bookId") for b in (r.json().get("books") or []) if b.get("type") == 3}
except Exception as e:
    print(f"拉取书架订阅失败: {e!r}")
    subscribed = None

print("检查公众号状态（-2041 按书架订阅区分未订阅/限频）...\n")
for acc in accounts:
    name = acc.get("name", "")
    book = mpids.get(name, "")
    if not book:
        print(f"[无bookId] {name}")
        continue
    try:
        r = requests.get("https://weread.qq.com/web/mp/articles",
                         params={"bookId": book, "offset": 0},
                         headers=hdr, cookies=cookies, timeout=15)
        d = r.json()
        n = len(d.get("reviews") or [])
        code = d.get("errCode")
        if code == -2041:
            if subscribed is not None and book not in subscribed:
                print(f"[未订阅] {name}  <- 手机微信读书 App 关注后再跑")
            else:
                print(f"[限频/异常] {name}（书架已订阅仍 -2041，等几分钟重跑）")
        elif code:
            print(f"[异常]   {name}: errCode={code} {d.get('errMsg')}")
        else:
            print(f"[已订阅] {name}: 文章数={n}")
    except Exception as e:
        print(f"[异常]   {name}: {e!r}")
    time.sleep(2.5)
print("\n完成")
