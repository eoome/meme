#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
顶部状态栏 - Logo / 名称 / 连接状态 / 数据源 / 市场状态 / 实时时钟
"""

from PyQt5.QtWidgets import QWidget, QHBoxLayout, QLabel, QFrame
from PyQt5.QtCore import Qt, QTimer, QDateTime
from PyQt5.QtGui import QFont


class HeaderPanel(QFrame):
    """顶部状态栏 - Logo / 名称 / 连接状态 / 数据源 / 市场状态 / 实时时钟"""

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)
        self.setFixedHeight(52)
        self._init_ui()
        self._start_clock()

    def _init_ui(self):
        self.setStyleSheet("""
            HeaderPanel {
                background-color: #ffffff;
                border-bottom: 1px solid #eee;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(10)

        # ---- 左侧: Logo + 名称 ----
        logo = QLabel("Xm")
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignCenter)
        logo.setFont(QFont("Consolas", 13, QFont.Bold))
        logo.setStyleSheet("""
            QLabel {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a73e8, stop:1 #4facfe);
                color: white;
                border-radius: 8px;
            }
        """)
        layout.addWidget(logo)

        name = QLabel("Xm-LH")
        name.setFont(QFont("Consolas", 15, QFont.Bold))
        name.setStyleSheet("color: #1a1a1a; background: transparent; border: none;")
        layout.addWidget(name)

        version = QLabel("v1.0")
        version.setFont(QFont("Microsoft YaHei", 9))
        version.setStyleSheet("color: #bbb; background: transparent; border: none;")
        layout.addWidget(version)

        layout.addStretch()

        # ---- 市场状态 ----
        self._market_dot = QLabel("\u25cf")
        self._market_dot.setFont(QFont("Microsoft YaHei", 11))
        self._market_dot.setStyleSheet("color: #4CAF50; background: transparent; border: none;")
        layout.addWidget(self._market_dot)

        self._market_status = QLabel("开市中")
        self._market_status.setFont(QFont("Microsoft YaHei", 11))
        self._market_status.setStyleSheet("color: #4CAF50; background: transparent; border: none;")
        layout.addWidget(self._market_status)

        layout.addSpacing(8)

        # ---- 连接状态 ----
        self._status_dot = QLabel("\u25cf")
        self._status_dot.setFont(QFont("Microsoft YaHei", 11))
        self._status_dot.setStyleSheet("color: #4CAF50; background: transparent; border: none;")
        layout.addWidget(self._status_dot)

        self._status_text = QLabel("已连接")
        self._status_text.setFont(QFont("Microsoft YaHei", 11))
        self._status_text.setStyleSheet("color: #4CAF50; background: transparent; border: none;")
        layout.addWidget(self._status_text)

        layout.addSpacing(8)

        # 数据源标签
        self._source_tag = QLabel("\U0001f4e1 腾讯行情")
        self._source_tag.setFont(QFont("Microsoft YaHei", 10))
        self._source_tag.setStyleSheet("""
            QLabel {
                background: transparent;
                color: #999;
                border: none;
            }
        """)
        layout.addWidget(self._source_tag)

        layout.addSpacing(8)

        # ---- 实时时钟 ----
        self._clock_label = QLabel()
        self._clock_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._clock_label.setFont(QFont("Consolas", 12, QFont.Bold))
        self._clock_label.setStyleSheet("color: #333; background: transparent; border: none;")
        layout.addWidget(self._clock_label)

    def _start_clock(self):
        self._update_clock()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_clock)
        self._timer.start(1000)

    def _update_clock(self):
        now = QDateTime.currentDateTime()
        self._clock_label.setText(now.toString("yyyy-MM-dd HH:mm:ss"))

    def set_connected(self, ok=True):
        """设置连接状态 — 更新数据源指示器"""
        if ok:
            self._status_dot.setStyleSheet("color: #4CAF50; background: transparent; border: none;")
            self._status_text.setText("已连接")
            self._status_text.setStyleSheet("color: #4CAF50; background: transparent; border: none;")
        else:
            self._status_dot.setStyleSheet("color: #F44336; background: transparent; border: none;")
            self._status_text.setText("已断开")
            self._status_text.setStyleSheet("color: #F44336; background: transparent; border: none;")

    def set_market_status(self, status="open"):
        """设置市场状态 — open/closed"""
        styles = {
            "open":      ("#4CAF50", "开市中"),
            "closed":    ("#F44336", "已休盘"),
            "suspended": ("#FF9800", "停牌"),
        }
        color, text = styles.get(status, ("#999", "未知"))
        self._market_dot.setStyleSheet(f"color: {color}; background: transparent; border: none;")
        self._market_status.setText(text)
        self._market_status.setStyleSheet(f"color: {color}; background: transparent; border: none;")

    def set_source_label(self, text):
        """设置数据源标签文本"""
        self._source_tag.setText(f"\U0001f4e1 {text}")
