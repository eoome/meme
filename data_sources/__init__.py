# -*- coding: utf-8 -*-
"""
多源数据层 - 统一数据接口
按数据类型路由不同数据源，避免单一接口限频
"""
from .router import DataRouter

__all__ = ["DataRouter"]
