import os
import json
from datetime import datetime, timedelta
from date_utils import get_current_analysis_date, ensure_archive_folder, print_date_info, get_friday_date_for_weekend, get_next_trading_day
from bili_summary import run_bili_task
from wechat_weread import run_wechat_task, WECHAT_ENABLED
from weibo_get import run_weibo_task
from deepseek_summary import deepseek_summary
from momentum_analyzer import run_momentum_analysis
from prediction_recorder import record_predictions_from_advice, generate_yesterday_review
from position_manager import run_position_analysis, list_positions, load_positions, calculate_portfolio_risk, format_portfolio_risk_report, fetch_position_f10_and_news, format_position_f10_report
from wechat_push import push_to_wechat
from cookie_validator import perform_unified_login
from backtest_analyzer import load_latest_backtest_stats, format_backtest_summary_for_prompt
from market_breadth import run_market_breadth_analysis
from fund_flow_analysis import run_fund_flow_analysis
from sector_volume_monitor import run_sector_volume_analysis
from momentum_analyzer import run_position_relative_strength, format_relative_strength_report
from signal_knowledge_base import format_kb_summary_for_prompt, record_signals_from_analysis, update_signal_outcomes
from advice_history import format_history_for_prompt, record_advice
from deepseek_summary import deepseek_chat
from stage_timer import stage, timed, timer, fmt_secs

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

        if not WECHAT_ENABLED:
            print("微信任务已禁用（WECHAT_ENABLED=False），跳过执行")
            return None

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
        
        momentum_report, momentum_results = timed(
            "动量因子分析", run_momentum_analysis,
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
        backtest_stats = timed("博主回测命中率加载", load_latest_backtest_stats)
        backtest_summary = format_backtest_summary_for_prompt(backtest_stats)
        if backtest_summary:
            combined_content += f"=== 博主历史预测命中率 ===\n{backtest_summary}\n\n"
            print("已注入博主回测命中率到合并prompt")
        
        # 生成昨日预测复盘
        yesterday_review = timed("昨日预测复盘", generate_yesterday_review, self.current_date)
        if yesterday_review:
            combined_content += f"=== 昨日预测复盘 ===\n{yesterday_review}\n\n"
            print("已注入昨日预测复盘到合并prompt")
        
        # 市场宽度分析
        try:
            market_breadth_report = timed("市场宽度分析", run_market_breadth_analysis)
            if market_breadth_report:
                combined_content += f"=== 市场宽度分析 ===\n{market_breadth_report}\n\n"
                print("已注入市场宽度分析到合并prompt")
                # 缓存供 build_chat_context 复用，避免推送后重复计算
                self._market_breadth_report = market_breadth_report
        except Exception as e:
            print(f"市场宽度分析失败（不影响主流程）: {e}")
        
        # 持仓组合风险分析
        try:
            risk_data = timed("持仓组合风险分析", calculate_portfolio_risk)
            if risk_data:
                risk_report = format_portfolio_risk_report(risk_data)
                combined_content += f"=== 持仓组合风险分析 ===\n{risk_report}\n\n"
                print("已注入持仓组合风险分析到合并prompt")
        except Exception as e:
            print(f"持仓组合风险分析失败（不影响主流程）: {e}")

        # 资金面分析（行业/概念资金流、个股资金流、持仓股资金流、两融余额）
        try:
            positions_for_fund = load_positions()
            fund_flow_report = timed("资金面分析", run_fund_flow_analysis, positions_for_fund)
            if fund_flow_report:
                combined_content += f"=== 资金面分析（行业/概念/个股资金流+两融） ===\n{fund_flow_report}\n\n"
                print("已注入资金面分析到合并prompt")
        except Exception as e:
            print(f"资金面分析失败（不影响主流程）: {e}")

        # 板块放量监控（底部放量=机会，顶部放量=风险）
        try:
            sector_report = timed("板块放量监控", run_sector_volume_analysis)
            if sector_report:
                combined_content += f"=== 板块放量监控（底部放量机会 / 顶部放量风险） ===\n{sector_report}\n\n"
                print("已注入板块放量监控到合并prompt")
                sector_report_path = os.path.join(self.archive_folder, f"板块放量监控报告_{self.current_date}.txt")
                with open(sector_report_path, "w", encoding="utf-8") as f:
                    f.write(sector_report)
                print(f"板块放量监控报告已保存到: {sector_report_path}")
        except Exception as e:
            print(f"板块放量监控失败（不影响主流程）: {e}")

        # 持仓股相对所属行业强弱（RS = 个股涨幅 - 行业涨幅）
        try:
            # 注意：与 run_all_tasks 里的 F10 抓取重复，是主要可优化点之一
            f10_data_for_rs = timed("持仓F10/新闻公告抓取", fetch_position_f10_and_news)
            if f10_data_for_rs:
                rs_data = timed("持仓股相对行业强弱", run_position_relative_strength, f10_data_for_rs)
                if rs_data and rs_data.get("results"):
                    rs_report = format_relative_strength_report(rs_data)
                    combined_content += f"=== 持仓股相对所属行业强弱 ===\n{rs_report}\n\n"
                    print("已注入持仓股相对强弱到合并prompt")
        except Exception as e:
            print(f"持仓股相对强弱分析失败（不影响主流程）: {e}")
        
        # 信号-胜率知识库：先更新历史信号收益，再注入摘要
        try:
            with stage("知识库-回填历史信号收益"):
                update_signal_outcomes()
            kb_summary = format_kb_summary_for_prompt()
            if kb_summary:
                combined_content += f"=== 信号-胜率知识库 ===\n{kb_summary}\n\n"
                print("已注入信号-胜率知识库到合并prompt")
        except Exception as e:
            print(f"信号知识库更新失败（不影响主流程）: {e}")
        
        # 建议历史：注入过去N天观点，避免反复
        try:
            history_text = timed("建议历史加载(7天)", format_history_for_prompt, days=7)
            if history_text:
                combined_content += f"=== 过去7天建议历史 ===\n{history_text}\n\n"
                print("已注入建议历史到合并prompt")
        except Exception as e:
            print(f"建议历史加载失败（不影响主流程）: {e}")
        
        print(f"准备合并的投资建议内容长度: {len(combined_content)}字符")
        
        try:
            # 目标交易日 = 数据日（current_date）的下一交易日，由程序确定而非 LLM 推算
            target_date = get_next_trading_day(self.current_date)
            
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
            
            merged_advice = timed(
                "DeepSeek 合并生成（含思考链）", deepseek_summary,
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
                    f"以下是来自三大平台的投资分析与动量数据，请汇编成【明日作战计划】（而非分析报告）：\n\n"
                    f"当前数据日期：{self.current_date}；本作战计划服务目标：{target_date}（下一交易日）。\n"
                    f"标题务必写作战计划（{target_date}），正文中涉及日期一律使用 {target_date}，严禁自行推算日期。\n\n"
                    "请按以下结构输出。盘中触发条件已交由程序自动盯盘，"
                    "因此【具体价格触发】只允许出现在第五部分表格里，其余章节一律用方向/逻辑/仓位表述，"
                    "不得重复罗列价格；请把篇幅重点放在信号综述与环境判断上。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第一部分：昨日复盘（如有）\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "对照昨日预测与实际行情，逐条标注✓/✗，分析错误原因，"
                    "明确说明今日是否需要修正方向。如无昨日复盘数据则跳过。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第二部分：三源信号综述（篇幅最大，本页核心价值）\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "分信源提炼素材中的实质信息，让读者不看原文即可掌握全貌。每个来源都要写，"
                    "必须点名博主/文章并带上其关键数据与论据，不得笼统概括：\n"
                    "- 【B站信号摘要】核心观点、共识方向、代表性UP主的逻辑链\n"
                    "- 【微信信号摘要】各公众号的产业判断、宏观线索、关键数据\n"
                    "- 【微博信号摘要】市场情绪温度、大V资金调仓动向与路线图\n"
                    "- 【动量与技术面摘要】指数/板块的趋势、量能、超买超卖、相对强弱要点\n"
                    "随后给出【三源交叉验证矩阵】表格，逐议题对比各源立场并给出共识度与结论：\n"
                    "  核心议题 | B站 | 微信 | 微博 | 动量 | 共识数 | 结论\n"
                    "  做多XXX | ✅看多 | ✅看多 | ⚠️谨慎 | ✅强势 | 3.5/4 | 核心进攻方向\n"
                    "（用 ✅/⚠️/❌/－ 标注立场，只有≥2源同向才可列为核心方向；最后一行汇总本周期的裁决依据）\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第三部分：市场环境判断\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "逐项给出判据再下结论，不要只写定性：\n"
                    "- 市场宽度：涨跌停家数、封板率、连板高度、温度读数\n"
                    "- 量能：两市成交额及其相对5/20日均值的变化，是放量还是缩量\n"
                    "- 资金面：两融、主力资金倾向（如有北向数据一并说明）\n"
                    "- 技术面：主要指数相对关键均线、前高前低、缺口的位置\n"
                    "- 板块：强势主线与弱势方向，资金切换路径\n"
                    "- 外部变量：隔夜美股、重要事件与数据发布日程\n"
                    "最后给出【环境定性】（过热/偏热/中性/偏冷/过冷）与【仓位基调】"
                    "（满仓/半仓/轻仓/空仓），并说明是全面行情还是结构性行情。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第四部分：核心方向与仓位配置\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "- 主线方向：只保留被2个以上信源共同支持的方向，逐条写清看多/看空的核心逻辑\n"
                    "- 板块仓位分配：各主线分别配置多少仓位，合计不超过仓位基调上限\n"
                    "- 品种优先级：同一主线内先做什么、后做什么，理由是什么\n"
                    "- 回避清单：明确不碰的方向及原因\n"
                    "本部分只讲方向与逻辑，进出场的具体价位与动作统一由第五部分表格承载。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第五部分：价格警报清单（唯一盘中触发源，供程序自动盯盘）\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "列出明日需要盘中监控的关键价格（基于支撑/阻力位和ATR），"
                    "这是全篇唯一允许出现具体触发价之处，其他章节不得重复这些价格。\n"
                    "表格四列必须满足以下硬性要求：\n"
                    "- 触发动作：只用确定性动作词并带具体量化，禁止\"观察/关注/参考/确认/待命\"等无操作含义的词；\n"
                    "  动作词限 买入/加仓/减仓/清仓/止损/止盈/持有，且必须带比例或价格，如\"止损离场\"\"减仓50%\"\"加仓至3%\"\"清仓\"\"止盈离场\"。\n"
                    "- 监控原因：必须明确方向——上方用\"阻力/高点/高位\"，下方用\"支撑/下沿/低点/低位\"，如\"跌破波段低点支撑\"\"触及20日高点阻力\"。\n"
                    "- 标的列：个股/ETF必须在名称后附6位代码（程序据此定位行情），指数写名称即可。\n"
                    "  标的 | 警报价 | 触发动作 | 监控原因\n"
                    "  XXX(600519)  | 15.2   | 止损离场 | 跌破波段低点下方支撑\n"
                    "  上证指数     | 18.5   | 止盈减半 | 触及上方整数阻力\n"
                    "紧接表格后补一小节【复合触发条件】：只用 IF...THEN... 一行一条，"
                    "写**价格以外**的可监控判据（成交额、量比、涨跌幅、高开低开、均线、连板数、炸板家数），"
                    "价格判据不得在此重复。示例：\n"
                    "  IF 开盘30分钟两市成交额>4000亿 THEN 突破确认，科技仓可加至30%\n"
                    "  IF 科创50高开>0.5% AND 量比<1.1 THEN 不追高\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第六部分：持仓组合建议\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "基于组合风险分析，给出：\n"
                    "- 总仓位调整建议（加/减/持有，目标比例XX%）\n"
                    "- 单一标的仓位调整（如有过高集中度）\n"
                    "- 对冲/防御配置\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第七部分：与昨日建议的差异\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "明确标注今日观点与昨日的差异（如有）：\n"
                    "  - 方向变化：昨日看多XXX → 今日转中性，原因...\n"
                    "  - 仓位变化：昨日建议70% → 今日建议50%，原因...\n"
                    "  - 新增/移除标的：新增关注XXX，移除关注YYY，原因...\n"
                    "如无昨日数据则标注\"首次分析\"。\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "第八部分：情景预案\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "只写情景判据与应对思路，需要引用具体价位时写\"见价格警报清单\"，不要重复罗列价格：\n"
                    "- Plan A（基准情景）：概率XX%，判据与操作思路...\n"
                    "- Plan B（上行风险）：概率XX%，触发判据与应对...\n"
                    "- Plan C（下行风险）：概率XX%，触发判据与止损思路...\n"
                    "- 黑天鹅应对预案\n\n"
                    "=== 以下为分析素材 ===\n\n"
                ),
                temperature=0.15,
                # 不覆盖 max_tokens：思考链(reasoning_tokens)与正文共用同一预算，
                # 继承 MODEL_CONFIG 的 32768 给正文留足空间（20480 时曾被思考挤掉后半篇）
            )
            
            merged_advice_path = os.path.join(self.archive_folder, f"综合投资建议_{self.current_date}.txt")
            with open(merged_advice_path, "w", encoding="utf-8") as f:
                f.write(f"综合投资建议 - {self.current_date}\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*50 + "\n\n")
                f.write(merged_advice)
            
            print(f"综合投资建议已保存到: {merged_advice_path}")
            
            # 生成盯盘参数文件（供 market_monitor.py 读取，盘中条件触发时弹窗提醒）
            # 参数服务于"下一交易日"：目标日期由程序计算（target_date），不依赖建议文本推算；
            # generated_from 指向真实的建议文件（按数据日命名）
            self._monitor_params_path = None
            try:
                from generate_monitor_params import generate_monitor_params
                monitor_file = timed(
                    "盯盘参数生成", generate_monitor_params,
                    merged_advice, target_date, self.archive_folder, use_llm=False,
                    meta_extra={"generated_from": f"综合投资建议_{self.current_date}.txt"},
                )
                if monitor_file:
                    # 解析不到任何条件时不推送空参数（此前建议正文被截断曾导致 0 条 alerts 仍照推）
                    try:
                        with open(monitor_file, "r", encoding="utf-8") as mf:
                            n_alerts = len(json.load(mf).get("alerts") or [])
                    except Exception:
                        n_alerts = -1
                    if n_alerts == 0:
                        print(f"⚠️ 盯盘参数解析到 0 条监控条件，跳过推送（请检查建议正文是否完整、价格警报清单是否存在）")
                    else:
                        self._monitor_params_path = monitor_file
                        print(f"盯盘参数已生成: {monitor_file}（{n_alerts} 条条件）")
            except Exception as e:
                print(f"生成盯盘参数失败（不影响主流程）: {e}")
            
            # 提取并保存综合投资建议的预测观点
            timed("预测观点入库", record_predictions_from_advice,
                  merged_advice, "merged", "综合分析", self.current_date, self.archive_folder)
            
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
                with stage("知识库-记录信号事件"):
                    record_signals_from_analysis(
                        momentum_results, kol_preds, None, advice_direction
                    )
            except Exception as e:
                print(f"记录信号事件到知识库失败（不影响主流程）: {e}")
            
            # 记录当日建议到历史（用于多日连续性追踪）
            try:
                timed("建议历史写入", record_advice, self.current_date, merged_advice)
            except Exception as e:
                print(f"记录建议历史失败（不影响主流程）: {e}")
            
            print("投资建议合并完成")
            return merged_advice
            
        except Exception as e:
            print(f"合并投资建议失败: {str(e)}")
            return None
    
    def _resolve_monitor_params_path(self):
        """定位盯盘参数 JSON 文件

        优先本次生成路径；否则在归档目录/根目录按文件名日期取最新的
        monitor_params_*.json（文件名日期为下一交易日，与 current_date 不一致，
        不能用 current_date 直接拼）。

        Returns:
            str 或 None: 存在的文件路径
        """
        import glob

        candidates = []
        p = getattr(self, "_monitor_params_path", None)
        if p:
            candidates.append(p)
        if self.archive_folder:
            candidates += sorted(glob.glob(
                os.path.join(self.archive_folder, "monitor_params_*.json")))
        candidates += sorted(glob.glob("monitor_params_*.json"))
        for c in candidates:
            if os.path.exists(c):
                return c
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
        timer.reset()
        
        if not skip_login:
            login_results = timed("统一登录/凭据检查", perform_unified_login)
            print("\n>>> 登录状态检查完成，开始执行任务...")
        else:
            print("\n>>> 跳过统一登录流程，直接执行任务...")
        
        print("\n>>> 任务执行顺序: 微博 → 微信 → B站")
        
        try:
            weibo_advice = timed("微博任务", self.run_weibo_task)
        except Exception as e:
            print(f"微博任务异常: {e}")
            weibo_advice = None
        
        try:
            wechat_advice = timed("微信任务", self.run_wechat_task)
        except Exception as e:
            print(f"微信任务异常: {e}")
            wechat_advice = None
        
        try:
            bili_advice = timed("B站任务", self.run_bili_task)
        except Exception as e:
            print(f"B站任务异常: {e}")
            bili_advice = None
        
        try:
            merged_advice = timed("合并投资建议", self.merge_investment_advice,
                                  bili_advice, wechat_advice, weibo_advice)
        except Exception as e:
            print(f"合并投资建议异常: {e}")
            merged_advice = None
        
        position_result = None
        match_result = None
        match_report = None
        
        if include_position:
            print("\n" + "="*60)
            print("持仓分析")
            print("="*60)
            
            positions = load_positions()
            if positions["stocks"] or positions["indices"]:
                try:
                    position_result, match_result, match_report = timed(
                        "持仓分析与KOL匹配", run_position_analysis,
                        bili_advice=bili_advice,
                        wechat_advice=wechat_advice,
                        weibo_advice=weibo_advice
                    )
                except Exception as e:
                    print(f"持仓分析异常: {e}")
                    position_result, match_result, match_report = None, None, None
                
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

                    # F10基本面与新闻公告
                    try:
                        f10_data = timed("持仓F10/新闻公告抓取", fetch_position_f10_and_news)
                        if f10_data:
                            f10_report = format_position_f10_report(f10_data)
                            f10_report_path = os.path.join(self.archive_folder, f"持仓F10新闻公告_{self.current_date}.txt")
                            with open(f10_report_path, "w", encoding="utf-8") as f:
                                f.write(f10_report)
                            print(f"持仓F10新闻公告已保存到: {f10_report_path}")
                    except Exception as e:
                        print(f"持仓F10新闻公告抓取失败（不影响主流程）: {str(e)}")
            else:
                print("暂无持仓数据，跳过持仓分析")
                print("提示: 使用 position_manager.py 添加持仓信息")
        
        print("\n" + "="*60)
        print("所有KOL分析任务完成")
        print("="*60)

        # 推送综合投资建议到微信（PushPlus）
        try:
            if merged_advice:
                ok, msg = timed("微信推送-综合建议", push_to_wechat,
                                f"KOL分析报告 {self.current_date}",
                                merged_advice
                                )
                if ok:
                    print(f"✓ 综合投资建议已推送到微信: {msg}")
                else:
                    print(f"微信推送未成功（不影响主流程）: {msg}")

                # 盯盘参数完整 JSON 单独推送：不受建议正文长度/超长截断影响，
                # 保证整个 json 都能复制使用（纯文本模板，避免 markdown 渲染干扰）
                monitor_path = self._resolve_monitor_params_path()
                if monitor_path:
                    try:
                        with open(monitor_path, "r", encoding="utf-8") as f:
                            raw_json = f.read().strip()
                        ok2, msg2 = timed("微信推送-盯盘参数", push_to_wechat,
                                          f"盯盘参数 {self.current_date}（完整JSON）",
                                          raw_json,
                                          template="txt",
                                          )
                        if ok2:
                            print(f"✓ 盯盘参数已推送到微信: {msg2}")
                        else:
                            print(f"盯盘参数推送未成功: {msg2}")
                    except Exception as e:
                        print(f"附加盯盘参数失败（不影响推送）: {e}")
                else:
                    print("未找到盯盘参数文件，跳过盯盘参数推送")
            else:
                print("无综合投资建议，跳过微信推送")
        except Exception as e:
            print(f"微信推送异常（不影响主流程）: {e}")

        # 各阶段耗时汇总（按自身耗时排序，用于定位需要优化的慢阶段）
        timer.report(f"各阶段耗时统计 - {self.current_date}")

        return {
            "bili_advice": bili_advice,
            "wechat_advice": wechat_advice,
            "weibo_advice": weibo_advice,
            "merged_advice": merged_advice,
            "position_result": position_result,
            "match_result": match_result,
            "date": self.current_date
        }

    def build_chat_context(self, result):
        """构建交互式对话的上下文消息列表
        
        将所有分析报告作为系统上下文注入，让后续对话可以引用。
        
        Args:
            result: run_all_tasks 的返回结果
        
        Returns:
            list: messages 列表
        """
        context_parts = []
        
        # 各平台原始建议
        if result.get("bili_advice"):
            context_parts.append(f"【B站投资建议】\n{result['bili_advice']}")
        if result.get("wechat_advice"):
            context_parts.append(f"【微信投资建议】\n{result['wechat_advice']}")
        if result.get("weibo_advice"):
            context_parts.append(f"【微博投资建议】\n{result['weibo_advice']}")
        
        # 动量分析报告
        momentum_path = os.path.join(self.archive_folder, f"动量分析报告_{self.current_date}.txt")
        if os.path.exists(momentum_path):
            try:
                with open(momentum_path, "r", encoding="utf-8") as f:
                    context_parts.append(f"【动量分析报告】\n{f.read()}")
            except Exception:
                pass
        
        # 市场宽度分析（复用合并阶段缓存，避免重复计算）
        market_report = getattr(self, "_market_breadth_report", None)
        if not market_report:
            try:
                market_report = run_market_breadth_analysis()
            except Exception:
                market_report = None
        if market_report:
            context_parts.append(f"【市场宽度分析】\n{market_report}")
        
        # 持仓组合风险
        try:
            risk_data = calculate_portfolio_risk()
            if risk_data:
                risk_report = format_portfolio_risk_report(risk_data)
                context_parts.append(f"【持仓组合风险分析】\n{risk_report}")
        except Exception:
            pass
        
        # 持仓匹配分析
        match_path = os.path.join(self.archive_folder, f"持仓匹配分析_{self.current_date}.txt")
        if os.path.exists(match_path):
            try:
                with open(match_path, "r", encoding="utf-8") as f:
                    context_parts.append(f"【持仓匹配分析】\n{f.read()}")
            except Exception:
                pass

        # 板块放量监控（底部放量机会 / 顶部放量风险）
        sector_path = os.path.join(self.archive_folder, f"板块放量监控报告_{self.current_date}.txt")
        if os.path.exists(sector_path):
            try:
                with open(sector_path, "r", encoding="utf-8") as f:
                    context_parts.append(f"【板块放量监控】\n{f.read()}")
            except Exception:
                pass

        # 持仓F10基本面与新闻公告
        f10_path = os.path.join(self.archive_folder, f"持仓F10新闻公告_{self.current_date}.txt")
        if os.path.exists(f10_path):
            try:
                with open(f10_path, "r", encoding="utf-8") as f:
                    context_parts.append(f"【持仓股F10基本面与新闻公告】\n{f.read()}")
            except Exception:
                pass
        
        # 信号知识库
        try:
            kb_summary = format_kb_summary_for_prompt()
            if kb_summary:
                context_parts.append(f"【信号-胜率知识库】\n{kb_summary}")
        except Exception:
            pass
        
        # 建议历史
        try:
            history_text = format_history_for_prompt(days=7)
            if history_text:
                context_parts.append(f"【过去7天建议历史】\n{history_text}")
        except Exception:
            pass
        
        # 综合投资建议（作为助手首条回复）
        merged = result.get("merged_advice", "")
        
        context_text = "\n\n---\n\n".join(context_parts) if context_parts else "暂无分析数据"
        
        system_msg = (
            "你是首席投资策略官，拥有20年多资产配置经验。"
            "以下是今日的全部分析报告和数据，作为你回答问题的背景知识。"
            "用户会基于这些报告继续提问，请结合背景数据给出精准、可操作的回答。"
            "回答时可以直接引用报告中的具体数据和价格，不需要重复整个报告。"
        )
        
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"以下是今日的全部分析报告：\n\n{context_text}"},
        ]
        
        if merged:
            messages.append({"role": "assistant", "content": merged})
        
        return messages

    def interactive_chat(self, result):
        """交互式对话模式
        
        获取投资建议后进入对话模式，可以继续提问。
        对话上下文包含所有分析报告。
        
        Args:
            result: run_all_tasks 的返回结果
        """
        messages = self.build_chat_context(result)
        
        print("\n" + "=" * 60)
        print("进入交互式对话模式")
        print("输入问题继续探讨，输入 q/quit/exit 退出")
        print("=" * 60)
        
        if result.get("merged_advice"):
            print("\n今日综合投资建议已生成，你可以基于上述建议继续提问。")
        
        while True:
            try:
                user_input = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n退出对话模式")
                break
            
            if not user_input:
                continue
            if user_input.lower() in ("q", "quit", "exit", "退出"):
                print("退出对话模式")
                break
            
            messages.append({"role": "user", "content": user_input})
            
            print("\n策略官: ", end="", flush=True)
            try:
                reply = deepseek_chat(messages, temperature=0.3, max_tokens=4096, stream=True)
                messages.append({"role": "assistant", "content": reply})
            except Exception as e:
                print(f"\n对话出错: {e}")
                # 移除失败的用户消息
                messages.pop()


if __name__ == "__main__":
    analyzer = KOLAnalyzer()
    
    try:
        result = analyzer.run_all_tasks()
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
        result = {
            "bili_advice": None,
            "wechat_advice": None,
            "weibo_advice": None,
            "merged_advice": None,
            "position_result": None,
            "match_result": None,
            "date": analyzer.current_date
        }
    except BaseException as e:
        print(f"\n任务执行异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        result = {
            "bili_advice": None,
            "wechat_advice": None,
            "weibo_advice": None,
            "merged_advice": None,
            "position_result": None,
            "match_result": None,
            "date": analyzer.current_date
        }
    
    if result:
        print("\n任务执行结果:")
        print(f"- B站投资建议: {'有' if result.get('bili_advice') else '无'}")
        print(f"- 微信投资建议: {'有' if result.get('wechat_advice') else '无'}")
        print(f"- 微博投资建议: {'有' if result.get('weibo_advice') else '无'}")
        print(f"- 综合投资建议: {'有' if result.get('merged_advice') else '无'}")
        print(f"- 持仓分析: {'有' if result.get('position_result') else '无'}")
        print(f"- 持仓匹配: {'有' if result.get('match_result') else '无'}")
        print(f"- 执行日期: {result.get('date', 'N/A')}")
        
        # 进入交互式对话模式
        analyzer.interactive_chat(result)
