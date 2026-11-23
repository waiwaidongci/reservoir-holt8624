"""校验、指纹与分析串联的可复算校验（仅标准库，python -I 运行）。

覆盖：满格/缺报 CSV 解析、同指纹幂等、晚到核尺重新约束、旧快照不变、冲突分类。
"""
import csv
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.analysis import analyze  # noqa: E402
from src.domain.demo import demo_calibrations, demo_rows  # noqa: E402
from src.services.fingerprint import fingerprint  # noqa: E402


class ValidationLike:
    """直接复用 parse_rows，避免在测试里依赖 FastAPI。"""

    @staticmethod
    def parse(text):
        from src.services.validation import parse_rows
        return parse_rows(text)


def csv_text(rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["reservoir_id", "reading_date", "level_m", "reading_kind"])
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return "﻿" + buffer.getvalue()


class WorkflowTests(unittest.TestCase):
    def test_csv_distinguishes_full_and_missing(self):
        text = csv_text([
            {"reservoir_id": "RW-02", "reading_date": "2026-11-01", "level_m": "1.0", "reading_kind": "measured"},
            {"reservoir_id": "RW-02", "reading_date": "2026-11-02", "level_m": "2.0", "reading_kind": "full"},
            {"reservoir_id": "RW-02", "reading_date": "2026-11-03", "level_m": "", "reading_kind": "missing"},
        ])
        rows = ValidationLike.parse(text)
        kinds = {row["reading_date"]: row["reading_kind"] for row in rows}
        self.assertEqual(kinds["2026-11-02"], "full")
        self.assertEqual(kinds["2026-11-03"], "missing")
        self.assertIsNone(rows[2]["level_m"])
        self.assertEqual(rows[1]["level_m"], 2.0)

    def test_missing_with_level_rejected(self):
        text = csv_text([
            {"reservoir_id": "RW-02", "reading_date": "2026-11-01", "level_m": "1.0", "reading_kind": "measured"},
            {"reservoir_id": "RW-02", "reading_date": "2026-11-02", "level_m": "1.1", "reading_kind": "missing"},
        ])
        with self.assertRaises(ValueError):
            ValidationLike.parse(text)

    def test_full_requires_level(self):
        text = csv_text([
            {"reservoir_id": "RW-02", "reading_date": "2026-11-01", "level_m": "", "reading_kind": "full"},
        ])
        with self.assertRaises(ValueError):
            ValidationLike.parse(text)

    def _snapshot(self, rows, calibrations):
        from src.services.validation import normalize_params
        params = normalize_params({})
        return {"reservoir_id": "RW-02", "dataset_id": 1, "dataset_checksum": "x",
                "readings": rows, "calibrations": calibrations, "params": params,
                "limits": {k: params[k] for k in
                           ("gauge_max_m", "pool_top_m", "max_daily_change_m")}}

    def test_rerun_same_inputs_same_fingerprint_no_drift(self):
        snapshot = self._snapshot(demo_rows(), demo_calibrations())
        first = analyze(snapshot)
        second = analyze(snapshot)
        self.assertEqual(fingerprint(snapshot), fingerprint(snapshot))
        self.assertEqual(first["band_headline"], second["band_headline"])
        self.assertEqual(first["tables"], second["tables"])

    def test_late_calibration_changes_new_run_but_old_snapshot_frozen(self):
        old_snapshot = self._snapshot(demo_rows(), [])
        old_result = analyze(old_snapshot)
        new_snapshot = self._snapshot(demo_rows(), demo_calibrations())
        new_result = analyze(new_snapshot)
        self.assertNotEqual(fingerprint(old_snapshot), fingerprint(new_snapshot))
        # 旧结果对象保持当时口径，不受新核尺影响。
        old_band = next(t for t in old_result["tables"] if t["title"] == "生效核尺记录（运行时刻快照）")
        self.assertEqual(old_band["rows"], [])
        new_band = next(t for t in new_result["tables"] if t["title"] == "生效核尺记录（运行时刻快照）")
        self.assertEqual(len(new_band["rows"]), 1)

    def test_conflict_state_and_categories_present(self):
        result = analyze(self._snapshot(demo_rows(), demo_calibrations()))
        contact = next(t for t in result["tables"] if t["title"] == "未来液位区间与触线分类")
        self.assertTrue(all(row["contact"] in ("必然触线", "可能触线", "不会触线") for row in contact["rows"]))
        self.assertIn(result["state"], ("ok", "warning", "conflict"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
