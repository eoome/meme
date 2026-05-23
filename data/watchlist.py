#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自选股池管理
============
管理 ETF T+0 扫描池，支持全量 ETF 自动入池、手动增删。
"""

import json
import os
import time
from pathlib import Path
from typing import List, Tuple, Optional, Dict

from core.logger import log

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_WATCHLIST_FILE = _DATA_DIR / "watchlist.json"
_ETF_CACHE_FILE = _DATA_DIR / "etf_pool.json"

# ETF 缓存有效期 (秒) — 1天
ETF_CACHE_TTL = 24 * 3600


def load_watchlist() -> List[Dict]:
    """
    加载自选股池
    返回: [{"code": "513100", "name": "纳指ETF", "type": "etf"}, ...]
    """
    if not _WATCHLIST_FILE.exists():
        return []

    try:
        raw = _WATCHLIST_FILE.read_bytes()
    except Exception:
        return []

    if not raw:
        return []

    # ── 优先尝试 UTF-8 (带/不带 BOM) ──
    try:
        text = raw.decode("utf-8-sig")  # 自动去掉 BOM
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    # ── 回退: 尝试 GBK 系列编码 → 修复为 UTF-8 ──
    for enc in ("gbk", "gb2312", "gb18030", "latin-1"):
        try:
            data = json.loads(raw.decode(enc))
            save_watchlist(data)
            log.signal_log("watchlist", f"自选池文件编码修复: {enc} → utf-8")
            return data
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

    log.warning("watchlist", "自选池文件编码损坏，无法加载，请检查 data/watchlist.json")
    return []


def save_watchlist(items: List[Dict]) -> None:
    """保存自选股池 (UTF-8 无 BOM)"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _WATCHLIST_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def add_to_watchlist(code: str, name: str, item_type: str = "etf") -> bool:
    """添加到自选股池"""
    items = load_watchlist()
    if any(i["code"] == code for i in items):
        return False
    items.append({"code": code, "name": name, "type": item_type, "added_at": time.strftime("%Y-%m-%d %H:%M")})
    save_watchlist(items)
    return True


def remove_from_watchlist(code: str) -> bool:
    """从自选股池移除"""
    items = load_watchlist()
    new_items = [i for i in items if i["code"] != code]
    if len(new_items) == len(items):
        return False
    save_watchlist(new_items)
    return True


def get_watchlist_codes() -> List[str]:
    """获取自选股池代码列表"""
    return [i["code"] for i in load_watchlist()]


# ─── ETF T+0 全量池 ───

def fetch_all_etfs() -> List[Tuple[str, str, str]]:
    """
    从东方财富拉取全部 ETF 列表
    返回: [(code, name, market), ...]
    """
    try:
        from data_sources.router import fetch_stock_list_eastmoney
        all_stocks = fetch_stock_list_eastmoney()
        # 过滤 ETF: sector == "ETF" 或代码以 5/1 开头的6位
        etfs = []
        for code, name, sector in all_stocks:
            if sector == "ETF" or _is_etf_code(code):
                etfs.append((code, name, "ETF"))
        return etfs
    except Exception as e:
        log.warning("watchlist", f"拉取ETF列表失败: {e}")
        return []


def _is_etf_code(code: str) -> bool:
    """粗判是否为 ETF 代码"""
    if len(code) != 6 or not code.isdigit():
        return False
    # 沪市: 51xxxx/52xxxx/56xxxx/58xxxx  深市: 15xxxx/16xxxx
    return code[:2] in ("51", "52", "56", "58", "15", "16")


def load_etf_pool_cached() -> List[Tuple[str, str, str]]:
    """加载 ETF 缓存"""
    if _ETF_CACHE_FILE.exists():
        try:
            age = time.time() - os.path.getmtime(_ETF_CACHE_FILE)
            if age < ETF_CACHE_TTL:
                data = json.loads(_ETF_CACHE_FILE.read_text("utf-8"))
                return [(d["code"], d["name"], d.get("market", "ETF")) for d in data]
        except Exception:
            pass
    return []


def save_etf_pool_cache(etfs: List[Tuple[str, str, str]]) -> None:
    """保存 ETF 缓存"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = [{"code": c, "name": n, "market": m} for c, n, m in etfs]
    _ETF_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False))


def get_etf_pool(force_refresh: bool = False) -> List[Tuple[str, str, str]]:
    """
    获取 ETF T+0 全量池 (有缓存用缓存，无缓存拉一次)
    """
    if not force_refresh:
        cached = load_etf_pool_cached()
        if cached:
            return cached

    etfs = fetch_all_etfs()
    if etfs:
        save_etf_pool_cache(etfs)
        log.signal_log("watchlist", f"ETF池已更新: {len(etfs)} 只")
    return etfs


def init_etf_watchlist() -> int:
    """
    一键初始化: 拉取全部 ETF 并写入自选股池
    返回: 新增数量
    """
    etfs = get_etf_pool()
    if not etfs:
        log.warning("watchlist", "无 ETF 数据，无法初始化")
        return 0

    existing = {i["code"] for i in load_watchlist()}
    new_items = []
    for code, name, _ in etfs:
        if code not in existing:
            new_items.append({
                "code": code,
                "name": name,
                "type": "etf",
                "added_at": time.strftime("%Y-%m-%d %H:%M"),
            })

    if new_items:
        all_items = load_watchlist() + new_items
        save_watchlist(all_items)

    log.signal_log("watchlist", f"ETF自选池初始化完成: 新增 {len(new_items)} 只，总计 {len(load_watchlist())} 只")
    return len(new_items)
