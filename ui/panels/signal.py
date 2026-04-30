#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略信号面板 - 使用 QTextEdit + HTML 格式，与日志页面风格一致
支持信号卡（带完整解释的富文本信号）
"""

from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout,
                             QLabel, QTextEdit, QFrame, QPushButton)
from PyQt5.QtCore import Qt, QDateTime, pyqtSignal
from PyQt5.QtGui import QFont

from ui.theme import get_current_colors


class SignalPanel(QFrame):
    """策略信号面板 - 日志风格 + 信号卡"""

    signal_added = pyqtSignal(str, str)  # message, type

    # 样式映射
    _LEVEL_STYLE = {
        "buy":     ("\U0001f7e2", "#4CAF50", "#4CAF50"),  # 绿
        "sell":    ("\U0001f534", "#F44336", "#F44336"),  # 红
        "warning": ("\u26a0\ufe0f", "#e6a23c", "#e6a23c"),  # 黄
        "info":    ("\U0001f4a1", "#1a73e8", "#409eff"),  # 蓝
        "success": ("\u2705", "#4CAF50", "#4CAF50"),
        "error":   ("\u274c", "#F44336", "#F44336"),
    }

    def __init__(self):
        """初始化"""
        super().__init__()
        self._signals = []
        self._max_signals = 100
        self._init_ui()

    def _init_ui(self):
        c = get_current_colors()

        self.setStyleSheet(f"""
            SignalPanel {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # ═══ 标题栏 ═══
        header = QHBoxLayout()

        title = QLabel("\U0001f4cb  \u7b56\u7565\u4fe1\u53f7")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        header.addWidget(title)

        # 实时指示灯
        self._live_dot = QLabel("\u25cf \u5b9e\u65f6")
        self._live_dot.setFont(QFont("Microsoft YaHei", 10))
        self._live_dot.setStyleSheet(f"color: #4CAF50; background: transparent;")
        header.addWidget(self._live_dot)

        header.addStretch()

        # 清空按钮
        clear_btn = QPushButton("\u6e05\u7a7a")
        clear_btn.setFixedSize(42, 26)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent; color: #ccc;
                border: 1px solid #e0e0e0; border-radius: 13px;
                font-size: 11px; font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{ color: #F44336; border-color: #F44336; }}
        """)
        clear_btn.clicked.connect(self.clear_signals)
        header.addWidget(clear_btn)

        layout.addLayout(header)

        # ═══ 信号显示区域 (QTextEdit + HTML) ═══
        self._signal_text = QTextEdit()
        self._signal_text.setReadOnly(True)
        self._signal_text.setFont(QFont("Consolas", 13))
        self._signal_text.setStyleSheet(f"""
            QTextEdit {{
                background: {c.bg_app}; border: 1px solid #f0f0f0;
                border-radius: 6px; padding: 8px 10px;
            }}
        """)
        layout.addWidget(self._signal_text, stretch=1)

    def add_signal(self, message: str, signal_type: str = "info"):
        """添加信号 - HTML 日志风格"""
        icon, bg_color, text_color = self._LEVEL_STYLE.get(
            signal_type, ("\u25cb", "#999", "#999")
        )
        timestamp = QDateTime.currentDateTime().toString("HH:mm:ss")

        html = (
            f'<div style="margin-bottom:8px;padding:7px 10px;">'
            f'<span style="color:#333;font-size:14px;font-family:Consolas,monospace;">{timestamp}</span>'
            f'<span style="color:{text_color};padding:1px 5px;'
            f'font-size:14px;margin-left:6px;">{icon}</span>'
            f'<span style="margin-left:6px;color:#333;font-size:14px;">{message}</span>'
        )
        html += '</div>'

        self._signal_text.append(html)
        sb = self._signal_text.verticalScrollBar()
        sb.setValue(sb.maximum())

        # 保存信号
        self._signals.append({
            'time': timestamp,
            'type': signal_type,
            'message': message
        })

        # 限制数量
        if len(self._signals) > self._max_signals:
            self._signals = self._signals[-self._max_signals:]

        self.signal_added.emit(message, signal_type)

    def add_signal_card(self, code: str, name: str, signal_obj, price: float = 0):
        """
        添加信号卡 — 带完整解释的富文本信号

        Args:
            code: 股票代码
            name: 股票名称
            signal_obj: strategies.signal.Signal 对象（必须有 get_explanation() 方法）
            price: 当前价格
        """
        explanation = signal_obj.get_explanation()
        timestamp = QDateTime.currentDateTime().toString("HH:mm:ss")

        # 信号颜色映射
        sig_color = signal_obj.signal.color
        sig_label = signal_obj.signal.label
        is_actionable = signal_obj.signal.is_buy or signal_obj.signal.is_sell

        # 价格显示
        price_str = f"¥{price:.3f}" if price > 0 else ""
        # 涨跌颜色（从 details 中取，如果没有就用默认色）
        change_pct = explanation['raw_details'].get('change_pct', 0)
        if change_pct > 0:
            price_color = "#16a34a"  # 红涨
        elif change_pct < 0:
            price_color = "#ef4444"  # 绿跌
        else:
            price_color = "#333"

        # ── 构建 HTML ──
        html = f'<div style="margin-bottom:12px;padding:10px 14px;border-left:4px solid {sig_color};background:#fafbfc;border-radius:6px;">'

        # 第一行: 时间 + 标的 + 信号 + 价格
        html += (
            f'<div style="margin-bottom:6px;">'
            f'<span style="color:#666;font-size:12px;font-family:Consolas;">{timestamp}</span>'
            f'<span style="margin-left:10px;font-size:15px;font-weight:bold;color:#333;">{name}</span>'
            f'<span style="margin-left:6px;color:#999;font-size:12px;">{code}</span>'
            f'<span style="margin-left:12px;font-size:14px;font-weight:bold;color:{sig_color};">{sig_label}</span>'
        )
        # 价格（紧跟在信号后面）
        if price_str:
            html += (
                f'<span style="margin-left:12px;font-size:15px;font-weight:bold;color:{price_color};'
                f'background:#f0f0f0;padding:2px 8px;border-radius:4px;">{price_str}</span>'
            )
        html += '</div>'

        # 第二行: 一句话总结
        html += (
            f'<div style="margin-bottom:8px;font-size:14px;color:#333;font-weight:bold;">'
            f'{explanation["summary"]}'
            f'</div>'
        )

        # 第三行: 各因素明细
        if explanation['factors']:
            html += '<div style="margin-bottom:6px;">'
            for f in explanation['factors']:
                dir_color = {
                    'buy': '#16a34a', 'sell': '#ef4444', 'neutral': '#6b7280'
                }.get(f['direction'], '#6b7280')
                html += (
                    f'<div style="margin-bottom:3px;font-size:13px;">'
                    f'<span>{f["icon"]}</span>'
                    f'<span style="color:#666;margin-left:4px;">{f["label"]}:</span>'
                    f'<span style="color:{dir_color};margin-left:4px;font-weight:bold;">{f["value"]}</span>'
                    f'</div>'
                )
            html += '</div>'

        # 第四行: 风控提示
        if explanation['risk_notes']:
            html += '<div style="margin-bottom:4px;">'
            for note in explanation['risk_notes']:
                html += f'<div style="font-size:12px;color:#888;margin-bottom:2px;">{note}</div>'
            html += '</div>'

        # 原始原因（折叠在最后，小字）
        if explanation['reason']:
            html += (
                f'<div style="font-size:11px;color:#aaa;margin-top:4px;'
                f'border-top:1px solid #eee;padding-top:4px;">'
                f'📋 {explanation["reason"]}'
                f'</div>'
            )

        html += '</div>'

        self._signal_text.append(html)
        sb = self._signal_text.verticalScrollBar()
        sb.setValue(sb.maximum())

        # 保存
        self._signals.append({
            'time': timestamp,
            'type': 'buy' if signal_obj.signal.is_buy else ('sell' if signal_obj.signal.is_sell else 'info'),
            'message': f"{name} {sig_label} {price_str}",
            'explanation': explanation,
        })

        if len(self._signals) > self._max_signals:
            self._signals = self._signals[-self._max_signals:]

        sig_type = 'buy' if signal_obj.signal.is_buy else ('sell' if signal_obj.signal.is_sell else 'info')
        self.signal_added.emit(f"{name} {sig_label} {price_str}", sig_type)

    def clear_signals(self):
        """清空所有信号"""
        self._signal_text.clear()
        self._signals.clear()

    def get_signals(self, signal_type: str = None) -> list:
        """获取信号列表"""
        if signal_type:
            return [s for s in self._signals if s['type'] == signal_type]
        return self._signals.copy()


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)

    panel = SignalPanel()
    panel.setFixedSize(400, 500)

    # 添加测试信号
    panel.add_signal("系统启动成功", "success")
    panel.add_signal("贵州茅台 买入信号", "buy")
    panel.add_signal("中国平安 卖出信号", "sell")
    panel.add_signal("价格波动超过5%", "warning")
    panel.add_signal("数据更新完成", "info")

    panel.show()
    sys.exit(app.exec_())
