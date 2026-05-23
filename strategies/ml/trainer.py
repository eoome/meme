#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ML 训练器
==========
LightGBM 三分类训练 + 评估 + 保存

流水线: 加载标注数据 → 特征提取 → 训练 → 验证 → 保存
"""

import json
import logging
import os
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── 路径 ───
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_MODEL_DIR = _DATA_DIR / "ml" / "models"
_LABELED_DIR = _DATA_DIR / "labeled"

# 三分类标签
_CLASSES = ['BUY', 'SELL', 'NONE']


def _prepare_dataset(
    lookback: int = 20,
    use_extended_features: bool = False,
    winsorize: bool = True,
) -> Optional[Tuple[np.ndarray, np.ndarray, List[str]]]:
    """
    加载标注数据，提取特征，返回 (X, y, feature_cols)

    Args:
        lookback: 每个样本使用的K线窗口
        use_extended_features: 是否使用扩展特征集
        winsorize: 是否对特征做 Winsorize 截断

    Returns:
        (X, y, feature_cols) 或 None
    """
    from strategies.data.features import (
        calculate_features, prepare_training_sample,
        FEATURE_COLS, FEATURE_COLS_EXTENDED, winsorize_features,
    )

    # 加载所有标注文件
    if not _LABELED_DIR.exists():
        logger.error(f"标注目录不存在: {_LABELED_DIR}")
        return None

    csv_files = list(_LABELED_DIR.glob("*_labeled.csv"))
    if not csv_files:
        logger.error(f"无标注文件: {_LABELED_DIR}/*_labeled.csv")
        return None

    feature_cols = FEATURE_COLS_EXTENDED if use_extended_features else FEATURE_COLS
    all_X = []
    all_y = []
    available_cols = None  # 统一可用特征列，从第一个有效文件确定

    for f in csv_files:
        try:
            df = pd.read_csv(f)
            df.columns = [c.lower().strip() for c in df.columns]

            if 'label' not in df.columns:
                continue

            df = calculate_features(df)

            if winsorize:
                df = winsorize_features(df, cols=[c for c in feature_cols if c in df.columns])

            # 逐行提取展平特征
            # 从第一个有效文件确定可用特征列，后续文件统一使用
            if available_cols is None:
                available_cols = [c for c in feature_cols if c in df.columns]
                if len(available_cols) < len(feature_cols) * 0.5:
                    logger.warning(f"特征列不足: {len(available_cols)}/{len(feature_cols)}")
                    continue
            else:
                # 确保后续文件也有这些列
                missing = [c for c in available_cols if c not in df.columns]
                if missing:
                    logger.debug(f"  {f.name} 缺少 {len(missing)} 个特征列，跳过")
                    continue

            for i in range(lookback, len(df)):
                try:
                    window = df.iloc[i - lookback:i]
                    sample = window[available_cols].values.flatten()
                    # 对象数组防护: 如果包含非数值, 尝试转换并跳过无效值
                    if sample.dtype == object:
                        try:
                            sample = sample.astype(np.float64)
                        except (ValueError, TypeError):
                            continue
                    if np.isnan(sample).any():
                        continue
                    label = df.iloc[i]['label']
                    if label not in _CLASSES:
                        label = 'NONE'
                    all_X.append(sample)
                    all_y.append(label)
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"处理 {f.name} 失败: {e}")

    if not all_X:
        logger.error("无有效训练样本")
        return None

    if available_cols is None:
        logger.error("无可用特征列")
        return None

    X = np.array(all_X)
    y = np.array(all_y)
    logger.info(f"训练集: {len(X)} 样本, 特征维度 {X.shape[1]}, "
                f"标签分布: {dict(zip(*np.unique(y, return_counts=True)))}")
    return X, y, available_cols


def _train_lgb(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    n_estimators: int = 200,
    learning_rate: float = 0.05,
    max_depth: int = 5,
    feature_names: List[str] = None,
) -> Tuple[object, Dict]:
    """训练 LightGBM 模型，返回 (model, metrics)"""
    import lightgbm as lgb
    from sklearn.metrics import f1_score, classification_report

    model = lgb.LGBMClassifier(
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=max_depth,
        num_leaves=31,
        class_weight='balanced',
        random_state=42,
        verbose=-1,
        n_jobs=-1,
    )

    # 用 DataFrame 包装训练/验证数据，保留 feature names（消除 sklearn 警告）
    if feature_names and len(feature_names) == X_train.shape[1]:
        X_train_df = pd.DataFrame(X_train, columns=feature_names)
        X_val_df = pd.DataFrame(X_val, columns=feature_names)
    else:
        X_train_df = X_train
        X_val_df = X_val

    # 早停
    try:
        model.fit(
            X_train_df, y_train,
            eval_set=[(X_val_df, y_val)],
            callbacks=[lgb.early_stopping(20, verbose=False)],
        )
    except TypeError:
        # 旧版 lightgbm 不支持 callbacks
        model.fit(X_train_df, y_train)

    # 验证
    y_pred = model.predict(X_val_df)
    val_f1 = f1_score(y_val, y_pred, average='weighted', zero_division=0)

    report = classification_report(y_val, y_pred, zero_division=0, output_dict=True)

    metrics = {
        'val_f1': float(val_f1),
        'val_report': report,
        'n_train': len(X_train),
        'n_val': len(X_val),
        'n_features': X_train.shape[1],
        'n_estimators_used': model.n_estimators_,
        'best_iteration': getattr(model, 'best_iteration_', model.n_estimators_),
    }

    return model, metrics


def _walk_forward_validate(
    X: np.ndarray,
    y: np.ndarray,
    n_folds: int = 5,
    n_estimators: int = 200,
    learning_rate: float = 0.05,
    max_depth: int = 5,
    feature_names: List[str] = None,
) -> Dict:
    """Walk-forward 时序交叉验证"""
    import lightgbm as lgb
    from sklearn.metrics import f1_score

    fold_size = len(X) // (n_folds + 1)
    if fold_size < 50:
        return {'enabled': False, 'reason': '样本不足'}

    scores = []
    for i in range(n_folds):
        train_end = fold_size * (i + 2)
        val_start = train_end
        val_end = min(val_start + fold_size, len(X))

        if val_end <= val_start:
            break

        X_train, y_train = X[:train_end], y[:train_end]
        X_val, y_val = X[val_start:val_end], y[val_start:val_end]

        # 用 DataFrame 包装以保留 feature names
        if feature_names and len(feature_names) == X_train.shape[1]:
            _cols = feature_names
            X_train_df = pd.DataFrame(X_train, columns=_cols)
            X_val_df = pd.DataFrame(X_val, columns=_cols)
        else:
            X_train_df = X_train
            X_val_df = X_val

        model = lgb.LGBMClassifier(
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            max_depth=max_depth,
            num_leaves=31,
            class_weight='balanced',
            random_state=42,
            verbose=-1,
        )
        try:
            model.fit(
                X_train_df, y_train,
                eval_set=[(X_val_df, y_val)],
                callbacks=[lgb.early_stopping(20, verbose=False)],
            )
        except TypeError:
            model.fit(X_train_df, y_train)

        y_pred = model.predict(X_val_df)
        f1 = f1_score(y_val, y_pred, average='weighted', zero_division=0)
        scores.append(f1)

    if not scores:
        return {'enabled': False, 'reason': '验证失败'}

    return {
        'enabled': True,
        'f1_mean': float(np.mean(scores)),
        'f1_std': float(np.std(scores)),
        'f1_scores': [float(s) for s in scores],
        'n_folds': len(scores),
    }


def train_model(
    lookback: int = 20,
    n_estimators: int = 200,
    learning_rate: float = 0.05,
    max_depth: int = 5,
    use_extended_features: bool = False,
    enable_feature_selection: bool = False,
    n_top_features: int = 20,
    winsorize: bool = True,
    time_series_split: bool = True,
) -> Tuple[Optional[str], Dict]:
    """
    完整训练流水线: 加载数据 → 训练 → 验证 → 保存

    Args:
        lookback: K线窗口
        n_estimators: LightGBM 迭代次数
        learning_rate: 学习率
        max_depth: 最大树深度
        use_extended_features: 是否使用扩展特征集
        enable_feature_selection: 是否启用特征筛选
        n_top_features: 特征筛选保留数
        winsorize: 是否 Winsorize 截断
        time_series_split: 是否使用时序划分验证

    Returns:
        (model_path, metrics) 或 (None, {})
    """
    os.makedirs(_MODEL_DIR, exist_ok=True)

    # 1. 准备数据
    logger.info("📦 加载训练数据...")
    dataset = _prepare_dataset(
        lookback=lookback,
        use_extended_features=use_extended_features,
        winsorize=winsorize,
    )
    if dataset is None:
        return None, {}

    X, y, feature_cols = dataset

    # 2. 特征筛选（可选）
    if enable_feature_selection and len(feature_cols) > n_top_features:
        logger.info(f"🔍 特征筛选: {len(feature_cols)} → {n_top_features}")
        try:
            from strategies.data.features import select_top_features
            # 构造 DataFrame 用于 IC + LGBM 双重筛选
            # X 的列顺序与 feature_cols 对应（展平后的 lookback * n_features）
            # 但 select_top_features 工作在单行特征级别，这里对展平特征做方差+IC筛选
            # 先用方差预筛（去除常量特征），再用 IC 精筛
            variances = np.var(X, axis=0)
            var_mask = variances > 1e-10  # 去除常量特征
            X_filtered = X[:, var_mask]
            filtered_cols = [feature_cols[i] for i in range(len(feature_cols)) if var_mask[i]]

            if len(filtered_cols) > n_top_features:
                # 用 IC 筛选（构造临时 DataFrame，列名用实际特征名重复 lookback 次）
                # 对展平特征逐列计算与 label 的相关性
                from sklearn.preprocessing import LabelEncoder
                le = LabelEncoder()
                y_encoded = le.fit_transform(y)
                ic_scores = []
                for col_idx in range(X_filtered.shape[1]):
                    try:
                        col_data = X_filtered[:, col_idx]
                        if np.std(col_data) < 1e-10:
                            ic_scores.append(0.0)
                        else:
                            ic = abs(np.corrcoef(col_data, y_encoded)[0, 1])
                            ic_scores.append(ic if not np.isnan(ic) else 0.0)
                    except Exception:
                        ic_scores.append(0.0)
                top_idx = np.argsort(ic_scores)[-n_top_features:]
                X = X_filtered[:, top_idx]
                feature_cols = [filtered_cols[i] for i in top_idx]
            else:
                X = X_filtered
                feature_cols = filtered_cols
            logger.info(f"  筛选后特征维度: {X.shape[1]}")
        except Exception as e:
            logger.warning(f"特征筛选失败，使用全部特征: {e}")

    # 3. 时序划分（不用随机划分，避免信息泄漏）
    split = int(len(X) * 0.8)
    X_train, X_val = X[:split], X[split:]
    y_train, y_val = y[:split], y[split:]

    logger.info(f"🧠 训练 LightGBM: {X_train.shape[0]} 训练 / {X_val.shape[0]} 验证")

    # 4. 训练
    model, metrics = _train_lgb(
        X_train, y_train, X_val, y_val,
        n_estimators=n_estimators,
        learning_rate=learning_rate,
        max_depth=max_depth,
        feature_names=feature_cols,
    )

    # 5. Walk-forward 验证（可选）
    if time_series_split and len(X) > 500:
        logger.info("📊 Walk-forward 5折验证...")
        wf = _walk_forward_validate(
            X, y, n_folds=5,
            n_estimators=n_estimators,
            learning_rate=learning_rate,
            max_depth=max_depth,
            feature_names=feature_cols,
        )
        metrics['walk_forward'] = wf
        if wf.get('enabled'):
            logger.info(f"  WF F1: {wf['f1_mean']:.3f} ± {wf['f1_std']:.3f}")
    else:
        metrics['walk_forward'] = {'enabled': False}

    # 6. 保存
    version = datetime.now().strftime("v%Y%m%d_%H%M%S")
    model_path = str(_MODEL_DIR / f"model_{version}.pkl")

    try:
        import joblib
        joblib.dump({
            'model': model,
            'feature_cols': feature_cols,
            'lookback': lookback,
            'version': version,
            'metadata': {
                'train_date_range': _get_date_range(),
                'n_samples': len(X),
                'n_features': len(feature_cols),
                'classes': _CLASSES,
                'trained_at': datetime.now().isoformat(),
                'metrics': {k: v for k, v in metrics.items() if k != 'val_report'},
            },
        }, model_path)
        logger.info(f"✅ 模型已保存: {model_path}")
    except ImportError:
        logger.error("joblib 未安装，无法保存模型")
        return None, metrics

    metrics['model_path'] = model_path
    metrics['version'] = version
    return model_path, metrics


def quick_train() -> Tuple[Optional[str], Dict]:
    """
    快速训练 — 使用默认参数
    适合首次训练或快速验证
    """
    return train_model(
        lookback=20,
        n_estimators=200,
        learning_rate=0.05,
        max_depth=5,
        use_extended_features=False,
        enable_feature_selection=False,
        winsorize=True,
        time_series_split=True,
    )


def _get_date_range() -> str:
    """从标注数据中提取日期范围"""
    try:
        csv_files = list(_LABELED_DIR.glob("*_labeled.csv"))
        if not csv_files:
            return ""
        # 从第一个文件的首尾时间推断
        df = pd.read_csv(csv_files[0])
        for col in ['time', 'datetime', 'timestamp', 'date']:
            if col in df.columns:
                first = str(df[col].iloc[0])[:10]
                last = str(df[col].iloc[-1])[:10]
                return f"{first}~{last}"
    except Exception:
        pass
    return ""
