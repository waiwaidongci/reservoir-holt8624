"""人工核尺实测接口。晚到的核尺重新约束后续运行，历史报告保持冻结快照。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from src.config.database import connection
from src.constants.enums import Role
from src.controllers.auth import allow, current
from src.models.identity import User
from src.schemas.requests import CalibrationRequest
from src.services.audit import record
from src.services.calibrations import list_for_pool, upsert
from src.utils.time import iso

router = APIRouter(prefix='/calibrations')


def _view(item):
    return {'id': item.id, 'reservoir_id': item.reservoir_id,
            'reading_date': item.reading_date.isoformat(), 'level_m': item.level_m,
            'note': item.note, 'author': item.author, 'created_at': iso(item.created_at)}


@router.get('')
def index(reservoir_id: str, db: Session = Depends(connection),
          actor: User = Depends(current)):
    return [_view(item) for item in list_for_pool(db, reservoir_id.strip())]


@router.post('', status_code=201)
def create(body: CalibrationRequest, db: Session = Depends(connection),
           actor: User = Depends(allow(Role.ADMIN, Role.ANALYST))):
    item, replaced = upsert(db, body.reservoir_id, body.reading_date, body.level_m,
                            body.note or None, actor.username)
    record(db, actor.username,
           'calibration.replaced' if replaced else 'calibration.created',
           item.id, reservoir_id=item.reservoir_id, reading_date=item.reading_date.isoformat(),
           level_m=item.level_m)
    db.commit()
    return {**_view(item), 'replaced': replaced}
