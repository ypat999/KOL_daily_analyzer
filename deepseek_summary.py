
from openai import OpenAI

MODEL_CONFIG = {
    "model": "deepseek-v4-pro",
    "temperature": 0.2,
    "max_tokens": 32768,
    "top_p": 0.95,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.1,
    "thinking": {"type": "enabled"},
    "reasoning_effort": "max",
    "response_format": {"type": "text"},
}

DEFAULT_SYSPROMPT = (
    "你是一个经验丰富的宏观对冲基金分析师，拥有15年全球金融市场投资经验。"
    "你擅长从海量碎片化信息中提炼关键信号，识别市场情绪拐点、资金流向变化和政策预期差。"
    "你的分析框架覆盖：宏观周期定位、行业景气度比较、资金面博弈分析、技术面信号验证。"
    "你始终保持理性、客观、审慎，用数据和逻辑说话，避免情绪化判断。"
)

DEFAULT_USERPROMPT = "请基于以下信息进行深度分析，提炼核心观点和可操作的投资洞察："


def load_api_key_from_file():
    """从deepseek_api_key.txt文件读取key值"""
    try:
        with open("deepseek_api_key.txt", "r", encoding="utf-8") as f:
            cookie = f.read().strip()
            if not cookie:
                print("警告: deepseek_api_key.txt文件为空")
                return ""
            return cookie
    except FileNotFoundError:
        print("错误: 未找到deepseek_api_key.txt文件")
        return ""
    except Exception as e:
        print(f"读取key文件时出错: {e}")
        return ""

def deepseek_summary(subtitle, 
                    sysprompt=DEFAULT_SYSPROMPT, 
                    userprompt=DEFAULT_USERPROMPT,
                    model=None,
                    temperature=None,
                    max_tokens=None,
                    top_p=None,
                    thinking=None,
                    reasoning_effort=None,
                    response_format=None,
                    stop=None,
                    max_continue_rounds=2):
    
    DEEPSEEK_API_KEY = load_api_key_from_file()
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    thinking_config = thinking if thinking is not None else MODEL_CONFIG.get("thinking")
    effort = None if (thinking_config and thinking_config.get("type") == "disabled") \
        else (reasoning_effort if reasoning_effort is not None else MODEL_CONFIG.get("reasoning_effort"))

    messages = [
        {"role": "system", "content": sysprompt},
        {"role": "user", "content": f"{userprompt}{subtitle}"}
    ]

    def _call(msgs, think_cfg, reason_effort):
        extra_body = {}
        if think_cfg:
            extra_body["thinking"] = think_cfg
        if reason_effort:
            extra_body["reasoning_effort"] = reason_effort
        resp = client.chat.completions.create(
            model=model or MODEL_CONFIG["model"],
            messages=msgs,
            temperature=temperature if temperature is not None else MODEL_CONFIG["temperature"],
            max_tokens=max_tokens or MODEL_CONFIG["max_tokens"],
            top_p=top_p if top_p is not None else MODEL_CONFIG["top_p"],
            frequency_penalty=MODEL_CONFIG["frequency_penalty"],
            presence_penalty=MODEL_CONFIG["presence_penalty"],
            stop=stop,
            response_format=response_format if response_format is not None else MODEL_CONFIG.get("response_format"),
            extra_body=extra_body if extra_body else None,
            stream=False
        )
        choice = resp.choices[0]
        det = getattr(getattr(resp, "usage", None), "completion_tokens_details", None)
        rt = getattr(det, "reasoning_tokens", None) if det else None
        return (choice.message.content or ""), choice.finish_reason, getattr(resp, "usage", None), rt

    content, finish_reason, usage, reasoning_tokens = _call(messages, thinking_config, effort)

    # 输出触达长度上限时静默截断（finish_reason=length），thinking/reasoning 会占用大量
    # 输出预算，导致正文写到一半就被砍。此处自动续写补齐剩余章节。
    rounds = 0
    while finish_reason == "length" and rounds < max_continue_rounds:
        rounds += 1
        comp = getattr(usage, "completion_tokens", "?") if usage else "?"
        print(f"⚠️ 模型输出触达 max_tokens 被截断（第{rounds}次续写）：正文 {len(content)} 字符，"
              f"completion_tokens={comp}（其中 reasoning_tokens={reasoning_tokens}）")
        cont_messages = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": (
                "你的上一段输出因长度限制被截断。请严格紧接最后已输出的文字继续写完剩余章节，"
                "禁止重复任何已输出内容，禁止重新起头或添加前言，直接从断点的下一个字开始写。"
            )}
        ]
        # 续写关闭思考链，把输出预算全部留给正文，避免再次被推理占满
        more, finish_reason, usage, reasoning_tokens = _call(cont_messages, {"type": "disabled"}, None)
        if not more:
            print("⚠️ 续写返回为空，停止续写")
            break
        content += more

    if finish_reason == "length":
        print(f"❌ 续写{rounds}次后仍被截断，正文不完整（{len(content)}字符）。"
              f"请调高 max_tokens 或降低 reasoning_effort。")

    return content


def deepseek_chat(messages, model=None, temperature=None, max_tokens=None, stream=True):
    """多轮对话接口
    
    Args:
        messages: 消息列表 [{"role": "system"/"user"/"assistant", "content": "..."}]
        model: 模型名称
        temperature: 温度
        max_tokens: 最大token数
        stream: 是否流式输出
    
    Returns:
        str: 助手回复内容
    """
    DEEPSEEK_API_KEY = load_api_key_from_file()
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    response = client.chat.completions.create(
        model=model or MODEL_CONFIG["model"],
        messages=messages,
        temperature=temperature if temperature is not None else 0.3,
        max_tokens=max_tokens or 8192,
        top_p=MODEL_CONFIG["top_p"],
        frequency_penalty=MODEL_CONFIG["frequency_penalty"],
        presence_penalty=MODEL_CONFIG["presence_penalty"],
        stream=stream,
    )

    if not stream:
        return response.choices[0].message.content

    # 流式输出
    full_content = ""
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            print(text, end="", flush=True)
            full_content += text

    print()  # 换行
    return full_content