# -*- coding: utf-8 -*-
"""阶段耗时统计

用途：给流水线的每个阶段计时，阶段结束时即时打印，最后汇总排名，便于定位瓶颈。

用法：
    from stage_timer import stage, timer

    with stage("资金面分析"):
        do_work()

    for code in codes:
        with stage(f"K线抓取 {code}"):   # 同名阶段会在汇总里合并计数
            fetch(code)

    timer.report()                       # 打印耗时排名 + 执行顺序树

说明：
- 支持嵌套；排名按"自身耗时"（总耗时减去子阶段耗时）排序，避免父子重复计数。
- 阶段名带变量时建议用 timer.norm_name 归一化，否则汇总会出现几十条不同名字。
"""

import time
from contextlib import contextmanager


def fmt_secs(secs: float) -> str:
    """人类可读的耗时"""
    if secs < 1:
        return f"{secs * 1000:.0f}ms"
    if secs < 60:
        return f"{secs:.1f}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{int(m)}分{s:.0f}秒"
    h, m = divmod(m, 60)
    return f"{int(h)}时{int(m)}分{s:.0f}秒"


def norm_name(prefix: str, variable: str) -> str:
    """归一化阶段名，让同类阶段在汇总里合并（如 'K线抓取 sh600000' -> 'K线抓取'）"""
    return prefix


class StageTimer:
    def __init__(self):
        self.records = []          # {name, group, secs, children, depth}
        self._stack = []           # 当前打开的 record 索引
        self._t0 = None
        self._running = 0          # 顶层并发/串行阶段的墙钟总览用

    def reset(self):
        self.records = []
        self._stack = []
        self._t0 = time.perf_counter()

    @contextmanager
    def stage(self, name: str, group: str = None, echo: bool = True):
        """计时一个阶段；嵌套阶段自动归属父阶段"""
        if self._t0 is None:
            self._t0 = time.perf_counter()
        depth = len(self._stack)
        start = time.perf_counter()
        rec = {"name": name, "group": group or name, "secs": 0.0, "children": 0.0, "depth": depth}
        idx = len(self.records)
        self.records.append(rec)
        self._stack.append(idx)
        try:
            yield rec
        finally:
            self._stack.pop()
            rec["secs"] = time.perf_counter() - start
            if self._stack:
                self.records[self._stack[-1]]["children"] += rec["secs"]
            if echo:
                prefix = "  " * depth
                print(f"{prefix}⏱ {name}: {fmt_secs(rec['secs'])}")

    def add(self, name: str, secs: float, group: str = None):
        """登记一个已经自己量过耗时的阶段"""
        self.records.append({"name": name, "group": group or name,
                             "secs": secs, "children": 0.0, "depth": 0})

    def report(self, title: str = "各阶段耗时统计", top: int = 20):
        """打印耗时排名（按自身耗时）与执行顺序树"""
        if not self.records:
            return
        wall = (time.perf_counter() - self._t0) if self._t0 else sum(
            r["secs"] for r in self.records if r["depth"] == 0)

        # 同名分组（含嵌套出现多次的）合并
        groups = {}
        for r in self.records:
            g = groups.setdefault(r["group"], {"name": r["group"], "total": 0.0,
                                               "self": 0.0, "count": 0, "max": 0.0})
            g["total"] += r["secs"]
            g["self"] += r["secs"] - r["children"]
            g["count"] += 1
            g["max"] = max(g["max"], r["secs"])

        ranked = sorted(groups.values(), key=lambda g: g["self"], reverse=True)
        total_self = sum(g["self"] for g in ranked) or 1.0

        print("\n" + "=" * 60)
        print(f"{title}（本次运行墙钟 {fmt_secs(wall)}）")
        print("=" * 60)
        print(f"{'排名':<4}{'阶段':<34}{'自身耗时':>10}{'占比':>8}{'次数':>6}{'最慢一次':>10}")
        print("-" * 72)
        for i, g in enumerate(ranked[:top], 1):
            pct = g["self"] / total_self * 100
            name = g["name"][:32]
            print(f"{i:<4}{name:<34}{fmt_secs(g['self']):>10}{pct:>7.1f}%"
                  f"{g['count']:>6}{fmt_secs(g['max']):>10}")
        if len(ranked) > top:
            print(f"...其余 {len(ranked) - top} 项省略")
        print("-" * 72)
        print("执行顺序：")
        for r in self.records:
            print(f"  {'  ' * r['depth']}{r['name']}: {fmt_secs(r['secs'])}")
        print("=" * 60)


timer = StageTimer()


def stage(name: str, group: str = None, echo: bool = True):
    """模块级便捷入口，使用全局 timer"""
    return timer.stage(name, group=group, echo=echo)


def timed(name: str, fn, *args, group: str = None, echo: bool = True, **kwargs):
    """计时执行一个函数调用（适合整段调用无法用 with 包裹的场景）"""
    with timer.stage(name, group=group, echo=echo):
        return fn(*args, **kwargs)


def reset():
    timer.reset()


def report(title: str = "各阶段耗时统计", top: int = 20):
    timer.report(title=title, top=top)
