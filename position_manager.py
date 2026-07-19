import json
import os
from datetime import datetime
from momentum_analyzer import get_stock_kline, calculate_momentum_factors, get_index_kline

POSITION_FILE = "positions.json"

def load_positions():
    """加载持仓数据"""
    if not os.path.exists(POSITION_FILE):
        return {"stocks": [], "indices": [], "last_update": None}
    
    try:
        with open(POSITION_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"加载持仓数据失败: {e}")
        return {"stocks": [], "indices": [], "last_update": None}

def save_positions(positions):
    """保存持仓数据"""
    positions["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(POSITION_FILE, 'w', encoding='utf-8') as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
        print(f"持仓数据已保存到 {POSITION_FILE}")
        return True
    except Exception as e:
        print(f"保存持仓数据失败: {e}")
        return False

def add_stock_position(code, name, shares, cost_price, account="default"):
    """添加股票持仓
    
    Args:
        code: 股票代码 (如 "600519")
        name: 股票名称 (如 "贵州茅台")
        shares: 持仓股数
        cost_price: 成本价
        account: 账户名称 (如 "海通证券")
    """
    positions = load_positions()
    
    new_stock = {
        "code": code,
        "name": name,
        "shares": shares,
        "cost_price": cost_price,
        "account": account,
        "add_date": datetime.now().strftime("%Y-%m-%d")
    }
    
    existing_idx = None
    for i, s in enumerate(positions["stocks"]):
        if s["code"] == code and s["account"] == account:
            existing_idx = i
            break
    
    if existing_idx is not None:
        positions["stocks"][existing_idx] = new_stock
        print(f"更新持仓: {name}({code}) - {shares}股 @ {cost_price}")
    else:
        positions["stocks"].append(new_stock)
        print(f"添加持仓: {name}({code}) - {shares}股 @ {cost_price}")
    
    return save_positions(positions)

def add_index_position(code, name, shares, cost_price, account="default"):
    """添加指数/ETF持仓"""
    positions = load_positions()
    
    new_index = {
        "code": code,
        "name": name,
        "shares": shares,
        "cost_price": cost_price,
        "account": account,
        "add_date": datetime.now().strftime("%Y-%m-%d")
    }
    
    existing_idx = None
    for i, s in enumerate(positions["indices"]):
        if s["code"] == code and s["account"] == account:
            existing_idx = i
            break
    
    if existing_idx is not None:
        positions["indices"][existing_idx] = new_index
        print(f"更新持仓: {name}({code}) - {shares}份 @ {cost_price}")
    else:
        positions["indices"].append(new_index)
        print(f"添加持仓: {name}({code}) - {shares}份 @ {cost_price}")
    
    return save_positions(positions)

def remove_position(code, account="default", is_index=False):
    """删除持仓"""
    positions = load_positions()
    key = "indices" if is_index else "stocks"
    
    for i, p in enumerate(positions[key]):
        if p["code"] == code and p["account"] == account:
            removed = positions[key].pop(i)
            print(f"已删除: {removed['name']}({code})")
            return save_positions(positions)
    
    print(f"未找到持仓: {code}")
    return False

def list_positions():
    """列出所有持仓"""
    positions = load_positions()
    
    print("\n" + "=" * 60)
    print("当前持仓列表")
    print("=" * 60)
    
    if positions["stocks"]:
        print("\n【股票持仓】")
        print("-" * 40)
        for s in positions["stocks"]:
            print(f"  {s['name']}({s['code']}) - {s['shares']}股 @ {s['cost_price']} - 账户: {s['account']}")
    
    if positions["indices"]:
        print("\n【指数/ETF持仓】")
        print("-" * 40)
        for idx in positions["indices"]:
            print(f"  {idx['name']}({idx['code']}) - {idx['shares']}份 @ {idx['cost_price']} - 账户: {idx['account']}")
    
    if not positions["stocks"] and not positions["indices"]:
        print("\n暂无持仓数据")
    
    print(f"\n最后更新: {positions.get('last_update', 'N/A')}")
    print("=" * 60)
    
    return positions

def analyze_position_momentum():
    """分析持仓的动量因子
    
    Returns:
        dict: 持仓动量分析结果
    """
    positions = load_positions()
    results = {
        "stocks": [],
        "indices": [],
        "analysis_date": datetime.now().strftime("%Y-%m-%d"),
        "total_market_value": 0,
        "total_profit_loss": 0
    }
    
    print("\n" + "=" * 50)
    print("持仓动量分析")
    print("=" * 50)
    
    for stock in positions["stocks"]:
        code = stock["code"]
        name = stock["name"]
        shares = stock["shares"]
        cost_price = stock["cost_price"]
        account = stock.get("account", "default")
        
        print(f"\n分析股票: {name}({code})")
        df = get_stock_kline(code)
        
        if df is not None:
            factors = calculate_momentum_factors(df)
            latest_price = float(df['收盘'].iloc[-1])
            market_value = latest_price * shares
            profit_loss = (latest_price - cost_price) * shares
            profit_pct = (latest_price / cost_price - 1) * 100
            
            if factors:
                stock_result = {
                    "code": code,
                    "name": name,
                    "shares": shares,
                    "cost_price": cost_price,
                    "latest_price": latest_price,
                    "market_value": round(market_value, 2),
                    "profit_loss": round(profit_loss, 2),
                    "profit_pct": round(profit_pct, 2),
                    "account": account,
                    "momentum_factors": factors
                }
                results["stocks"].append(stock_result)
                results["total_market_value"] += market_value
                results["total_profit_loss"] += profit_loss
                
                print(f"  最新价: {latest_price:.2f} | 成本: {cost_price:.2f}")
                print(f"  盈亏: {profit_loss:+.2f} ({profit_pct:+.2f}%)")
                print(f"  20日收益率: {factors['return_20d']}%")
                print(f"  60日收益率: {factors['return_60d']}%")
                if factors['trend_strength']:
                    print(f"  趋势强度: {factors['trend_strength']['trend_level']} ({factors['trend_strength']['overall_strength']})")
                if factors.get('breakout') and factors['breakout'].get('is_new_high'):
                    print(f"  ★★★ 20日新高突破!")
        else:
            print(f"  无法获取数据")
    
    for idx in positions["indices"]:
        code = idx["code"]
        name = idx["name"]
        shares = idx["shares"]
        cost_price = idx["cost_price"]
        account = idx.get("account", "default")
        
        print(f"\n分析指数/ETF: {name}({code})")
        df = get_index_kline(code)
        
        if df is not None:
            factors = calculate_momentum_factors(df)
            latest_price = float(df['收盘'].iloc[-1])
            market_value = latest_price * shares
            profit_loss = (latest_price - cost_price) * shares
            profit_pct = (latest_price / cost_price - 1) * 100
            
            if factors:
                idx_result = {
                    "code": code,
                    "name": name,
                    "shares": shares,
                    "cost_price": cost_price,
                    "latest_price": latest_price,
                    "market_value": round(market_value, 2),
                    "profit_loss": round(profit_loss, 2),
                    "profit_pct": round(profit_pct, 2),
                    "account": account,
                    "momentum_factors": factors
                }
                results["indices"].append(idx_result)
                results["total_market_value"] += market_value
                results["total_profit_loss"] += profit_loss
                
                print(f"  最新价: {latest_price:.2f} | 成本: {cost_price:.2f}")
                print(f"  盈亏: {profit_loss:+.2f} ({profit_pct:+.2f}%)")
                print(f"  20日收益率: {factors['return_20d']}%")
                print(f"  60日收益率: {factors['return_60d']}%")
                if factors['trend_strength']:
                    print(f"  趋势强度: {factors['trend_strength']['trend_level']} ({factors['trend_strength']['overall_strength']})")
        else:
            print(f"  无法获取数据")
    
    return results

def check_position_match_kol(positions, kol_targets):
    """检查持仓与KOL推荐的匹配性
    
    Args:
        positions: 持仓数据
        kol_targets: KOL推荐的标的 (从momentum_analyzer提取)
    
    Returns:
        dict: 匹配分析结果
    """
    match_results = {
        "matched_stocks": [],
        "matched_indices": [],
        "unmatched_positions": [],
        "unmatched_kol_stocks": [],
        "unmatched_kol_indices": [],
        "analysis_date": datetime.now().strftime("%Y-%m-%d")
    }
    
    position_stock_codes = set()
    for s in positions.get("stocks", []):
        position_stock_codes.add(s["code"])
    
    position_index_codes = set()
    for idx in positions.get("indices", []):
        position_index_codes.add(idx["code"])
    
    kol_stock_codes = set()
    for s in kol_targets.get("stocks", []):
        kol_stock_codes.add(s.get("code", ""))
    
    kol_index_codes = set()
    for idx in kol_targets.get("indices", []):
        kol_index_codes.add(idx.get("code", ""))
    
    matched_stock_codes = position_stock_codes & kol_stock_codes
    matched_index_codes = position_index_codes & kol_index_codes
    
    for code in matched_stock_codes:
        pos_info = next((s for s in positions.get("stocks", []) if s["code"] == code), None)
        kol_info = next((s for s in kol_targets.get("stocks", []) if s.get("code") == code), None)
        match_results["matched_stocks"].append({
            "code": code,
            "position": pos_info,
            "kol_info": kol_info,
            "match_type": "持仓与KOL推荐一致"
        })
    
    for code in matched_index_codes:
        pos_info = next((idx for idx in positions.get("indices", []) if idx["code"] == code), None)
        kol_info = next((idx for idx in kol_targets.get("indices", []) if idx.get("code") == code), None)
        match_results["matched_indices"].append({
            "code": code,
            "position": pos_info,
            "kol_info": kol_info,
            "match_type": "持仓与KOL推荐一致"
        })
    
    for s in positions.get("stocks", []):
        if s["code"] not in matched_stock_codes:
            match_results["unmatched_positions"].append({
                "code": s["code"],
                "name": s["name"],
                "type": "股票",
                "status": "持仓但未被KOL推荐"
            })
    
    for idx in positions.get("indices", []):
        if idx["code"] not in matched_index_codes:
            match_results["unmatched_positions"].append({
                "code": idx["code"],
                "name": idx["name"],
                "type": "指数",
                "status": "持仓但未被KOL推荐"
            })
    
    for s in kol_targets.get("stocks", []):
        code = s.get("code", "")
        if code and code not in matched_stock_codes:
            match_results["unmatched_kol_stocks"].append({
                "code": code,
                "name": s.get("name", ""),
                "reasons": s.get("reasons", []),
                "status": "KOL推荐但未持仓"
            })
    
    for idx in kol_targets.get("indices", []):
        code = idx.get("code", "")
        if code and code not in matched_index_codes:
            match_results["unmatched_kol_indices"].append({
                "code": code,
                "name": idx.get("name", ""),
                "reasons": idx.get("reasons", []),
                "status": "KOL推荐但未持仓"
            })
    
    return match_results

def format_match_report(match_results):
    """格式化匹配分析报告"""
    report_lines = []
    report_lines.append("\n" + "=" * 60)
    report_lines.append("持仓与KOL推荐匹配分析")
    report_lines.append(f"分析日期: {match_results['analysis_date']}")
    report_lines.append("=" * 60)
    
    if match_results["matched_stocks"] or match_results["matched_indices"]:
        report_lines.append("\n【持仓与KOL推荐一致】✓")
        report_lines.append("-" * 40)
        for m in match_results["matched_stocks"]:
            pos = m["position"]
            report_lines.append(f"  ★ {pos['name']}({pos['code']}) - 持仓: {pos['shares']}股")
            if m.get("kol_info"):
                reasons = m["kol_info"].get("reasons", [])
                if reasons:
                    report_lines.append(f"    KOL关注原因: {'; '.join(reasons)}")
        for m in match_results["matched_indices"]:
            pos = m["position"]
            report_lines.append(f"  ★ {pos['name']}({pos['code']}) - 持仓: {pos['shares']}份")
            if m.get("kol_info"):
                reasons = m["kol_info"].get("reasons", [])
                if reasons:
                    report_lines.append(f"    KOL关注原因: {'; '.join(reasons)}")
    
    if match_results["unmatched_positions"]:
        report_lines.append("\n【持仓但未被KOL推荐】⚠")
        report_lines.append("-" * 40)
        for u in match_results["unmatched_positions"]:
            report_lines.append(f"  - {u['name']}({u['code']}) [{u['type']}]")
    
    if match_results["unmatched_kol_stocks"] or match_results["unmatched_kol_indices"]:
        report_lines.append("\n【KOL推荐但未持仓】💡")
        report_lines.append("-" * 40)
        for u in match_results["unmatched_kol_stocks"]:
            reasons = u.get("reasons", [])
            reason_str = f" - {'; '.join(reasons)}" if reasons else ""
            report_lines.append(f"  + {u['name']}({u['code']}) [股票]{reason_str}")
        for u in match_results["unmatched_kol_indices"]:
            reasons = u.get("reasons", [])
            reason_str = f" - {'; '.join(reasons)}" if reasons else ""
            report_lines.append(f"  + {u['name']}({u['code']}) [指数]{reason_str}")
    
    total_matched = len(match_results["matched_stocks"]) + len(match_results["matched_indices"])
    total_positions = total_matched + len(match_results["unmatched_positions"])
    total_kol = total_matched + len(match_results["unmatched_kol_stocks"]) + len(match_results["unmatched_kol_indices"])
    
    report_lines.append("\n" + "-" * 40)
    report_lines.append(f"匹配统计: {total_matched}/{total_positions} 持仓被KOL推荐")
    report_lines.append(f"潜在机会: {len(match_results['unmatched_kol_stocks']) + len(match_results['unmatched_kol_indices'])} 个KOL推荐未持仓")
    report_lines.append("=" * 60)
    
    return "\n".join(report_lines)

def calculate_portfolio_risk(positions=None):
    """计算持仓组合风险
    
    包含：
    1. 仓位集中度（单一标的占比）
    2. 板块集中度
    3. 组合最大回撤
    4. 持仓间相关性
    5. 组合Beta（相对大盘）
    
    Args:
        positions: 持仓数据，默认从文件加载
    
    Returns:
        dict: 组合风险分析结果
    """
    import numpy as np
    
    if positions is None:
        positions = load_positions()
    
    stocks = positions.get("stocks", [])
    if not stocks:
        return None
    
    # 1. 仓位集中度
    total_value = 0
    position_values = []
    for s in stocks:
        df = get_stock_kline(s["code"], days=5)
        if df is not None and len(df) > 0:
            current_price = float(df['收盘'].iloc[-1])
        else:
            current_price = s["cost_price"]
        value = current_price * s["shares"]
        position_values.append({
            "code": s["code"],
            "name": s["name"],
            "value": round(value, 2),
            "weight": 0,  # 稍后计算
        })
        total_value += value
    
    if total_value == 0:
        return None
    
    for pv in position_values:
        pv["weight"] = round(pv["value"] / total_value * 100, 2)
    
    # 按权重排序
    position_values.sort(key=lambda x: x["weight"], reverse=True)
    max_weight = position_values[0]["weight"] if position_values else 0
    top3_weight = sum(pv["weight"] for pv in position_values[:3])
    
    # 2. 板块集中度（用代码前3位粗略分组，实际应查板块归属）
    sector_map = {}
    for s in stocks:
        # 简化：用代码前3位作为板块代理
        sector = s["code"][:3]
        sector_map.setdefault(sector, []).append(s["name"])
    sector_concentration = {
        sector: len(names) for sector, names in sector_map.items()
    }
    max_sector_count = max(sector_concentration.values()) if sector_concentration else 0
    sector_concentration_ratio = max_sector_count / len(stocks) * 100 if stocks else 0
    
    # 3. 组合最大回撤 & 4. 相关性 & 5. Beta
    # 获取每只股票的历史收益率序列
    returns_data = {}
    benchmark_returns = None
    
    # 获取基准（上证指数）收益率
    try:
        bench_df = get_index_kline("000001", days=60)
        if bench_df is not None and len(bench_df) > 20:
            bench_close = bench_df['收盘'].values.astype(float)
            benchmark_returns = np.diff(bench_close) / bench_close[:-1]
    except Exception:
        pass
    
    for s in stocks:
        try:
            df = get_stock_kline(s["code"], days=60)
            if df is not None and len(df) > 20:
                close = df['收盘'].values.astype(float)
                returns = np.diff(close) / close[:-1]
                returns_data[s["code"]] = returns
        except Exception:
            continue
    
    # 组合等权收益率
    portfolio_returns = None
    if returns_data:
        min_len = min(len(r) for r in returns_data.values())
        aligned_returns = np.column_stack([r[-min_len:] for r in returns_data.values()])
        portfolio_returns = np.mean(aligned_returns, axis=1)
    
    # 最大回撤
    max_drawdown = 0
    if portfolio_returns is not None and len(portfolio_returns) > 0:
        cum_returns = np.cumprod(1 + portfolio_returns)
        running_max = np.maximum.accumulate(cum_returns)
        drawdowns = (cum_returns - running_max) / running_max
        max_drawdown = float(np.min(drawdowns)) * 100
    
    # 相关性矩阵
    correlation_matrix = None
    if len(returns_data) >= 2:
        min_len = min(len(r) for r in returns_data.values())
        aligned = np.column_stack([r[-min_len:] for r in returns_data.values()])
        if aligned.shape[1] >= 2:
            corr = np.corrcoef(aligned, rowvar=False)
            codes = list(returns_data.keys())
            correlation_matrix = {
                "codes": codes,
                "matrix": np.round(corr, 2).tolist(),
            }
            # 平均相关性（排除对角线）
            n = corr.shape[0]
            if n > 1:
                off_diag = corr[~np.eye(n, dtype=bool)]
                avg_correlation = float(np.mean(off_diag))
            else:
                avg_correlation = 0
        else:
            avg_correlation = 0
    else:
        avg_correlation = 0
    
    # 组合Beta
    portfolio_beta = None
    if portfolio_returns is not None and benchmark_returns is not None:
        min_len = min(len(portfolio_returns), len(benchmark_returns))
        if min_len > 10:
            port = portfolio_returns[-min_len:]
            bench = benchmark_returns[-min_len:]
            bench_var = np.var(bench)
            if bench_var > 0:
                portfolio_beta = float(np.cov(port, bench)[0, 1] / bench_var)
    
    # 风险评级
    risk_warnings = []
    if max_weight > 30:
        risk_warnings.append(f"单一标的「{position_values[0]['name']}」占比{max_weight}%过高（>30%）")
    if top3_weight > 60:
        risk_warnings.append(f"前三大持仓占比{top3_weight}%过高（>60%）")
    if sector_concentration_ratio > 50:
        risk_warnings.append(f"板块集中度{sector_concentration_ratio:.0f}%过高")
    if avg_correlation > 0.7:
        risk_warnings.append(f"持仓间平均相关性{avg_correlation:.2f}过高（>0.7），分散度不足")
    if max_drawdown < -15:
        risk_warnings.append(f"组合近期最大回撤{max_drawdown:.1f}%较大")
    if portfolio_beta is not None and portfolio_beta > 1.3:
        risk_warnings.append(f"组合Beta={portfolio_beta:.2f}偏高，波动大于大盘")
    
    if not risk_warnings:
        risk_level = "低风险"
    elif len(risk_warnings) <= 2:
        risk_level = "中风险"
    else:
        risk_level = "高风险"
    
    return {
        "total_value": round(total_value, 2),
        "position_count": len(stocks),
        "position_weights": position_values,
        "max_single_weight": max_weight,
        "top3_weight": round(top3_weight, 2),
        "sector_concentration": sector_concentration,
        "sector_concentration_ratio": round(sector_concentration_ratio, 1),
        "max_drawdown": round(max_drawdown, 2),
        "avg_correlation": round(avg_correlation, 2),
        "correlation_matrix": correlation_matrix,
        "portfolio_beta": round(portfolio_beta, 2) if portfolio_beta else None,
        "risk_level": risk_level,
        "risk_warnings": risk_warnings,
    }


def format_portfolio_risk_report(risk_data):
    """格式化组合风险报告
    
    Args:
        risk_data: calculate_portfolio_risk() 返回的结果
    
    Returns:
        str: 格式化的风险报告文本
    """
    if not risk_data:
        return ""
    
    lines = []
    lines.append("=" * 60)
    lines.append("持仓组合风险分析报告")
    lines.append("=" * 60)
    
    lines.append(f"\n【组合概览】")
    lines.append(f"  总市值: ¥{risk_data['total_value']:,.2f}")
    lines.append(f"  持仓数量: {risk_data['position_count']}只")
    lines.append(f"  风险等级: {risk_data['risk_level']}")
    
    lines.append(f"\n【仓位集中度】")
    lines.append(f"  最大单一标的占比: {risk_data['max_single_weight']}%")
    lines.append(f"  前三大持仓占比: {risk_data['top3_weight']}%")
    lines.append(f"  各持仓权重:")
    for pv in risk_data['position_weights']:
        lines.append(f"    {pv['name']}({pv['code']}): {pv['weight']}% (¥{pv['value']:,.2f})")
    
    lines.append(f"\n【板块集中度】")
    lines.append(f"  最大板块占比: {risk_data['sector_concentration_ratio']}%")
    for sector, count in risk_data['sector_concentration'].items():
        lines.append(f"    板块{sector}: {count}只")
    
    lines.append(f"\n【组合波动风险】")
    lines.append(f"  近期最大回撤: {risk_data['max_drawdown']}%")
    lines.append(f"  持仓间平均相关性: {risk_data['avg_correlation']}")
    if risk_data.get('portfolio_beta'):
        lines.append(f"  组合Beta: {risk_data['portfolio_beta']}（相对上证指数）")
    
    if risk_data['risk_warnings']:
        lines.append(f"\n【⚠️ 风险预警】")
        for w in risk_data['risk_warnings']:
            lines.append(f"  ⚠️ {w}")
    else:
        lines.append(f"\n【风险预警】无显著风险")
    
    lines.append("=" * 60)
    
    return "\n".join(lines)


# ==================== F10 基本面与新闻公告抓取 ====================

def _safe_float(v):
    """安全转 float，nan/inf/None 返回 None（akshare 偶发 nan）"""
    if v is None:
        return None
    try:
        import math
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def fetch_stock_f10(code):
    """抓取个股F10基本面信息
    
    整合公司概况 + 主要股东 + 财务摘要（最近4期）
    
    Args:
        code: 股票代码（如 "600519"）
    
    Returns:
        dict 或 None: 包含 profile、shareholders、financials 三个子项
    """
    import akshare as ak
    
    result = {"profile": None, "shareholders": None, "financials": None}
    
    # 1. 公司概况（巨潮资讯）
    try:
        df = ak.stock_profile_cninfo(symbol=code)
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            result["profile"] = {
                "name": str(row.get("公司名称", "")),
                "industry": str(row.get("所属行业", "")),
                "market": str(row.get("所属市场", "")),
                "list_date": str(row.get("上市日期", "")),
                "registered_capital": str(row.get("注册资金", "")),
                "main_business": str(row.get("主营业务", ""))[:200],
            }
    except Exception as e:
        print(f"  获取{code}公司概况失败: {e}")
    
    # 2. 主要股东（巨潮资讯）
    try:
        df = ak.stock_main_stock_holder(stock=code)
        if df is not None and len(df) > 0:
            # 按截至日期取最新一期
            latest_date = df["截至日期"].iloc[0] if "截至日期" in df.columns else None
            latest_df = df[df["截至日期"] == latest_date] if latest_date else df.head(10)
            
            top5 = []
            for _, row in latest_df.head(5).iterrows():
                top5.append({
                    "name": str(row.get("股东名称", "")),
                    "shares": _safe_float(row.get("持股数量")),
                    "ratio": _safe_float(row.get("持股比例")),
                    "type": str(row.get("股本性质", "")),
                })
            
            total_holders = None
            if "股东总数" in latest_df.columns:
                vals = latest_df["股东总数"].dropna()
                total_holders = float(vals.iloc[0]) if len(vals) > 0 else None
            
            result["shareholders"] = {
                "as_of_date": str(latest_date),
                "top5": top5,
                "total_holders": total_holders,
            }
    except Exception as e:
        print(f"  获取{code}主要股东失败: {e}")
    
    # 3. 财务摘要（新浪，取最近4期）
    try:
        df = ak.stock_financial_abstract(symbol=code)
        if df is not None and len(df) > 0:
            # 日期列从第3列开始
            date_cols = [c for c in df.columns if c not in ("选项", "指标")]
            recent_dates = date_cols[:4] if len(date_cols) >= 4 else date_cols
            
            financials = []
            # 关键指标行
            key_metrics = ["归母净利润", "营业总收入", "营业成本", "净利润", "基本每股收益"]
            for metric in key_metrics:
                row = df[df["指标"] == metric]
                if len(row) > 0:
                    values = {}
                    for d in recent_dates:
                        val = row[d].iloc[0]
                        values[d] = _safe_float(val)
                    financials.append({"metric": metric, "values": values})
            
            result["financials"] = {
                "report_dates": recent_dates,
                "metrics": financials,
            }
    except Exception as e:
        print(f"  获取{code}财务摘要失败: {e}")
    
    # 至少有一个数据源成功才返回
    if not any(result.values()):
        return None
    
    return result


def fetch_stock_news(code, limit=5):
    """抓取个股近期新闻（东财新闻接口）
    
    Args:
        code: 股票代码
        limit: 返回条数上限
    
    Returns:
        list: 新闻列表，每条含 title/time/source/summary
    """
    import akshare as ak
    
    try:
        df = ak.stock_news_em(symbol=code)
        if df is None or len(df) == 0:
            return []
        
        news_list = []
        for _, row in df.head(limit).iterrows():
            content = str(row.get("新闻内容", ""))
            news_list.append({
                "title": str(row.get("新闻标题", "")),
                "time": str(row.get("发布时间", "")),
                "source": str(row.get("文章来源", "")),
                "summary": content[:100],
            })
        return news_list
    except Exception as e:
        print(f"  获取{code}新闻失败: {e}")
        return []


def fetch_stock_announcements(code, days=3):
    """抓取个股近期公告（巨潮资讯全市场公告按代码过滤）
    
    Args:
        code: 股票代码
        days: 查询最近几天
    
    Returns:
        list: 公告列表，每条含 title/date/type
    """
    import akshare as ak
    from datetime import datetime, timedelta
    
    announcements = []
    today = datetime.now()
    
    for i in range(days):
        check_date = today - timedelta(days=i)
        date_str = check_date.strftime("%Y%m%d")
        
        try:
            df = ak.stock_notice_report(symbol="全部", date=date_str)
            if df is None or len(df) == 0:
                continue
            
            # 按代码过滤
            matched = df[df["代码"] == code]
            if len(matched) == 0:
                continue
            
            for _, row in matched.head(3).iterrows():
                announcements.append({
                    "title": str(row.get("公告标题", "")),
                    "date": str(row.get("公告日期", "")),
                    "type": str(row.get("公告类型", "")),
                })
            
            if len(announcements) >= 3:
                break
        except Exception as e:
            print(f"  获取{code}公告失败({date_str}): {e}")
            continue
    
    return announcements[:3]


def fetch_position_restricted_release(days=30):
    """一次性获取未来N天全市场限售解禁明细，按持仓股代码分组

    一次调用 stock_restricted_release_detail_em 获取全市场解禁数据，
    再按持仓股代码过滤，避免逐股查询导致大量请求。

    Args:
        days: 查询未来N天内的解禁

    Returns:
        dict: {code: [{date, type, shares, actual_shares, market_value, ratio}, ...]}
    """
    import akshare as ak
    from datetime import datetime, timedelta

    try:
        start_date = datetime.now().strftime("%Y%m%d")
        end_date = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
        df = ak.stock_restricted_release_detail_em(
            start_date=start_date, end_date=end_date
        )
        if df is None or df.empty:
            return {}

        # 按"股票代码"列分组
        code_col = "股票代码" if "股票代码" in df.columns else df.columns[1]
        restricted_map = {}
        for _, row in df.iterrows():
            code = str(row.get(code_col, "")).strip()
            if not code:
                continue
            restricted_map.setdefault(code, []).append({
                "date": str(row.get("解禁时间", ""))[:10],
                "type": str(row.get("限售股类型", "")),
                "shares": _safe_float(row.get("解禁数量")),
                "actual_shares": _safe_float(row.get("实际解禁数量")),
                "market_value": _safe_float(row.get("实际解禁市值")),
                "ratio": _safe_float(row.get("占解禁前流通市值比例")),
            })
        return restricted_map
    except Exception as e:
        print(f"获取全市场限售解禁失败: {e}")
        return {}


def fetch_position_f10_and_news():
    """遍历所有持仓股，抓取F10基本面和新闻公告

    Returns:
        list: 每个元素为 {code, name, f10, news, announcements, restricted_release}
    """
    import time

    positions = load_positions()
    stocks = positions.get("stocks", [])

    if not stocks:
        print("暂无持仓，跳过F10抓取")
        return None

    print(f"\n开始抓取 {len(stocks)} 只持仓股的F10与新闻公告...")

    # 一次性获取全市场未来30天解禁，按持仓股过滤
    print("获取全市场限售解禁数据（一次性）...")
    restricted_map = fetch_position_restricted_release(days=30)
    if restricted_map:
        print(f"  全市场解禁数据获取成功，共 {len(restricted_map)} 只股票有待解禁")

    results = []

    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock["name"]
        print(f"[{i+1}/{len(stocks)}] 抓取 {name}({code})...")

        entry = {
            "code": code,
            "name": name,
            "f10": None,
            "news": [],
            "announcements": [],
            "restricted_release": restricted_map.get(code, []),
        }

        try:
            entry["f10"] = fetch_stock_f10(code)
        except Exception as e:
            print(f"  F10抓取异常: {e}")

        try:
            entry["news"] = fetch_stock_news(code, limit=5)
        except Exception as e:
            print(f"  新闻抓取异常: {e}")

        try:
            entry["announcements"] = fetch_stock_announcements(code, days=3)
        except Exception as e:
            print(f"  公告抓取异常: {e}")

        results.append(entry)

        # 限流：非最后一只股票时等待
        if i < len(stocks) - 1:
            time.sleep(1.5)

    print(f"F10与新闻公告抓取完成，共 {len(results)} 只")
    return results


def format_position_f10_report(f10_data):
    """格式化持仓F10+新闻公告报告
    
    Args:
        f10_data: fetch_position_f10_and_news() 返回的列表
    
    Returns:
        str: 格式化的报告文本
    """
    if not f10_data:
        return ""
    
    lines = []
    lines.append("=" * 60)
    lines.append("持仓股F10基本面与新闻公告报告")
    lines.append(f"分析日期: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 60)
    
    for entry in f10_data:
        code = entry["code"]
        name = entry["name"]
        f10 = entry.get("f10")
        news = entry.get("news", [])
        anns = entry.get("announcements", [])
        restricted = entry.get("restricted_release", [])
        
        lines.append(f"\n{'─' * 40}")
        lines.append(f"  {name}({code})")
        lines.append(f"{'─' * 40}")
        
        # F10 基本面
        if f10:
            profile = f10.get("profile")
            if profile:
                lines.append(f"\n  【公司概况】")
                lines.append(f"    所属行业: {profile.get('industry', 'N/A')}")
                lines.append(f"    所属市场: {profile.get('market', 'N/A')}")
                lines.append(f"    上市日期: {profile.get('list_date', 'N/A')}")
                lines.append(f"    注册资金: {profile.get('registered_capital', 'N/A')}")
                biz = profile.get('main_business', '')
                if biz:
                    lines.append(f"    主营业务: {biz}")
            
            shareholders = f10.get("shareholders")
            if shareholders:
                lines.append(f"\n  【主要股东】截至 {shareholders.get('as_of_date', 'N/A')}")
                if shareholders.get('total_holders'):
                    th = shareholders['total_holders']
                    lines.append(f"    股东总数: {int(th):,}")
                for j, sh in enumerate(shareholders.get("top5", []), 1):
                    ratio = sh.get("ratio")
                    ratio_str = f"{ratio:.2f}%" if ratio is not None else "N/A"
                    lines.append(f"    {j}. {sh['name']} - {ratio_str} ({sh['type']})")
            
            financials = f10.get("financials")
            if financials and financials.get("metrics"):
                lines.append(f"\n  【财务摘要】")
                dates = financials.get("report_dates", [])
                # 表头
                header = "    指标"
                for d in dates:
                    header += f" | {d}"
                lines.append(header)
                lines.append("    " + "-" * (len(header) - 4))
                for m in financials["metrics"]:
                    row_str = f"    {m['metric']}"
                    for d in dates:
                        val = m["values"].get(d)
                        if val is not None:
                            if abs(val) >= 1e8:
                                row_str += f" | {val/1e8:.2f}亿"
                            elif abs(val) >= 1e4:
                                row_str += f" | {val/1e4:.2f}万"
                            else:
                                row_str += f" | {val:.2f}"
                        else:
                            row_str += " | N/A"
                    lines.append(row_str)
        else:
            lines.append(f"\n  【F10基本面】数据获取失败")
        
        # 近期新闻
        if news:
            lines.append(f"\n  【近期新闻】{len(news)}条")
            for n in news:
                lines.append(f"    • [{n['time']}] {n['title']} ({n['source']})")
        else:
            lines.append(f"\n  【近期新闻】无")
        
        # 近期公告
        if anns:
            lines.append(f"\n  【近期公告】{len(anns)}条")
            for a in anns:
                lines.append(f"    • [{a['date']}] {a['title']} [{a['type']}]")
        else:
            lines.append(f"\n  【近期公告】无")

        # 限售解禁（未来30天）
        if restricted:
            lines.append(f"\n  【限售解禁（未来30天）】{len(restricted)}笔")
            total_value = sum(r.get("market_value", 0) or 0 for r in restricted)
            for r in restricted:
                mv = r.get("market_value") or 0
                mv_yi = mv / 1e8 if mv else 0
                ratio = r.get("ratio") or 0
                shares = r.get("shares") or 0
                shares_wan = shares / 1e4 if shares else 0
                lines.append(
                    f"    • [{r.get('date', 'N/A')}] {r.get('type', '')} | "
                    f"解禁{shares_wan:.2f}万股 | 市值{mv_yi:.2f}亿 | "
                    f"占流通{ratio:.2f}%"
                )
            if total_value:
                lines.append(f"    合计解禁市值: {total_value/1e8:.2f}亿")
        else:
            lines.append(f"\n  【限售解禁】未来30天无解禁 ✓")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def run_position_analysis(bili_advice=None, wechat_advice=None, weibo_advice=None):
    """运行完整的持仓分析流程
    
    Args:
        bili_advice: B站投资建议
        wechat_advice: 微信投资建议
        weibo_advice: 微博投资建议
    
    Returns:
        tuple: (持仓动量结果, 匹配分析结果, 匹配报告)
    """
    from momentum_analyzer import extract_key_targets, merge_targets
    
    positions = load_positions()
    
    if not positions["stocks"] and not positions["indices"]:
        print("暂无持仓数据，请先添加持仓")
        print("使用 add_stock_position() 或 add_index_position() 添加持仓")
        return None, None, None
    
    position_momentum = analyze_position_momentum()
    
    all_targets = []
    if bili_advice:
        targets = extract_key_targets(bili_advice, "B站")
        all_targets.append(targets)
    if wechat_advice:
        targets = extract_key_targets(wechat_advice, "微信")
        all_targets.append(targets)
    if weibo_advice:
        targets = extract_key_targets(weibo_advice, "微博")
        all_targets.append(targets)
    
    if all_targets:
        kol_targets = merge_targets(all_targets)
        match_results = check_position_match_kol(positions, kol_targets)
        match_report = format_match_report(match_results)
        print(match_report)
    else:
        match_results = None
        match_report = None
    
    return position_momentum, match_results, match_report

def interactive_add_position():
    """交互式添加持仓"""
    print("\n" + "=" * 50)
    print("添加持仓")
    print("=" * 50)
    
    pos_type = input("类型 (1=股票, 2=指数/ETF): ").strip()
    is_index = pos_type == "2"
    
    code = input("代码 (如 600519): ").strip()
    name = input("名称 (如 贵州茅台): ").strip()
    shares = float(input("数量: ").strip())
    cost_price = float(input("成本价: ").strip())
    account = input("账户 (默认: 海通证券): ").strip() or "海通证券"
    
    if is_index:
        add_index_position(code, name, shares, cost_price, account)
    else:
        add_stock_position(code, name, shares, cost_price, account)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        
        if cmd == "list":
            list_positions()
        elif cmd == "add":
            interactive_add_position()
        elif cmd == "analyze":
            analyze_position_momentum()
        elif cmd == "remove":
            if len(sys.argv) >= 3:
                code = sys.argv[2]
                account = sys.argv[3] if len(sys.argv) > 3 else "海通证券"
                remove_position(code, account)
            else:
                print("用法: python position_manager.py remove <代码> [账户]")
        else:
            print("用法: python position_manager.py [list|add|analyze|remove]")
    else:
        list_positions()
        print("\n命令: list|add|analyze|remove")
