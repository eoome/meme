#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
训练数据采集脚本

从数据源拉取扩展历史 K 线，保存到 data/klines/ 供标注流水线使用。
默认读取持仓列表，无持仓则读取自选股池。

用法:
  python fetch_training_data.py                    # 默认拉取持仓/自选股
  python fetch_training_data.py 000001 600519      # 指定股票代码
  python fetch_training_data.py --all              # 拉取默认关注列表 (50+只)
  python fetch_training_data.py --count 800        # 自定义获取条数
"""

import sys
import os
import time
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from data_sources.router import fetch_kline_tencent, fetch_kline_eastmoney

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

_DATA_DIR = Path(__file__).resolve().parent / "data"
_KLINES_DIR = _DATA_DIR / "klines"

# 默认关注列表 (50+ 只，覆盖各板块；默认拉取每只 600 条日K ≈ 2.5年数据)
DEFAULT_WATCHLIST = [
    # 银行 (5)
    '000001',  # 平安银行
    '601318',  # 中国平安
    '600036',  # 招商银行
    '601166',  # 兴业银行
    '601398',  # 工商银行
    # 消费 (6)
    '600519',  # 贵州茅台
    '000858',  # 五粮液
    '000568',  # 泸州老窖
    '603288',  # 海天味业
    '002304',  # 洋河股份
    '600887',  # 伊利股份
    # 新能源 (5)
    '300750',  # 宁德时代
    '002594',  # 比亚迪
    '601012',  # 隆基绿能
    '002459',  # 晶澳科技
    '600438',  # 通威股份
    # 科技 (6)
    '002415',  # 海康威视
    '603501',  # 韦尔股份
    '002230',  # 科大讯飞
    '688981',  # 中芯国际
    '000725',  # 京东方A
    '002371',  # 北方华创
    # 医药 (4)
    '600276',  # 恒瑞医药
    '300760',  # 迈瑞医疗
    '000538',  # 云南白药
    '002007',  # 华兰生物
    # 券商 (3)
    '601211',  # 国泰君安
    '600030',  # 中信证券
    '601688',  # 华泰证券
    # 地产 (2)
    '000002',  # 万科A
    '600048',  # 保利发展
    # 电力 (2)
    '600900',  # 长江电力
    '601985',  # 中国核电
    # 军工 (2)
    '600760',  # 中航沈飞
    '002049',  # 紫光国微
    # 有色 (2)
    '601899',  # 紫金矿业
    '603993',  # 洛阳钼业
    # 化工 (2)
    '002601',  # 龙佰集团
    '600309',  # 万华化学
    # 传媒 (2)
    '002027',  # 分众传媒
    '300413',  # 芒果超媒
    # 汽车 (2)
    '600104',  # 上汽集团
    '000625',  # 长安汽车
    # 家电 (2)
    '000333',  # 美的集团
    '000651',  # 格力电器
    # 通信 (1)
    '600050',  # 中国联通
    # ETF (5)
    '510300',  # 沪深300 ETF
    '510500',  # 中证500 ETF
    '159915',  # 创业板 ETF
    '518880',  # 黄金 ETF
    '513100',  # 纳指 ETF
]


def fetch_single(
    code: str,
    count: int = 0,       # 默认0表示从config读取，保证与全局配置一致
    period: str = 'day',
    force: bool = False,
) -> bool:
    """
    拉取单只股票的 K 线数据，保存到 data/klines/

    Args:
        code: 股票代码
        count: 期望获取条数
        period: 周期 (day/week/month)
        force: 是否强制重新拉取（忽略已有文件）

    Returns:
        是否成功
    """
    # count为0时从全局配置读取，避免默认值与配置不一致
    if count <= 0:
        count = _get_kline_count()

    os.makedirs(_KLINES_DIR, exist_ok=True)
    filepath = _KLINES_DIR / f"{code}_{period}.csv"

    # 检查是否已有数据且够新
    if filepath.exists() and not force:
        try:
            existing = pd.read_csv(filepath)
            if len(existing) >= count * 0.8:  # 已有数据达到期望的 80% 就跳过
                logger.info(f"  跳过 {code}: 已有 {len(existing)} 条 (≥{int(count*0.8)})")
                return True
        except Exception:
            pass

    # 拉取数据 (腾讯优先，东财降级)
    klines = fetch_kline_tencent(code, period, count)
    source = 'tencent'

    # ═══════════════════════════════════════════════════════════
    # 数据量检查与降级策略
    # 腾讯API有时返回数据不足，当低于期望的80%时尝试东财
    # 阈值从50%提高到80%，确保获取足够数据用于训练
    # ═══════════════════════════════════════════════════════════
    if len(klines) < count * 0.8:
        # 腾讯数据不足，尝试东财补充
        em_period = {'day': 'daily', 'week': 'weekly', 'month': 'monthly'}
        klines_em = fetch_kline_eastmoney(code, em_period.get(period, 'daily'), count)
        if len(klines_em) > len(klines):
            klines = klines_em
            source = 'eastmoney'

    if not klines:
        logger.warning(f"  ❌ {code}: 无数据")
        return False

    # 转 DataFrame 并保存
    df = pd.DataFrame(klines)
    # 统一列名
    col_map = {'date': 'time'}
    df = df.rename(columns=col_map)

    # 确保列顺序
    expected_cols = ['time', 'open', 'high', 'low', 'close', 'volume']
    for col in expected_cols:
        if col not in df.columns:
            df[col] = 0.0
    df = df[expected_cols]

    df.to_csv(filepath, index=False)

    days = len(df)
    months = days / 20
    logger.info(f"  ✅ {code}: {days} 条 ({source}), {df['time'].iloc[0]} ~ {df['time'].iloc[-1]}, ≈{months:.1f}月")

    return True


def _get_default_codes() -> list:
    """获取默认股票代码列表：优先持仓 → 自选股池 → DEFAULT_WATCHLIST"""
    # 1. 读取持仓
    positions_file = _DATA_DIR / "positions.json"
    if positions_file.exists():
        try:
            positions = json.loads(positions_file.read_text("utf-8"))
            codes = [p["code"] for p in positions if p.get("code")]
            if codes:
                logger.info(f"使用持仓列表: {len(codes)} 只")
                return codes
        except Exception:
            pass

    # 2. 读取自选股池
    try:
        from data.watchlist import get_watchlist_codes
        codes = get_watchlist_codes()
        if codes:
            logger.info(f"使用自选股池: {len(codes)} 只")
            return codes
    except Exception:
        pass

    # 3. 兜底
    logger.info(f"使用默认关注列表: {len(DEFAULT_WATCHLIST)} 只")
    return DEFAULT_WATCHLIST


def _get_kline_count() -> int:
    """从配置读取K线条数，默认800"""
    try:
        from core.config import get_config
        return get_config().data.kline_count
    except Exception:
        return 800


def fetch_all(
    codes: list = None,
    count: int = 0,
    period: str = 'day',
    delay: float = 1.0,
    force: bool = False,
) -> dict:
    """
    批量拉取 K 线数据

    Args:
        codes: 股票代码列表，默认使用持仓/自选股池
        count: 每只股票获取条数，默认从config读取(800)
        period: 周期
        delay: 请求间隔(秒)
        force: 强制重新拉取

    Returns:
        统计信息
    """
    codes = codes or _get_default_codes()
    if count <= 0:
        count = _get_kline_count()

    stats = {'total': len(codes), 'success': 0, 'failed': 0, 'skipped': 0}
    failed_codes = []

    logger.info(f"开始拉取 {len(codes)} 只股票的 {period} K 线 (count={count})")
    logger.info(f"保存目录: {_KLINES_DIR}")
    logger.info(f"{'='*50}")

    for i, code in enumerate(codes):
        logger.info(f"[{i+1}/{len(codes)}] 拉取 {code}...")
        ok = fetch_single(code, count=count, period=period, force=force)
        if ok:
            stats['success'] += 1
        else:
            stats['failed'] += 1
            failed_codes.append(code)

        # 限频
        if i < len(codes) - 1:
            time.sleep(delay)

    # 汇总
    logger.info(f"\n{'='*50}")
    logger.info(f"拉取完成: 成功 {stats['success']}/{stats['total']}, 失败 {stats['failed']}")
    if failed_codes:
        logger.info(f"失败代码: {failed_codes}")

    # 检查数据量
    check_data_quality()

    return stats


def check_data_quality():
    """检查 data/klines/ 中的数据质量"""
    logger.info(f"\n📊 数据质量检查:")
    logger.info(f"{'='*50}")

    if not _KLINES_DIR.exists():
        logger.warning("  data/klines/ 不存在")
        return

    csv_files = list(_KLINES_DIR.glob("*.csv"))
    if not csv_files:
        logger.warning("  无 CSV 文件")
        return

    for f in sorted(csv_files):
        try:
            df = pd.read_csv(f)
            n = len(df)
            months = n / 20

            # 数据覆盖度
            from strategies.data.features import validate_data_sufficiency
            val = validate_data_sufficiency(df, freq='daily')

            status = "✅" if val['ok'] else "⚠️"
            logger.info(f"  {status} {f.stem:<20s}  {n:>5d} 条  ≈{months:>5.1f}月  {val['message']}")
        except Exception as e:
            logger.info(f"  ❌ {f.stem}: {e}")


def run_full_pipeline(codes: list = None, count: int = 0, force: bool = False):
    """
    完整数据流水线: 拉取 → 标注 → 训练
    """
    logger.info("=" * 60)
    logger.info("完整数据流水线: 拉取 → 标注 → 训练")
    logger.info("=" * 60)

    # Step 0: 拉取数据
    logger.info("\n📦 Step 0: 拉取训练数据")
    fetch_all(codes=codes, count=count, force=force)

    # Step 1: 标注
    logger.info("\n🏷️ Step 1: 自动标注")
    from strategies.data.labeler import batch_label
    batch_label()

    # Step 2: 训练
    logger.info("\n🧠 Step 2: 训练模型")
    from strategies.ml.trainer import train_model
    # ═══════════════════════════════════════════════════════════
    # 训练参数必须与 quick_train() 保持一致，否则会导致:
    #   - 模型元数据中的 lookback 与推理时不一致 → 特征维度错误
    #   - use_extended_features 不一致 → 特征列数量不匹配
    # quick_train() 默认: lookback=20, use_extended_features=False
    # ═══════════════════════════════════════════════════════════
    from strategies.ml.trainer import quick_train
    path, metrics = quick_train()
    if path:
        logger.info(f"\n✅ 模型已训练: {path}")
        logger.info(f"   F1: {metrics.get('val_f1', 0):.3f}")
    else:
        logger.error("\n❌ 训练失败: 无有效训练数据或训练异常")

    return path, metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="训练数据采集")
    parser.add_argument('codes', nargs='*', help='股票代码')
    parser.add_argument('--all', action='store_true', help='使用默认关注列表')
    parser.add_argument('--count', type=int, default=0, help='每只股票获取条数 (默认从config读取, 800)')
    parser.add_argument('--force', action='store_true', help='强制重新拉取')
    parser.add_argument('--pipeline', action='store_true', help='拉取+标注+训练一条龙')
    parser.add_argument('--check', action='store_true', help='仅检查数据质量')

    args = parser.parse_args()
    count = args.count if args.count > 0 else _get_kline_count()

    if args.check:
        check_data_quality()
    elif args.pipeline:
        codes = args.codes if args.codes else (DEFAULT_WATCHLIST if args.all else None)
        run_full_pipeline(codes=codes, count=count, force=args.force)
    elif args.all:
        fetch_all(codes=DEFAULT_WATCHLIST, count=count, force=args.force)
    elif args.codes:
        fetch_all(codes=args.codes, count=count, force=args.force)
    else:
        # 无参数：使用持仓/自选股池（fetch_all 内部自动判断）
        fetch_all(count=count, force=args.force)
