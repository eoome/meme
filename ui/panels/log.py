#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志页 - 交易日志 + 操作日志 上下分栏，操作日志实时订阅 Logger
"""

import json
from pathlib import Path

from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QTextEdit, QFrame)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont

from core.logger import log


class LogPanel(QFrame):
    """日志页 - 交易日志 + 操作日志 上下分栏，操作日志实时订阅 Logger"""

    # level → 样式映射
    _LEVEL_STYLE = {
        "warning": ("\u26a0\ufe0f", "#e6a23c", "#e6a23c"),
        "error":   ("\u274c",       "#F44336", "#F44336"),
        "signal":  ("\U0001f4a1",   "#1a73e8", "#409eff"),
    }

    def __init__(self):
        """初始化"""
        super().__init__()
        self._init_ui()
        self._connect_logger()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        page_title = QLabel("\u65e5\u5fd7")
        page_title.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        page_title.setStyleSheet("color: #333;")
        layout.addWidget(page_title)

        # ---- 上半: 交易日志 ----
        trade_card = QFrame()
        trade_card.setStyleSheet("QFrame { background: #fafbfc; border: 1px solid #eee; border-radius: 10px; }")
        tv = QVBoxLayout(trade_card)
        tv.setContentsMargins(14, 10, 14, 10)
        tv.setSpacing(8)

        theader = QHBoxLayout()
        tlbl = QLabel("\U0001f4b0  \u4ea4\u6613\u65e5\u5fd7")
        tlbl.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        tlbl.setStyleSheet("color: #333; background: transparent;")
        theader.addWidget(tlbl)
        theader.addStretch()
        tv.addLayout(theader)

        self.trade_log = QTextEdit()
        self.trade_log.setReadOnly(True)
        self.trade_log.setFont(QFont("Consolas", 13))
        self.trade_log.setStyleSheet("""
            QTextEdit {
                background: white; border: 1px solid #f0f0f0;
                border-radius: 6px; padding: 8px 10px;
            }
        """)
        tv.addWidget(self.trade_log)
        layout.addWidget(trade_card, stretch=3)

        # ---- 下半: 操作日志 (实时) ----
        op_card = QFrame()
        op_card.setStyleSheet("QFrame { background: #fafbfc; border: 1px solid #eee; border-radius: 10px; }")
        ov = QVBoxLayout(op_card)
        ov.setContentsMargins(14, 10, 14, 10)
        ov.setSpacing(8)

        oheader = QHBoxLayout()
        olbl = QLabel("\U0001f4dd  \u64cd\u4f5c\u65e5\u5fd7")
        olbl.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        olbl.setStyleSheet("color: #333; background: transparent;")
        oheader.addWidget(olbl)

        # 实时指示灯
        self._live_dot = QLabel("\u25cf \u5b9e\u65f6")
        self._live_dot.setFont(QFont("Microsoft YaHei", 10))
        self._live_dot.setStyleSheet("color: #4CAF50; background: transparent;")
        oheader.addWidget(self._live_dot)
        oheader.addSpacing(12)

        # 筛选按钮
        self._op_filter_btns = []
        self._op_filter_level = None  # None = 全部
        for text, level, color in [
            ("\u5168\u90e8",    None,       "#1a73e8"),
            ("\u26a0 \u6570\u636e", "warning", "#e6a23c"),
            ("\U0001f9e0 \u7b56\u7565", "signal",  "#409eff"),
            ("\u274c \u5f02\u5e38", "error",   "#F44336"),
        ]:
            btn = QPushButton(text)
            btn.setCheckable(True)
            if text == "\u5168\u90e8":
                btn.setChecked(True)
            btn.setFixedSize(64, 26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent; color: #bbb;
                    border: 1px solid #e0e0e0; border-radius: 13px;
                    font-size: 11px; font-family: "Microsoft YaHei";
                }}
                QPushButton:hover {{ color: #666; border-color: #ccc; }}
                QPushButton:checked {{
                    background-color: {color}; color: white;
                    border: 1px solid {color};
                }}
            """)
            btn.clicked.connect(lambda checked, l=level: self._set_op_filter(l))
            oheader.addWidget(btn)
            self._op_filter_btns.append((btn, level))
            oheader.addSpacing(4)

        # 清空按钮
        clear_btn = QPushButton("\u6e05\u7a7a")
        clear_btn.setFixedSize(42, 26)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #ccc;
                border: 1px solid #e0e0e0; border-radius: 13px;
                font-size: 11px; font-family: "Microsoft YaHei";
            }
            QPushButton:hover { color: #F44336; border-color: #F44336; }
        """)
        clear_btn.clicked.connect(self._clear_op_log)
        oheader.addWidget(clear_btn)

        ov.addLayout(oheader)

        self.op_log = QTextEdit()
        self.op_log.setReadOnly(True)
        self.op_log.setFont(QFont("Consolas", 13))
        self.op_log.setStyleSheet("""
            QTextEdit {
                background: white; border: 1px solid #f0f0f0;
                border-radius: 6px; padding: 8px 10px;
            }
        """)
        ov.addWidget(self.op_log)
        layout.addWidget(op_card, stretch=2)

        # 加载真实交易日志
        self._load_trade_logs()

        # 定时刷新交易日志（10秒）
        self._trade_timer = QTimer(self)
        self._trade_timer.timeout.connect(self._load_trade_logs)
        self._trade_timer.start(10000)

    # ---- Logger 订阅 ----

    def _connect_logger(self):
        self._logger = log
        if log.signal:
            log.signal.connect(self._on_new_log)
        for entry in log.get_recent(50):
            self._append_op_entry(entry)

    def _on_new_log(self, entry):
        """Logger 新日志回调 (主线程)"""
        self._append_op_entry(entry)

    def _append_op_entry(self, entry):
        """追加一条操作日志"""
        if self._op_filter_level is not None and entry.level != self._op_filter_level:
            return

        icon, bg_color, text_color = self._LEVEL_STYLE.get(
            entry.level, ("\u25cb", "#999", "#999")
        )

        cat_labels = {
            "data":     "\u6570\u636e",
            "strategy": "\u7b56\u7565",
            "position": "\u6301\u4ed3",
            "system":   "\u7cfb\u7edf",
        }
        cat_label = cat_labels.get(entry.category, entry.category)

        html = (
            f'<div style="margin-bottom:8px;padding:7px 10px;border-radius:5px;'
            f'background:{bg_color}08;border-left:3px solid {bg_color};">'
            f'<span style="color:#bbb;font-size:14px;font-family:Consolas,monospace;">{entry.time}</span>'
            f'<span style="background:{bg_color}15;color:{bg_color};padding:1px 5px;'
            f'border-radius:3px;font-size:14px;margin-left:6px;">{icon} {cat_label}</span>'
            f'<span style="margin-left:6px;color:#333;font-size:14px;">{entry.message}</span>'
        )
        if entry.detail:
            html += f'<div style="color:#888;font-size:13px;margin-top:3px;padding-left:4px;">{entry.detail}</div>'
        html += '</div>'

        self.op_log.append(html)
        sb = self.op_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_op_filter(self, level):
        """切换操作日志筛选"""
        self._op_filter_level = level
        for btn, lv in self._op_filter_btns:
            btn.setChecked(lv == level)
        self._reload_op_log()

    def _reload_op_log(self):
        """按当前筛选重新加载操作日志"""
        self.op_log.clear()
        level = self._op_filter_level
        entries = self._logger.get_recent(100, level=level)
        for entry in entries:
            self._append_op_entry_raw(entry)

    def _append_op_entry_raw(self, entry):
        """追加一条 (不检查筛选，内部用)"""
        icon, bg_color, text_color = self._LEVEL_STYLE.get(entry.level, ("\u25cb", "#999", "#999"))
        cat_labels = {"data": "\u6570\u636e", "strategy": "\u7b56\u7565", "position": "\u6301\u4ed3", "system": "\u7cfb\u7edf"}
        cat_label = cat_labels.get(entry.category, entry.category)

        html = (
            f'<div style="margin-bottom:8px;padding:7px 10px;border-radius:5px;'
            f'background:{bg_color}08;border-left:3px solid {bg_color};">'
            f'<span style="color:#bbb;font-size:14px;font-family:Consolas,monospace;">{entry.time}</span>'
            f'<span style="background:{bg_color}15;color:{bg_color};padding:1px 5px;'
            f'border-radius:3px;font-size:14px;margin-left:6px;">{icon} {cat_label}</span>'
            f'<span style="margin-left:6px;color:#333;font-size:14px;">{entry.message}</span>'
        )
        if entry.detail:
            html += f'<div style="color:#888;font-size:13px;margin-top:3px;padding-left:4px;">{entry.detail}</div>'
        html += '</div>'
        self.op_log.append(html)

    def _clear_op_log(self):
        self.op_log.clear()
        self._logger.clear()

    # ---- 交易日志：从真实数据源加载 ----

    def _load_trade_logs(self):
        """从 advisor_log.json 和 signal monitor 加载真实交易记录"""
        self.trade_log.clear()
        entries = []

        # 1. 从 advisor_log.json 加载（顾问建议记录）
        try:
            advisor_file = Path(__file__).resolve().parent.parent.parent / "data" / "advisor_log.json"
            if advisor_file.exists():
                with open(advisor_file, 'r', encoding='utf-8') as f:
                    advisor_logs = json.load(f)
                    # 只显示非HOLD的真实信号，按时间倒序
                    for log_entry in reversed(advisor_logs[-50:]):
                        action = log_entry.get("action", "")
                        if action in ("HOLD", ""):
                            continue
                        entries.append({
                            "time": log_entry.get("time", ""),
                            "action": action,
                            "code": log_entry.get("code", ""),
                            "name": log_entry.get("name", ""),
                            "price": log_entry.get("price", 0),
                            "cost": log_entry.get("cost", 0),
                            "pnl_pct": log_entry.get("pnl_pct", 0),
                            "confidence": log_entry.get("confidence", 0),
                            "reason": log_entry.get("reason", ""),
                        })
        except Exception:
            pass

        # 2. 从 SignalMonitor 加载（当日信号）
        try:
            from strategies.monitor import get_monitor
            monitor = get_monitor()
            recent = monitor.get_recent_signals(30)
            for sig in reversed(recent):
                action = sig.get("action", "")
                if action in ("HOLD", ""):
                    continue
                entries.append({
                    "time": f"{sig.get('timestamp', '')}",
                    "action": action,
                    "code": sig.get("code", ""),
                    "name": sig.get("name", ""),
                    "price": 0,
                    "cost": 0,
                    "pnl_pct": sig.get("pnl_pct", 0),
                    "confidence": sig.get("confidence", 0),
                    "reason": sig.get("reason", ""),
                })
        except Exception:
            pass

        # 按时间排序（最新的在前面）
        entries.sort(key=lambda x: x.get("time", ""), reverse=True)

        if not entries:
            self.trade_log.setHtml(
                '<div style="color:#999;font-size:14px;text-align:center;padding:40px 0;">'
                '📭 暂无交易信号<br><br>'
                '<span style="font-size:12px;">交易信号将在顾问扫描后显示</span>'
                '</div>'
            )
            return

        for entry in entries[:50]:
            self._append_trade_entry(entry)

    def _append_trade_entry(self, entry: dict):
        """渲染单条交易记录"""
        action = entry.get("action", "")
        action_config = {
            "BUY":        ("买入", "#4CAF50", "🟢"),
            "SELL":       ("卖出", "#F44336", "🔴"),
            "STOP_LOSS":  ("止损", "#FF5722", "🛑"),
            "TAKE_PROFIT":("止盈", "#2196F3", "🎯"),
            "STRONG_BUY": ("强买", "#2E7D32", "🔥"),
            "STRONG_SELL":("强卖", "#C62828", "🔥"),
        }
        action_label, color, icon = action_config.get(
            action, (action, "#999", "⚪")
        )

        ts = entry.get("time", "")
        code = entry.get("code", "")
        name = entry.get("name", "")
        price = entry.get("price", 0)
        pnl = entry.get("pnl_pct", 0)
        conf = entry.get("confidence", 0)
        reason = entry.get("reason", "")[:40]

        # 价格显示
        price_str = f"@{price:.2f}" if price > 0 else ""
        # 盈亏显示
        pnl_color = "#4CAF50" if pnl > 0 else "#F44336" if pnl < 0 else "#666"
        pnl_str = f"盈亏{pnl:+.1f}%" if pnl != 0 else ""

        html = (
            f'<div style="margin-bottom:8px;padding:7px 10px;border-radius:5px;'
            f'background:white;border-left:3px solid {color};">'
            f'<span style="color:#bbb;font-size:13px;font-family:Consolas,monospace;">{ts}</span>'
            f'<span style="background:{color}15;color:{color};padding:1px 6px;'
            f'border-radius:3px;font-size:13px;margin-left:6px;font-weight:bold;">'
            f'{icon} {action_label}</span>'
            f'<span style="margin-left:8px;color:#333;font-size:13px;font-weight:500;">'
            f'{code} {name}</span>'
        )
        if price_str:
            html += f'<span style="color:#666;font-size:13px;margin-left:6px;">{price_str}</span>'
        if pnl_str:
            html += f'<span style="color:{pnl_color};font-size:13px;margin-left:6px;">{pnl_str}</span>'
        if conf > 0:
            html += f'<span style="color:#2196F3;font-size:12px;margin-left:6px;">信心{conf:.0f}%</span>'
        if reason:
            html += f'<div style="color:#888;font-size:12px;margin-top:3px;padding-left:4px;">{reason}</div>'
        html += '</div>'

        self.trade_log.append(html)
