import json
import requests

def extract_content_to_txt(json_file, output_file):
    # 读取JSON文件
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # 提取所有content内容
    contents = []
    for item in data['body']:
        contents.append(item['content'])
    
    # 将内容写入txt文件
    with open(output_file, 'w', encoding='utf-8') as f:
        for content in contents:
            f.write(content + '\n')

def extract_subtitle_from_url(subtitle_url):
    # 从URL获取JSON数据
    try:
        response = requests.get(subtitle_url)
        response.raise_for_status()  # 检查请求是否成功
        data = response.json()
        
        # 提取所有content内容
        contents = []
        for item in data['body']:
            contents.append(item['content'])
        
        # 将内容连接成字符串
        subtitle = '\n'.join(contents)
        return subtitle
    except Exception as e:
        print(f"从URL提取字幕失败: {str(e)}")
        return None

if __name__ == '__main__':
    json_file = 'BV1uj3uzXELB.json'  # JSON文件路径
    output_file = 'BV1uj3uzXELB.txt'  # 输出文件路径
    
    try:
        extract_content_to_txt(json_file, output_file)
        print(f"内容已成功提取并保存到 {output_file}")
    except Exception as e:
        print(f"发生错误: {str(e)}")