#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据自动下载/清理工具
====================

添加股票时自动下载数据，删除股票时自动清理数据。
所有下载在后台线程执行，不阻塞UI。

使用方式:
    from utils.data_fetcher import auto_download_stock_data, auto_cleanup_stock_data

    # 后台线程中调用
    result = auto_download_stock_data("510300", "沪深300ETF")
    log_text = format_download_report(result)
    # → "✅ 510300 沪深300ETF | 日K:已下载600条(腾讯) | 分钟:已有280条"
"""

import os
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── 路径 ───
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_KLINES_DIR = _DATA_DIR / "klines"
_CACHE_DIR = _DATA_DIR / "cache"
_POSITIONS_FILE = _DATA_DIR / "positions.json"
_WATCHLIST_FILE = _DATA_DIR / "watchlist.json"

# ─── 下载量配置 (从 config 读取，此处为兜底默认值) ───
_KLINE_COUNT_DEFAULT = 800

def _get_kline_count() -> int:
    """从配置读取K线条数"""
    try:
        from core.config import get_config
        return get_config().data.kline_count
    except Exception:
        return _KLINE_COUNT_DEFAULT

DOWNLOAD_CONFIG = {
    "add": {
        "kline_count": _KLINE_COUNT_DEFAULT,
        "kline_desc": f"{_KLINE_COUNT_DEFAULT}根≈{_KLINE_COUNT_DEFAULT/240:.1f}年",
    },
    "training": {
        "kline_count": _KLINE_COUNT_DEFAULT,
        "kline_desc": f"{_KLINE_COUNT_DEFAULT}根≈{_KLINE_COUNT_DEFAULT/240:.1f}年",
    },
}

# ─── 数据最低标准 ───
KLINE_MIN_FOR_TRAINING = 500     # 训练最低要求: 500根日K ≈ 2年
KLINE_SKIP_THRESHOLD = 200       # 本地已有≥200根 → 跳过下载
MINUTE_SKIP_THRESHOLD = 20       # 分钟缓存已有≥20条 → 跳过下载


def auto_download_stock_data(code: str, name: str = "", scene: str = "add") -> Dict:
    """
    自动下载单只股票数据 (应在后台线程调用)

    Args:
        code: 股票代码 (6位数字)
        name: 股票名称
        scene: 场景 "add"(添加) / "training"(训练)

    Returns:
        {
            "code": "510300",
            "name": "沪深300ETF",
            "kline": {"status": "downloaded"/"skipped"/"failed", "count": 300, "source": "tencent"},
            "minute": {"status": "downloaded"/"skipped"/"failed", "count": 280},
        }
    """
    from data_sources.router import DataRouter

    kline_target = _get_kline_count()  # 始终从 config 实时读取

    result = {
        "code": code,
        "name": name or code,
        "kline": {"status": "failed", "count": 0, "source": ""},
        "minute": {"status": "failed", "count": 0},
    }

    router = DataRouter()

    # ═══ 1. 日K线 ═══
    kline_result = _download_kline(code, router, kline_target)
    result["kline"] = kline_result

    # ═══ 2. 分钟数据 (通过缓存管理器) ═══
    minute_result = _download_minute(code, router)
    result["minute"] = minute_result

    # ═══ 3. 竞态保护: 下载完成后检查股票是否还在列表中 ═══
    # 防止"添加→下载→删除→下载完成→数据残留"的情况
    if not _check_in_use(code):
        # 下载期间用户已删除该股票，清理刚下载的数据
        _cleanup_files(code)
        result["kline"]["status"] = "cancelled"
        result["minute"]["status"] = "cancelled"

    return result


def _cleanup_files(code: str) -> None:
    """静默删除数据文件 (内部用)"""
    kline_path = _KLINES_DIR / f"{code}.csv"
    cache_path = _CACHE_DIR / f"{code}.json"
    try:
        kline_path.unlink(missing_ok=True)
    except Exception:
        pass
    try:
        cache_path.unlink(missing_ok=True)
    except Exception:
        pass


def _download_kline(code: str, router, target_count: int) -> Dict:
    """下载日K线数据"""
    import pandas as pd

    csv_path = _KLINES_DIR / f"{code}.csv"

    # 检查本地是否已有足够数据
    if csv_path.exists():
        try:
            existing = pd.read_csv(csv_path)
            count = len(existing)
            if count >= KLINE_SKIP_THRESHOLD:
                return {"status": "skipped", "count": count, "source": "local"}
        except Exception:
            pass

    # 联网下载
    try:
        _KLINES_DIR.mkdir(parents=True, exist_ok=True)
        klines = router.get_kline(code, period="day", count=target_count)
        if klines and len(klines) >= 50:
            df = pd.DataFrame(klines)
            df.columns = [c.lower().strip() for c in df.columns]
            # 确保必要列存在
            for col in ['open', 'high', 'low', 'close', 'volume']:
                if col not in df.columns:
                    df[col] = 0
            if 'date' not in df.columns and 'time' in df.columns:
                df = df.rename(columns={'time': 'date'})
            df.to_csv(csv_path, index=False)
            source = "tencent" if len(klines) >= target_count * 0.8 else "eastmoney"
            return {"status": "downloaded", "count": len(klines), "source": source}
        else:
            count = len(klines) if klines else 0
            if count > 0:
                # 有一些数据但不够50条，仍然保存（可能有用）
                df = pd.DataFrame(klines)
                df.columns = [c.lower().strip() for c in df.columns]
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col not in df.columns:
                        df[col] = 0
                df.to_csv(csv_path, index=False)
            return {"status": "failed", "count": count, "source": "insufficient"}
    except Exception as e:
        logger.debug(f"日K下载失败 {code}: {e}")
        return {"status": "failed", "count": 0, "source": str(e)[:40]}


def _download_minute(code: str, router) -> Dict:
    """下载分钟数据 (通过缓存管理器)"""
    try:
        from data.cache_manager import get_cache_manager
        cache = get_cache_manager()

        # 检查 L2 缓存是否已有足够数据
        l2_data = cache._db_load(code)
        if l2_data and len(l2_data) >= MINUTE_SKIP_THRESHOLD:
            return {"status": "skipped", "count": len(l2_data)}

        # 通过缓存管理器获取 (会自动写入 L2)
        data = cache.get_minute_for_backtest(code, router)
        if data and len(data) >= 10:
            return {"status": "downloaded", "count": len(data)}
        else:
            return {"status": "failed", "count": len(data) if data else 0}
    except Exception as e:
        logger.debug(f"分钟数据下载失败 {code}: {e}")
        return {"status": "failed", "count": 0}


def format_download_report(result: Dict) -> Tuple[str, str]:
    """
    格式化下载结果为日志文本

    Returns:
        (main_msg, detail_msg) 元组
        main_msg: "✅ 510300 沪深300ETF 数据就绪"
        detail_msg: "日K:已下载600条(腾讯) | 分钟:已有280条"
    """
    code = result["code"]
    name = result["name"]
    kl = result["kline"]
    mn = result["minute"]

    # 竞态取消
    if kl.get("status") == "cancelled" or mn.get("status") == "cancelled":
        return f"🚫 {code} {name} 已取消(股票已移除)", "下载已中止，数据已清理"

    # 日K 状态
    if kl["status"] == "downloaded":
        kl_text = f"已下载{kl['count']}条({kl.get('source', '?')})"
    elif kl["status"] == "skipped":
        kl_text = f"已有{kl['count']}条(跳过)"
    else:
        kl_text = f"失败({kl.get('source', '无数据')})"

    # 分钟状态
    if mn["status"] == "downloaded":
        mn_text = f"已下载{mn['count']}条"
    elif mn["status"] == "skipped":
        mn_text = f"已有{mn['count']}条(跳过)"
    else:
        mn_text = "失败"

    detail = f"日K:{kl_text} │ 分钟:{mn_text}"

    # 主状态
    kl_ok = kl["status"] in ("downloaded", "skipped") and kl["count"] >= 50
    mn_ok = mn["status"] in ("downloaded", "skipped") and mn["count"] >= 10

    if kl_ok and mn_ok:
        main = f"✅ {code} {name} 数据就绪"
    elif kl_ok:
        main = f"⚠️ {code} {name} 日K已就绪(分钟不足)"
    elif mn_ok:
        main = f"⚠️ {code} {name} 分钟已就绪(日K不足)"
    else:
        main = f"❌ {code} {name} 数据下载失败"

    return main, detail


def auto_cleanup_stock_data(code: str) -> Dict:
    """
    清理单只股票数据

    检查该股票是否还在持仓/回测/ETF池中:
    - 三处都不在 → 删除日K CSV + 分钟缓存 JSON
    - 还在某处 → 跳过，返回 "still_in_use"

    Returns:
        {"code": "510300", "action": "deleted"/"skipped"/"partial",
         "reason": "不在任何列表中"/"仍在持仓中", ...}
    """
    result = {
        "code": code,
        "action": "skipped",
        "reason": "",
        "kline_deleted": False,
        "minute_deleted": False,
    }

    # 检查是否还在使用中
    in_use_reasons = _check_in_use(code)

    if in_use_reasons:
        result["action"] = "skipped"
        result["reason"] = f"仍在: {', '.join(in_use_reasons)}"
        return result

    # 都不在了，清理数据
    kline_path = _KLINES_DIR / f"{code}.csv"
    cache_path = _CACHE_DIR / f"{code}.json"

    if kline_path.exists():
        try:
            kline_path.unlink()
            result["kline_deleted"] = True
        except Exception as e:
            logger.debug(f"删除日K失败 {code}: {e}")

    if cache_path.exists():
        try:
            cache_path.unlink()
            result["minute_deleted"] = True
        except Exception as e:
            logger.debug(f"删除分钟缓存失败 {code}: {e}")

    if result["kline_deleted"] or result["minute_deleted"]:
        result["action"] = "deleted"
        result["reason"] = "已清理"
    else:
        result["action"] = "skipped"
        result["reason"] = "无数据文件"

    return result


def _check_in_use(code: str) -> List[str]:
    """
    检查股票是否还在持仓/回测/ETF池中

    Returns:
        还在的列表名，如 ["持仓", "ETF池"]
    """
    reasons = []

    # 检查持仓
    if _POSITIONS_FILE.exists():
        try:
            positions = json.loads(_POSITIONS_FILE.read_text("utf-8"))
            if any(p.get("code") == code for p in positions):
                reasons.append("持仓")
        except Exception:
            pass

    # 检查 ETF 自选池
    if _WATCHLIST_FILE.exists():
        try:
            watchlist = json.loads(_WATCHLIST_FILE.read_text("utf-8-sig"))
            if any(w.get("code") == code for w in watchlist):
                reasons.append("ETF池")
        except Exception:
            pass

    return reasons


def format_cleanup_report(result: Dict) -> str:
    """格式化清理结果为日志文本"""
    code = result["code"]

    if result["action"] == "deleted":
        parts = []
        if result["kline_deleted"]:
            parts.append("日K已删除")
        if result["minute_deleted"]:
            parts.append("分钟缓存已删除")
        return f"🗑️ {code} 数据清理: {', '.join(parts)}"

    elif result["action"] == "skipped":
        return f"🔒 {code} 数据保留: {result['reason']}"

    return f"ℹ️ {code} {result['reason']}"


# ═══ 批量下载 (用于全量导入) ═══

def auto_download_batch(codes_and_names: List[Tuple[str, str]],
                        scene: str = "add",
                        delay: float = 0.5,
                        progress_callback=None) -> List[Dict]:
    """
    批量下载多只股票数据

    Args:
        codes_and_names: [(code, name), ...]
        scene: 场景
        delay: 每只间隔秒数 (防限频)
        progress_callback: fn(current, total, code, result) 可选回调

    Returns:
        [result_dict, ...]
    """
    results = []
    total = len(codes_and_names)

    for i, (code, name) in enumerate(codes_and_names):
        try:
            result = auto_download_stock_data(code, name, scene=scene)
            results.append(result)
            if progress_callback:
                progress_callback(i + 1, total, code, result)
        except Exception as e:
            logger.debug(f"批量下载异常 {code}: {e}")
            results.append({
                "code": code, "name": name,
                "kline": {"status": "failed", "count": 0, "source": str(e)[:40]},
                "minute": {"status": "failed", "count": 0},
            })

        if i < total - 1:
            time.sleep(delay)

    return results


def check_training_data_ready() -> Dict:
    """
    检查训练数据是否满足最低要求

    Returns:
        {
            "ready": bool,
            "total_files": int,
            "sufficient_files": int,  # ≥500条的文件数
            "min_required": 500,
            "message": str,
        }
    """
    import pandas as pd

    if not _KLINES_DIR.exists():
        return {
            "ready": False, "total_files": 0, "sufficient_files": 0,
            "min_required": KLINE_MIN_FOR_TRAINING,
            "message": "❌ 日K数据目录不存在，请先添加持仓或导入数据",
        }

    csv_files = list(_KLINES_DIR.glob("*.csv"))
    if not csv_files:
        return {
            "ready": False, "total_files": 0, "sufficient_files": 0,
            "min_required": KLINE_MIN_FOR_TRAINING,
            "message": "❌ 无日K数据文件，请先添加持仓或导入数据",
        }

    sufficient = 0
    total = len(csv_files)
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            if len(df) >= KLINE_MIN_FOR_TRAINING:
                sufficient += 1
        except Exception:
            pass

    # 至少1只股票数据充足即可训练（不要求3只，用户可能只有1-2只持仓）
    ready = sufficient >= 1
    if ready:
        msg = f"✅ {sufficient}/{total} 只股票日K≥{KLINE_MIN_FOR_TRAINING}条，可以开始训练"
    else:
        msg = f"⚠️ 0/{total} 只股票日K≥{KLINE_MIN_FOR_TRAINING}条，请先添加持仓并等待数据下载完成"

    return {
        "ready": ready,
        "total_files": total,
        "sufficient_files": sufficient,
        "min_required": KLINE_MIN_FOR_TRAINING,
        "message": msg,
    }
