#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓位管理器
==========
提供多种仓位管理策略
"""

import numpy as np
import pandas as pd
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PositionSize:
    """仓位大小"""
    target_shares: int  # 目标股数
    target_value: float  # 目标市值
    risk_amount: float  # 风险金额
    confidence: float  # 置信度 (0-1)


class BasePositionSizer(ABC):
    """仓位管理基类"""

    def __init__(self, max_position_pct: float = 0.2, min_position_pct: float = 0.01):
        """
        Args:
            max_position_pct: 最大仓位比例 (相对于总资金)
            min_position_pct: 最小仓位比例
        """
        self.max_position_pct = max_position_pct
        self.min_position_pct = min_position_pct
        # 仓位-止损联动: 最大单笔风险金额占总资金比例
        self.max_risk_per_trade: float = 0.02  # 默认2%

    @abstractmethod
    def calculate(self, **kwargs) -> PositionSize:
        """计算仓位大小"""
        pass

    def calculate_with_risk(
        self,
        price: float,
        total_capital: float,
        stop_loss_pct: float = None,
        stop_price: float = None,
        **kwargs
    ) -> PositionSize:
        """
        风险反推仓位（仓位-止损联动）

        逻辑: 最大亏损金额 = total_capital * max_risk_per_trade
              仓位 = 最大亏损金额 / (price - stop_price)

        Args:
            price: 当前价格
            total_capital: 总资金
            stop_loss_pct: 止损百分比 (如 0.03 = 3%)
            stop_price: 止损价格（与 stop_loss_pct 二选一）
        """
        if stop_price is not None:
            risk_per_share = abs(price - stop_price)
        elif stop_loss_pct is not None:
            risk_per_share = price * stop_loss_pct
        else:
            # 无止损信息，走默认逻辑
            return self.calculate(price=price, total_capital=total_capital, **kwargs)

        max_risk_amount = total_capital * self.max_risk_per_trade
        if risk_per_share <= 0:
            return self.calculate(price=price, total_capital=total_capital, **kwargs)

        shares = int(max_risk_amount / risk_per_share)
        shares = self._apply_limits(shares, price, total_capital)

        return PositionSize(
            target_shares=shares,
            target_value=shares * price,
            risk_amount=shares * risk_per_share,
            confidence=kwargs.get('confidence', 1.0),
        )

    def _apply_limits(self, shares: int, price: float, total_capital: float) -> int:
        """应用仓位限制"""
        max_shares = int(total_capital * self.max_position_pct / price)
        min_shares = int(total_capital * self.min_position_pct / price)

        shares = min(shares, max_shares)
        shares = max(shares, min_shares)

        # 确保是100的整数倍 (A股)
        shares = (shares // 100) * 100

        return shares


class FixedPositionSizer(BasePositionSizer):
    """固定仓位管理"""
    
    def __init__(self, position_pct: float = 0.1, **kwargs):
        """
        Args:
            position_pct: 固定仓位比例
        """
        super().__init__(**kwargs)
        self.position_pct = position_pct
    
    def calculate(self, price: float, total_capital: float, **kwargs) -> PositionSize:
        """
        计算固定仓位
        
        Args:
            price: 当前价格
            total_capital: 总资金
        
        Returns:
            PositionSize
        """
        target_value = total_capital * self.position_pct
        shares = int(target_value / price)
        shares = self._apply_limits(shares, price, total_capital)
        
        return PositionSize(
            target_shares=shares,
            target_value=shares * price,
            risk_amount=0,  # 固定仓位不计算风险
            confidence=1.0
        )


# 便捷函数
def create_position_sizer(sizer_type: str = 'fixed', **kwargs) -> BasePositionSizer:
    """
    创建仓位管理器

    Args:
        sizer_type: 类型 ('fixed')
        **kwargs: 额外参数

    Returns:
        BasePositionSizer
    """
    sizers = {
        'fixed': FixedPositionSizer,
    }
    sizer_class = sizers.get(sizer_type, FixedPositionSizer)
    return sizer_class(**kwargs)
