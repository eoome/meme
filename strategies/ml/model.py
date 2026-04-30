#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 模型封装
============
LightGBM 三分类模型 (BUY / SELL / NONE) + 规则回退

模型文件存储在 data/ml/models/model_v*.pkl
无模型时自动降级为基于规则的回退策略
"""

import logging
import os
import threading
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from utils.numeric import clean_num as _clean_num

logger = logging.getLogger(__name__)

# 彻底屏蔽 sklearn/LightGBM 的 feature names 警告（预测时传入 numpy 是设计意图）
warnings.filterwarnings("ignore", message="X does not have valid feature names.*LGBMClassifier")
warnings.filterwarnings("ignore", message="X has feature names.*")

# ─── 路径 ───
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_MODEL_DIR = _DATA_DIR / "ml" / "models"

# 三分类标签
_CLASSES = ['BUY', 'SELL', 'NONE']


class MLModel:
    """
    LightGBM 三分类模型封装

    - 有模型文件 → 加载 LightGBM 推理
    - 无模型文件 → 规则回退（基于特征的简单评分）
    """

    def __init__(self, model_path: Optional[str] = None):
        """初始化"""
        self._lgb_model = None
        self._feature_cols: List[str] = []
        self._flattened_cols: List[str] = []  # 展平特征名: close_t0, close_t1, ..., volume_t0, ...
        self._lookback: int = 20
        self._version: str = "none"
        self._metadata: Dict = {}

        if model_path and os.path.exists(model_path):
            self._load(model_path)

    # ─── 公开属性 ───

    @property
    def is_ml_model(self) -> bool:
        """判断当前模型是否为ML模型（property）"""
        return self._lgb_model is not None

    @property
    def version(self) -> str:
        """获取模型版本号"""
        return self._version

    # ─── 预测 ───

    def predict(self, sample) -> Dict[str, float]:
        """
        预测 BUY / SELL / NONE 概率 — 含极端数据清洗，防止任何脏值进入 LightGBM

        Args:
            sample: 展平的特征向量 (lookback * n_features,) 或 2D array (lookback, n_features)
                    或 list/array-like

        Returns:
            {"BUY": float, "SELL": float, "NONE": float}  概率之和 = 1
        """
        # 空输入保护
        if sample is None or len(sample) == 0:
            return self._safe_default()

        # ── 快速路径: 已经是干净的 float64 ndarray（回测热循环走这里）──
        if isinstance(sample, np.ndarray) and sample.dtype == np.float64:
            if not np.isnan(sample).any() and not np.isinf(sample).any():
                return self._predict_fast(sample)

        # ── 慢速路径: 需要清洗的任意输入 ──

        # 统一转为 object 数组逐个清洗（兼容 list / tuple / ndarray / pd.Series.values）
        try:
            raw_arr = np.asarray(sample)
            if raw_arr.dtype == object or raw_arr.dtype.kind in ('U', 'S', 'O'):
                has_dirty = any(isinstance(v, str) for v in raw_arr.ravel())
                sample = np.array([_clean_num(v) for v in raw_arr.ravel()], dtype=np.float64)
                if has_dirty:
                    logger.warning(f"predict 检测到脏数据(含字符串)，已清洗: dtype={raw_arr.dtype}, shape={raw_arr.shape}")
            else:
                sample = raw_arr.astype(np.float64)
        except Exception:
            # 兜底：逐个遍历清洗
            try:
                sample = np.array([_clean_num(v) for v in sample], dtype=np.float64)
            except Exception:
                return self._safe_default()

        # NaN / Inf 二次保护
        if np.isnan(sample).any() or np.isinf(sample).any():
            sample = np.where(np.isnan(sample) | np.isinf(sample), 0.0, sample)

        # 维度校验
        expected_dim = len(self._feature_cols) * self._lookback if self._feature_cols else 0
        if expected_dim > 0 and len(sample) != expected_dim:
            logger.debug(
                f"特征维度不匹配: 期望 {expected_dim}, 实际 {len(sample)}, 使用规则回退"
            )
            return self._rule_predict(sample)

        # LightGBM 推理
        if self._lgb_model is not None:
            try:
                X_arr = sample.reshape(1, -1).astype(np.float64)
                # 直接用 numpy 预测（已过滤 sklearn 警告）
                proba = self._lgb_model.predict(X_arr)[0]
                return self._proba_to_dict(proba)
            except Exception as e:
                logger.warning(f"LightGBM 推理失败，降级规则回退: {e}")

        # 规则回退
        return self._rule_predict(sample)

    def _predict_fast(self, sample: np.ndarray) -> Dict[str, float]:
        """
        快速预测路径 — 跳过所有清洗逻辑，直接推理。
        要求: sample 已经是干净的 float64 ndarray，无 NaN/Inf。
        用于回测热循环，避免每根 K 线都做类型检查 + 数据清洗。
        """
        if self._lgb_model is None:
            return self._rule_predict(sample)

        try:
            # ── 关键修复: 训练时样本是 (lookback * n_features,) 展平的，
            #    LightGBM 实际学到的特征数 = lookback * n_features (如 20*10=200)。
            #    预测时必须保持相同的维度，不能 reshape 成 (lookback, n_features)。
            if sample.ndim == 1:
                X_arr = sample.reshape(1, -1)
            elif sample.ndim == 2:
                X_arr = sample.flatten().reshape(1, -1)
            else:
                X_arr = sample.reshape(1, -1)

            # 直接用 numpy 预测（已过滤 sklearn 警告，无需 DataFrame 包装）
            proba = self._lgb_model.predict(X_arr)[0]
            return self._proba_to_dict(proba)
        except Exception:
            return self._rule_predict(sample)

    @staticmethod
    def _proba_to_dict(proba) -> Dict[str, float]:
        """将模型输出的概率数组转为标准字典"""
        if len(proba) >= 3:
            return {
                'BUY': float(proba[0]),
                'SELL': float(proba[1]),
                'NONE': float(proba[2]),
            }
        elif len(proba) == 2:
            return {
                'BUY': float(proba[0]),
                'SELL': 0.0,
                'NONE': float(proba[1]),
            }
        return MLModel._safe_default()

    # ─── 模型信息 ───

    def get_info(self) -> Dict:
        """获取模型元信息"""
        info = {
            'version': self._version,
            'type': 'lightgbm' if self.is_ml_model else 'rule',
            'loaded': self.is_ml_model,
            'feature_cols': list(self._feature_cols),
            'lookback': self._lookback,
            'n_features': len(self._feature_cols),
            'n_classes': 3,
            'metadata': self._metadata,
            'updated_at': '',
            'best_iteration': '—',
            'model_size_kb': 0.0,
        }

        # 从 metadata 中提取训练时间和迭代次数
        if self._metadata:
            info['updated_at'] = self._metadata.get('trained_at', '')
            metrics = self._metadata.get('metrics', {})
            info['best_iteration'] = metrics.get('n_estimators_used', '—')

        # 计算模型文件大小
        if self._lgb_model is not None:
            try:
                import sys
                # 先尝试 booster 内部大小（更准确）
                if hasattr(self._lgb_model, 'booster_'):
                    booster = self._lgb_model.booster_
                    if hasattr(booster, 'model_to_string'):
                        info['model_size_kb'] = len(booster.model_to_string().encode('utf-8')) / 1024.0
                    else:
                        info['model_size_kb'] = sys.getsizeof(self._lgb_model) / 1024.0
                else:
                    info['model_size_kb'] = sys.getsizeof(self._lgb_model) / 1024.0
            except Exception:
                info['model_size_kb'] = 0.0

        # 最佳迭代次数（LightGBM 模型的 best_iteration_ 属性）
        if self._lgb_model is not None and hasattr(self._lgb_model, 'best_iteration_'):
            info['best_iteration'] = self._lgb_model.best_iteration_

        return info

    # ─── 模型加载 ───

    def _load(self, path: str) -> None:
        """从磁盘加载 LightGBM 模型 + 元数据"""
        try:
            import joblib
            data = joblib.load(path)
            if isinstance(data, dict):
                self._lgb_model = data.get('model')
                self._feature_cols = data.get('feature_cols', [])
                self._lookback = data.get('lookback', 20)
                self._version = data.get('version', Path(path).stem)
                self._metadata = data.get('metadata', {})
            else:
                # 纯模型文件（兼容旧格式）
                self._lgb_model = data
                from strategies.data.features import FEATURE_COLS
                self._feature_cols = FEATURE_COLS
                self._lookback = 20
                self._version = Path(path).stem

            # 生成展平后的特征名用于 DataFrame 包装（消除 sklearn 警告）
            # 训练时样本展平为 (lookback * n_features,)，每个特征在 lookback 个时间步上都有值
            self._flattened_cols = []
            if self._feature_cols and self._lookback:
                for col in self._feature_cols:
                    for t in range(self._lookback):
                        self._flattened_cols.append(f"{col}_t{t}")

            if self._lgb_model is not None:
                logger.info(f"✅ ML 模型已加载: {self._version} "
                            f"({len(self._feature_cols)} 特征 × {self._lookback} 步 = {len(self._flattened_cols)} 维)")
            else:
                logger.warning(f"模型文件加载后为空: {path}")
                self._lgb_model = None

        except ImportError:
            logger.warning("joblib 未安装，无法加载模型文件，使用规则回退")
            self._lgb_model = None
        except Exception as e:
            logger.warning(f"模型加载失败: {e}，使用规则回退")
            self._lgb_model = None

    # ─── 规则回退 ───

    def _rule_predict(self, sample: np.ndarray) -> Dict[str, float]:
        """
        规则回退预测 — 基于特征的简单评分

        特征映射（基于 FEATURE_COLS 的顺序）:
          [0]  feat_return        涨跌幅
          [6]  feat_vwap_dev      VWAP偏离度
          [7]  feat_ma_aligned    均线排列
          [4]  feat_volume_ratio  量比
          [14] feat_rsi14         RSI (如果是扩展特征)

        设计: 规则回退是降级模式，信号应果断（多条件一致时 >70%）
        """
        buy_score = 0.0
        sell_score = 0.0

        try:
            n = len(sample)
            if n >= 8:
                ret = float(sample[0])           # 涨跌幅
                vwap_dev = float(sample[6])      # VWAP偏离
                ma_aligned = float(sample[7])    # 均线排列
                vol_ratio = float(sample[4]) if n > 4 else 0.0  # 量比

                # ── 买入评分 ──
                if ret > 0 and vwap_dev > 0:
                    buy_score += 0.35
                if ma_aligned > 0.5:
                    buy_score += 0.25
                if vol_ratio > 1.5:
                    buy_score += 0.15

                # ── 卖出评分 ──
                if ret < 0 and vwap_dev < 0:
                    sell_score += 0.35
                if ma_aligned < 0.5:
                    sell_score += 0.20
                if ret < -0.02:
                    sell_score += 0.25

                # RSI (如果有扩展特征)
                if n > 14:
                    rsi = float(sample[14])
                    if rsi > 0.7:       # 超买
                        sell_score += 0.15
                    elif rsi < 0.3:     # 超卖
                        buy_score += 0.15

        except (IndexError, ValueError, TypeError):
            pass

        # 直接映射: 分数即概率，保留 NONE 基线
        # buy_score 范围 0~0.9, 映射到概率空间
        buy_p = min(0.05 + buy_score, 0.95)
        sell_p = min(0.05 + sell_score, 0.95)
        # 无买卖信号时 NONE 主导
        none_p = max(1.0 - buy_p - sell_p, 0.05)

        total = buy_p + sell_p + none_p
        return {
            'BUY': round(buy_p / total, 4),
            'SELL': round(sell_p / total, 4),
            'NONE': round(none_p / total, 4),
        }

    @staticmethod
    def _safe_default() -> Dict[str, float]:
        """安全的默认概率（无法预测时返回）"""
        return {'BUY': 0.3, 'SELL': 0.3, 'NONE': 0.4}


# ═══════════════════════════════════════════════════════════════
#  全局单例 + 热加载
# ═══════════════════════════════════════════════════════════════

_model_instance: Optional[MLModel] = None
_model_lock = threading.Lock()


def _find_latest_model() -> Optional[str]:
    """查找最新的模型文件"""
    if not _MODEL_DIR.exists():
        return None

    candidates = list(_MODEL_DIR.glob("model_v*.pkl")) + \
                 list(_MODEL_DIR.glob("model_v*.joblib"))
    if not candidates:
        return None

    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def get_model() -> MLModel:
    """获取全局模型单例（线程安全）"""
    global _model_instance
    if _model_instance is None:
        with _model_lock:
            if _model_instance is None:
                model_path = _find_latest_model()
                _model_instance = MLModel(model_path)
    return _model_instance


def reload_model() -> None:
    """重新加载模型（线程安全）— 训练完成后调用"""
    global _model_instance
    with _model_lock:
        model_path = _find_latest_model()
        _model_instance = MLModel(model_path)
        logger.info(f"模型已热加载: {_model_instance.version}")
