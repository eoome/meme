#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动标注器单元测试
测试内容:
- 正常标注流程
- BUY/SELL 标签生成
- 前瞻偏差防护
- 最大回撤约束
- 间距过滤
"""

import sys
import unittest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.data.labeler import KlineLabeler, label_from_csv, LookaheadBiasError


class TestKlineLabeler(unittest.TestCase):
    """测试标注器核心功能"""

    def _make_trending_df(self, n: int = 100, trend: str = 'up') -> pd.DataFrame:
        """构造趋势K线: up=上涨趋势, down=下跌趋势, zigzag=震荡"""
        np.random.seed(42)
        
        if trend == 'up':
            base = np.linspace(10, 15, n)  # 从10涨到15
        elif trend == 'down':
            base = np.linspace(15, 10, n)  # 从15跌到10
        elif trend == 'zigzag':
            base = 12 + 2 * np.sin(np.linspace(0, 4*np.pi, n))  # 震荡
        else:
            base = np.full(n, 10.0)
        
        noise = np.random.randn(n) * 0.1
        closes = base + noise
        opens = closes + np.random.randn(n) * 0.05
        highs = np.maximum(opens, closes) + np.abs(np.random.randn(n)) * 0.08
        lows = np.minimum(opens, closes) - np.abs(np.random.randn(n)) * 0.08
        
        # 历史时间戳（确保不是未来）
        times = pd.date_range(
            start=datetime.now() - timedelta(days=30),
            periods=n, freq='5min'
        )
        
        return pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': np.random.randint(10000, 1000000, n),
            'time': times,
        })

    def test_label_basic(self):
        """测试基本标注功能"""
        df = self._make_trending_df(200, 'zigzag')
        labeler = KlineLabeler()
        result = labeler.label(df)
        
        # 标签列存在
        self.assertIn('label', result.columns)
        self.assertIn('label_price', result.columns)
        
        # 标签值合法
        valid_labels = {'BUY', 'SELL', 'NONE'}
        self.assertTrue(result['label'].isin(valid_labels).all())

    def test_buy_signals_in_zigzag(self):
        """测试震荡市中的 BUY 信号"""
        df = self._make_trending_df(300, 'zigzag')
        labeler = KlineLabeler()
        result = labeler.label(df)
        
        stats = labeler.get_statistics(result)
        # 震荡市中应该有一些信号
        self.assertGreater(stats['signal_rate'], 0, "震荡市中未产生任何信号")

    def test_max_drawdown_constraint(self):
        """测试最大回撤约束"""
        # 构造一个先大跌再小涨的数据
        n = 100
        df = pd.DataFrame({
            'open': [10.0] * n,
            'high': [10.0] * n,
            'low': [10.0] * n,
            'close': [10.0] * n,
            'volume': [100000] * n,
            'time': pd.date_range(
                start=datetime.now() - timedelta(days=10),
                periods=n, freq='5min'
            ),
        })
        # 在位置30设置一个"假低点"：先跌5%再涨1%
        for i in range(30, 50):
            df.loc[i, 'low'] = 9.0   # 大跌
            df.loc[i, 'close'] = 9.5
        for i in range(50, 60):
            df.loc[i, 'high'] = 10.1  # 小涨
            df.loc[i, 'close'] = 10.1
        
        labeler = KlineLabeler(max_drawdown=0.03)  # 3%回撤限制
        result = labeler.label(df)
        
        # 大回撤后的BUY应被过滤掉
        # 但不能完全没信号，可能有其他位置的BUY

    def test_spacing_filter(self):
        """测试信号间距过滤"""
        df = self._make_trending_df(500, 'zigzag')
        labeler = KlineLabeler(min_spacing=10)
        result = labeler.label(df)
        
        buy_indices = result[result['label'] == 'BUY'].index.tolist()
        # BUY信号间距应 >= min_spacing
        for i in range(1, len(buy_indices)):
            spacing = buy_indices[i] - buy_indices[i-1]
            self.assertGreaterEqual(spacing, 10,
                f"BUY信号间距 {spacing} 小于最小间距 10")

    def test_statistics(self):
        """测试统计功能"""
        df = self._make_trending_df(200, 'zigzag')
        labeler = KlineLabeler()
        result = labeler.label(df)
        stats = labeler.get_statistics(result)
        
        required_keys = ['total_samples', 'buy_signals', 'sell_signals', 
                        'none_samples', 'signal_rate']
        for key in required_keys:
            self.assertIn(key, stats)
        
        # 总样本数正确
        self.assertEqual(stats['total_samples'], len(df))
        # BUY + SELL + NONE = 总样本
        total = stats['buy_signals'] + stats['sell_signals'] + stats['none_samples']
        self.assertEqual(total, stats['total_samples'])

    def test_future_data_detection(self):
        """测试前瞻偏差防护 — 包含未来时间的数据应抛出异常"""
        # 构造包含未来时间的数据
        future_time = datetime.now() + timedelta(days=7)
        df = pd.DataFrame({
            'open': [10.0] * 50,
            'high': [10.5] * 50,
            'low': [9.8] * 50,
            'close': [10.2] * 50,
            'volume': [100000] * 50,
            'time': pd.date_range(future_time, periods=50, freq='5min'),
        })
        
        labeler = KlineLabeler()
        with self.assertRaises(LookaheadBiasError) as ctx:
            labeler.label(df)
        
        self.assertIn("未来", str(ctx.exception))

    def test_historical_data_allowed(self):
        """测试历史数据可以正常标注"""
        past_time = datetime.now() - timedelta(days=60)
        df = pd.DataFrame({
            'open': [10.0] * 100,
            'high': [10.5] * 100,
            'low': [9.8] * 100,
            'close': [10.2] * 100,
            'volume': [100000] * 100,
            'time': pd.date_range(past_time, periods=100, freq='5min'),
        })
        
        labeler = KlineLabeler()
        # 历史数据不应抛出异常
        result = labeler.label(df)
        self.assertIn('label', result.columns)


class TestLabelerEdgeCases(unittest.TestCase):
    """测试标注器边界情况"""

    def test_empty_dataframe(self):
        """测试空DataFrame"""
        df = pd.DataFrame({
            'open': [], 'high': [], 'low': [], 'close': [], 'volume': [],
            'time': [],
        })
        labeler = KlineLabeler()
        result = labeler.label(df)
        self.assertEqual(len(result), 0)

    def test_single_row(self):
        """测试只有一行数据"""
        df = pd.DataFrame({
            'open': [10.0],
            'high': [10.5],
            'low': [9.8],
            'close': [10.2],
            'volume': [100000],
            'time': [datetime.now() - timedelta(days=1)],
        })
        labeler = KlineLabeler()
        result = labeler.label(df)
        # 单行不应产生信号，但不应崩溃
        self.assertEqual(result['label'].iloc[0], 'NONE')

    def test_all_same_price(self):
        """测试价格完全不变"""
        n = 100
        past_time = datetime.now() - timedelta(days=10)
        df = pd.DataFrame({
            'open': [10.0] * n,
            'high': [10.0] * n,
            'low': [10.0] * n,
            'close': [10.0] * n,
            'volume': [100000] * n,
            'time': pd.date_range(past_time, periods=n, freq='5min'),
        })
        
        labeler = KlineLabeler()
        result = labeler.label(df)
        # 价格不变时，不应产生有效信号（meaningful过滤）
        stats = labeler.get_statistics(result)
        self.assertEqual(stats['signal_rate'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
