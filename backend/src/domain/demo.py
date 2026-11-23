"""澄湾会展园 RW-02 的固定晨检台账。

读数状态：measured=实测，full=液位计满格（真实液位在量程顶到池顶之间），
missing=通讯中断/换表缺报（传播断开）。11 月 5–19 日为连续实测段，
保证滚动验证和留出评估有足够样本；末日 20 日为满格，演示区间推算。
"""


# (日期, 读数状态, 液位 m)。满格液位记录量程顶 2.0 m；缺报液位为 None。
_LEDGER = (
    (1, "measured", 0.83),
    (2, "measured", 0.87),
    (3, "measured", 0.89),
    (4, "missing", None),
    (5, "measured", 0.94),
    (6, "measured", 1.02),
    (7, "measured", 1.06),
    (8, "measured", 1.13),
    (9, "measured", 1.23),
    (10, "measured", 1.25),
    (11, "measured", 1.35),
    (12, "measured", 1.37),
    (13, "measured", 1.44),
    (14, "measured", 1.51),
    (15, "measured", 1.59),
    (16, "measured", 1.65),
    (17, "measured", 1.72),
    (18, "measured", 1.76),
    (19, "measured", 1.83),
    (20, "full", 2.0),
)


def demo_rows() -> list[dict]:
    return [
        {"reservoir_id": "RW-02", "reading_date": f"2026-11-{day:02d}",
         "level_m": level, "reading_kind": kind}
        for day, kind, level in _LEDGER
    ]


def demo_calibrations() -> list[dict]:
    """补录在缺报日（11 月 4 日）的人工核尺，用于演示桥接与重新约束。"""
    return [
        {"reservoir_id": "RW-02", "reading_date": "2026-11-04", "level_m": 0.92,
         "note": "通讯中断当日人工量尺补测"},
    ]
