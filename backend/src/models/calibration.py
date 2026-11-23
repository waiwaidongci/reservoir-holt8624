from datetime import date, datetime
from sqlalchemy import Date, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
from src.utils.time import now


class Calibration(Base):
    """人工核尺实测。可晚于晨检读数录入，按水池编号和日期唯一。

    核尺是确定值约束：录入后重新约束该日前后的可行液位；历史运行的
    结果快照不被改写，新运行才会采用新增核尺。
    """

    __tablename__ = 'calibrations'
    __table_args__ = (UniqueConstraint('reservoir_id', 'reading_date', name='uq_calibration_pool_day'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    reservoir_id: Mapped[str] = mapped_column(String(40), index=True)
    reading_date: Mapped[date] = mapped_column(Date, index=True)
    level_m: Mapped[float] = mapped_column(Float)
    author: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=now)
