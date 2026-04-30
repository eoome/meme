#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 策略引擎
"""

import logging
import threading
import numpy as np
import pandas as pd
from typing import List, Dict, Tuple

from strategies.signal import Signal, SignalType
from strategies.ml.model import get_model
from strategies.data.features import (
    calculate_features, prepare_training_sample, FEATURE_COLS, from_minute_list
)
from strategies.data.chanlun import get_chanlun_signal
from strategies.risk.market_regime import MarketRegimeDetector, MarketRegime
from strategies.risk.position_sizer import create_position_sizer
from utils.exceptions import ModelError
from core.config import get_config

logger = logging.getLogger(__name__)

# 策略配置已迁移到 core/config.py 的 MLConfig，此处保留兼容引用
def _get_ml_config():
    cfg = get_config()
    return {
        "enabled": cfg.ml.enabled,
        "confidence_threshold": cfg.ml.confidence_threshold,
        "strong_threshold": cfg.ml.strong_threshold,
    }


class MLEngine:
    """ML 策略引擎"""

    def __init__(self, config: dict = None):
        """初始化"""
        self.config = config or _get_ml_config()
        self._model = None
        self._model_lock = threading.Lock()
        self._regime_detector = MarketRegimeDetector()
        # 仓位计算器（用于生成信号时附带建议仓位）
        self._position_sizer = create_position_sizer('fixed', position_pct=0.1)

    @property
    def model(self):
        """获取/创建ML模型实例（property）"""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        self._model = get_model()
                    except Exception as e:
                        logger.error(f"模型加载失败: {e}")
                        raise ModelError(f"模型加载失败: {e}", error_type="load_error") from e
        return self._model

    def analyze(self, minute_data=None, code: str = "", name: str = "",
                df: pd.DataFrame = None, prices: List[float] = None,
                total_capital: float = None, current_shares: int = 0,
                cost_price: float = None) -> Signal:
        """
        统一信号生成入口

        Args:
            minute_data: router.get_minute_for_backtest() 返回的分钟数据列表
            df: 已有的 DataFrame (含 open/high/low/close/volume)
            prices: 简单价格列表 (兼容旧接口)
            code: 股票代码
            name: 股票名称
            total_capital: 总资金（用于计算建议仓位股数）
            current_shares: 当前持仓股数（卖出时显示建议卖出量）
            cost_price: 持仓成本价（计算盈亏）
        """
        if not self.config.get("enabled", True):
            return Signal(strategy="ml_trend", signal=SignalType.HOLD, reason="ML策略已禁用")

        try:
            # ═══════════════════════════════════════════════════════
            # 1. 获取模型实际使用的特征列（训练/推理一致性保障）
            # ═══════════════════════════════════════════════════════
            model_info = self.model.get_info()
            actual_feature_cols = model_info.get('feature_cols', FEATURE_COLS)
            actual_lookback = model_info.get('lookback', 20)

            # 降级：如果模型信息中没有特征列，使用默认值
            if not actual_feature_cols:
                actual_feature_cols = FEATURE_COLS

            # ═══════════════════════════════════════════════════════
            # 2. 统一转为 DataFrame
            # ═══════════════════════════════════════════════════════
            if df is not None:
                pass  # 直接用
            elif minute_data:
                if len(minute_data) < actual_lookback:
                    return Signal(
                        strategy="ml_trend", signal=SignalType.HOLD,
                        reason=f"数据不足，需要≥{actual_lookback}根，当前{len(minute_data)}根"
                    )
                df = from_minute_list(minute_data)
            elif prices:
                if len(prices) < actual_lookback:
                    return Signal(strategy="ml_trend", signal=SignalType.HOLD, reason="价格数据不足")
                df = pd.DataFrame({
                    'open': prices, 'high': prices, 'low': prices,
                    'close': prices, 'volume': [1] * len(prices)
                })
            else:
                return Signal(strategy="ml_trend", signal=SignalType.HOLD, reason="无数据输入")

            # ═══════════════════════════════════════════════════════
            # 3. 计算特征（全部30维）+ 使用模型实际特征列提取样本
            # ═══════════════════════════════════════════════════════
            df = calculate_features(df)

            # 最终防线: 确保所有特征列都是数值类型，object/string 清洗为数字
            for col in df.columns:
                if df[col].dtype == object or df[col].dtype.kind in ('U', 'S', 'O'):
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                df[col] = df[col].replace([np.inf, -np.inf], np.nan).fillna(0.0)

            # 过滤掉 DataFrame 中不存在的特征列（兼容性处理）
            available_cols = [c for c in actual_feature_cols if c in df.columns]
            if not available_cols:
                logger.warning(f"模型特征列 {actual_feature_cols} 均不在 DataFrame 中，使用默认特征")
                available_cols = [c for c in FEATURE_COLS if c in df.columns]

            sample = prepare_training_sample(df, lookback=actual_lookback, cols=available_cols)

            if sample is None:
                return Signal(strategy="ml_trend", signal=SignalType.HOLD, reason="特征提取失败")

            # ═══════════════════════════════════════════════════════
            # 4. 预测（模型自动处理维度）
            # ═══════════════════════════════════════════════════════
            proba = self.model.predict(sample)
            buy_p = proba.get('BUY', 0)
            sell_p = proba.get('SELL', 0)
            none_p = proba.get('NONE', 1 - buy_p - sell_p)

            conf = self.config['confidence_threshold']
            strong = self.config['strong_threshold']

            # ═══════════════════════════════════════════════════════
            # 4.5 市场状态自适应 — 动态调整置信度阈值
            # ═══════════════════════════════════════════════════════
            regime_result = self._regime_detector.detect(df)
            regime_params = self._regime_detector.get_risk_params(regime_result.regime)

            # 根据市场状态调整置信度阈值
            # 震荡/高波动 → 提高阈值（减少交易）; 趋势 → 降低阈值（增加交易）
            original_conf = conf
            if regime_result.regime == MarketRegime.RANGING:
                conf = max(conf, regime_params.min_confidence)
            elif regime_result.regime == MarketRegime.VOLATILE:
                conf = max(conf, regime_params.min_confidence)

            # ═══════════════════════════════════════════════════════
            # 5. 缠论信号 — ML 的 "第二意见"，提升信号质量
            # ═══════════════════════════════════════════════════════
            cl_signal = self._get_chanlun_signal(df)
            cl_direction = cl_signal.get('signal', 'HOLD')  # BUY/SELL/HOLD
            cl_confidence = cl_signal.get('confidence', 0)
            cl_reason = cl_signal.get('reason', '')
            cl_buy_point = cl_signal.get('buy_point', 0)   # 0/1/2/3
            cl_sell_point = cl_signal.get('sell_point', 0)  # 0/1/2/3

            # 信号融合: ML + 缠论（含买卖点级别）
            fused_signal, fused_conf, fused_reason = self._fuse_signals(
                ml_buy=buy_p, ml_sell=sell_p,
                ml_conf=conf, ml_strong=strong,
                cl_direction=cl_direction, cl_confidence=cl_confidence,
                cl_reason=cl_reason,
                cl_buy_point=cl_buy_point, cl_sell_point=cl_sell_point,
            )

            details = {
                'model_version': self.model.get_info().get('version', 'unknown'),
                'proba_buy': round(buy_p, 4),
                'proba_sell': round(sell_p, 4),
                'proba_none': round(none_p, 4),
                'chanlun_signal': cl_direction,
                'chanlun_confidence': cl_confidence,
                'chanlun_reason': cl_reason[:50],
                'chanlun_buy_point': cl_buy_point,
                'chanlun_sell_point': cl_sell_point,
                'market_regime': regime_result.regime.value,
                'regime_confidence': round(regime_result.confidence, 2),
                'regime_reason': regime_result.reason[:40],
                'conf_threshold_used': round(conf, 4),
                'conf_threshold_original': round(original_conf, 4),
                'current_price': float(df['close'].iloc[-1]) if df is not None and len(df) > 0 else 0,
            }

            # ═══════════════════════════════════════════════════════
            # 6. 仓位建议 — 基于信号强度 + 市场状态 + 资金计算
            # ═══════════════════════════════════════════════════════
            cur_price = details['current_price']
            if cur_price > 0 and total_capital and total_capital > 0:
                # 根据市场状态缩放仓位比例
                pos_pct = 0.1  # 默认10%
                if regime_result.regime == MarketRegime.TRENDING:
                    pos_pct = 0.12
                elif regime_result.regime == MarketRegime.RANGING:
                    pos_pct = 0.06
                elif regime_result.regime == MarketRegime.VOLATILE:
                    pos_pct = 0.03

                # 强信号加仓
                if fused_signal in (SignalType.STRONG_BUY, SignalType.STRONG_SELL):
                    pos_pct *= 1.3

                # 建议买入股数（100整数倍）
                target_value = total_capital * pos_pct
                suggest_shares = int(target_value / cur_price)
                suggest_shares = (suggest_shares // 100) * 100

                # 建议卖出股数
                suggest_sell = current_shares

                # 持仓盈亏
                pnl_pct = 0
                if cost_price and cost_price > 0 and current_shares > 0:
                    pnl_pct = (cur_price - cost_price) / cost_price

                details['suggest_buy_shares'] = max(suggest_shares, 0)
                details['suggest_sell_shares'] = suggest_sell
                details['suggest_position_pct'] = round(pos_pct, 4)
                details['suggest_position_value'] = round(suggest_shares * cur_price, 2)
                details['current_shares'] = current_shares
                details['cost_price'] = cost_price or 0
                details['pnl_pct'] = round(pnl_pct, 4)
            else:
                details['suggest_buy_shares'] = 0
                details['suggest_sell_shares'] = 0
                details['suggest_position_pct'] = 0
                details['suggest_position_value'] = 0
                details['current_shares'] = current_shares
                details['cost_price'] = cost_price or 0
                details['pnl_pct'] = 0

            return Signal(
                strategy="ml_trend", signal=fused_signal,
                reason=fused_reason,
                confidence=min(fused_conf, 99), details=details
            )

        except ModelError:
            raise
        except Exception as e:
            logger.error(f"ML analyze error: {e}")
            return Signal(
                strategy="ml_trend", signal=SignalType.HOLD,
                reason=f"ML异常: {str(e)[:50]}"
            )

    # ─── 缠论信号集成 ───

    def _get_chanlun_signal(self, df: pd.DataFrame) -> Dict:
        """获取缠论独立信号（异常安全）"""
        try:
            return get_chanlun_signal(df)
        except Exception as e:
            logger.debug(f"缠论信号获取失败: {e}")
            return {'signal': 'HOLD', 'confidence': 0, 'reason': ''}

    def _fuse_signals(self, ml_buy: float, ml_sell: float,
                      ml_conf: float, ml_strong: float,
                      cl_direction: str, cl_confidence: float,
                      cl_reason: str,
                      cl_buy_point: int = 0, cl_sell_point: int = 0) -> Tuple[SignalType, float, str]:
        """
        融合 ML 和缠论信号

        融合规则:
        - 方向一致: 置信度 +15%，可能升级为 STRONG
        - 方向相反: 置信度 -20%，可能降级为 HOLD
        - ML=HOLD 但缠论明确: 缠论作为补充信号（置信度打折）
        - 买卖点级别加成: 一买/一卖 +20%, 二买/二卖 +12%, 三买/三卖 +8%
        """
        # 缠论买卖点级别加成
        bp_bonus = {1: 20, 2: 12, 3: 8}.get(cl_buy_point, 0)
        sp_bonus = {1: 20, 2: 12, 3: 8}.get(cl_sell_point, 0)

        # 先确定 ML 原始信号
        if ml_buy > ml_conf and ml_buy > ml_sell:
            ml_sig = SignalType.STRONG_BUY if ml_buy > ml_strong else SignalType.BUY
            ml_base_conf = ml_buy * 100
        elif ml_sell > ml_conf and ml_sell > ml_buy:
            ml_sig = SignalType.STRONG_SELL if ml_sell > ml_strong else SignalType.SELL
            ml_base_conf = ml_sell * 100
        else:
            ml_sig = SignalType.HOLD
            ml_base_conf = 50.0

        # 缠论方向映射
        cl_is_buy = cl_direction == 'BUY'
        cl_is_sell = cl_direction == 'SELL'

        # 融合逻辑
        if ml_sig in (SignalType.BUY, SignalType.STRONG_BUY):
            if cl_is_buy:
                # ML买 + 缠论买 = 共振增强
                fused_conf = ml_base_conf + 15 + bp_bonus
                fused_sig = (SignalType.STRONG_BUY
                            if fused_conf >= ml_strong * 100 else SignalType.BUY)
                bp_str = f" 一买" if cl_buy_point == 1 else (f" 二买" if cl_buy_point == 2 else (f" 三买" if cl_buy_point == 3 else ""))
                reason = f"ML+缠论共振买入{bp_str} (ML:{ml_buy:.0%}, 缠论:{cl_confidence:.0f}%)"
            elif cl_is_sell:
                # ML买 + 缠论卖 = 分歧，强降级为 HOLD
                fused_conf = ml_base_conf * 0.5
                fused_sig = SignalType.HOLD
                reason = f"ML买/缠论卖分歧→观望 (ML:{ml_buy:.0%}, 缠论:{cl_confidence:.0f}%)"
            else:
                # 缠论观望，ML主导
                fused_conf = ml_base_conf
                fused_sig = ml_sig
                reason = f"ML预测买入 {ml_buy:.1%} ({self.model.version})"

        elif ml_sig in (SignalType.SELL, SignalType.STRONG_SELL):
            if cl_is_sell:
                # ML卖 + 缠论卖 = 共振增强
                fused_conf = ml_base_conf + 15 + sp_bonus
                fused_sig = (SignalType.STRONG_SELL
                            if fused_conf >= ml_strong * 100 else SignalType.SELL)
                sp_str = f" 一卖" if cl_sell_point == 1 else (f" 二卖" if cl_sell_point == 2 else (f" 三卖" if cl_sell_point == 3 else ""))
                reason = f"ML+缠论共振卖出{sp_str} (ML:{ml_sell:.0%}, 缠论:{cl_confidence:.0f}%)"
            elif cl_is_buy:
                # ML卖 + 缠论买 = 分歧，强降级为 HOLD
                fused_conf = ml_base_conf * 0.5
                fused_sig = SignalType.HOLD
                reason = f"ML卖/缠论买分歧→观望 (ML:{ml_sell:.0%}, 缠论:{cl_confidence:.0f}%)"
            else:
                # 缠论观望，ML主导
                fused_conf = ml_base_conf
                fused_sig = ml_sig
                reason = f"ML预测卖出 {ml_sell:.1%} ({self.model.version})"

        else:
            # ML 观望，看缠论是否有明确信号
            if cl_is_buy and cl_confidence >= 70:
                fused_conf = cl_confidence * 0.8 + bp_bonus * 0.5
                fused_sig = SignalType.BUY
                bp_str = f" 一买" if cl_buy_point == 1 else (f" 二买" if cl_buy_point == 2 else (f" 三买" if cl_buy_point == 3 else ""))
                reason = f"缠论补充买入{bp_str} ({cl_reason[:30]})"
            elif cl_is_sell and cl_confidence >= 70:
                fused_conf = cl_confidence * 0.8 + sp_bonus * 0.5
                fused_sig = SignalType.SELL
                sp_str = f" 一卖" if cl_sell_point == 1 else (f" 二卖" if cl_sell_point == 2 else (f" 三卖" if cl_sell_point == 3 else ""))
                reason = f"缠论补充卖出{sp_str} ({cl_reason[:30]})"
            else:
                fused_conf = 50.0
                fused_sig = SignalType.HOLD
                reason = f"ML观望 (买{ml_buy:.0%}/卖{ml_sell:.0%})"

        # 边界保护
        fused_conf = max(10.0, min(fused_conf, 99.0))

        return fused_sig, fused_conf, reason

    def get_model_info(self) -> Dict:
        """获取当前模型信息"""
        return self.model.get_info()

    def get_health_status(self) -> Dict:
        """获取模型健康状态（供 UI 显示）"""
        info = self.model.get_info()
        is_ml = self.model.is_ml_model
        return {
            "status": "ml" if is_ml else "rule_fallback",
            "version": info.get("version", "unknown"),
            "model_type": info.get("type", "unknown"),
            "feature_dim": len(info.get("feature_cols", [])),
            "healthy": is_ml,
            "message": (
                f"ML 模型 v{info.get('version', '?')} 已加载"
                if is_ml
                else "⚠️ ML 模型未加载，使用规则回退策略"
            ),
        }

    def reload_model(self):
        """重新加载模型（线程安全）"""
        with self._model_lock:
            self._model = None
