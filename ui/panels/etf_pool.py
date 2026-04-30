#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF T+0 扫描面板
================
显示自选池中所有 ETF 的实时行情 + ML 信号 + 迷你走势图，支持增删管理。
"""

import time
from datetime import datetime
from typing import List, Dict, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QPushButton, QLineEdit,
    QHeaderView, QAbstractItemView,
    QProgressBar, QSizePolicy, QSizePolicy as QSP
)
from ui.panels.search import SearchInput
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QFont, QColor, QBrush, QPainter, QPen, QPixmap

from ui.theme import get_current_colors
from core.logger import log
from utils.data_fetcher import auto_download_stock_data, auto_cleanup_stock_data, format_download_report, format_cleanup_report


# ═══════════════════════════════════════════════════════════════
# 迷你走势图 Widget
# ═══════════════════════════════════════════════════════════════

class SparklineWidget(QLabel):
    """迷你走势图 — 绘制分时价格曲线"""

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)
        self.setFixedSize(120, 32)
        self._prices: List[float] = []
        self._color = "#94a3b8"

    def set_data(self, prices: List[float], color: str = None):
        """设置价格数据并绘制"""
        self._prices = prices[-60:] if len(prices) > 60 else prices  # 最多60个点
        if color:
            self._color = color
        self._draw()

    def _draw(self):
        if len(self._prices) < 2:
            self.clear()
            return

        w, h = self.width(), self.height()
        pixmap = QPixmap(w, h)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        p_min = min(self._prices)
        p_max = max(self._prices)
        p_range = p_max - p_min if p_max != p_min else 1.0

        # 填充色 (半透明)
        fill_color = QColor(self._color)
        fill_color.setAlpha(35)

        # 画填充区域
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(fill_color))

        from PyQt5.QtGui import QPainterPath
        path = QPainterPath()

        for i, p in enumerate(self._prices):
            x = i / (len(self._prices) - 1) * w
            y = h - (p - p_min) / p_range * (h - 4) - 2
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        # 闭合填充区域
        path.lineTo(w, h)
        path.lineTo(0, h)
        path.closeSubpath()
        painter.drawPath(path)

        # 画线条
        pen = QPen(QColor(self._color))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        line_path = QPainterPath()
        for i, p in enumerate(self._prices):
            x = i / (len(self._prices) - 1) * w
            y = h - (p - p_min) / p_range * (h - 4) - 2
            if i == 0:
                line_path.moveTo(x, y)
            else:
                line_path.lineTo(x, y)
        painter.drawPath(line_path)

        painter.end()
        self.setPixmap(pixmap)


# ═══════════════════════════════════════════════════════════════
# 筛选按钮
# ═══════════════════════════════════════════════════════════════

class FilterButton(QPushButton):
    """平铺筛选按钮"""

    def __init__(self, text: str, filter_key: str, parent=None):
        """初始化"""
        super().__init__(text, parent)
        self.filter_key = filter_key
        self.setCheckable(True)
        self.setFixedHeight(28)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(self._build_style(False))

    def _build_style(self, active: bool) -> str:
        c = get_current_colors()
        if active:
            return f"""
                QPushButton {{
                    background: {c.accent}; color: white;
                    border: none; border-radius: 14px;
                    padding: 0 14px; font-size: 12px;
                    font-family: "Microsoft YaHei"; font-weight: bold;
                }}
            """
        return f"""
            QPushButton {{
                background: {c.bg_app}; color: {c.text_secondary};
                border: 1px solid {c.border}; border-radius: 14px;
                padding: 0 14px; font-size: 12px;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                color: {c.accent}; border-color: {c.accent};
            }}
        """

    def set_active(self, active: bool):
        """设置激活状态 — 视觉高亮"""
        self.setChecked(active)
        self.setStyleSheet(self._build_style(active))


# ═══════════════════════════════════════════════════════════════
# 后台刷新线程
# ═══════════════════════════════════════════════════════════════

class ETFRefreshWorker(QThread):
    """后台线程：批量拉行情 + 分时数据 + ML 信号"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, codes: List[str], parent=None):
        """初始化"""
        super().__init__(parent)
        self.codes = codes

    def run(self):
        """运行回测 — ML驱动的历史数据回测"""
        try:
            from data_sources.router import DataRouter
            from strategies.engine import MLEngine
            from core.config import get_config

            router = DataRouter()
            engine = MLEngine()

            # 读取总资金（用于仓位建议计算）
            try:
                cfg = get_config()
                total_capital = getattr(cfg.backtest, 'initial_capital', 1000000)
            except Exception:
                total_capital = 1000000

            # 批量拉行情
            realtime = router.get_realtime(self.codes)

            results = []
            for code in self.codes:
                info = realtime.get(code, {})
                if not info or info.get("price", 0) <= 0:
                    continue

                item = {
                    "code": code,
                    "name": info.get("name", code),
                    "price": info.get("price", 0),
                    "change_pct": info.get("change_pct", 0),
                    "volume": info.get("volume", 0),
                    "amount": info.get("amount", 0),
                    "signal": "HOLD",
                    "confidence": 50,
                    "sparkline": [],
                }

                # 拉分时数据 (用于迷你图 + ML)
                try:
                    minute_data = router.get_minute_for_backtest(code)
                    if minute_data and len(minute_data) >= 5:
                        # 提取价格序列用于迷你图
                        item["sparkline"] = [
                            d.get("price", d.get("close", 0))
                            for d in minute_data[-60:]
                            if d.get("price", d.get("close", 0)) > 0
                        ]
                        # ML 信号（附带仓位建议）
                        if len(minute_data) >= 20:
                            sig = engine.analyze(
                                minute_data=minute_data, code=code, name=item["name"],
                                total_capital=total_capital,
                            )
                            item["signal"] = sig.signal.value
                            item["confidence"] = sig.confidence
                            # 传递完整信号对象（用于 UI 信号卡展示解释）
                            item["signal_obj"] = sig
                except Exception:
                    pass

                results.append(item)

            self.finished.emit(results)

        except Exception as e:
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════
# ETF T+0 主面板
# ═══════════════════════════════════════════════════════════════

class ETFPoolPanel(QWidget):
    """ETF T+0 扫描面板"""

    etf_clicked = pyqtSignal(str, str)
    new_signal = pyqtSignal(str, str, object, float)  # code, name, Signal对象, 价格 — 用于信号卡展示

    _SIGNAL_MAP = {
        "STRONG_BUY": ("🟢🟢", "#16a34a"),
        "BUY":        ("🟢",   "#22c55e"),
        "HOLD":       ("⚪",   "#94a3b8"),
        "SELL":       ("🔴",   "#ef4444"),
        "STRONG_SELL":("🔴🔴", "#dc2626"),
    }

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)
        self._items: List[Dict] = []
        self._filtered_items: List[Dict] = []  # 当前筛选后的可见列表
        self._worker: Optional[ETFRefreshWorker] = None
        self._refreshing = False
        self._active_filter = "all"
        self._sort_col = 3  # 默认按涨跌幅
        self._sort_order = Qt.DescendingOrder
        self._init_ui()
        self._load_watchlist()

        self._refresh_interval = 60 * 1000
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(self._refresh_interval)
        QTimer.singleShot(500, self.refresh)

    def _init_ui(self):
        c = get_current_colors()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # ── 顶栏 ──
        top_bar = QHBoxLayout()

        title = QLabel("🔄  ETF T+0  扫描池")
        title.setFont(QFont("Microsoft YaHei", 15, QFont.Bold))
        title.setStyleSheet(f"color: {c.text_primary};")
        top_bar.addWidget(title)

        self._count_label = QLabel("0 只")
        self._count_label.setFont(QFont("Microsoft YaHei", 11))
        self._count_label.setStyleSheet(f"color: {c.text_secondary};")
        top_bar.addWidget(self._count_label)

        top_bar.addStretch()

        self._search_input = SearchInput(popup=True)
        self._search_input.setFixedWidth(280)
        self._search_input.stock_selected.connect(self._on_search_selected)
        # 注入数据路由器（支持搜索自动补全）
        try:
            from data_sources.router import DataRouter
            self._search_input.set_router(DataRouter())
        except Exception:
            pass
        top_bar.addWidget(self._search_input)
        top_bar.addSpacing(8)

        add_btn = QPushButton("添加")
        add_btn.setFixedSize(50, 32)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.accent}; color: white; border: none;
                border-radius: 8px; font-size: 13px; font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{ background: {c.accent_hover}; }}
        """)
        add_btn.clicked.connect(self._add_from_input)
        top_bar.addWidget(add_btn)
        top_bar.addSpacing(10)

        init_btn = QPushButton("📥 全量导入")
        init_btn.setFixedSize(90, 32)
        init_btn.setCursor(Qt.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c.accent};
                border: 1px solid {c.accent}; border-radius: 8px;
                font-size: 12px; font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{ background: {c.accent}15; }}
        """)
        init_btn.clicked.connect(self._init_all_etfs)
        top_bar.addWidget(init_btn)
        top_bar.addSpacing(6)

        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.setFixedSize(70, 32)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {c.text_secondary};
                border: 1px solid {c.border}; border-radius: 8px;
                font-size: 12px; font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{ color: {c.accent}; border-color: {c.accent}; }}
        """)
        refresh_btn.clicked.connect(self.refresh)
        top_bar.addWidget(refresh_btn)

        layout.addLayout(top_bar)

        # ── 筛选栏: 平铺按钮 ──
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(6)

        self._filter_buttons = []
        filter_defs = [
            ("全部", "all"),
            ("🟢 买", "buy"),
            ("🔴 卖", "sell"),
            ("⚪ 观望", "hold"),
            ("🟢🟢 强买", "strong_buy"),
            ("🔴🔴 强卖", "strong_sell"),
        ]
        for text, key in filter_defs:
            btn = FilterButton(text, key)
            btn.clicked.connect(lambda checked, k=key: self._on_filter_clicked(k))
            filter_bar.addWidget(btn)
            self._filter_buttons.append(btn)
        self._filter_buttons[0].set_active(True)  # 默认选中"全部"

        filter_bar.addStretch()

        self._status_label = QLabel("")
        self._status_label.setFont(QFont("Microsoft YaHei", 10))
        self._status_label.setStyleSheet(f"color: {c.text_secondary};")
        filter_bar.addWidget(self._status_label)

        # 刷新频率: 平铺按钮
        sep_label = QLabel("  │  间隔:")
        sep_label.setStyleSheet(f"color: {c.text_secondary}; font-size: 12px;")
        filter_bar.addWidget(sep_label)

        self._interval_buttons = []
        interval_defs = [("30秒", 30_000), ("1分", 60_000), ("2分", 120_000), ("5分", 300_000)]
        for text, ms in interval_defs:
            btn = FilterButton(text, str(ms))
            btn.clicked.connect(lambda checked, v=ms: self._set_interval(v))
            filter_bar.addWidget(btn)
            self._interval_buttons.append(btn)
        self._interval_buttons[1].set_active(True)  # 默认1分钟

        layout.addLayout(filter_bar)

        # ── 进度条 ──
        self._progress = QProgressBar()
        self._progress.setFixedHeight(3)
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        self._progress.setStyleSheet(f"""
            QProgressBar {{ background: transparent; border: none; }}
            QProgressBar::chunk {{ background: {c.accent}; border-radius: 1px; }}
        """)
        layout.addWidget(self._progress)

        # ── ETF 表格 (8列) ──
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels([
            "代码", "名称", "走势", "最新价", "涨跌幅", "ML信号", "置信度", ""
        ])
        # 表头全部靠左对齐
        for col in range(8):
            hdr_item = self._table.horizontalHeaderItem(col)
            if hdr_item:
                hdr_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setFont(QFont("Microsoft YaHei", 12))
        self._table.verticalHeader().setDefaultSectionSize(42)

        hdr = self._table.horizontalHeader()
        # 7列数据用比率响应式 + 1列删除按钮固定
        # 比率: 代码15, 名称25, 走势45, 最新价15, 涨跌幅15, ML信号15, 置信度15
        self._col_ratios = [10, 15, 25, 10, 10, 10, 10, 10]
        for i in range(8):
            hdr.setSectionResizeMode(i, QHeaderView.Fixed)
        self._apply_column_widths()

        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {c.bg_surface};
                alternate-background-color: {c.bg_app};
                border: 1px solid {c.border};
                border-radius: 8px;
                gridline-color: transparent;
                selection-background-color: {c.accent}20;
                selection-color: {c.text_primary};
            }}
            QTableWidget::item {{
                padding: 4px 8px; border-bottom: 1px solid {c.border}40;
            }}
            QHeaderView::section {{
                background: {c.bg_app}; border: none;
                border-bottom: 2px solid {c.border};
                padding: 6px 8px; font-weight: bold; font-size: 12px;
                color: {c.text_secondary};
            }}
        """)

        self._table.cellClicked.connect(self._on_row_clicked)
        hdr.sectionClicked.connect(self._on_header_clicked)

        layout.addWidget(self._table, stretch=1)

    # ─── 响应式列宽 ───

    def resizeEvent(self, event):
        """窗口大小变化时按比例重新分配列宽"""
        super().resizeEvent(event)
        self._apply_column_widths()

    def _apply_column_widths(self):
        """根据比率权重计算并设置各列宽度"""
        if not hasattr(self, '_col_ratios'):
            return
        total_ratio = sum(self._col_ratios)
        table_width = self._table.width()
        usable = max(table_width - 30, 300)
        for i, ratio in enumerate(self._col_ratios):
            self._table.setColumnWidth(i, int(usable * ratio / total_ratio))

    # ─── 筛选逻辑 ───

    def _on_filter_clicked(self, key: str):
        """平铺按钮点击"""
        self._active_filter = key
        for btn in self._filter_buttons:
            btn.set_active(btn.filter_key == key)
        self._apply_filter()

    # ─── 数据加载 ───

    def _load_watchlist(self):
        try:
            from data.watchlist import load_watchlist
            items = load_watchlist()
            self._count_label.setText(f"{len(items)} 只")
            if len(items) == 0:
                self._status_label.setText("⚠️ 自选池为空，点击「全量导入」添加 ETF")
        except Exception as e:
            self._count_label.setText("0 只")
            self._status_label.setText(f"⚠️ 无法加载自选池: {str(e)[:60]}")

    def _set_interval(self, ms: int):
        """切换刷新频率"""
        self._refresh_interval = ms
        self._timer.stop()
        self._timer.start(ms)
        for btn in self._interval_buttons:
            btn.set_active(btn.filter_key == str(ms))
        labels = {"30000": "30秒", "60000": "1分钟", "120000": "2分钟", "300000": "5分钟"}
        log.signal_log("etf_pool", f"刷新间隔: {labels.get(str(ms), str(ms))}")

    def refresh(self):
        """刷新显示 — 重新加载数据"""
        if self._refreshing:
            return
        try:
            from data.watchlist import get_watchlist_codes
            codes = get_watchlist_codes()
        except Exception:
            return
        if not codes:
            self._status_label.setText("自选池为空，点击「全量导入」添加 ETF")
            return
        self._refreshing = True
        self._progress.setVisible(True)
        self._status_label.setText(f"正在刷新 {len(codes)} 只 ETF...")
        self._worker = ETFRefreshWorker(codes)
        self._worker.finished.connect(self._on_refresh_done)
        self._worker.error.connect(self._on_refresh_error)
        self._worker.start()

    def _on_refresh_done(self, results: List[Dict]):
        self._refreshing = False
        self._progress.setVisible(False)
        self._items = results
        now = datetime.now().strftime("%H:%M:%S")
        buy_count = sum(1 for r in results if r["signal"] in ("BUY", "STRONG_BUY"))
        sell_count = sum(1 for r in results if r["signal"] in ("SELL", "STRONG_SELL"))
        self._status_label.setText(
            f"更新于 {now}  |  🟢买{buy_count}  🔴卖{sell_count}  ⚪观望{len(results)-buy_count-sell_count}"
        )

        # 有买卖信号的推送到信号面板（带完整解释）
        for r in results:
            sig_obj = r.get("signal_obj")
            if sig_obj and sig_obj.signal.value in ("BUY", "STRONG_BUY", "SELL", "STRONG_SELL"):
                self.new_signal.emit(r["code"], r["name"], sig_obj, r.get("price", 0))

        self._apply_filter()

    def _on_refresh_error(self, err: str):
        self._refreshing = False
        self._progress.setVisible(False)
        self._status_label.setText(f"刷新失败: {err[:60]}")
        log.warning("etf_pool", f"刷新失败: {err}")

    # ─── 自动下载/清理 ───

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

    def _batch_download(self, codes_and_names):
        """后台线程: 批量下载数据"""
        from utils.data_fetcher import auto_download_batch
        total = len(codes_and_names)
        success = 0
        for i, result in enumerate(auto_download_batch(codes_and_names, delay=0.5)):
            main_msg, detail_msg = format_download_report(result)
            log.signal_log("data", f"[{i+1}/{total}] {main_msg}", detail_msg)
            if "✅" in main_msg:
                success += 1
        log.signal_log("data", f"批量下载完成", f"成功 {success}/{total} 只")

    # ─── 表格渲染 ───

    def _apply_filter(self):
        key = self._active_filter
        items = self._items
        if key == "buy":
            items = [i for i in items if i["signal"] in ("BUY",)]
        elif key == "sell":
            items = [i for i in items if i["signal"] in ("SELL",)]
        elif key == "hold":
            items = [i for i in items if i["signal"] == "HOLD"]
        elif key == "strong_buy":
            items = [i for i in items if i["signal"] == "STRONG_BUY"]
        elif key == "strong_sell":
            items = [i for i in items if i["signal"] == "STRONG_SELL"]
        self._filtered_items = items
        self._populate_table(items)

    def _populate_table(self, items: List[Dict]):
        c = get_current_colors()
        self._table.setRowCount(len(items))

        for row, item in enumerate(items):
            # 0: 代码
            code_item = QTableWidgetItem(item["code"])
            code_item.setFont(QFont("Consolas", 12, QFont.Bold))
            code_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._table.setItem(row, 0, code_item)

            # 1: 名称
            name_item = QTableWidgetItem(item["name"])
            name_item.setFont(QFont("Microsoft YaHei", 12))
            self._table.setItem(row, 1, name_item)

            # 2: 迷你走势图
            sparkline = SparklineWidget()
            chg = item["change_pct"]
            prices = item.get("sparkline", [])
            if prices:
                color = "#ef4444" if chg > 0 else "#22c55e" if chg < 0 else "#94a3b8"
                sparkline.set_data(prices, color)
            self._table.setCellWidget(row, 2, sparkline)

            # 3: 最新价
            price = item["price"]
            price_item = QTableWidgetItem(f"{price:.3f}")
            price_item.setFont(QFont("Consolas", 12))
            price_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._table.setItem(row, 3, price_item)

            # 4: 涨跌幅
            chg_item = QTableWidgetItem(f"{chg:+.2f}%")
            chg_item.setFont(QFont("Consolas", 11, QFont.Bold))
            chg_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if chg > 0:
                chg_item.setForeground(QBrush(QColor("#ef4444")))
            elif chg < 0:
                chg_item.setForeground(QBrush(QColor("#22c55e")))
            else:
                chg_item.setForeground(QBrush(QColor("#94a3b8")))
            self._table.setItem(row, 4, chg_item)

            # 5: ML 信号
            sig = item["signal"]
            sig_label, sig_color = self._SIGNAL_MAP.get(sig, ("⚪", "#94a3b8"))
            sig_item = QTableWidgetItem(sig_label)
            sig_item.setFont(QFont("Microsoft YaHei", 14))
            sig_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            sig_item.setForeground(QBrush(QColor(sig_color)))
            self._table.setItem(row, 5, sig_item)

            # 6: 置信度
            conf = item.get("confidence", 50)
            conf_item = QTableWidgetItem(f"{conf:.0f}%")
            conf_item.setFont(QFont("Consolas", 11))
            conf_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if conf >= 85:
                conf_item.setForeground(QBrush(QColor("#16a34a")))
            elif conf >= 70:
                conf_item.setForeground(QBrush(QColor("#2563eb")))
            else:
                conf_item.setForeground(QBrush(QColor("#94a3b8")))
            self._table.setItem(row, 6, conf_item)

            # 7: 删除按钮
            del_btn = QPushButton("✕")
            del_btn.setFixedSize(28, 28)
            del_btn.setCursor(Qt.PointingHandCursor)
            del_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: #ccc; border: none;
                    border-radius: 14px; font-size: 12px; font-weight: bold;
                }}
                QPushButton:hover {{ color: #F44336; background: #F4433615; }}
            """)
            del_btn.clicked.connect(lambda checked, code=item["code"]: self._remove_etf(code))
            self._table.setCellWidget(row, 7, del_btn)

        self._count_label.setText(f"{len(self._items)} 只")

    # ─── 交互 ───

    def _on_row_clicked(self, row, col):
        """单击行 → 跳转到行情页"""
        if 0 <= row < len(self._filtered_items):
            item = self._filtered_items[row]
            self.etf_clicked.emit(item["code"], item["name"])

    def _on_header_clicked(self, logical_index):
        if logical_index == self._sort_col:
            self._sort_order = (
                Qt.DescendingOrder if self._sort_order == Qt.AscendingOrder
                else Qt.AscendingOrder
            )
        else:
            self._sort_col = logical_index
            self._sort_order = Qt.DescendingOrder

        key_map = {0: "code", 1: "name", 3: "price", 4: "change_pct", 5: "signal", 6: "confidence"}
        key = key_map.get(logical_index, "change_pct")
        reverse = self._sort_order == Qt.DescendingOrder
        self._items.sort(key=lambda x: x.get(key, 0), reverse=reverse)
        self._apply_filter()

    def _on_search_selected(self, code: str, name: str, market: str):
        """搜索下拉选中一只 ETF → 直接添加到自选池"""
        if not code or not code.isdigit() or len(code) != 6:
            return
        try:
            from data.watchlist import add_to_watchlist
            added = add_to_watchlist(code, name, "etf")
            if added:
                self._search_input.clear()
                self._load_watchlist()
                self.refresh()
                log.signal_log("etf_pool", f"已添加: {code} {name}")
                # 自动下载数据 (后台线程)
                from threading import Thread
                Thread(target=self._auto_download, args=(code, name), daemon=True).start()
            else:
                self._search_input.clear()
                log.warning("etf_pool", f"{code} {name} 已在自选池中")
        except Exception as e:
            log.warning("etf_pool", f"添加失败: {e}")

    def _add_from_input(self):
        raw = self._search_input.text().strip()
        if not raw:
            return
        # 兼容 "513100 纳指ETF" 和纯 "513100" 两种格式
        parts = raw.split(None, 1)
        code = parts[0]
        name = parts[1] if len(parts) > 1 else ""
        if not code or not code.isdigit() or len(code) != 6:
            return
        # 没有名称时尝试从行情获取
        if not name:
            name = self._fetch_name(code) or f"ETF-{code}"
        try:
            from data.watchlist import add_to_watchlist
            added = add_to_watchlist(code, name, "etf")
            if added:
                self._search_input.clear()
                self._load_watchlist()
                self.refresh()
                log.signal_log("etf_pool", f"已添加: {code} {name}")
                # 自动下载数据 (后台线程)
                from threading import Thread
                Thread(target=self._auto_download, args=(code, name), daemon=True).start()
            else:
                self._search_input.clear()
                log.warning("etf_pool", f"{code} {name} 已在自选池中")
        except Exception as e:
            log.warning("etf_pool", f"添加失败: {e}")

    def _fetch_name(self, code: str) -> str:
        """尝试从行情接口获取股票名称"""
        try:
            from data_sources.router import DataRouter
            router = DataRouter()
            data = router.get_realtime([code])
            if code in data:
                return data[code].get("name", "")
        except Exception:
            pass
        return ""

    def _remove_etf(self, code: str):
        try:
            from data.watchlist import remove_from_watchlist
            remove_from_watchlist(code)
            self._items = [i for i in self._items if i["code"] != code]
            self._apply_filter()
            self._load_watchlist()
            log.signal_log("etf_pool", f"已移除: {code}")
            # 自动清理数据 (后台线程)
            from threading import Thread
            Thread(target=self._auto_cleanup, args=(code,), daemon=True).start()
        except Exception as e:
            log.warning("etf_pool", f"移除失败: {e}")

    def _init_all_etfs(self):
        try:
            from data.watchlist import init_etf_watchlist, load_watchlist
            from threading import Thread

            # 记录导入前的自选池
            before = {w["code"] for w in load_watchlist()}

            added = init_etf_watchlist()
            self._load_watchlist()
            self.refresh()
            log.signal_log("etf_pool", f"全量导入完成，新增 {added} 只")

            # 获取新增的ETF列表，后台批量下载数据
            after = load_watchlist()
            new_etfs = [(w["code"], w["name"]) for w in after if w["code"] not in before]
            if new_etfs:
                log.signal_log("etf_pool", f"开始下载 {len(new_etfs)} 只新增ETF数据", "")
                Thread(target=self._batch_download, args=(new_etfs,), daemon=True).start()
        except Exception as e:
            log.warning("etf_pool", f"全量导入失败: {e}")
