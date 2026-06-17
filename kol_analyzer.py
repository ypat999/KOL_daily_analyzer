import os
import json
from datetime import datetime, timedelta
from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend
from bili_summary import run_bili_task
from wechat_get import run_wechat_task
from weibo_get import run_weibo_task
from deepseek_summary import deepseek_summary
from momentum_analyzer import run_momentum_analysis
from prediction_recorder import record_predictions_from_advice, generate_yesterday_review
from position_manager import run_position_analysis, list_positions, load_positions, calculate_portfolio_risk, format_portfolio_risk_report
from cookie_validator import perform_unified_login
from backtest_analyzer import load_latest_backtest_stats, format_backtest_summary_for_prompt
from market_breadth import run_market_breadth_analysis
from signal_knowledge_base import format_kb_summary_for_prompt, record_signals_from_analysis, update_signal_outcomes
from advice_history import format_history_for_prompt, record_advice

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
        
        # 加载回测命中率统计，动态调整博主权重
        backtest_stats = load_latest_backtest_stats()
        backtest_summary = format_backtest_summary_for_prompt(backtest_stats)
        if backtest_summary:
            combined_content += f"=== 博主历史预测命中率 ===\n{backtest_summary}\n\n"
            print("已注入博主回测命中率到合并prompt")
        
        # 生成昨日预测复盘
        yesterday_review = generate_yesterday_review(self.current_date)
        if yesterday_review:
            combined_content += f"=== 昨日预测复盘 ===\n{yesterday_review}\n\n"
            print("已注入昨日预测复盘到合并prompt")
        
        # 市场宽度分析
        try:
            market_breadth_report = run_market_breadth_analysis()
            if market_breadth_report:
                combined_content += f"=== 市场宽度分析 ===\n{market_breadth_report}\n\n"
                print("已注入市场宽度分析到合并prompt")
        except Exception as e:
            print(f"市场宽度分析失败（不影响主流程）: {e}")
        
        # 持仓组合风险分析
        try:
            risk_data = calculate_portfolio_risk()
            if risk_data:
                risk_report = format_portfolio_risk_report(risk_data)
                combined_content += f"=== 持仓组合风险分析 ===\n{risk_report}\n\n"
                print("已注入持仓组合风险分析到合并prompt")
        except Exception as e:
            print(f"持仓组合风险分析失败（不影响主流程）: {e}")
        
        # 信号-胜率知识库：先更新历史信号收益，再注入摘要
        try:
            update_signal_outcomes()
            kb_summary = format_kb_summary_for_prompt()
            if kb_summary:
                combined_content += f"=== 信号-胜率知识库 ===\n{kb_summary}\n\n"
                print("已注入信号-胜率知识库到合并prompt")
        except Exception as e:
            print(f"信号知识库更新失败（不影响主流程）: {e}")
        
        # 建议历史：注入过去N天观点，避免反复
        try:
            history_text = format_history_for_prompt(days=7)
            if history_text:
                combined_content += f"=== 过去7天建议历史 ===\n{history_text}\n\n"
                print("已注入建议历史到合并prompt")
        except Exception as e:
            print(f"建议历史加载失败（不影响主流程）: {e}")
        
        print(f"准备合并的投资建议内容长度: {len(combined_content)}字符")
        
        try:
            # 根据是否有回测数据动态构建权重说明
            if backtest_stats:
                weight_guidance = (
                    "信息源权重分配（已根据历史回测命中率动态调整）：\n"
                    "- 请参考下方「博主历史预测命中率」数据，对低命中率博主的观点降权，高命中率博主升权\n"
                    "- 命中率<40%的博主观点仅作参考，不作为核心交易依据\n"
                    "- 命中率>60%的博主观点可作为核心信号源\n"
                    "- 动量数据（客观技术面）：用于验证或证伪上述主观判断，权重不低于15%\n\n"
                )
            else:
                weight_guidance = (
                    "信息源特征与权重分配（默认，尚无回测数据）：\n"
                    "- B站来源（权重30%）：视频UP主观点，信息密度高但质量参差，侧重发现非共识机会\n"
                    "- 微信来源（权重40%）：专业公众号深度文章，逻辑性最强，侧重基本面和中长期判断\n"
                    "- 微博来源（权重20%）：大V实时情绪和资金动向，侧重市场温度和短期博弈\n"
                    "- 动量数据（权重10%）：客观技术面指标，用于验证或证伪上述主观判断\n\n"
                )
            
            merged_advice = deepseek_summary(
                combined_content,
                sysprompt=(
                    "你是首席投资策略官，拥有20年多资产配置经验，曾管理超百亿规模组合。"
                    "你的核心职责是将来自B站（视频分析）、微信（深度文章）、微博（市场情绪）三个信息源的投资洞见，"
                    "结合动量分析技术指标，汇编成一份可以直接指导交易的终极投资决策报告。\n\n"
                    + weight_guidance +
                    "决策框架：\n"
                    "1. 三源交叉验证，只有被2个以上来源共同支持的方向才作为核心交易方向\n"
                    "2. 技术面必须与基本面/情绪面方向一致时才入场，背离时必须解释原因\n"
                    "3. 入场判断必须包含：方向、时机、仓位、止损、目标价五个要素\n"
                    "4. 最终必须给出一个明确的 Go/No-Go 决策，不允许模棱两可\n\n"
                    "输出语气：坚定自信但不傲慢，敢于表达明确观点，同时诚实标注不确定性。"
                ),
                userprompt=(
                    "以下是来自三大平台的投资分析与动量数据，请汇编成【明日作战计划】（而非分析报告）：\n\n"
                    "请按以下结构输出，每部分必须可执行、可验证：\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第一部分：昨日复盘（如有）\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "对照昨日预测与实际行情，逐条标注✓/✗，分析错误原因，"
                    "明确说明今日是否需要修正方向。如无昨日复盘数据则跳过。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第二部分：市场环境判断\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "根据市场宽度数据，用一句话定性今日市场环境（过热/偏热/偏冷/过冷），"
                    "并给出对应的仓位基调（满仓/半仓/轻仓/空仓）。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第三部分：明日作战清单（核心）\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "用if-then格式列出明日所有触发条件，必须包含具体价格：\n"
                    "格式示例：\n"
                    "  IF 上证指数高开>0.5% AND 成交量放大 THEN 做多XXX，仓位XX%\n"
                    "  IF XXX跌破15.2（支撑位）THEN 立即止损离场\n"
                    "  IF XXX触及18.5（阻力位）AND 缩量 THEN 减仓50%\n"
                    "  IF 北向资金净流出>50亿 THEN 全面防御，仓位降至XX%\n"
                    "每条必须可量化、可执行，不允许模糊表述。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第四部分：价格警报清单\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "列出明日需要盘中监控的关键价格（基于支撑/阻力位和ATR）：\n"
                    "  标的 | 警报价 | 触发动作 | 监控原因\n"
                    "  XXX  | 15.2   | 止损    | 跌破波段低点支撑\n"
                    "  XXX  | 18.5   | 止盈    | 触及阻力位\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第五部分：持仓组合建议\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "基于组合风险分析，给出：\n"
                    "- 总仓位调整建议（加/减/持有，目标比例XX%）\n"
                    "- 单一标的仓位调整（如有过高集中度）\n"
                    "- 对冲/防御配置\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第六部分：与昨日建议的差异\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "明确标注今日观点与昨日的差异（如有）：\n"
                    "  - 方向变化：昨日看多XXX → 今日转中性，原因...\n"
                    "  - 仓位变化：昨日建议70% → 今日建议50%，原因...\n"
                    "  - 新增/移除标的：新增关注XXX，移除关注YYY，原因...\n"
                    "如无昨日数据则标注\"首次分析\"。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第七部分：风险预案\n"
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
            
            # 记录信号事件到知识库（用于长期胜率统计）
            try:
                # 从综合建议中提取最终方向
                advice_direction = "neutral"
                if "做多" in merged_advice or "看多" in merged_advice or "Go" in merged_advice:
                    advice_direction = "bullish"
                elif "做空" in merged_advice or "看空" in merged_advice or "No-Go" in merged_advice:
                    advice_direction = "bearish"
                
                # 加载KOL预测用于共识统计
                from prediction_recorder import load_predictions
                kol_preds = load_predictions(self.archive_folder)
                
                # 提取市场宽度数据（简化版，从报告文本无法还原，用None）
                record_signals_from_analysis(
                    momentum_results, kol_preds, None, advice_direction
                )
            except Exception as e:
                print(f"记录信号事件到知识库失败（不影响主流程）: {e}")
            
            # 记录当日建议到历史（用于多日连续性追踪）
            try:
                record_advice(self.current_date, merged_advice)
            except Exception as e:
                print(f"记录建议历史失败（不影响主流程）: {e}")
            
            print("投资建议合并完成")
            return merged_advice
            
        except Exception as e:
            print(f"合并投资建议失败: {str(e)}")
            return None
    
    def run_all_tasks(self, skip_login: bool = False, include_position: bool = True):
        """顺序运行所有任务并合并投资建议
        
        Args:
            skip_login: 是否跳过统一登录流程
            include_position: 是否包含持仓分析
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
        
        position_result = None
        match_result = None
        match_report = None
        
        if include_position:
            print("\n" + "="*60)
            print("持仓分析")
            print("="*60)
            
            positions = load_positions()
            if positions["stocks"] or positions["indices"]:
                position_result, match_result, match_report = run_position_analysis(
                    bili_advice=bili_advice,
                    wechat_advice=wechat_advice,
                    weibo_advice=weibo_advice
                )
                
                if match_report:
                    match_report_path = os.path.join(self.archive_folder, f"持仓匹配分析_{self.current_date}.txt")
                    try:
                        with open(match_report_path, "w", encoding="utf-8") as f:
                            f.write(match_report)
                        print(f"持仓匹配分析已保存到: {match_report_path}")
                    except Exception as e:
                        print(f"保存持仓匹配分析失败: {str(e)}")
                
                if position_result:
                    position_report_path = os.path.join(self.archive_folder, f"持仓动量分析_{self.current_date}.txt")
                    try:
                        report_lines = []
                        report_lines.append("=" * 60)
                        report_lines.append("持仓动量分析报告")
                        report_lines.append(f"分析日期: {position_result['analysis_date']}")
                        report_lines.append("=" * 60)
                        
                        if position_result["stocks"]:
                            report_lines.append("\n【股票持仓】")
                            report_lines.append("-" * 40)
                            for s in position_result["stocks"]:
                                report_lines.append(f"\n{s['name']}({s['code']})")
                                report_lines.append(f"  持仓: {s['shares']}股 @ {s['cost_price']}")
                                report_lines.append(f"  最新价: {s['latest_price']:.2f}")
                                report_lines.append(f"  市值: {s['market_value']:.2f}")
                                report_lines.append(f"  盈亏: {s['profit_loss']:+.2f} ({s['profit_pct']:+.2f}%)")
                                factors = s['momentum_factors']
                                report_lines.append(f"  20日收益率: {factors['return_20d']}%")
                                report_lines.append(f"  60日收益率: {factors['return_60d']}%")
                                if factors['trend_strength']:
                                    ts = factors['trend_strength']
                                    report_lines.append(f"  趋势: {ts['trend_direction']} | 强度: {ts['trend_level']} ({ts['overall_strength']})")
                        
                        if position_result["indices"]:
                            report_lines.append("\n【指数/ETF持仓】")
                            report_lines.append("-" * 40)
                            for idx in position_result["indices"]:
                                report_lines.append(f"\n{idx['name']}({idx['code']})")
                                report_lines.append(f"  持仓: {idx['shares']}份 @ {idx['cost_price']}")
                                report_lines.append(f"  最新价: {idx['latest_price']:.2f}")
                                report_lines.append(f"  市值: {idx['market_value']:.2f}")
                                report_lines.append(f"  盈亏: {idx['profit_loss']:+.2f} ({idx['profit_pct']:+.2f}%)")
                                factors = idx['momentum_factors']
                                report_lines.append(f"  20日收益率: {factors['return_20d']}%")
                                report_lines.append(f"  60日收益率: {factors['return_60d']}%")
                                if factors['trend_strength']:
                                    ts = factors['trend_strength']
                                    report_lines.append(f"  趋势: {ts['trend_direction']} | 强度: {ts['trend_level']} ({ts['overall_strength']})")
                        
                        report_lines.append("\n" + "-" * 40)
                        report_lines.append(f"总市值: {position_result['total_market_value']:.2f}")
                        report_lines.append(f"总盈亏: {position_result['total_profit_loss']:+.2f}")
                        report_lines.append("=" * 60)
                        
                        with open(position_report_path, "w", encoding="utf-8") as f:
                            f.write("\n".join(report_lines))
                        print(f"持仓动量分析已保存到: {position_report_path}")
                    except Exception as e:
                        print(f"保存持仓动量分析失败: {str(e)}")
            else:
                print("暂无持仓数据，跳过持仓分析")
                print("提示: 使用 position_manager.py 添加持仓信息")
        
        print("\n" + "="*60)
        print("所有KOL分析任务完成")
        print("="*60)
        
        return {
            "bili_advice": bili_advice,
            "wechat_advice": wechat_advice,
            "weibo_advice": weibo_advice,
            "merged_advice": merged_advice,
            "position_result": position_result,
            "match_result": match_result,
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
    print(f"- 持仓分析: {'有' if result['position_result'] else '无'}")
    print(f"- 持仓匹配: {'有' if result['match_result'] else '无'}")
    print(f"- 执行日期: {result['date']}")
