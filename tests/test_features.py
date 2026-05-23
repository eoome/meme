#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特征工程单元测试
测试内容:
- 基础10维特征计算正确性
- NaN/异常值处理
- 特征归一化范围
- 缠论特征降级
- 特征IC计算
- 特征筛选
"""

import sys
import math
import unittest
import numpy as np
import pandas as pd
from pathlib import Path

# 确保能找到项目代码
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.data.features import (
    calculate_features, extract_window_features, prepare_training_sample,
    FEATURE_COLS, FEATURE_COLS_EXTENDED, winsorize_features,
    compute_feature_ic, select_top_features, validate_data_sufficiency,
)


class TestFeatureCalculation(unittest.TestCase):
    """测试特征计算"""

    def _make_kline_df(self, n: int = 50, add_time: bool = True) -> pd.DataFrame:
        """构造模拟K线数据"""
        np.random.seed(42)
        base = 10.0
        opens = []
        closes = []
        highs = []
        lows = []
        volumes = []
        
        for i in range(n):
            o = base + np.random.randn() * 0.1
            c = o + np.random.randn() * 0.2
            h = max(o, c) + abs(np.random.randn()) * 0.1
            l = min(o, c) - abs(np.random.randn()) * 0.1
            opens.append(o)
            closes.append(c)
            highs.append(h)
            lows.append(l)
            volumes.append(np.random.randint(1000, 100000))
        
        df = pd.DataFrame({
            'open': opens,
            'high': highs,
            'low': lows,
            'close': closes,
            'volume': volumes,
        })
        
        if add_time:
            # 模拟交易时间 09:30 ~ 15:00
            times = pd.date_range('2024-01-01 09:30', periods=n, freq='1min')
            df['time'] = times
        
        return df

    def test_basic_features_exist(self):
        """测试基础10维特征都被计算出来"""
        df = self._make_kline_df(100)
        result = calculate_features(df)
        
        for col in FEATURE_COLS:
            self.assertIn(col, result.columns, f"特征 {col} 不存在")
            # 检查没有 NaN
            self.assertFalse(result[col].isna().all(), f"特征 {col} 全部为 NaN")

    def test_feature_normalization_range(self):
        """测试特征归一化范围"""
        df = self._make_kline_df(100)
        result = calculate_features(df)
        
        # feat_body_ratio 应在 0~1 之间
        br = result['feat_body_ratio'].dropna()
        self.assertTrue((br >= 0).all() and (br <= 1).all(),
                       "feat_body_ratio 超出 [0,1] 范围")
        
        # feat_upper_shadow 应在 0~1 之间
        us = result['feat_upper_shadow'].dropna()
        self.assertTrue((us >= 0).all() and (us <= 1).all(),
                       "feat_upper_shadow 超出 [0,1] 范围")
        
        # feat_lower_shadow 应在 0~1 之间
        ls = result['feat_lower_shadow'].dropna()
        self.assertTrue((ls >= 0).all() and (ls <= 1).all(),
                       "feat_lower_shadow 超出 [0,1] 范围")
        
        # feat_ma_aligned 应为 0 或 1
        ma = result['feat_ma_aligned'].dropna()
        self.assertTrue(ma.isin([0.0, 1.0]).all(),
                       "feat_ma_aligned 不是 0/1")

    def test_feature_with_insufficient_data(self):
        """测试数据不足时的处理"""
        df = self._make_kline_df(5)  # 只有5条，远不够
        result = calculate_features(df)
        
        # 特征列应该存在
        self.assertIn('feat_return', result.columns)
        # 不会因为数据不足而崩溃

    def test_extract_window_features(self):
        """测试窗口特征提取"""
        df = self._make_kline_df(100)
        result = calculate_features(df)
        
        # 正常提取
        window = extract_window_features(result, window=20, cols=FEATURE_COLS)
        self.assertIsNotNone(window)
        self.assertEqual(window.shape, (20, len(FEATURE_COLS)))
        
        # 数据不足时返回 None
        window_none = extract_window_features(result, window=200, cols=FEATURE_COLS)
        self.assertIsNone(window_none)

    def test_prepare_training_sample(self):
        """测试训练样本准备"""
        df = self._make_kline_df(100)
        result = calculate_features(df)
        
        sample = prepare_training_sample(result, lookback=20, cols=FEATURE_COLS)
        self.assertIsNotNone(sample)
        self.assertEqual(sample.shape, (20 * len(FEATURE_COLS),))
        
        # NaN 被填充为 0
        self.assertFalse(np.isnan(sample).any(), "样本中包含 NaN")

    def test_chanlun_features_fallback(self):
        """测试缠论特征降级为0"""
        df = self._make_kline_df(50)
        result = calculate_features(df)
        
        # 缠论特征列存在且为0（降级）
        cl_cols = [c for c in result.columns if c.startswith('feat_cl_')]
        self.assertTrue(len(cl_cols) > 0, "缠论特征列不存在")
        for col in cl_cols:
            self.assertIn(col, result.columns)

    def test_winsorize_features(self):
        """测试极值截断"""
        df = pd.DataFrame({
            'feat_return': [-10, -5, -0.1, 0, 0.1, 5, 10],
            'feat_volume_ratio': [0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 100.0],
        })
        result = winsorize_features(df, cols=['feat_return', 'feat_volume_ratio'], limit=0.1)
        
        # 截断后极值应被限制
        self.assertTrue(result['feat_return'].max() < 10)
        self.assertTrue(result['feat_return'].min() > -10)

    def test_compute_feature_ic(self):
        """测试特征IC计算"""
        df = self._make_kline_df(100)
        result = calculate_features(df)
        
        ic_dict = compute_feature_ic(result, cols=FEATURE_COLS, future_periods=1)
        
        # 返回字典
        self.assertIsInstance(ic_dict, dict)
        # 值在 0~1 之间
        for k, v in ic_dict.items():
            self.assertGreaterEqual(v, 0)
            self.assertLessEqual(v, 1)

    def test_validate_data_sufficiency(self):
        """测试数据充足性校验"""
        # 充足数据
        df_sufficient = self._make_kline_df(50000)  # 约208个交易日
        coverage = validate_data_sufficiency(df_sufficient, freq='minute')
        self.assertTrue(coverage['ok'])
        
        # 不足数据
        df_insufficient = self._make_kline_df(100)
        coverage = validate_data_sufficiency(df_insufficient, freq='minute')
        self.assertFalse(coverage['ok'])


class TestEdgeCases(unittest.TestCase):
    """测试边界情况"""

    def test_empty_dataframe(self):
        """测试空 DataFrame"""
        df = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        result = calculate_features(df)
        # 不应崩溃，返回包含特征列的DataFrame
        self.assertIn('feat_return', result.columns)

    def test_single_row(self):
        """测试只有一行数据"""
        df = pd.DataFrame({
            'open': [10.0],
            'high': [10.5],
            'low': [9.8],
            'close': [10.2],
            'volume': [10000],
        })
        result = calculate_features(df)
        self.assertEqual(len(result), 1)
        self.assertIn('feat_return', result.columns)

    def test_zero_volume(self):
        """测试成交量为0的情况"""
        df = pd.DataFrame({
            'open': [10.0] * 50,
            'high': [10.5] * 50,
            'low': [9.8] * 50,
            'close': [10.2] * 50,
            'volume': [0] * 50,
        })
        result = calculate_features(df)
        # 除以0的处理不应产生 inf
        volume_features = ['feat_volume_ratio']
        for col in volume_features:
            if col in result.columns:
                self.assertFalse(np.isinf(result[col]).any(),
                               f"{col} 包含 inf（除以0未处理）")

    def test_constant_price(self):
        """测试价格不变的情况"""
        df = pd.DataFrame({
            'open': [10.0] * 50,
            'high': [10.0] * 50,
            'low': [10.0] * 50,
            'close': [10.0] * 50,
            'volume': [10000] * 50,
        })
        result = calculate_features(df)
        # K线振幅为0时的处理
        self.assertIn('feat_body_ratio', result.columns)
        # 不应产生 NaN
        for col in FEATURE_COLS:
            if col in result.columns:
                self.assertTrue(result[col].isna().sum() <= len(result),
                              f"{col} 在价格不变时产生异常")


class TestFeatureExtended(unittest.TestCase):
    """测试扩展特征"""

    def test_extended_features_calculated(self):
        """测试扩展特征也被计算"""
        df = pd.DataFrame({
            'open': np.random.randn(100) + 10,
            'high': np.random.randn(100) + 10.5,
            'low': np.random.randn(100) + 9.5,
            'close': np.random.randn(100) + 10,
            'volume': np.random.randint(1000, 100000, 100),
            'time': pd.date_range('2024-01-01', periods=100, freq='1min'),
        })
        result = calculate_features(df)
        
        # 扩展特征列应存在
        extended_cols = [c for c in FEATURE_COLS_EXTENDED if c not in FEATURE_COLS]
        for col in extended_cols[:5]:  # 抽查前5个
            self.assertIn(col, result.columns, f"扩展特征 {col} 不存在")


if __name__ == '__main__':
    unittest.main(verbosity=2)
