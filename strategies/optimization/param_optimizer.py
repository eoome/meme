#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
参数优化器
==========
提供多种参数优化方法
"""

import numpy as np
import pandas as pd
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Callable, Any
from dataclasses import dataclass
from datetime import datetime
import itertools
import warnings

# 尝试导入optuna (贝叶斯优化)
try:
    import optuna
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False


@dataclass
class OptimizationResult:
    """优化结果"""
    best_params: Dict
    best_score: float
    all_results: List[Dict]
    optimization_time: float
    method: str
    metric: str


class BaseOptimizer(ABC):
    """参数优化基类"""
    
    def __init__(self, engine, metric: str = 'sharpe_ratio'):
        """
        Args:
            engine: 回测引擎
            metric: 优化目标指标
        """
        self.engine = engine
        self.metric = metric
        self.results = []
        self._precomputed_df = None  # 预计算特征的 DataFrame（优化模式复用）
    
    def set_precomputed_df(self, df):
        """设置预计算特征的 DataFrame，避免每次评估都重算特征"""
        self._precomputed_df = df
    
    @abstractmethod
    def optimize(self, param_grid: Dict, **kwargs) -> OptimizationResult:
        """执行优化"""
        pass
    
    def _evaluate_params(self, params: Dict, data: List[dict], 
                        code: str, name: str) -> float:
        """评估一组参数 — 带完整错误处理和参数映射"""
        try:
            # 将优化参数名映射到 BacktestConfig 字段名
            config_kwargs = {}
            
            # min_signal_confidence → BacktestConfig 已有该字段
            if 'min_signal_confidence' in params:
                config_kwargs['min_signal_confidence'] = params['min_signal_confidence']
            
            # initial_stop_pct → 映射到 stop_loss_params.initial_stop_pct
            if 'initial_stop_pct' in params:
                config_kwargs['stop_loss_params'] = {
                    'initial_stop_pct': params['initial_stop_pct'],
                    'trailing_pct': params.get('trailing_pct', 0.03),
                }
            
            # tp_activate_pct → 映射到止盈激活百分比
            if 'tp_activate_pct' in params:
                config_kwargs['tp_activate_pct'] = params['tp_activate_pct']
            
            # tp_trail_pct → 映射到止盈跟踪百分比
            if 'tp_trail_pct' in params:
                config_kwargs['tp_trail_pct'] = params['tp_trail_pct']
            
            # exit_cooldown_bars → 映射到冷却期
            if 'exit_cooldown_bars' in params:
                config_kwargs['exit_cooldown_bars'] = params['exit_cooldown_bars']

            # 更新引擎配置
            from strategies.backtest_engine_v2 import BacktestConfig
            
            config = BacktestConfig(**config_kwargs)
            self.engine.config = config
            
            # 运行回测（如果有预计算特征则复用）
            result = self.engine.run(code, name, data,
                                     precomputed_df=self._precomputed_df)
            
            # 获取指标
            score = getattr(result, self.metric, 0)
            
            # 记录结果
            self.results.append({
                'params': params.copy(),
                'score': score,
                'total_return': getattr(result, 'total_return', 0),
                'sharpe_ratio': getattr(result, 'sharpe_ratio', 0),
                'max_drawdown': getattr(result, 'max_drawdown', 0),
            })
            
            return float(score) if not np.isnan(score) else 0.0
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"参数评估失败: {e}")
            return -np.inf


class GridSearchOptimizer(BaseOptimizer):
    """网格搜索优化器"""
    
    def optimize(self, param_grid: Dict, data: List[dict], 
                code: str, name: str, **kwargs) -> OptimizationResult:
        """
        网格搜索优化
        
        Args:
            param_grid: 参数网格, 如 {'confidence_threshold': [0.6, 0.7, 0.8]}
            data: 回测数据
            code: 股票代码
            name: 股票名称
            progress_callback: 可选回调 fn(current, total, msg) 报告进度
        
        Returns:
            OptimizationResult
        """
        import time
        start_time = time.time()
        progress_callback = kwargs.get('progress_callback')
        
        # 生成所有参数组合
        keys = list(param_grid.keys())
        values = list(param_grid.values())
        combinations = list(itertools.product(*values))
        
        print(f"[GridSearch] 开始网格搜索: {len(combinations)} 组参数")
        
        best_score = -np.inf
        best_params = None
        
        for i, combo in enumerate(combinations):
            params = dict(zip(keys, combo))
            if progress_callback:
                progress_callback(i, len(combinations), f"评估参数 {i+1}/{len(combinations)}: {params}")
            score = self._evaluate_params(params, data, code, name)
            
            if score > best_score:
                best_score = score
                best_params = params
            
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{len(combinations)}, 当前最佳: {best_score:.4f}")
        
        elapsed = time.time() - start_time
        
        print(f"[GridSearch] 优化完成, 耗时: {elapsed:.1f}s")
        print(f"  最佳参数: {best_params}")
        print(f"  最佳得分: {best_score:.4f}")
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=self.results,
            optimization_time=elapsed,
            method='grid_search',
            metric=self.metric
        )


class RandomSearchOptimizer(BaseOptimizer):
    """随机搜索优化器"""
    
    def __init__(self, engine, metric: str = 'sharpe_ratio', n_iter: int = 50):
        """初始化"""
        super().__init__(engine, metric)
        self.n_iter = n_iter
    
    def optimize(self, param_distributions: Dict, data: List[dict],
                code: str, name: str, **kwargs) -> OptimizationResult:
        """
        随机搜索优化
        
        Args:
            param_distributions: 参数分布, 如 {'confidence_threshold': (0.5, 0.9)}
            data: 回测数据
            code: 股票代码
            name: 股票名称
        
        Returns:
            OptimizationResult
        """
        import time
        start_time = time.time()
        
        print(f"[RandomSearch] 开始随机搜索: {self.n_iter} 次迭代")
        
        best_score = -np.inf
        best_params = None
        
        for i in range(self.n_iter):
            # 随机采样参数
            params = self._sample_params(param_distributions)
            score = self._evaluate_params(params, data, code, name)
            
            if score > best_score:
                best_score = score
                best_params = params
            
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{self.n_iter}, 当前最佳: {best_score:.4f}")
        
        elapsed = time.time() - start_time
        
        print(f"[RandomSearch] 优化完成, 耗时: {elapsed:.1f}s")
        print(f"  最佳参数: {best_params}")
        print(f"  最佳得分: {best_score:.4f}")
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=self.results,
            optimization_time=elapsed,
            method='random_search',
            metric=self.metric
        )
    
    def _sample_params(self, distributions: Dict) -> Dict:
        """从分布中采样参数"""
        params = {}
        for key, dist in distributions.items():
            if isinstance(dist, tuple) and len(dist) == 2:
                # 均匀分布
                params[key] = np.random.uniform(dist[0], dist[1])
            elif isinstance(dist, list):
                # 离散选择
                params[key] = np.random.choice(dist)
            else:
                params[key] = dist
        return params


class BayesianOptimizer(BaseOptimizer):
    """贝叶斯优化器 (需要optuna)"""
    
    def __init__(self, engine, metric: str = 'sharpe_ratio', n_trials: int = 100):
        """初始化"""
        super().__init__(engine, metric)
        self.n_trials = n_trials
    
    def optimize(self, param_space: Dict, data: List[dict],
                code: str, name: str, **kwargs) -> OptimizationResult:
        """
        贝叶斯优化
        
        Args:
            param_space: 参数空间定义
            data: 回测数据
            code: 股票代码
            name: 股票名称
        
        Returns:
            OptimizationResult
        """
        if not OPTUNA_AVAILABLE:
            print("[BayesianOptimizer] optuna未安装, 使用随机搜索")
            random_opt = RandomSearchOptimizer(self.engine, self.metric, self.n_trials)
            return random_opt.optimize(param_space, data, code, name)
        
        import time
        start_time = time.time()
        
        print(f"[BayesianOptimizer] 开始贝叶斯优化: {self.n_trials} 次试验")
        
        # 存储数据供objective使用
        self._opt_data = data
        self._opt_code = code
        self._opt_name = name
        self._param_space = param_space
        
        # 创建study
        study = optuna.create_study(direction='maximize')
        study.optimize(self._objective, n_trials=self.n_trials, show_progress_bar=True)
        
        elapsed = time.time() - start_time
        
        best_params = study.best_params
        best_score = study.best_value
        
        print(f"[BayesianOptimizer] 优化完成, 耗时: {elapsed:.1f}s")
        print(f"  最佳参数: {best_params}")
        print(f"  最佳得分: {best_score:.4f}")
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=self.results,
            optimization_time=elapsed,
            method='bayesian',
            metric=self.metric
        )
    
    def _objective(self, trial):
        """optuna目标函数"""
        params = {}
        for key, space in self._param_space.items():
            if space['type'] == 'float':
                params[key] = trial.suggest_float(key, space['low'], space['high'])
            elif space['type'] == 'int':
                params[key] = trial.suggest_int(key, space['low'], space['high'])
            elif space['type'] == 'categorical':
                params[key] = trial.suggest_categorical(key, space['choices'])
        
        return self._evaluate_params(params, self._opt_data, self._opt_code, self._opt_name)


class WalkForwardOptimizer(BaseOptimizer):
    """滚动前向优化器"""
    
    def __init__(self, engine, metric: str = 'sharpe_ratio',
                 train_size: int = 60, test_size: int = 20):
        super().__init__(engine, metric)
        self.train_size = train_size
        self.test_size = test_size
    
    def optimize(self, param_grid: Dict, data: List[dict],
                code: str, name: str, **kwargs) -> OptimizationResult:
        """
        滚动前向优化
        
        将数据分为多个窗口, 在每个窗口内训练优化, 在下一个窗口测试
        
        Args:
            param_grid: 参数网格
            data: 回测数据
            code: 股票代码
            name: 股票名称
        
        Returns:
            OptimizationResult
        """
        import time
        start_time = time.time()
        
        print(f"[WalkForward] 开始滚动前向优化")
        print(f"  训练窗口: {self.train_size}, 测试窗口: {self.test_size}")
        
        n_windows = (len(data) - self.train_size) // self.test_size
        print(f"  窗口数量: {n_windows}")
        
        all_scores = []
        
        for i in range(n_windows):
            train_start = i * self.test_size
            train_end = train_start + self.train_size
            test_start = train_end
            test_end = min(test_start + self.test_size, len(data))
            
            train_data = data[train_start:train_end]
            test_data = data[test_start:test_end]
            
            print(f"\n  窗口 {i+1}/{n_windows}")
            print(f"    训练: {train_start}-{train_end}, 测试: {test_start}-{test_end}")
            
            # 在训练集上优化
            grid_opt = GridSearchOptimizer(self.engine, self.metric)
            train_result = grid_opt.optimize(param_grid, train_data, code, name)
            
            # 在测试集上验证
            test_score = self._evaluate_params(
                train_result.best_params, test_data, code, name
            )
            
            all_scores.append({
                'window': i + 1,
                'train_score': train_result.best_score,
                'test_score': test_score,
                'params': train_result.best_params
            })
            
            print(f"    训练得分: {train_result.best_score:.4f}, 测试得分: {test_score:.4f}")
        
        # 选择平均表现最好的参数
        param_scores = {}
        for score_info in all_scores:
            params_key = str(score_info['params'])
            if params_key not in param_scores:
                param_scores[params_key] = {'params': score_info['params'], 'scores': []}
            param_scores[params_key]['scores'].append(score_info['test_score'])
        
        best_avg_score = -np.inf
        best_params = None
        
        for params_key, info in param_scores.items():
            avg_score = np.mean(info['scores'])
            if avg_score > best_avg_score:
                best_avg_score = avg_score
                best_params = info['params']
        
        elapsed = time.time() - start_time
        
        print(f"\n[WalkForward] 优化完成, 耗时: {elapsed:.1f}s")
        print(f"  最佳参数: {best_params}")
        print(f"  平均测试得分: {best_avg_score:.4f}")
        
        return OptimizationResult(
            best_params=best_params,
            best_score=best_avg_score,
            all_results=self.results,
            optimization_time=elapsed,
            method='walk_forward',
            metric=self.metric
        )
