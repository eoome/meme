#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特征工程：从K线计算ML特征

所有特征均归一化到 ~0~1 或 ~-1~1 区间，保证不同价位股票可比。
训练与推理使用统一的特征列，由 FEATURE_COLS 单一控制。

特征清单:
  基础特征集 FEATURE_COLS: 10 维
  扩展特征集 FEATURE_COLS_EXTENDED: 34 维 (基础10 + 扩展19 + 缠论5)
"""

import logging
import warnings
import pandas as pd
import numpy as np
from typing import Optional, List, Dict

# 抑制 pandas/numpy 滚动计算中的无害警告
warnings.filterwarnings("ignore", message="All-NaN slice encountered", category=RuntimeWarning)
warnings.filterwarnings("ignore", message="invalid value encountered in divide", category=RuntimeWarning)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 数据最低标准 — 特征质量依赖数据量
# ═══════════════════════════════════════════════════════════════
MINUTE_MIN_BARS   = 30_000   # 分钟级 T+0 策略: ≥6 个月 ≈ 3万条
DAILY_MIN_BARS    = 500      # 日频策略: ≥2 年 ≈ 500 条

# ═══════════════════════════════════════════════════════════════
# 单一特征维度控制 — 训练和推理必须完全一致
# ═══════════════════════════════════════════════════════════════

FEATURE_COLS = [
    # ── 基础 10 维 ──
    'feat_return',          # 涨跌幅 (close-open)/open, 归一化
    'feat_body_ratio',      # K线实体占比 |close-open|/(high-low), 0~1
    'feat_upper_shadow',    # 上影线比例, 归一化 0~1
    'feat_lower_shadow',    # 下影线比例, 归一化 0~1
    'feat_volume_ratio',    # 成交量/20均量, 归一化
    'feat_atr5',            # 5日真实波幅 / close, 归一化
    'feat_vwap_dev',        # VWAP偏离度, 归一化
    'feat_ma_aligned',      # 均线排列 (MA5>MA10>MA20), 0/1
    'feat_time_sin',        # 日内时间正弦编码, -1~1
    'feat_vol_regime',      # 波动率状态, 0/1
]

# 扩展特征集 (需要重新训练模型才能使用)
# ── 波动率与风险 4 维 ──
# ── 动量与反转 4 维 ──
# ── 量价关系 4 维 ──
# ── 市场微观结构 3 维 ──
# ── 原有扩展 5 维 ──
FEATURE_COLS_EXTENDED = FEATURE_COLS + [
    # 波动率与风险 (4)
    'feat_rv5d',                # 5日实现波动率 (分钟级高频数据低频化)
    'feat_parkinson_vol',       # 帕金森波动率 (基于高低价)
    'feat_gk_vol',              # Garman-Klass 波动率 (利用 OHLC 四价)
    'feat_vol_regime_tri',      # 波动率三态 (高=1/中=0.5/低=0)
    # 动量与反转 (4)
    'feat_rsi14',               # RSI14 归一化 0~1
    'feat_macd_hist',           # MACD 柱状图 (动量加速度), 归一化
    'feat_momentum_20d',        # 20日动量 (剔除近5日), 归一化
    'feat_return_1m_max',       # 过去1月最大单日涨幅 (极端情绪), 归一化
    # 量价关系 (4)
    'feat_turnover',            # 换手率代理 (成交量/20均量 对数), 归一化
    'feat_vpt',                 # 量价趋势指标 (VPT 累积), 归一化
    'feat_obv',                 # 能量潮 OBV 趋势斜率, 归一化
    'feat_mfi',                 # 资金流量指标 MFI, 归一化 0~1
    # 市场微观结构 (3)
    'feat_amihud',              # Amihud 非流动性比率, 归一化
    'feat_intraday_skew',       # 日内收益率偏度, 归一化
    'feat_buy_sell_pressure',   # 买卖压力比, 归一化
    # 原有扩展 (5)
    'feat_bb_position',         # 布林带位置 0~1
    'feat_macd_diff',           # MACD差离 归一化
    'feat_gap',                 # 跳空缺口 归一化
    'feat_volume_trend',        # 成交量趋势 归一化
    # ── 缠论特征 (5维, 用预留位 + 新增) ──
    'feat_cl_fractal_dir',      # 最新分型方向 1/-1/0
    'feat_cl_stroke_dir',       # 最新笔方向 1/-1/0
    'feat_cl_hub_position',     # 价格相对中枢位置 1/0/-1
    'feat_cl_hub_deviation',    # 偏离中枢百分比 归一化
    'feat_cl_stroke_momentum',  # 笔动量 -3~3 归一化
    # ── 缠论扩展 (3维, 线段+成交量背驰) ──
    'feat_cl_seg_dir',          # 最新线段方向 1/-1/0
    'feat_cl_seg_has_hub',      # 线段内是否有中枢 1/0
    'feat_cl_volume_div',       # 成交量背驰(底+顶) 1/0
]


# ─── 内部辅助函数 ───

def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """计算 RSI (0~100)
    注: 功能等同于 pandas-ta 的 RSI，此处为避免外部依赖自行实现。
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period, min_periods=1).mean()
    avg_loss = loss.rolling(period, min_periods=1).mean()
    rs = avg_gain / (avg_loss + 1e-8)
    return 100 - (100 / (1 + rs))


def _bollinger_position(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.Series:
    """计算布林带位置 (0=下轨, 1=上轨)
    注: 功能等同于 pandas-ta 的 bbands 位置计算，此处为避免外部依赖自行实现。
    """
    ma = series.rolling(period, min_periods=1).mean()
    std = series.rolling(period, min_periods=1).std()
    upper = ma + num_std * std
    lower = ma - num_std * std
    return (series - lower) / (upper - lower + 1e-8)


def _macd_diff(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """计算 MACD 差离值 — 与 _macd_histogram 相同（兼容别名）"""
    return _macd_histogram(series, fast, slow, signal)


def _macd_histogram(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """计算 MACD 柱状图 (MACD Histogram = DIF - DEA) — 动量加速度"""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif - dea


def _realized_volatility(close: pd.Series, window: int = 5) -> pd.Series:
    """实现波动率: log 收益率的滚动标准差 * sqrt(周期数)"""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window, min_periods=1).std() * np.sqrt(window)


def _parkinson_volatility(high: pd.Series, low: pd.Series, window: int = 5) -> pd.Series:
    """Parkinson 波动率: 基于高低价, 比 Close-to-Close 更高效"""
    log_hl = np.log(high / (low + 1e-8)) ** 2
    factor = 1.0 / (4.0 * np.log(2.0))
    return np.sqrt(factor * log_hl.rolling(window, min_periods=1).mean())


def _garman_klass_volatility(open_: pd.Series, high: pd.Series, low: pd.Series,
                              close: pd.Series, window: int = 5) -> pd.Series:
    """Garman-Klass 波动率: 利用 OHLC 四价, 比 Parkinson 更优"""
    log_hl = np.log(high / (low + 1e-8)) ** 2
    log_co = np.log(close / (open_ + 1e-8)) ** 2
    gk = 0.5 * log_hl - (2 * np.log(2) - 1) * log_co
    return np.sqrt(gk.rolling(window, min_periods=1).mean().clip(lower=0))


def _obv_slope(close: pd.Series, volume: pd.Series, window: int = 10) -> pd.Series:
    """OBV 趋势斜率 — 正=累积买入, 负=累积卖出"""
    direction = np.sign(close.diff()).fillna(0)
    obv = (direction * volume).cumsum()
    # 归一化: OBV 与其 window 期均线的距离 / OBV 绝对值
    obv_ma = obv.rolling(window, min_periods=1).mean()
    return (obv - obv_ma) / (obv.abs() + 1e-8)


def _money_flow_index(high: pd.Series, low: pd.Series, close: pd.Series,
                       volume: pd.Series, period: int = 14) -> pd.Series:
    """MFI 资金流量指标 (0~100)"""
    typical = (high + low + close) / 3
    raw_money_flow = typical * volume
    pos_flow = np.where(typical > typical.shift(1), raw_money_flow, 0)
    neg_flow = np.where(typical <= typical.shift(1), raw_money_flow, 0)
    pos_flow = pd.Series(pos_flow, index=close.index)
    neg_flow = pd.Series(neg_flow, index=close.index)
    pos_sum = pos_flow.rolling(period, min_periods=1).sum()
    neg_sum = neg_flow.rolling(period, min_periods=1).sum()
    mfr = pos_sum / (neg_sum + 1e-8)
    return 100 - (100 / (1 + mfr))


def _amihud_illiquidity(close: pd.Series, amount: pd.Series, window: int = 20) -> pd.Series:
    """Amihud 非流动性比率: |收益率| / 成交额 — 越大越不流动"""
    abs_ret = abs(close.pct_change())
    dollar_vol = amount.replace(0, np.nan)
    illiq = (abs_ret / dollar_vol).rolling(window, min_periods=1).mean()
    # 对数压缩 + 分位数归一化
    log_illiq = np.log1p(illiq * 1e8)  # 缩放到合理量级
    return log_illiq / (log_illiq.rolling(60, min_periods=1).quantile(0.95) + 1e-8)


def _intraday_return_skew(close: pd.Series, window: int = 60) -> pd.Series:
    """日内收益率偏度 — 肥尾/不对称风险度量"""
    log_ret = np.log(close / close.shift(1))
    skew = log_ret.rolling(window, min_periods=10).skew()
    return skew.clip(-3, 3) / 3  # 归一化到 -1~1


def _buy_sell_pressure(close: pd.Series, high: pd.Series, low: pd.Series,
                        volume: pd.Series, window: int = 20) -> pd.Series:
    """买卖压力比 — 基于价格位置推算资金流入流出"""
    position = (close - low) / (high - low + 1e-8)  # 收盘在 K 线中的位置
    buy_vol = position * volume   # 收盘靠近高点 → 买方主导
    sell_vol = (1 - position) * volume
    buy_sum = buy_vol.rolling(window, min_periods=1).sum()
    sell_sum = sell_vol.rolling(window, min_periods=1).sum()
    ratio = (buy_sum - sell_sum) / (buy_sum + sell_sum + 1e-8)
    return ratio  # 已在 -1~1 区间


def _vpt(close: pd.Series, volume: pd.Series) -> pd.Series:
    """VPT 量价趋势: cumsum(volume * pct_change)"""
    pct = close.pct_change().fillna(0)
    vpt = (volume * pct).cumsum()
    # 归一化: 与均线的偏差
    vpt_ma = vpt.rolling(20, min_periods=1).mean()
    return (vpt - vpt_ma) / (vpt.abs() + 1e-8)


# ─── 主特征计算 ───

def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    从K线DataFrame计算全部特征 (10维基础 + 5维扩展)
    输入列: open, high, low, close, volume (可选: time/datetime/timestamp)
    所有特征均归一化，保证跨品种可比。
    """
    df = df.copy()

    # ─── 原始 10 维特征 ───

    # 1. 涨跌幅 — 已归一化
    df['feat_return'] = (df['close'] - df['open']) / (df['open'] + 1e-8)

    # 2. 实体占比 — 0~1 (除以 K 线振幅，不是绝对价格)
    df['feat_body_ratio'] = abs(df['close'] - df['open']) / (df['high'] - df['low'] + 1e-8)

    # 3. 上影线比例 — 归一化到 0~1 (除以振幅，不是绝对价格)
    df['feat_upper_shadow'] = (
        (df['high'] - np.maximum(df['close'], df['open']))
        / (df['high'] - df['low'] + 1e-8)
    )

    # 4. 下影线比例 — 归一化到 0~1 (除以振幅，不是绝对价格)
    df['feat_lower_shadow'] = (
        (np.minimum(df['close'], df['open']) - df['low'])
        / (df['high'] - df['low'] + 1e-8)
    )

    # 5. 成交量比 — 当前 / 20均量，对数压缩防止极端值
    vol_mean = df['volume'].rolling(20, min_periods=1).mean()
    raw_ratio = df['volume'] / (vol_mean + 1e-8)
    df['feat_volume_ratio'] = np.log1p(raw_ratio) / np.log1p(3.0)  # 压缩到 ~0~1 (ratio=3 → ~0.7)

    # 6. ATR5 / close — 波幅百分比，归一化
    high_low = df['high'] - df['low']
    high_close = abs(df['high'] - df['close'].shift(1))
    low_close = abs(df['low'] - df['close'].shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr5 = tr.rolling(5, min_periods=1).mean()
    df['feat_atr5'] = atr5 / (df['close'] + 1e-8)  # 已是百分比

    # 7. VWAP 偏离 — 按交易日重置累积，归一化
    # 空DataFrame防护: 数据为空时直接返回0
    if len(df) == 0:
        df['feat_vwap_dev'] = 0.0
    else:
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        tp_vol = typical_price * df['volume']
        # 从 time/datetime/timestamp 列提取日期分组
        _dates = None
        _time_col = None
        for _col_name in ['time', 'datetime', 'timestamp']:
            if _col_name in df.columns:
                _time_col = _col_name
                _dates = pd.to_datetime(df[_col_name], errors='coerce').dt.date
                break
        if _dates is None:
            # 无时间列时，按固定窗口（240条≈1交易日）模拟按日分组
            # 避免 VWAP 变成全局累积导致开盘时段失真
            # 动态推断 bars_per_day: 尝试从时间列计算，否则用默认值
            _bars_per_day = 240  # 默认: A股分钟线 4h×60min
            if _time_col is not None:
                try:
                    _dates_series = pd.to_datetime(df[_time_col], errors='coerce')
                    if _dates_series.notna().sum() > 1:
                        _unique_dates = _dates_series.dt.date.nunique()
                        if _unique_dates > 0:
                            _bars_per_day = max(1, len(df) // _unique_dates)
                except Exception as e:
                    logger.debug(f"特征计算失败: {e}")
            _dates = pd.Series((df.index // _bars_per_day).astype(int))
        cum_vol = df['volume'].groupby(_dates).cumsum().replace(0, np.nan)
        cum_tp_vol = tp_vol.groupby(_dates).cumsum()
        # 防NaN传播: cum_vol全为NaN时用close代替VWAP
        if cum_vol.isna().all():
            vwap = df['close']
        else:
            vwap = (cum_tp_vol / cum_vol).ffill().bfill()
            if vwap.isna().all():
                vwap = df['close']
        df['feat_vwap_dev'] = (df['close'] - vwap) / (vwap + 1e-8)

    # 8. 均线排列 (MA5 > MA10 > MA20) — 0/1
    ma5 = df['close'].rolling(5, min_periods=1).mean()
    ma10 = df['close'].rolling(10, min_periods=1).mean()
    ma20 = df['close'].rolling(20, min_periods=1).mean()
    df['feat_ma_aligned'] = ((ma5 > ma10) & (ma10 > ma20)).astype(float)

    # 9. 时间编码 — 日内分钟数正弦变换, -1~1
    _time_col = None
    for _col in ['time', 'datetime', 'timestamp']:
        if _col in df.columns:
            _time_col = _col
            break
    if _time_col:
        dt = pd.to_datetime(df[_time_col], errors='coerce')
        minutes = dt.dt.hour * 60 + dt.dt.minute
        df['feat_time_sin'] = np.sin(2 * np.pi * minutes / 240)
    else:
        df['feat_time_sin'] = 0.0

    # 10. 波动率状态 (ATR/价格 > 0.2%) — 0/1
    df['feat_vol_regime'] = (df['feat_atr5'] > 0.002).astype(float)

    # ─── 波动率与风险 (4 维) ───

    # 11. 5日实现波动率 — log 收益率标准差
    df['feat_rv5d'] = _realized_volatility(df['close'], 5)

    # 12. Parkinson 波动率 — 基于高低价
    df['feat_parkinson_vol'] = _parkinson_volatility(df['high'], df['low'], 5)

    # 13. Garman-Klass 波动率 — OHLC 四价
    df['feat_gk_vol'] = _garman_klass_volatility(
        df['open'], df['high'], df['low'], df['close'], 5
    )

    # 14. 波动率三态 — 高/中/低 (基于 ATR5 分位数)
    atr_pct = df['feat_atr5']
    p33 = atr_pct.rolling(60, min_periods=10).quantile(0.33)
    p67 = atr_pct.rolling(60, min_periods=10).quantile(0.67)
    df['feat_vol_regime_tri'] = np.where(
        atr_pct > p67, 1.0, np.where(atr_pct < p33, 0.0, 0.5)
    )

    # ─── 动量与反转 (4 维) ───

    # 15. RSI14 — 归一化 0~1
    df['feat_rsi14'] = _rsi(df['close'], 14) / 100.0

    # 16. MACD 柱状图 — 动量加速度, 归一化
    macd_hist_raw = _macd_histogram(df['close'])
    price_std = df['close'].rolling(20, min_periods=1).std()
    df['feat_macd_hist'] = macd_hist_raw / (price_std + 1e-8)

    # 17. 20日动量 (剔除近5日) — 归一化
    mom20 = (df['close'] / df['close'].shift(20) - 1).shift(5)
    df['feat_momentum_20d'] = mom20.clip(-0.3, 0.3) / 0.3  # ±30% 截断

    # 18. 过去1月最大单日涨幅 — 极端情绪, 归一化
    daily_ret = df['close'].pct_change()
    df['feat_return_1m_max'] = daily_ret.rolling(20, min_periods=1).max().clip(0, 0.1) / 0.1

    # ─── 量价关系 (4 维) ───

    # 19. 换手率代理 — 成交量对数比, 归一化
    vol_ma20 = df['volume'].rolling(20, min_periods=1).mean()
    df['feat_turnover'] = np.log1p(df['volume'] / (vol_ma20 + 1e-8)) / np.log1p(5.0)

    # 20. VPT 量价趋势 — 累积量价, 归一化
    df['feat_vpt'] = _vpt(df['close'], df['volume'])

    # 21. OBV 趋势斜率 — 归一化
    df['feat_obv'] = _obv_slope(df['close'], df['volume'], 10)

    # 22. MFI 资金流量 — 归一化 0~1
    df['feat_mfi'] = _money_flow_index(
        df['high'], df['low'], df['close'], df['volume'], 14
    ) / 100.0

    # ─── 市场微观结构 (3 维) ───

    # 23. Amihud 非流动性 — 归一化
    _amount = df.get('amount', df['volume'] * df['close'])
    df['feat_amihud'] = _amihud_illiquidity(df['close'], _amount, 20)

    # 24. 日内收益率偏度 — 归一化到 -1~1
    df['feat_intraday_skew'] = _intraday_return_skew(df['close'], 60)

    # 25. 买卖压力比 — 已在 -1~1 区间
    df['feat_buy_sell_pressure'] = _buy_sell_pressure(
        df['close'], df['high'], df['low'], df['volume'], 20
    )

    # ─── 原有扩展 (5 维) ───

    # 26. 布林带位置 — 0~1
    df['feat_bb_position'] = _bollinger_position(df['close'])

    # 27. MACD 差离 — 归一化
    macd_raw = _macd_diff(df['close'])
    df['feat_macd_diff'] = macd_raw / (price_std + 1e-8)

    # 28. 跳空缺口 — 归一化
    prev_close = df['close'].shift(1)
    df['feat_gap'] = (df['open'] - prev_close) / (prev_close + 1e-8)

    # 29. 成交量趋势 — 5日量均线斜率, 归一化
    vol_ma5 = df['volume'].rolling(5, min_periods=1).mean()
    vol_ma5_prev = vol_ma5.shift(1)
    df['feat_volume_trend'] = (vol_ma5 - vol_ma5_prev) / (vol_ma5_prev + 1e-8)

    # 30. 预留 → 缠论特征
    # ═══════════════════════════════════════════════════════════
    # 缠论特征: 分型/笔/中枢 — 序列特征，需完整DataFrame
    # 跳过条件：如果已有缠论特征列（避免重复计算）
    # ═══════════════════════════════════════════════════════════
    chanlun_cols = ['feat_cl_fractal_dir', 'feat_cl_stroke_dir',
                   'feat_cl_hub_position', 'feat_cl_hub_deviation',
                   'feat_cl_stroke_momentum',
                   'feat_cl_seg_dir', 'feat_cl_seg_has_hub', 'feat_cl_volume_div']
    if all(c in df.columns for c in chanlun_cols):
        pass  # 已计算过，跳过
    else:
        try:
            from strategies.data.chanlun import ChanLunFeatureExtractor
            cl = ChanLunFeatureExtractor(df)
            cl_features = cl.extract_features()

            # 逐个提取特征，单个失败不影响其他特征
            def _safe_clip(val, lo=-1, hi=1, scale=1):
                try:
                    return np.clip(float(val) / scale, lo, hi)
                except (TypeError, ValueError):
                    return 0.0

            df['feat_cl_fractal_dir'] = _safe_clip(cl_features.get('cl_last_fractal_dir', 0))
            df['feat_cl_stroke_dir'] = _safe_clip(cl_features.get('cl_last_stroke_dir', 0))
            df['feat_cl_hub_position'] = _safe_clip(cl_features.get('cl_price_to_hub', 0))
            df['feat_cl_hub_deviation'] = _safe_clip(cl_features.get('cl_hub_deviation_pct', 0), scale=10)
            df['feat_cl_stroke_momentum'] = _safe_clip(cl_features.get('cl_stroke_momentum', 0), scale=3)
            df['feat_cl_seg_dir'] = _safe_clip(cl_features.get('cl_seg_dir', 0))
            df['feat_cl_seg_has_hub'] = _safe_clip(cl_features.get('cl_seg_has_hub', 0), lo=0, hi=1)
            has_vol_div = (
                cl_features.get('cl_volume_bottom_div', 0) > 0 or
                cl_features.get('cl_volume_top_div', 0) > 0
            )
            df['feat_cl_volume_div'] = 1.0 if has_vol_div else 0.0
        except Exception as e:
            # 缠论特征失败用 warning（首次可见），不影响主流程继续
            logger.warning(f"缠论特征计算失败，已降级为0: {e}")
            for _col in ['feat_cl_fractal_dir', 'feat_cl_stroke_dir',
                         'feat_cl_hub_position', 'feat_cl_hub_deviation',
                         'feat_cl_stroke_momentum', 'feat_cl_seg_dir',
                         'feat_cl_seg_has_hub', 'feat_cl_volume_div']:
                df[_col] = 0.0

    # ═══════════════════════════════════════════════════════════
    # 最终防线: 强制将所有特征列转为数值类型，删除/转换任何残留的字符串
    # ═══════════════════════════════════════════════════════════
    all_feature_cols = FEATURE_COLS_EXTENDED if FEATURE_COLS_EXTENDED else FEATURE_COLS
    for col in all_feature_cols:
        if col in df.columns:
            if df[col].dtype == object or df[col].dtype.kind in ('U', 'S', 'O'):
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

    return df


def extract_window_features(
    df: pd.DataFrame,
    window: int = 20,
    cols: Optional[List[str]] = None
) -> Optional[np.ndarray]:
    """
    提取最近 window 根 K 线的特征矩阵 → (window, len(cols))
    默认使用 FEATURE_COLS (训练/推理统一)
    自动清洗 object/string 类型，防止脏值进入模型
    """
    cols = cols or FEATURE_COLS
    available = [c for c in cols if c in df.columns]
    if len(df) < window or not available:
        return None
    recent = df[available].tail(window)
    # 强制转为数值类型: object/string 列会被转为 float，无法转换的变为 NaN
    for c in available:
        if recent[c].dtype == object or recent[c].dtype.kind in ('U', 'S', 'O'):
            recent[c] = pd.to_numeric(recent[c], errors='coerce')
    recent = recent.fillna(0)
    return recent.values.astype(np.float64)


def prepare_training_sample(
    df: pd.DataFrame,
    lookback: int = 20,
    cols: Optional[List[str]] = None
) -> Optional[np.ndarray]:
    """
    为模型预测准备单条展平样本 → (window * len(cols),)
    训练和推理均调用此函数，保证维度一致。
    """
    features = extract_window_features(df, lookback, cols)
    if features is None:
        return None
    return features.flatten()


def get_feature_dim(cols: Optional[List[str]] = None, lookback: int = 20) -> int:
    """返回展平后的特征维度 (lookback * n_features)

    训练时样本被展平为 (lookback * n_features,) 向量，
    推理时必须保持相同的维度。

    Args:
        cols: 特征列名列表，默认 FEATURE_COLS (10维)
        lookback: 回看窗口大小，默认20

    Returns:
        展平后的特征向量长度

    示例:
        >>> get_feature_dim()  # 10 * 20 = 200
        200
        >>> get_feature_dim(FEATURE_COLS_EXTENDED, 15)  # 34 * 15 = 510
        510
    """
    cols = cols or FEATURE_COLS
    return len(cols) * lookback


def from_minute_list(minute_data: List[dict]) -> pd.DataFrame:
    """
    将 router.get_minute_for_backtest() 返回的分钟数据转为标准 DataFrame
    返回前强制所有数值列转为 float64，防止字符串/脏值混入后续特征计算
    """
    import numpy as np
    df = pd.DataFrame(minute_data)
    # 统一列名
    if 'close' not in df.columns and 'price' in df.columns:
        df['close'] = df['price']
    for col in ['open', 'high', 'low']:
        if col not in df.columns:
            df[col] = df['close']
    if 'volume' not in df.columns:
        df['volume'] = 0
    # 最终防线: 强制数值列转 float64，object/string 清洗为数字
    for col in ['open', 'high', 'low', 'close', 'volume', 'price', 'change', 'amount']:
        if col in df.columns:
            if df[col].dtype == object or df[col].dtype.kind in ('U', 'S', 'O'):
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return df


def winsorize_features(
    df: pd.DataFrame,
    cols: Optional[List[str]] = None,
    limit: float = 0.05
) -> pd.DataFrame:
    """
    Winsorize 极值截断：将每个特征列超出 [limit, 1-limit] 分位数的值截断
    防止极端值污染模型训练和推理

    Args:
        df: 含特征列的 DataFrame
        cols: 要处理的列，默认 FEATURE_COLS
        limit: 截断比例，默认 5%（即保留 5%~95% 区间）
    Returns:
        截断后的 DataFrame（副本）
    """
    df = df.copy()
    cols = cols or [c for c in FEATURE_COLS if c in df.columns]
    for col in cols:
        if col not in df.columns:
            continue
        if df[col].dtype.kind not in ('f', 'i'):
            continue
        lower = df[col].quantile(limit)
        upper = df[col].quantile(1 - limit)
        if not np.isnan(lower) and not np.isnan(upper):
            df[col] = df[col].clip(lower=lower, upper=upper)
    return df


def compute_feature_ic(
    df: pd.DataFrame,
    cols: Optional[List[str]] = None,
    future_periods: int = 1
) -> Dict[str, float]:
    """
    计算特征与未来收益率的 IC (Information Coefficient) 值
    IC = Pearson 相关系数，衡量单个特征的预测能力

    Args:
        df: 必须含 'close' 列
        cols: 要评估的特征列，默认 FEATURE_COLS
        future_periods: 未来收益率的周期，默认 1（下一期）
    Returns:
        {feature_name: |IC|, ...}，按绝对值排序（越高越好）
    """
    cols = cols or [c for c in FEATURE_COLS if c in df.columns]
    target = df['close'].pct_change(periods=future_periods).shift(-future_periods)

    result = {}
    with np.errstate(divide='ignore', invalid='ignore'):
        for col in cols:
            if col not in df.columns:
                continue
            try:
                col_std = df[col].std()
                target_std = target.std()
                if col_std < 1e-10 or target_std < 1e-10 or pd.isna(col_std) or pd.isna(target_std):
                    result[col] = 0.0
                    continue
                ic = df[col].corr(target)
                result[col] = abs(ic) if not pd.isna(ic) else 0.0
            except Exception:
                result[col] = 0.0

    return dict(sorted(result.items(), key=lambda x: x[1], reverse=True))


def select_top_features(
    df: pd.DataFrame,
    cols: Optional[List[str]] = None,
    n_top: int = 20,
    min_ic: float = 0.01,
    label_col: str = 'label'
) -> List[str]:
    """
    基于 IC 值 + LightGBM importance 双重筛选 Top 特征
    适合训练前预筛，减少过拟合风险

    Args:
        df: 含特征列 + 可选 label 列的 DataFrame
        cols: 候选特征列，默认 FEATURE_COLS
        n_top: 最多保留的特征数（默认 20）
        min_ic: 最低 IC 阈值（默认 0.01），低于此值的直接剔除
        label_col: 标签列名（提供后会用 LGBM 做二轮筛选）
    Returns:
        筛选后的特征名列表
    """
    cols = cols or [c for c in FEATURE_COLS if c in df.columns]

    # ── 第一轮：IC 筛选 ──
    ic_dict = compute_feature_ic(df, cols=cols)
    ic_passed = [f for f, ic in ic_dict.items() if ic >= min_ic]

    if not ic_passed:
        warnings.warn(f"所有特征 IC 均低于 {min_ic}，回退到全部特征")
        ic_passed = cols[:n_top]

    # 如果 IC 已经筛到足够少，直接返回
    if len(ic_passed) <= n_top:
        return ic_passed

    # ── 第二轮：LGBM importance 筛选（需要 label）──
    if label_col not in df.columns:
        return ic_passed[:n_top]

    try:
        import lightgbm as lgb

        valid = df[ic_passed + [label_col]].dropna()
        valid = valid[valid[label_col].isin(['BUY', 'SELL', 'NONE'])]
        if len(valid) < 100:
            return ic_passed[:n_top]

        X = valid[ic_passed].values
        y = valid[label_col].values

        temp_model = lgb.LGBMClassifier(
            n_estimators=100, learning_rate=0.1, num_leaves=15,
            random_state=42, verbose=-1
        )
        temp_model.fit(X, y)

        importance = pd.DataFrame({
            'feature': ic_passed,
            'importance': temp_model.feature_importances_
        }).sort_values('importance', ascending=False)

        selected = importance.head(n_top)['feature'].tolist()
        return selected

    except ImportError:
        return ic_passed[:n_top]


def validate_data_sufficiency(df: pd.DataFrame, freq: str = 'minute') -> dict:
    """
    校验数据是否满足最低标准

    Returns:
        dict: {
            'ok': bool,          # 是否达标
            'bars': int,         # 实际数据条数
            'min_required': int, # 最低要求
            'days': float,       # 约覆盖天数
            'has_market_correction': bool,  # 是否覆盖过 ≥5% 回撤
            'message': str,      # 可读提示
        }
    """
    n = len(df)
    min_req = MINUTE_MIN_BARS if freq == 'minute' else DAILY_MIN_BARS

    # 估算天数
    if freq == 'minute':
        days = n / (240)  # A 股每日 ~240 分钟
    else:
        days = n

    # 是否覆盖过 5%+ 回撤
    if 'close' in df.columns and n > 1:
        peak = df['close'].expanding().max()
        drawdown = (df['close'] - peak) / peak
        has_correction = drawdown.min() <= -0.05
    else:
        has_correction = False

    ok = n >= min_req
    freq_label = '分钟级' if freq == 'minute' else '日频'
    if ok:
        msg = f"✅ {freq_label}数据 {n} 条 (≥{min_req}), 约 {days:.0f} 天"
    else:
        msg = f"⚠️ {freq_label}数据不足: {n} 条, 最低要求 {min_req} 条 (~{min_req/240:.0f} 天)"
    if not has_correction:
        msg += " | ⚠️ 未覆盖 ≥5% 市场回撤"

    return {
        'ok': ok,
        'bars': n,
        'min_required': min_req,
        'days': round(days, 1),
        'has_market_correction': has_correction,
        'message': msg,
    }
