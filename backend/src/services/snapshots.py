"""运行输入快照的数据库装载与同指纹复用。

同一批读数、限值、核尺和参数提交多次，指纹相同，直接复用既有运行，
不新增报告；每次运行冻结当时输入，旧报告永久保留当时口径。
"""

from sqlalchemy import select

from src.constants.enums import Status
from src.models.calibration import Calibration
from src.models.jobs import Run
from src.services.fingerprint import build_snapshot, fingerprint

__all__ = ["build_snapshot", "fingerprint", "load_calibrations", "find_existing_run"]


def load_calibrations(db, reservoir_id):
    """按水池读取当前全部生效核尺，按日期升序。"""
    records = db.scalars(
        select(Calibration)
        .where(Calibration.reservoir_id == reservoir_id)
        .order_by(Calibration.reading_date, Calibration.id)
    ).all()
    return [{"reading_date": item.reading_date.isoformat(), "level_m": item.level_m,
             "note": item.note or "", "author": item.author,
             "created_at": item.created_at.isoformat() if item.created_at else None}
            for item in records]


def find_existing_run(db, signature):
    """同指纹且未失败的运行视为可复用，不新增报告。"""
    return db.scalar(
        select(Run)
        .where(Run.fingerprint == signature, Run.status != Status.FAILED)
        .order_by(Run.id)
        .limit(1)
    )
