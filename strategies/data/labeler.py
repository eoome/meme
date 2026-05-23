#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动标注：从K线中识别最优买卖点

⚠️ 前瞻偏差警告 (Look-ahead Bias):
    标注器使用未来数据（future_window）标注当前K线。这在训练时是正确的
    （用已知未来标注历史数据），但如果标注结果用于实时信号或回测中的
    "已标注"判断，就会产生前瞻偏差。

    使用限制:
    - ✅ 训练集标注（已知历史，标注更晚的历史）
    - ❌ 实时信号生成（不能用未来信息标注当前）
    - ❌ 回测中的标签判断（不能用未来信息）

改进:
- BUY 标注增加入场后最大回撤约束，避免「先跌10%再涨1%」的虚假信号
- SELL 标注同理
"""

import os
import json
import logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


class LookaheadBiasError(RuntimeError):
    """前瞻偏差错误 — 当标注器被错误用于实时/未来数据时抛出"""
    pass


def _detect_future_data(df: pd.DataFrame, tolerance_hours: int = 1) -> bool:
    """
    检测 DataFrame 是否包含未来数据
    
    通过检查最后一条数据的时间是否在未来（加上容差）来判断
    额外防护：对无日期的时间列（如 "09:30"），检查数据行数是否合理
    """
    now = datetime.now()
    cutoff = now + timedelta(hours=tolerance_hours)
    
    for col in ['time', 'datetime', 'timestamp', 'date']:
        if col in df.columns:
            try:
                last_val = df[col].iloc[-1]
                last_ts = pd.to_datetime(last_val, errors='coerce')
                if pd.notna(last_ts) and last_ts > cutoff:
                    return True
                # 对无日期的时间列（只有 HH:MM），pd.to_datetime 会补上今天日期
                # 如果原始值不含日期，额外检查是否真的是今天的数据
                if pd.notna(last_ts) and isinstance(last_val, str):
                    if len(last_val.strip()) <= 5 and ':' in last_val:
                        # 格式如 "09:30"，可能是跨日旧数据
                        # 检查数据量：分钟数据每天最多240条
                        n_rows = len(df)
                        if n_rows > 240 * 2:  # 超过2天的分钟数据，但无日期信息
                            return True  # 可疑数据
            except Exception:
                continue
    return False


class KlineLabeler:
    """
    自动标注算法：
    1. 识别局部极值点（低点/高点）
    2. 配对买卖：低点后能找到足够收益的高点
    3. 入场后最大回撤不超过阈值
    4. 过滤噪声和重复信号

    参数优先级: 构造函数参数 > core/config 配置 > 默认值
    """

    def __init__(
        self,
        lookback: int = None,
        min_return: float = None,
        max_hold_bars: int = None,
        min_spacing: int = None,
        noise_filter: float = None,
        max_drawdown: float = None,
    ):
        """初始化 — 参数优先级: 构造函数参数 > core/config 配置 > 默认值"""
        # 从配置读取默认值，构造函数参数可覆盖
        try:
            from core.config import get_config
            cfg = get_config()
            labeler_cfg = getattr(cfg, 'label', None)
        except Exception:
            labeler_cfg = None

        defaults = {
            'lookback': 3,
            'min_return': 0.008,
            'max_hold_bars': 20,
            'min_spacing': 5,
            'noise_filter': 0.003,
            'max_drawdown': 0.03,
        }

        self.lookback = lookback if lookback is not None else (
            getattr(labeler_cfg, 'lookback', defaults['lookback']) if labeler_cfg else defaults['lookback']
        )
        self.min_return = min_return if min_return is not None else (
            getattr(labeler_cfg, 'min_return', defaults['min_return']) if labeler_cfg else defaults['min_return']
        )
        self.max_hold_bars = max_hold_bars if max_hold_bars is not None else (
            getattr(labeler_cfg, 'max_hold_bars', defaults['max_hold_bars']) if labeler_cfg else defaults['max_hold_bars']
        )
        self.min_spacing = min_spacing if min_spacing is not None else (
            getattr(labeler_cfg, 'min_spacing', defaults['min_spacing']) if labeler_cfg else defaults['min_spacing']
        )
        self.noise_filter = noise_filter if noise_filter is not None else (
            getattr(labeler_cfg, 'noise_filter', defaults['noise_filter']) if labeler_cfg else defaults['noise_filter']
        )
        self.max_drawdown = max_drawdown if max_drawdown is not None else (
            getattr(labeler_cfg, 'max_drawdown', defaults['max_drawdown']) if labeler_cfg else defaults['max_drawdown']
        )

    def find_local_extremes(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
        """找局部低点和高点"""
        low_mask = pd.Series(True, index=df.index)
        for i in range(1, self.lookback + 1):
            low_mask &= (df['low'] < df['low'].shift(i))
            low_mask &= (df['low'] < df['low'].shift(-i))

        high_mask = pd.Series(True, index=df.index)
        for i in range(1, self.lookback + 1):
            high_mask &= (df['high'] > df['high'].shift(i))
            high_mask &= (df['high'] > df['high'].shift(-i))

        price_range = df['high'] - df['low']
        avg_range = price_range.rolling(self.lookback * 2, min_periods=1).mean()
        meaningful = price_range > avg_range * 0.5

        return low_mask & meaningful, high_mask & meaningful

    def label(self, df: pd.DataFrame) -> pd.DataFrame:
        """主标注流程（含前瞻偏差运行时防护）"""
        # ═══════════════════════════════════════════════════
        # 前瞻偏差防护: 禁止标注包含未来时间的K线数据
        # ═══════════════════════════════════════════════════
        if _detect_future_data(df):
            raise LookaheadBiasError(
                "标注器检测到未来时间戳数据！标注器仅允许用于历史训练数据，"
                "禁止用于实时信号生成或回测中的标签判断。"
            )
        
        df = df.copy().reset_index(drop=True)
        df['label'] = 'NONE'
        df['label_price'] = np.nan
        df['target_price'] = np.nan
        df['max_return'] = np.nan

        lows, highs = self.find_local_extremes(df)
        low_indices = df[lows].index.tolist()
        high_indices = df[highs].index.tolist()

        # 标注 BUY
        used_highs = set()
        for i in low_indices:
            entry_price = df.loc[i, 'close']
            future_window = df.loc[i + 1: i + self.max_hold_bars]
            if len(future_window) < 3:
                continue

            # 检查入场后最大回撤 — 关键改进
            future_low = future_window['low'].min()
            entry_drawdown = (entry_price - future_low) / (entry_price + 1e-8)
            if entry_drawdown > self.max_drawdown:
                # 先跌太多，不算有效 BUY 信号
                continue

            future_high = future_window['high'].max()
            max_return = (future_high - entry_price) / (entry_price + 1e-8)
            if max_return > self.min_return:
                for j in future_window.index:
                    ret = (future_window.loc[j, 'high'] - entry_price) / (entry_price + 1e-8)
                    if ret > self.min_return and j not in used_highs:
                        df.loc[i, 'label'] = 'BUY'
                        df.loc[i, 'label_price'] = entry_price
                        df.loc[i, 'target_price'] = future_window.loc[j, 'high']
                        df.loc[i, 'max_return'] = max_return
                        used_highs.add(j)
                        break

        # 标注 SELL
        used_lows = set()
        for i in high_indices:
            entry_price = df.loc[i, 'close']
            future_window = df.loc[i + 1: i + self.max_hold_bars]
            if len(future_window) < 3:
                continue

            # 检查入场后最大反弹 — 同理
            future_high = future_window['high'].max()
            entry_rebound = (future_high - entry_price) / (entry_price + 1e-8)
            if entry_rebound > self.max_drawdown:
                # 先涨太多，不算有效 SELL 信号
                continue

            future_low = future_window['low'].min()
            max_drop = (entry_price - future_low) / (entry_price + 1e-8)
            if max_drop > self.min_return:
                for j in future_window.index:
                    drop = (entry_price - future_window.loc[j, 'low']) / (entry_price + 1e-8)
                    if drop > self.min_return and j not in used_lows:
                        df.loc[i, 'label'] = 'SELL'
                        df.loc[i, 'label_price'] = entry_price
                        df.loc[i, 'target_price'] = future_window.loc[j, 'low']
                        df.loc[i, 'max_return'] = max_drop
                        used_lows.add(j)
                        break

        df = self._filter_spacing(df)
        return df

    def _filter_spacing(self, df: pd.DataFrame) -> pd.DataFrame:
        """过滤间距过近的信号，保留更极端的"""
        for label in ['BUY', 'SELL']:
            mask = df['label'] == label
            indices = df[mask].index.tolist()
            if len(indices) <= 1:
                continue
            keep = []
            last_kept = -self.min_spacing - 1
            for i in indices:
                if i - last_kept >= self.min_spacing:
                    keep.append(i)
                    last_kept = i
                else:
                    last_idx = keep[-1]
                    if label == 'BUY' and df.loc[i, 'low'] < df.loc[last_idx, 'low']:
                        keep[-1] = i
                        last_kept = i
                    elif label == 'SELL' and df.loc[i, 'high'] > df.loc[last_idx, 'high']:
                        keep[-1] = i
                        last_kept = i
            for i in indices:
                if i not in keep:
                    df.loc[i, 'label'] = 'NONE'
                    df.loc[i, 'label_price'] = np.nan
        return df

    def get_statistics(self, df: pd.DataFrame) -> dict:
        """获取标注统计"""
        total = len(df)
        buy_count = (df['label'] == 'BUY').sum()
        sell_count = (df['label'] == 'SELL').sum()
        return {
            'total_samples': total,
            'buy_signals': int(buy_count),
            'sell_signals': int(sell_count),
            'none_samples': int(total - buy_count - sell_count),
            'signal_rate': round((buy_count + sell_count) / total * 100, 2),
        }


def label_from_csv(input_path: str, output_path: Optional[str] = None) -> pd.DataFrame:
    """标注单个 CSV 文件"""
    df = pd.read_csv(input_path)
    df.columns = [c.lower().strip() for c in df.columns]

    labeler = KlineLabeler()
    labeled = labeler.label(df)
    stats = labeler.get_statistics(labeled)

    logger.info(f"标注完成: {input_path}")
    logger.info(f"  总样本: {stats['total_samples']}, BUY: {stats['buy_signals']}, SELL: {stats['sell_signals']}")

    if output_path:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        labeled.to_csv(output_path, index=False)
        stats_path = output_path.replace('.csv', '_stats.json')
        with open(stats_path, 'w') as f:
            json.dump(stats, f, indent=2)

    return labeled


def batch_label(input_dir: Optional[str] = None, output_dir: Optional[str] = None):
    """批量标注 data/klines/ 下所有 CSV"""
    input_dir = input_dir or str(_DATA_DIR / "klines")
    output_dir = output_dir or str(_DATA_DIR / "labeled")
    os.makedirs(output_dir, exist_ok=True)

    csv_files = list(Path(input_dir).glob("*.csv"))
    if not csv_files:
        logger.error("=" * 60)
        logger.error(f"❌ 未找到K线数据文件 (data/klines/*.csv)")
        logger.error(f"   搜索目录: {input_dir}")
        logger.error("")
        logger.error("📋 解决步骤:")
        logger.error("   1. 在UI策略页点击「开始训练」按钮，会自动下载K线数据")
        logger.error("   2. 或手动准备K线CSV文件放到 data/klines/ 目录")
        logger.error("   CSV格式: time,open,high,low,close,volume")
        logger.error("=" * 60)
        return

    logger.info(f"找到 {len(csv_files)} 个文件待标注")

    for f in csv_files:
        try:
            out = os.path.join(output_dir, f"{f.stem}_labeled.csv")
            label_from_csv(str(f), out)
        except Exception as e:
            logger.error(f"  [错误] {f.name}: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        label_from_csv(sys.argv[1], sys.argv[1].replace('.csv', '_labeled.csv'))
    else:
        batch_label()
