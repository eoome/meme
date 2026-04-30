#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版ML回测引擎 v2.1
====================

修复记录 (v2.1):
- [BUG] 滑点双重计算 → _execute_buy/_execute_sell 不再重复加滑点
- [BUG] Sortino 比率 → 使用下行偏差而非负收益 std
- [BUG] 回撤序列索引 → 直接用循环 idx
- [NEW] 蒙特卡洛 → 基于策略逐笔 PnL 的 bootstrap 采样
- [NEW] 信号冷却期 → 止损/止盈后等待 N 根 K 线
- [NEW] 单日亏损限制 → 日亏损超阈值暂停交易
- [FIX] 均价计算 → 包含佣金和印花税
- [FIX] 仓位计算 → 基于总权益(现金+持仓市值)而非剩余现金
"""

import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from strategies.ml.model import get_model
from strategies.data.features import calculate_features, FEATURE_COLS
from strategies.risk.position_sizer import create_position_sizer
from strategies.risk.stop_loss import create_stop_loss
from strategies.risk.take_profit import create_take_profit
from strategies.risk.portfolio_risk import PortfolioRiskManager
from data.auto_save import get_auto_saver
from core.config import get_config


class BacktestConfig:
    """增强回测配置 — 默认值从全局配置读取"""

    def __init__(self, **overrides):
        """初始化"""
        cfg = get_config()
        bt = cfg.backtest
        sl = cfg.stop_loss

        # 基础配置
        self.initial_capital = overrides.get('initial_capital', bt.initial_capital)
        self.base_position = overrides.get('base_position', bt.base_position)
        self.trade_unit = overrides.get('trade_unit', bt.trade_unit)

        # 交易成本（滑点根据品种自动选择）
        self.commission_rate = overrides.get('commission_rate', bt.commission_rate)
        self.stamp_tax = overrides.get('stamp_tax', bt.stamp_tax)
        # T+0 / T+1 交易模式
        self.market_type = overrides.get('market_type', 'etf')
        # 滑点：根据品种类型自动选择，支持手动覆盖
        if 'slippage' in overrides and overrides['slippage'] > 0:
            self.slippage = overrides['slippage']
        else:
            self.slippage = bt.slippage_etf if self.market_type == 'etf' else bt.slippage_stock
        # 滑点细分
        self.slippage_etf = overrides.get('slippage_etf', bt.slippage_etf)
        self.slippage_stock = overrides.get('slippage_stock', bt.slippage_stock)

        # 仓位管理
        self.position_sizer_type = overrides.get('position_sizer_type', 'fixed')
        self.position_sizer_params = overrides.get('position_sizer_params', {})
        self.max_position_multiplier = overrides.get('max_position_multiplier', bt.max_position_multiplier)

        # 止损设置
        self.stop_loss_type = overrides.get('stop_loss_type', 'trailing')
        self.stop_loss_params = overrides.get('stop_loss_params', {'initial_stop_pct': 0.05, 'trailing_pct': 0.03})

        # 止盈设置
        self.tp_activate_pct = overrides.get('tp_activate_pct', 0.03)
        self.tp_trail_pct = overrides.get('tp_trail_pct', 0.015)

        # ML信号设置（注意：config 中是小数如 0.7，BacktestConfig 统一用百分比如 70.0）
        self.min_signal_confidence = overrides.get('min_signal_confidence', cfg.ml.confidence_threshold * 100)
        self.strong_signal_threshold = overrides.get('strong_signal_threshold', cfg.ml.strong_threshold * 100)

        # 交易限制
        self.tail_no_trade_minutes = overrides.get('tail_no_trade_minutes', 5)
        self.min_trade_interval = overrides.get('min_trade_interval', 5)

        # ── 新增: 冷却期 & 日亏损限制 ──
        # 止损/止盈后的冷却 K 线数 (防止在下跌趋势中反复止损-买入)
        self.exit_cooldown_bars = overrides.get('exit_cooldown_bars', 10)
        # 单日最大亏损比例 (占初始资金), 超过则当日暂停交易
        self.daily_loss_limit_pct = overrides.get('daily_loss_limit_pct', 0.03)

        # 回测周期
        self.periods = overrides.get('periods', ['day'])


@dataclass
class EnhancedBacktestResult:
    """增强回测结果"""
    # 基础指标
    total_return: float = 0.0
    annualized_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0

    # 扩展指标
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    information_ratio: float = 0.0
    omega_ratio: float = 0.0

    # 交易统计
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    win_loss_ratio: float = 0.0
    avg_trade_return: float = 0.0

    # 风险统计
    volatility: float = 0.0
    var_95: float = 0.0
    cvar_95: float = 0.0

    # 成本统计
    total_commission: float = 0.0
    total_tax: float = 0.0
    cost_ratio: float = 0.0

    # 对比基准
    hold_only_return: float = 0.0
    excess_return: float = 0.0

    # 数据
    equity_curve: List[Tuple[str, float]] = field(default_factory=list)
    daily_stats: List[Dict] = field(default_factory=list)
    trades: List[Dict] = field(default_factory=list)
    drawdown_series: List[Tuple[str, float]] = field(default_factory=list)

    # 模型信息
    model_info: Dict = field(default_factory=dict)

    # 止损统计
    stop_loss_triggered: int = 0
    stop_loss_pnl: float = 0.0

    def to_dict(self) -> dict:
        """转为可序列化的字典"""
        from dataclasses import asdict
        return asdict(self)


class EnhancedBacktestEngine:
    """增强版回测引擎"""

    def __init__(self, config: BacktestConfig = None):
        """初始化"""
        self.config = config or BacktestConfig()
        self.saver = get_auto_saver()

        # 根据品种类型自动选择滑点
        if self.config.slippage <= 0:
            if self.config.market_type == 'stock':
                self.config.slippage = self.config.slippage_stock
            else:
                self.config.slippage = self.config.slippage_etf

    def run(self, code: str, name: str, data: List[dict],
            cost_price: float = None,
            precomputed_df: pd.DataFrame = None) -> EnhancedBacktestResult:
        """
        运行回测

        Args:
            code: 股票代码
            name: 股票名称
            data: 原始 K 线数据（precomputed_df 为空时使用）
            cost_price: 持仓成本价
            precomputed_df: 预计算特征的 DataFrame（优化模式复用，避免重复计算缠论+特征）
        """
        if precomputed_df is not None:
            df = precomputed_df
        elif data:
            df = self._prepare_data(data)
            if df is None or len(df) < 20:
                return EnhancedBacktestResult()
            df = calculate_features(df)
        else:
            return EnhancedBacktestResult()

        try:
            model = get_model()
        except Exception as e:
            logger.warning(f"回测时模型加载失败: {e}")
            return EnhancedBacktestResult()

        # 确定实际特征列和回看窗口（跟随模型训练配置，避免维度不匹配）
        try:
            model_info = model.get_info()
        except Exception as e:
            logger.warning(f"回测时获取模型信息失败: {e}")
            model_info = {}

        actual_feature_cols = model_info.get('feature_cols', FEATURE_COLS)
        actual_feature_cols = [c for c in actual_feature_cols if c in df.columns]
        if not actual_feature_cols:
            actual_feature_cols = [c for c in FEATURE_COLS if c in df.columns]
        
        # 从模型元数据读取实际的 lookback，避免硬编码 20 导致维度不匹配
        actual_lookback = model_info.get('lookback', 20)

        position_sizer = create_position_sizer(
            self.config.position_sizer_type,
            **self.config.position_sizer_params
        )
        stop_loss = create_stop_loss(
            self.config.stop_loss_type,
            **self.config.stop_loss_params
        )
        take_profit = create_take_profit('trailing', activate_pct=self.config.tp_activate_pct, trail_pct=self.config.tp_trail_pct)
        portfolio_risk = PortfolioRiskManager()

        try:
            result = self._run_backtest(df, model, position_sizer, stop_loss,
                                        take_profit, portfolio_risk, cost_price,
                                        code=code,
                                        actual_feature_cols=actual_feature_cols,
                                        actual_lookback=actual_lookback)
        except Exception as e:
            logger.warning(f"回测执行异常: {e}")
            import traceback
            traceback.print_exc()
            result = EnhancedBacktestResult()

        try:
            self.saver.save_backtest_result(code, result.to_dict())
        except Exception:
            pass
        return result

    def _prepare_data(self, data: List[dict]) -> Optional[pd.DataFrame]:
        """准备数据 — 含深度清洗，防止字符串/None混入数值列"""
        from utils.numeric import clean_num as _clean_num

        # 先逐条清洗数值字段
        cleaned = []
        for item in data:
            d = dict(item)
            for col in ['open', 'high', 'low', 'close', 'price', 'volume', 'change']:
                if col in d:
                    d[col] = _clean_num(d[col], 0.0 if col == 'volume' else 0.0)
            cleaned.append(d)

        df = pd.DataFrame(cleaned)

        if 'close' not in df.columns and 'price' in df.columns:
            df['close'] = df['price']
        for col in ['open', 'high', 'low']:
            if col not in df.columns:
                df[col] = df['close']
        if 'volume' not in df.columns:
            df['volume'] = 0
        # ═══════════════════════════════════════════════════════════
        # 时间列统一: 多数据源返回的时间列名不同，需统一映射为 timestamp
        #   腾讯K线: date  (如 "2024-01-15")
        #   东财K线: date  (如 "2024-01-15")
        #   分钟数据: time (如 "2024-01-15 09:30")
        #   已有预处理: timestamp (统一目标列名)
        # ═══════════════════════════════════════════════════════════
        time_col_candidates = ['timestamp', 'time', 'date', 'datetime']
        time_col = None
        for c in time_col_candidates:
            if c in df.columns:
                time_col = c
                break

        if time_col is None:
            # 无任何时间列，无法排序和回测
            return None

        if time_col != 'timestamp':
            df['timestamp'] = df[time_col]

        # 最终校验: 确保核心列存在
        for col in ['timestamp', 'close', 'volume']:
            if col not in df.columns:
                return None

        return df.sort_values('timestamp').reset_index(drop=True)

    # ──────────────────────────────────────────────────────────
    # 核心回测循环
    # ──────────────────────────────────────────────────────────

    def _run_backtest(self, df: pd.DataFrame, model, position_sizer, stop_loss,
                      take_profit, portfolio_risk, cost_price: float,
                      code: str = "",
                      actual_feature_cols: list = None,
                      actual_lookback: int = 20) -> EnhancedBacktestResult:
        """
        执行回测 — v2.2 修复版

        修复:
        - 滑点: 只在 _execute_buy/_execute_sell 内部应用一次
        - 均价: 包含佣金+税
        - 仓位: 基于总权益(现金+持仓市值)
        - 冷却: 止损/止盈后冷却 N 根 K 线
        - 日亏损: 超限暂停
        - 特征列: 跟随模型训练配置，不再硬编码
        """
        result = EnhancedBacktestResult()

        # 初始化状态
        capital = self.config.initial_capital
        position = 0
        first_price = float(df['close'].iloc[0]) if len(df) > 0 and df['close'].iloc[0] > 0 else 1.0
        avg_cost = cost_price if cost_price and cost_price > 0 else first_price
        total_cost_basis = 0.0  # 总成本基础（含佣金），用于精确计算均价

        equity_curve = []
        trades = []
        daily_pnl = []

        stop_loss_active = False
        stop_loss_count = 0
        stop_loss_pnl_total = 0

        take_profit_active = False

        feature_window = actual_lookback  # 使用模型训练时的 lookback，避免维度不匹配
        last_trade_idx = -self.config.min_trade_interval

        # ── 冷却期计数器 ──
        cooldown_remaining = 0  # 止损/止盈后递减，>0 时禁止买入

        # T+0/T+1 跟踪
        today_bought = 0
        today_sold = 0
        current_date = None
        base_position = self.config.base_position

        # ── 日亏损跟踪 ──
        today_start_equity = self.config.initial_capital
        daily_loss_limit = self.config.initial_capital * self.config.daily_loss_limit_pct

        for idx in range(len(df)):
            row = df.iloc[idx]
            ts = str(row['timestamp'])
            price = float(row['close'])
            date_str = ts[:10] if len(ts) >= 10 else ts

            # 日期切换
            if current_date is None or current_date != date_str:
                current_date = date_str
                today_bought = 0
                today_sold = 0
                today_start_equity = capital + position * price

            # 记录权益
            equity = capital + position * price
            equity_curve.append((ts, equity))

            # 冷却期递减 (每根 K 线减 1)
            if cooldown_remaining > 0:
                cooldown_remaining -= 1

            # 需要足够窗口数据
            if idx < feature_window:
                continue

            # 提取特征并预测（跟随模型训练时的特征列）
            window = df.iloc[idx - feature_window:idx]
            raw_vals = window[actual_feature_cols].values

            # 向量化清洗（特征已预计算，这里只做 NaN/Inf 兜底）
            # flatten: 训练时样本展平为 (lookback * n_features,) 向量，推理需保持一致
            sample_np = np.nan_to_num(raw_vals, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float64).flatten()
            proba = model.predict(sample_np)
            buy_p = proba.get('BUY', 0)
            sell_p = proba.get('SELL', 0)

            conf = self.config.min_signal_confidence / 100.0

            signal = None
            if buy_p > conf and buy_p > sell_p:
                signal = 'BUY'
            elif sell_p > conf and sell_p > buy_p:
                signal = 'SELL'

            # ── 日亏损限制检查 ──
            current_equity = capital + position * price
            daily_loss = today_start_equity - current_equity
            if daily_loss > daily_loss_limit and signal == 'BUY':
                signal = None  # 当日亏损超限，禁止新开仓

            # 检查止损
            if stop_loss_active and position > 0:
                sl_result = stop_loss.update(price)
                if sl_result.should_exit:
                    exec_price = price * (1 - self.config.slippage)
                    pnl = self._execute_sell(position, exec_price, avg_cost,
                                             trades, ts, "止损")
                    capital += position * exec_price
                    daily_pnl.append(pnl)
                    portfolio_risk.record_trade(
                        pnl=pnl, is_close=True, code=code, direction='sell',
                        shares=position, price=exec_price,
                    )
                    position = 0
                    total_cost_basis = 0  # 清仓后重置成本基础
                    stop_loss_active = False
                    take_profit_active = False
                    stop_loss_count += 1
                    stop_loss_pnl_total += pnl
                    # ── 触发冷却 ──
                    cooldown_remaining = self.config.exit_cooldown_bars
                    continue

            # 检查止盈
            if take_profit_active and position > 0:
                tp_result = take_profit.update(price)
                if tp_result.should_exit or tp_result.should_reduce:
                    reduce_pct = tp_result.reduce_pct if tp_result.should_reduce else 1.0
                    sell_qty = max(1, int(position * reduce_pct))
                    sell_qty = min(sell_qty, position)
                    exec_price = price * (1 - self.config.slippage)
                    pnl = self._execute_sell(sell_qty, exec_price, avg_cost,
                                             trades, ts, "止盈")
                    capital += sell_qty * exec_price
                    daily_pnl.append(pnl)
                    portfolio_risk.record_trade(
                        pnl=pnl, is_close=True, code=code, direction='sell',
                        shares=sell_qty, price=exec_price,
                    )
                    position -= sell_qty
                    if position <= 0:
                        stop_loss_active = False
                        take_profit_active = False
                        total_cost_basis = 0  # 清仓后重置成本基础
                        # ── 全仓止盈也触发冷却 ──
                        cooldown_remaining = self.config.exit_cooldown_bars
                    if tp_result.should_exit:
                        continue

            # ─── 信号执行 ───
            if signal == 'BUY':
                # 冷却期检查
                if cooldown_remaining > 0:
                    continue

                # 交易间隔检查
                if idx - last_trade_idx < self.config.min_trade_interval:
                    continue

                # 尾盘禁买
                if len(ts) >= 16:
                    try:
                        from datetime import datetime as dt
                        t = dt.strptime(ts[11:16], "%H:%M")
                        if t.hour == 14 and t.minute >= (60 - self.config.tail_no_trade_minutes):
                            continue
                    except ValueError:
                        pass

                # 仓位限制检查
                max_pos = int(base_position * self.config.max_position_multiplier)
                if position >= max_pos or today_bought >= base_position:
                    continue

                # 组合风控检查
                can_trade, block_reason = portfolio_risk.check_can_trade(
                    code=code, direction='buy', shares=100, price=price,
                    total_capital=self.config.initial_capital,
                )
                if not can_trade:
                    continue

                # ── 仓位计算: 基于总权益 ──
                total_equity = capital + position * price
                stop_loss_pct = self.config.stop_loss_params.get('initial_stop_pct', 0.05)
                pos_size = position_sizer.calculate_with_risk(
                    price=price,
                    total_capital=total_equity,  # 修复: 用总权益而非剩余现金
                    stop_loss_pct=stop_loss_pct,
                )

                shares = min(pos_size.target_shares, int(capital * 0.95 / price))
                shares = (shares // 100) * 100

                remaining = base_position - today_bought
                shares = min(shares, remaining)

                if shares >= 100:
                    exec_price = price * (1 + self.config.slippage)
                    cost = self._execute_buy(shares, exec_price, trades, ts)
                    capital -= cost
                    position += shares
                    today_bought += shares
                    # ── 均价: 基于总成本基础精确计算（不重复计算旧持仓佣金）──
                    buy_commission = max(exec_price * shares * self.config.commission_rate, 5.0)
                    total_cost_basis += exec_price * shares + buy_commission
                    avg_cost = total_cost_basis / position if position > 0 else exec_price
                    last_trade_idx = idx

                    stop_loss.on_entry(exec_price)
                    stop_loss_active = True
                    take_profit.on_entry(exec_price)
                    take_profit_active = True
                    portfolio_risk.record_trade(
                        pnl=0, is_close=False, code=code,
                        direction='buy', shares=shares, price=exec_price,
                    )

            elif signal == 'SELL' and position > 0:
                # 交易间隔检查
                if idx - last_trade_idx < self.config.min_trade_interval:
                    continue

                if self.config.market_type == 'stock':
                    max_sell = base_position - today_sold
                else:
                    max_sell = today_bought + base_position - today_sold

                sell_qty = min(position, max_sell)
                if sell_qty <= 0:
                    continue

                exec_price = price * (1 - self.config.slippage)
                pnl = self._execute_sell(sell_qty, exec_price, avg_cost,
                                         trades, ts, "信号")
                capital += sell_qty * exec_price
                daily_pnl.append(pnl)
                portfolio_risk.record_trade(
                    pnl=pnl, is_close=True, code=code, direction='sell',
                    shares=sell_qty, price=exec_price,
                )
                position -= sell_qty
                today_sold += sell_qty
                last_trade_idx = idx

                if position <= 0:
                    stop_loss_active = False
                    take_profit_active = False
                    total_cost_basis = 0  # 清仓后重置成本基础

        result = self._calculate_metrics(equity_curve, trades, daily_pnl, df)
        result.stop_loss_triggered = stop_loss_count
        result.stop_loss_pnl = stop_loss_pnl_total
        result.model_info = model.get_info()
        return result

    # ──────────────────────────────────────────────────────────
    # 交易执行 (滑点只在这里应用一次)
    # ──────────────────────────────────────────────────────────

    def _execute_buy(self, shares: int, exec_price: float, trades: List, ts: str) -> float:
        """
        执行买入 — exec_price 已含滑点，不再重复计算
        返回: 实际扣款金额 (含佣金)
        """
        amount = exec_price * shares
        commission = max(amount * self.config.commission_rate, 5.0)
        total_cost = amount + commission

        trades.append({
            'time': ts,
            'direction': 'BUY',
            'price': round(exec_price, 3),
            'shares': shares,
            'amount': round(amount, 2),
            'commission': round(commission, 2),
            'tax': 0,
            'pnl': 0,  # BUY交易无盈亏，显式设为0保持字段一致
            'reason': '信号买入'
        })
        return total_cost

    def _execute_sell(self, shares: int, exec_price: float, avg_cost: float,
                      trades: List, ts: str, reason: str) -> float:
        """
        执行卖出 — exec_price 已含滑点，不再重复计算
        返回: 本笔盈亏 (扣佣税后)
        """
        amount = exec_price * shares
        commission = max(amount * self.config.commission_rate, 5.0)
        tax = amount * self.config.stamp_tax

        pnl = (exec_price - avg_cost) * shares - commission - tax

        trades.append({
            'time': ts,
            'direction': 'SELL',
            'price': round(exec_price, 3),
            'shares': shares,
            'amount': round(amount, 2),
            'commission': round(commission, 2),
            'tax': round(tax, 2),
            'pnl': round(pnl, 2),
            'reason': reason
        })
        return pnl

    # ──────────────────────────────────────────────────────────
    # 绩效指标计算
    # ──────────────────────────────────────────────────────────

    def _calculate_metrics(self, equity_curve: List, trades: List,
                           daily_pnl: List, df: pd.DataFrame) -> EnhancedBacktestResult:
        """计算绩效指标"""
        result = EnhancedBacktestResult()

        if not equity_curve:
            return result

        equities = [e[1] for e in equity_curve]
        result.equity_curve = equity_curve

        # 基础指标
        start_equity = equities[0]
        end_equity = equities[-1]
        result.total_return = (end_equity - start_equity) / start_equity

        # 年化收益 — 用实际交易日数而非数据点数（分钟数据点数远大于日数）
        timestamps = [e[0] for e in equity_curve]
        unique_dates = set()
        for ts in timestamps:
            ts_str = str(ts)
            if len(ts_str) >= 10:
                unique_dates.add(ts_str[:10])
        total_days = len(unique_dates) if unique_dates else 1
        if total_days > 0:
            result.annualized_return = (1 + result.total_return) ** (252 / total_days) - 1

        # 日收益率
        daily_returns = []
        for i in range(1, len(equities)):
            ret = (equities[i] - equities[i - 1]) / equities[i - 1]
            daily_returns.append(ret)

        # 波动率
        if daily_returns:
            result.volatility = np.std(daily_returns) * np.sqrt(252)

        # 夏普比率
        if daily_returns and np.std(daily_returns) > 0:
            result.sharpe_ratio = np.sqrt(252) * np.mean(daily_returns) / np.std(daily_returns)

        # ── Sortino 比率 (修复: 使用下行偏差) ──
        if daily_returns:
            mean_ret = np.mean(daily_returns)
            # 下行偏差: 将正收益视为 0, 对所有收益求 std
            downside = [min(r, 0) for r in daily_returns]
            downside_dev = np.sqrt(np.mean([d ** 2 for d in downside]))
            if downside_dev > 0:
                result.sortino_ratio = np.sqrt(252) * mean_ret / downside_dev

        # ── 最大回撤 (修复: 直接用 idx) ──
        peak = equities[0]
        max_dd = 0
        drawdown_series = []
        for i, eq in enumerate(equities):
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak if peak > 0 else 0
            max_dd = min(max_dd, dd)
            drawdown_series.append((equity_curve[i][0], dd))
        result.max_drawdown = max_dd
        result.drawdown_series = drawdown_series

        # Calmar 比率
        if result.max_drawdown != 0:
            result.calmar_ratio = result.annualized_return / abs(result.max_drawdown)

        # VaR 和 CVaR
        if daily_returns:
            result.var_95 = np.percentile(daily_returns, 5)
            cvar_values = [r for r in daily_returns if r <= result.var_95]
            result.cvar_95 = np.mean(cvar_values) if cvar_values else result.var_95

        # 交易统计
        result.total_trades = len(trades)

        if trades:
            sell_trades = [t for t in trades if t['direction'] == 'SELL']
            if sell_trades:
                wins = [t for t in sell_trades if t.get('pnl', 0) > 0]
                result.win_rate = len(wins) / len(sell_trades)

                win_pnl = [t['pnl'] for t in wins]
                loss_pnl = [abs(t['pnl']) for t in sell_trades if t.get('pnl', 0) <= 0]

                if loss_pnl:
                    result.win_loss_ratio = (np.mean(win_pnl) / np.mean(loss_pnl)
                                             if win_pnl else 0)
                    result.profit_factor = (sum(win_pnl) / sum(loss_pnl)
                                            if sum(loss_pnl) > 0 else 0)

                result.avg_trade_return = np.mean([t['pnl'] for t in sell_trades])

        # 成本统计
        result.total_commission = sum(t.get('commission', 0) for t in trades)
        result.total_tax = sum(t.get('tax', 0) for t in trades)
        total_gross = sum(t.get('pnl', 0) + t.get('commission', 0) + t.get('tax', 0)
                          for t in trades if t['direction'] == 'SELL')
        if total_gross > 0:
            result.cost_ratio = (result.total_commission + result.total_tax) / total_gross

        # 基准对比
        first_price = float(df['close'].iloc[0])
        last_price = float(df['close'].iloc[-1])
        result.hold_only_return = (last_price - first_price) / first_price
        result.excess_return = result.total_return - result.hold_only_return

        # Omega 比率
        if daily_returns:
            gains = sum(r for r in daily_returns if r > 0)
            losses = sum(abs(r) for r in daily_returns if r < 0)
            result.omega_ratio = gains / losses if losses > 0 else 0

        result.trades = trades
        return result

    # ──────────────────────────────────────────────────────────
    # 蒙特卡洛模拟 (修复: 基于策略逐笔 PnL 的 bootstrap)
    # ──────────────────────────────────────────────────────────

    def run_monte_carlo(self, code: str, name: str, data: List[dict],
                        n_simulations: int = 1000,
                        base_result: EnhancedBacktestResult = None) -> Dict:
        """
        蒙特卡洛模拟 — 基于策略实际交易 PnL 的 bootstrap 采样

        修复: 不再用价格收益率(只模拟持股), 而是用策略逐笔卖出 PnL 做 bootstrap,
        这样模拟的是策略本身在不同随机序列下的表现分布。

        优化: 支持传入已有的 base_result，避免重复回测。
        """
        # 复用已有回测结果，或重新跑一次
        if base_result is not None and base_result.trades:
            sell_pnls = [t['pnl'] for t in base_result.trades
                         if t['direction'] == 'SELL' and 'pnl' in t]
            if sell_pnls:
                sell_pnls = np.array(sell_pnls)
                simulated_returns = []
                for _ in range(n_simulations):
                    sampled = np.random.choice(sell_pnls, size=len(sell_pnls), replace=True)
                    total_pnl = np.sum(sampled)
                    sim_return = total_pnl / self.config.initial_capital
                    simulated_returns.append(sim_return)

                simulated_returns = np.array(simulated_returns)
                return {
                    'mean_return': float(np.mean(simulated_returns)),
                    'std_return': float(np.std(simulated_returns)),
                    'median_return': float(np.median(simulated_returns)),
                    'percentile_5': float(np.percentile(simulated_returns, 5)),
                    'percentile_95': float(np.percentile(simulated_returns, 95)),
                    'prob_positive': float(np.mean(simulated_returns > 0)),
                    'worst_case': float(np.min(simulated_returns)),
                    'best_case': float(np.max(simulated_returns)),
                    'n_trades': len(sell_pnls),
                }

        # 无已有结果或无交易，重新跑回测
        df = self._prepare_data(data)
        if df is None or len(df) < 20:
            return {}

        df = calculate_features(df)
        model = get_model()

        model_info = model.get_info()
        actual_feature_cols = model_info.get('feature_cols', FEATURE_COLS)
        actual_feature_cols = [c for c in actual_feature_cols if c in df.columns]
        if not actual_feature_cols:
            actual_feature_cols = [c for c in FEATURE_COLS if c in df.columns]
        actual_lookback = model_info.get('lookback', 20)

        position_sizer = create_position_sizer(
            self.config.position_sizer_type,
            **self.config.position_sizer_params
        )
        stop_loss = create_stop_loss(
            self.config.stop_loss_type,
            **self.config.stop_loss_params
        )
        take_profit = create_take_profit('trailing', activate_pct=self.config.tp_activate_pct, trail_pct=self.config.tp_trail_pct)
        portfolio_risk = PortfolioRiskManager()

        base_result = self._run_backtest(df, model, position_sizer, stop_loss,
                                         take_profit, portfolio_risk, cost_price=None,
                                         code=code,
                                         actual_feature_cols=actual_feature_cols,
                                         actual_lookback=actual_lookback)

        # 提取逐笔卖出 PnL
        sell_pnls = [t['pnl'] for t in base_result.trades
                     if t['direction'] == 'SELL' and 'pnl' in t]

        if not sell_pnls:
            # 没有交易, fallback 到价格收益率模拟
            return self._mc_from_prices(df, n_simulations)

        sell_pnls = np.array(sell_pnls)

        # Bootstrap: 随机打乱逐笔 PnL, 累加得到总收益分布
        simulated_returns = []
        for _ in range(n_simulations):
            # 有放回地随机采样 N 笔交易
            sampled = np.random.choice(sell_pnls, size=len(sell_pnls), replace=True)
            total_pnl = np.sum(sampled)
            sim_return = total_pnl / self.config.initial_capital
            simulated_returns.append(sim_return)

        simulated_returns = np.array(simulated_returns)

        return {
            'mean_return': float(np.mean(simulated_returns)),
            'std_return': float(np.std(simulated_returns)),
            'median_return': float(np.median(simulated_returns)),
            'percentile_5': float(np.percentile(simulated_returns, 5)),
            'percentile_95': float(np.percentile(simulated_returns, 95)),
            'prob_positive': float(np.mean(simulated_returns > 0)),
            'worst_case': float(np.min(simulated_returns)),
            'best_case': float(np.max(simulated_returns)),
            'n_trades': len(sell_pnls),
        }

    def _mc_from_prices(self, df: pd.DataFrame, n_simulations: int) -> Dict:
        """Fallback: 用价格收益率做蒙特卡洛 (无交易时)"""
        returns = df['close'].pct_change().dropna()
        simulated_returns = []
        for _ in range(n_simulations):
            shuffled = np.random.choice(returns, size=len(returns), replace=True)
            cumulative = (1 + shuffled).cumprod() - 1
            simulated_returns.append(cumulative[-1])

        simulated_returns = np.array(simulated_returns)
        return {
            'mean_return': float(np.mean(simulated_returns)),
            'std_return': float(np.std(simulated_returns)),
            'median_return': float(np.median(simulated_returns)),
            'percentile_5': float(np.percentile(simulated_returns, 5)),
            'percentile_95': float(np.percentile(simulated_returns, 95)),
            'prob_positive': float(np.mean(simulated_returns > 0)),
            'worst_case': float(np.min(simulated_returns)),
            'best_case': float(np.max(simulated_returns)),
            'n_trades': 0,
        }
