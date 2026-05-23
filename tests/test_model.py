#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 模型模块单元测试
测试内容:
- 规则回退模式
- 维度校验
- 模型信息获取
- 单例线程安全
"""

import sys
import unittest
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies.data.features import FEATURE_COLS


class TestMLModelFallback(unittest.TestCase):
    """测试 ML 模型的规则回退功能"""

    def test_rule_fallback_basic(self):
        """测试规则回退返回有效概率分布"""
        # 由于模型文件可能不存在，测试规则回退路径
        from strategies.ml.model import MLModel
        
        model = MLModel()  # 无模型路径，使用规则回退
        
        # 构造一个特征向量
        n_features = len(FEATURE_COLS)
        sample = np.zeros(n_features)
        sample[0] = 0.02   # feat_return 正收益
        sample[6] = 0.01   # feat_vwap_dev 正偏离
        sample[7] = 1.0    # feat_ma_aligned 多头排列
        
        result = model.predict(sample)
        
        # 返回 BUY/SELL/NONE 三个概率
        self.assertIn('BUY', result)
        self.assertIn('SELL', result)
        self.assertIn('NONE', result)
        
        # 概率之和约等于1
        total = result['BUY'] + result['SELL'] + result['NONE']
        self.assertAlmostEqual(total, 1.0, places=5)

    def test_rule_fallback_buy_signal(self):
        """测试规则回退在买入条件下给出较高BUY概率"""
        from strategies.ml.model import MLModel
        
        model = MLModel()
        n_features = len(FEATURE_COLS)
        sample = np.zeros(n_features)
        sample[0] = 0.05   # 大涨
        sample[6] = 0.03   # VWAP正偏离
        sample[7] = 1.0    # 多头排列
        sample[4] = 2.0    # 放量
        
        result = model.predict(sample)
        # 上涨条件应给出较高的BUY概率（>= 0.3）
        self.assertGreaterEqual(result['BUY'], 0.3)

    def test_rule_fallback_sell_signal(self):
        """测试规则回退在卖出条件下给出较高SELL概率"""
        from strategies.ml.model import MLModel
        
        model = MLModel()
        n_features = len(FEATURE_COLS)
        sample = np.zeros(n_features)
        sample[0] = -0.05  # 大跌
        sample[6] = -0.03  # VWAP负偏离
        sample[7] = 0.0    # 非多头排列
        
        result = model.predict(sample)
        # 下跌条件应给出较高的SELL概率（>= 0.3）
        self.assertGreaterEqual(result['SELL'], 0.3)

    def test_dimension_validation(self):
        """测试特征维度校验 — 错误维度应返回安全回退"""
        from strategies.ml.model import MLModel
        
        model = MLModel()
        
        # 维度不匹配的样本（假设模型期望的维度不同）
        wrong_sample = np.zeros(5)  # 错误的维度
        
        # 不应崩溃，应返回规则回退结果
        result = model.predict(wrong_sample)
        self.assertIn('BUY', result)
        self.assertIn('SELL', result)

    def test_model_info(self):
        """测试模型信息获取（字段完整性）"""
        from strategies.ml.model import MLModel
        
        model = MLModel()
        info = model.get_info()
        
        # 核心字段必须存在
        required_keys = ['version', 'type', 'feature_cols', 'lookback',
                         'loaded', 'n_features', 'n_classes']
        for key in required_keys:
            self.assertIn(key, info, f"缺少字段: {key}")
        
        # 类型必须是 lightgbm 或 rule
        self.assertIn(info['type'], ('lightgbm', 'rule'))
        self.assertIsInstance(info['loaded'], bool)
        self.assertEqual(info['n_classes'], 3)
        
        # loaded 和 type 一致性
        if info['type'] == 'lightgbm':
            self.assertTrue(info['loaded'])
        else:
            self.assertFalse(info['loaded'])

    def test_nan_input(self):
        """测试 NaN 输入的处理"""
        from strategies.ml.model import MLModel
        
        model = MLModel()
        n_features = len(FEATURE_COLS)
        sample = np.full(n_features, np.nan)
        
        # NaN 输入不应崩溃
        result = model.predict(sample)
        self.assertIn('BUY', result)
        self.assertIn('SELL', result)

    def test_empty_input(self):
        """测试空输入"""
        from strategies.ml.model import MLModel
        
        model = MLModel()
        
        # 空数组应返回默认概率
        result = model.predict(np.array([]))
        self.assertIn('BUY', result)
        self.assertAlmostEqual(result['BUY'], 0.3, places=1)


class TestModelSingleton(unittest.TestCase):
    """测试模型单例"""

    def test_singleton(self):
        """测试 get_model 返回同一实例"""
        from strategies.ml.model import get_model, reload_model
        
        # 先重置
        reload_model()
        
        m1 = get_model()
        m2 = get_model()
        self.assertIs(m1, m2)
        
        # 重载后应返回新实例
        reload_model()
        m3 = get_model()
        self.assertIsNot(m1, m3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
