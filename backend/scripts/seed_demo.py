import json
from sqlalchemy import func, select
from src.config.database import Session
from src.constants.enums import Role
from src.domain.demo import demo_calibrations, demo_rows
from src.domain.spec import SPEC
from src.models.calibration import Calibration
from src.models.identity import User
from src.models.source import Dataset
from src.services.calibrations import upsert
from src.services.datasets import save
from src.utils.security import hash_password


def seed():
    with Session.begin() as db:
        for role in Role:
            if not db.get(User, role.value):
                db.add(User(username=role.value, role=role, password_hash=hash_password(f'{role.value}-demo-2611')))
        item, created = save(db, SPEC['default_dataset'], SPEC['sample_name'], demo_rows(), 'seed')
        cal_count = 0
        for cal in demo_calibrations():
            _, _ = upsert(db, cal['reservoir_id'], cal['reading_date'], cal['level_m'],
                          cal.get('note'), 'seed')
            cal_count += 1
        return {'users': db.scalar(select(func.count()).select_from(User)),
                'datasets': db.scalar(select(func.count()).select_from(Dataset)),
                'calibrations': db.scalar(select(func.count()).select_from(Calibration)),
                'seed_dataset_id': item.id, 'row_count': item.row_count, 'checksum': item.checksum,
                'created_dataset': created, 'calibration_rows': cal_count}


if __name__ == '__main__':
    print(json.dumps(seed(), ensure_ascii=False))
