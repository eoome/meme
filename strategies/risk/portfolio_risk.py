#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
组合级风控管理器
================
从单标的风控升级到组合层面:
- 总仓位上限控制
- 相关性约束（避免同方向高度相关持仓）
- 日亏损限额（达到后停止交易）
- 单标的仓位上限
- 行业/板块集中度限制
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import date, datetime
import threading


@dataclass
class PortfolioPosition:
    """单个持仓"""
    code: str
    name: str
    direction: str       # 'long' / 'short'
    shares: int
    avg_cost: float
    current_price: float
    entry_time: str = ""
    sector: str = ""     # 行业/板块


@dataclass
class PortfolioRiskStatus:
    """组合风控状态"""
    total_exposure: float = 0.0      # 总敞口 (持仓市值 / 总资金)
    net_exposure: float = 0.0        # 净敞口 (多头-空头)
    daily_pnl: float = 0.0          # 当日盈亏
    daily_pnl_pct: float = 0.0      # 当日盈亏百分比
    daily_trades: int = 0            # 当日交易次数
    max_correlation: float = 0.0     # 持仓间最大相关性
    sector_concentration: Dict = field(default_factory=dict)  # 各行业占比
    warnings: List[str] = field(default_factory=list)
    blocked: bool = False            # 是否被风控阻止交易
    block_reason: str = ""


class PortfolioRiskManager:
    """
    组合级风控管理器

    在交易前调用 check_can_trade() 检查是否允许，
    交易后调用 record_trade() 更新状态。
    """

    def __init__(
        self,
        max_total_exposure: float = 0.8,      # 总仓位上限 80%
        max_single_position: float = 0.25,     # 单标的上限 25%
        max_daily_loss_pct: float = 0.03,      # 日亏损限额 3%
        max_daily_trades: int = 20,            # 日交易次数上限
        max_sector_pct: float = 0.40,          # 单行业集中度上限 40%
        max_correlation: float = 0.8,          # 持仓相关性上限
        cooldown_after_loss: int = 30,         # 亏损后冷却期(分钟)
    ):
        self.max_total_exposure = max_total_exposure
        self.max_single_position = max_single_position
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_daily_trades = max_daily_trades
        self.max_sector_pct = max_sector_pct
        self.max_correlation = max_correlation
        self.cooldown_after_loss = cooldown_after_loss

        self._positions: Dict[str, PortfolioPosition] = {}
        self._daily_pnl = 0.0
        self._daily_trades = 0
        self._today = date.today().isoformat()
        self._last_loss_time = None
        self._lock = threading.Lock()

    def record_trade(
        self,
        pnl: float = 0,
        is_close: bool = False,
        code: str = "",
        direction: str = "",
        shares: int = 0,
        price: float = 0,
        name: str = "",
        sector: str = "",
    ):
        """
        记录交易结果并维护持仓状态

        Args:
            pnl: 本次交易盈亏金额
            is_close: 是否是平仓交易
            code: 股票代码（建仓/平仓时必传）
            direction: 'buy' / 'sell'
            shares: 交易股数
            price: 交易价格
            name: 股票名称
            sector: 行业/板块
        """
        with self._lock:
            self._check_day_reset()
            self._daily_trades += 1
            if is_close:
                self._daily_pnl += pnl
                if pnl < 0:
                    self._last_loss_time = datetime.now()

            # 维护持仓状态
            if code and shares > 0 and price > 0:
                if direction == 'buy' and not is_close:
                    # 建仓 / 加仓
                    if code in self._positions:
                        pos = self._positions[code]
                        old_value = pos.shares * pos.avg_cost
                        new_value = shares * price
                        pos.shares += shares
                        pos.avg_cost = (old_value + new_value) / pos.shares if pos.shares > 0 else price
                        pos.current_price = price
                    else:
                        self._positions[code] = PortfolioPosition(
                            code=code,
                            name=name or code,
                            direction='long',
                            shares=shares,
                            avg_cost=price,
                            current_price=price,
                            entry_time=datetime.now().isoformat(),
                            sector=sector,
                        )
                elif direction == 'sell' or is_close:
                    # 减仓 / 清仓
                    if code in self._positions:
                        pos = self._positions[code]
                        pos.shares -= shares
                        pos.current_price = price
                        if pos.shares <= 0:
                            del self._positions[code]
                    else:
                        import logging
                        logging.getLogger(__name__).warning(
                            f"卖出不存在的持仓: {code}, {shares}股 @ {price}"
                        )

    def check_can_trade(
        self,
        code: str,
        direction: str,
        shares: int,
        price: float,
        total_capital: float,
        sector: str = "",
        correlation_data: Optional[pd.DataFrame] = None,
    ) -> Tuple[bool, str]:
        """
        检查是否允许交易

        Returns:
            (允许, 原因)
        """
        with self._lock:
            self._check_day_reset()

            warnings = []

            # 1. 日交易次数限制
            if self._daily_trades >= self.max_daily_trades:
                return False, f"日交易次数已达上限 ({self._daily_trades}/{self.max_daily_trades})"

            # 2. 日亏损限额
            total_pnl_pct = self._daily_pnl / (total_capital + 1e-8)
            if total_pnl_pct <= -self.max_daily_loss_pct:
                return False, f"日亏损已达限额 ({total_pnl_pct:+.2%} ≤ -{self.max_daily_loss_pct:.2%})"

            # 3. 亏损冷却期
            if self._last_loss_time and direction == 'buy':
                elapsed = (datetime.now() - self._last_loss_time).total_seconds() / 60
                if elapsed < self.cooldown_after_loss:
                    return False, f"亏损冷却中 ({elapsed:.0f}/{self.cooldown_after_loss}分钟)"

            # 4. 计算新仓位后的总敞口
            new_position_value = shares * price
            existing_value = sum(
                p.shares * p.current_price for p in self._positions.values()
            )
            new_total_exposure = (existing_value + new_position_value) / (total_capital + 1e-8)
            if direction == 'buy' and new_total_exposure > self.max_total_exposure:
                return False, f"总仓位将超限 ({new_total_exposure:.1%} > {self.max_total_exposure:.1%})"

            # 5. 单标的仓位限制
            existing_in_code = self._positions.get(code)
            existing_code_value = (
                existing_in_code.shares * existing_in_code.current_price
                if existing_in_code else 0
            )
            new_code_value = existing_code_value + new_position_value
            code_pct = new_code_value / (total_capital + 1e-8)
            if direction == 'buy' and code_pct > self.max_single_position:
                return False, f"单标的仓位将超限 ({code_pct:.1%} > {self.max_single_position:.1%})"

            # 6. 行业集中度
            if sector and direction == 'buy':
                sector_value = sum(
                    p.shares * p.current_price
                    for p in self._positions.values()
                    if p.sector == sector
                ) + new_position_value
                sector_pct = sector_value / (total_capital + 1e-8)
                if sector_pct > self.max_sector_pct:
                    return False, f"行业集中度超限 ({sector} {sector_pct:.1%} > {self.max_sector_pct:.1%})"

            # 7. 相关性检查
            if correlation_data is not None and direction == 'buy' and len(self._positions) > 0:
                max_corr = self._check_max_correlation(code, correlation_data)
                if max_corr > self.max_correlation:
                    warnings.append(
                        f"与持仓 {max_corr:.2f} 高度相关 (>{self.max_correlation})"
                    )
                    # 不阻止，只警告

            return True, "; ".join(warnings) if warnings else "OK"

    def get_status(self, total_capital: float) -> PortfolioRiskStatus:
        """获取当前组合风控状态"""
        with self._lock:
            self._check_day_reset()

            positions = list(self._positions.values())
            total_value = sum(p.shares * p.current_price for p in positions)
            total_exposure = total_value / (total_capital + 1e-8)

            # 净敞口
            long_value = sum(
                p.shares * p.current_price
                for p in positions if p.direction == 'long'
            )
            short_value = sum(
                p.shares * p.current_price
                for p in positions if p.direction == 'short'
            )
            net_exposure = (long_value - short_value) / (total_capital + 1e-8)

            # 行业集中度
            sector_map: Dict[str, float] = {}
            for p in positions:
                s = p.sector or "未分类"
                sector_map[s] = sector_map.get(s, 0) + p.shares * p.current_price
            sector_pct = {
                s: v / (total_capital + 1e-8)
                for s, v in sector_map.items()
            }

            daily_pnl_pct = self._daily_pnl / (total_capital + 1e-8)

            warnings = []
            if total_exposure > self.max_total_exposure * 0.9:
                warnings.append(f"总仓位接近上限 ({total_exposure:.1%})")
            if daily_pnl_pct <= -self.max_daily_loss_pct * 0.8:
                warnings.append(f"日亏损接近限额 ({daily_pnl_pct:+.2%})")

            return PortfolioRiskStatus(
                total_exposure=total_exposure,
                net_exposure=net_exposure,
                daily_pnl=self._daily_pnl,
                daily_pnl_pct=daily_pnl_pct,
                daily_trades=self._daily_trades,
                sector_concentration=sector_pct,
                warnings=warnings,
                blocked=daily_pnl_pct <= -self.max_daily_loss_pct,
                block_reason="日亏损限额" if daily_pnl_pct <= -self.max_daily_loss_pct else "",
            )

    def _check_max_correlation(self, new_code: str, corr_data: pd.DataFrame) -> float:
        """检查新标的与现有持仓的最大相关性"""
        existing_codes = [c for c in self._positions.keys() if c != new_code]
        if not existing_codes or new_code not in corr_data.columns:
            return 0.0
        max_corr = 0.0
        for code in existing_codes:
            if code in corr_data.columns:
                try:
                    corr = abs(corr_data.loc[new_code, code])
                    max_corr = max(max_corr, corr)
                except Exception:
                    pass
        return max_corr

    def _check_day_reset(self):
        """日期切换时重置计数"""
        today = date.today().isoformat()
        if today != self._today:
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._last_loss_time = None
            self._today = today

    def reset(self):
        """清空所有状态"""
        with self._lock:
            self._positions.clear()
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._last_loss_time = None


# ── 全局单例 ──
_instance: Optional[PortfolioRiskManager] = None
_lock = threading.Lock()


def get_portfolio_risk_manager(**kwargs) -> PortfolioRiskManager:
    """获取全局组合风控管理器"""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = PortfolioRiskManager(**kwargs)
    return _instance
