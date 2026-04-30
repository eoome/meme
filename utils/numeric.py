#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用数值清洗工具
================

统一处理 None / NaN / Inf / 字符串脏值 → 合法 float。
项目中所有需要数值清洗的地方统一调用此模块，避免重复定义。
"""

import numpy as np


def clean_num(val, default: float = 0.0) -> float:
    """
    安全地将任意值转为 float，异常时返回 default。

    处理的脏值:
      - None, NaN, Inf, -Inf
      - 空字符串, '-', 'N', 'NaN', 'null', 'None', 'nan'
      - 无法转为 float 的字符串

    Args:
        val: 任意类型的输入值
        default: 无法转换时的默认值

    Returns:
        合法的 float 值
    """
    if val is None:
        return default
    if isinstance(val, str):
        val = val.strip()
        if val in ('', '-', 'N', 'NaN', 'null', 'None', 'nan'):
            return default
        try:
            return float(val)
        except ValueError:
            return default
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def clean_kline_record(record: dict, volume_default: float = 0.0) -> dict:
    """
    清洗一条 K 线记录中的数值字段。

    Args:
        record: 包含 open/high/low/close/volume 等字段的 dict
        volume_default: volume 字段的默认值

    Returns:
        清洗后的 dict（副本）
    """
    d = dict(record)
    for col in ['open', 'high', 'low', 'close', 'price', 'change']:
        if col in d:
            d[col] = clean_num(d[col], 0.0)
    for col in ['volume', 'amount']:
        if col in d:
            d[col] = clean_num(d[col], volume_default)
    return d


def clean_kline_list(records: list) -> list:
    """
    批量清洗 K 线记录列表。

    Args:
        records: list[dict]，每条包含 K 线字段

    Returns:
        清洗后的 list[dict]（新列表）
    """
    if not records:
        return []
    return [clean_kline_record(r) for r in records if isinstance(r, dict)]


def clean_minute_record(record: dict) -> dict:
    """
    清洗一条分时记录中的数值字段。

    Args:
        record: 包含 price/volume 等字段的 dict

    Returns:
        清洗后的 dict（副本）
    """
    d = dict(record)
    for col in ['price', 'open', 'high', 'low', 'close', 'avg_price']:
        if col in d:
            d[col] = clean_num(d[col], 0.0)
    for col in ['volume', 'amount']:
        if col in d:
            d[col] = clean_num(d[col], 0.0)
    return d


def clean_minute_list(records: list) -> list:
    """
    批量清洗分时记录列表。

    Args:
        records: list[dict]，每条包含分时字段

    Returns:
        清洗后的 list[dict]（新列表）
    """
    if not records:
        return []
    return [clean_minute_record(r) for r in records if isinstance(r, dict)]
