
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
                    stop=None):
    
    DEEPSEEK_API_KEY = load_api_key_from_file()
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    extra_body = {}

    thinking_config = thinking if thinking is not None else MODEL_CONFIG.get("thinking")
    if thinking_config:
        extra_body["thinking"] = thinking_config

    effort = reasoning_effort if reasoning_effort is not None else MODEL_CONFIG.get("reasoning_effort")
    if effort:
        extra_body["reasoning_effort"] = effort

    response = client.chat.completions.create(
        model=model or MODEL_CONFIG["model"],
        messages=[
            {"role": "system", "content": sysprompt},
            {"role": "user", "content": f"{userprompt}{subtitle}"}
        ],
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

    return response.choices[0].message.content