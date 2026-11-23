"""Chronological reservoir forecasts with separated selection and holdout."""

from datetime import date, timedelta
from math import isclose, sqrt
from statistics import fmean

from . import bands as band_engine
from .spec import SPEC


_DAY = timedelta(days=1)
_MIN_TRAINING = 3
_MIN_ROLLING = 2
_ALERT_TOLERANCE_M = 1e-12
_MODELS = {"holt": "Holt 趋势", "level_only": "仅水平"}
# Prefer the simpler model when the errors are equal.
_SELECTION_ORDER = ("level_only", "holt")
_PREDICTIONS = (("holt", "Holt 趋势（m）"), ("level_only", "仅水平（m）"))
_EVALUATION = (("date", "实测日期"), ("step", "提前天数"), ("actual", "实测（m）")) + _PREDICTIONS
_CONTACT_LABEL = {"certain": "必然触线", "possible": "可能触线", "none": "不会触线"}


def validate_rows(rows):
    """Validate a single pool's daily readings and return them in date order."""
    readings = {}
    reservoir_id = None
    for index, row in enumerate(rows, start=1):
        try:
            pool = row["reservoir_id"].strip()
            day = date.fromisoformat(row["reading_date"])
            kind = row.get("reading_kind", band_engine.MEASURED)
            level = row["level_m"]
            if not pool or len(pool) > 40 or kind not in band_engine.READING_KINDS:
                raise ValueError
            if kind == band_engine.MISSING:
                if level is not None:
                    raise ValueError
            else:
                if isinstance(level, bool) or not isinstance(level, (int, float)) or not 0 <= level <= 20:
                    raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError):
            raise ValueError(f"第 {index} 条晨检读数无效，请检查水池编号、日期、读数状态和 0–20 m 液位。") from None
        if reservoir_id is not None and pool != reservoir_id:
            raise ValueError("同一批次只能预测一座水池，请按水池编号拆分 CSV。")
        if day in readings:
            raise ValueError(f"{day} 存在重复晨检日期，请保留当天唯一的有效读数。")
        reservoir_id = pool
        readings[day] = {"kind": kind, "level": float(level) if level is not None else None}
    if not readings:
        raise ValueError("缺少晨检读数，请先导入单池日液位台账。")
    return reservoir_id, sorted(readings.items())


def _options(params):
    fields = {field["key"]: field for field in SPEC["params"]}
    if set(params) - fields.keys():
        raise ValueError("存在未知预测参数，请重新打开预测设置。")
    values = {}
    for key, field in fields.items():
        value = params.get(key, field["default"])
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not field["min"] <= value <= field["max"]
                or (field["type"] == "int" and not isinstance(value, int))):
            raise ValueError(f'{field["label"]}必须为 {field["min"]}–{field["max"]} 范围内的有效值。')
        values[key] = value
    return values


def _point_series(readings):
    """Holt/仅水平模型只使用确定点值：实测与核尺（满格、缺报不进入点模型）。"""
    points = []
    for day, item in readings:
        if item["kind"] in (band_engine.MEASURED, band_engine.CALIBRATION):
            points.append((day, item["level"]))
    return points


def _recent_segment(readings):
    """点模型在缺报处重置，只取截止日所属的连续实测段。"""
    points = _point_series(readings)
    start = 0
    gaps = []
    for index, ((previous, _), (current, _)) in enumerate(zip(points, points[1:]), start=1):
        missing = (current - previous).days - 1
        if missing:
            gaps.append({"start": (previous + _DAY).isoformat(), "end": (current - _DAY).isoformat(), "days": missing})
            start = index
    return points[start:], gaps


def _gauge_rows(readings, gauge_max):
    rows = []
    for day, item in readings:
        if item["kind"] == band_engine.FULL:
            # 满格台账记录的读数即量程顶；以参数量程顶为准，避免抄写偏差。
            rows.append({"day": day, "kind": band_engine.FULL, "reading": gauge_max})
        elif item["kind"] == band_engine.MEASURED:
            rows.append({"day": day, "kind": band_engine.MEASURED, "reading": item["level"]})
        else:
            rows.append({"day": day, "kind": band_engine.MISSING})
    return rows


def _calibration_rows(calibrations):
    return [{"day": date.fromisoformat(item["reading_date"]), "level": item["level_m"]}
            for item in calibrations]


class _Models:
    def __init__(self, first, second, alpha, beta):
        self.alpha = alpha
        self.beta = beta
        self.holt_level = second
        self.trend = second - first
        self.level_only = first + alpha * (second - first)

    def predict(self, step):
        return {"holt": max(0.0, self.holt_level + step * self.trend), "level_only": self.level_only}

    def update(self, actual):
        previous_level = self.holt_level
        projected_level = previous_level + self.trend
        self.holt_level = projected_level + self.alpha * (actual - projected_level)
        self.trend += self.beta * (self.holt_level - previous_level - self.trend)
        self.level_only += self.alpha * (actual - self.level_only)


def _evaluation_row(models, reading, step):
    day, actual = reading
    return {"date": day.isoformat(), "step": step, "actual": actual, **models.predict(step)}


def _errors(rows, model):
    errors = [abs(row[model] - row["actual"]) for row in rows]
    return fmean(errors), sqrt(fmean(error * error for error in errors))


def _table(title, columns, rows):
    return {"title": title, "columns": [{"key": key, "label": label} for key, label in columns], "rows": rows}


def _chart(title, points):
    return {"title": title, "kind": "line", "series": [{"label": day, "value": value} for day, value in points]}


def _band_tables(segments, conflicts, bands):
    """可行液位区间、冲突区间与未来触线分类三张证据表。"""
    kind_label = {"measured": "实测", "full": "满格", "calibration": "核尺"}
    band_rows = []
    for segment in segments:
        for node in segment.nodes:
            band_rows.append({
                "date": node.day.isoformat(),
                "kind": kind_label.get(node.kind, node.kind),
                "reading": node.reading,
                "lower": round(node.lower, 6),
                "upper": round(node.upper, 6),
                "feasible": node.feasible,
                "sources": "；".join(node.sources),
            })
    band_table = _table(
        "各日可行液位区间",
        (("date", "日期"), ("kind", "读数状态"), ("reading", "台账/核尺读数（m）"),
         ("lower", "可行下界（m）"), ("upper", "可行上界（m）"),
         ("feasible", "区间是否成立"), ("sources", "约束来源")),
        band_rows,
    )
    contact_rows = [{
        "date": item["date"], "step": item["step"],
        "lower": item["lower"], "upper": item["upper"],
        "category": item["category"], "contact": _CONTACT_LABEL[item["category"]],
    } for item in bands]
    contact_table = _table(
        "未来液位区间与触线分类",
        (("date", "预报日期"), ("step", "提前天数"), ("lower", "区间下界（m）"),
         ("upper", "区间上界（m）"), ("contact", "触线分类")),
        contact_rows,
    )
    conflict_rows = [{
        "start": item.start.isoformat(), "end": item.end.isoformat(), "days": item.days,
        "lower": round(item.lower, 6), "upper": round(item.upper, 6),
        "sources": "；".join(item.sources), "reason": item.reason,
    } for item in conflicts]
    conflict_table = _table(
        "无解冲突区间（最短优先，并列取最早）",
        (("start", "冲突首日"), ("end", "冲突末日"), ("days", "区间天数"),
         ("lower", "收紧下界（m）"), ("upper", "收紧上界（m）"),
         ("sources", "冲突约束"), ("reason", "冲突说明")),
        conflict_rows,
    )
    return band_table, contact_table, conflict_table


def analyze(snapshot, normalized_params=None):
    """依据运行时刻冻结的输入快照完成点模型与可行区间两套计算。

    snapshot: {"reservoir_id", "readings": [...], "calibrations": [...],
               "limits": {"gauge_max_m", "pool_top_m", "max_daily_change_m"},
               "params": {...}}
    """
    reservoir_id, readings = validate_rows(snapshot["readings"])
    options = _options(normalized_params if normalized_params is not None else snapshot["params"])
    limits = snapshot.get("limits", {})
    gauge_max = limits.get("gauge_max_m", options["gauge_max_m"])
    pool_top = limits.get("pool_top_m", options["pool_top_m"])
    max_change = limits.get("max_daily_change_m", options["max_daily_change_m"])
    horizon = options["forecast_days"]
    threshold = options["alert_level_m"]
    holdout_days = options["holdout_days"]

    # ---- 可行区间引擎：满格按[量程顶,池顶]，缺报断开，核尺重新约束前后 ----
    gauge_rows = _gauge_rows(readings, gauge_max)
    calibration_rows = _calibration_rows(snapshot.get("calibrations", []))
    segments, _missing = band_engine.build_segments(gauge_rows, calibration_rows, gauge_max, pool_top)
    conflicts = []
    for segment in segments:
        band_engine.propagate(segment, max_change)
        conflicts.extend(band_engine.find_conflicts(segment, max_change, gauge_max, pool_top))
    conflicts.sort(key=lambda item: (item.days, item.start))
    bands = band_engine.forecast_bands(segments, horizon, max_change, pool_top, threshold)

    # ---- 点值模型（Holt/仅水平）：仅吃确定实测，缺报重置 ----
    recent, gaps = _recent_segment(readings)
    required = _MIN_TRAINING + _MIN_ROLLING + holdout_days
    if len(recent) < required:
        raise ValueError(
            f"最近连续段仅有 {len(recent)} 条确定实测，至少需要 {required} 条才能完成滚动验证和留出评估。"
            "请补录缺测、核尺或缩短留出期。")
    last_day, last_level = recent[-1]
    if (date.max - last_day).days < horizon:
        raise ValueError("预报日期超出日历范围，请缩短未来预报天数。")

    selection_rows = recent[:-holdout_days]
    holdout_rows = recent[-holdout_days:]
    models = _Models(recent[0][1], recent[1][1], options["alpha"], options["beta"])
    for _, actual in selection_rows[2:_MIN_TRAINING]:
        models.update(actual)
    rolling = []
    for reading in selection_rows[_MIN_TRAINING:]:
        rolling.append(_evaluation_row(models, reading, 1))
        models.update(reading[1])
    rolling_scores = {model: _errors(rolling, model) for model in _MODELS}
    winner = min(_SELECTION_ORDER, key=lambda model: rolling_scores[model][0])
    holdout = [_evaluation_row(models, reading, step) for step, reading in enumerate(holdout_rows, start=1)]
    holdout_scores = {model: _errors(holdout, model) for model in _MODELS}
    for _, actual in holdout_rows:
        models.update(actual)

    forecasts = []
    for step in range(1, horizon + 1):
        day = (last_day + step * _DAY).isoformat()
        estimates = models.predict(step)
        selected = estimates[winner]
        reaches_alert = selected >= threshold or isclose(selected, threshold, rel_tol=0, abs_tol=_ALERT_TOLERANCE_M)
        forecasts.append({"date": day, "step": step, **estimates, "selected": selected, "reaches_alert": reaches_alert})
    if last_level >= threshold:
        trigger = last_day.isoformat()
        headline = f"末日实测已达预警线 · {trigger}"
    else:
        trigger = next((row["date"] for row in forecasts if row["reaches_alert"]), None)
        headline = f"预计 {trigger} 首次达到高位预警线" if trigger else f"未来 {horizon} 天预计未触线"

    first_certain = next((item["date"] for item in bands if item["category"] == "certain"), None)
    first_possible = next((item["date"] for item in bands if item["category"] == "possible"), None)
    if conflicts:
        first_conflict = conflicts[0]
        band_headline = (f"约束无解：最短冲突区间 {first_conflict.start} 至 {first_conflict.end}"
                         f"（{first_conflict.days} 天）")
    elif first_certain:
        band_headline = f"必然触线：最早 {first_certain} 整个可行区间已不低于预警线"
    elif first_possible:
        band_headline = f"可能触线：最早 {first_possible} 可行区间跨越预警线"
    else:
        band_headline = f"未来 {horizon} 天可行区间上界均低于预警线，不会触线"

    comparison = [
        {"model": label, "rolling_mae": rolling_scores[model][0], "rolling_rmse": rolling_scores[model][1],
         "holdout_mae": holdout_scores[model][0], "holdout_rmse": holdout_scores[model][1], "selected": model == winner}
        for model, label in _MODELS.items()
    ]
    band_table, contact_table, conflict_table = _band_tables(segments, conflicts, bands)
    calibration_table = _table(
        "生效核尺记录（运行时刻快照）",
        (("reading_date", "核尺日期"), ("level_m", "核尺液位（m）"), ("note", "备注")),
        [{"reading_date": item["reading_date"], "level_m": item["level_m"], "note": item.get("note", "")}
         for item in snapshot.get("calibrations", [])],
    )
    tables = [
        _table("逐日预报", (("date", "预报日期"), ("step", "提前天数")) + _PREDICTIONS
               + (("selected", "采用预报（m）"), ("reaches_alert", "达到预警线")), forecasts),
        contact_table,
        band_table,
        conflict_table,
        calibration_table,
        _table("模型误差对照", (("model", "模型"), ("rolling_mae", "滚动 MAE（m）"),
               ("rolling_rmse", "滚动 RMSE（m）"), ("holdout_mae", "留出 MAE（m）"),
               ("holdout_rmse", "留出 RMSE（m）"), ("selected", "采用")), comparison),
        _table("滚动单步验证", _EVALUATION, rolling),
        _table("固定起点留出评估", _EVALUATION, holdout),
        _table("日读数缺测区间", (("start", "缺测首日"), ("end", "缺测末日"), ("days", "缺测天数")), gaps),
    ]
    full_days = sum(1 for _, item in readings if item["kind"] == band_engine.FULL)
    missing_count = sum(1 for _, item in readings if item["kind"] == band_engine.MISSING)
    cal_count = len(snapshot.get("calibrations", []))
    return {
        "state": "conflict" if conflicts else ("warning" if (trigger or gaps) else "ok"),
        "headline": headline,
        "band_headline": band_headline,
        "has_conflict": bool(conflicts),
        "note": (f"{reservoir_id} 点模型使用 {recent[0][0]} 至 {last_day} 的 {len(recent)} 条连续确定实测，"
                 f"预报采用{_MODELS[winner]}模型；可行区间按日最大涨跌 {max_change:g}m/日、"
                 f"量程顶 {gauge_max:g}m、池顶 {pool_top:g}m 推算。"),
        "limits": {"gauge_max_m": gauge_max, "pool_top_m": pool_top,
                   "max_daily_change_m": max_change, "alert_level_m": threshold},
        "contact_summary": {"certain": first_certain, "possible": first_possible,
                            "none": not (first_certain or first_possible)},
        "metrics": [
            {"label": "末日实测（m）", "value": last_level, "detail": last_day.isoformat()},
            {"label": "预警线（m）", "value": threshold, "detail": "达到预警线即计入"},
            {"label": "首次点预测触线", "value": trigger or "窗口内未触线",
             "detail": "末日实测" if last_level >= threshold else "采用模型预报"},
            {"label": "必然触线首日", "value": first_certain or "窗口内无", "detail": "整段可行区间≥预警线"},
            {"label": "可能触线首日", "value": first_possible or "窗口内无", "detail": "可行区间跨越预警线"},
            {"label": "满格/缺报/核尺", "value": f"{full_days}/{missing_count}/{cal_count}",
             "detail": "满格天数/缺报天数/核尺条数"},
            {"label": "冲突区间数", "value": len(conflicts), "detail": "无解时见最短冲突区间表"},
            {"label": "滚动验证天数", "value": len(rolling),
             "detail": f'{rolling[0]["date"]} 至 {rolling[-1]["date"]}'},
            {"label": "留出评估天数", "value": holdout_days,
             "detail": f'{holdout[0]["date"]} 至 {holdout[-1]["date"]}'},
        ],
        "tables": tables,
        "charts": [
            _chart("连续段实测液位（m，最近 30 天）", ((day.isoformat(), level) for day, level in recent[-30:])),
            _chart(f"{_MODELS[winner]}逐日预报（m）", ((row["date"], row["selected"]) for row in forecasts)),
        ],
        "sections": [
            {"title": "评估时间边界", "text": f"模型仅按留出前的滚动 MAE 选择。留出预测统一以 {selection_rows[-1][0]} 为起点。留出评估结束后，将这些实测纳入未来预报。"},
            {"title": "满格与缺报口径", "text": "液位计到量程顶只报满格：满格日真实液位按量程顶到池顶计为一个区间；缺报日断开相邻传播，不与前后日发生涨跌关系。"},
            {"title": "核尺与历史报告", "text": "晚到的核尺作为确定值重新约束该日前后可行液位，并可桥接缺报日；每次运行冻结当时的读数、限值与核尺快照，旧报告仍能查到当时口径。"},
            {"title": "触线分类", "text": "必然触线指该日整个可行区间都不低于预警线；可能触线指区间跨越预警线；不会触线指区间上界仍低于预警线。约束无解时给出最短冲突区间，多个并列取最早。"},
        ],
    }
