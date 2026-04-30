#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI 面板组件包 v2.0
==================

包含所有UI面板组件
"""

from .header import HeaderPanel
from .search import SearchInput
from .chart import ChartPanel
from .position import PositionPanel
from .signal import SignalPanel
from .strategy import StrategyPanel
from .log import LogPanel
from .backtest import BacktestPanel
from .settings import SettingsPanel
from .etf_pool import ETFPoolPanel

# 新增组件
from .strategy import (
    CircularProgress,
    MetricCard,
    PipelineStepper
)
from .backtest import (
    SummaryDashboard,
    StockSummaryRow,
    StockDetailPanel,
    TradesTable,
    CollapsiblePanel,
)
from .settings import (
    SettingItem,
    ThemeCard
)

__all__ = [
    # 主要面板
    "HeaderPanel",
    "SearchInput",
    "ChartPanel",
    "PositionPanel",
    "SignalPanel",
    "StrategyPanel",
    "LogPanel",
    "BacktestPanel",
    "SettingsPanel",
    "ETFPoolPanel",
    # 策略面板组件
    "CircularProgress",
    "MetricCard",
    "PipelineStepper",
    # 回测面板组件
    "SummaryDashboard",
    "StockSummaryRow",
    "StockDetailPanel",
    "TradesTable",
    "CollapsiblePanel",
    # 设置面板组件
    "SettingItem",
    "ThemeCard",
]
