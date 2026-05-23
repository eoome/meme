#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 训练流水线：标注 → 训练 → 部署
用法:
  python train_pipeline.py              # 完整流水线
  python train_pipeline.py label        # 仅标注
  python train_pipeline.py train        # 仅训练
  python train_pipeline.py test         # 测试策略
"""

import sys
import os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_label():
    """Step 1: 自动标注"""
    print("=" * 50)
    print("[1/3] 自动标注")
    print("=" * 50)
    from strategies.data.labeler import batch_label
    batch_label()


def run_train():
    """Step 2: 训练模型"""
    print("\n" + "=" * 50)
    print("[2/3] 训练模型")
    print("=" * 50)
    from strategies.ml.trainer import quick_train
    try:
        path, metrics = quick_train()
        if path is None:
            print("\n❌ 训练失败: 无有效训练数据或训练异常")
            return
        print(f"\n✅ 训练完成!")
        print(f"  模型: {path}")
        val_f1 = metrics.get('val_f1', 0)
        print(f"  验证 F1: {val_f1:.3f}")
    except Exception as e:
        print(f"\n❌ 训练失败: {e}")


def run_test():
    """Step 3: 测试策略"""
    print("\n" + "=" * 50)
    print("[3/3] 测试 ML 策略")
    print("=" * 50)

    from strategies.engine import MLEngine

    # 构造模拟数据
    np.random.seed(42)
    prices = 4.0 + np.random.randn(30).cumsum() * 0.01
    minute_data = []
    for i, p in enumerate(prices):
        minute_data.append({
            'time': f'2026-04-14 {9 + i // 12:02d}:{(i % 12) * 5:02d}',
            'open': p - 0.002,
            'high': p + 0.005,
            'low': p - 0.005,
            'close': p,
            'volume': np.random.randint(10000, 50000),
        })

    engine = MLEngine()
    info = engine.get_model_info()
    print(f"\n模型: {info['version']} ({info['type']})")

    signal = engine.analyze(minute_data, code="513100", name="纳指ETF")
    print(f"\n信号: {signal.signal.value}")
    print(f"原因: {signal.reason}")
    print(f"置信度: {signal.confidence:.1f}%")
    print(f"详情: {signal.details}")


def full_pipeline():
    """完整流水线"""
    run_label()
    run_train()
    run_test()
    print("\n" + "=" * 50)
    print("🎉 流水线完成!")
    print("=" * 50)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode == "label":
        run_label()
    elif mode == "train":
        run_train()
    elif mode == "test":
        run_test()
    else:
        full_pipeline()
