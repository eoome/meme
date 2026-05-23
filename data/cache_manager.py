#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓数据缓存管理器
==================
仅缓存持仓股票的数据，自选股票按需实时拉取，不缓存。

两级缓存架构:
    L1 内存: dict 存储，实时行情 TTL=5s，分钟数据 TTL=60s
    L2 磁盘: JSON 文件存储，持久化，支持增量更新

使用方式:
    from data.cache_manager import get_cache_manager
    cache = get_cache_manager()

    # 持仓变更时更新跟踪列表
    cache.on_position_changed(["510300", "513100"])

    # 获取数据（缓存优先）
    minute_data = cache.get_minute_for_backtest("510300", router)
    realtime = cache.get_realtime("510300", router)
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.logger import log
from utils.numeric import clean_num, clean_kline_list

# ─── 路径 ───
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CACHE_DIR = _DATA_DIR / "cache"  # JSON 文件缓存目录
_META_FILE = _CACHE_DIR / "_meta.json"

# ─── 缓存配置 ───
L1_REALTIME_TTL = 5        # 实时行情内存缓存 5 秒
L1_MINUTE_TTL = 60         # 分钟数据内存缓存 60 秒
L2_MAX_DAYS = 5            # 磁盘最多保留 5 天数据
BATCH_WARM_UP_DELAY = 0.5  # 批量预热间隔（秒），避免限频


def _atomic_write_json(path: Path, data: dict) -> None:
    """原子写入 JSON 文件（临时文件 + rename）"""
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _load_json(path: Path, default=None):
    """安全加载 JSON 文件"""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default if default is not None else {}


class DataCacheManager:
    """
    持仓数据缓存管理器 — 线程安全

    核心原则:
    - 只缓存持仓股票的数据
    - 自选股票不缓存，每次扫描实时拉取
    - 增量更新：只拉取本地没有的新数据
    - 持仓删除时自动清理缓存
    """

    _instance: Optional["DataCacheManager"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "DataCacheManager":
        """单例模式 — 确保全局唯一实例"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        """初始化缓存管理器"""
        # L1 内存缓存: code -> (data, expire_timestamp)
        self._l1_realtime: Dict[str, Tuple[dict, float]] = {}
        self._l1_minute: Dict[str, Tuple[List[dict], float]] = {}
        self._l1_lock = threading.Lock()

        # 跟踪的持仓代码（仅这些会缓存）
        self._tracked_codes: Set[str] = set()
        self._tracked_lock = threading.Lock()

        # L2 磁盘缓存初始化
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._meta = _load_json(_META_FILE)

        log.signal_log("cache", "缓存管理器已初始化",
                       f"L1内存 TTL: 实时{L1_REALTIME_TTL}s/分钟{L1_MINUTE_TTL}s, "
                       f"L2磁盘(JSON): {_CACHE_DIR}")

    # ═══════════════════════════════════════════════════════════
    #  L2 磁盘缓存 (JSON 文件)
    #  每只股票一个文件: data/cache/{code}.json
    # ═══════════════════════════════════════════════════════════

    def _cache_file(self, code: str) -> Path:
        """获取某只股票的缓存文件路径"""
        return _CACHE_DIR / f"{code}.json"

    def _db_save(self, code: str, records: List[dict], overwrite: bool = False) -> None:
        """保存分钟数据到 L2 缓存（JSON 文件）"""
        path = self._cache_file(code)
        if not overwrite and path.exists():
            # 增量合并：加载已有数据，去重后保存
            existing = _load_json(path, {}).get("records", [])
            existing_times = {r.get("time", "") for r in existing}
            merged = existing + [r for r in records if r.get("time", "") not in existing_times]
            merged.sort(key=lambda x: x.get("time", ""))
            records = merged
        else:
            records.sort(key=lambda x: x.get("time", ""))

        if not records:
            return

        data = {
            "code": code,
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "record_count": len(records),
            "data_span": f"{records[0].get('time', '')} ~ {records[-1].get('time', '')}",
            "records": records,
        }
        _atomic_write_json(path, data)

    def _deep_clean_records(self, records: List[dict]) -> List[dict]:
        """深度清洗记录列表 — 将 None/'N'/'-'/'NaN' 等脏值转为合法数字"""
        return clean_kline_list(records)

    def _db_load(self, code: str) -> List[dict]:
        """从 L2 缓存加载分钟数据 — 返回前深度清洗"""
        path = self._cache_file(code)
        records = _load_json(path, {}).get("records", [])
        if not records:
            return records
        return self._deep_clean_records(records)

    def _db_get_latest_time(self, code: str) -> Optional[str]:
        """获取某只股票缓存中最新的时间"""
        records = self._db_load(code)
        return records[-1].get("time", "") if records else None

    def _db_delete(self, code: str) -> None:
        """删除某只股票的所有缓存数据"""
        path = self._cache_file(code)
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        # 从元数据中移除
        self._meta.pop(code, None)
        self._save_meta()

    def _db_cleanup(self) -> int:
        """清理非持仓数据，返回删除文件数"""
        with self._tracked_lock:
            tracked = set(self._tracked_codes)

        deleted = 0
        try:
            for f in _CACHE_DIR.glob("*.json"):
                if f.name == "_meta.json":
                    continue
                code = f.stem
                if code not in tracked:
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception:
                        pass
                    # 同步清理元数据 — 用 code 做 key（与 _update_meta 一致）
                    if code in self._meta:
                        del self._meta[code]
        except Exception:
            pass

        if deleted > 0:
            log.signal_log("cache", f"L2缓存清理完成", f"删除 {deleted} 个非持仓缓存文件")
        return deleted

    def _update_meta(self, code: str, count: int, span: str) -> None:
        """更新元数据"""
        self._meta[code] = {
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "record_count": count,
            "data_span": span,
        }
        self._save_meta()

    def _save_meta(self) -> None:
        """保存元数据文件"""
        try:
            _atomic_write_json(_META_FILE, self._meta)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  L1 内存缓存
    # ═══════════════════════════════════════════════════════════

    def _l1_get_realtime(self, code: str) -> Optional[dict]:
        """L1 获取实时行情（带TTL检查）"""
        with self._l1_lock:
            entry = self._l1_realtime.get(code)
            if entry and time.time() < entry[1]:
                return entry[0]
            self._l1_realtime.pop(code, None)
            return None

    def _l1_set_realtime(self, code: str, data: dict) -> None:
        """L1 写入实时行情"""
        with self._l1_lock:
            self._l1_realtime[code] = (data, time.time() + L1_REALTIME_TTL)

    def _l1_get_minute(self, code: str) -> Optional[List[dict]]:
        """L1 获取分钟数据（带TTL检查）— 返回前深度清洗"""
        with self._l1_lock:
            entry = self._l1_minute.get(code)
            if entry and time.time() < entry[1]:
                return self._deep_clean_records(entry[0])
            self._l1_minute.pop(code, None)
            return None

    def _l1_set_minute(self, code: str, data: List[dict]) -> None:
        """L1 写入分钟数据"""
        with self._l1_lock:
            self._l1_minute[code] = (data, time.time() + L1_MINUTE_TTL)

    def _l1_invalidate(self, code: str) -> None:
        """清除某只股票的 L1 缓存"""
        with self._l1_lock:
            self._l1_realtime.pop(code, None)
            self._l1_minute.pop(code, None)

    # ═══════════════════════════════════════════════════════════
    #  持仓跟踪管理
    # ═══════════════════════════════════════════════════════════

    def on_position_changed(self, codes: List[str]) -> None:
        """
        持仓列表变更时调用
        新增: 加入跟踪列表并触发预热
        移除: 从跟踪列表移除并清理缓存
        """
        new_codes = set(codes)
        with self._tracked_lock:
            old_codes = self._tracked_codes.copy()
            self._tracked_codes = new_codes

        added = new_codes - old_codes
        removed = old_codes - new_codes

        if added:
            log.signal_log("cache", f"持仓新增: {added}", "触发数据预热")
            for code in added:
                self._l1_invalidate(code)

        if removed:
            log.signal_log("cache", f"持仓移除: {removed}", "清理缓存数据")
            for code in removed:
                self._l1_invalidate(code)
                self._db_delete(code)

        if added:
            threading.Thread(target=self._warm_up_batch, args=(list(added),),
                           daemon=True).start()

    def is_tracked(self, code: str) -> bool:
        """判断某股票是否在持仓跟踪列表中"""
        with self._tracked_lock:
            return code in self._tracked_codes

    def get_tracked_codes(self) -> List[str]:
        """获取当前跟踪的持仓代码列表"""
        with self._tracked_lock:
            return list(self._tracked_codes)

    # ═══════════════════════════════════════════════════════════
    #  数据获取接口（缓存优先）
    # ═══════════════════════════════════════════════════════════

    def get_realtime(self, code: str, router) -> Optional[dict]:
        """
        获取实时行情 — L1 缓存优先
        持仓和自选都走缓存（但自选不预热，只在扫描时缓存）
        """
        cached = self._l1_get_realtime(code)
        if cached:
            return cached

        try:
            result = router.get_realtime([code])
            if code in result:
                data = result[code]
                if self.is_tracked(code):
                    self._l1_set_realtime(code, data)
                return data
        except Exception as e:
            log.warning("cache", f"实时行情获取失败: {code}", str(e))

        return None

    def get_minute_for_backtest(self, code: str, router) -> List[dict]:
        """
        获取分钟数据 — L1 → L2 → 网络增量 → 回写缓存
        持仓股票: L1内存 → L2磁盘 → 增量网络 → 回写
        非持仓股票: L2磁盘 → 网络 → 回写L2（不入L1，不加入跟踪列表）
        """
        if not self.is_tracked(code):
            # 非持仓: 先查L2磁盘缓存，有就直接用，不联网
            l2_data = self._db_load(code)
            if l2_data and len(l2_data) >= 20:
                log.signal_log("cache", f"{code} L2命中(非持仓)",
                               f"{len(l2_data)}条")
                return l2_data

            # L2没有，从网络拉取并持久化
            try:
                data = router.get_minute_for_backtest(code)
                if data and len(data) >= 20:
                    self._db_save(code, data, overwrite=True)
                    self._update_meta(code, len(data),
                                      f"{data[0].get('time','')} ~ {data[-1].get('time','')}")
                    log.signal_log("cache", f"{code} 网络拉取+缓存(非持仓)",
                                   f"{len(data)}条")
                return data
            except Exception as e:
                log.warning("cache", f"自选分钟数据获取失败: {code}", str(e))
                return []

        # ═══════════════════════════════════════════════════
        # 持仓股票: L1 → L2(JSON) → 增量网络 → 回写
        # ═══════════════════════════════════════════════════

        l1_data = self._l1_get_minute(code)
        if l1_data and len(l1_data) >= 20:
            return l1_data

        l2_data = self._db_load(code)
        if l2_data and len(l2_data) >= 20:
            latest_cached = l2_data[-1].get("time", "")
            now_str = datetime.now().strftime("%H:%M")

            if self._is_data_fresh(latest_cached, now_str):
                self._l1_set_minute(code, l2_data)
                log.signal_log("cache", f"{code} L2命中",
                               f"{len(l2_data)}条, 最新={latest_cached}")
                return l2_data

            log.signal_log("cache", f"{code} 增量更新",
                           f"缓存最新={latest_cached}")
            incremental = self._fetch_incremental(code, latest_cached, router)
            if incremental:
                merged = l2_data + incremental
                self._db_save(code, incremental, overwrite=False)
                self._update_meta(code, len(merged),
                                  f"{merged[0]['time']} ~ {merged[-1]['time']}")
                self._l1_set_minute(code, merged)
                log.signal_log("cache", f"{code} 增量更新完成",
                               f"新增{len(incremental)}条, 总计{len(merged)}条")
                return merged

            self._l1_set_minute(code, l2_data)
            return l2_data

        log.signal_log("cache", f"{code} L2未命中，全量下载")
        try:
            network_data = router.get_minute_for_backtest(code)
            if network_data and len(network_data) >= 20:
                self._db_save(code, network_data, overwrite=True)
                self._update_meta(code, len(network_data),
                                  f"{network_data[0]['time']} ~ {network_data[-1]['time']}")
                self._l1_set_minute(code, network_data)
                log.signal_log("cache", f"{code} 全量下载完成",
                               f"{len(network_data)}条")
                return network_data
        except Exception as e:
            log.warning("cache", f"{code} 全量下载失败", str(e))

        return l2_data if l2_data else []

    # ═══════════════════════════════════════════════════════════
    #  内部工具
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _is_data_fresh(latest_time: str, now_str: str) -> bool:
        """判断缓存数据是否足够新（同一天内，最后一条在5分钟内）"""
        try:
            # 提取日期部分（如果有）
            date_part = ""
            time_part = latest_time
            if " " in latest_time:
                parts = latest_time.split(" ", 1)
                date_part = parts[0]
                time_part = parts[1]

            # 跨日检查：如果缓存日期不是今天，直接判定为不新鲜
            if date_part:
                today_str = datetime.now().strftime("%Y-%m-%d")
                if date_part != today_str:
                    return False
            else:
                # 无日期部分（如 "09:30"）：无法判断是否为今天，视为不新鲜
                return False

            try:
                from datetime import datetime as dt
                parsed = dt.strptime(latest_time[:16], "%Y-%m-%d %H:%M")
                hour, minute = parsed.hour, parsed.minute
            except (ValueError, IndexError):
                hour, minute = 9, 30  # 解析失败默认开盘时间
            latest_min = hour * 60 + minute
            if len(now_str) >= 4:
                now_min = int(now_str[:2]) * 60 + int(now_str[3:5])
                return abs(latest_min - now_min) <= 5
        except (ValueError, IndexError):
            pass
        return False

    def _fetch_incremental(self, code: str, since_time: str, router) -> List[dict]:
        """增量拉取：尝试获取 since_time 之后的新数据（时间格式安全比较）"""
        try:
            full_data = router.get_minute_for_backtest(code)
            if not full_data:
                return []
            # 统一格式: 去除空格后补齐到16字符再比较，避免 "9:30" > "10:00" 的字符串比较陷阱
            since_normalized = since_time.replace(" ", "").strip()
            since_padded = since_normalized.ljust(16, '0')
            incremental = []
            for d in full_data:
                t = d.get("time", "").replace(" ", "").strip()
                if t.ljust(16, '0') > since_padded:
                    incremental.append(d)
            return incremental
        except Exception as e:
            log.warning("cache", f"{code} 增量拉取失败", str(e))
            return []

    def _warm_up_batch(self, codes: List[str]) -> None:
        """批量预热新持仓数据（后台线程）"""
        if not codes:
            return
        from data_sources.router import DataRouter

        router = DataRouter()
        for code in codes:
            if not self.is_tracked(code):
                continue
            try:
                log.signal_log("cache", f"预热: {code}")
                data = router.get_minute_for_backtest(code)
                if data and len(data) >= 20:
                    self._db_save(code, data, overwrite=True)
                    self._update_meta(code, len(data),
                                      f"{data[0]['time']} ~ {data[-1]['time']}")
                    self._l1_set_minute(code, data)
                    log.signal_log("cache", f"预热完成: {code}",
                                   f"{len(data)}条")
            except Exception as e:
                log.warning("cache", f"预热失败: {code}", str(e))
            time.sleep(BATCH_WARM_UP_DELAY)

    # ═══════════════════════════════════════════════════════════
    #  维护接口
    # ═══════════════════════════════════════════════════════════

    def invalidate(self, code: str) -> None:
        """手动清除某只股票的缓存"""
        self._l1_invalidate(code)
        self._db_delete(code)
        with self._tracked_lock:
            self._tracked_codes.discard(code)
        log.signal_log("cache", f"缓存已清理: {code}")

    def daily_cleanup(self) -> int:
        """每日清理：删除非持仓数据，返回删除文件数"""
        deleted = self._db_cleanup()
        now = time.time()
        with self._l1_lock:
            self._l1_realtime = {
                k: v for k, v in self._l1_realtime.items()
                if v[1] > now
            }
            self._l1_minute = {
                k: v for k, v in self._l1_minute.items()
                if v[1] > now
            }
        return deleted

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        with self._tracked_lock:
            tracked_count = len(self._tracked_codes)
        with self._l1_lock:
            l1_realtime_count = len(self._l1_realtime)
            l1_minute_count = len(self._l1_minute)

        l2_total = 0
        l2_codes = 0
        try:
            for f in _CACHE_DIR.glob("*.json"):
                if f.name == "_meta.json":
                    continue
                l2_codes += 1
                data = _load_json(f, {})
                l2_total += len(data.get("records", []))
        except Exception:
            pass

        cache_size_mb = 0
        try:
            for f in _CACHE_DIR.glob("*.json"):
                cache_size_mb += f.stat().st_size
            cache_size_mb = round(cache_size_mb / (1024 * 1024), 2)
        except Exception:
            pass

        return {
            "tracked_codes": tracked_count,
            "l1_realtime_entries": l1_realtime_count,
            "l1_minute_entries": l1_minute_count,
            "l2_total_records": l2_total,
            "l2_distinct_codes": l2_codes,
            "cache_size_mb": cache_size_mb,
        }


# ═══════════════════════════════════════════════════════════════
#  全局单例
# ═══════════════════════════════════════════════════════════════

_cache_manager: Optional[DataCacheManager] = None
_cache_lock = threading.Lock()


def get_cache_manager() -> DataCacheManager:
    """获取全局缓存管理器实例"""
    global _cache_manager
    if _cache_manager is None:
        with _cache_lock:
            if _cache_manager is None:
                _cache_manager = DataCacheManager()
    return _cache_manager


def on_position_changed(codes: List[str]) -> None:
    """持仓列表变更通知"""
    get_cache_manager().on_position_changed(codes)


def get_minute_for_backtest(code: str, router) -> List[dict]:
    """获取分钟数据（缓存优先）"""
    return get_cache_manager().get_minute_for_backtest(code, router)


def get_realtime(code: str, router) -> Optional[dict]:
    """获取实时行情（L1缓存）"""
    return get_cache_manager().get_realtime(code, router)


def invalidate(code: str) -> None:
    """清除缓存"""
    get_cache_manager().invalidate(code)


def daily_cleanup() -> int:
    """每日清理"""
    return get_cache_manager().daily_cleanup()


def get_stats() -> dict:
    """缓存统计"""
    return get_cache_manager().get_stats()
