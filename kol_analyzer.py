import concurrent.futures
import os
from datetime import datetime, timedelta
from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend
from bili_summary import run_bili_task
from wechat_get import run_wechat_task
from weibo_get import run_weibo_task
from deepseek_summary import deepseek_summary

class KOLAnalyzer:
    """KOL分析器主类，用于同时执行B站和微信任务并合并投资建议"""
    
    def __init__(self):
        # 使用统一的日期工具获取当前分析日期
        self.current_date, date_reason, self.archive_folder = get_current_analysis_date()
        print_date_info()
        
        # 确保归档文件夹存在
        ensure_archive_folder(self.archive_folder)
    
    def run_bili_task(self):
        """运行B站视频分析任务"""
        print("\n" + "="*50)
        print("开始执行B站视频分析任务")
        print("="*50)
        
        # 检查B站投资建议文件是否已存在
        bili_advice_path = os.path.join(self.archive_folder, f"bili_投资建议_{self.current_date}.txt")
        if os.path.exists(bili_advice_path):
            print(f"B站投资建议文件已存在: {bili_advice_path}")
            print("跳过B站任务执行")
            # 读取已存在的投资建议
            try:
                with open(bili_advice_path, "r", encoding="utf-8") as f:
                    bili_advice = f.read()
                print(f"已读取现有B站投资建议，长度: {len(bili_advice)}字符")
                return bili_advice
            except Exception as e:
                print(f"读取现有B站投资建议失败: {str(e)}")
                return None
        
        try:
            # 调用bili_summary.py中的run_bili_task方法
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
        
        # 检查微信投资建议文件是否已存在
        wechat_advice_path = os.path.join(self.archive_folder, f"wechat_投资建议_{self.current_date}.txt")
        if os.path.exists(wechat_advice_path):
            print(f"微信投资建议文件已存在: {wechat_advice_path}")
            print("跳过微信任务执行")
            # 读取已存在的投资建议
            try:
                with open(wechat_advice_path, "r", encoding="utf-8") as f:
                    wechat_advice = f.read()
                print(f"已读取现有微信投资建议，长度: {len(wechat_advice)}字符")
                return wechat_advice
            except Exception as e:
                print(f"读取现有微信投资建议失败: {str(e)}")
                return None
        
        try:
            # 调用wechat_get.py中的run_wechat_task方法
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
        
        # 检查微博投资建议文件是否已存在
        weibo_advice_path = os.path.join(self.archive_folder, f"weibo_投资建议_{self.current_date}.txt")
        if os.path.exists(weibo_advice_path):
            print(f"微博投资建议文件已存在: {weibo_advice_path}")
            print("跳过微博任务执行")
            # 读取已存在的投资建议
            try:
                with open(weibo_advice_path, "r", encoding="utf-8") as f:
                    weibo_advice = f.read()
                print(f"已读取现有微博投资建议，长度: {len(weibo_advice)}字符")
                return weibo_advice
            except Exception as e:
                print(f"读取现有微博投资建议失败: {str(e)}")
                return None
        
        try:
            # 调用weibo_get.py中的run_weibo_task方法
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
        
        # 检查综合投资建议文件是否已存在
        merged_advice_path = os.path.join(self.archive_folder, f"综合投资建议_{self.current_date}.txt")
        if os.path.exists(merged_advice_path):
            print(f"综合投资建议文件已存在: {merged_advice_path}")
            print("跳过投资建议合并")
            # 读取已存在的综合投资建议
            try:
                with open(merged_advice_path, "r", encoding="utf-8") as f:
                    merged_advice = f.read()
                print(f"已读取现有综合投资建议，长度: {len(merged_advice)}字符")
                return merged_advice
            except Exception as e:
                print(f"读取现有综合投资建议失败: {str(e)}")
                return None
        
        # 检查是否有投资建议需要合并
        if not bili_advice and not wechat_advice and not weibo_advice:
            print("没有可用的投资建议，跳过合并")
            return None
        
        # 准备合并内容
        combined_content = ""
        if bili_advice:
            combined_content += f"=== B站视频分析投资建议 ===\n{bili_advice}\n\n"
        if wechat_advice:
            combined_content += f"=== 微信公众号文章分析投资建议 ===\n{wechat_advice}\n\n"
        if weibo_advice:
            combined_content += f"=== 微博分析投资建议 ===\n{weibo_advice}\n\n"
        
        print(f"准备合并的投资建议内容长度: {len(combined_content)}字符")
        
        try:
            # 使用DeepSeek合并投资建议
            merged_advice = deepseek_summary(
                combined_content,
                sysprompt="你是一个资深的投资策略分析师，擅长综合多个信息源的投资建议，给出全面、客观、专业的综合投资建议。你需要考虑不同信息源的权重、时效性和可靠性。",
                userprompt="以下是来自B站财经视频分析、微信公众号文章分析和微博分析的投资建议，请综合分析并给出未来几天的综合投资建议，包括：\n1. 整体市场判断\n2. 重点行业/板块分析\n3. 具体投资策略\n4. 风险提示\n5. 综合建议\n\n请详细分析并给出专业建议：\n\n"
            )
            
            # 保存合并后的投资建议
            merged_advice_path = os.path.join(self.archive_folder, f"综合投资建议_{self.current_date}.txt")
            with open(merged_advice_path, "w", encoding="utf-8") as f:
                f.write(f"综合投资建议 - {self.current_date}\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*50 + "\n\n")
                f.write(merged_advice)
            
            print(f"综合投资建议已保存到: {merged_advice_path}")
            print("投资建议合并完成")
            return merged_advice
            
        except Exception as e:
            print(f"合并投资建议失败: {str(e)}")
            return None
    
    def run_all_tasks(self):
        """同时运行所有任务并合并投资建议"""
        print("\n" + "="*60)
        print(f"开始执行KOL分析任务 - {self.current_date}")
        print("="*60)
        
        # 使用线程池同时执行B站、微信和微博任务
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            # 提交任务
            bili_future = executor.submit(self.run_bili_task)
            wechat_future = executor.submit(self.run_wechat_task)
            weibo_future = executor.submit(self.run_weibo_task)
            
            # 等待任务完成并获取结果
            bili_advice = bili_future.result()
            wechat_advice = wechat_future.result()
            weibo_advice = weibo_future.result()
        
        # 合并投资建议
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
    # 创建KOL分析器实例并运行所有任务
    analyzer = KOLAnalyzer()
    result = analyzer.run_all_tasks()
    
    print("\n任务执行结果:")
    print(f"- B站投资建议: {'有' if result['bili_advice'] else '无'}")
    print(f"- 微信投资建议: {'有' if result['wechat_advice'] else '无'}")
    print(f"- 微博投资建议: {'有' if result['weibo_advice'] else '无'}")
    print(f"- 综合投资建议: {'有' if result['merged_advice'] else '无'}")
    print(f"- 执行日期: {result['date']}")