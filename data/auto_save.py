#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据自动保存管理器
==================
自动保存K线数据、持仓数据、回测结果、模型训练历史等
支持版本控制、压缩存储、增量更新
"""

import gzip
import hashlib
import json
import os
import shutil
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.logger import log

# ─── 路径 (使用 resolve() 安全绝对路径) ───

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_KLINES_DIR = _DATA_DIR / "klines"
_POSITIONS_FILE = _DATA_DIR / "positions.json"
_BACKUP_DIR = _DATA_DIR / "backups"
_BACKTEST_DIR = _DATA_DIR / "backtest_results"
_MODEL_HISTORY_FILE = _DATA_DIR / "ml" / "model_history.json"
_VERSIONS_DIR = _DATA_DIR / "versions"

# 配置参数
MAX_VERSIONS = 30       # 最大保留版本数
COMPRESS_LEVEL = 6      # 压缩级别
AUTO_BACKUP_HOUR = 2    # 自动备份时间 (凌晨2点)


@dataclass
class SaveMetadata:
    """保存元数据"""
    timestamp: str
    data_type: str
    record_count: int
    file_size: int
    checksum: str
    version: int
    is_compressed: bool = True
    is_incremental: bool = False
    parent_version: Optional[int] = None


class DataAutoSaver:
    """
    数据自动保存管理器

    功能:
    1. 自动保存K线数据 (支持增量更新)
    2. 自动保存持仓数据
    3. 自动保存回测结果
    4. 自动保存模型训练历史
    5. 版本控制 (保留最近30个版本)
    6. 压缩存储 (节省空间)
    7. 定时备份
    """

    _instance: Optional["DataAutoSaver"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "DataAutoSaver":
        """单例模式 — 确保全局唯一实例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self._ensure_dirs()
        self._version_counter = self._load_version_counter()
        self._save_lock = threading.RLock()  # 可重入锁，防止 _save_with_version → _get_next_version 死锁
        self._last_backup_date: Optional[object] = None

    def _ensure_dirs(self) -> None:
        for dir_path in [_KLINES_DIR, _BACKUP_DIR, _BACKTEST_DIR,
                         _DATA_DIR / "ml", _VERSIONS_DIR]:
            os.makedirs(dir_path, exist_ok=True)

    def _load_version_counter(self) -> int:
        counter_file = _VERSIONS_DIR / "counter.json"
        if counter_file.exists():
            try:
                return json.loads(counter_file.read_text()).get("counter", 0)
            except Exception:
                pass
        return 0

    def _save_version_counter(self) -> None:
        counter_file = _VERSIONS_DIR / "counter.json"
        counter_file.write_text(json.dumps({"counter": self._version_counter}))

    def _get_next_version(self) -> int:
        with self._save_lock:
            self._version_counter += 1
            self._save_version_counter()
            return self._version_counter

    @staticmethod
    def _calculate_checksum(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

    def _save_with_version(self, data_type: str, data: Any, identifier: str = "") -> SaveMetadata:
        """保存数据并创建版本"""
        with self._save_lock:
            version = self._get_next_version()
            timestamp = datetime.now().isoformat()

            if isinstance(data, pd.DataFrame):
                serialized = data.to_json(orient="records", date_format="iso").encode("utf-8")
            elif isinstance(data, (dict, list)):
                serialized = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
            else:
                # 使用 JSON 序列化替代 pickle，避免 RCE 风险
                try:
                    serialized = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                except (TypeError, ValueError):
                    log.warning("data", f"无法序列化类型 {type(data).__name__}，跳过保存")
                    return SaveMetadata(
                        timestamp=datetime.now().isoformat(),
                        data_type=data_type,
                        record_count=0, file_size=0, checksum="",
                        version=0, is_compressed=False,
                    )

            compressed = gzip.compress(serialized, COMPRESS_LEVEL)
            checksum = self._calculate_checksum(compressed)

            suffix = f"_{identifier}" if identifier else ""
            filename = f"{data_type}{suffix}_v{version}.gz"
            filepath = _VERSIONS_DIR / filename

            filepath.write_bytes(compressed)

            metadata = SaveMetadata(
                timestamp=timestamp,
                data_type=data_type,
                record_count=len(data) if hasattr(data, "__len__") else 1,
                file_size=len(compressed),
                checksum=checksum,
                version=version,
                is_compressed=True,
            )

            meta_file = filepath.with_suffix("").with_suffix(".json")
            meta_file.write_text(json.dumps(asdict(metadata), indent=2))

            self._cleanup_old_versions(data_type, identifier)
            return metadata

    def _cleanup_old_versions(self, data_type: str, identifier: str = "") -> None:
        suffix = f"_{identifier}" if identifier else ""
        pattern = f"{data_type}{suffix}_v*.gz"
        files = sorted(_VERSIONS_DIR.glob(pattern),
                       key=lambda x: x.stat().st_mtime, reverse=True)
        for old_file in files[MAX_VERSIONS:]:
            try:
                old_file.unlink()
                meta = old_file.with_suffix("").with_suffix(".json")
                meta.unlink(missing_ok=True)
            except Exception:
                pass

    # ─── 公开 API ───

    def save_kline_data(self, code: str, df: pd.DataFrame, incremental: bool = True) -> SaveMetadata:
        """保存K线数据（支持增量合并）"""
        df = df.copy()
        df.columns = [c.lower().strip() for c in df.columns]
        if "date" in df.columns:
            df = df.sort_values("date")
        elif "time" in df.columns:
            df = df.sort_values("time")

        if incremental:
            existing = self.load_kline_data(code)
            if existing is not None and len(existing) > 0:
                df = pd.concat([existing, df], ignore_index=True)
                subset = "date" if "date" in df.columns else "time"
                df = df.drop_duplicates(subset=[subset], keep="last")

        metadata = self._save_with_version("kline", df, code)
        csv_path = _KLINES_DIR / f"{code}.csv"
        df.to_csv(csv_path, index=False)
        log.signal_log("data", f"K线已保存: {code}", f"v{metadata.version}, {metadata.record_count}条")
        return metadata

    def load_kline_data(self, code: str) -> Optional[pd.DataFrame]:
        """加载最新K线数据"""
        csv_path = _KLINES_DIR / f"{code}.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path)
                df.columns = [c.lower().strip() for c in df.columns]
                return df
            except Exception as e:
                log.warning("data", f"加载CSV失败: {code}", str(e))

        pattern = f"kline_{code}_v*.gz"
        files = sorted(_VERSIONS_DIR.glob(pattern),
                       key=lambda x: x.stat().st_mtime, reverse=True)
        if files:
            try:
                compressed = files[0].read_bytes()
                serialized = gzip.decompress(compressed)
                df = pd.read_json(serialized.decode("utf-8"), orient="records")
                df.columns = [c.lower().strip() for c in df.columns]
                return df
            except Exception as e:
                log.warning("data", f"加载版本数据失败: {code}", str(e))
        return None

    def save_positions(self, positions: List[Dict]) -> SaveMetadata:
        """保存持仓数据（原子写入）"""
        import tempfile
        metadata = self._save_with_version("position", positions)
        # 原子写入：临时文件 + rename
        dir_name = str(_DATA_DIR)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(positions, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(_POSITIONS_FILE))
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
        log.signal_log("data", f"持仓已保存: {len(positions)}只")
        return metadata

    def load_positions(self) -> List[Dict]:
        """加载持仓数据"""
        if _POSITIONS_FILE.exists():
            try:
                return json.loads(_POSITIONS_FILE.read_text("utf-8"))
            except Exception as e:
                log.warning("data", "加载持仓失败", str(e))
        return []

    def save_backtest_result(self, code: str, result: Dict) -> SaveMetadata:
        """保存回测结果"""
        result["saved_at"] = datetime.now().isoformat()
        metadata = self._save_with_version("backtest", result, code)
        result_file = _BACKTEST_DIR / f"{code}_latest.json"
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        log.signal_log("data", f"回测结果已保存: {code}")
        return metadata

    def load_backtest_result(self, code: str) -> Optional[Dict]:
        """加载最新回测结果"""
        result_file = _BACKTEST_DIR / f"{code}_latest.json"
        if result_file.exists():
            try:
                return json.loads(result_file.read_text("utf-8"))
            except Exception as e:
                log.warning("data", f"加载回测失败: {code}", str(e))
        return None

    def save_model_history(self, history: Dict) -> SaveMetadata:
        """保存模型训练历史"""
        existing = self.load_model_history()
        existing.append(history)
        existing = existing[-100:]
        metadata = self._save_with_version("model", existing)
        _MODEL_HISTORY_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2, default=str))
        log.signal_log("data", f"模型历史已保存: {len(existing)}条")
        return metadata

    def load_model_history(self) -> List[Dict]:
        """加载模型训练历史"""
        if _MODEL_HISTORY_FILE.exists():
            try:
                return json.loads(_MODEL_HISTORY_FILE.read_text("utf-8"))
            except Exception:
                pass
        return []

    def create_backup(self, backup_name: Optional[str] = None) -> str:
        """创建完整备份"""
        if backup_name is None:
            backup_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = _BACKUP_DIR / backup_name
        backup_path.mkdir(parents=True, exist_ok=True)
        data_backup = backup_path / "data"
        if _DATA_DIR.exists():
            shutil.copytree(_DATA_DIR, data_backup, dirs_exist_ok=True)
        info = {
            "name": backup_name,
            "created_at": datetime.now().isoformat(),
            "data_dir": str(data_backup),
            "size_mb": round(sum(f.stat().st_size for f in data_backup.rglob("*") if f.is_file()) / (1024 * 1024), 2),
        }
        (backup_path / "backup_info.json").write_text(json.dumps(info, indent=2))
        log.signal_log("data", f"备份已创建: {backup_name}")
        # 清理 30 天前的旧备份
        try:
            self._cleanup_old_backups()
        except Exception as e:
            log.warning("data", f"清理旧备份失败: {e}")
        return str(backup_path)

    def _cleanup_old_backups(self) -> None:
        cutoff = datetime.now() - timedelta(days=30)
        for d in _BACKUP_DIR.iterdir():
            if d.is_dir():
                try:
                    dir_date = datetime.strptime(d.name[:8], "%Y%m%d")
                    if dir_date < cutoff:
                        shutil.rmtree(d)
                        log.signal_log("data", f"清理旧备份: {d.name}")
                except Exception:
                    pass

# ─── 全局单例 ───

_auto_saver: Optional[DataAutoSaver] = None


def get_auto_saver() -> DataAutoSaver:
    """获取全局自动保存器单例"""
    global _auto_saver
    if _auto_saver is None:
        _auto_saver = DataAutoSaver()
    return _auto_saver



