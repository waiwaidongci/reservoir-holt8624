import csv
import io
import math
from datetime import date
from src.domain import analysis
from src.domain.bands import READING_KINDS
from src.domain.spec import SPEC


def cell(field, raw):
    label, kind = field['label'], field['type']
    value = raw.strip() if isinstance(raw, str) else raw
    if value is None or value == '':
        if field.get('required', True):
            raise ValueError(f'{label}不能为空')
        return None
    try:
        if kind == 'int':
            if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                raise ValueError
            value = int(value)
        elif kind == 'number':
            if isinstance(value, bool):
                raise ValueError
            value = float(value)
            if not math.isfinite(value):
                raise ValueError
        elif kind == 'date':
            value = date.fromisoformat(str(value)).isoformat()
        elif kind == 'bool':
            values = {'true': True, 'false': False, '1': True, '0': False, '是': True, '否': False}
            value = values[str(value).lower()]
        elif kind in {'select', 'string'}:
            if not isinstance(value, str) or len(value) > field.get('max_length', 300):
                raise ValueError
            if kind == 'select' and value not in field['options']:
                raise ValueError
    except (TypeError, ValueError, KeyError, OverflowError):
        raise ValueError(f'{label}格式不正确，请检查字段说明') from None
    if kind in {'int', 'number'}:
        if 'min' in field and value < field['min']:
            raise ValueError(f'{label}最小为 {field["min"]}')
        if 'max' in field and value > field['max']:
            raise ValueError(f'{label}最大为 {field["max"]}')
    return value


def _field(key):
    return next(item for item in SPEC['fields'] if item['key'] == key)


def _reading_row(raw, line_num):
    """把一行 CSV 规范化成晨检记录，区分实测、满格和缺报。"""
    kind_cell = raw.get('reading_kind')
    kind = kind_cell.strip() if isinstance(kind_cell, str) else kind_cell
    if kind not in READING_KINDS:
        raise ValueError('读数状态必须为 measured（实测）、full（满格）或 missing（缺报）')
    level_raw = raw.get('level_m')
    level_empty = level_raw is None or (isinstance(level_raw, str) and not level_raw.strip())
    if kind == 'missing':
        if not level_empty:
            raise ValueError('缺报行必须留空实测液位')
        level = None
    elif level_empty:
        raise ValueError('实测或满格行必须填写液位；满格行填液位计量程顶读数')
    else:
        level = cell(_field('level_m'), level_raw)
    return {
        'reservoir_id': cell(_field('reservoir_id'), raw.get('reservoir_id')),
        'reading_date': cell(_field('reading_date'), raw.get('reading_date')),
        'level_m': level,
        'reading_kind': kind,
    }


def parse_rows(text):
    reader = csv.DictReader(io.StringIO(text.removeprefix('﻿'), newline=''), strict=True)
    fields = SPEC['fields']
    keys = {field['key'] for field in fields}
    required = {field['key'] for field in fields if field.get('required', True)}
    rows = []
    try:
        headers = reader.fieldnames or []
        if not required <= set(headers) or not set(headers) <= keys or len(headers) != len(set(headers)):
            raise ValueError('CSV 表头与字段说明不符，请下载模板检查列名')
        for raw in reader:
            if len(rows) >= 5000:
                raise ValueError('一个数据批次最多导入 5000 行')
            try:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError('工作表的列数与表头不一致')
                rows.append(_reading_row(raw, reader.line_num))
            except ValueError as error:
                raise ValueError(f'CSV 第 {reader.line_num} 行 · {error}') from None
    except csv.Error:
        raise ValueError(f'CSV 第 {reader.line_num} 行附近格式不正确') from None
    if not rows:
        raise ValueError('CSV 至少需要一行有效数据')
    if hasattr(analysis, 'validate_rows'):
        analysis.validate_rows(rows)
    return rows


def normalize_params(params):
    fields = SPEC['params']
    if set(params) - {field['key'] for field in fields}:
        raise ValueError('存在未知运行参数，请重新打开工作面板')
    normalized = {field['key']: cell(field, params.get(field['key'], field['default'])) for field in fields}
    if normalized['gauge_max_m'] > normalized['pool_top_m']:
        raise ValueError('液位计量程顶不能高于池顶液位')
    return normalized
