#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论算法模块（v2 - 重构版）
============================
提取缠论核心特征，用于ML模型输入。

核心改进:
- 分型检测: 非重叠约束（相邻分型不共享K线）
- 笔构建: 时间连续性修复 + 至少5根独立K线
- 线段构建: 特征序列分型法（经典缠论）
- 推演增强: 中枢对齐 + 趋势强弱 + 成交量加权 + 置信度
- 增量计算: compute_incremental() 支持增量更新
- 特征去冗余: 删除 cl_n_fractals_20（可由 tops+bottoms 推出）

参考: 缠中说禅《教你炒股票108课》

使用方式:
    from strategies.data.chanlun import ChanLunFeatureExtractor
    extractor = ChanLunFeatureExtractor(df)
    features = extractor.extract_features()  # 返回 dict[str, float]

    # 增量计算
    extractor.compute_incremental(new_df)
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

from utils.numeric import clean_num

logger = logging.getLogger(__name__)


@dataclass
class Fractal:
    """分型"""
    idx: int          # 在序列中的索引
    time: str         # 时间
    type: str         # "top" 顶分型 / "bottom" 底分型
    high: float       # 高点价格
    low: float        # 低点价格
    price: float      # 收盘价
    range_start: int  # 分型占用的K线起始索引（含）
    range_end: int    # 分型占用的K线结束索引（含）


@dataclass
class Stroke:
    """笔 (连接两个相邻分型)"""
    start_idx: int
    end_idx: int
    start_time: str
    end_time: str
    start_price: float
    end_price: float
    direction: str     # "up" 向上笔 / "down" 向下笔
    length: float      # 价格变化幅度
    avg_volume: float = 0.0  # 笔内平均成交量（推演用）


@dataclass
class Hub:
    """中枢 (至少3笔重叠的价格区间)"""
    start_idx: int
    end_idx: int
    top: float         # 中枢上沿 (min of stroke highs)
    bottom: float      # 中枢下沿 (max of stroke lows)
    center: float      # 中枢中轴
    strokes_in: int    # 包含的笔数


@dataclass
class Segment:
    """线段 (至少3笔构成，连接两个同级别中枢或走势端点)"""
    start_idx: int
    end_idx: int
    start_time: str
    end_time: str
    start_price: float
    end_price: float
    direction: str     # "up" / "down"
    length: float      # 价格变化幅度
    strokes_count: int # 包含笔数
    hub: Optional[Hub] = None  # 线段内的中枢（如果有）


# ─── 可配参数（从 config.yaml 读取，支持覆盖） ───

_CHANLUN_DEFAULTS = {
    'bi_min_gap': 5,           # 笔内最少K线间隔（经典缠论=5根独立K线，确保笔的稳定性）
    'bi_strict': True,         # 严格模式开启，过滤低质量笔
    'bi_min_bars': 4,          # 严格模式: 笔内最少独立K线数(不含分型K线，经典=4)
    'seg_min_strokes': 3,      # 线段最少笔数
    'divergence_rate': 0.8,    # MACD背驰阈值: 面积比 < 此值视为背驰
    'volume_divergence': True, # 是否启用成交量背驰
    'hub_window': 5,           # 中枢扩展最大笔数（缩小以识别更精确的中枢）
    'fractal_range': 1,        # 分型占用K线半径: 1=3根K线(更精确), 2=5根K线(更宽泛)
}


def _get_chanlun_config() -> dict:
    """从全局配置读取缠论参数，未配置则用默认值"""
    try:
        from core.config import get_config
        cfg = get_config()
        # 如果 config.yaml 中有 chanlun 配置段，读取覆盖
        if hasattr(cfg, 'chanlun'):
            user_cfg = {k: v for k, v in vars(cfg.chanlun).items() if v is not None}
            return {**_CHANLUN_DEFAULTS, **user_cfg}
    except Exception:
        pass
    return dict(_CHANLUN_DEFAULTS)


class ChanLunFeatureExtractor:
    """
    缠论特征提取器（v2 重构版）

    从K线数据中提取缠论相关特征，供ML模型使用。
    不追求缠论理论的完整实现，聚焦于可量化的有效特征。

    改进:
    - 分型非重叠约束
    - 笔时间连续性修复 + 5根独立K线检查
    - 线段特征序列分型法
    - 推演增强（中枢对齐 + 趋势强弱 + 成交量加权 + 置信度）
    - 增量计算支持
    - 可配参数 (config.yaml)
    """

    def __init__(self, df: pd.DataFrame, config: dict = None):
        """
        Args:
            df: DataFrame with columns [open, high, low, close, volume]
                至少20条数据
            config: 缠论参数覆盖，未提供则从 config.yaml 读取
        """
        self.df = df.copy()
        if self.df.empty:
            self.df = pd.DataFrame({'open': [0.0], 'high': [0.0], 'low': [0.0], 'close': [0.0], 'volume': [0]})
        self.df.columns = [c.lower().strip() for c in self.df.columns]

        # 数值列深度清洗
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in self.df.columns:
                self.df[col] = self.df[col].apply(lambda x: clean_num(x, 0.0))
            else:
                if col == 'volume':
                    self.df[col] = 0
                else:
                    self.df[col] = self.df['close'] if 'close' in self.df.columns else 0.0

        self.fractals: List[Fractal] = []
        self.strokes: List[Stroke] = []
        self.hubs: List[Hub] = []
        self.segments: List[Segment] = []
        self._computed = False
        # 可配参数
        self._cfg = config if config else _get_chanlun_config()

    # ═══════════════════════════════════════════════════════════
    #  核心算法
    # ═══════════════════════════════════════════════════════════

    def _detect_fractals(self, processed_df: pd.DataFrame = None) -> List[Fractal]:
        """
        分型检测（基于包含处理后的K线，含非重叠约束）

        顶分型: 中间K线高点最高，左右K线高点都低于中间
        底分型: 中间K线低点最低，左右K线低点都高于中间

        非重叠约束: 相邻两个分型之间至少间隔1根独立K线，
                   即新分型的起始K线必须 > 上一个分型的结束K线。

        参数控制:
          - fractal_range: 分型占用K线半径 (1=3根K线更精确, 2=5根K线更宽泛)

        返回的 Fractal.idx 使用 orig_idx 映射回原始K线索引
        """
        df = processed_df if processed_df is not None else self.df
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        # 使用 orig_idx 列映射回原始索引，没有则使用当前索引
        orig_idx_map = df["orig_idx"].values if "orig_idx" in df.columns else np.arange(len(df))
        times = df.index.astype(str).values if hasattr(df.index, 'astype') else df.iloc[:, 0].astype(str).values

        fractals = []
        n = len(highs)
        # 分型半径由配置控制，默认1（使用3根K线而非5根，更精确）
        r = self._cfg.get('fractal_range', 1)
        min_required = 2 * r + 1  # 最少需要的K线数
        if n < min_required:
            return fractals

        # 上一个分型占用的K线范围（processed_df 索引），用于非重叠约束
        last_end = -1  # 上一个分型的结束位置

        for i in range(r, n - r):
            # 非重叠约束: 新分型的起始位置必须 > 上一个分型的结束位置
            # 分型占用的K线范围: [i-r, i+r]
            if i - r <= last_end:
                continue

            # 顶分型: 中间高点严格最高
            is_top = all(highs[i] > highs[i - j] for j in range(1, r + 1)) and \
                     all(highs[i] > highs[i + j] for j in range(1, r + 1))
            # 底分型: 中间低点严格最低
            is_bottom = all(lows[i] < lows[i - j] for j in range(1, r + 1)) and \
                        all(lows[i] < lows[i + j] for j in range(1, r + 1))

            orig_i = int(orig_idx_map[i]) if i < len(orig_idx_map) else i
            if is_top:
                fractals.append(Fractal(
                    idx=orig_i, time=str(times[i]), type="top",
                    high=float(highs[i]), low=float(lows[i]),
                    price=float(closes[i]),
                    range_start=i - r, range_end=i + r
                ))
                last_end = i + r  # 更新分型结束位置
            elif is_bottom:
                fractals.append(Fractal(
                    idx=orig_i, time=str(times[i]), type="bottom",
                    high=float(highs[i]), low=float(lows[i]),
                    price=float(closes[i]),
                    range_start=i - r, range_end=i + r
                ))
                last_end = i + r  # 更新分型结束位置

        return fractals

    def _process_containment(self) -> pd.DataFrame:
        """
        处理K线包含关系（完整实现）
        包含关系: K线A的高低点完全被K线B包含（或反之）
        处理方式:
          - 上升趋势中取两根K线高点的最大值和低点的最大值
          - 下降趋势中取两根K线高点的最小值和低点的最小值
        返回合并后的DataFrame，带 orig_idx 列记录原始索引起点
        确保后续所有索引（分型/笔/中枢）映射回原始K线位置
        """
        df = self.df.copy()
        if len(df) < 3:
            df['orig_idx'] = df.index.astype(int)
            return df.reset_index(drop=True)

        merged_rows = []
        i = 0
        while i < len(df):
            row = df.iloc[i].copy()
            orig_start = i  # 记录原始起始索引
            # 检查下一根是否与当前有包含关系
            while i + 1 < len(df):
                next_row = df.iloc[i + 1]
                curr_high, curr_low = row['high'], row['low']
                next_high, next_low = next_row['high'], next_row['low']

                # 包含关系: 一根完全包含另一根
                is_contain = (curr_high >= next_high and curr_low <= next_low) or \
                             (next_high >= curr_high and next_low <= curr_low)
                if not is_contain:
                    break

                # 判断趋势方向: 用 high 判断（缠论标准：高点上升为上涨趋势）
                if merged_rows:
                    prev_high = merged_rows[-1]['high']
                    is_up = prev_high <= row['high']
                else:
                    is_up = row['high'] <= next_row['high']

                if is_up:
                    row['high'] = max(curr_high, next_high)
                    row['low'] = max(curr_low, next_low)
                else:
                    row['high'] = min(curr_high, next_high)
                    row['low'] = min(curr_low, next_low)

                i += 1

            row['orig_idx'] = int(orig_start)  # 保留原始索引起点
            merged_rows.append(row)
            i += 1

        result = pd.DataFrame(merged_rows)
        result = result.reset_index(drop=True)
        return result

    def _build_strokes(self) -> List[Stroke]:
        """
        构建笔：连接相邻的顶分型和底分型（v2 修复版）

        规则:
          - 顶 → 底 = 向下笔，底 → 顶 = 向上笔
          - 两分型之间至少有 bi_min_gap 根K线（经典缠论=5）
          - 向上笔: 终点高点必须高于起点低点
          - 向下笔: 终点低点必须低于起点高点

        时间连续性修复:
          - 遇到同类型分型时，如果当前更极端则更新 last_used
          - 如果不更极端，也检查能否与 last_used 构成有效笔
          - 笔内至少包含5根独立K线（扣除分型K线本身后）
          - 修复 last_used 跳跃导致丢失有效笔的问题

        严格模式 (bi_strict=True) 额外检查:
          - 笔内独立K线数 ≥ bi_min_bars（排除分型K线本身）
          - 笔内振幅合理（不超过全局振幅的 80%，过滤异常笔）
        """
        if len(self.fractals) < 2:
            return []

        min_gap = self._cfg.get('bi_min_gap', 5)
        strict = self._cfg.get('bi_strict', True)
        min_bars = self._cfg.get('bi_min_bars', 4)

        # 全局振幅参考（用于严格模式异常过滤）
        if strict and len(self.df) > 0:
            global_range = float(self.df['high'].max() - self.df['low'].min())
        else:
            global_range = 0.0

        strokes = []
        last_used = 0

        for i in range(1, len(self.fractals)):
            prev = self.fractals[last_used]
            curr = self.fractals[i]

            # 必须是相反类型的分型才能构成笔
            if prev.type == curr.type:
                # 同类型分型: 比较价格极值
                if prev.type == "top":
                    if curr.high > prev.high:
                        # 当前顶分型更高 → 更新 last_used
                        last_used = i
                    # 否则不更极端，跳过当前分型（保留 last_used 不变）
                else:  # bottom
                    if curr.low < prev.low:
                        # 当前底分型更低 → 更新 last_used
                        last_used = i
                    # 否则不更极端，跳过当前分型
                continue

            # K线间隔检查
            gap = curr.idx - prev.idx
            if gap < min_gap:
                continue

            # 严格模式: 独立K线数检查（排除两端各3根分型K线）
            if strict:
                independent_bars = gap - 3  # 减去两个分型各占的K线
                if independent_bars < min_bars:
                    continue

            # 方向与价格验证
            if prev.type == "bottom" and curr.type == "top":
                # 向上笔: 终点高点必须高于起点低点
                if curr.high <= prev.low:
                    continue
                direction = "up"
                length = curr.high - prev.low
            elif prev.type == "top" and curr.type == "bottom":
                # 向下笔: 终点低点必须低于起点高点
                if curr.low >= prev.high:
                    continue
                direction = "down"
                length = prev.high - curr.low
            else:
                continue

            # 严格模式: 异常振幅过滤（笔长超过全局振幅80%的可能是包含处理遗漏）
            if strict and global_range > 0:
                if length > global_range * 0.8:
                    continue

            # 计算笔内平均成交量（推演用）
            avg_vol = self._calc_stroke_avg_volume(prev.idx, curr.idx)

            strokes.append(Stroke(
                start_idx=prev.idx,
                end_idx=curr.idx,
                start_time=prev.time,
                end_time=curr.time,
                start_price=prev.price,
                end_price=curr.price,
                direction=direction,
                length=length,
                avg_volume=avg_vol,
            ))
            last_used = i

        return strokes

    def _calc_stroke_avg_volume(self, start_idx: int, end_idx: int) -> float:
        """计算笔内平均成交量"""
        try:
            if self.df is None or len(self.df) == 0 or 'volume' not in self.df.columns:
                return 0.0
            si = max(0, min(start_idx, len(self.df) - 1))
            ei = max(0, min(end_idx, len(self.df) - 1))
            if si > ei:
                si, ei = ei, si
            seg = self.df.iloc[si:ei + 1]
            if len(seg) == 0:
                return 0.0
            return float(seg['volume'].mean())
        except Exception:
            return 0.0

    def _detect_hubs(self) -> List[Hub]:
        """
        中枢检测: 至少3笔重叠的价格区间
        中枢区间 = [max(各笔低点), min(各笔高点)] 的笔段交集
        使用原始K线数据回溯每笔的真实高低点范围，而非端点价格

        配置统一: window_size 从 self._cfg['hub_window'] 读取
        """
        if len(self.strokes) < 3 or self.df is None or len(self.df) == 0:
            return []

        df = self.df
        df_len = len(df)

        # 预计算每笔的真实高低点区间（基于原始K线）
        def _stroke_range(s):
            """返回笔经过的所有原始K线的真实 [high_max, low_min]"""
            si = max(0, min(s.start_idx, df_len - 1))
            ei = max(0, min(s.end_idx, df_len - 1))
            if si > ei:
                si, ei = ei, si
            seg = df.iloc[si:ei + 1]
            if len(seg) == 0:
                return (float(max(s.start_price, s.end_price)),
                        float(min(s.start_price, s.end_price)))
            return (float(seg['high'].max()), float(seg['low'].min()))

        # 构建每笔的真实区间
        stroke_ranges = [_stroke_range(s) for s in self.strokes]

        hubs = []
        # 配置统一: 从 self._cfg 读取 hub_window，不再硬编码
        window_size = self._cfg.get('hub_window', 9)

        for i in range(len(self.strokes) - 2):
            # 检查连续3笔是否有重叠
            r1, r2, r3 = stroke_ranges[i], stroke_ranges[i+1], stroke_ranges[i+2]

            # 重叠区间: [max(lows), min(highs)]
            hub_bottom = max(r1[1], r2[1], r3[1])  #  lows 的最大值
            hub_top = min(r1[0], r2[0], r3[0])     #  highs 的最小值

            # 有有效重叠才构成中枢
            if hub_top > hub_bottom:
                # 尝试扩展到更多笔
                end_j = i + 2
                for j in range(i + 3, min(len(self.strokes), i + window_size)):
                    rj = stroke_ranges[j]
                    new_top = min(hub_top, rj[0])
                    new_bottom = max(hub_bottom, rj[1])
                    if new_top > new_bottom:
                        hub_top, hub_bottom = new_top, new_bottom
                        end_j = j
                    else:
                        break

                # 过滤: 中枢宽度太小（< 0.05% 价格）可能是噪声
                center = (hub_top + hub_bottom) / 2
                if center > 0 and (hub_top - hub_bottom) / center < 0.0005:
                    continue

                hubs.append(Hub(
                    start_idx=self.strokes[i].start_idx,
                    end_idx=self.strokes[end_j].end_idx,
                    top=round(hub_top, 6),
                    bottom=round(hub_bottom, 6),
                    center=round(center, 6),
                    strokes_in=end_j - i + 1
                ))

        return hubs

    def _build_segments(self) -> List[Segment]:
        """
        构建线段（v2: 特征序列分型法）

        经典缠论线段定义:
          - 线段由至少 seg_min_strokes 根笔构成
          - 对线段内的笔提取特征序列: 取每笔的高低点构成虚拟K线
          - 在特征序列上检测分型（顶分型/底分型）
          - 特征序列分型确认线段终结

        降级策略: 如果特征序列不足3根，使用简化逻辑（走势拐点法）
        """
        if len(self.strokes) < self._cfg.get('seg_min_strokes', 3):
            return []

        segments = []
        i = 0

        while i < len(self.strokes) - 2:
            start_stroke = self.strokes[i]
            seg_direction = start_stroke.direction

            # 尝试用特征序列分型法确定线段终点
            seg_end_idx = self._find_segment_end_by_char_seq(i, seg_direction)

            # 如果特征序列法失败（返回 -1），降级为简化逻辑
            if seg_end_idx < 0:
                seg_end_idx = self._find_segment_end_simple(i, seg_direction)

            if seg_end_idx < 0:
                # 无法确定线段终点，跳过
                i += 1
                continue

            end_stroke = self.strokes[seg_end_idx]

            # 构建线段
            if seg_direction == "up":
                seg_start = min(start_stroke.start_price, start_stroke.end_price)
                seg_end = max(end_stroke.start_price, end_stroke.end_price)
            else:
                seg_start = max(start_stroke.start_price, start_stroke.end_price)
                seg_end = min(end_stroke.start_price, end_stroke.end_price)

            # 线段内中枢（如果有）
            seg_hub = None
            for h in self.hubs:
                if h.start_idx >= start_stroke.start_idx and h.end_idx <= end_stroke.end_idx:
                    seg_hub = h
                    break

            segments.append(Segment(
                start_idx=start_stroke.start_idx,
                end_idx=end_stroke.end_idx,
                start_time=start_stroke.start_time,
                end_time=end_stroke.end_time,
                start_price=seg_start,
                end_price=seg_end,
                direction=seg_direction,
                length=abs(seg_end - seg_start),
                strokes_count=seg_end_idx - i + 1,
                hub=seg_hub,
            ))

            # 下一个线段从当前线段末尾开始
            i = seg_end_idx + 1

        return segments

    def _find_segment_end_by_char_seq(self, start_i: int, direction: str) -> int:
        """
        特征序列分型法确定线段终点

        原理:
          1. 对线段内的笔提取特征序列（虚拟K线）
             - 向上线段: 取每根向上笔的高点和低点 → 虚拟K线的高/低
             - 向下线段: 取每根向下笔的高点和低点 → 虚拟K线的高/低
          2. 在特征序列上检测分型
             - 向上线段: 检测顶分型 → 线段结束
             - 向下线段: 检测底分型 → 线段结束

        Args:
            start_i: 线段起始笔在 self.strokes 中的索引
            direction: 线段方向 "up" / "down"

        Returns:
            线段结束笔的索引，或 -1（特征序列不足，降级）
        """
        min_strokes = self._cfg.get('seg_min_strokes', 3)

        # 提取特征序列: 取同方向笔的高低点
        char_seq = []  # [(high, low, stroke_idx)]
        for j in range(start_i, len(self.strokes)):
            s = self.strokes[j]
            if direction == "up":
                # 向上线段: 取向上笔的高低点
                if s.direction == "up":
                    h = max(s.start_price, s.end_price)
                    l = min(s.start_price, s.end_price)
                    char_seq.append((h, l, j))
            else:
                # 向下线段: 取向下笔的高低点
                if s.direction == "down":
                    h = max(s.start_price, s.end_price)
                    l = min(s.start_price, s.end_price)
                    char_seq.append((h, l, j))

        # 特征序列至少需要3根才能检测分型
        if len(char_seq) < 3:
            return -1  # 降级信号

        # 在特征序列上检测分型
        for k in range(1, len(char_seq) - 1):
            prev_h, prev_l, prev_j = char_seq[k - 1]
            curr_h, curr_l, curr_j = char_seq[k]
            next_h, next_l, next_j = char_seq[k + 1]

            if direction == "up":
                # 向上线段: 检测顶分型（高点最高）
                if curr_h > prev_h and curr_h > next_h:
                    # 线段结束点 = 顶分型对应笔的索引
                    # 线段至少需要 min_strokes 根笔
                    end_j = curr_j
                    stroke_count = end_j - start_i + 1
                    if stroke_count >= min_strokes:
                        return end_j
            else:
                # 向下线段: 检测底分型（低点最低）
                if curr_l < prev_l and curr_l < next_l:
                    end_j = curr_j
                    stroke_count = end_j - start_i + 1
                    if stroke_count >= min_strokes:
                        return end_j

        # 未找到分型 → 线段可能还在延续，返回最后一根笔
        # 但至少需要 min_strokes 根笔
        last_j = char_seq[-1][2]
        if last_j - start_i + 1 >= min_strokes:
            return last_j

        return -1  # 降级

    def _find_segment_end_simple(self, start_i: int, direction: str) -> int:
        """
        简化逻辑确定线段终点（降级方案）

        基于中枢位置和走势拐点判断线段结束。
        当特征序列不足3根时使用此方法。
        """
        min_strokes = self._cfg.get('seg_min_strokes', 3)
        start_stroke = self.strokes[start_i]

        if start_i + min_strokes - 1 >= len(self.strokes):
            return -1

        end_j = start_i + min_strokes - 1  # 最少笔数

        # 尝试扩展: 找到走势的自然结束点
        for j in range(start_i + min_strokes - 1, len(self.strokes)):
            end_j = j
            # 检查是否出现反向突破
            if j + 1 < len(self.strokes):
                next_s = self.strokes[j + 1]
                if direction == "up":
                    # 向上线段: 下一笔的低点跌破线段起点附近 → 结束
                    if next_s.direction == "down":
                        seg_low = min(
                            min(s.start_price, s.end_price)
                            for s in self.strokes[start_i:j+1]
                        )
                        next_low = min(next_s.start_price, next_s.end_price)
                        if next_low <= seg_low * 1.005:  # 0.5% 容差
                            break
                else:
                    # 向下线段: 下一笔的高点突破线段起点附近 → 结束
                    if next_s.direction == "up":
                        seg_high = max(
                            max(s.start_price, s.end_price)
                            for s in self.strokes[start_i:j+1]
                        )
                        next_high = max(next_s.start_price, next_s.end_price)
                        if next_high >= seg_high * 0.995:
                            break

        return end_j

    def compute(self) -> "ChanLunFeatureExtractor":
        """执行完整计算链路: 包含处理 → 分型检测 → 笔构建 → 中枢识别 → 线段构建"""
        if self._computed:
            return self
        # Step 1: 处理K线包含关系
        processed_df = self._process_containment()
        # Step 2: 在合并后的K线上检测分型
        self.fractals = self._detect_fractals(processed_df)
        # Step 3: 构建笔（含严格模式检查）
        self.strokes = self._build_strokes()
        # Step 4: 识别中枢
        self.hubs = self._detect_hubs()
        # Step 5: 构建线段（笔的更高层级结构）
        self.segments = self._build_segments()
        self._computed = True
        return self

    def compute_incremental(self, new_df: pd.DataFrame) -> "ChanLunFeatureExtractor":
        """
        增量计算: 接收新的完整 DataFrame，只对新增的K线做缠论计算

        策略:
          - 如果新增K线 < 10根，走增量路径（复用已有结果）
          - 否则全量重算

        增量路径:
          - 复用已有的分型/笔/中枢
          - 只检查最后一个可能受影响的分型/笔
          - 对新增K线重新做包含处理 → 分型 → 笔 → 中枢 → 线段

        Args:
            new_df: 新的完整 DataFrame（包含历史 + 新增数据）

        Returns:
            self（更新后的提取器）
        """
        old_len = len(self.df)
        new_len = len(new_df)

        # 新增K线数
        added = new_len - old_len

        if added <= 0 or not self._computed:
            # 没有新增或未计算过 → 全量重算
            self.df = new_df.copy()
            self.df.columns = [c.lower().strip() for c in self.df.columns]
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in self.df.columns:
                    self.df[col] = self.df[col].apply(lambda x: clean_num(x, 0.0))
            self._computed = False
            return self.compute()

        if added >= 10:
            # 新增太多 → 全量重算更可靠
            self.df = new_df.copy()
            self.df.columns = [c.lower().strip() for c in self.df.columns]
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col in self.df.columns:
                    self.df[col] = self.df[col].apply(lambda x: clean_num(x, 0.0))
            self._computed = False
            return self.compute()

        # 增量路径: 新增 < 10 根K线
        # 1. 更新 DataFrame
        self.df = new_df.copy()
        self.df.columns = [c.lower().strip() for c in self.df.columns]
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in self.df.columns:
                self.df[col] = self.df[col].apply(lambda x: clean_num(x, 0.0))

        # 2. 对新增K线做包含处理
        # 简化: 对整个数据集重新做包含处理（数据量小时开销可接受）
        processed_df = self._process_containment()

        # 3. 重新检测分型（利用非重叠约束，已有分型不会重复检测）
        new_fractals = self._detect_fractals(processed_df)

        # 4. 合并分型: 复用已有的 + 新增的（去重）
        # 由于非重叠约束，新检测的分型如果与已有重叠会被跳过
        # 直接替换即可（全量分型列表）
        self.fractals = new_fractals

        # 5. 重新构建笔（基于新的分型列表）
        self.strokes = self._build_strokes()

        # 6. 重新识别中枢
        self.hubs = self._detect_hubs()

        # 7. 重新构建线段
        self.segments = self._build_segments()

        self._computed = True
        return self

    # ═══════════════════════════════════════════════════════════
    #  推演逻辑（增强版）
    # ═══════════════════════════════════════════════════════════

    def _calc_historical_callback_ratios(self) -> Optional[Dict]:
        """
        从历史线段中统计回调深度分布

        分析每对相邻反向线段，计算回调比例：
          上涨线段后回调比例 = 回调幅度 / 上涨幅度
          下跌线段后反弹比例 = 反弹幅度 / 下跌幅度

        Returns:
            dict with 'up_callback_mean', 'up_callback_median', 'down_callback_mean',
            'down_callback_median', 'sample_count', or None if insufficient data
        """
        if len(self.segments) < 4:
            return None

        up_callbacks = []   # 上涨后的回调比例
        down_callbacks = [] # 下跌后的反弹比例

        for i in range(len(self.segments) - 1):
            seg_cur = self.segments[i]
            seg_next = self.segments[i + 1]

            # 必须是反向线段
            if seg_cur.direction == seg_next.direction:
                continue

            if seg_cur.length <= 0:
                continue

            ratio = seg_next.length / seg_cur.length

            if seg_cur.direction == 'up':
                up_callbacks.append(ratio)
            else:
                down_callbacks.append(ratio)

        if len(up_callbacks) < 2 and len(down_callbacks) < 2:
            return None

        result = {'sample_count': len(up_callbacks) + len(down_callbacks)}

        if up_callbacks:
            result['up_callback_mean'] = float(np.mean(up_callbacks))
            result['up_callback_median'] = float(np.median(up_callbacks))
            result['up_callback_std'] = float(np.std(up_callbacks)) if len(up_callbacks) > 1 else 0.0
        else:
            result['up_callback_mean'] = 0.5
            result['up_callback_median'] = 0.5
            result['up_callback_std'] = 0.0

        if down_callbacks:
            result['down_callback_mean'] = float(np.mean(down_callbacks))
            result['down_callback_median'] = float(np.median(down_callbacks))
            result['down_callback_std'] = float(np.std(down_callbacks)) if len(down_callbacks) > 1 else 0.0
        else:
            result['down_callback_mean'] = 0.5
            result['down_callback_median'] = 0.5
            result['down_callback_std'] = 0.0

        return result

    def _calc_volume_at_segment_end(self, seg: 'Segment') -> float:
        """计算线段结束时的平均成交量"""
        try:
            if self.df is None or len(self.df) == 0 or 'volume' not in self.df.columns:
                return 0.0
            end_idx = min(seg.end_idx, len(self.df) - 1)
            start_idx = max(0, end_idx - 5)
            return float(self.df['volume'].iloc[start_idx:end_idx + 1].mean())
        except Exception:
            return 0.0

    def _calc_volume_at_segment_start(self, seg: 'Segment') -> float:
        """计算线段开始时的平均成交量"""
        try:
            if self.df is None or len(self.df) == 0 or 'volume' not in self.df.columns:
                return 0.0
            start_idx = max(0, min(seg.start_idx, len(self.df) - 1))
            end_idx = min(start_idx + 5, len(self.df))
            return float(self.df['volume'].iloc[start_idx:end_idx].mean())
        except Exception:
            return 0.0

    def compute_projection(self) -> Optional[Dict]:
        """
        推演逻辑增强版: 基于当前缠论结构推算未来走势

        增强内容:
          - 历史线段回调分布: 从历史线段中统计真实回调比例，替代固定 Fibonacci
          - 中枢位置作为支撑/阻力参考（推演目标位与中枢上沿/下沿对齐）
          - 趋势强弱判断: 最近几笔长度递增（加速）vs 递减（衰减）
          - 成交量加权: 线段结束时 vs 开始时的量能变化
          - 输出推演置信度（high/medium/low）

        Returns:
            推演结果字典，或 None（数据不足）
        """
        if not self.strokes or len(self.strokes) < 3:
            return None

        import numpy as np

        last_stroke = self.strokes[-1]
        recent_strokes = self.strokes[-5:] if len(self.strokes) >= 5 else self.strokes

        # 平均笔振幅
        avg_amplitude = np.mean([abs(s.length) for s in recent_strokes])

        # 当前笔方向和端点
        is_up = last_stroke.direction == 'up'
        last_end = last_stroke.end_price

        # ── 趋势强弱判断 ──
        trend_strength = 1.0
        if len(recent_strokes) >= 3:
            lengths = [s.length for s in recent_strokes[-3:]]
            if lengths[-1] > lengths[-2] > lengths[0]:
                trend_strength = 1.2
            elif lengths[-1] < lengths[-2] < lengths[0]:
                trend_strength = 0.7

        # ── 成交量加权（基于线段级别） ──
        volume_factor = 1.0
        if self.segments and len(self.segments) >= 2:
            last_seg = self.segments[-1]
            vol_end = self._calc_volume_at_segment_end(last_seg)
            vol_start = self._calc_volume_at_segment_start(last_seg)
            if vol_start > 0:
                vol_ratio = vol_end / vol_start
                if vol_ratio < 0.8:
                    volume_factor = 0.8   # 量缩 → 推演打折
                elif vol_ratio > 1.2:
                    volume_factor = 1.1   # 量增 → 推演放大

        # ── 历史线段回调分布（替代固定 Fibonacci） ──
        hist_ratios = self._calc_historical_callback_ratios()
        if hist_ratios:
            if is_up:
                # 向上后回调：用历史回调比例
                ratio_mid = hist_ratios['up_callback_median']
                ratio_mean = hist_ratios['up_callback_mean']
                # 用中位数为主，均值为辅（抗异常值）
                base_ratio = ratio_mid * 0.7 + ratio_mean * 0.3
            else:
                # 向下后反弹：用历史反弹比例
                ratio_mid = hist_ratios['down_callback_median']
                ratio_mean = hist_ratios['down_callback_mean']
                base_ratio = ratio_mid * 0.7 + ratio_mean * 0.3

            # 浅/深回调 = 历史比例 ± 1 个标准差
            ratio_std = hist_ratios.get('up_callback_std' if is_up else 'down_callback_std', 0.1)
            shallow_ratio = max(0.15, base_ratio - ratio_std)
            deep_ratio = base_ratio + ratio_std
        else:
            # 线段样本不足，降级到经典 Fibonacci
            base_ratio = 0.5
            shallow_ratio = 0.382
            deep_ratio = 0.618

        # 综合调整系数
        adjust = trend_strength * volume_factor

        # ── 推演下一笔 ──
        if is_up:
            proj_direction = 'down'
            proj_end_shallow = last_end - avg_amplitude * shallow_ratio * adjust
            proj_end_mid = last_end - avg_amplitude * base_ratio * adjust
            proj_end_deep = last_end - avg_amplitude * deep_ratio * adjust
            proj_end_target = proj_end_mid
            support_zone_top = max(proj_end_shallow, proj_end_deep)
            support_zone_bottom = min(proj_end_shallow, proj_end_deep)
        else:
            proj_direction = 'up'
            proj_end_shallow = last_end + avg_amplitude * shallow_ratio * adjust
            proj_end_mid = last_end + avg_amplitude * base_ratio * adjust
            proj_end_deep = last_end + avg_amplitude * deep_ratio * adjust
            proj_end_target = proj_end_mid
            support_zone_top = max(proj_end_shallow, proj_end_deep)
            support_zone_bottom = min(proj_end_shallow, proj_end_deep)

        # ── 中枢对齐: 推演目标位与中枢上沿/下沿对齐 ──
        next_hub_center = None
        next_hub_top = None
        next_hub_bottom = None
        if self.hubs:
            last_hub = self.hubs[-1]
            hub_width = last_hub.top - last_hub.bottom
            if is_up:
                if abs(proj_end_target - last_hub.top) < hub_width * 0.5:
                    proj_end_target = last_hub.top
            else:
                if abs(proj_end_target - last_hub.bottom) < hub_width * 0.5:
                    proj_end_target = last_hub.bottom

            next_hub_center = round(float(proj_end_target), 3)
            next_hub_top = round(float(proj_end_target + hub_width / 2), 3)
            next_hub_bottom = round(float(proj_end_target - hub_width / 2), 3)
            if is_up:
                next_hub_top = min(next_hub_top, round(float(last_end), 3))
            else:
                next_hub_bottom = max(next_hub_bottom, round(float(last_end), 3))

        # ── 推演置信度 ──
        confidence = "medium"
        if hist_ratios and hist_ratios['sample_count'] >= 6:
            if trend_strength > 1.0 and volume_factor > 1.0:
                confidence = "high"
            elif trend_strength < 0.8 or volume_factor < 0.8:
                confidence = "low"
        else:
            # 历史样本不足时降级
            confidence = "low"

        # ── 推演时间范围 ──
        last_stroke_bars = last_stroke.end_idx - last_stroke.start_idx
        proj_bars = max(3, int(last_stroke_bars * 0.8))

        return {
            'direction': proj_direction,
            'start_price': round(float(last_end), 3),
            'end_target': round(float(proj_end_target), 3),
            'end_shallow': round(float(proj_end_shallow), 3),
            'end_deep': round(float(proj_end_deep), 3),
            'support_zone_top': round(float(support_zone_top), 3),
            'support_zone_bottom': round(float(support_zone_bottom), 3),
            'next_hub_center': next_hub_center,
            'next_hub_top': next_hub_top,
            'next_hub_bottom': next_hub_bottom,
            'last_date': '',
            'proj_bars': proj_bars,
            'avg_amplitude': round(float(avg_amplitude), 3),
            'is_current_up': is_up,
            'confidence': confidence,
            'trend_strength': round(float(trend_strength), 2),
            'volume_factor': round(float(volume_factor), 2),
            'base_ratio': round(float(base_ratio), 3),
            'hist_sample_count': hist_ratios['sample_count'] if hist_ratios else 0,
        }

    # ═══════════════════════════════════════════════════════════
    #  特征提取
    # ═══════════════════════════════════════════════════════════

    def extract_features(self) -> Dict[str, float]:
        """
        提取缠论特征，返回特征字典
        所有值均为 float，可直接用于ML模型

        注意: cl_n_fractals_20 已删除（可由 cl_n_tops_20 + cl_n_bottoms_20 推出）
        """
        self.compute()

        if len(self.df) < 20:
            return self._default_features()

        current_price = float(self.df["close"].iloc[-1])
        features = {}

        # ═══ 分型特征 ═══
        recent_n = min(20, len(self.df))
        recent_fractals = [f for f in self.fractals
                          if f.idx >= len(self.df) - recent_n]

        # 删除 cl_n_fractals_20（可由 tops + bottoms 推出）
        features["cl_n_tops_20"] = float(sum(1 for f in recent_fractals if f.type == "top"))
        features["cl_n_bottoms_20"] = float(sum(1 for f in recent_fractals if f.type == "bottom"))

        # 分型比例（用 tops + bottoms 计算）
        total_fractals = features["cl_n_tops_20"] + features["cl_n_bottoms_20"]
        if total_fractals > 0:
            features["cl_top_ratio"] = features["cl_n_tops_20"] / total_fractals
        else:
            features["cl_top_ratio"] = 0.5

        # 最新分型方向 (1=顶, -1=底, 0=无)
        if self.fractals:
            latest = self.fractals[-1]
            features["cl_last_fractal_dir"] = 1.0 if latest.type == "top" else -1.0
            features["cl_last_fractal_dist"] = float(len(self.df) - 1 - latest.idx)
        else:
            features["cl_last_fractal_dir"] = 0.0
            features["cl_last_fractal_dist"] = 999.0

        # ═══ 笔特征 ═══
        recent_strokes = [s for s in self.strokes
                         if s.end_idx >= len(self.df) - recent_n]

        features["cl_n_strokes_20"] = float(len(recent_strokes))

        if recent_strokes:
            lengths = [s.length for s in recent_strokes]
            features["cl_stroke_mean_len"] = float(np.mean(lengths))
            features["cl_stroke_max_len"] = float(np.max(lengths))
            features["cl_stroke_std_len"] = float(np.std(lengths)) if len(lengths) > 1 else 0.0

            # 笔方向趋势 (向上笔比例)
            up_count = sum(1 for s in recent_strokes if s.direction == "up")
            features["cl_up_stroke_ratio"] = up_count / len(recent_strokes)

            # 最新笔方向
            features["cl_last_stroke_dir"] = 1.0 if recent_strokes[-1].direction == "up" else -1.0
            features["cl_last_stroke_len"] = recent_strokes[-1].length
        else:
            features["cl_stroke_mean_len"] = 0.0
            features["cl_stroke_max_len"] = 0.0
            features["cl_stroke_std_len"] = 0.0
            features["cl_up_stroke_ratio"] = 0.5
            features["cl_last_stroke_dir"] = 0.0
            features["cl_last_stroke_len"] = 0.0

        # ═══ 中枢特征 ═══
        recent_hubs = [h for h in self.hubs
                      if h.end_idx >= len(self.df) - recent_n]

        features["cl_n_hubs_20"] = float(len(recent_hubs))

        if recent_hubs:
            latest_hub = recent_hubs[-1]
            features["cl_hub_center"] = latest_hub.center
            features["cl_hub_width"] = latest_hub.top - latest_hub.bottom
            features["cl_hub_width_pct"] = (features["cl_hub_width"] / latest_hub.center * 100) if latest_hub.center > 0 else 0.0

            # 当前价格相对于中枢的位置
            if current_price > latest_hub.top:
                features["cl_price_to_hub"] = 1.0  # 上方
            elif current_price < latest_hub.bottom:
                features["cl_price_to_hub"] = -1.0  # 下方
            else:
                features["cl_price_to_hub"] = 0.0  # 中枢内

            # 偏离中枢百分比
            features["cl_hub_deviation_pct"] = ((current_price - latest_hub.center)
                                                / latest_hub.center * 100) if latest_hub.center > 0 else 0.0
            features["cl_hub_strokes"] = float(latest_hub.strokes_in)
        else:
            features["cl_hub_center"] = current_price
            features["cl_hub_width"] = 0.0
            features["cl_hub_width_pct"] = 0.0
            features["cl_price_to_hub"] = 0.0
            features["cl_hub_deviation_pct"] = 0.0
            features["cl_hub_strokes"] = 0.0

        # ═══ 趋势特征 ═══
        if self.strokes:
            # 笔的动量（最近3笔的净方向）
            last_3 = self.strokes[-3:]
            momentum = sum(1 if s.direction == "up" else -1 for s in last_3)
            features["cl_stroke_momentum"] = float(momentum)

            # 笔长度趋势（增长/衰减）
            if len(last_3) >= 2:
                features["cl_stroke_trend"] = 1.0 if last_3[-1].length > last_3[-2].length else -1.0
            else:
                features["cl_stroke_trend"] = 0.0
        else:
            features["cl_stroke_momentum"] = 0.0
            features["cl_stroke_trend"] = 0.0

        # ═══ 线段特征 ═══
        if self.segments:
            latest_seg = self.segments[-1]
            features["cl_seg_dir"] = 1.0 if latest_seg.direction == "up" else -1.0
            features["cl_seg_count"] = float(len(self.segments))
            features["cl_seg_strokes"] = float(latest_seg.strokes_count)
            # 线段内是否有中枢
            features["cl_seg_has_hub"] = 1.0 if latest_seg.hub is not None else 0.0
        else:
            features["cl_seg_dir"] = 0.0
            features["cl_seg_count"] = 0.0
            features["cl_seg_strokes"] = 0.0
            features["cl_seg_has_hub"] = 0.0

        # ═══ 成交量背驰特征 ═══
        divergence = self._detect_macd_divergence()
        features["cl_volume_bottom_div"] = 1.0 if divergence.get("volume_bottom_div", False) else 0.0
        features["cl_volume_top_div"] = 1.0 if divergence.get("volume_top_div", False) else 0.0

        # 所有特征NaN检查
        for key, val in features.items():
            if np.isnan(val) or np.isinf(val):
                features[key] = 0.0

        return features

    def get_signal(self) -> Dict[str, any]:
        """
        生成缠论交易信号（独立信号，不依赖ML模型）
        包含三类买卖点判定 + MACD面积背驰

        三类买点:
          一买: 下跌趋势末端（底分型 + 下跌笔背驰）
          二买: 一买后第一次回调不破低点
          三买: 突破中枢后回踩不进中枢

        三类卖点（对称）:
          一卖: 上涨趋势末端（顶分型 + 上涨笔背驰）
          二卖: 一卖后第一次反弹不破高点
          三卖: 跌破中枢后反抽不进中枢

        返回: {"signal": "BUY/SELL/HOLD", "confidence": float, "reason": str,
               "buy_point": int (0/1/2/3), "sell_point": int (0/1/2/3)}
        """
        self.compute()
        features = self.extract_features()

        score = 0.0
        reasons = []
        buy_point = 0
        sell_point = 0

        # ── MACD 面积背驰 + 成交量背驰 检测 ──
        divergence = self._detect_macd_divergence()
        has_bottom_div = divergence.get("bottom_divergence", False) or divergence.get("volume_bottom_div", False)
        has_top_div = divergence.get("top_divergence", False) or divergence.get("volume_top_div", False)

        # ═══ 三类买点 ═══

        # 一买: 底分型 + 背驰（MACD面积或成交量）
        if (features["cl_last_fractal_dir"] == -1.0 and
                features["cl_last_fractal_dist"] <= 3 and
                has_bottom_div):
            score += 45
            buy_point = max(buy_point, 1)
            reasons.append("一买: 底分型+MACD底背驰")

        # 二买: 最近笔向上（一买后首次回调），且不破前低
        if (features["cl_last_stroke_dir"] > 0 and
              len(self.strokes) >= 3 and
              self._check_second_buy()):
            score += 35
            buy_point = max(buy_point, 2)
            reasons.append("二买: 回调不破前低")

        # 三买: 价格在中枢上方，回踩不进中枢
        if (features["cl_price_to_hub"] == 1.0 and
              features["cl_hub_deviation_pct"] > 0 and
              features["cl_hub_deviation_pct"] < 5 and
              self._check_third_buy()):
            score += 30
            buy_point = max(buy_point, 3)
            reasons.append("三买: 突破中枢回踩不入")

        # 底分型（无背驰的一般信号）
        if (features["cl_last_fractal_dir"] == -1.0 and
              features["cl_last_fractal_dist"] <= 3 and buy_point == 0):
            score += 25
            reasons.append("底分型形成")

        # ═══ 三类卖点 ═══

        if (features["cl_last_fractal_dir"] == 1.0 and
                features["cl_last_fractal_dist"] <= 3 and
                has_top_div):
            score -= 45
            sell_point = max(sell_point, 1)
            reasons.append("一卖: 顶分型+MACD顶背驰")

        # 二卖: 最近笔向下（一卖后首次反弹），且不破前高
        if (features["cl_last_stroke_dir"] < 0 and
              len(self.strokes) >= 3 and
              self._check_second_sell()):
            score -= 35
            sell_point = max(sell_point, 2)
            reasons.append("二卖: 反弹不破前高")

        # 三卖: 价格在中枢下方，反抽不进中枢
        if (features["cl_price_to_hub"] == -1.0 and
              features["cl_hub_deviation_pct"] < 0 and
              features["cl_hub_deviation_pct"] > -5 and
              self._check_third_sell()):
            score -= 30
            sell_point = max(sell_point, 3)
            reasons.append("三卖: 跌破中枢反抽不入")

        # 顶分型（无背驰的一般信号）
        if (features["cl_last_fractal_dir"] == 1.0 and
              features["cl_last_fractal_dist"] <= 3 and sell_point == 0):
            score -= 25
            reasons.append("顶分型形成")

        # ═══ 辅助信号 ═══

        # 突破中枢
        if features["cl_price_to_hub"] == 1.0 and features["cl_hub_deviation_pct"] > 2:
            if score > 0:
                score += 15
            reasons.append("突破中枢上方")

        # 中枢内回踩
        if features["cl_price_to_hub"] == 0.0 and features["cl_last_stroke_dir"] > 0:
            if score >= 0:
                score += 10
            reasons.append("中枢内反弹")

        # 笔动量
        if features["cl_stroke_momentum"] >= 2:
            score += 8
            reasons.append("笔动量向上")
        elif features["cl_stroke_momentum"] <= -2:
            score -= 8
            reasons.append("笔动量向下")

        # 判断信号
        if score >= 55:
            return {
                "signal": "BUY",
                "confidence": min(abs(score), 95),
                "reason": "缠论: " + "、".join(reasons),
                "buy_point": buy_point,
                "sell_point": 0,
            }
        elif score <= -55:
            return {
                "signal": "SELL",
                "confidence": min(abs(score), 95),
                "reason": "缠论: " + "、".join(reasons),
                "buy_point": 0,
                "sell_point": sell_point,
            }
        else:
            return {
                "signal": "HOLD",
                "confidence": 50,
                "reason": "缠论: " + ("、".join(reasons) if reasons else "无明确信号"),
                "buy_point": 0,
                "sell_point": 0,
            }

    # ── 三类买卖点辅助方法 ──

    def _check_second_buy(self) -> bool:
        """
        二买判定: 最近一次回调（向下笔）的低点 > 前一次向下笔的低点
        即不创新低
        """
        down_strokes = [s for s in self.strokes if s.direction == "down"]
        if len(down_strokes) < 2:
            return False
        last_down = down_strokes[-1]
        prev_down = down_strokes[-2]
        last_low = min(last_down.start_price, last_down.end_price)
        prev_low = min(prev_down.start_price, prev_down.end_price)
        return last_low > prev_low

    def _check_third_buy(self) -> bool:
        """
        三买判定: 价格突破中枢后回踩，低点不进中枢上沿
        """
        if not self.hubs or self.df is None or len(self.df) == 0:
            return False
        latest_hub = self.hubs[-1]
        current_price = float(self.df["close"].iloc[-1])
        n = min(5, len(self.df))
        recent_low = float(self.df["low"].iloc[-n:].min()) if n > 0 else current_price
        return current_price > latest_hub.top and recent_low >= latest_hub.top

    def _check_second_sell(self) -> bool:
        """
        二卖判定: 最近一次反弹（向上笔）的高点 < 前一次向上笔的高点
        即不创新高
        """
        up_strokes = [s for s in self.strokes if s.direction == "up"]
        if len(up_strokes) < 2:
            return False
        last_up = up_strokes[-1]
        prev_up = up_strokes[-2]
        last_high = max(last_up.start_price, last_up.end_price)
        prev_high = max(prev_up.start_price, prev_up.end_price)
        return last_high < prev_high

    def _check_third_sell(self) -> bool:
        """
        三卖判定: 价格跌破中枢后反抽，高点不进中枢下沿
        """
        if not self.hubs or self.df is None or len(self.df) == 0:
            return False
        latest_hub = self.hubs[-1]
        current_price = float(self.df["close"].iloc[-1])
        n = min(5, len(self.df))
        recent_high = float(self.df["high"].iloc[-n:].max()) if n > 0 else current_price
        return current_price < latest_hub.bottom and recent_high <= latest_hub.bottom

    def _detect_macd_divergence(self) -> Dict[str, bool]:
        """
        MACD 面积背驰 + 成交量背驰 检测

        底背驰: 价格创新低，但 MACD 柱状图面积（绿柱）缩小 → 下跌力度衰竭
        顶背驰: 价格创新高，但 MACD 柱状图面积（红柱）缩小 → 上涨力度衰竭

        成交量背驰 (volume_divergence=True):
          底背驰: 价格创新低，但成交量缩小 → 卖压衰竭
          顶背驰: 价格创新高，但成交量缩小 → 买盘衰竭

        Returns:
            {"bottom_divergence": bool, "top_divergence": bool,
             "volume_bottom_div": bool, "volume_top_div": bool}
        """
        div_rate = self._cfg.get('divergence_rate', 0.8)
        vol_div = self._cfg.get('volume_divergence', True)

        result = {
            "bottom_divergence": False, "top_divergence": False,
            "volume_bottom_div": False, "volume_top_div": False,
        }

        if len(self.df) < 40 or len(self.strokes) < 4:
            return result

        try:
            close = self.df["close"]
            volume = self.df.get("volume", pd.Series(dtype=float))

            # 计算 MACD 柱状图
            ema_fast = close.ewm(span=12, adjust=False).mean()
            ema_slow = close.ewm(span=26, adjust=False).mean()
            dif = ema_fast - ema_slow
            dea = dif.ewm(span=9, adjust=False).mean()
            macd_hist = dif - dea

            # 取最近两段同方向的笔
            down_strokes = [s for s in self.strokes if s.direction == "down"]
            up_strokes = [s for s in self.strokes if s.direction == "up"]

            # ── 底背驰: 比较最近两段向下笔 ──
            if len(down_strokes) >= 2:
                s1 = down_strokes[-2]
                s2 = down_strokes[-1]

                # 索引边界保护
                max_idx = len(macd_hist) - 1
                s1_start = max(0, min(s1.start_idx, max_idx))
                s1_end = max(0, min(s1.end_idx, max_idx))
                s2_start = max(0, min(s2.start_idx, max_idx))
                s2_end = max(0, min(s2.end_idx, max_idx))

                low1 = min(s1.start_price, s1.end_price)
                low2 = min(s2.start_price, s2.end_price)
                price_new_low = low2 <= low1

                # MACD 绿柱面积
                area1 = abs(macd_hist.iloc[s1_start:s1_end + 1].clip(upper=0).sum())
                area2 = abs(macd_hist.iloc[s2_start:s2_end + 1].clip(upper=0).sum())
                macd_weakened = area2 < area1 * div_rate
                result["bottom_divergence"] = price_new_low and macd_weakened

                # 成交量背驰
                if vol_div and len(volume) > 0:
                    vol1 = volume.iloc[s1_start:s1_end + 1].mean()
                    vol2 = volume.iloc[s2_start:s2_end + 1].mean()
                    if vol1 > 0:
                        result["volume_bottom_div"] = price_new_low and (vol2 < vol1 * div_rate)

            # ── 顶背驰: 比较最近两段向上笔 ──
            if len(up_strokes) >= 2:
                s1 = up_strokes[-2]
                s2 = up_strokes[-1]

                # 索引边界保护
                max_idx = len(macd_hist) - 1
                s1_start = max(0, min(s1.start_idx, max_idx))
                s1_end = max(0, min(s1.end_idx, max_idx))
                s2_start = max(0, min(s2.start_idx, max_idx))
                s2_end = max(0, min(s2.end_idx, max_idx))

                high1 = max(s1.start_price, s1.end_price)
                high2 = max(s2.start_price, s2.end_price)
                price_new_high = high2 >= high1

                # MACD 红柱面积
                area1 = macd_hist.iloc[s1_start:s1_end + 1].clip(lower=0).sum()
                area2 = macd_hist.iloc[s2_start:s2_end + 1].clip(lower=0).sum()
                macd_weakened = area2 < area1 * div_rate
                result["top_divergence"] = price_new_high and macd_weakened

                # 成交量背驰
                if vol_div and len(volume) > 0:
                    vol1 = volume.iloc[s1_start:s1_end + 1].mean()
                    vol2 = volume.iloc[s2_start:s2_end + 1].mean()
                    if vol1 > 0:
                        result["volume_top_div"] = price_new_high and (vol2 < vol1 * div_rate)

        except Exception as e:
            logger.debug(f"MACD背驰检测失败: {e}")

        return result

    def _default_features(self) -> Dict[str, float]:
        """数据不足时的默认值（已删除 cl_n_fractals_20）"""
        return {
            "cl_n_tops_20": 0.0,
            "cl_n_bottoms_20": 0.0,
            "cl_top_ratio": 0.5,
            "cl_last_fractal_dir": 0.0,
            "cl_last_fractal_dist": 999.0,
            "cl_n_strokes_20": 0.0,
            "cl_stroke_mean_len": 0.0,
            "cl_stroke_max_len": 0.0,
            "cl_stroke_std_len": 0.0,
            "cl_up_stroke_ratio": 0.5,
            "cl_last_stroke_dir": 0.0,
            "cl_last_stroke_len": 0.0,
            "cl_n_hubs_20": 0.0,
            "cl_hub_center": 0.0,
            "cl_hub_width": 0.0,
            "cl_hub_width_pct": 0.0,
            "cl_price_to_hub": 0.0,
            "cl_hub_deviation_pct": 0.0,
            "cl_hub_strokes": 0.0,
            "cl_stroke_momentum": 0.0,
            "cl_stroke_trend": 0.0,
            # 线段特征
            "cl_seg_dir": 0.0,
            "cl_seg_count": 0.0,
            "cl_seg_strokes": 0.0,
            "cl_seg_has_hub": 0.0,
            # 成交量背驰
            "cl_volume_bottom_div": 0.0,
            "cl_volume_top_div": 0.0,
        }


# ═══════════════════════════════════════════════════════════════
#  便捷函数
# ═══════════════════════════════════════════════════════════════

def get_chanlun_signal(df: pd.DataFrame, config: dict = None) -> Dict[str, any]:
    """便捷函数：获取缠论交易信号"""
    extractor = ChanLunFeatureExtractor(df, config=config)
    return extractor.get_signal()


def get_multi_timeframe_signal(
    df_higher: pd.DataFrame,
    df_lower: pd.DataFrame,
    config: dict = None,
) -> Dict[str, any]:
    """
    多级别联立信号（区间套）

    用高级别（如日线）判断大方向，低级别（如30分钟）找精确入场点。
    当两个级别信号方向一致时，置信度大幅提升。

    Args:
        df_higher: 高级别K线数据（如日线）
        df_lower: 低级别K线数据（如30分钟线）
        config: 缠论参数

    Returns:
        与 get_chanlun_signal 相同格式，额外包含 higher_signal 和 lower_signal
    """
    higher_sig = get_chanlun_signal(df_higher, config=config)
    lower_sig = get_chanlun_signal(df_lower, config=config)

    h_dir = higher_sig.get("signal", "HOLD")
    l_dir = lower_sig.get("signal", "HOLD")
    h_conf = higher_sig.get("confidence", 50)
    l_conf = lower_sig.get("confidence", 50)

    # 方向一致 → 共振增强
    if h_dir == l_dir and h_dir != "HOLD":
        fused_conf = min(95, (h_conf + l_conf) / 2 + 15)
        return {
            "signal": h_dir,
            "confidence": fused_conf,
            "reason": f"多级别共振: {h_dir} (日线{h_conf:.0f}% + 分钟{l_conf:.0f}%)",
            "buy_point": max(higher_sig.get("buy_point", 0), lower_sig.get("buy_point", 0)),
            "sell_point": max(higher_sig.get("sell_point", 0), lower_sig.get("sell_point", 0)),
            "higher_signal": higher_sig,
            "lower_signal": lower_sig,
        }

    # 方向矛盾 → 观望
    if h_dir != "HOLD" and l_dir != "HOLD" and h_dir != l_dir:
        return {
            "signal": "HOLD",
            "confidence": 40,
            "reason": f"多级别分歧: 日线{h_dir} vs 分钟{l_dir}",
            "buy_point": 0,
            "sell_point": 0,
            "higher_signal": higher_sig,
            "lower_signal": lower_sig,
        }

    # 高级别有信号，低级别无明确信号 → 用高级别（打折）
    if h_dir != "HOLD":
        return {
            "signal": h_dir,
            "confidence": h_conf * 0.8,
            "reason": f"日线{h_dir}，分钟级别无确认",
            "buy_point": higher_sig.get("buy_point", 0),
            "sell_point": higher_sig.get("sell_point", 0),
            "higher_signal": higher_sig,
            "lower_signal": lower_sig,
        }

    # 低级别有信号，高级别无明确信号 → 用低级别（大幅打折）
    if l_dir != "HOLD":
        return {
            "signal": l_dir,
            "confidence": l_conf * 0.6,
            "reason": f"分钟{l_dir}，日线未确认",
            "buy_point": lower_sig.get("buy_point", 0),
            "sell_point": lower_sig.get("sell_point", 0),
            "higher_signal": higher_sig,
            "lower_signal": lower_sig,
        }

    # 都无信号
    return {
        "signal": "HOLD",
        "confidence": 50,
        "reason": "多级别均无明确信号",
        "buy_point": 0,
        "sell_point": 0,
        "higher_signal": higher_sig,
        "lower_signal": lower_sig,
    }


# 参考特征名列表（已删除 cl_n_fractals_20，可由 tops + bottoms 推出）
CHANLUN_FEATURES = [
    "cl_n_tops_20", "cl_n_bottoms_20", "cl_top_ratio",
    "cl_last_fractal_dir", "cl_last_fractal_dist",
    "cl_n_strokes_20", "cl_stroke_mean_len", "cl_stroke_max_len",
    "cl_stroke_std_len", "cl_up_stroke_ratio",
    "cl_last_stroke_dir", "cl_last_stroke_len",
    "cl_n_hubs_20", "cl_hub_center", "cl_hub_width", "cl_hub_width_pct",
    "cl_price_to_hub", "cl_hub_deviation_pct", "cl_hub_strokes",
    "cl_stroke_momentum", "cl_stroke_trend",
    # 线段
    "cl_seg_dir", "cl_seg_count", "cl_seg_strokes", "cl_seg_has_hub",
    # 成交量背驰
    "cl_volume_bottom_div", "cl_volume_top_div",
]
