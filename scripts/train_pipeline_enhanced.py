#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版ML训练流水线
==================
标注 → 训练 → 测试 → 优化 → 验证
"""

import sys
import os
import time
import json
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_label():
    """Step 1: 自动标注"""
    print("=" * 60)
    print("[1/5] 自动标注")
    print("=" * 60)

    from strategies.data.labeler import batch_label
    batch_label()
    print("✅ 标注完成\n")


def run_train():
    """Step 2: 训练模型"""
    print("=" * 60)
    print("[2/5] 训练模型")
    print("=" * 60)

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

        # 保存训练历史
        try:
            from data.auto_save import get_auto_saver
            saver = get_auto_saver()
            history = {
                'timestamp': datetime.now().isoformat(),
                'model_path': path,
                'metrics': metrics,
                'version': os.path.basename(path)
            }
            saver.save_model_history(history)
        except Exception as e:
            print(f"  ⚠️ 保存训练历史失败: {e}")

    except Exception as e:
        print(f"\n❌ 训练失败: {e}")
        raise


def run_test():
    """Step 3: 测试策略"""
    print("\n" + "=" * 60)
    print("[3/5] 测试 ML 策略")
    print("=" * 60)

    from strategies.engine import MLEngine
    import numpy as np

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


def run_optimize():
    """Step 4: 参数优化（可选模块）"""
    print("\n" + "=" * 60)
    print("[4/5] 参数优化")
    print("=" * 60)

    try:
        from strategies.optimization.param_optimizer import GridSearchOptimizer
    except ImportError as e:
        print(f"⚠️ 优化模块不可用: {e}")
        print("  跳过参数优化步骤")
        return

    try:
        from strategies.backtest_engine_v2 import EnhancedBacktestEngine, BacktestConfig
    except ImportError as e:
        print(f"⚠️ 回测引擎不可用: {e}")
        return

    from data_sources.router import DataRouter

    # 获取测试数据
    router = DataRouter()
    test_codes = ['510300', '159915']

    param_grid = {
        'min_signal_confidence': [60, 70, 80],
    }

    print(f"参数网格: {param_grid}")
    print(f"测试股票: {test_codes}")

    engine = EnhancedBacktestEngine()
    optimizer = GridSearchOptimizer(engine, metric='sharpe_ratio')

    all_results = []

    for code in test_codes:
        print(f"\n  优化 {code}...")
        data = router.get_kline(code, period='day', count=200)
        if data:
            try:
                result = optimizer.optimize(param_grid, data, code, code)
                all_results.append({
                    'code': code,
                    'best_params': result.best_params,
                    'best_score': result.best_score
                })
            except Exception as e:
                print(f"  ⚠️ {code} 优化失败: {e}")

    # 保存优化结果
    if all_results:
        opt_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data", "optimization_results.json"
        )
        os.makedirs(os.path.dirname(opt_file), exist_ok=True)
        with open(opt_file, 'w', encoding='utf-8') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'results': all_results
            }, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 优化结果已保存: {opt_file}")


def run_validate():
    """Step 5: 验证（可选模块）"""
    print("\n" + "=" * 60)
    print("[5/5] 验证")
    print("=" * 60)

    try:
        from strategies.monte_carlo.simulator import MonteCarloSimulator
    except (ImportError, ModuleNotFoundError):
        print("⚠️ 蒙特卡洛模拟模块不可用，跳过验证步骤")
        return

    from data_sources.router import DataRouter

    router = DataRouter()
    code = '510300'

    print(f"\n  对 {code} 进行蒙特卡洛模拟...")

    data = router.get_kline(code, period='day', count=300)
    if data:
        import pandas as pd
        df = pd.DataFrame(data)
        returns = df['close'].pct_change().dropna().tolist()

        simulator = MonteCarloSimulator(n_simulations=500)
        result = simulator.simulate_returns(returns, method='bootstrap')

        simulator.print_report(result)
        print("\n✅ 验证完成")
    else:
        print(f"⚠️ 无法获取 {code} 数据")


def run_health_check():
    """系统健康检查"""
    print("\n" + "=" * 60)
    print("[Health Check] 系统健康检查")
    print("=" * 60)

    try:
        from utils.health_check import HealthChecker
        checker = HealthChecker()
        status = checker.check_all()
        checker.print_report(status)
        return status.overall_status == 'ok'
    except Exception as e:
        print(f"⚠️ 健康检查失败: {e}")
        return False


def full_pipeline():
    """完整流水线"""
    start_time = time.time()

    print("\n" + "=" * 70)
    print("🚀 增强版ML训练流水线")
    print("=" * 70 + "\n")

    # 健康检查
    if not run_health_check():
        print("\n⚠️ 系统健康检查未通过，继续执行...")

    # 执行各步骤
    run_label()
    run_train()
    run_test()
    run_optimize()
    run_validate()

    elapsed = time.time() - start_time

    print("\n" + "=" * 70)
    print(f"🎉 流水线完成! 耗时: {elapsed:.1f}秒")
    print("=" * 70 + "\n")


def quick_pipeline():
    """快速流水线 (跳过优化和验证)"""
    print("\n" + "=" * 70)
    print("🚀 快速训练流水线")
    print("=" * 70 + "\n")

    run_label()
    run_train()
    run_test()

    print("\n" + "=" * 70)
    print("🎉 快速流水线完成!")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="增强版ML训练流水线")
    parser.add_argument("mode", nargs='?', default="all",
                        help="运行模式: all, quick, label, train, test, optimize, validate, health")

    args = parser.parse_args()

    modes = {
        'all': full_pipeline,
        'quick': quick_pipeline,
        'label': run_label,
        'train': run_train,
        'test': run_test,
        'optimize': run_optimize,
        'validate': run_validate,
        'health': run_health_check,
    }

    func = modes.get(args.mode, full_pipeline)

    try:
        func()
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
