import os
import json
from datetime import datetime, timedelta
from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend
from bili_summary import run_bili_task
from wechat_get import run_wechat_task
from weibo_get import run_weibo_task
from deepseek_summary import deepseek_summary
from momentum_analyzer import run_momentum_analysis
from prediction_recorder import record_predictions_from_advice

COOKIE_FILES = {
    "weibo": "weibo_cookies.json",
    "bili": "bili_cookies.json",
    "wechat": "wechat_cookies.json"
}

def check_cookie_exists(platform: str) -> bool:
    """检查指定平台的cookie文件是否存在"""
    cookie_file = COOKIE_FILES.get(platform)
    if cookie_file and os.path.exists(cookie_file):
        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    return True
        except:
            pass
    return False

def perform_unified_login():
    """统一登录所有平台"""
    print("\n" + "="*60)
    print("开始统一登录流程")
    print("="*60)
    
    login_results = {}
    
    print("\n[1/3] 检查微博登录状态...")
    if check_cookie_exists("weibo"):
        print("微博cookie文件已存在")
        login_results["weibo"] = True
    else:
        print("微博cookie不存在，需要登录")
        login_results["weibo"] = login_weibo_standalone()
    
    print("\n[2/3] 检查微信登录状态...")
    if check_cookie_exists("wechat"):
        print("微信cookie文件已存在")
        login_results["wechat"] = True
    else:
        print("微信cookie不存在，需要登录")
        login_results["wechat"] = login_wechat_standalone()
    
    print("\n[3/3] 检查B站登录状态...")
    if check_cookie_exists("bili"):
        print("B站cookie文件已存在")
        login_results["bili"] = True
    else:
        print("B站cookie不存在，需要登录")
        login_results["bili"] = login_bili_standalone()
    
    print("\n" + "="*60)
    print("统一登录流程完成")
    print("="*60)
    print(f"微博: {'✓ 已登录' if login_results['weibo'] else '✗ 登录失败'}")
    print(f"微信: {'✓ 已登录' if login_results['wechat'] else '✗ 登录失败'}")
    print(f"B站: {'✓ 已登录' if login_results['bili'] else '✗ 登录失败'}")
    
    return login_results

def login_weibo_standalone():
    """独立微博登录函数"""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        
        print("正在启动微博登录浏览器...")
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        driver.set_window_size(1000, 800)
        
        driver.get("https://weibo.com/")
        print("\n" + "="*50)
        print("请在浏览器中手动登录微博...")
        print("登录完成后，请按Enter键继续...")
        print("="*50 + "\n")
        input()
        
        import time
        time.sleep(3)
        
        cookies = driver.get_cookies()
        if cookies:
            with open(COOKIE_FILES["weibo"], 'w', encoding='utf-8') as f:
                json.dump(cookies, f)
            print("微博登录成功，cookie已保存")
            driver.quit()
            return True
        else:
            print("微博登录失败，未获取到cookie")
            driver.quit()
            return False
    except Exception as e:
        print(f"微博登录出错: {str(e)}")
        return False

def login_wechat_standalone():
    """独立微信登录函数"""
    try:
        print("微信使用cookie文件登录，请确保wechat_cookies.json文件有效")
        print("如需更新cookie，请运行 wechat_login.py")
        return check_cookie_exists("wechat")
    except Exception as e:
        print(f"微信登录检查出错: {str(e)}")
        return False

def login_bili_standalone():
    """独立B站登录函数"""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        print("正在启动B站登录浏览器...")
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        driver.set_window_size(800, 600)
        
        driver.get("https://www.bilibili.com")
        print("\n" + "="*50)
        print("请在浏览器中手动登录B站...")
        print("登录完成后，请按Enter键继续...")
        print("="*50 + "\n")
        input()
        
        import time
        time.sleep(3)
        
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.bili-avatar'))
            )
            cookies = driver.get_cookies()
            if cookies:
                with open(COOKIE_FILES["bili"], 'w', encoding='utf-8') as f:
                    json.dump(cookies, f)
                print("B站登录成功，cookie已保存")
                driver.quit()
                return True
            else:
                print("B站登录失败，未获取到cookie")
                driver.quit()
                return False
        except:
            print("B站登录状态验证失败，但cookie已保存")
            cookies = driver.get_cookies()
            if cookies:
                with open(COOKIE_FILES["bili"], 'w', encoding='utf-8') as f:
                    json.dump(cookies, f)
                driver.quit()
                return True
            driver.quit()
            return False
    except Exception as e:
        print(f"B站登录出错: {str(e)}")
        return False

class KOLAnalyzer:
    """KOL分析器主类，用于执行各平台任务并合并投资建议"""
    
    def __init__(self):
        self.current_date, date_reason, self.archive_folder = get_current_analysis_date()
        print_date_info()
        
        ensure_archive_folder(self.archive_folder)
    
    def run_bili_task(self):
        """运行B站视频分析任务"""
        print("\n" + "="*50)
        print("开始执行B站视频分析任务")
        print("="*50)
        
        bili_advice_path = os.path.join(self.archive_folder, f"bili_投资建议_{self.current_date}.txt")
        if os.path.exists(bili_advice_path):
            print(f"B站投资建议文件已存在: {bili_advice_path}")
            print("跳过B站任务执行")
            try:
                with open(bili_advice_path, "r", encoding="utf-8") as f:
                    bili_advice = f.read()
                print(f"已读取现有B站投资建议，长度: {len(bili_advice)}字符")
                return bili_advice
            except Exception as e:
                print(f"读取现有B站投资建议失败: {str(e)}")
                return None
        
        try:
            bili_advice = run_bili_task()
            print(f"B站任务完成，返回投资建议: {bili_advice is not None}")
            return bili_advice
        except Exception as e:
            print(f"B站任务执行失败: {str(e)}")
            return None
    
    def run_wechat_task(self):
        """运行微信公众号文章分析任务"""
        print("\n" + "="*50)
        print("开始执行微信公众号文章分析任务")
        print("="*50)
        
        wechat_advice_path = os.path.join(self.archive_folder, f"wechat_投资建议_{self.current_date}.txt")
        if os.path.exists(wechat_advice_path):
            print(f"微信投资建议文件已存在: {wechat_advice_path}")
            print("跳过微信任务执行")
            try:
                with open(wechat_advice_path, "r", encoding="utf-8") as f:
                    wechat_advice = f.read()
                print(f"已读取现有微信投资建议，长度: {len(wechat_advice)}字符")
                return wechat_advice
            except Exception as e:
                print(f"读取现有微信投资建议失败: {str(e)}")
                return None
        
        try:
            wechat_advice = run_wechat_task()
            print(f"微信任务完成，返回投资建议: {wechat_advice is not None}")
            return wechat_advice
        except Exception as e:
            print(f"微信任务执行失败: {str(e)}")
            return None
    
    def run_weibo_task(self):
        """运行微博分析任务"""
        print("\n" + "="*50)
        print("开始执行微博分析任务")
        print("="*50)
        
        weibo_advice_path = os.path.join(self.archive_folder, f"weibo_投资建议_{self.current_date}.txt")
        if os.path.exists(weibo_advice_path):
            print(f"微博投资建议文件已存在: {weibo_advice_path}")
            print("跳过微博任务执行")
            try:
                with open(weibo_advice_path, "r", encoding="utf-8") as f:
                    weibo_advice = f.read()
                print(f"已读取现有微博投资建议，长度: {len(weibo_advice)}字符")
                return weibo_advice
            except Exception as e:
                print(f"读取现有微博投资建议失败: {str(e)}")
                return None
        
        try:
            weibo_advice = run_weibo_task()
            print(f"微博任务完成，返回投资建议: {weibo_advice is not None}")
            return weibo_advice
        except Exception as e:
            print(f"微博任务执行失败: {str(e)}")
            return None
    
    def merge_investment_advice(self, bili_advice, wechat_advice, weibo_advice):
        """使用DeepSeek合并B站、微信和微博的投资建议"""
        print("\n" + "="*50)
        print("开始合并投资建议")
        print("="*50)
        
        merged_advice_path = os.path.join(self.archive_folder, f"综合投资建议_{self.current_date}.txt")
        if os.path.exists(merged_advice_path):
            print(f"综合投资建议文件已存在: {merged_advice_path}")
            print("跳过投资建议合并")
            try:
                with open(merged_advice_path, "r", encoding="utf-8") as f:
                    merged_advice = f.read()
                print(f"已读取现有综合投资建议，长度: {len(merged_advice)}字符")
                return merged_advice
            except Exception as e:
                print(f"读取现有综合投资建议失败: {str(e)}")
                return None
        
        if not bili_advice and not wechat_advice and not weibo_advice:
            print("没有可用的投资建议，跳过合并")
            return None
        
        momentum_report, momentum_results = run_momentum_analysis(
            bili_advice=bili_advice,
            wechat_advice=wechat_advice,
            weibo_advice=weibo_advice
        )
        
        if momentum_report:
            momentum_report_path = os.path.join(self.archive_folder, f"动量分析报告_{self.current_date}.txt")
            try:
                with open(momentum_report_path, "w", encoding="utf-8") as f:
                    f.write(momentum_report)
                print(f"动量分析报告已保存到: {momentum_report_path}")
            except Exception as e:
                print(f"保存动量分析报告失败: {str(e)}")
        
        combined_content = ""
        if bili_advice:
            combined_content += f"=== B站视频分析投资建议 ===\n{bili_advice}\n\n"
        if wechat_advice:
            combined_content += f"=== 微信公众号文章分析投资建议 ===\n{wechat_advice}\n\n"
        if weibo_advice:
            combined_content += f"=== 微博分析投资建议 ===\n{weibo_advice}\n\n"
        
        if momentum_report:
            combined_content += f"=== 重点关注标的动量分析 ===\n{momentum_report}\n\n"
        
        print(f"准备合并的投资建议内容长度: {len(combined_content)}字符")
        
        try:
            merged_advice = deepseek_summary(
                combined_content,
                sysprompt=(
                    "你是首席投资策略官，拥有20年多资产配置经验，曾管理超百亿规模组合。"
                    "你的核心职责是将来自B站（视频分析）、微信（深度文章）、微博（市场情绪）三个信息源的投资洞见，"
                    "结合动量分析技术指标，汇编成一份可以直接指导交易的终极投资决策报告。\n\n"
                    "信息源特征与权重分配：\n"
                    "- B站来源（权重30%）：视频UP主观点，信息密度高但质量参差，侧重发现非共识机会\n"
                    "- 微信来源（权重40%）：专业公众号深度文章，逻辑性最强，侧重基本面和中长期判断\n"
                    "- 微博来源（权重20%）：大V实时情绪和资金动向，侧重市场温度和短期博弈\n"
                    "- 动量数据（权重10%）：客观技术面指标，用于验证或证伪上述主观判断\n\n"
                    "决策框架：\n"
                    "1. 三源交叉验证，只有被2个以上来源共同支持的方向才作为核心交易方向\n"
                    "2. 技术面必须与基本面/情绪面方向一致时才入场，背离时必须解释原因\n"
                    "3. 入场判断必须包含：方向、时机、仓位、止损、目标价五个要素\n"
                    "4. 最终必须给出一个明确的 Go/No-Go 决策，不允许模棱两可\n\n"
                    "输出语气：坚定自信但不傲慢，敢于表达明确观点，同时诚实标注不确定性。"
                ),
                userprompt=(
                    "以下是来自三大平台的投资分析与动量数据，请汇编成最终投资作战方案：\n\n"
                    "请按以下结构输出：\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第一部分：信息综述\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "【B站信号摘要】核心观点和共识方向\n"
                    "【微信信号摘要】核心逻辑和产业链判断\n"
                    "【微博信号摘要】市场情绪和资金动向\n"
                    "【动量验证】技术面是否支持上述判断\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第二部分：三源交叉验证矩阵\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "用表格列示各来源对核心议题的立场（B站/微信/微博/动量），"
                    "标注共识数量和分歧点，红色标记高度共识的方向。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第三部分：最终交易决策\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "★ 总仓位建议：XX%（激进/中性/保守）\n"
                    "★ 入场/离场决策：Go / No-Go\n"
                    "★ 核心交易方向（1-2个）：\n"
                    "  - 方向1：做多/做空 XXX，逻辑...，入场价...，止损...，目标...，仓位...\n"
                    "  - 方向2：...\n"
                    "★ 对冲/防御配置：\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第四部分：风险预案\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "- Plan A（基准情景）：概率XX%，操作...\n"
                    "- Plan B（上行风险）：概率XX%，触发条件...\n"
                    "- Plan C（下行风险）：概率XX%，触发条件...，止损方案...\n"
                    "- 黑天鹅应对预案\n\n"
                    "=== 以下为分析素材 ===\n\n"
                ),
                temperature=0.15,
                max_tokens=20480
            )
            
            merged_advice_path = os.path.join(self.archive_folder, f"综合投资建议_{self.current_date}.txt")
            with open(merged_advice_path, "w", encoding="utf-8") as f:
                f.write(f"综合投资建议 - {self.current_date}\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*50 + "\n\n")
                f.write(merged_advice)
            
            print(f"综合投资建议已保存到: {merged_advice_path}")
            
            # 提取并保存综合投资建议的预测观点
            record_predictions_from_advice(merged_advice, "merged", "综合分析", self.current_date, self.archive_folder)
            
            print("投资建议合并完成")
            return merged_advice
            
        except Exception as e:
            print(f"合并投资建议失败: {str(e)}")
            return None
    
    def run_all_tasks(self, skip_login: bool = False):
        """顺序运行所有任务并合并投资建议
        
        Args:
            skip_login: 是否跳过统一登录流程
        """
        print("\n" + "="*60)
        print(f"开始执行KOL分析任务 - {self.current_date}")
        print("="*60)
        
        if not skip_login:
            login_results = perform_unified_login()
            print("\n>>> 登录状态检查完成，开始执行任务...")
        else:
            print("\n>>> 跳过统一登录流程，直接执行任务...")
        
        print("\n>>> 任务执行顺序: 微博 → 微信 → B站")
        
        weibo_advice = self.run_weibo_task()
        wechat_advice = self.run_wechat_task()
        bili_advice = self.run_bili_task()
        
        merged_advice = self.merge_investment_advice(bili_advice, wechat_advice, weibo_advice)
        
        print("\n" + "="*60)
        print("所有KOL分析任务完成")
        print("="*60)
        
        return {
            "bili_advice": bili_advice,
            "wechat_advice": wechat_advice,
            "weibo_advice": weibo_advice,
            "merged_advice": merged_advice,
            "date": self.current_date
        }


if __name__ == "__main__":
    analyzer = KOLAnalyzer()
    result = analyzer.run_all_tasks()
    
    print("\n任务执行结果:")
    print(f"- B站投资建议: {'有' if result['bili_advice'] else '无'}")
    print(f"- 微信投资建议: {'有' if result['wechat_advice'] else '无'}")
    print(f"- 微博投资建议: {'有' if result['weibo_advice'] else '无'}")
    print(f"- 综合投资建议: {'有' if result['merged_advice'] else '无'}")
    print(f"- 执行日期: {result['date']}")
