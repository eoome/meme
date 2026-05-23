#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据层 — 缓存管理、自动保存、自选池

使用懒加载避免循环导入：
  core.advisor → data → data_sources.router → core → 循环
"""

from .auto_save import get_auto_saver
from .cache_manager import get_cache_manager
from .watchlist import (
    load_watchlist, save_watchlist, add_to_watchlist,
    remove_from_watchlist, get_etf_pool
)

__all__ = [
    # 自动保存
    "get_auto_saver",
    # 缓存管理
    "get_cache_manager",
    # 自选池
    "load_watchlist", "save_watchlist",
    "add_to_watchlist", "remove_from_watchlist",
    "get_etf_pool",
]
