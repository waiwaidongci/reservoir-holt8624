"""人工核尺实测的录入与查询。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.domain.bands import validate_calibration_input
from src.models.calibration import Calibration


def list_for_pool(db, reservoir_id):
    records = db.scalars(
        select(Calibration)
        .where(Calibration.reservoir_id == reservoir_id)
        .order_by(Calibration.reading_date.desc(), Calibration.id.desc())
        .limit(200)
    ).all()
    return records


def upsert(db, reservoir_id, reading_date, level_m, note, actor, pool_top=20.0):
    """按（水池，日期）唯一录入；同日再次提交即覆盖，并保留覆盖审计。"""
    pool, day, level = validate_calibration_input(reservoir_id, reading_date, level_m, pool_top)
    existing = db.scalar(
        select(Calibration)
        .where(Calibration.reservoir_id == pool, Calibration.reading_date == day)
        .with_for_update()
    )
    replaced = existing is not None
    if existing:
        existing.level_m = level
        existing.note = note
        existing.author = actor
        record_obj = existing
    else:
        record_obj = Calibration(reservoir_id=pool, reading_date=day, level_m=level,
                                 note=note, author=actor)
        db.add(record_obj)
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            record_obj = db.scalar(
                select(Calibration)
                .where(Calibration.reservoir_id == pool, Calibration.reading_date == day)
                .with_for_update()
            )
            record_obj.level_m = level
            record_obj.note = note
            record_obj.author = actor
            replaced = True
    return record_obj, replaced
