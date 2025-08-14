
from openai import OpenAI




def deepseek_summary(subtitle, 
                    sysprompt = "你是一个专业的内容总结助手，擅长对视频字幕进行简洁明了的总结。", 
                    userprompt = "请总结以下字幕内容："):
    DEEPSEEK_API_KEY = "sk-3cf3bde544ae4589b5810f49e92117ea"
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