import json
import os

PUSH_CONFIG_FILE = os.path.join("config", "push_config.json")


def load_push_config():
    """读取推送配置 push_config.json

    遵循项目文件存配置的约定（参考 deepseek_api_key.txt 模式）。

    Returns:
        dict 或 None: 配置字典；文件不存在/token为空/enabled=False 时返回 None
    """
    if not os.path.exists(PUSH_CONFIG_FILE):
        return None

    try:
        with open(PUSH_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        if not cfg.get("enabled", False):
            return None

        token = (cfg.get("pushplus_token") or "").strip()
        if not token:
            return None

        return cfg
    except Exception as e:
        print(f"读取推送配置失败: {e}")
        return None


def _send_pushplus(token, title, content, template="markdown", max_retries=1):
    """通过 PushPlus 推送

    Args:
        token: PushPlus token
        title: 消息标题
        content: 消息内容
        template: 模板类型（markdown / html / txt），
                  纯文本内容（如 JSON）用 txt（PushPlus 合法模板，text 不存在）
        max_retries: 失败重试次数（仅对瞬时错误重试；实名用户 1 分钟限 5 次请求，
                     所以总请求数需控制）

    Returns:
        tuple: (ok: bool, msg: str)
    """
    import time as _time

    import requests

    url = "https://www.pushplus.plus/send"
    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": template,
    }

    last_msg = ""
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(url, json=data, timeout=15)
            result = resp.json()
        except Exception as e:
            last_msg = f"网络/解析错误: {e}"
        else:
            code = result.get("code")
            if code == 200:
                return True, "推送成功"
            # 999 = 服务端验证错误，具体原因在 data 里；500 = 系统异常，二者可能瞬时
            detail = result.get("data")
            last_msg = f"code={code} {result.get('msg', '未知错误')}"
            if detail:
                last_msg += f"（{detail}）"
            if code not in (500, 999):
                return False, last_msg

        if attempt < max_retries:
            delay = 3
            print(f"  推送失败（{last_msg}），{delay} 秒后重试 {attempt + 1}/{max_retries}")
            _time.sleep(delay)

    return False, last_msg


def push_to_wechat(title, content, template="markdown"):
    """推送消息到微信（PushPlus）

    未配置或 token 为空时优雅跳过（打印提示，不报错），符合项目
    "每任务独立异常、不影响主流程" 的约定。

    Args:
        title: 消息标题
        content: 消息内容
        template: 模板类型（markdown / html / txt）

    Returns:
        tuple: (ok: bool, msg: str)。未配置时返回 (False, "未配置")
    """
    cfg = load_push_config()

    if cfg is None:
        print(
            "未配置微信推送，跳过。如需启用：\n"
            "  1. 关注「pushplus推送加」公众号，登录 pushplus.plus 获取 token\n"
            f"  2. 创建 {PUSH_CONFIG_FILE}:\n"
            '     {"channel":"pushplus","pushplus_token":"你的token","enabled":true}'
        )
        return False, "未配置"

    token = (cfg.get("pushplus_token") or "").strip()

    # 长文本兜底：超长截断并提示（综合建议通常 <1万字，此处兜底极端情况）
    MAX_LEN = 30000
    if len(content) > MAX_LEN:
        content = content[:MAX_LEN] + "\n\n...(内容过长已截断，完整内容见本地 综合投资建议 文件)"

    return _send_pushplus(token, title, content, template)


if __name__ == "__main__":
    # 手动测试: python wechat_push.py "标题" "内容"
    import sys
    test_title = sys.argv[1] if len(sys.argv) > 1 else "测试推送"
    test_content = sys.argv[2] if len(sys.argv) > 2 else "# 测试标题\n这是来自 wechat_push 的测试内容。"
    ok, msg = push_to_wechat(test_title, test_content)
    print(f"结果: ok={ok}, msg={msg}")
