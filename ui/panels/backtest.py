#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回测分析页 v3.0 — 可折叠面板版
============================

布局:
- 顶部工具栏 (搜索 + 回测按钮，进度条贴底)
- 可折叠面板: 总览仪表盘 / 个股回测 / 交易记录
- 个股回测: 紧凑行 + 点击展开详情
- 主题自适应
"""

import os
import json
import base64
import threading
from threading import Thread
from datetime import datetime

from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QPushButton, QFrame, QWidget,
                             QScrollArea, QSizePolicy, QProgressBar,
                             QTabWidget, QTableWidget, QTableWidgetItem,
                             QHeaderView)
from PyQt5.QtCore import (Qt, pyqtSignal, pyqtSlot, QSize, QTimer)
from PyQt5.QtGui import QFont, QColor, QPainter, QPen
from PyQt5.QtWebEngineWidgets import QWebEngineView

from data_sources import DataRouter
from core.logger import log
from ui.theme import ThemeManager, get_current_colors
from ui.animations import NumberRollAnimation
from ui.panels.strategy import MetricCard
from ui.panels.search import SearchInput
from utils.data_fetcher import auto_download_stock_data, auto_cleanup_stock_data, format_download_report, format_cleanup_report


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data"
)
_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets"
)
_ECHARTS_TAG_CACHE = None


def _echarts_script_tag():
    """获取ECharts脚本标签"""
    global _ECHARTS_TAG_CACHE
    if _ECHARTS_TAG_CACHE is not None:
        return _ECHARTS_TAG_CACHE
    js_path = os.path.join(_ASSETS_DIR, "echarts.min.js")
    if os.path.exists(js_path):
        with open(js_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        _ECHARTS_TAG_CACHE = (
            '<script>document.write(\'<script src="data:application/javascript;base64,'
            + b64
            + '"><\\/script>\');</script>'
        )
    else:
        _ECHARTS_TAG_CACHE = '<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>'
    return _ECHARTS_TAG_CACHE


def _load_positions():
    """加载持仓数据"""
    path = os.path.join(_DATA_DIR, "positions.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [(d["code"], d["name"], d["volume"], d["cost"]) for d in data]
    except Exception:
        return []


def _c(val, pos="#22c55e", neg="#ef4444"):
    """根据值返回颜色"""
    return pos if val >= 0 else neg


# ═══════════════════════════════════════════════════════════════
# 可折叠面板容器
# ═══════════════════════════════════════════════════════════════

class CollapsiblePanel(QFrame):
    """可折叠面板 — 带箭头标题栏 + 动画展开/收起"""

    def __init__(self, title="", icon="", badge="", subtitle="", parent=None):
        """初始化"""
        super().__init__(parent)
        self._is_open = True
        self._title = title
        self._icon = icon
        self._badge_text = badge
        self._subtitle = subtitle
        self._init_ui()

    def _init_ui(self):
        c = get_current_colors()

        self.setStyleSheet(f"""
            CollapsiblePanel {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ═══ 标题栏 ═══
        self._header = QFrame()
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setStyleSheet(f"""
            QFrame {{
                background: transparent;
                border: none;
                border-radius: 12px;
            }}
            QFrame:hover {{
                background: {c.bg_hover};
            }}
        """)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(20, 14, 20, 14)
        header_layout.setSpacing(10)

        # 箭头
        self._arrow = QLabel("▶")
        self._arrow.setFont(QFont("Microsoft YaHei", 10))
        self._arrow.setStyleSheet(f"color: {c.text_muted}; background: transparent; border: none;")
        self._arrow.setFixedWidth(16)
        self._arrow.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(self._arrow)

        # 图标+标题
        self._title_lbl = QLabel(f"{self._icon} {self._title}" if self._icon else self._title)
        self._title_lbl.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        self._title_lbl.setStyleSheet(f"color: {c.text_primary}; background: transparent; border: none;")
        header_layout.addWidget(self._title_lbl)

        # Badge
        if self._badge_text:
            self._badge = QLabel(self._badge_text)
            self._badge.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
            self._badge.setStyleSheet(f"""
                background: {c.accent};
                color: white;
                border-radius: 10px;
                padding: 2px 8px;
                border: none;
            """)
            header_layout.addWidget(self._badge)
        else:
            self._badge = None

        header_layout.addStretch()

        # 副标题
        self._subtitle_lbl = QLabel(self._subtitle)
        self._subtitle_lbl.setFont(QFont("Microsoft YaHei", 11))
        self._subtitle_lbl.setStyleSheet(f"color: {c.text_muted}; background: transparent; border: none;")
        header_layout.addWidget(self._subtitle_lbl)

        root.addWidget(self._header)
        self._header.mousePressEvent = lambda e: self.toggle()

        # ═══ 内容区域 ═══
        self._body = QFrame()
        self._body.setStyleSheet("background: transparent; border: none;")
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(20, 4, 20, 20)
        self._body_layout.setSpacing(4)
        root.addWidget(self._body)

    @property
    def body_layout(self):
        """获取内容区域布局 — 用于动态添加子控件"""
        return self._body_layout

    def set_subtitle(self, text):
        """设置副标题文本"""
        self._subtitle = text
        self._subtitle_lbl.setText(text)

    def set_badge(self, text):
        """设置徽标文本与颜色"""
        if self._badge:
            self._badge.setText(text)

    def toggle(self):
        """切换展开/折叠状态"""
        self._is_open = not self._is_open
        self._body.setVisible(self._is_open)
        # 旋转箭头
        self._arrow.setText("▼" if self._is_open else "▶")

    def set_open(self, open_state):
        """设置展开/折叠状态"""
        self._is_open = open_state
        self._body.setVisible(open_state)
        self._arrow.setText("▼" if open_state else "▶")


# ═══════════════════════════════════════════════════════════════
# 总览仪表盘 - 4 个 MetricCard
# ═══════════════════════════════════════════════════════════════

class SummaryDashboard(QFrame):
    """总览: 4个MetricCard"""

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)
        self._cards = []
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("background: transparent;")

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(12)

        card_configs = [
            ("持仓数量", "📦"),
            ("平均胜率", "🎯"),
            ("总超额收益", "📈"),
            ("总交易笔数", "🔄"),
        ]

        for title, icon in card_configs:
            card = MetricCard(title, icon)
            self._cards.append(card)
            self._layout.addWidget(card)

    def update_data(self, count, avg_wr, total_excess, total_trades):
        """更新数据 — 刷新显示内容"""
        excess_color = "#34d399" if total_excess >= 0 else "#f87171"

        self._cards[0].set_value(f"{count}")
        self._cards[1].set_value(f"{avg_wr:.0f}%")
        self._cards[2].set_value(f"{total_excess:+.2f}%", excess_color)
        self._cards[3].set_value(f"{total_trades}")


# ═══════════════════════════════════════════════════════════════
# 个股紧凑行 (列表中的一行)
# ═══════════════════════════════════════════════════════════════

class StockSummaryRow(QFrame):
    """个股紧凑行 — 显示代码+名称+4项核心指标"""

    clicked = pyqtSignal(str, str)       # code, name
    remove_clicked = pyqtSignal(str)      # code

    def __init__(self, code, name, volume, cost, parent=None):
        """初始化"""
        super().__init__(parent)
        self.code = code
        self.name = name
        self.volume = volume
        self.cost = cost
        self._is_selected = False
        self._is_removable = False
        self._result = None
        self._init_ui()

    def set_removable(self, removable: bool):
        """设置是否可删除"""
        self._is_removable = removable
        if hasattr(self, '_remove_btn'):
            self._remove_btn.setVisible(removable)

    def _init_ui(self):
        c = get_current_colors()

        self.setFixedHeight(56)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style(selected=False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 12, 0)
        layout.setSpacing(14)

        # 代码 badge
        self._code_badge = QLabel(self.code)
        self._code_badge.setFont(QFont("Consolas", 12, QFont.Bold))
        self._code_badge.setStyleSheet(f"""
            background: transparent;
            color: {c.accent};
            padding: 4px 10px;
        """)
        layout.addWidget(self._code_badge)

        # 名称
        self._name_lbl = QLabel(self.name)
        self._name_lbl.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        self._name_lbl.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        self._name_lbl.setMinimumWidth(70)
        layout.addWidget(self._name_lbl)

        # 持仓标签 (非持仓显示"模拟")
        if self.volume > 0:
            vol_text = f"{self.volume:,}股 · ¥{self.cost:.2f}"
        else:
            vol_text = "模拟回测"
        self._vol_lbl = QLabel(vol_text)
        self._vol_lbl.setFont(QFont("Microsoft YaHei", 11))
        self._vol_lbl.setStyleSheet(f"color: {c.text_muted}; background: transparent;")
        layout.addWidget(self._vol_lbl)

        layout.addStretch()

        # 4项指标 (初始占位) — 标签:数值 横排
        self._metric_labels = []
        metric_names = ["收益", "回撤", "夏普", "胜率"]
        for i, name in enumerate(metric_names):
            ml = QLabel(f"{name}:")
            ml.setFont(QFont("Microsoft YaHei", 10))
            ml.setStyleSheet(f"color: {c.text_muted}; background: transparent;")

            mv = QLabel("—")
            mv.setFont(QFont("DIN Alternate", 13, QFont.Bold))
            mv.setStyleSheet(f"color: {c.text_muted}; background: transparent;")

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 50, 0)
            row.setSpacing(4)
            row.addWidget(ml)
            row.addWidget(mv)

            container = QFrame()
            container.setStyleSheet("background: transparent;")
            container.setLayout(row)
            layout.addWidget(container)

            self._metric_labels.append(mv)

        # 删除按钮
        self._remove_btn = QPushButton("✕")
        self._remove_btn.setFixedSize(24, 24)
        self._remove_btn.setCursor(Qt.PointingHandCursor)
        self._remove_btn.setVisible(False)
        self._remove_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {c.text_muted};
                border: 1px solid {c.border};
                border-radius: 12px;
                font-size: 11px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {c.red_bg};
                color: {c.red};
                border-color: {c.red};
            }}
        """)
        self._remove_btn.clicked.connect(lambda: self.remove_clicked.emit(self.code))
        layout.addWidget(self._remove_btn)

        # 展开箭头
        self._expand_arrow = QLabel("▶")
        self._expand_arrow.setFont(QFont("Microsoft YaHei", 9))
        self._expand_arrow.setStyleSheet(f"color: {c.text_muted}; background: transparent;")
        layout.addWidget(self._expand_arrow)

    def _apply_style(self, selected=False):
        c = get_current_colors()
        if selected:
            self.setStyleSheet(f"""
                StockSummaryRow {{
                    background: transparent;
                    border: none;
                    border-bottom: 1px solid {c.border};
                }}
            """)
        else:
            self.setStyleSheet(f"""
                StockSummaryRow {{
                    background: transparent;
                    border: none;
                    border-bottom: 1px solid {c.divider};
                }}
                StockSummaryRow:hover {{
                    background: {c.bg_hover};
                }}
            """)

    def set_selected(self, selected):
        """设置选中状态"""
        self._is_selected = selected
        self._apply_style(selected)
        self._expand_arrow.setText("▼" if selected else "▶")

    def update_result(self, result_dict):
        """更新紧凑行的4项指标（支持异常状态显示）"""
        c = get_current_colors()
        self._result = result_dict

        # 如果回测异常，显示异常状态而非0值
        if result_dict.get('error_msg'):
            for lbl in self._metric_labels:
                lbl.setText("异常")
                lbl.setStyleSheet(f"color: {c.red}; background: transparent;")
            return

        r = result_dict
        ret_pct = r['total_return'] * 100
        dd_pct = abs(r['max_drawdown']) * 100
        sharpe = r['sharpe_ratio']
        win_rate = r['win_rate'] * 100

        metrics = [
            (f"{ret_pct:+.2f}%", c.green if ret_pct >= 0 else c.red),
            (f"-{dd_pct:.1f}%", c.red if dd_pct > 5 else c.orange),
            (f"{sharpe:.2f}", c.blue if sharpe > 0 else c.red),
            (f"{win_rate:.0f}%", c.purple),
        ]

        for i, (value, color) in enumerate(metrics):
            if i < len(self._metric_labels):
                self._metric_labels[i].setText(value)
                self._metric_labels[i].setStyleSheet(f"color: {color}; background: transparent;")

    def set_loading(self):
        """设置加载状态 — 显示/隐藏loading动画"""
        for lbl in self._metric_labels:
            lbl.setText("…")
            c = get_current_colors()
            lbl.setStyleSheet(f"color: {c.text_muted}; background: transparent;")

    def mousePressEvent(self, event):
        """鼠标按下事件 — PyQt5交互回调"""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.code, self.name)


# ═══════════════════════════════════════════════════════════════
# 个股详情区 (展开后显示)
# ═══════════════════════════════════════════════════════════════

class StockDetailPanel(QFrame):
    """个股展开详情 — 5指标大卡片 + 收益曲线 + 费用汇总"""

    def __init__(self, code, name, parent=None):
        """初始化"""
        super().__init__(parent)
        self.code = code
        self.name = name
        self._result = None
        self._init_ui()

    def _init_ui(self):
        c = get_current_colors()

        self.setStyleSheet(f"""
            StockDetailPanel {{
                background: {c.bg_card};
                border: 1.5px solid {c.border};
                border-top: none;
                border-radius: 0 0 10px 10px;
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 16)
        root.setSpacing(14)

        # ═══ 5项指标大卡片 ═══
        self._metrics_container = QFrame()
        self._metrics_container.setStyleSheet(f"""
            background: {c.bg_app};
            border-radius: 10px;
        """)
        metrics_layout = QHBoxLayout(self._metrics_container)
        metrics_layout.setSpacing(0)
        metrics_layout.setContentsMargins(8, 10, 8, 10)

        self._metric_widgets = []
        for _ in range(5):
            ph = self._make_metric("—", "—", "", c.text_muted)
            self._metric_widgets.append(ph)
            metrics_layout.addWidget(ph)

        root.addWidget(self._metrics_container)

        # ═══ 收益曲线 ═══
        self._chart_view = QWebEngineView()
        self._chart_view.setFixedHeight(200)
        self._chart_view.setStyleSheet(f"""
            background: {c.bg_app};
            border-radius: 10px;
        """)
        root.addWidget(self._chart_view)

        # ═══ 费用 + 风控汇总 ═══
        self._cost_info_lbl = QLabel(" ")
        self._cost_info_lbl.setFont(QFont("Microsoft YaHei", 10))
        self._cost_info_lbl.setStyleSheet(f"color: {c.text_muted}; background: transparent;")
        self._cost_info_lbl.setWordWrap(True)
        root.addWidget(self._cost_info_lbl)

    def _make_metric(self, label, value, sub, color):
        c = get_current_colors()

        f = QFrame()
        f.setStyleSheet("background: transparent;")
        lo = QVBoxLayout(f)
        lo.setContentsMargins(10, 4, 10, 4)
        lo.setSpacing(4)
        lo.setAlignment(Qt.AlignCenter)

        v = QLabel(value)
        v.setFont(QFont("DIN Alternate", 20, QFont.Bold))
        v.setStyleSheet(f"color: {color}; background: transparent;")
        v.setAlignment(Qt.AlignCenter)
        lo.addWidget(v)

        l = QLabel(label)
        l.setFont(QFont("Microsoft YaHei", 10))
        l.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        l.setAlignment(Qt.AlignCenter)
        lo.addWidget(l)

        if sub:
            s = QLabel(sub)
            s.setFont(QFont("Microsoft YaHei", 9))
            s.setStyleSheet(f"color: {color}99; background: transparent;")
            s.setAlignment(Qt.AlignCenter)
            lo.addWidget(s)

        return f

    def update_result(self, result_dict):
        """更新详情面板"""
        c = get_current_colors()
        self._result = result_dict

        r = result_dict
        ret_pct = r['total_return'] * 100
        dd_pct = abs(r['max_drawdown']) * 100
        sharpe = r['sharpe_ratio']
        win_rate = r['win_rate'] * 100
        trades = r['total_trades']
        hold_pct = r['hold_only_return'] * 100
        excess = ret_pct - hold_pct

        excess_str = f"超额 +{excess:.2f}%" if excess > 0 else f"弱基准 {excess:.2f}%"
        dd_str = "极低" if dd_pct < 2 else ("可控" if dd_pct < 5 else "偏大")
        sharpe_str = "卓越" if sharpe > 2 else ("优秀" if sharpe > 1 else ("正向" if sharpe > 0 else "风险高"))

        metrics = [
            (f"{ret_pct:+.2f}%", "策略收益", excess_str,
             c.green if ret_pct >= 0 else c.red),
            (f"{dd_pct:.2f}%", "最大回撤", dd_str,
             c.red if dd_pct > 5 else c.orange),
            (f"{sharpe:.2f}", "夏普比率", sharpe_str,
             c.blue if sharpe > 0 else c.red),
            (f"{win_rate:.0f}%", "胜率", f"{trades}笔",
             c.purple),
            (f"{r.get('avg_daily_pnl', 0):+.0f}", "日均收益", "元",
             c.green if r.get('avg_daily_pnl', 0) >= 0 else c.red),
        ]

        for i, (value, label, sub, color) in enumerate(metrics):
            if i < len(self._metric_widgets):
                old = self._metric_widgets[i]
                new = self._make_metric(label, value, sub, color)
                self._metric_widgets[i] = new
                self._metrics_container.layout().removeWidget(old)
                old.setParent(None)
                old.deleteLater()
                self._metrics_container.layout().insertWidget(i, new)

        # 费用 + 风控 + 蒙特卡洛
        comm = r['total_commission']
        tax = r['total_tax']
        total_cost = comm + tax
        cost_ratio = r['cost_ratio'] * 100
        sl_triggered = r.get('stop_loss_triggered', 0)
        mc_prob = r.get('mc_prob_positive', 0)
        extra_parts = [
            f"佣金 ¥{comm:.2f}  ·  印花税 ¥{tax:.2f}  ·  "
            f"合计 ¥{total_cost:.2f}  ·  占毛利 {cost_ratio:.1f}%",
        ]
        if sl_triggered > 0:
            extra_parts.append(f"止损触发 {sl_triggered} 次")
        calmar = r.get('calmar_ratio', 0)
        sortino = r.get('sortino_ratio', 0)
        if calmar or sortino:
            extra_parts.append(f"Calmar {calmar:.2f}  Sortino {sortino:.2f}")
        if mc_prob:
            extra_parts.append(f"MC盈利概率 {mc_prob:.0%}")
        self._cost_info_lbl.setText("  ·  ".join(extra_parts))

        self._render_chart(r)

    def _render_chart(self, r):
        import json as _json
        c = get_current_colors()

        equity = r.get('equity_curve', [])
        if not equity:
            self._chart_view.setHtml(f"""
                <div style='
                    text-align:center;
                    padding:80px 0;
                    color:{c.text_muted};
                    font-family:Microsoft YaHei;
                    font-size:14px;
                    background:{c.bg_app};
                    border-radius:10px;
                '>暂无曲线数据</div>
            """)
            return

        times = [e[0] for e in equity]
        values = [round(e[1], 2) for e in equity]

        base_start = values[0] if values else 1000000
        hold_return = r['hold_only_return']
        hold_values = [
            round(base_start * (1 + hold_return * i / max(len(values) - 1, 1)), 2)
            for i in range(len(values))
        ]

        short_times = [t[11:16] if len(t) >= 16 else t for t in times]

        is_dark = c.bg_app.startswith("#0f") or c.bg_app.startswith("#1a")
        text_color = "#e0e0e0" if is_dark else "#64748b"
        grid_color = "#334155" if is_dark else "#f1f5f9"
        bg_color = c.bg_app

        html = f"""
        <!DOCTYPE html><html><head>
        <meta charset="utf-8">
        {_echarts_script_tag()}
        <style>
            body {{ margin:0; overflow:hidden; background:{bg_color}; border-radius:10px; }}
            #main {{ width:100%; height:200px; }}
        </style>
        </head><body>
        <div id="main"></div>
        <script>
            var chart = echarts.init(document.getElementById('main'));
            chart.setOption({{
                backgroundColor: '{bg_color}',
                grid: {{ left: 52, right: 16, top: 12, bottom: 22 }},
                tooltip: {{
                    trigger: 'axis',
                    backgroundColor: '{c.bg_surface}',
                    borderColor: '{c.border}',
                    borderWidth: 1,
                    textStyle: {{ color: '{c.text_primary}', fontSize: 11 }},
                    formatter: function(p) {{
                        var s = '<div style="color:{c.text_secondary};margin-bottom:2px;">' + p[0].axisValue + '</div>';
                        for (var i = 0; i < p.length; i++) {{
                            s += '<div style="display:flex;align-items:center;gap:6px;">'
                               + '<span style="display:inline-block;width:8px;height:3px;border-radius:2px;background:' + p[i].color + ';"></span>'
                               + p[i].seriesName + ': <b>' + p[i].data.toLocaleString() + '</b></div>';
                        }}
                        return s;
                    }}
                }},
                xAxis: {{
                    type: 'category',
                    data: {_json.dumps(short_times)},
                    axisLine: {{ show: false }},
                    axisTick: {{ show: false }},
                    axisLabel: {{ color: '{text_color}', fontSize: 9, interval: Math.floor({_json.dumps(short_times)}.length / 5) }},
                    splitLine: {{ show: false }}
                }},
                yAxis: {{
                    type: 'value',
                    axisLine: {{ show: false }},
                    axisTick: {{ show: false }},
                    axisLabel: {{ color: '{text_color}', fontSize: 9, formatter: function(v) {{ return (v/10000).toFixed(0)+'万'; }} }},
                    splitLine: {{ lineStyle: {{ color: '{grid_color}', type: 'dashed' }} }}
                }},
                series: [
                    {{
                        name: '策略收益',
                        type: 'line',
                        data: {_json.dumps(values)},
                        smooth: 0.4,
                        showSymbol: false,
                        lineStyle: {{ color: '#6366f1', width: 2.5 }},
                        areaStyle: {{
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                {{ offset: 0, color: 'rgba(99,102,241,0.15)' }},
                                {{ offset: 1, color: 'rgba(99,102,241,0)' }}
                            ])
                        }}
                    }},
                    {{
                        name: '持股不动',
                        type: 'line',
                        data: {_json.dumps(hold_values)},
                        smooth: 0.4,
                        showSymbol: false,
                        lineStyle: {{ color: '{c.border_strong}', width: 1.5, type: 'dashed' }}
                    }}
                ]
            }});
            window.addEventListener('resize', function() {{ chart.resize(); }});
        </script></body></html>
        """
        self._chart_view.setHtml(html)


# ═══════════════════════════════════════════════════════════════
# 交易记录表格
# ═══════════════════════════════════════════════════════════════

class TradesTable(QFrame):
    """交易记录表格"""

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        c = get_current_colors()

        self.setStyleSheet("QFrame { background: transparent; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table = QTableWidget()
        self._table.setMinimumHeight(120)  # 确保表格有可见高度
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(["时间", "方向", "价格", "数量", "金额", "盈亏"])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {c.bg_surface};
                border: none;
                gridline-color: {c.divider};
            }}
            QTableWidget::item {{
                padding: 8px;
                border: none;
            }}
            QTableWidget::item:selected {{
                background-color: {c.accent_light};
            }}
            QHeaderView::section {{
                background-color: {c.bg_header};
                color: {c.text_secondary};
                border: none;
                border-bottom: 2px solid {c.border};
                padding: 8px;
                font-weight: bold;
            }}
        """)

        header = self._table.horizontalHeader()
        for i in range(6):
            header.setSectionResizeMode(i, header.Stretch)

        layout.addWidget(self._table)

    def set_trades(self, trades):
        """设置交易记录列表 — 更新回测结果面板（强制刷新确保可见）"""
        c = get_current_colors()

        self._table.clearContents()
        self._table.setRowCount(len(trades))

        for i, trade in enumerate(trades):
            # 时间列
            time_val = trade.get('time', '-')
            if time_val is None or time_val == '':
                time_val = '-'
            self._table.setItem(i, 0, QTableWidgetItem(str(time_val)))

            # 方向列
            direction = trade.get('direction', '-')
            dir_item = QTableWidgetItem(str(direction))
            if direction == 'BUY':
                dir_item.setForeground(QColor(c.green))
            elif direction == 'SELL':
                dir_item.setForeground(QColor(c.red))
            self._table.setItem(i, 1, dir_item)

            # 价格列
            price = trade.get('price', 0)
            try:
                price_str = f"¥{float(price):.2f}" if price is not None else '¥-'
            except (ValueError, TypeError):
                price_str = '¥-'
            self._table.setItem(i, 2, QTableWidgetItem(price_str))

            # 数量列
            shares = trade.get('shares', 0)
            try:
                shares_str = f"{int(shares):,}" if shares is not None else '-'
            except (ValueError, TypeError):
                shares_str = '-'
            self._table.setItem(i, 3, QTableWidgetItem(shares_str))

            # 金额列
            amount = trade.get('amount', 0)
            try:
                amount_str = f"¥{float(amount):,.2f}" if amount is not None else '¥-'
            except (ValueError, TypeError):
                amount_str = '¥-'
            self._table.setItem(i, 4, QTableWidgetItem(amount_str))

            # 盈亏列（BUY交易pnl为0或缺失，显示为'-'）
            pnl = trade.get('pnl', 0)
            try:
                if pnl is None or (isinstance(pnl, (int, float)) and float(pnl) == 0):
                    pnl_str = '-'
                else:
                    pnl_str = f"{float(pnl):+.2f}"
            except (ValueError, TypeError):
                pnl_str = '-'
            pnl_item = QTableWidgetItem(pnl_str)
            try:
                pnl_float = float(pnl) if pnl is not None else 0
                if pnl_float > 0:
                    pnl_item.setForeground(QColor(c.green))
                elif pnl_float < 0:
                    pnl_item.setForeground(QColor(c.red))
            except (ValueError, TypeError):
                pass
            self._table.setItem(i, 5, pnl_item)

        # 强制调整列宽行高，确保内容可见
        self._table.resizeColumnsToContents()
        self._table.resizeRowsToContents()
        self._table.update()
        self.update()
        log.debug("backtest", f"交易记录表格已更新: {len(trades)} 行")


# ═══════════════════════════════════════════════════════════════
# 主面板
# ═══════════════════════════════════════════════════════════════

class BacktestPanel(QFrame):
    """回测分析页 — 可折叠面板版 v3.0"""

    _result_signal = pyqtSignal(str, dict)
    _error_signal = pyqtSignal(str, str)
    _summary_signal = pyqtSignal(dict)
    _done_signal = pyqtSignal()
    _progress_signal = pyqtSignal(int)

    stock_selected = pyqtSignal(str, str)

    def __init__(self):
        """初始化"""
        super().__init__()
        self._router: DataRouter = None
        self._is_running = False
        self._stop_event = threading.Event()
        self._row_map = {}           # code -> StockSummaryRow
        self._detail_map = {}        # code -> StockDetailPanel
        self._results = {}
        self._failed_codes = set()   # 回测失败的代码集合
        self._current_code = None
        self._extra_stocks = []
        self._init_ui()

        ThemeManager.on_change(self._on_theme_changed)

    def set_router(self, router: DataRouter):
        """设置数据路由器实例"""
        self._router = router
        self._search_input.set_router(router)
        self._rebuild_cards()

    def _init_ui(self):
        c = get_current_colors()

        root = QVBoxLayout(self)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        # ═══ 顶部工具栏 (搜索 + 按钮一行，进度条贴底) ═══
        toolbar_wrap = QFrame()
        toolbar_wrap.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px 12px 0 0;
                border-bottom: none;
            }}
        """)
        toolbar_v = QVBoxLayout(toolbar_wrap)
        toolbar_v.setContentsMargins(0, 0, 0, 0)
        toolbar_v.setSpacing(0)

        # 搜索+按钮行
        toolbar_row = QHBoxLayout()
        toolbar_row.setContentsMargins(16, 10, 16, 10)
        toolbar_row.setSpacing(12)

        self._search_input = SearchInput()
        self._search_input._input.setPlaceholderText("🔍 输入股票代码或名称，添加到回测列表…")
        self._search_input.stock_selected.connect(self._on_search_add)
        toolbar_row.addWidget(self._search_input, stretch=1)

        self.run_btn = QPushButton("▶ 开始回测")
        self.run_btn.setFixedHeight(40)
        self.run_btn.setCursor(Qt.PointingHandCursor)
        self.run_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.accent};
                color: {c.text_on_accent};
                border: none;
                border-radius: 10px;
                padding: 0 24px;
                font-size: 14px;
                font-weight: bold;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                background: {c.accent_hover};
            }}
            QPushButton:disabled {{
                background: {c.border};
                color: {c.text_secondary};
            }}
        """)
        self.run_btn.clicked.connect(self._on_run_all)
        toolbar_row.addWidget(self.run_btn)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setFixedHeight(40)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setVisible(False)
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.red if hasattr(c, 'red') else '#ef4444'};
                color: white;
                border: none;
                border-radius: 10px;
                padding: 0 20px;
                font-size: 13px;
                font-weight: bold;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                background: {c.red_hover if hasattr(c, 'red_hover') else '#dc2626'};
            }}
        """)
        self.stop_btn.clicked.connect(self._on_stop)
        toolbar_row.addWidget(self.stop_btn)

        toolbar_v.addLayout(toolbar_row)

        # 进度条 (贴底，全宽)
        self._backtest_progress = QProgressBar()
        self._backtest_progress.setFixedHeight(4)
        self._backtest_progress.setRange(0, 100)
        self._backtest_progress.setValue(0)
        self._backtest_progress.setTextVisible(False)
        self._backtest_progress.setStyleSheet(f"""
            QProgressBar {{
                background: {c.border};
                border: none;
                border-radius: 0;
            }}
            QProgressBar::chunk {{
                background: {c.accent};
                border-radius: 0;
            }}
        """)
        toolbar_v.addWidget(self._backtest_progress)

        root.addWidget(toolbar_wrap)

        # ═══ 内容区域 (可滚动) ═══
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        content = QWidget()
        content.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 12, 0, 12)
        content_layout.setSpacing(12)

        # ═══ Panel 0: 回测参数 ═══
        self._params_panel = CollapsiblePanel(
            title="回测参数", icon="🎛️",
            badge="", subtitle="调整回测参数后点击「开始回测」"
        )
        self._build_backtest_params()
        content_layout.addWidget(self._params_panel)

        # ═══ Panel 1: 总览仪表盘 ═══
        self._summary_panel = CollapsiblePanel(
            title="总览仪表盘", icon="📊",
            badge="", subtitle=""
        )
        self._dashboard = SummaryDashboard()
        self._summary_panel.body_layout.addWidget(self._dashboard)
        content_layout.addWidget(self._summary_panel)

        # ═══ Panel 2: 个股回测 ═══
        self._stocks_panel = CollapsiblePanel(
            title="个股回测", icon="📈",
            badge="", subtitle="点击股票展开详情"
        )

        # ── 区块 1: 我的持仓 ──
        self._portfolio_header = self._make_section_header("💰 我的持仓", c)
        self._stocks_panel.body_layout.addWidget(self._portfolio_header)

        self._portfolio_container = QWidget()
        self._portfolio_container.setStyleSheet("background: transparent;")
        self._portfolio_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._portfolio_layout = QVBoxLayout(self._portfolio_container)
        self._portfolio_layout.setContentsMargins(4, 0, 4, 0)
        self._portfolio_layout.setSpacing(0)
        self._stocks_panel.body_layout.addWidget(self._portfolio_container)

        # ── 区块 2: 手动添加 ──
        self._extra_header = self._make_section_header("➕ 手动添加", c)
        self._extra_header.setStyleSheet(f"""
            color: {c.text_secondary};
            background: transparent;
            padding: 16px 4px 4px 4px;
        """)
        self._stocks_panel.body_layout.addWidget(self._extra_header)

        self._extra_container = QWidget()
        self._extra_container.setStyleSheet("background: transparent;")
        self._extra_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._extra_layout = QVBoxLayout(self._extra_container)
        self._extra_layout.setContentsMargins(4, 0, 4, 0)
        self._extra_layout.setSpacing(0)
        self._stocks_panel.body_layout.addWidget(self._extra_container)

        # 展开详情容器 (独立于列表)
        self._detail_container = QWidget()
        self._detail_container.setStyleSheet("background: transparent;")
        self._detail_container.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._detail_layout = QVBoxLayout(self._detail_container)
        self._detail_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_layout.setSpacing(0)
        self._stocks_panel.body_layout.addWidget(self._detail_container)

        content_layout.addWidget(self._stocks_panel)

        # ═══ Panel 3: 交易记录 ═══
        self._trades_panel_widget = CollapsiblePanel(
            title="交易记录", icon="📋",
            badge="", subtitle=""
        )
        self._trades_panel_widget.set_open(False)  # 默认折叠
        self._trades_table = TradesTable()
        self._trades_panel_widget.body_layout.addWidget(self._trades_table)
        content_layout.addWidget(self._trades_panel_widget)

        content_layout.addStretch()
        scroll.setWidget(content)
        root.addWidget(scroll, stretch=1)

        # 占位
        self._show_empty()

        # 信号连接
        self._result_signal.connect(self._on_stock_result)
        self._error_signal.connect(self._on_stock_error)
        self._summary_signal.connect(self._on_summary)
        self._done_signal.connect(self._on_all_done)
        self._progress_signal.connect(self._on_backtest_progress)

    @staticmethod
    def _make_section_header(text, c=None):
        """创建区块小标题"""
        if c is None:
            c = get_current_colors()
        lbl = QLabel(text)
        lbl.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        lbl.setStyleSheet(f"""
            color: {c.text_secondary};
            background: transparent;
            padding: 12px 4px 4px 4px;
        """)
        return lbl

    def _build_backtest_params(self):
        """构建回测参数面板"""
        from PyQt5.QtWidgets import QDoubleSpinBox, QSpinBox
        c = get_current_colors()

        body = self._params_panel.body_layout

        grid = QFrame()
        grid.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 10px;
            }}
        """)
        grid_layout = QHBoxLayout(grid)
        grid_layout.setSpacing(24)
        grid_layout.setContentsMargins(16, 12, 16, 12)

        def _make_spin(label_text, value, min_val, max_val, step, suffix="", is_float=True):
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setFont(QFont("Microsoft YaHei", 11))
            lbl.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
            lbl.setFixedWidth(90)
            row.addWidget(lbl)
            if is_float:
                spin = QDoubleSpinBox()
                spin.setDecimals(2)
                spin.setSingleStep(step)
                spin.setValue(value)
            else:
                spin = QSpinBox()
                spin.setSingleStep(step)
                spin.setValue(int(value))
            spin.setRange(min_val, max_val)
            spin.setSuffix(suffix)
            spin.setFixedWidth(90)
            # 禁用上下箭头按钮和滚轮事件，改为纯手动输入
            spin.setButtonSymbols(QSpinBox.NoButtons)
            spin.wheelEvent = lambda event: None
            spin.setStyleSheet(f"""
                QSpinBox, QDoubleSpinBox {{
                    background: {c.bg_surface};
                    color: {c.text_primary};
                    border: 1px solid {c.border};
                    border-radius: 6px;
                    padding: 4px 8px;
                    font-family: "Microsoft YaHei";
                    font-size: 12px;
                }}
                QSpinBox::up-button, QDoubleSpinBox::up-button {{ width: 0px; }}
                QSpinBox::down-button, QDoubleSpinBox::down-button {{ width: 0px; }}
                QSpinBox:focus, QDoubleSpinBox:focus {{
                    border-color: {c.accent};
                }}
            """)
            row.addWidget(spin)
            row.addStretch()
            return row, spin

        # 列1: 止损
        col1 = QVBoxLayout()
        col1.setSpacing(8)
        t1 = QLabel("🛡️ 止损")
        t1.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        t1.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        col1.addWidget(t1)

        r, self._bt_stop_initial = _make_spin("初始止损", 3.0, 1.0, 10.0, 0.5, "%")
        col1.addLayout(r)
        r, self._bt_stop_trailing = _make_spin("跟踪止损", 2.0, 0.5, 5.0, 0.5, "%")
        col1.addLayout(r)

        grid_layout.addLayout(col1)

        # 列2: 止盈
        col2 = QVBoxLayout()
        col2.setSpacing(8)
        t2 = QLabel("🎯 止盈")
        t2.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        t2.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        col2.addWidget(t2)

        r, self._bt_tp_activate = _make_spin("激活比例", 3.0, 1.0, 10.0, 0.5, "%")
        col2.addLayout(r)
        r, self._bt_tp_trail = _make_spin("回撤比例", 1.5, 0.5, 5.0, 0.5, "%")
        col2.addLayout(r)

        grid_layout.addLayout(col2)

        # 列3: 资金
        col3 = QVBoxLayout()
        col3.setSpacing(8)
        t3 = QLabel("💰 资金")
        t3.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        t3.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        col3.addWidget(t3)

        r, self._bt_capital = _make_spin("初始资金", 1000000, 100000, 10000000, 100000, "", False)
        col3.addLayout(r)
        r, self._bt_base_pos = _make_spin("基础仓位", 10000, 1000, 100000, 1000, " 股", False)
        col3.addLayout(r)

        grid_layout.addLayout(col3)

        # 列4: 控制
        col4 = QVBoxLayout()
        col4.setSpacing(8)
        t4 = QLabel("⚙️ 控制")
        t4.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        t4.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        col4.addWidget(t4)

        r, self._bt_max_pos = _make_spin("仓位上限", 2.0, 1.0, 5.0, 0.5, "x")
        col4.addLayout(r)
        r, self._bt_confidence = _make_spin("信号阈值", 70.0, 50.0, 90.0, 5.0, "%")
        col4.addLayout(r)

        grid_layout.addLayout(col4)

        # 列5: 蒙特卡洛
        from PyQt5.QtWidgets import QCheckBox, QComboBox
        col_mc = QVBoxLayout()
        col_mc.setSpacing(8)
        t_mc = QLabel("🎲 蒙特卡洛")
        t_mc.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        t_mc.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        col_mc.addWidget(t_mc)

        mc_row = QHBoxLayout()
        mc_row.setSpacing(8)
        mc_lbl = QLabel("模拟次数")
        mc_lbl.setFont(QFont("Microsoft YaHei", 11))
        mc_lbl.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        mc_lbl.setFixedWidth(90)
        mc_row.addWidget(mc_lbl)

        self._mc_preset_combo = QComboBox()
        self._mc_preset_combo.setFixedWidth(140)
        self._mc_preset_combo.addItems(["⚡ 快速(10次)", "📐 标准(30次)", "🔬 精确(100次)"])
        self._mc_preset_combo.setCurrentIndex(1)
        self._mc_preset_combo.setCursor(Qt.PointingHandCursor)
        self._mc_preset_combo.setStyleSheet(f"""
            QComboBox {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                font-family: "Microsoft YaHei";
            }}
            QComboBox:hover {{
                border-color: {c.accent};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                selection-background-color: {c.accent};
            }}
        """)
        mc_row.addWidget(self._mc_preset_combo)
        mc_row.addStretch()
        col_mc.addLayout(mc_row)

        grid_layout.addLayout(col_mc)

        # 列6: 数据源
        from PyQt5.QtWidgets import QCheckBox
        col5 = QVBoxLayout()
        col5.setSpacing(8)
        t5 = QLabel("📦 数据源")
        t5.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        t5.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        col5.addWidget(t5)

        self._bt_use_daily = QCheckBox("优先日K (本地CSV)")
        self._bt_use_daily.setChecked(True)
        self._bt_use_daily.setFont(QFont("Microsoft YaHei", 10))
        self._bt_use_daily.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        col5.addWidget(self._bt_use_daily)

        self._bt_use_minute = QCheckBox("分时缓存优先")
        self._bt_use_minute.setChecked(True)
        self._bt_use_minute.setFont(QFont("Microsoft YaHei", 10))
        self._bt_use_minute.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        col5.addWidget(self._bt_use_minute)

        self._bt_fetch_network = QCheckBox("允许联网")
        self._bt_fetch_network.setChecked(False)
        self._bt_fetch_network.setFont(QFont("Microsoft YaHei", 10))
        self._bt_fetch_network.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        col5.addWidget(self._bt_fetch_network)

        grid_layout.addLayout(col5)

        body.addWidget(grid)

    def _show_empty(self):
        c = get_current_colors()
        empty = QLabel("暂无持仓数据，请在「我的持仓」页面添加")
        empty.setAlignment(Qt.AlignCenter)
        empty.setFont(QFont("Microsoft YaHei", 14))
        empty.setStyleSheet(f"color: {c.text_muted}; background: transparent; padding: 40px 0;")
        empty.setObjectName("empty_placeholder")
        self._portfolio_layout.addWidget(empty)

    def _clear_cards(self):
        # 清空持仓列表
        while self._portfolio_layout.count():
            item = self._portfolio_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # 清空手动添加列表
        while self._extra_layout.count():
            item = self._extra_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # 清空详情区
        while self._detail_layout.count():
            item = self._detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._row_map.clear()
        self._detail_map.clear()
        self._current_code = None

    def _rebuild_cards(self):
        self._clear_cards()

        positions = _load_positions()
        portfolio_codes = set()

        # ── 持仓股票 → portfolio_layout ──
        if positions:
            self._portfolio_header.setVisible(True)
            for code, name, volume, cost in positions:
                portfolio_codes.add(code)
                row = StockSummaryRow(code, name, volume, cost)
                row.clicked.connect(self._on_row_clicked)
                self._row_map[code] = row
                self._portfolio_layout.addWidget(row)
        else:
            self._portfolio_header.setVisible(False)

        # ── 手动添加的非持仓股票 → extra_layout ──
        extra_to_remove = []
        has_extra = False
        for code, name in self._extra_stocks:
            if code in portfolio_codes:
                extra_to_remove.append((code, name))
                continue
            row = StockSummaryRow(code, name, 0, 0)
            row.clicked.connect(self._on_row_clicked)
            row.set_removable(True)
            row.remove_clicked.connect(self._on_remove_extra)
            self._row_map[code] = row
            self._extra_layout.addWidget(row)
            has_extra = True

        if has_extra:
            self._extra_header.setVisible(True)
        else:
            self._extra_header.setVisible(False)

        for item in extra_to_remove:
            self._extra_stocks.remove(item)

        if not self._row_map:
            self._show_empty()

        # 更新 badge
        count = len(self._row_map)
        self._stocks_panel.set_badge(f"{count} 只")
        self._summary_panel.set_badge(f"{count} 只")

    def _on_search_add(self, code, name, sector):
        if code in self._row_map:
            return

        self._extra_stocks.append((code, name))
        self._search_input.clear()

        row = StockSummaryRow(code, name, 0, 0)
        row.clicked.connect(self._on_row_clicked)
        row.set_removable(True)
        row.remove_clicked.connect(self._on_remove_extra)
        self._row_map[code] = row

        # 显示"手动添加"标题
        self._extra_header.setVisible(True)

        # 添加到 extra_layout
        self._extra_layout.addWidget(row)

        self._remove_empty_placeholder()

        count = len(self._row_map)
        self._stocks_panel.set_badge(f"{count} 只")
        self._summary_panel.set_badge(f"{count} 只")

        log.signal_log("backtest", f"添加回测: {code} {name}", "")

        # 自动下载数据 (后台线程)
        Thread(target=self._auto_download, args=(code, name), daemon=True).start()

    def _remove_empty_placeholder(self):
        for layout in (self._portfolio_layout, self._extra_layout):
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), QLabel):
                    w = item.widget()
                    if "暂无" in w.text():
                        w.deleteLater()
                        break

    def _on_remove_extra(self, code):
        self._extra_stocks = [(c, n) for c, n in self._extra_stocks if c != code]
        if code in self._row_map:
            row = self._row_map.pop(code)
            self._extra_layout.removeWidget(row)
            row.deleteLater()
        if code in self._detail_map:
            detail = self._detail_map.pop(code)
            self._detail_layout.removeWidget(detail)
            detail.deleteLater()
        self._results.pop(code, None)

        # 隐藏"手动添加"标题 (如果没有手动添加的股票了)
        self._extra_header.setVisible(len(self._extra_stocks) > 0)

        if not self._row_map:
            self._show_empty()

        count = len(self._row_map)
        self._stocks_panel.set_badge(f"{count} 只")
        self._summary_panel.set_badge(f"{count} 只")

        log.signal_log("backtest", f"移除回测: {code}", "")

        # 自动清理数据 (后台线程)
        Thread(target=self._auto_cleanup, args=(code,), daemon=True).start()

    def _on_row_clicked(self, code, name):
        """点击紧凑行 — 展开/收起详情"""
        self.stock_selected.emit(code, name)

        # 如果点击已展开的行 → 收起
        if self._current_code == code:
            self._deselect_current()
            return

        # 收起旧的
        self._deselect_current()

        # 展开新的
        self._current_code = code
        if code in self._row_map:
            self._row_map[code].set_selected(True)

        # 创建或显示详情面板
        if code not in self._detail_map:
            detail = StockDetailPanel(code, name)
            self._detail_map[code] = detail
            # 如果已有回测结果，立即更新
            if code in self._results:
                detail.update_result(self._results[code])
        else:
            detail = self._detail_map[code]

        # 将详情面板插入到列表下方
        self._detail_layout.addWidget(detail)
        detail.setVisible(True)

        # 更新交易记录面板
        result = self._results.get(code, {})
        trades = result.get('trades', [])
        if trades:
            self._trades_table.set_trades(trades)
            self._trades_panel_widget.set_subtitle(f"{name} · {len(trades)} 笔交易")
            self._trades_panel_widget.set_badge(f"{len(trades)} 笔")
            self._trades_panel_widget.set_open(True)

    def _deselect_current(self):
        """收起当前展开的详情"""
        if self._current_code:
            if self._current_code in self._row_map:
                self._row_map[self._current_code].set_selected(False)
            if self._current_code in self._detail_map:
                self._detail_map[self._current_code].setVisible(False)
            self._current_code = None

    # ── 运行 ──

    def _on_run_all(self):
        if self._is_running:
            return

        if not self._router:
            self._trades_panel_widget.set_open(True)
            self._trades_panel_widget.set_subtitle("⚠️ 数据源未连接，请稍候再试")
            return

        all_stocks = []
        positions = _load_positions()
        portfolio_codes = set()

        if positions:
            for code, name, volume, cost in positions:
                portfolio_codes.add(code)
                all_stocks.append((code, name, volume, cost))

        for code, name in self._extra_stocks:
            if code not in portfolio_codes:
                all_stocks.append((code, name, 1000, 0))

        if not all_stocks:
            return

        self._is_running = True
        self._stop_event.clear()
        self._results.clear()
        self._failed_codes.clear()
        self.run_btn.setEnabled(False)
        self.run_btn.setText("⏳ 回测中…")
        self.stop_btn.setVisible(True)
        self._backtest_progress.setVisible(True)   # 确保进度条可见
        self._backtest_progress.setValue(0)

        # 在主线程读取蒙特卡洛模拟次数
        _mc_idx = self._mc_preset_combo.currentIndex()
        mc_simulations = [10, 30, 100][_mc_idx]

        for code, *_ in all_stocks:
            if code in self._row_map:
                self._row_map[code].set_loading()

        Thread(target=self._run_all_backtest, args=(all_stocks, mc_simulations), daemon=True).start()

    def _on_stop(self):
        """用户点击停止按钮"""
        self._stop_event.set()
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("停止中…")
        log.signal_log("backtest", "用户请求停止回测", "")

    def _run_all_backtest(self, positions, mc_simulations=30):
        import time as _time
        _start_time = _time.time()
        _GLOBAL_TIMEOUT = 600

        try:
            from strategies.backtest_engine_v2 import EnhancedBacktestEngine, BacktestConfig
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._error_signal.emit("", f"\u5f15\u64ce\u52a0\u8f7d\u5931\u8d25: {e}")
            self._done_signal.emit()
            return

        # 读取数据源选项
        use_daily = self._bt_use_daily.isChecked()
        use_minute_cache = self._bt_use_minute.isChecked()
        allow_network = self._bt_fetch_network.isChecked()

        try:
            total = len(positions)

            # ══ 阶段 1: 加载数据 ══
            self._progress_signal.emit(5)
            data_map = {}

            # ── 方式A: 本地日K CSV ──
            if use_daily:
                for code, name, volume, cost in positions:
                    if self._stop_event.is_set():
                        break
                    if _time.time() - _start_time > _GLOBAL_TIMEOUT:
                        break
                    df = self._load_daily_kline(code)
                    if df is not None and len(df) >= 30:
                        data_map[code] = (df, name)
                        log.signal_log("backtest", f"{code} \u65e5K\u52a0\u8f7d", f"{len(df)}\u6761")
                if data_map:
                    self._progress_signal.emit(30)

            # ── 方式B: 分时缓存 ──
            if use_minute_cache:
                from data.cache_manager import get_cache_manager
                cache = get_cache_manager()
                missing = [p for p in positions if p[0] not in data_map]
                for code, name, volume, cost in missing:
                    if _time.time() - _start_time > _GLOBAL_TIMEOUT:
                        break
                    try:
                        l2_data = cache._db_load(code)
                        if l2_data and len(l2_data) >= 20:
                            data_map[code] = (l2_data, name)
                            log.signal_log("backtest", f"{code} \u5206\u65f6\u7f13\u5b58\u547d\u4e2d", f"{len(l2_data)}\u6761")
                    except Exception:
                        pass
                if missing:
                    self._progress_signal.emit(40)

            # ── 方式C: 自动联网 (本地数据不足时自动启用) ──
            missing = [p for p in positions if p[0] not in data_map]
            if missing and (allow_network or not data_map):
                from data.cache_manager import get_cache_manager
                cache = get_cache_manager()
                from concurrent.futures import ThreadPoolExecutor, as_completed

                def _fetch_one(code, name, volume, cost):
                    """获取单只股票数据 — 内部超时10秒，防止阻塞"""
                    import time as _t
                    _s = _t.time()
                    try:
                        data = cache.get_minute_for_backtest(code, self._router)
                        elapsed = _t.time() - _s
                        log.signal_log("backtest", f"{code} 网络获取完成",
                                       f"{len(data) if data else 0}\u6761 | \u8017\u65f6{elapsed:.1f}\u79d2")
                        return code, data, name
                    except Exception as e:
                        elapsed = _t.time() - _s
                        log.warning("backtest", f"{code} 网络获取失败({elapsed:.1f}\u79d2)", str(e))
                        return code, None, name

                log.signal_log("backtest", f"开始联网获取数据",
                               f"{len(missing)}\u53ea | \u5de5\u4f5c\u7ebf{min(len(missing), 4)}\u6761")
                with ThreadPoolExecutor(max_workers=min(len(missing), 4)) as pool:
                    futures = {
                        pool.submit(_fetch_one, c, n, v, co): c
                        for c, n, v, co in missing
                    }
                    done_count = 0
                    for future in as_completed(futures, timeout=30):
                        if _time.time() - _start_time > _GLOBAL_TIMEOUT:
                            log.warning("backtest", "数据获取阶段全局超时",
                                        f"已完成{done_count}/{len(missing)}")
                            break
                        try:
                            code, data, name = future.result(timeout=15)
                            if data and len(data) >= 20:
                                data_map[code] = (data, name)
                            else:
                                self._error_signal.emit(code, "网络无数据")
                        except Exception as e:
                            log.warning("backtest", f"网络拉取失败: {e}")
                        done_count += 1
                log.signal_log("backtest", "数据获取阶段完成",
                               f"成功{len(data_map)}/{len(positions)}\u53ea")
                self._progress_signal.emit(50)

            # ══ 阶段 2: 逐只回测 + 蒙特卡洛 ══
            bt_count = 0
            for code, name, volume, cost in positions:
                if self._stop_event.is_set():
                    log.signal_log("backtest", "收到停止信号，中断回测", "")
                    break
                if _time.time() - _start_time > _GLOBAL_TIMEOUT:
                    self._error_signal.emit("", "\u56de\u6d4b\u8d85\u65f6\uff0c\u5df2\u8df3\u8fc7\u5269\u4f59\u6807\u7684")
                    break
                if code not in data_map:
                    self._error_signal.emit(code, "\u65e0\u53ef\u7528\u6570\u636e\uff08\u672c\u5730+\u7f13\u5b58\u5747\u65e0\uff09")
                    continue

                raw_data, display_name = data_map[code]
                bt_count += 1

                try:
                    config = BacktestConfig(
                        base_position=volume if volume > 0 else 10000,
                        stop_loss_type="trailing",
                        stop_loss_params={
                            'initial_stop_pct': self._bt_stop_initial.value() / 100.0,
                            'trailing_pct': self._bt_stop_trailing.value() / 100.0,
                        },
                        tp_activate_pct=self._bt_tp_activate.value() / 100.0,
                        tp_trail_pct=self._bt_tp_trail.value() / 100.0,
                        position_sizer_type="fixed",
                        initial_capital=self._bt_capital.value(),
                        max_position_multiplier=self._bt_max_pos.value(),
                        min_signal_confidence=self._bt_confidence.value(),
                    )
                    engine = EnhancedBacktestEngine(config)

                    if isinstance(raw_data, list):
                        # 预计算特征（回测 + 蒙特卡洛共用）
                        precomputed_df = engine._prepare_data(raw_data)
                        if precomputed_df is not None and len(precomputed_df) >= 20:
                            from strategies.data.features import calculate_features
                            precomputed_df = calculate_features(precomputed_df)
                        else:
                            precomputed_df = None
                        result = engine.run(code, display_name, raw_data,
                                            cost_price=cost, precomputed_df=precomputed_df)
                        mc_result = engine.run_monte_carlo(
                            code, display_name, raw_data,
                            n_simulations=mc_simulations, base_result=result,
                        )
                    else:
                        kline_list = raw_data.to_dict('records')
                        precomputed_df = engine._prepare_data(kline_list)
                        if precomputed_df is not None and len(precomputed_df) >= 20:
                            from strategies.data.features import calculate_features
                            precomputed_df = calculate_features(precomputed_df)
                        else:
                            precomputed_df = None
                        result = engine.run(code, display_name, kline_list,
                                            cost_price=cost, precomputed_df=precomputed_df)
                        mc_result = engine.run_monte_carlo(
                            code, display_name, kline_list,
                            n_simulations=mc_simulations, base_result=result,
                        )

                    self._results[code] = {
                        'code': code, 'name': display_name,
                        'total_return': result.total_return,
                        'sharpe_ratio': result.sharpe_ratio,
                        'max_drawdown': result.max_drawdown,
                        'win_rate': result.win_rate,
                        'profit_factor': result.profit_factor,
                        'total_trades': result.total_trades,
                        'avg_daily_pnl': result.avg_trade_return,
                        'cost_ratio': result.cost_ratio,
                        'total_commission': result.total_commission,
                        'total_tax': result.total_tax,
                        'hold_only_return': result.hold_only_return,
                        'equity_curve': result.equity_curve,
                        'daily_stats': result.daily_stats,
                        'trades': result.trades,
                        'mc_mean_return': mc_result.get('mean_return', 0),
                        'mc_prob_positive': mc_result.get('prob_positive', 0),
                        'mc_worst_case': mc_result.get('worst_case', 0),
                        'mc_best_case': mc_result.get('best_case', 0),
                        'mc_percentile_5': mc_result.get('percentile_5', 0),
                        'mc_percentile_95': mc_result.get('percentile_95', 0),
                        'stop_loss_triggered': result.stop_loss_triggered,
                        'stop_loss_pnl': result.stop_loss_pnl,
                        'calmar_ratio': result.calmar_ratio,
                        'sortino_ratio': result.sortino_ratio,
                        'volatility': result.volatility,
                        'var_95': result.var_95,
                    }
                    self._result_signal.emit(code, self._results[code])
                    self._progress_signal.emit(50 + int(bt_count / total * 50))

                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    log.error("backtest", f"{code} 回测异常", str(e))
                    self._failed_codes.add(code)  # 记录失败代码
                    # 构造一个带错误信息的结果对象，而非发送 error_signal
                    self._results[code] = {
                        'code': code, 'name': display_name,
                        'total_return': 0.0,
                        'sharpe_ratio': 0.0,
                        'max_drawdown': 0.0,
                        'win_rate': 0.0,
                        'profit_factor': 0.0,
                        'total_trades': 0,
                        'avg_daily_pnl': 0.0,
                        'cost_ratio': 0.0,
                        'total_commission': 0.0,
                        'total_tax': 0.0,
                        'hold_only_return': 0.0,
                        'equity_curve': [],
                        'daily_stats': [],
                        'trades': [],
                        'mc_mean_return': 0.0,
                        'mc_prob_positive': 0.0,
                        'mc_worst_case': 0.0,
                        'mc_best_case': 0.0,
                        'mc_percentile_5': 0.0,
                        'mc_percentile_95': 0.0,
                        'stop_loss_triggered': 0,
                        'stop_loss_pnl': 0.0,
                        'calmar_ratio': 0.0,
                        'sortino_ratio': 0.0,
                        'volatility': 0.0,
                        'var_95': 0.0,
                        'error_msg': str(e),  # 标记异常原因
                    }
                    self._result_signal.emit(code, self._results[code])

            if self._results:
                try:
                    self._summary_signal.emit(self._calc_summary(positions))
                except Exception:
                    pass

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._error_signal.emit("", f"\u56de\u6d4b\u5f02\u5e38: {e}")

        finally:
            self._done_signal.emit()

    @staticmethod
    def _load_daily_kline(code):
        """从 data/klines/ 加载本地日K CSV -> DataFrame"""
        import pandas as pd
        klines_dir = os.path.join(_DATA_DIR, "klines")
        if not os.path.exists(klines_dir):
            return None

        candidates = [
            os.path.join(klines_dir, f"{code}_day.csv"),
            os.path.join(klines_dir, f"{code}.csv"),
        ]
        for path in candidates:
            if os.path.exists(path):
                try:
                    df = pd.read_csv(path)
                    df.columns = [c.lower().strip() for c in df.columns]
                    if 'close' not in df.columns:
                        continue
                    for col in ['open', 'high', 'low', 'volume']:
                        if col not in df.columns:
                            df[col] = df['close'] if col != 'volume' else 0
                    if 'time' not in df.columns and 'date' in df.columns:
                        df = df.rename(columns={'date': 'time'})
                    return df
                except Exception:
                    continue
        return None

    def _auto_download(self, code, name):
        """后台线程: 自动下载数据"""
        try:
            result = auto_download_stock_data(code, name, scene="add")
            main_msg, detail_msg = format_download_report(result)
            log.signal_log("data", main_msg, detail_msg)
        except Exception as e:
            log.warning("data", f"{code} 数据下载异常: {e}")

    def _auto_cleanup(self, code):
        """后台线程: 自动清理数据"""
        try:
            result = auto_cleanup_stock_data(code)
            log.signal_log("data", format_cleanup_report(result), "")
        except Exception as e:
            log.warning("data", f"{code} 数据清理异常: {e}")

    def _calc_summary(self, positions):
        win_rates, excess_returns = [], []
        total_trades = 0

        for code, name, volume, cost in positions:
            if code not in self._results:
                continue
            r = self._results[code]
            win_rates.append(r['win_rate'] * 100)
            excess = r['total_return'] * 100 - r['hold_only_return'] * 100
            excess_returns.append(excess)
            total_trades += r.get('total_trades', 0)

        return {
            'count': len(self._results),
            'avg_win_rate': sum(win_rates) / len(win_rates) if win_rates else 0,
            'total_excess': sum(excess_returns),
            'total_trades': total_trades,
        }

    # ── 信号 ──

    @pyqtSlot(str, dict)
    def _on_stock_result(self, code, r):
        # 更新紧凑行
        if code in self._row_map:
            self._row_map[code].update_result(r)
        # 更新展开详情 (如果当前展开的是这只)
        if code in self._detail_map:
            self._detail_map[code].update_result(r)

    @pyqtSlot(str, str)
    def _on_stock_error(self, code, msg):
        """股票回测错误回调 — 显示具体错误而非笼统'失败'"""
        self._failed_codes.add(code)  # 记录失败代码
        c = get_current_colors()
        if code in self._row_map:
            row = self._row_map[code]
            # 仅更新第一个指标标签显示错误摘要，保持其他清晰
            if row._metric_labels:
                first_lbl = row._metric_labels[0]
                # 错误摘要映射：将技术错误转用户友好提示
                user_msg = "超时" if "超时" in msg else ("无数据" if "无数据" in msg else "异常")
                first_lbl.setText(user_msg)
                first_lbl.setStyleSheet(f"color: {c.red}; background: transparent;")
                # 其余标签清空避免显示旧数据
                for lbl in row._metric_labels[1:]:
                    lbl.setText("-")
                    lbl.setStyleSheet(f"color: {c.text_muted}; background: transparent;")

    @pyqtSlot(dict)
    def _on_summary(self, s):
        self._dashboard.update_data(
            s['count'], s['avg_win_rate'], s['total_excess'],
            s.get('total_trades', 0),
        )
        # 更新总览面板副标题
        avg_wr = s['avg_win_rate']
        total_excess = s['total_excess']
        excess_str = f"+{total_excess:.2f}%" if total_excess >= 0 else f"{total_excess:.2f}%"
        self._summary_panel.set_subtitle(
            f"平均胜率 {avg_wr:.0f}%  ·  总超额 {excess_str}"
        )

    @pyqtSlot(int)
    def _on_backtest_progress(self, value: int):
        self._backtest_progress.setValue(value)

    @pyqtSlot()
    def _on_all_done(self):
        stopped = self._stop_event.is_set()
        self._is_running = False
        self.run_btn.setEnabled(True)
        self.stop_btn.setVisible(False)
        self.stop_btn.setEnabled(True)
        self.stop_btn.setText("⏹ 停止")
        self._backtest_progress.setVisible(False)  # 完成后隐藏进度条

        success = len(self._results)
        failed = len(self._failed_codes)
        total = len(self._row_map)

        if stopped:
            self.run_btn.setText("▶ 继续回测")
            log.signal_log("backtest", "回测已停止", f"已完成 {success}/{total} 只")
        elif failed == 0 and success > 0:
            self.run_btn.setText("✅ 已回测")
            log.signal_log("backtest", "全部回测完成", f"{success}/{total} 只")
        elif success > 0 and failed > 0:
            self.run_btn.setText("⚠️ 部分失败")
            log.signal_log("backtest", "部分回测失败", f"成功 {success} 只, 失败 {failed} 只")
        else:
            self.run_btn.setText("❌ 回测失败")
            log.signal_log("backtest", "回测完成", "无有效结果")

    def _on_theme_changed(self, colors):
        """主题变化 — 保存结果后重建"""
        saved_results = dict(self._results)
        saved_extra = list(self._extra_stocks)
        saved_current = self._current_code

        self._rebuild_cards()

        # 恢复额外股票
        self._extra_stocks = saved_extra

        # 恢复回测结果
        for code, result in saved_results.items():
            if code in self._row_map:
                self._row_map[code].update_result(result)
            self._results[code] = result

        # 恢复展开状态
        if saved_current and saved_current in self._row_map:
            self._on_row_clicked(saved_current, self._results.get(saved_current, {}).get('name', ''))


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)

    panel = BacktestPanel()
    panel.setFixedSize(1200, 800)
    panel.show()

    sys.exit(app.exec_())
