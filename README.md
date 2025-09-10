# KOL 每日分析工具集

本项目是一个综合性的KOL（关键意见领袖）内容分析工具，能够自动提取、分析和总结B站财经UP主视频和微信公众号文章内容，生成投资建议报告。

## 目录结构
- `kol_analyzer.py`: 主程序入口，协调所有分析任务并合并投资建议
- `bili_summary.py`: 提取B站UP主18小时内视频并生成投资建议
- `wechat_get.py`: 提取微信公众号18小时内文章并生成投资建议
- `deepseek_summary.py`: 使用DeepSeek API进行AI文本总结和分析
- `extract_subtitle.py`: 提取B站视频字幕内容
- `requirements.txt`: 项目依赖包列表
- `bili_cookies.json`: B站登录凭证配置文件
- `wechat_cookies.txt`: 微信公众号登录凭证配置文件
- `deepseek_api_key.txt`: DeepSeek API密钥配置文件
- `archive_YYYY-MM-DD/`: 按日期归档的分析结果文件夹

## 配置文件说明

### 1. B站Cookie配置文件 (`bili_cookies.json`)

#### 文件内容结构
```json
[
  {
    "domain": ".bilibili.com",
    "expiry": 1234567890,
    "httpOnly": true,
    "name": "SESSDATA",
    "path": "/",
    "secure": true,
    "value": "your_sessdata_value_here"
  },
  {
    "domain": ".bilibili.com",
    "expiry": 1234567890,
    "httpOnly": false,
    "name": "bili_jct",
    "path": "/",
    "secure": false,
    "value": "your_bili_jct_value_here"
  },
  {
    "domain": ".bilibili.com",
    "expiry": 1234567890,
    "httpOnly": false,
    "name": "DedeUserID",
    "path": "/",
    "secure": false,
    "value": "your_user_id_here"
  },
  {
    "domain": ".bilibili.com",
    "expiry": 1234567890,
    "httpOnly": false,
    "name": "DedeUserID__ckMd5",
    "path": "/",
    "secure": false,
    "value": "your_md5_hash_here"
  },
  {
    "domain": ".bilibili.com",
    "expiry": 1234567890,
    "httpOnly": false,
    "name": "sid",
    "path": "/",
    "secure": false,
    "value": "your_sid_value_here"
  }
]
```

#### 获取方式
1. **自动获取（推荐）**：
   - 首次运行`bili_summary.py`时，程序会自动打开浏览器
   - 手动扫码登录B站账号
   - 程序会自动保存cookie到`bili_cookies.json`文件
   - 后续运行将自动使用保存的cookie

2. **手动获取**：
   - 在浏览器中登录B站账号
   - 打开开发者工具（F12）
   - 切换到Network标签
   - 刷新页面，找到任意请求
   - 在请求头中找到Cookie字段
   - 将Cookie字符串转换为JSON格式保存到文件

#### 关键Cookie字段说明
- `SESSDATA`: 主要的登录凭证，必须包含
- `bili_jct`: CSRF防护令牌，必须包含
- `DedeUserID`: 用户ID，必须包含
- `DedeUserID__ckMd5`: 用户ID的MD5值，必须包含
- `sid`: 会话ID，建议包含

### 2. 微信公众号Cookie配置文件 (`wechat_cookies.txt`)

#### 文件内容结构
```
uin=your_uin_value; pass_ticket=your_pass_ticket_value; key=your_key_value; wxuin=your_wxuin_value; devicetype=your_devicetype_value; version=your_version_value; lang=zh_CN; 
```

#### 获取方式
1. **浏览器获取**：
   - 参考文章：https://zhuanlan.zhihu.com/p/714173074

   - 在浏览器中登录微信公众号平台（mp.weixin.qq.com，需注册一个公众号）
   - 打开开发者工具（F12）
   - 切换到Application标签
   - 在Storage → Cookies中找到mp.weixin.qq.com
   - 复制所有Cookie值，保存到`wechat_cookies.txt`文件

2. **关键Cookie字段说明**：
   - `uin`: 用户标识符
   - `pass_ticket`: 登录票据
   - `key`: 访问密钥
   - `wxuin`: 微信用户ID
   - `devicetype`: 设备类型
   - `version`: 版本号

#### 注意事项
- Cookie值需要完整，缺少关键字段会导致请求失败
- Cookie有一定有效期，过期后需要重新获取
- 建议使用Chrome或Firefox浏览器获取

### 3. DeepSeek API密钥配置文件 (`deepseek_api_key.txt`)

#### 文件内容结构
```
sk-your-api-key-here
```

#### 获取方式
1. **注册DeepSeek账号**：
   - 访问DeepSeek官网（https://platform.deepseek.com/）
   - 注册并登录账号
   - 完成实名认证（如需要）

2. **创建API密钥**：
   - 进入控制台或API管理页面
   - 点击"创建API密钥"或类似按钮
   - 为密钥设置名称（可选）
   - 复制生成的API密钥

3. **保存密钥**：
   - 将API密钥保存到`deepseek_api_key.txt`文件
   - 确保文件只包含密钥字符串，不包含其他内容
   - 密钥格式通常为`sk-`开头的一串字符

#### 注意事项
- API密钥具有访问权限，请妥善保管
- 不要将密钥提交到代码仓库
- 密钥泄露后请立即在平台撤销并重新生成
- DeepSeek API可能有调用限制和费用，请关注使用情况

## 微信公众号文章分析工具 (wechat_get.py)

### 功能
- 自动获取指定微信公众号18小时内发布的文章
- 提取文章完整内容并进行清理
- 使用DeepSeek API生成投资建议分析
- 按日期归档保存文章内容和投资建议
- 支持断点续传，避免重复处理已保存文章

### 配置
在运行脚本前，需要配置以下参数：

1. **公众号配置**：在`wechat_get.py`中修改`account_list`字典，配置目标公众号的fakeid和名称
   ```python
   account_list = {
       "MzI1NzAwNzY4OQ%3D%3D": "财经旗舰",
       "Mzg2MDc2NzQ3MQ%3D%3D": "表舅是养基大户",
       "MzUxNzE3NzI0NA%3D%3D": "华尔街情报圈",
       "MzIyODU5NTU5Mg%3D%3D": "知识旅行家",
       "MzU4NTkwMDY5MQ%3D%3D": "炒股拌饭",
       "MzU1MDk3Njc3NA%3D%3D": "韭圈儿",
       "MzU4OTg2NTY0OA%3D%3D": "路透财经早报",
       "Mzg2NzcxMjE1NA%3D%3D": "猫笔刀",
       "Mzg4NzUxNjgyMQ%3D%3D": "章叔论市"
   }
   ```

2. **Cookie配置**：在`wechat_cookies.txt`文件中配置有效的微信公众号平台cookie
   ```
   uin=your_uin_value; pass_ticket=your_pass_ticket_value; key=your_key_value; wxuin=your_wxuin_value; devicetype=your_devicetype_value; version=your_version_value; lang=zh_CN; 
   ```

3. **DeepSeek API密钥**：确保`deepseek_api_key.txt`文件中的API密钥有效
   ```
   sk-your-api-key-here
   ```

### 使用方法
1. 安装依赖
   ```bash
   pip install -r requirements.txt
   ```

2. 配置cookie文件
   ```bash
   # 将有效的微信公众号cookie保存到wechat_cookies.json文件中
   ```

3. 运行脚本
   ```bash
   python wechat_get.py
   ```

### 注意事项
- 脚本会自动过滤18小时内发布的文章
- 包含反爬虫机制，添加了随机延迟
- 支持断点续传，已保存的文章不会重复处理
- 文章按日期归档保存在`archive_YYYY-MM-DD/`文件夹中

## B站视频分析工具 (bili_summary.py)

### 功能
- 自动登录B站平台
- 提取指定UP主18小时内发布的视频
- 自动提取视频字幕内容
- 使用DeepSeek API分析字幕并生成投资建议
- 支持多线程并行处理多个UP主
- 按日期归档保存视频内容和投资建议

### 配置
1. **UP主配置**：在`bili_summary.py`中修改`UP_MIDS`列表，添加目标UP主的ID，有的UP视频都没有字幕，无法分析，不建议添加
   ```python
   UP_MIDS = [
       "2137589551", #李大霄
       "480472604",  #鹰眼看盘
       "518031546", #财经-沉默的螺旋
       "1421580803", #九先生笔记
   ]
   ```

2. **Cookie配置**：首次运行时会自动生成`bili_cookies.json`文件保存登录凭证

3. **DeepSeek API密钥**：同微信工具配置

### 使用方法
1. 安装依赖（同上）
2. 运行脚本
   ```bash
   python bili_summary.py
   ```

3. 首次运行需要扫码登录B站，后续会自动使用保存的cookie

### 注意事项
- 脚本会自动过滤18小时内发布的视频
- 使用selenium-wire进行浏览器自动化，反检测能力强
- 支持多线程并行处理，提高效率
- 视频字幕和投资建议按日期归档保存

## 主程序入口 (kol_analyzer.py)

### 功能
- 统一协调B站和微信分析任务
- 使用多线程并行执行两个平台的任务
- 智能合并B站和微信的投资建议
- 生成综合性的投资分析报告
- 支持断点续传，避免重复执行已完成任务

### 使用方法
1. 安装依赖（同上）
2. 运行主程序
   ```bash
   python kol_analyzer.py
   ```

### 工作流程
1. 并行执行B站视频分析和微信公众号文章分析
2. 收集两个平台的投资建议
3. 使用DeepSeek API综合分析并生成统一的投资建议
4. 按日期保存所有分析结果到归档文件夹

## 常见问题
1. **登录失败**：请检查网络连接，确保能够访问目标平台，并确认cookie文件配置正确
2. **文章/视频提取失败**：可能是页面结构发生变化或cookie过期，请更新cookie文件，必要时请手动单独运行b站或微信任务
3. **总结生成失败**：请检查DeepSeek API密钥是否有效，以及网络连接是否正常
4. **依赖安装问题**：确保使用Python 3.8+版本，并按照requirements.txt安装所有依赖

## AI总结工具 (deepseek_summary.py)

### 功能
- 集成DeepSeek API进行智能文本分析
- 支持自定义系统提示词和用户提示词
- 生成专业的投资建议和市场分析
- 提供可配置的AI分析参数

### 配置
在`deepseek_api_key.txt`文件中配置API密钥：
```
sk-your-api-key-here
```

### 使用方法
```python
from deepseek_summary import deepseek_summary

# 基础总结
result = deepseek_summary(content)

# 自定义提示词
result = deepseek_summary(
    content,
    sysprompt="你是一个资深的投资策略分析师...",
    userprompt="请分析以下内容并给出投资建议..."
)
```

## 字幕提取工具 (extract_subtitle.py)

### 功能
- 从B站视频字幕URL提取字幕内容
- 支持JSON格式字幕文件解析
- 将字幕内容转换为纯文本格式
- 提供本地文件和URL两种提取方式

### 使用方法
```python
from extract_subtitle import extract_subtitle_from_url

# 从URL提取字幕
subtitle = extract_subtitle_from_url(subtitle_url)

# 从本地JSON文件提取
extract_content_to_txt('input.json', 'output.txt')
```

## 项目依赖 (requirements.txt)

项目依赖以下核心库：
- `requests>=2.31.0`: HTTP请求库
- `selenium-wire>=5.1.0`: 浏览器自动化（反检测版）
- `selenium>=4.9.1`: Selenium基础依赖
- `webdriver-manager>=3.8.6`: ChromeDriver自动管理
- `blinker==1.6.2`: 信号处理库
- `beautifulsoup4>=4.9.3`: HTML解析库
- `openai`: OpenAI API客户端（用于DeepSeek）

## 免责声明
本工具仅用于学习和研究目的，请勿用于商业用途或违反平台规定的行为。