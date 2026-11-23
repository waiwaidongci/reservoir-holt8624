"""可行液位区间推算。

读数口径：
- ``measured`` 实测：当天晨检读数为确定值。
- ``full`` 满格：液位计到量程顶只报满格，真实液位位于 [量程顶, 池顶]。
- ``missing`` 缺报：通讯中断/换表留空，传播在此断开，不与相邻日发生关系。
- ``calibration`` 核尺实测：人工量尺的确定值，晚到时追加约束，可桥接缺报日。

相邻两个“有约束日”之间的真实液位满足 |x_j - x_i| <= D * (j - i)，
其中 D 为日最大涨跌（m/日）。每个物理上限不超过池顶、不低于 0 m。
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

_DAY = timedelta(days=1)
_EPS = 1e-9

# 晨检读数状态
MEASURED = "measured"
FULL = "full"
MISSING = "missing"
CALIBRATION = "calibration"

READING_KINDS = (MEASURED, FULL, MISSING)


@dataclass(frozen=True)
class Constraint:
    """单日液位 x 满足 lower <= x <= upper。"""

    day: date
    lower: float
    upper: float
    source: str  # measured / full / calibration
    label: str

    def clamp_physical(self, pool_top):
        lower = max(0.0, self.lower)
        upper = min(pool_top, self.upper)
        return Constraint(self.day, lower, upper, self.source, self.label)


@dataclass
class Node:
    day: date
    lower: float
    upper: float
    sources: list = field(default_factory=list)  # 当日生效约束标签
    kind: str = MEASURED
    reading: float | None = None  # 台账原始读数（满格为量程顶，缺报为 None）

    @property
    def feasible(self):
        return self.upper + _EPS >= self.lower


@dataclass
class Segment:
    nodes: list  # list[Node]，按日期升序，日期间连续

    @property
    def days(self):
        return [node.day for node in self.nodes]


@dataclass
class Conflict:
    start: date
    end: date
    days: int
    lower: float
    upper: float
    sources: list
    reason: str


def gauge_constraint(day, kind, reading, gauge_max, pool_top):
    """把一条晨检台账记录转换成单日约束；缺报返回 None。"""
    if kind == MISSING:
        return None
    if kind == FULL:
        return Constraint(day, gauge_max, pool_top, FULL, f"满格≥{gauge_max:g}m").clamp_physical(pool_top)
    return Constraint(day, reading, reading, MEASURED, f"实测{reading:g}m").clamp_physical(pool_top)


def calibration_constraint(day, level, pool_top):
    return Constraint(day, level, level, CALIBRATION, f"核尺{level:g}m").clamp_physical(pool_top)


def validate_calibration_input(reservoir_id, reading_date, level_m, pool_top=20.0):
    """核尺录入的纯函数校验，返回 (水池编号, date, float 液位)。"""
    import math
    pool = (reservoir_id or "").strip()
    if not pool or len(pool) > 40:
        raise ValueError("水池编号无效，长度需在 1–40 之间。")
    try:
        day = date.fromisoformat(reading_date)
    except (TypeError, ValueError):
        raise ValueError("核尺日期格式不正确，应为 YYYY-MM-DD。") from None
    if isinstance(level_m, bool) or not isinstance(level_m, (int, float)) or not math.isfinite(level_m):
        raise ValueError("核尺液位必须为有效数字。")
    if not 0 <= float(level_m) <= pool_top:
        raise ValueError(f"核尺液位必须在 0–{pool_top:g} m 之间。")
    return pool, day, float(level_m)


def _merge(day, constraints):
    """合并同一天的多条约束：取下界最大、上界最小。"""
    lower = max(item.lower for item in constraints)
    upper = min(item.upper for item in constraints)
    sources = [item.label for item in constraints]
    kind = constraints[0].source
    reading = constraints[0].lower if kind in (MEASURED, FULL) else None
    return Node(day=day, lower=lower, upper=upper, sources=sources, kind=kind, reading=reading)


def build_segments(reading_rows, calibration_rows, gauge_max, pool_top):
    """把台账与核尺记录切成若干连续传播段。

    缺报日断开传播；核尺即使落在缺报日上也会作为节点把前后重新连接。
    """
    gauge = {}
    missing_days = set()
    for row in reading_rows:
        day = row["day"]
        constraint = gauge_constraint(day, row["kind"], row.get("reading"), gauge_max, pool_top)
        if constraint is None:
            missing_days.add(day)
        else:
            gauge[day] = constraint

    cal_by_day = {}
    for row in calibration_rows:
        cal_by_day[row["day"]] = calibration_constraint(row["day"], row["level"], pool_top)

    constrained_days = sorted(set(gauge) | set(cal_by_day))
    if not constrained_days:
        return [], missing_days

    segments = []
    current = []
    previous = None
    for day in constrained_days:
        if previous is not None:
            span = (day - previous).days
            # 中间若存在缺报日且当天没有核尺补点，传播断开。
            broken = any((previous + k * _DAY) in missing_days
                         and (previous + k * _DAY) not in cal_by_day
                         for k in range(1, span))
            if broken:
                if current:
                    segments.append(Segment(current))
                current = []
        same_day = [item for item in (gauge.get(day), cal_by_day.get(day)) if item is not None]
        current.append(_merge(day, same_day))
        previous = day
    if current:
        segments.append(Segment(current))
    return segments, missing_days


def propagate(segment, max_change):
    """在一个连续段内做前向+后向传播，收紧各日可行区间。

    相邻 i、j（j 在 i 之后 gap 天，reach=D·gap）相容条件为
    L_j-U_i <= reach（向上可达）且 L_i-U_j <= reach（向下可达）。
    前向用 i 收紧 j：L_j=max(L_j, L_i-reach)、U_j=min(U_j, U_i+reach)；
    反向用 j 收紧 i：L_i=max(L_i, L_j-reach)、U_i=min(U_i, U_j+reach)。
    """
    nodes = segment.nodes
    for previous, node in zip(nodes, nodes[1:]):
        reach = max_change * (node.day - previous.day).days
        node.lower = max(node.lower, previous.lower - reach)
        node.upper = min(node.upper, previous.upper + reach)
    for node, following in zip(nodes[:-1], nodes[1:]):
        reach = max_change * (following.day - node.day).days
        node.lower = max(node.lower, following.lower - reach)
        node.upper = min(node.upper, following.upper + reach)
    return nodes


def find_conflicts(segment, max_change, gauge_max, pool_top):
    """找出无解处的最短冲突区间（并列取最早）。

    传播后若某日 lower>upper，越界必然在某条相邻边上显形，因此相邻节点对
    就是理论上最短的冲突区间，扫描一遍即可；单日物理越界单独列出。
    """
    nodes = segment.nodes
    edge_conflicts = []
    for left, right in zip(nodes, nodes[1:]):
        gap = (right.day - left.day).days
        reach = max_change * gap
        # 两区间存在满足 |x_right-x_left|<=reach 的取值，当且仅当：
        # 向上可达 right.lower-left.upper <= reach；向下可达 right.upper-left.lower <= reach。
        pair_clash = (right.lower - left.upper > reach + _EPS
                      or right.upper - left.lower < -reach - _EPS)
        physical_clash = (left.upper < -_EPS or left.lower > pool_top + _EPS
                          or right.upper < -_EPS or right.lower > pool_top + _EPS)
        if not (pair_clash or physical_clash):
            continue
        required = max(0.0, right.lower - left.upper, left.lower - right.upper)
        reason = (f"{left.day} 与 {right.day} 相隔 {gap} 天，日最大涨跌 {max_change:g}m "
                  f"最多允许变化 {reach:g}m，但两侧约束至少需要 {required:g}m 变化，无法同时满足")
        edge_conflicts.append(Conflict(left.day, right.day, gap + 1,
                                       min(left.lower, right.lower), max(left.upper, right.upper),
                                       sorted(set(left.sources + right.sources)), reason))
    covered = set()
    for conflict in edge_conflicts:
        covered.update((conflict.start, conflict.end))
    for node in nodes:
        if not node.feasible and node.day not in covered:
            edge_conflicts.append(Conflict(
                node.day, node.day, 1, node.lower, node.upper, list(node.sources),
                f"{node.day} 的约束区间 [{node.lower:g}, {node.upper:g}] 超出 0 m 至池顶 {pool_top:g}m 的物理范围"))
    return sorted(edge_conflicts, key=lambda item: (item.days, item.start))


def forecast_bands(segments, horizon, max_change, pool_top, threshold):
    """从最后一个传播段的末日向外推算未来各日的可行区间与触线分类。

    分类：
    - certain  必然触线：区间下界已达到预警线；
    - possible 可能触线：区间跨越预警线；
    - none     不会触线：区间上界仍低于预警线。
    """
    if not segments:
        return []
    anchor = segments[-1].nodes[-1]
    last_day = anchor.day
    bands = []
    lower, upper = anchor.lower, anchor.upper
    for step in range(1, horizon + 1):
        day = last_day + step * _DAY
        lower = max(0.0, lower - max_change)
        upper = min(pool_top, upper + max_change)
        if lower + _EPS >= threshold:
            category = "certain"
        elif upper + _EPS >= threshold:
            category = "possible"
        else:
            category = "none"
        bands.append({"date": day.isoformat(), "step": step, "lower": round(lower, 6),
                      "upper": round(upper, 6), "category": category})
    return bands
