#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI 包 v2.0
==========

包含主题系统、动画效果、面板组件
"""

from .theme import (
    ThemeManager,
    get_current_theme,
    get_current_colors,
    switch_theme,
    init_theme,
    THEME_META,
    get_all_themes
)

from .design_tokens import (
    get_colors,
)

from .animations import (
    NumberRollAnimation,
)

from .panels import (
    HeaderPanel,
    SearchInput,
    ChartPanel,
    PositionPanel,
    SignalPanel,
    StrategyPanel,
    LogPanel,
    BacktestPanel,
    SettingsPanel,
    # 子组件
    CircularProgress,
    MetricCard,
    PipelineStepper,
    SummaryDashboard,
    StockSummaryRow,
    StockDetailPanel,
    CollapsiblePanel,
    TradesTable,
    SettingItem,
    ThemeCard
)

from .main_window import MainWindow

__all__ = [
    # 主题系统
    "ThemeManager",
    "get_current_theme",
    "get_current_colors",
    "switch_theme",
    "init_theme",
    "THEME_META",
    # 设计令牌
    "get_colors",
    # 动画
    "NumberRollAnimation",
    # 面板
    "HeaderPanel",
    "SearchInput",
    "ChartPanel",
    "PositionPanel",
    "SignalPanel",
    "StrategyPanel",
    "LogPanel",
    "BacktestPanel",
    "SettingsPanel",
    # 子组件
    "CircularProgress",
    "MetricCard",
    "PipelineStepper",
    "SummaryDashboard",
    "StockSummaryRow",
    "StockDetailPanel",
    "CollapsiblePanel",
    "TradesTable",
    "SettingItem",
    "ThemeCard",
    # 主窗口
    "MainWindow",
]
