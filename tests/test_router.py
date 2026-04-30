#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据路由器单元测试
测试内容:
- 限流器功能
- 股票代码转换
- 安全浮点转换
- 数据源状态
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_sources.router import (
    RateLimiter, _code_to_symbol, _safe_float,
    DataRouter,
)


class TestRateLimiter(unittest.TestCase):
    """测试限流器"""

    def test_min_interval_enforced(self):
        """测试最小间隔限制"""
        call_times = []
        
        @RateLimiter.min_interval(0.5)  # 500ms间隔
        def fetch_test():
            """数据源测试 — 验证多源数据获取"""
            call_times.append(time.time())
            return "ok"
        
        # 第一次调用
        fetch_test()
        # 立即第二次调用（应被延迟）
        fetch_test()
        
        # 两次调用间隔应 >= 0.5秒
        self.assertEqual(len(call_times), 2)
        interval = call_times[1] - call_times[0]
        self.assertGreaterEqual(interval, 0.5, 
            f"调用间隔 {interval:.3f}s 小于限制 0.5s")

    def test_different_functions_independent(self):
        """测试不同函数互相独立限流"""
        @RateLimiter.min_interval(10.0)  # 很长的间隔
        def func_a():
            """函数A — 获取完整K线数据"""
            return "a"
        
        @RateLimiter.min_interval(10.0)
        def func_b():
            """函数B — 获取半年K线数据"""
            return "b"
        
        # 两个函数应能独立调用，互不影响
        t0 = time.time()
        result_a = func_a()
        result_b = func_b()
        elapsed = time.time() - t0
        
        # 应能在短时间内都完成（不是串行等待）
        self.assertEqual(result_a, "a")
        self.assertEqual(result_b, "b")


class TestCodeToSymbol(unittest.TestCase):
    """测试股票代码转换"""

    def test_shanghai_stock(self):
        """测试沪A代码"""
        self.assertEqual(_code_to_symbol("600519"), "sh600519")
        self.assertEqual(_code_to_symbol("510300"), "sh510300")  # ETF

    def test_shenzhen_stock(self):
        """测试深A代码"""
        self.assertEqual(_code_to_symbol("000001"), "sz000001")
        self.assertEqual(_code_to_symbol("300750"), "sz300750")  # 创业板

    def test_beijing_stock(self):
        """测试北交所代码"""
        self.assertEqual(_code_to_symbol("830000"), "bj830000")
        self.assertEqual(_code_to_symbol("870000"), "bj870000")
        self.assertEqual(_code_to_symbol("920000"), "bj920000")


class TestSafeFloat(unittest.TestCase):
    """测试安全浮点转换"""

    def test_valid_number(self):
        """测试正常数字"""
        self.assertEqual(_safe_float("3.14"), 3.14)
        self.assertEqual(_safe_float("100"), 100.0)
        self.assertEqual(_safe_float("-5.5"), -5.5)

    def test_invalid_string(self):
        """测试无效字符串"""
        self.assertEqual(_safe_float("not_a_number"), 0.0)
        self.assertEqual(_safe_float(""), 0.0)
        self.assertEqual(_safe_float("null"), 0.0)

    def test_special_values(self):
        """测试特殊值"""
        self.assertEqual(_safe_float("inf", default=0.0), 0.0)
        self.assertEqual(_safe_float("-inf", default=0.0), 0.0)
        self.assertEqual(_safe_float("nan", default=0.0), 0.0)

    def test_none_input(self):
        """测试 None 输入"""
        self.assertEqual(_safe_float(None), 0.0)


class TestDataRouter(unittest.TestCase):
    """测试数据路由器"""

    def test_singleton_creation(self):
        """测试路由器创建"""
        router = DataRouter()
        self.assertIsNotNone(router)

    def test_source_status(self):
        """测试数据源状态"""
        router = DataRouter()
        status = router.get_source_status()
        
        # 应有基本数据源状态
        self.assertIn('tencent', status)
        self.assertIn('sina', status)
        self.assertIn('eastmoney', status)
        self.assertIn('akshare', status)

    def test_market_status(self):
        """测试市场状态判断"""
        router = DataRouter()
        status = router.get_market_status()
        
        # 返回值应为 open 或 closed
        self.assertIn(status, ['open', 'closed'])

    def test_close_session(self):
        """测试关闭 session 不崩溃"""
        from data_sources.router import close_session
        # 多次关闭不应崩溃
        close_session()
        close_session()


if __name__ == '__main__':
    unittest.main(verbosity=2)
