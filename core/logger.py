#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轻量日志系统
============

级别: WARNING / ERROR / SIGNAL
- WARNING: 数据源降级、超时、异常但能恢复
- ERROR: 数据源全部失败、文件读写异常
- SIGNAL: 策略触发、持仓变动

线程安全，环形缓冲区存储最近 500 条
UI 通过 pyqtSignal 订阅实时更新

改进:
- 同时接入 Python 标准 logging 模块，统一日志输出
- 文件日志支持轮转（默认 10MB × 5 个备份）
"""

import logging
import logging.handlers
import os
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from collections import deque

# ─── 文件日志配置（轮转） ───
_LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "meme.log"

# 配置 stdlib logging（控制台 + 文件轮转）
_stdlog = logging.getLogger("meme")
_stdlog.setLevel(logging.DEBUG)

if not _stdlog.handlers:
    # 控制台输出
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    _stdlog.addHandler(_console_handler)

    # 文件轮转输出（10MB × 5 个备份）
    _file_handler = logging.handlers.RotatingFileHandler(
        str(_LOG_FILE),
        maxBytes=10 * 1024 * 1024,   # 10MB
        backupCount=5,                # 保留5个备份
        encoding='utf-8',
    )
    _file_handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _stdlog.addHandler(_file_handler)

# PyQt5 signal for UI binding (optional, graceful fallback)
try:
    from PyQt5.QtCore import QObject, pyqtSignal
    _HAS_QT = True
except ImportError:
    _HAS_QT = False


# ─── Log entry ───

class LogEntry:
    """单条日志记录 — 支持树状关联（交易信号链追踪）"""
    __slots__ = ("time", "level", "category", "message", "detail")

    def __init__(self, level: str, category: str, message: str, detail: str = ""):
        """初始化"""
        self.time = datetime.now().strftime("%H:%M:%S")
        self.level = level        # "warning" / "error" / "signal"
        self.category = category  # "data" / "strategy" / "position" / "system"
        self.message = message
        self.detail = detail


# ─── Logger singleton ───

if _HAS_QT:
    class _LogEmitter(QObject):
        """Qt signal emitter for UI subscription"""
        new_log = pyqtSignal(object)  # emits LogEntry
else:
    _LogEmitter = None


class Logger:
    """全局日志器 — 线程安全，支持3级别4分类"""
    _instance = None
    _lock = Lock()

    def __new__(cls):
        """单例模式 — 确保全局唯一实例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self._buffer = deque(maxlen=500)
        self._buf_lock = Lock()
        self._emitter = None
        if _HAS_QT:
            try:
                self._emitter = _LogEmitter()
            except Exception:
                pass

    @property
    def signal(self):
        """返回 Qt signal 用于 UI 订阅，无 Qt 时返回 None"""
        return self._emitter.new_log if self._emitter else None

    def _add(self, level: str, category: str, message: str, detail: str = ""):
        entry = LogEntry(level, category, message, detail)
        with self._buf_lock:
            self._buffer.append(entry)
        if self._emitter:
            try:
                self._emitter.new_log.emit(entry)
            except Exception:
                pass

        # 同时写入 stdlib logging
        full_msg = f"[{category}] {message}" + (f" | {detail}" if detail else "")
        if level == "error":
            _stdlog.error(full_msg)
        elif level == "warning":
            _stdlog.warning(full_msg)
        else:
            _stdlog.info(full_msg)

    def info(self, category: str, message: str, detail: str = ""):
        """常规运行信息：持仓变化、缓存预热、扫描完成等"""
        self._add("info", category, message, detail)

    def warning(self, category: str, message: str, detail: str = ""):
        """数据源降级、超时等可恢复异常"""
        self._add("warning", category, message, detail)

    def error(self, category: str, message: str, detail: str = ""):
        """严重错误: 数据源全挂、文件读写失败"""
        self._add("error", category, message, detail)

    def signal_log(self, category: str, message: str, detail: str = ""):
        """策略触发、持仓变动"""
        self._add("signal", category, message, detail)

    def debug(self, category: str, message: str, detail: str = ""):
        """调试信息：仅写入 stdlib logging，不进入 UI 缓冲区"""
        full_msg = f"[{category}] {message}" + (f" | {detail}" if detail else "")
        _stdlog.debug(full_msg)

    def get_recent(self, count: int = 100, level: str = None):
        """
        获取最近的日志
        count: 最多返回条数
        level: None=全部, "warning"/"error"/"signal" 过滤
        """
        with self._buf_lock:
            entries = list(self._buffer)

        if level:
            entries = [e for e in entries if e.level == level]

        return entries[-count:]

    def clear(self):
        """清空内容"""
        with self._buf_lock:
            self._buffer.clear()


# ─── Global instance ───

log = Logger()
