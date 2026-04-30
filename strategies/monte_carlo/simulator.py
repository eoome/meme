#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蒙特卡洛模拟器
==============
用于策略稳健性检验
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SimulationResult:
    """模拟结果"""
    # 收益统计
    mean_return: float = 0.0
    median_return: float = 0.0
    std_return: float = 0.0
    
    # 分位数
    percentile_5: float = 0.0
    percentile_25: float = 0.0
    percentile_75: float = 0.0
    percentile_95: float = 0.0
    
    # 极端情况
    worst_case: float = 0.0
    best_case: float = 0.0
    
    # 概率
    prob_positive: float = 0.0
    prob_profit_target: float = 0.0  # 达到盈利目标的概率
    prob_max_dd: float = 0.0  # 回撤超过阈值的概率
    
    # 回撤统计
    mean_max_dd: float = 0.0
    median_max_dd: float = 0.0
    worst_max_dd: float = 0.0
    
    # 夏普比率统计
    mean_sharpe: float = 0.0
    median_sharpe: float = 0.0
    
    # 原始数据
    all_returns: List[float] = field(default_factory=list)
    all_drawdowns: List[float] = field(default_factory=list)
    all_sharpes: List[float] = field(default_factory=list)


class MonteCarloSimulator:
    """
    蒙特卡洛模拟器
    
    功能:
    1. 随机打乱收益率顺序
    2. 自助法重采样
    3. 参数扰动模拟
    4. 生成统计报告
    """
    
    def __init__(self, n_simulations: int = 1000, random_seed: int = None):
        """
        Args:
            n_simulations: 模拟次数
            random_seed: 随机种子
        """
        self.n_simulations = n_simulations
        self.random_seed = random_seed
        
        self._rng = np.random.default_rng(random_seed)
    
    def simulate_returns(self, historical_returns: List[float], 
                        method: str = 'shuffle') -> SimulationResult:
        """
        模拟收益率
        
        Args:
            historical_returns: 历史收益率列表
            method: 模拟方法 ('shuffle', 'bootstrap', 'parametric')
        
        Returns:
            SimulationResult
        """
        historical_returns = np.array(historical_returns)
        n_periods = len(historical_returns)
        
        simulated_returns = []
        simulated_drawdowns = []
        simulated_sharpes = []
        
        for _ in range(self.n_simulations):
            if method == 'shuffle':
                # 随机打乱顺序
                shuffled = self._rng.permutation(historical_returns)
            elif method == 'bootstrap':
                # 自助法重采样
                shuffled = self._rng.choice(historical_returns, size=n_periods, replace=True)
            elif method == 'parametric':
                # 参数化模拟 (假设正态分布)
                mean = np.mean(historical_returns)
                std = np.std(historical_returns)
                shuffled = self._rng.normal(mean, std, n_periods)
            else:
                raise ValueError(f"未知方法: {method}")
            
            # 计算累计收益
            cumulative = np.cumprod(1 + shuffled) - 1
            total_return = cumulative[-1]
            
            # 计算最大回撤
            running_max = np.maximum.accumulate(cumulative)
            drawdown = cumulative - running_max
            max_dd = np.min(drawdown)
            
            # 计算夏普比率
            sharpe = np.mean(shuffled) / (np.std(shuffled) + 1e-8) * np.sqrt(252)
            
            simulated_returns.append(total_return)
            simulated_drawdowns.append(max_dd)
            simulated_sharpes.append(sharpe)
        
        return self._create_result(simulated_returns, simulated_drawdowns, simulated_sharpes)

    def _create_result(self, returns: List[float], drawdowns: List[float],
                      sharpes: List[float]) -> SimulationResult:
        """创建结果对象"""
        returns = np.array(returns)
        drawdowns = np.array(drawdowns)
        sharpes = np.array(sharpes)
        
        return SimulationResult(
            mean_return=np.mean(returns),
            median_return=np.median(returns),
            std_return=np.std(returns),
            percentile_5=np.percentile(returns, 5),
            percentile_25=np.percentile(returns, 25),
            percentile_75=np.percentile(returns, 75),
            percentile_95=np.percentile(returns, 95),
            worst_case=np.min(returns),
            best_case=np.max(returns),
            prob_positive=np.mean(returns > 0),
            mean_max_dd=np.mean(drawdowns),
            median_max_dd=np.median(drawdowns),
            worst_max_dd=np.min(drawdowns),
            mean_sharpe=np.mean(sharpes),
            median_sharpe=np.median(sharpes),
            all_returns=returns.tolist(),
            all_drawdowns=drawdowns.tolist(),
            all_sharpes=sharpes.tolist()
        )
    
    def print_report(self, result: SimulationResult, profit_target: float = 0.1,
                    max_dd_threshold: float = -0.2):
        """打印模拟报告"""
        print("\n" + "="*70)
        print("蒙特卡洛模拟报告")
        print("="*70)
        
        print(f"\n【收益统计】")
        print(f"  平均收益: {result.mean_return:+.2%}")
        print(f"  中位数收益: {result.median_return:+.2%}")
        print(f"  收益标准差: {result.std_return:.2%}")
        
        print(f"\n【分位数】")
        print(f"  5%分位数: {result.percentile_5:+.2%}")
        print(f"  25%分位数: {result.percentile_25:+.2%}")
        print(f"  75%分位数: {result.percentile_75:+.2%}")
        print(f"  95%分位数: {result.percentile_95:+.2%}")
        
        print(f"\n【极端情况】")
        print(f"  最坏情况: {result.worst_case:+.2%}")
        print(f"  最好情况: {result.best_case:+.2%}")
        
        print(f"\n【概率分析】")
        print(f"  盈利概率: {result.prob_positive:.1%}")
        print(f"  达到{profit_target:.0%}收益的概率: {np.mean(np.array(result.all_returns) > profit_target):.1%}")
        print(f"  回撤超过{abs(max_dd_threshold):.0%}的概率: {np.mean(np.array(result.all_drawdowns) < max_dd_threshold):.1%}")
        
        print(f"\n【回撤统计】")
        print(f"  平均最大回撤: {result.mean_max_dd:.2%}")
        print(f"  中位数最大回撤: {result.median_max_dd:.2%}")
        print(f"  最坏最大回撤: {result.worst_max_dd:.2%}")
        
        print(f"\n【夏普比率】")
        print(f"  平均夏普: {result.mean_sharpe:.2f}")
        print(f"  中位数夏普: {result.median_sharpe:.2f}")
        
        print("="*70)


# 便捷函数
def run_monte_carlo(returns: List[float], n_simulations: int = 1000,
                   method: str = 'shuffle') -> SimulationResult:
    """
    运行蒙特卡洛模拟
    
    Args:
        returns: 历史收益率
        n_simulations: 模拟次数
        method: 模拟方法
    
    Returns:
        SimulationResult
    """
    simulator = MonteCarloSimulator(n_simulations)
    return simulator.simulate_returns(returns, method)

