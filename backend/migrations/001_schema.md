# 001 初始表结构

应用首次启动以 SQLAlchemy 模型创建表与唯一约束。现有数据表只补建缺失表，升级到修改过字段的版本需要显式迁移。

- `users` 账号角色和加盐密码摘要。
- `datasets` 不可变记录批次，以名称和 SHA-256 去重。
- `runs` 记录参数、数据批次、运行租约、分析结果、导出文件及复核决定。
- `schedules` 保存定时计划和下一到期时间。
- `deliveries` 保存站内分发回执，每次运行最多一个回执。
- `audit_events` 记录登录、数据入库、运行、复核和报表分发动作。

# 002 满格/缺报/核尺与幂等重跑

新增表与字段（首次启动由 `Base.metadata.create_all` 补建缺失表；已存在的库需手动执行下列 DDL）：

- 新表 `calibrations`：人工核尺实测，`(reservoir_id, reading_date)` 唯一，晚到录入按日覆盖，记录录入人与备注。
- `runs` 新增 `fingerprint VARCHAR(64)`（带索引）：数据集读数 + 参数 + 限值 + 生效核尺的 SHA-256；同指纹重复提交复用既有运行，不新增报告。
- `runs` 新增 `input_snapshot JSON`：运行时刻冻结的读数、限值、核尺与参数，旧报告据此永久保留当时口径。
- 晨检台账行新增 `reading_kind`（`measured` 实测 / `full` 满格 / `missing` 缺报）；满格与缺报行的 `level_m` 允许为空，满格行存液位计量程顶读数。

```sql
CREATE TABLE IF NOT EXISTS calibrations (
    id SERIAL PRIMARY KEY,
    reservoir_id VARCHAR(40) NOT NULL,
    reading_date DATE NOT NULL,
    level_m DOUBLE PRECISION NOT NULL,
    author VARCHAR(64) NOT NULL,
    note VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT uq_calibration_pool_day UNIQUE (reservoir_id, reading_date)
);
CREATE INDEX IF NOT EXISTS ix_calibrations_reservoir ON calibrations (reservoir_id);
CREATE INDEX IF NOT EXISTS ix_calibrations_date ON calibrations (reading_date);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS fingerprint VARCHAR(64);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS input_snapshot JSON;
CREATE INDEX IF NOT EXISTS ix_runs_fingerprint ON runs (fingerprint);
```
