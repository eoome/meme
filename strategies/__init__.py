#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略模块 — ML驱动 + 缠论 + 风险融合决策

信号生成流程:
  1. MLEngine.analyze() → ML信号(0~100%置信度)
  2. ChanLunFeatureExtractor → 缠论信号(分型/笔/中枢/背驰)
  3. _fuse_signals() → 融合决策(一致增强/相反降级)

子模块:
  signal:     信号类型定义 — BUY/SELL/NONE
  engine:     ML策略引擎 — LightGBM驱动
  data:       特征工程与数据标注
  ml:         机器学习模型与训练
  risk:       风险管理 — 止损/止盈/仓位/组合风控
  monitor:    策略监控 — 信号统计与健康检查
  backtest_engine_v2: 增强版ML回测引擎
  optimization: 参数优化 — 网格搜索
"""

from .signal import Signal, SignalType
from .engine import MLEngine
from .data.features import (
    calculate_features, prepare_training_sample,
    FEATURE_COLS, FEATURE_COLS_EXTENDED,
)
from .data.labeler import KlineLabeler, batch_label
from .data.chanlun import (
    ChanLunFeatureExtractor, get_chanlun_signal, get_multi_timeframe_signal
)
from .ml.model import MLModel, get_model
from .ml.trainer import train_model, quick_train
from .risk.stop_loss import (
    create_stop_loss, FixedStopLoss, TrailingStopLoss, ATRStopLoss
)
from .risk.market_regime import (
    MarketRegimeDetector, MarketRegime,
)
from .monitor import get_monitor, record_signal

__all__ = [
    # 信号系统
    "Signal", "SignalType",
    # ML 引擎
    "MLEngine",
    # 特征工程
    "calculate_features", "prepare_training_sample",
    "FEATURE_COLS", "FEATURE_COLS_EXTENDED",
    # 标注
    "KlineLabeler", "batch_label",
    # 缠论
    "ChanLunFeatureExtractor", "get_chanlun_signal", "get_multi_timeframe_signal",
    # ML 模型
    "MLModel", "get_model",
    "train_model", "quick_train",
    # 风险管理 — 止损
    "create_stop_loss", "FixedStopLoss", "TrailingStopLoss", "ATRStopLoss",
    # 市场状态
    "MarketRegimeDetector", "MarketRegime",
    # 信号监控
    "get_monitor", "record_signal",
]
