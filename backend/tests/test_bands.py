"""可行液位区间引擎的可复算校验（仅标准库，python -I 运行）。"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from domain.bands import (  # noqa: E402
    CALIBRATION, FULL, MEASURED, MISSING, build_segments, find_conflicts,
    forecast_bands, gauge_constraint, propagate, validate_calibration_input,
)

D = date.fromisoformat


def r(day, kind, reading=None):
    return {"day": D(day), "kind": kind, "reading": reading}


def cal(day, level):
    return {"day": D(day), "level": level}


class ConstraintEngineTests(unittest.TestCase):
    def test_gauge_constraint_kinds(self):
        self.assertIsNone(gauge_constraint(D("2026-11-04"), MISSING, None, 2.0, 2.4))
        full = gauge_constraint(D("2026-11-03"), FULL, 2.0, 2.0, 2.4)
        self.assertEqual((full.lower, full.upper), (2.0, 2.4))
        measured = gauge_constraint(D("2026-11-01"), MEASURED, 1.2, 2.0, 2.4)
        self.assertEqual((measured.lower, measured.upper), (1.2, 1.2))

    def test_full_scale_band_is_range_top_to_pool_top(self):
        segs, _ = build_segments([r("2026-11-01", FULL, 2.0)], [], 2.0, 2.4)
        node = segs[0].nodes[0]
        self.assertEqual((node.lower, node.upper), (2.0, 2.4))
        self.assertEqual(node.kind, FULL)

    def test_missing_breaks_propagation(self):
        rows = [r("2026-11-01", MEASURED, 1.0), r("2026-11-02", MISSING),
                r("2026-11-03", MEASURED, 1.2)]
        segs, missing = build_segments(rows, [], 2.0, 2.4)
        self.assertEqual(len(segs), 2)
        self.assertEqual(missing, {D("2026-11-02")})

    def test_late_calibration_bridges_missing_and_retightens(self):
        rows = [r("2026-11-01", MEASURED, 1.0), r("2026-11-02", MISSING),
                r("2026-11-03", MEASURED, 1.4)]
        segs, _ = build_segments(rows, [cal("2026-11-02", 1.2)], 2.0, 2.4)
        self.assertEqual(len(segs), 1)
        nodes = propagate(segs[0], 0.3)
        self.assertEqual([n.lower for n in nodes], [1.0, 1.2, 1.4])
        self.assertTrue(all(n.feasible for n in nodes))
        self.assertIn("核尺1.2m", nodes[1].sources)

    def test_calibration_contradicts_gauge_reading(self):
        rows = [r("2026-11-02", MEASURED, 1.0)]
        segs, _ = build_segments(rows, [cal("2026-11-02", 1.8)], 2.0, 2.4)
        node = segs[0].nodes[0]
        self.assertFalse(node.feasible)
        self.assertEqual(node.lower, 1.8)
        self.assertEqual(node.upper, 1.0)

    def test_feasible_full_scale_with_enough_change(self):
        rows = [r("2026-11-01", MEASURED, 1.0), r("2026-11-02", MEASURED, 1.1),
                r("2026-11-03", FULL, 2.0)]
        segs, _ = build_segments(rows, [], 2.0, 2.4)
        nodes = propagate(segs[0], 0.9)
        self.assertTrue(all(n.feasible for n in nodes))
        self.assertEqual((nodes[2].lower, nodes[2].upper), (2.0, 2.0))
        self.assertEqual(find_conflicts(segs[0], 0.9, 2.0, 2.4), [])

    def test_infeasible_rise_and_drop(self):
        for pair in ((1.0, 2.0), (2.0, 1.0)):
            segs, _ = build_segments(
                [r("2026-11-01", MEASURED, pair[0]), r("2026-11-02", MEASURED, pair[1])],
                [], 2.4, 2.4)
            propagate(segs[0], 0.2)
            conflicts = find_conflicts(segs[0], 0.2, 2.4, 2.4)
            self.assertEqual(len(conflicts), 1)
            self.assertEqual((conflicts[0].start, conflicts[0].end),
                             (D("2026-11-01"), D("2026-11-02")))

    def test_shortest_conflict_interval_tie_picks_earliest(self):
        # 两个跨度相同的独立冲突，最早的排第一，且都列出。
        rows = [r("2026-11-01", MEASURED, 0.0), r("2026-11-02", MEASURED, 1.0),
                r("2026-11-03", MEASURED, 1.1), r("2026-11-04", MEASURED, 0.1),
                r("2026-11-05", MEASURED, 1.1)]
        segs, _ = build_segments(rows, [], 2.4, 2.4)
        propagate(segs[0], 0.2)
        conflicts = find_conflicts(segs[0], 0.2, 2.4, 2.4)
        self.assertTrue(conflicts)
        self.assertTrue(all(c.days == 2 for c in conflicts))
        self.assertEqual(conflicts[0].start, D("2026-11-01"))

    def test_multi_day_gap_tolerance(self):
        feasible, _ = build_segments(
            [r("2026-11-01", MEASURED, 1.0), r("2026-11-03", MEASURED, 1.3)], [], 2.4, 2.4)
        propagate(feasible[0], 0.2)
        self.assertEqual(find_conflicts(feasible[0], 0.2, 2.4, 2.4), [])
        clash, _ = build_segments(
            [r("2026-11-01", MEASURED, 1.0), r("2026-11-03", MEASURED, 1.5)], [], 2.4, 2.4)
        propagate(clash[0], 0.2)
        conflicts = find_conflicts(clash[0], 0.2, 2.4, 2.4)
        self.assertEqual(conflicts[0].days, 3)

    def test_forecast_categories(self):
        # 末日实测恰在预警线，次日整段下界仍 >= 预警线 -> 必然触线。
        segs, _ = build_segments([r("2026-11-01", MEASURED, 2.05)], [], 2.4, 2.4)
        bands = forecast_bands(segs, 1, 0.0, 2.4, 2.05)
        self.assertEqual(bands[0]["category"], "certain")
        # 满格当日真实液位可高于量程顶，次日可能回落到预警线之下 -> 可能触线。
        segs_full, _ = build_segments([r("2026-11-01", FULL, 2.05)], [], 2.05, 2.4)
        self.assertEqual(forecast_bands(segs_full, 1, 0.1, 2.4, 2.05)[0]["category"], "possible")
        self.assertTrue(all(b["lower"] <= b["upper"] for b in bands))
        # 远离预警线 -> 不会触线。
        segs2, _ = build_segments([r("2026-11-01", MEASURED, 0.5)], [], 2.4, 2.4)
        bands2 = forecast_bands(segs2, 2, 0.1, 2.4, 2.05)
        self.assertEqual([b["category"] for b in bands2], ["none", "none"])
        # 区间跨越预警线 -> 可能触线。
        segs3, _ = build_segments([r("2026-11-01", MEASURED, 2.0)], [], 2.4, 2.4)
        bands3 = forecast_bands(segs3, 1, 0.1, 2.4, 2.05)
        self.assertEqual(bands3[0]["category"], "possible")

    def test_physical_ceiling_and_floor(self):
        segs, _ = build_segments([r("2026-11-01", MEASURED, 2.4)], [], 2.4, 2.4)
        bands = forecast_bands(segs, 2, 0.5, 2.4, 2.05)
        self.assertLessEqual(bands[0]["upper"], 2.4)
        segs2, _ = build_segments([r("2026-11-01", MEASURED, 0.0)], [], 2.4, 2.4)
        bands2 = forecast_bands(segs2, 2, 0.5, 2.4, 2.05)
        self.assertGreaterEqual(bands2[0]["lower"], 0.0)

    def test_calibration_input_validation(self):
        pool, day, level = validate_calibration_input(" RW-02 ", "2026-11-04", 0.92, pool_top=2.4)
        self.assertEqual((pool, level), ("RW-02", 0.92))
        self.assertEqual(day, D("2026-11-04"))
        for bad in (("", "2026-11-04", 1.0), ("RW-02", "bad", 1.0),
                    ("RW-02", "2026-11-04", -0.1), ("RW-02", "2026-11-04", 2.5),
                    ("RW-02", "2026-11-04", True)):
            with self.assertRaises(ValueError):
                validate_calibration_input(*bad, pool_top=2.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
