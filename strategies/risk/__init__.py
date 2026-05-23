#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
风险管理 — 仓位控制、止损止盈、组合风控

子模块:
  stop_loss:       止损 — 固定/跟踪/ATR三种策略
  take_profit:     止盈 — 固定/跟踪两种策略
  position_sizer:  仓位管理 — 固定仓位
  market_regime:   市场状态 — 趋势/震荡/暴跌检测
  portfolio_risk:  组合风控 — 集中度/回撤/风险敞口
"""

from .stop_loss import (
    create_stop_loss, FixedStopLoss, TrailingStopLoss, ATRStopLoss,
    StopLossResult, StopLossStatus,
)
from .position_sizer import (
    create_position_sizer, PositionSize,
    FixedPositionSizer,
)
from .take_profit import (
    create_take_profit, FixedTakeProfit, TrailingTakeProfit,
    TakeProfitResult, TakeProfitStatus,
)
from .market_regime import (
    MarketRegimeDetector, MarketRegime, RegimeResult, RiskParams,
)
from .portfolio_risk import (
    PortfolioRiskManager, PortfolioPosition, PortfolioRiskStatus,
)

__all__ = [
    # 止损
    "create_stop_loss", "FixedStopLoss", "TrailingStopLoss", "ATRStopLoss",
    "StopLossResult", "StopLossStatus",
    # 仓位
    "create_position_sizer", "PositionSize",
    "FixedPositionSizer",
    # 止盈
    "create_take_profit", "FixedTakeProfit", "TrailingTakeProfit",
    "TakeProfitResult", "TakeProfitStatus",
    # 市场状态
    "MarketRegimeDetector", "MarketRegime", "RegimeResult", "RiskParams",
    # 组合风控
    "PortfolioRiskManager", "PortfolioPosition", "PortfolioRiskStatus",
]
