#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核心模块 — 配置、日志、信号顾问

使用懒加载避免循环导入：
  data_sources.router → core.__init__ → core.advisor → data → data_sources.router
"""

from .config import get_config, AppConfig, ChanLunConfig
from .logger import Logger, log

# 懒加载：advisor 依赖 data.cache_manager，避免循环导入
def __getattr__(name):
    """懒加载属性 — 避免循环导入"""
    if name in ("Advisor", "get_advisor", "start_advisor", "stop_advisor"):
        from .advisor import Advisor, get_advisor, start_advisor, stop_advisor
        return locals()[name]
    raise AttributeError(f"module 'core' has no attribute {name!r}")

__all__ = [
    "get_config", "AppConfig",
    "Logger", "log",
    "Advisor", "get_advisor", "start_advisor", "stop_advisor",
]
