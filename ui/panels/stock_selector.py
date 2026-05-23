#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股面板 v3.1 — 精简版
========================

仅保留 MACD 交叉扫描功能:
- 金叉(东方财富秒出) + 死叉(全市场扫描)
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar,
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QSize
from PyQt5.QtGui import QFont, QColor, QBrush, QPainter, QLinearGradient

from ui.theme import get_current_colors


# ═══════════════════════════════════════════════════════════════
# 统计卡片（重设计）
# ═══════════════════════════════════════════════════════════════

class StatCard(QFrame):
    """带左侧色条 + 图标背景的统计卡片"""

    def __init__(self, icon, label, color, parent=None):
        super().__init__(parent)
        self._color = color
        self._icon = icon
        self._label_text = label
        self.setFixedHeight(72)
        self.setMinimumWidth(160)
        self._build_ui()

    def _build_ui(self):
        c = get_current_colors()
        self.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_app};
                border: 1px solid {c.border};
                border-left: 4px solid {self._color};
                border-radius: 10px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 16, 10)
        layout.setSpacing(12)

        # 左侧：图标圆底
        icon_wrap = QFrame()
        icon_wrap.setFixedSize(40, 40)
        icon_wrap.setStyleSheet(f"""
            QFrame {{
                background: {self._color}18;
                border: none;
                border-radius: 20px;
            }}
        """)
        icon_layout = QVBoxLayout(icon_wrap)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_lbl = QLabel(self._icon)
        icon_lbl.setFont(QFont("Microsoft YaHei", 16))
        icon_lbl.setAlignment(Qt.AlignCenter)
        icon_layout.addWidget(icon_lbl)
        layout.addWidget(icon_wrap)

        # 右侧：数值 + 标签
        text_col = QVBoxLayout()
        text_col.setSpacing(1)

        self._value_label = QLabel("0")
        self._value_label.setFont(QFont("Microsoft YaHei", 22, QFont.Bold))
        self._value_label.setStyleSheet(f"color: {self._color};")
        text_col.addWidget(self._value_label)

        name_lbl = QLabel(self._label_text)
        name_lbl.setFont(QFont("Microsoft YaHei", 10))
        name_lbl.setStyleSheet(f"color: {c.text_secondary};")
        text_col.addWidget(name_lbl)

        layout.addLayout(text_col, stretch=1)

    def set_value(self, text):
        self._value_label.setText(text)


# ═══════════════════════════════════════════════════════════════
# MACD 交叉结果表格（列宽自适应）
# ═══════════════════════════════════════════════════════════════

class MacdCrossTable(QTableWidget):
    """MACD 金叉/死叉结果表 — 统一展示，按类型分色"""
    stockClicked = pyqtSignal(str, str)

    HEADERS = ["类型", "代码", "名称", "最新价", "涨跌幅", "量比", "换手率"]

    # 各列固定宽度（像素），名称列由 stretch 处理
    COL_WIDTHS = {
        0: 72,   # 类型
        1: 80,   # 代码
        # 2: 名称 — 弹性
        3: 76,   # 最新价
        4: 76,   # 涨跌幅
        5: 60,   # 量比
        6: 72,   # 换手率
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        c = get_current_colors()
        self.setColumnCount(len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setSortingEnabled(True)
        self.setWordWrap(False)

        # 列宽策略：固定列用 ResizeToContents 控初始，名称列 Stretch 填充剩余
        header = self.horizontalHeader()
        for col in range(len(self.HEADERS)):
            if col in self.COL_WIDTHS:
                header.setSectionResizeMode(col, QHeaderView.Interactive)
                header.resizeSection(col, self.COL_WIDTHS[col])
            else:
                header.setSectionResizeMode(col, QHeaderView.Stretch)

        self.setStyleSheet(f"""
            QTableWidget {{
                background: {c.bg_surface}; alternate-background-color: {c.bg_app};
                border: 1px solid {c.border}; border-radius: 8px;
                gridline-color: {c.border}; font-family: "Microsoft YaHei"; font-size: 12px;
                selection-background-color: {c.accent}30;
            }}
            QTableWidget::item {{
                padding: 5px 6px; border-bottom: 1px solid {c.border};
            }}
            QTableWidget::item:selected {{
                background: {c.accent}40; color: {c.text_primary};
            }}
            QHeaderView::section {{
                background: {c.bg_app}; color: {c.text_primary};
                padding: 7px 6px; border: none;
                border-bottom: 2px solid {c.accent};
                font-family: "Microsoft YaHei"; font-size: 12px; font-weight: bold;
            }}
        """)
        self.cellDoubleClicked.connect(self._on_double_click)

    def _on_double_click(self, row, col):
        code_item = self.item(row, 1)
        name_item = self.item(row, 2)
        if code_item and name_item:
            self.stockClicked.emit(code_item.text(), name_item.text())

    def update_results(self, golden, death):
        """合并金叉+死叉结果，金叉在前，死叉在后"""
        all_items = []
        for s in golden:
            all_items.append({**s, "_sort_key": 0})
        for s in death:
            all_items.append({**s, "_sort_key": 1})

        self.setSortingEnabled(False)
        self.setRowCount(len(all_items))

        for row, s in enumerate(all_items):
            cross_type = s.get("cross_type", "")
            is_golden = cross_type == "golden"

            # 类型标签
            type_item = QTableWidgetItem("🔺 金叉" if is_golden else "🔻 死叉")
            type_item.setForeground(QBrush(QColor("#ef4444" if is_golden else "#22c55e")))
            font = type_item.font()
            font.setBold(True)
            type_item.setFont(font)
            type_item.setData(Qt.UserRole, s.get("_sort_key", 0))
            self.setItem(row, 0, type_item)

            # 代码 — 右对齐，等宽感
            code_item = QTableWidgetItem(s.get("code", ""))
            code_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            code_item.setFont(QFont("Consolas", 11))
            self.setItem(row, 1, code_item)

            self.setItem(row, 2, QTableWidgetItem(s.get("name", "")))

            price = s.get("price", 0)
            price_item = QTableWidgetItem(f"{price:.2f}" if price else "--")
            price_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.setItem(row, 3, price_item)

            change = s.get("change_pct", 0)
            change_item = QTableWidgetItem(f"{change:+.2f}%" if change else "--")
            change_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if change:
                change_item.setForeground(QBrush(QColor("#ef4444" if change > 0 else "#22c55e")))
            self.setItem(row, 4, change_item)

            vr = s.get("volume_ratio", 0)
            vr_item = QTableWidgetItem(f"{vr:.2f}" if vr else "--")
            vr_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.setItem(row, 5, vr_item)

            tr = s.get("turnover_rate", 0)
            tr_item = QTableWidgetItem(f"{tr:.2f}%" if tr else "--")
            tr_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.setItem(row, 6, tr_item)

        self.setSortingEnabled(True)

    def resizeEvent(self, event):
        """窗口缩放时保持列宽比例"""
        super().resizeEvent(event)
        header = self.horizontalHeader()
        # 固定列总宽
        fixed_total = sum(self.COL_WIDTHS.values())
        # 名称列拿到剩余空间，但至少 80px
        available = self.viewport().width() - fixed_total - 20
        name_width = max(80, available)
        header.resizeSection(2, name_width)


# ═══════════════════════════════════════════════════════════════
# 工作线程：MACD 交叉扫描
# ═══════════════════════════════════════════════════════════════

class MacdCrossWorker(QThread):
    progress = pyqtSignal(str, int, int)
    finished = pyqtSignal(list, list)  # golden, death
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            from data_sources.selector_data import (
                fetch_macd_golden_cross,
                fetch_macd_death_cross,
                fetch_all_stocks_eastmoney,
            )

            # Phase 0: 预拉全市场行情（死叉扫描需要，避免重复拉取）
            self.progress.emit("获取全市场行情...", 0, 3)
            all_stocks = fetch_all_stocks_eastmoney()

            # Phase 1: 金叉（秒出）
            self.progress.emit("扫描MACD金叉...", 1, 3)
            golden, golden_codes = fetch_macd_golden_cross()

            # Phase 2: 死叉（复用已拉取的行情数据）
            def _on_progress(done, total):
                self.progress.emit(f"扫描死叉 ({done}/{total})...", done, total)

            death = fetch_macd_death_cross(
                golden_codes, progress_cb=_on_progress, all_stocks=all_stocks
            )
            self.progress.emit(f"完成", 3, 3)

            self.finished.emit(golden, death)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════
# 主选股面板
# ═══════════════════════════════════════════════════════════════

class StockSelectorPanel(QWidget):
    stockClicked = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.macd_worker = None
        self._init_ui()

    def _init_ui(self):
        c = get_current_colors()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # ═══ 顶部：标题行 + 扫描按钮 ═══
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title = QLabel("📈 MACD 金叉 / 死叉 扫描")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title_col.addWidget(title)

        subtitle = QLabel("金叉 DIF↑DEA（趋势转强）  ·  死叉 DIF↓DEA（趋势转弱）  ·  数据源：东方财富")
        subtitle.setFont(QFont("Microsoft YaHei", 10))
        subtitle.setStyleSheet(f"color: {c.text_secondary};")
        title_col.addWidget(subtitle)

        top_bar.addLayout(title_col, stretch=1)

        # 按钮
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.macd_scan_btn = QPushButton("🔍 一键扫描")
        self.macd_scan_btn.setFixedHeight(40)
        self.macd_scan_btn.setMinimumWidth(120)
        self.macd_scan_btn.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self.macd_scan_btn.setCursor(Qt.PointingHandCursor)
        self.macd_scan_btn.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ef4444, stop:1 #f59e0b);
                color: white; border: none; border-radius: 8px;
                padding: 0 24px;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #dc2626, stop:1 #d97706);
            }}
            QPushButton:disabled {{ background: {c.text_secondary}; }}
        """)
        self.macd_scan_btn.clicked.connect(self._start_macd_scan)
        btn_row.addWidget(self.macd_scan_btn)

        self.macd_cancel_btn = QPushButton("⏹ 停止")
        self.macd_cancel_btn.setFixedHeight(40)
        self.macd_cancel_btn.setEnabled(False)
        self.macd_cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.bg_surface}; color: {c.text_primary};
                border: 1px solid {c.border}; border-radius: 8px;
                padding: 0 16px;
            }}
            QPushButton:hover {{ border-color: #ef4444; color: #ef4444; }}
            QPushButton:disabled {{ color: {c.text_secondary}; border-color: {c.border}; }}
        """)
        self.macd_cancel_btn.clicked.connect(self._cancel_macd_scan)
        btn_row.addWidget(self.macd_cancel_btn)

        top_bar.addLayout(btn_row)
        main_layout.addLayout(top_bar)

        # ═══ 进度条 ═══
        self.macd_progress_frame = QFrame()
        self.macd_progress_frame.setVisible(False)
        prog_layout = QVBoxLayout(self.macd_progress_frame)
        prog_layout.setContentsMargins(0, 0, 0, 0)
        prog_layout.setSpacing(4)

        self.macd_progress = QProgressBar()
        self.macd_progress.setFixedHeight(20)
        self.macd_progress.setFormat("%v / %m  (%p%)")
        self.macd_progress.setAlignment(Qt.AlignCenter)
        self.macd_progress.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid {c.border}; border-radius: 10px;
                background: {c.bg_app}; font-family: "Microsoft YaHei";
                font-size: 12px; font-weight: bold; color: {c.text_primary};
            }}
            QProgressBar::chunk {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ef4444, stop:1 #f59e0b);
                border-radius: 10px;
            }}
        """)
        prog_layout.addWidget(self.macd_progress)

        self.macd_status = QLabel("")
        self.macd_status.setStyleSheet(f"color: {c.text_secondary}; font-size: 12px;")
        self.macd_status.setAlignment(Qt.AlignCenter)
        prog_layout.addWidget(self.macd_status)

        main_layout.addWidget(self.macd_progress_frame)

        # ═══ 统计卡片 ═══
        stats_row = QHBoxLayout()
        stats_row.setSpacing(14)

        self.golden_card = StatCard("🔺", "金叉", "#ef4444")
        self.death_card  = StatCard("🔻", "死叉", "#22c55e")
        self.total_card  = StatCard("📊", "合计", c.accent)

        stats_row.addWidget(self.golden_card)
        stats_row.addWidget(self.death_card)
        stats_row.addWidget(self.total_card)
        stats_row.addStretch()
        main_layout.addLayout(stats_row)

        # ═══ 结果表格 ═══
        self.macd_table = MacdCrossTable()
        self.macd_table.stockClicked.connect(self.stockClicked)
        main_layout.addWidget(self.macd_table, stretch=1)

    # ═══════════════════════════════════════════════════════════
    # MACD 交叉 逻辑
    # ═══════════════════════════════════════════════════════════

    def _start_macd_scan(self):
        self.macd_scan_btn.setEnabled(False)
        self.macd_scan_btn.setText("扫描中...")
        self.macd_cancel_btn.setEnabled(True)
        self.macd_progress_frame.setVisible(True)
        self.macd_progress.setRange(0, 0)
        self.macd_status.setText("准备中...")

        self.macd_worker = MacdCrossWorker()
        self.macd_worker.progress.connect(self._on_macd_progress)
        self.macd_worker.finished.connect(self._on_macd_finished)
        self.macd_worker.error.connect(self._on_macd_error)
        self.macd_worker.start()

    def _on_macd_progress(self, msg, current, total):
        self.macd_status.setText(msg)
        if total > 0:
            self.macd_progress.setRange(0, total)
            self.macd_progress.setValue(current)

    def _on_macd_finished(self, golden, death):
        self.macd_scan_btn.setEnabled(True)
        self.macd_scan_btn.setText("🔍 一键扫描")
        self.macd_cancel_btn.setEnabled(False)
        self.macd_progress_frame.setVisible(False)

        # 更新统计卡片
        self.golden_card.set_value(str(len(golden)))
        self.death_card.set_value(str(len(death)))
        self.total_card.set_value(str(len(golden) + len(death)))

        # 更新表格
        self.macd_table.update_results(golden, death)

        self.macd_status.setText(
            f"✅ 完成 — 金叉 {len(golden)} 只, 死叉 {len(death)} 只"
        )

    def _on_macd_error(self, msg):
        self.macd_scan_btn.setEnabled(True)
        self.macd_scan_btn.setText("🔍 一键扫描")
        self.macd_cancel_btn.setEnabled(False)
        self.macd_progress_frame.setVisible(False)
        self.macd_status.setText(f"❌ 错误: {msg}")

    def _cancel_macd_scan(self):
        if self.macd_worker and self.macd_worker.isRunning():
            self.macd_worker.terminate()
            self.macd_worker.wait(3000)
        self.macd_scan_btn.setEnabled(True)
        self.macd_scan_btn.setText("🔍 一键扫描")
        self.macd_cancel_btn.setEnabled(False)
        self.macd_progress_frame.setVisible(False)
        self.macd_status.setText("⏹ 已取消")
