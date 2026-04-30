#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
止损管理器
==========
提供多种止损策略
"""

import numpy as np
import pandas as pd
from abc import ABC, abstractmethod
from typing import Dict
from dataclasses import dataclass
from enum import Enum


class StopLossStatus(Enum):
    """止损状态"""
    ACTIVE = "active"           # 活跃
    TRIGGERED = "triggered"     # 已触发
    UPDATED = "updated"         # 已更新


@dataclass
class StopLossResult:
    """止损结果"""
    status: StopLossStatus
    stop_price: float           # 止损价格
    current_price: float        # 当前价格
    should_exit: bool           # 是否应该退出
    reason: str                 # 原因
    pnl_pct: float             # 盈亏百分比


class BaseStopLoss(ABC):
    """止损基类"""
    
    def __init__(self, name: str = "base"):
        """初始化"""
        self.name = name
        self.entry_price = None
        self.highest_price = None
        self.lowest_price = None
        self.is_active = False
    
    def on_entry(self, entry_price: float):
        """入场时调用"""
        self.entry_price = entry_price
        self.highest_price = entry_price
        self.lowest_price = entry_price
        self.is_active = True
    
    def update(self, current_price: float) -> StopLossResult:
        """更新止损状态"""
        if not self.is_active:
            return StopLossResult(
                status=StopLossStatus.ACTIVE,
                stop_price=0,
                current_price=current_price,
                should_exit=False,
                reason="止损未激活",
                pnl_pct=0
            )
        
        # 更新最高/最低价
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price:
            self.lowest_price = current_price
        
        return self._check_stop(current_price)
    
    @abstractmethod
    def _check_stop(self, current_price: float) -> StopLossResult:
        """检查是否触发止损"""
        pass
    
    def reset(self):
        """重置止损"""
        self.entry_price = None
        self.highest_price = None
        self.lowest_price = None
        self.is_active = False


class FixedStopLoss(BaseStopLoss):
    """固定止损"""
    
    def __init__(self, stop_pct: float = 0.05, **kwargs):
        """
        Args:
            stop_pct: 止损百分比 (如0.05表示5%)
        """
        super().__init__(name="fixed")
        self.stop_pct = stop_pct
        self.stop_price = None
    
    def on_entry(self, entry_price: float):
        """入场时设置止损价"""
        super().on_entry(entry_price)
        self.stop_price = entry_price * (1 - self.stop_pct)
    
    def _check_stop(self, current_price: float) -> StopLossResult:
        """检查固定止损"""
        entry = self.entry_price if self.entry_price and self.entry_price > 0 else current_price
        pnl_pct = (current_price - entry) / entry
        
        if current_price <= self.stop_price:
            return StopLossResult(
                status=StopLossStatus.TRIGGERED,
                stop_price=self.stop_price,
                current_price=current_price,
                should_exit=True,
                reason=f"固定止损触发: 亏损 {self.stop_pct:.1%}",
                pnl_pct=pnl_pct
            )
        
        return StopLossResult(
            status=StopLossStatus.ACTIVE,
            stop_price=self.stop_price,
            current_price=current_price,
            should_exit=False,
            reason=f"止损价: {self.stop_price:.3f}",
            pnl_pct=pnl_pct
        )


class TrailingStopLoss(BaseStopLoss):
    """跟踪止损"""
    
    def __init__(self, initial_stop_pct: float = 0.05, trailing_pct: float = 0.03, **kwargs):
        """
        Args:
            initial_stop_pct: 初始止损百分比
            trailing_pct: 跟踪回撤百分比
        """
        super().__init__(name="trailing")
        self.initial_stop_pct = initial_stop_pct
        self.trailing_pct = trailing_pct
        self.stop_price = None
    
    def on_entry(self, entry_price: float):
        """入场时设置初始止损"""
        super().on_entry(entry_price)
        self.stop_price = entry_price * (1 - self.initial_stop_pct)
    
    def _check_stop(self, current_price: float) -> StopLossResult:
        """检查跟踪止损"""
        entry = self.entry_price if self.entry_price and self.entry_price > 0 else current_price
        pnl_pct = (current_price - entry) / entry
        
        # 更新止损价 (跟踪最高价)
        new_stop = self.highest_price * (1 - self.trailing_pct)
        if new_stop > self.stop_price:
            old_stop = self.stop_price
            self.stop_price = new_stop
            # 先检查是否已触发（止损上移后可能已触及）
            if current_price <= self.stop_price:
                return StopLossResult(
                    status=StopLossStatus.TRIGGERED,
                    stop_price=self.stop_price,
                    current_price=current_price,
                    should_exit=True,
                    reason=f"跟踪止损触发: 止损上移后触及 {old_stop:.3f}→{self.stop_price:.3f}",
                    pnl_pct=pnl_pct
                )
            return StopLossResult(
                status=StopLossStatus.UPDATED,
                stop_price=self.stop_price,
                current_price=current_price,
                should_exit=False,
                reason=f"止损上移: {old_stop:.3f} -> {self.stop_price:.3f}",
                pnl_pct=pnl_pct
            )
        
        # 检查是否触发
        if current_price <= self.stop_price:
            return StopLossResult(
                status=StopLossStatus.TRIGGERED,
                stop_price=self.stop_price,
                current_price=current_price,
                should_exit=True,
                reason=f"跟踪止损触发: 从高点回撤 {self.trailing_pct:.1%}",
                pnl_pct=pnl_pct
            )
        
        return StopLossResult(
            status=StopLossStatus.ACTIVE,
            stop_price=self.stop_price,
            current_price=current_price,
            should_exit=False,
            reason=f"跟踪止损: {self.stop_price:.3f} (高点: {self.highest_price:.3f})",
            pnl_pct=pnl_pct
        )


class ATRStopLoss(BaseStopLoss):
    """ATR波动率止损"""

    def __init__(self, atr_multiplier: float = 2.0, atr_period: int = 14, **kwargs):
        """
        Args:
            atr_multiplier: ATR倍数
            atr_period: ATR计算周期
        """
        super().__init__(name="atr")
        self.atr_multiplier = atr_multiplier
        self.atr_period = atr_period
        self.atr = None
        self.stop_price = None

    def on_entry(self, entry_price: float, atr: float = None,
                 price_history: Dict = None):
        """
        入场时设置止损

        Args:
            entry_price: 入场价格
            atr: ATR值
            price_history: 价格历史 (用于计算ATR)
        """
        super().on_entry(entry_price)

        if atr is not None:
            self.atr = atr
        elif price_history is not None:
            self.atr = self._calculate_atr(price_history)
        else:
            self.atr = entry_price * 0.02  # 默认2%

        self.stop_price = entry_price - self.atr * self.atr_multiplier

    def _check_stop(self, current_price: float) -> StopLossResult:
        """检查ATR止损"""
        entry = self.entry_price if self.entry_price and self.entry_price > 0 else current_price
        pnl_pct = (current_price - entry) / entry

        if current_price <= self.stop_price:
            return StopLossResult(
                status=StopLossStatus.TRIGGERED,
                stop_price=self.stop_price,
                current_price=current_price,
                should_exit=True,
                reason=f"ATR止损触发: 跌破 {self.atr_multiplier}×ATR",
                pnl_pct=pnl_pct
            )

        return StopLossResult(
            status=StopLossStatus.ACTIVE,
            stop_price=self.stop_price,
            current_price=current_price,
            should_exit=False,
            reason=f"ATR止损: {self.stop_price:.3f} ({self.atr_multiplier}×ATR={self.atr*self.atr_multiplier:.3f})",
            pnl_pct=pnl_pct
        )

    def _calculate_atr(self, price_history: Dict) -> float:
        """计算ATR"""
        highs = price_history.get('high', [])
        lows = price_history.get('low', [])
        closes = price_history.get('close', [])

        if len(highs) < self.atr_period or len(lows) < self.atr_period:
            return closes[-1] * 0.02 if closes else 0.2

        tr_list = []
        for i in range(-self.atr_period, 0):
            high_low = highs[i] - lows[i]
            high_close = abs(highs[i] - closes[i-1]) if abs(i) < len(closes) else 0
            low_close = abs(lows[i] - closes[i-1]) if abs(i) < len(closes) else 0
            tr_list.append(max(high_low, high_close, low_close))

        return np.mean(tr_list)


# 便捷函数
def create_stop_loss(stop_type: str, **kwargs) -> BaseStopLoss:
    """
    创建止损管理器

    Args:
        stop_type: 类型 ('fixed', 'trailing', 'atr')
        **kwargs: 额外参数

    Returns:
        BaseStopLoss
    """
    stops = {
        'fixed': FixedStopLoss,
        'trailing': TrailingStopLoss,
        'atr': ATRStopLoss,
    }
    stop_class = stops.get(stop_type, FixedStopLoss)
    return stop_class(**kwargs)
