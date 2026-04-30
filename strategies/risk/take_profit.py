#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
止盈管理器
==========
提供多种止盈策略，与止损配合形成完整的退出机制

策略:
- FixedTakeProfit: 固定目标止盈
- TrailingTakeProfit: 跟踪止盈（回撤触发）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class TakeProfitStatus(Enum):
    """止盈状态枚举 — NO_PROFIT(0)/PROFIT_ZONE(1)/TARGET_HIT(2)"""
    ACTIVE = "active"
    TRIGGERED = "triggered"
    PARTIAL = "partial"  # 部分止盈


@dataclass
class TakeProfitResult:
    """止盈结果"""
    status: TakeProfitStatus
    should_exit: bool        # 是否完全退出
    should_reduce: bool      # 是否减仓
    reduce_pct: float        # 减仓比例 (0~1)
    reason: str
    target_price: float
    current_price: float
    pnl_pct: float


class BaseTakeProfit(ABC):
    """止盈基类"""

    def __init__(self, name: str = "base"):
        """初始化"""
        self.name = name
        self.entry_price = None
        self.highest_price = None
        self.is_active = False

    def on_entry(self, entry_price: float, **kwargs):
        """入场时调用"""
        self.entry_price = entry_price
        self.highest_price = entry_price
        self.is_active = True

    def update(self, current_price: float) -> TakeProfitResult:
        """更新止盈状态"""
        if not self.is_active:
            return TakeProfitResult(
                status=TakeProfitStatus.ACTIVE, should_exit=False,
                should_reduce=False, reduce_pct=0,
                reason="止盈未激活", target_price=0,
                current_price=current_price, pnl_pct=0,
            )
        if current_price > self.highest_price:
            self.highest_price = current_price
        return self._check(current_price)

    @abstractmethod
    def _check(self, current_price: float) -> TakeProfitResult:
        pass

    def reset(self):
        """重置止损状态"""
        self.entry_price = None
        self.highest_price = None
        self.is_active = False


class FixedTakeProfit(BaseTakeProfit):
    """固定目标止盈"""

    def __init__(self, target_pct: float = 0.05, **kwargs):
        """
        Args:
            target_pct: 目标盈利百分比 (如 0.05 = 5%)
        """
        super().__init__(name="fixed")
        self.target_pct = target_pct
        self.target_price = None

    def on_entry(self, entry_price: float, **kwargs):
        """入场时调用 — 记录入场价"""
        super().on_entry(entry_price)
        self.target_price = entry_price * (1 + self.target_pct)

    def _check(self, current_price: float) -> TakeProfitResult:
        pnl_pct = (current_price - self.entry_price) / self.entry_price
        if current_price >= self.target_price:
            return TakeProfitResult(
                status=TakeProfitStatus.TRIGGERED, should_exit=True,
                should_reduce=False, reduce_pct=0,
                reason=f"固定止盈触发: +{self.target_pct:.1%}",
                target_price=self.target_price,
                current_price=current_price, pnl_pct=pnl_pct,
            )
        return TakeProfitResult(
            status=TakeProfitStatus.ACTIVE, should_exit=False,
            should_reduce=False, reduce_pct=0,
            reason=f"目标: {self.target_price:.3f} ({pnl_pct:+.1%}/{self.target_pct:.1%})",
            target_price=self.target_price,
            current_price=current_price, pnl_pct=pnl_pct,
        )


class TrailingTakeProfit(BaseTakeProfit):
    """
    跟踪止盈（回撤触发）
    盈利达到 activate_pct 后激活，从最高点回撤 trail_pct 时止盈
    """

    def __init__(self, activate_pct: float = 0.03, trail_pct: float = 0.015, **kwargs):
        """
        Args:
            activate_pct: 激活跟踪止盈的最低盈利 (如 0.03 = 3%)
            trail_pct: 从最高点回撤触发 (如 0.015 = 1.5%)
        """
        super().__init__(name="trailing")
        self.activate_pct = activate_pct
        self.trail_pct = trail_pct
        self._activated = False
        self._trail_stop = None

    def on_entry(self, entry_price: float, **kwargs):
        """入场时调用 — 记录入场价"""
        super().on_entry(entry_price)
        self._activated = False
        self._trail_stop = None

    def _check(self, current_price: float) -> TakeProfitResult:
        pnl_pct = (current_price - self.entry_price) / self.entry_price
        peak_pnl = (self.highest_price - self.entry_price) / self.entry_price

        # 激活条件: 最高盈利达到 activate_pct
        if not self._activated and peak_pnl >= self.activate_pct:
            self._activated = True
            self._trail_stop = self.highest_price * (1 - self.trail_pct)

        if not self._activated:
            return TakeProfitResult(
                status=TakeProfitStatus.ACTIVE, should_exit=False,
                should_reduce=False, reduce_pct=0,
                reason=f"跟踪止盈待激活 (最高{peak_pnl:+.1%}, 需{self.activate_pct:+.1%})",
                target_price=0, current_price=current_price, pnl_pct=pnl_pct,
            )

        # 更新跟踪止损价（只上移不下移）
        new_stop = self.highest_price * (1 - self.trail_pct)
        if new_stop > self._trail_stop:
            self._trail_stop = new_stop

        # 触发条件: 价格跌破跟踪止损
        if current_price <= self._trail_stop:
            return TakeProfitResult(
                status=TakeProfitStatus.TRIGGERED, should_exit=True,
                should_reduce=False, reduce_pct=0,
                reason=f"跟踪止盈触发: 从高点{self.highest_price:.3f}回撤{self.trail_pct:.1%}",
                target_price=self._trail_stop,
                current_price=current_price, pnl_pct=pnl_pct,
            )

        return TakeProfitResult(
            status=TakeProfitStatus.ACTIVE, should_exit=False,
            should_reduce=False, reduce_pct=0,
            reason=f"跟踪止盈: 止盈线{self._trail_stop:.3f} (高点{self.highest_price:.3f})",
            target_price=self._trail_stop,
            current_price=current_price, pnl_pct=pnl_pct,
        )


def create_take_profit(tp_type: str = 'trailing', **kwargs) -> BaseTakeProfit:
    """
    创建止盈管理器

    Args:
        tp_type: 'fixed' / 'trailing'
    """
    tps = {
        'fixed': FixedTakeProfit,
        'trailing': TrailingTakeProfit,
    }
    return tps.get(tp_type, FixedTakeProfit)(**kwargs)
