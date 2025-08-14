
from openai import OpenAI


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
                    sysprompt = "你是一个专业的内容总结助手，擅长对视频字幕进行简洁明了的总结。", 
                    userprompt = "请总结以下字幕内容："):
    
    DEEPSEEK_API_KEY = load_api_key_from_file()
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": sysprompt},
            {"role": "user", "content": f"{userprompt}{subtitle}"}
        ],
        stream=False
    )

    return response.choices[0].message.content