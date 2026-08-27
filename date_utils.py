"""
日期工具模块 - 统一处理KOL分析器中的日期逻辑
"""

from datetime import datetime, timedelta


def get_friday_date_for_weekend(current_date: datetime) -> datetime:
    """获取周末对应的周五日期"""
    weekday = current_date.weekday()  # 0=周一, 6=周日
    # 计算距离最近周五的天数
    if weekday == 4:  # 周五
        return current_date
    elif weekday > 4:  # 周六或周日
        return current_date - timedelta(days=weekday - 4)
    else:  # 周一到周四
        return current_date - timedelta(days=weekday + 3)


def get_next_trading_day(date_str: str) -> str:
    """返回 date_str 之后的下一个交易日（简单日历规则，不含法定节假日）

    周五 → 下周一(+3)，周六 → 下周一(+2)，周日 → 下周一(+1)，其余 +1。
    用于确定"明日作战计划"/盯盘参数的目标日期，避免依赖 LLM 自行推算。
    """
    d = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = d.weekday()  # 0=周一, 6=周日
    if weekday == 4:    # 周五
        d += timedelta(days=3)
    elif weekday == 5:  # 周六
        d += timedelta(days=2)
    elif weekday == 6:  # 周日
        d += timedelta(days=1)
    else:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def get_current_analysis_date():
    """
    获取当前分析应该使用的日期
    
    规则：
    1. 如果当前时间未达到当日9点，则使用前一天的日期
    2. 如果是周末（周六或周日），使用最近的周五日期
    3. 其他情况使用当天日期
    
    Returns:
        tuple: (date_str, date_reason, archive_folder)
            - date_str: 格式化日期字符串 'YYYY-MM-DD'
            - date_reason: 日期选择原因描述
            - archive_folder: 归档文件夹名称
    """
    # 获取当前时间
    now = datetime.now()
    
    # 确定使用的日期：周末使用周五，凌晨使用前一天
    current_date = now
    date_reason = "当前日期"

    if now.hour < 9:
        # 如果当前时间未达到当日9点，则使用前一天的日期
        current_date = (current_date - timedelta(days=1))
        date_reason = "凌晨运行，使用昨天日期"
    
    weekday = current_date.weekday()  # 0=周一, 6=周日
    # 检查是否为周末 (周六或周日)
    is_weekend = weekday >= 5  # 5=周六, 6=周日

    if is_weekend:
        # 使用优化的函数计算最近的周五日期
        friday_date = get_friday_date_for_weekend(now)
        date_str = friday_date.strftime('%Y-%m-%d')
        date_reason = f"周末({['周六','周日'][weekday-5]})，使用周五日期"
    else:
        date_str = current_date.strftime('%Y-%m-%d')
        date_reason = f"正常运行，使用当天日期"
    
    archive_folder = f'archive_{date_str}'
    
    return date_str, date_reason, archive_folder


def ensure_archive_folder(archive_folder):
    """
    确保归档文件夹存在，如果不存在则创建
    
    Args:
        archive_folder (str): 归档文件夹路径
    
    Returns:
        bool: 文件夹是否已存在或创建成功
    """
    import os
    if not os.path.exists(archive_folder):
        os.makedirs(archive_folder)
        print(f"已创建日期归档文件夹: {archive_folder}")
        return True
    return False


def print_date_info():
    """
    打印当前分析日期信息
    """
    date_str, date_reason, archive_folder = get_current_analysis_date()
    print(f"📅 {date_reason}: {date_str}")
    return date_str, date_reason, archive_folder