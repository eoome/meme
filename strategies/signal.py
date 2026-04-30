#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一信号格式
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Optional, List


class SignalType(Enum):
    """SignalType配置类"""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"

    @property
    def label(self) -> str:
        """获取信号标签文本"""
        return {
            "STRONG_BUY": "🟢 强买入",
            "BUY": "🟢 买入",
            "HOLD": "⚪ 观望",
            "SELL": "🔴 卖出",
            "STRONG_SELL": "🔴 强卖出",
        }[self.value]

    @property
    def color(self) -> str:
        """获取信号对应颜色 — BUY绿/SELL红/NONE灰"""
        return {
            "STRONG_BUY": "#16a34a",
            "BUY": "#22c55e",
            "HOLD": "#94a3b8",
            "SELL": "#ef4444",
            "STRONG_SELL": "#dc2626",
        }[self.value]

    @property
    def is_buy(self) -> bool:
        """判断是否为买入信号"""
        return self in (SignalType.BUY, SignalType.STRONG_BUY)

    @property
    def is_sell(self) -> bool:
        """判断是否为卖出信号"""
        return self in (SignalType.SELL, SignalType.STRONG_SELL)


@dataclass
class Signal:
    """交易信号"""
    strategy: str = "ml_trend"
    signal: SignalType = SignalType.HOLD
    reason: str = ""
    confidence: float = 50.0
    details: Dict = field(default_factory=dict)

    def get_explanation(self) -> Dict:
        """
        将信号详情翻译成结构化的人类可读解释

        Returns:
            {
                'summary': '一句话结论',
                'factors': [{'icon': '🧠', 'label': 'ML模型', 'value': '...', 'direction': 'buy/sell/neutral'}],
                'risk_notes': ['风控相关的说明'],
                'raw_details': {原始details}
            }
        """
        d = self.details
        factors: List[Dict] = []
        risk_notes: List[str] = []

        # ── 1. ML 模型预测 ──
        proba_buy = d.get('proba_buy', 0)
        proba_sell = d.get('proba_sell', 0)
        proba_none = d.get('proba_none', 0)
        model_ver = d.get('model_version', '?')

        if proba_buy > 0 or proba_sell > 0:
            ml_dir = 'buy' if proba_buy > proba_sell else ('sell' if proba_sell > proba_buy else 'neutral')
            ml_label = f"买{proba_buy:.0%} / 卖{proba_sell:.0%} / 观{proba_none:.0%}"
            factors.append({
                'icon': '🧠',
                'label': f'ML模型 v{model_ver}',
                'value': ml_label,
                'direction': ml_dir,
            })

        # ── 2. 缠论信号 ──
        cl_signal = d.get('chanlun_signal', 'HOLD')
        cl_conf = d.get('chanlun_confidence', 0)
        cl_reason = d.get('chanlun_reason', '')
        cl_bp = d.get('chanlun_buy_point', 0)
        cl_sp = d.get('chanlun_sell_point', 0)

        if cl_signal != 'HOLD' or cl_reason:
            cl_dir = 'buy' if cl_signal == 'BUY' else ('sell' if cl_signal == 'SELL' else 'neutral')
            # 买卖点级别
            bp_label = {1: '一买(背驰反转)', 2: '二买(回调不破低)', 3: '三买(突破回踩)'}.get(cl_bp, '')
            sp_label = {1: '一卖(背驰反转)', 2: '二卖(反弹不破高)', 3: '三卖(跌破反抽)'}.get(cl_sp, '')
            point_label = bp_label or sp_label or ''
            cl_desc = f"{cl_signal} {cl_conf:.0f}%"
            if point_label:
                cl_desc += f" · {point_label}"
            factors.append({
                'icon': '📐',
                'label': '缠论分析',
                'value': cl_desc,
                'direction': cl_dir,
            })

        # ── 3. 市场状态 ──
        regime = d.get('market_regime', '')
        regime_conf = d.get('regime_confidence', 0)
        regime_reason = d.get('regime_reason', '')

        if regime:
            regime_map = {
                'trending': ('📈 趋势行情', 'buy'),
                'ranging': ('📊 震荡行情', 'neutral'),
                'volatile': ('⚡ 高波动', 'sell'),
            }
            regime_label, regime_dir = regime_map.get(regime, (regime, 'neutral'))
            factors.append({
                'icon': '🌡️',
                'label': '市场状态',
                'value': f"{regime_label} (置信{regime_conf:.0%}) {regime_reason}",
                'direction': regime_dir,
            })

        # ── 4. 置信度阈值调整 ──
        conf_used = d.get('conf_threshold_used', 0)
        conf_orig = d.get('conf_threshold_original', 0)
        if conf_used and conf_orig and abs(conf_used - conf_orig) > 0.01:
            if conf_used > conf_orig:
                risk_notes.append(
                    f"⚠️ 市场状态不佳，置信度阈值从 {conf_orig:.0%} 提高到 {conf_used:.0%}（更严格过滤）"
                )
            else:
                risk_notes.append(
                    f"✅ 趋势行情，置信度阈值从 {conf_orig:.0%} 降低到 {conf_used:.0%}（更积极入场）"
                )

        # ── 5. 信号融合说明 ──
        if cl_signal != 'HOLD' and proba_buy > 0:
            if (cl_signal == 'BUY' and proba_buy > proba_sell) or \
               (cl_signal == 'SELL' and proba_sell > proba_buy):
                risk_notes.append("✅ ML与缠论方向一致，信号共振增强")
            elif (cl_signal == 'BUY' and proba_sell > proba_buy) or \
                 (cl_signal == 'SELL' and proba_buy > proba_sell):
                risk_notes.append("⚠️ ML与缠论方向矛盾，已降级为观望")

        # ── 6. 仓位建议 ──
        suggest_buy = d.get('suggest_buy_shares', 0)
        suggest_sell = d.get('suggest_sell_shares', 0)
        pos_pct = d.get('suggest_position_pct', 0)
        pos_value = d.get('suggest_position_value', 0)
        cur_shares = d.get('current_shares', 0)
        cost = d.get('cost_price', 0)
        pnl = d.get('pnl_pct', 0)

        if self.signal.is_buy and suggest_buy > 0:
            factors.append({
                'icon': '💰',
                'label': '建议买入',
                'value': f"{suggest_buy}股 (约¥{pos_value:,.0f}, 仓位{pos_pct:.0%})",
                'direction': 'buy',
            })
            if cur_shares > 0:
                factors.append({
                    'icon': '📦',
                    'label': '当前持仓',
                    'value': f"{cur_shares}股",
                    'direction': 'neutral',
                })

        elif self.signal.is_sell and suggest_sell > 0:
            sell_label = f"{suggest_sell}股"
            if cost > 0:
                pnl_sign = "+" if pnl >= 0 else ""
                sell_label += f" (成本¥{cost:.3f}, 盈亏{pnl_sign}{pnl:.2%})"
            factors.append({
                'icon': '💰',
                'label': '建议卖出',
                'value': sell_label,
                'direction': 'sell',
            })

        # ── 7. 生成总结 ──
        cur_price = d.get('current_price', 0)
        price_hint = f" @ ¥{cur_price:.3f}" if cur_price > 0 else ""

        if self.signal.is_buy:
            qty_hint = f" {suggest_buy}股" if suggest_buy > 0 else ""
            if self.signal == SignalType.STRONG_BUY:
                summary = f"强烈推荐买入{qty_hint} (置信度 {self.confidence:.0f}%){price_hint}"
            else:
                summary = f"推荐买入{qty_hint} (置信度 {self.confidence:.0f}%){price_hint}"
        elif self.signal.is_sell:
            qty_hint = f" {suggest_sell}股" if suggest_sell > 0 else ""
            if self.signal == SignalType.STRONG_SELL:
                summary = f"强烈建议卖出{qty_hint} (置信度 {self.confidence:.0f}%){price_hint}"
            else:
                summary = f"建议卖出{qty_hint} (置信度 {self.confidence:.0f}%){price_hint}"
        else:
            summary = f"建议观望 (置信度 {self.confidence:.0f}%)"

        return {
            'summary': summary,
            'factors': factors,
            'risk_notes': risk_notes,
            'reason': self.reason,
            'raw_details': d,
        }
