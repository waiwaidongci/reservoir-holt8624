"""运行输入快照与指纹的纯函数部分（不依赖数据库）。"""

import hashlib
import json


def canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False, default=str)


def fingerprint(snapshot):
    """同一批读数、限值、核尺和参数得到同一指纹，用于幂等重跑。"""
    return hashlib.sha256(canonical(snapshot).encode()).hexdigest()


def build_snapshot(dataset, params, calibrations):
    """组装运行时刻冻结的输入快照。"""
    return {
        "reservoir_id": dataset.rows[0]["reservoir_id"] if dataset.rows else None,
        "dataset_id": dataset.id,
        "dataset_checksum": dataset.checksum,
        "readings": dataset.rows,
        "calibrations": calibrations,
        "params": params,
        "limits": {
            "gauge_max_m": params["gauge_max_m"],
            "pool_top_m": params["pool_top_m"],
            "max_daily_change_m": params["max_daily_change_m"],
        },
    }
