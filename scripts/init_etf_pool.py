#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键初始化 ETF T+0 自选股池

用法:
  python scripts/init_etf_pool.py          # 初始化（增量添加）
  python scripts/init_etf_pool.py --refresh # 强制刷新 ETF 列表
  python scripts/init_etf_pool.py --show    # 查看当前自选池
  python scripts/init_etf_pool.py --clear   # 清空自选池
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    """程序主入口 — 从web获取ETF池数据并保存"""
    args = sys.argv[1:]

    if "--show" in args:
        from data.watchlist import load_watchlist
        items = load_watchlist()
        if not items:
            print("自选池为空")
            return
        print(f"自选池: {len(items)} 只")
        print("-" * 50)
        for i, item in enumerate(items, 1):
            print(f"  {i:4d}. {item['code']}  {item['name']:<12s}  [{item.get('type', 'etf')}]  添加于 {item.get('added_at', '-')}")
        return

    if "--clear" in args:
        from data.watchlist import save_watchlist
        save_watchlist([])
        print("✅ 自选池已清空")
        return

    force = "--refresh" in args

    print("=" * 50)
    print("ETF T+0 自选股池初始化")
    print("=" * 50)

    from data.watchlist import init_etf_watchlist, get_etf_pool, load_watchlist

    if force:
        print("强制刷新 ETF 列表...")
        etfs = get_etf_pool(force_refresh=True)
        print(f"  获取到 {len(etfs)} 只 ETF")
    else:
        etfs = get_etf_pool()
        print(f"  ETF 池: {len(etfs)} 只 (缓存)")

    added = init_etf_watchlist()
    total = len(load_watchlist())

    print(f"\n✅ 完成!")
    print(f"  新增: {added} 只")
    print(f"  总计: {total} 只")
    print(f"\n启动 Advisor 后会自动扫描自选池中的 ETF 信号")


if __name__ == "__main__":
    main()
