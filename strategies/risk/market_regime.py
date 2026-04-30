#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场状态识别器
==============
根据波动率、趋势强度、成交量等指标判断当前市场状态，
为风控和仓位管理提供自适应参数。

三种状态:
- trending: 趋势行情 → 跟踪止盈宽松、止损放宽
- ranging:  震荡行情 → 止盈收紧、止损收紧、减少仓位
- volatile: 高波动   → 大幅减仓、止损收紧
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, List


class MarketRegime(Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"


@dataclass
class RegimeResult:
    """市场状态结果"""
    regime: MarketRegime
    confidence: float        # 0~1, 状态判断置信度
    trend_strength: float    # 趋势强度 0~1
    volatility_level: float  # 波动率水平 0~1
    adx: float              # ADX 值
    atr_pct: float          # ATR/价格 百分比
    reason: str


@dataclass
class RiskParams:
    """基于市场状态的风控参数"""
    position_scale: float    # 仓位缩放系数 (0.3~1.0)
    stop_loss_pct: float     # 止损百分比
    take_profit_pct: float   # 止盈百分比
    trailing_pct: float      # 跟踪止盈回撤比例
    max_trades_per_day: int  # 每日最大交易次数
    min_confidence: float    # 最低信号置信度


# ── 各状态下的默认风控参数 ──
REGIME_RISK_PARAMS: Dict[MarketRegime, RiskParams] = {
    MarketRegime.TRENDING: RiskParams(
        position_scale=1.0,
        stop_loss_pct=0.04,
        take_profit_pct=0.08,
        trailing_pct=0.025,
        max_trades_per_day=8,
        min_confidence=0.60,
    ),
    MarketRegime.RANGING: RiskParams(
        position_scale=0.6,
        stop_loss_pct=0.025,
        take_profit_pct=0.03,
        trailing_pct=0.015,
        max_trades_per_day=4,
        min_confidence=0.70,
    ),
    MarketRegime.VOLATILE: RiskParams(
        position_scale=0.3,
        stop_loss_pct=0.02,
        take_profit_pct=0.05,
        trailing_pct=0.01,
        max_trades_per_day=2,
        min_confidence=0.80,
    ),
}


class MarketRegimeDetector:
    """
    市场状态检测器

    综合 ADX、ATR、波动率分位数、价格趋势斜率判断当前状态。
    使用滚动窗口，避免单点判断的噪声。
    """

    def __init__(
        self,
        adx_period: int = 14,
        atr_period: int = 14,
        lookback: int = 60,
        vol_high_quantile: float = 0.75,
        vol_low_quantile: float = 0.25,
        adx_trend_threshold: float = 25,
        adx_ranging_threshold: float = 20,
    ):
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.lookback = lookback
        self.vol_high_quantile = vol_high_quantile
        self.vol_low_quantile = vol_low_quantile
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_ranging_threshold = adx_ranging_threshold

    def detect(self, df: pd.DataFrame) -> RegimeResult:
        """
        检测当前市场状态

        Args:
            df: DataFrame with columns [open, high, low, close, volume]
                至少需要 lookback + max(adx_period, atr_period) 条数据

        Returns:
            RegimeResult
        """
        df = df.copy()
        df.columns = [c.lower().strip() for c in df.columns]

        min_bars = self.lookback + max(self.adx_period, self.atr_period) + 5
        if len(df) < min_bars:
            return self._default_result()

        try:
            adx = self._calc_adx(df)
            atr_pct = self._calc_atr_pct(df)
            vol_regime = self._calc_vol_regime(df)
            trend_slope = self._calc_trend_slope(df)

            # 取最近值
            cur_adx = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 20.0
            cur_atr_pct = float(atr_pct.iloc[-1]) if not np.isnan(atr_pct.iloc[-1]) else 0.02
            cur_vol = float(vol_regime) if not np.isnan(vol_regime) else 0.5
            cur_slope = float(trend_slope) if not np.isnan(trend_slope) else 0.0

            # 判断状态
            regime, confidence, reason = self._classify(
                cur_adx, cur_atr_pct, cur_vol, cur_slope
            )

            return RegimeResult(
                regime=regime,
                confidence=confidence,
                trend_strength=min(abs(cur_slope) * 10, 1.0),
                volatility_level=cur_vol,
                adx=cur_adx,
                atr_pct=cur_atr_pct,
                reason=reason,
            )
        except Exception as e:
            return self._default_result()

    def get_risk_params(self, regime: MarketRegime) -> RiskParams:
        """获取指定市场状态下的风控参数"""
        return REGIME_RISK_PARAMS.get(regime, REGIME_RISK_PARAMS[MarketRegime.RANGING])

    def _classify(self, adx: float, atr_pct: float, vol_regime: float,
                  trend_slope: float) -> tuple:
        """
        分类逻辑:
        1. 高波动优先: ATR/价格 > 3% 或 波动率分位 > 0.75 → VOLATILE
        2. 趋势行情: ADX > 25 且趋势斜率明显 → TRENDING
        3. 其余: RANGING
        """
        reasons = []

        # 高波动检测
        if atr_pct > 0.03 or vol_regime > 0.75:
            reasons.append(f"ATR={atr_pct:.2%}" if atr_pct > 0.03 else f"波动率P{vol_regime:.0%}")
            confidence = min(0.5 + vol_regime, 0.95)
            return MarketRegime.VOLATILE, confidence, "高波动: " + ", ".join(reasons)

        # 趋势检测
        if adx > self.adx_trend_threshold and abs(trend_slope) > 0.001:
            reasons.append(f"ADX={adx:.0f}")
            reasons.append(f"斜率={trend_slope:+.4f}")
            confidence = min(0.5 + (adx - 20) / 30, 0.95)
            return MarketRegime.TRENDING, confidence, "趋势行情: " + ", ".join(reasons)

        # 震荡
        if adx < self.adx_ranging_threshold:
            reasons.append(f"ADX={adx:.0f}<{self.adx_ranging_threshold}")
            confidence = min(0.5 + (self.adx_ranging_threshold - adx) / 20, 0.9)
            return MarketRegime.RANGING, confidence, "震荡行情: " + ", ".join(reasons)

        # 不确定 → 偏震荡
        return MarketRegime.RANGING, 0.5, f"不确定 (ADX={adx:.0f})"

    def _calc_adx(self, df: pd.DataFrame) -> pd.Series:
        """计算 ADX (Average Directional Index)"""
        high = df['high']
        low = df['low']
        close = df['close']

        plus_dm = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)

        # 只保留较大方向
        plus_dm[plus_dm < minus_dm] = 0
        minus_dm[minus_dm < plus_dm] = 0

        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        atr = tr.rolling(self.adx_period, min_periods=1).mean()
        plus_di = 100 * (plus_dm.rolling(self.adx_period, min_periods=1).mean() / (atr + 1e-8))
        minus_di = 100 * (minus_dm.rolling(self.adx_period, min_periods=1).mean() / (atr + 1e-8))

        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-8))
        adx = dx.rolling(self.adx_period, min_periods=1).mean()
        return adx

    def _calc_atr_pct(self, df: pd.DataFrame) -> pd.Series:
        """ATR/价格 百分比"""
        high = df['high']
        low = df['low']
        close = df['close']
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_period, min_periods=1).mean()
        return atr / (close + 1e-8)

    def _calc_vol_regime(self, df: pd.DataFrame) -> float:
        """当前波动率在历史中的分位数 (0~1)"""
        close = df['close']
        log_ret = np.log(close / close.shift(1)).dropna()
        if len(log_ret) < self.lookback:
            return 0.5
        recent_vol = log_ret.iloc[-20:].std()
        hist_vols = log_ret.rolling(20, min_periods=10).std().dropna()
        if len(hist_vols) < 10:
            return 0.5
        quantile = (hist_vols < recent_vol).mean()
        return float(quantile)

    def _calc_trend_slope(self, df: pd.DataFrame) -> float:
        """价格趋势斜率（线性回归，归一化）"""
        close = df['close'].iloc[-self.lookback:]
        if len(close) < 10:
            return 0.0
        x = np.arange(len(close))
        slope = np.polyfit(x, close.values, 1)[0]
        return slope / (close.mean() + 1e-8)

    def _default_result(self) -> RegimeResult:
        return RegimeResult(
            regime=MarketRegime.RANGING, confidence=0.3,
            trend_strength=0, volatility_level=0.5,
            adx=0, atr_pct=0, reason="数据不足，默认震荡",
        )


# ── 便捷函数 ──

def detect_market_regime(df: pd.DataFrame) -> RegimeResult:
    """便捷函数: 检测市场状态"""
    return MarketRegimeDetector().detect(df)


def get_regime_risk_params(df: pd.DataFrame) -> tuple:
    """便捷函数: 检测状态 + 获取风控参数"""
    detector = MarketRegimeDetector()
    result = detector.detect(df)
    params = detector.get_risk_params(result.regime)
    return result, params
