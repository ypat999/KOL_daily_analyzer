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
