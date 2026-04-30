#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓管理页 - 搜索添加 + 持仓表格 + 本地持久化 + 实时行情
"""

import os
import json
from datetime import datetime
import threading
from threading import Thread

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QPushButton, QTableWidget,
                             QTableWidgetItem, QLineEdit, QFrame)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QMetaObject, Q_ARG, pyqtSlot
from PyQt5.QtGui import QFont, QColor

from data_sources import DataRouter
from core.logger import log
from ui.panels.search import SearchInput
from data.cache_manager import get_cache_manager
from utils.data_fetcher import auto_download_stock_data, auto_cleanup_stock_data, format_download_report, format_cleanup_report


class PositionPanel(QFrame):
    """持仓管理 - 搜索添加 + 持仓表格 + 本地持久化 + 实时行情"""
    stock_clicked = pyqtSignal(str, str)
    positions_changed = pyqtSignal()  # 持仓数据变化信号
    _prices_ready = pyqtSignal(dict)  # 后台线程 → 主线程安全传递价格

    DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "positions.json")

    def __init__(self):
        """初始化"""
        super().__init__()
        self._positions = []  # [(code, name, volume, cost, timestamp), ...]
        self._prices = {}
        self._router: DataRouter = None
        self._row_to_index_map = {}  # 表格行 -> _positions 原始索引
        self._positions_lock = threading.Lock()  # 保护 _positions 的并发访问
        self._init_ui()
        self._load_from_file()

        # 价格信号连接 (后台线程安全)
        self._prices_ready.connect(self._on_prices_ready)

        # 定时刷新行情 (交易时段5秒，非交易时段60秒)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_prices_with_check)
        self._refresh_timer.start(5000)
        QTimer.singleShot(500, self._refresh_prices)

    def set_router(self, router: DataRouter):
        """设置数据路由器实例"""
        self._router = router
        self.search_input.set_router(router)

    def get_position_count(self) -> int:
        """获取持仓数量（线程安全）"""
        with self._positions_lock:
            return len(self._positions)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # ---- 添加区域 ----
        add_card = QFrame()
        add_card.setStyleSheet("""
            QFrame {
                background: #fafbfc;
                border: 1px solid #eee;
                border-radius: 12px;
            }
        """)
        add_layout = QVBoxLayout(add_card)
        add_layout.setContentsMargins(16, 14, 16, 14)
        add_layout.setSpacing(10)

        self.search_input = SearchInput()
        self.search_input.stock_selected.connect(self._on_stock_selected)
        self.search_input._on_empty_callback = self._hide_detail_card
        add_layout.addWidget(self.search_input)

        self._selected_code = ""
        self._selected_name = ""

        # 选中详情卡片
        self.detail_card = QFrame()
        self.detail_card.setVisible(False)
        self.detail_card.setStyleSheet("""
            QFrame {
                background: transparent;
                border: 1px solid #d0dcf0;
                border-radius: 8px;
            }
        """)
        detail_outer = QVBoxLayout(self.detail_card)
        detail_outer.setContentsMargins(12, 10, 12, 10)
        detail_outer.setSpacing(0)

        row = QHBoxLayout()
        row.setSpacing(16)

        # 数量
        self.vol_lbl = QLabel("持仓数量")
        self.vol_lbl.setStyleSheet("color: #999; font-size: 12px; background: transparent;")
        self.volume_input = QLineEdit()
        self.volume_input.setPlaceholderText("例: 100")
        self.volume_input.setFixedHeight(36)
        self.volume_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d0dcf0; border-radius: 6px;
                padding: 0 10px; font-size: 13px; background: white;
            }
            QLineEdit:focus { border: 1px solid #1a73e8; }
        """)
        row.addWidget(self.vol_lbl)
        row.addWidget(self.volume_input, 1)

        # 成本价
        self.cost_lbl = QLabel("成本价")
        self.cost_lbl.setStyleSheet("color: #999; font-size: 12px; background: transparent;")
        self.cost_input = QLineEdit()
        self.cost_input.setPlaceholderText("例: 1700.00")
        self.cost_input.setFixedHeight(36)
        self.cost_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d0dcf0; border-radius: 6px;
                padding: 0 10px; font-size: 13px; background: white;
            }
            QLineEdit:focus { border: 1px solid #1a73e8; }
        """)
        row.addWidget(self.cost_lbl)
        row.addWidget(self.cost_input, 1)

        # 现价
        self.price_lbl = QLabel("现价")
        self.price_lbl.setStyleSheet("color: #999; font-size: 12px; background: transparent;")
        self.price_display = QLabel("--")
        self.price_display.setFixedHeight(36)
        self.price_display.setMinimumWidth(120)
        self.price_display.setAlignment(Qt.AlignCenter)
        self.price_display.setStyleSheet("""
            QLabel {
                border: 1px solid #d0dcf0; border-radius: 6px;
                padding: 0 10px; font-size: 13px; font-weight: bold;
                background: white; color: #333;
            }
        """)
        row.addWidget(self.price_lbl)
        row.addWidget(self.price_display, 1)

        # 添加按钮
        add_btn = QPushButton("+ 添加")
        add_btn.setFixedSize(100, 36)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet("""
            QPushButton {
                background-color: #1a73e8;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-family: "Microsoft YaHei";
            }
            QPushButton:hover { background-color: #1557b0; }
        """)
        add_btn.clicked.connect(self._add_position)
        row.addWidget(add_btn)

        detail_outer.addLayout(row)
        add_layout.addWidget(self.detail_card)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #F44336; font-size: 11px; background: transparent;")
        self._error_label.setVisible(False)
        add_layout.addWidget(self._error_label)

        layout.addWidget(add_card)

        # ---- 持仓表格 ----
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        headers = ["时间", "代码", "名称", "数量", "成本价", "现价", "盈亏", "盈亏率", "操作"]
        self.table.setHorizontalHeaderLabels(headers)
        # 表头对齐: 统一左对齐
        for col in range(len(headers)):
            item = self.table.horizontalHeaderItem(col)
            if item:
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #eee;
                border-radius: 8px;
                gridline-color: #f0f0f0;
                outline: none;
                alternate-background-color: #fafbfd;
            }
            QTableWidget::item {
                padding: 6px;
                border: none;
            }
            QTableWidget::item:selected {
                background-color: #edf1fa;
            }
            QTableWidget::item:focus {
                border: none;
                outline: none;
            }
            QHeaderView::section {
                background-color: #f8f9fa;
                border: none;
                border-bottom: 2px solid #eee;
                padding: 8px;
                font-weight: bold;
                color: #888;
                text-align: left;
            }
        """)
        header = self.table.horizontalHeader()
        # 9列比例权重（窗口缩放时按比例分配）
        self._col_ratios = [25, 15, 25, 15, 15, 15, 15, 15, 15]
        for i in range(9):
            header.setSectionResizeMode(i, header.Fixed)
        # 初始分配
        self._apply_column_widths()

        self.table.cellClicked.connect(self._on_row_click)
        layout.addWidget(self.table, stretch=1)

    # ========== 持久化 ==========

    def resizeEvent(self, event):
        """窗口大小变化时按比例重新分配列宽"""
        super().resizeEvent(event)
        self._apply_column_widths()

    def _apply_column_widths(self):
        """根据比例权重计算并设置各列宽度"""
        if not hasattr(self, '_col_ratios'):
            return
        total_ratio = sum(self._col_ratios)
        table_width = self.table.width()
        # 减去滚动条和边框预留（约30px）
        usable = max(table_width - 30, 300)
        for i, ratio in enumerate(self._col_ratios):
            self.table.setColumnWidth(i, int(usable * ratio / total_ratio))

    def _load_from_file(self):
        try:
            if os.path.exists(self.DATA_FILE):
                with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                loaded = [(d["code"], d["name"], d["volume"], d["cost"], d.get("timestamp", "")) for d in data]
                with self._positions_lock:
                    self._positions = loaded
        except Exception:
            with self._positions_lock:
                self._positions = []

        # 同步缓存管理器：初始化持仓跟踪列表
        self._sync_cache_manager()

        if self._positions:
            self._refresh_table()

    def _sync_cache_manager(self):
        """同步持仓代码到缓存管理器（添加/删除时调用）"""
        try:
            with self._positions_lock:
                codes = list(set(p[0] for p in self._positions))
            cache = get_cache_manager()
            cache.on_position_changed(codes)
        except Exception as e:
            log.warning("position", f"同步缓存管理器失败: {e}")

    def _save_to_file(self):
        """原子写入持仓数据：先写临时文件，再 rename 替换"""
        import tempfile
        try:
            os.makedirs(os.path.dirname(self.DATA_FILE), exist_ok=True)
            data = [{"code": c, "name": n, "volume": v, "cost": p, "timestamp": t} for c, n, v, p, t in self._positions]
            # 原子写入：临时文件 + rename
            dir_name = os.path.dirname(self.DATA_FILE)
            fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, self.DATA_FILE)
            except Exception:
                # 清理临时文件
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise
        except Exception as e:
            log.error("position", f"保存持仓失败: {e}")

    # ========== 实时行情 (通过 DataRouter) ==========

    def _refresh_prices_with_check(self):
        """交易时段刷新行情，非交易时段降低频率"""
        from datetime import datetime, time as dtime
        now = datetime.now()
        is_trading = False
        if now.weekday() < 5:
            t = now.time()
            is_trading = (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))

        # 动态调整定时器间隔
        target_interval = 5000 if is_trading else 60000
        if self._refresh_timer.interval() != target_interval:
            self._refresh_timer.setInterval(target_interval)

        if is_trading:
            self._refresh_prices()

    def _refresh_prices(self):
        if not self._positions or not self._router:
            return
        Thread(target=self._fetch_prices, daemon=True).start()

    def _fetch_prices(self):
        """后台线程: 批量获取实时价格 (不直接修改UI或self属性)"""
        # 在锁内复制持仓列表，避免与主线程并发修改
        with self._positions_lock:
            codes = list(set(c for c, _, _, _, _ in self._positions))
        if not codes:
            return
        data = self._router.get_realtime(codes)
        prices = {}
        for code, info in data.items():
            price = info.get("price", 0)
            if price > 0:
                prices[code] = price
        if prices:
            self._prices_ready.emit(prices)

    @pyqtSlot(dict)
    def _on_prices_ready(self, prices):
        """主线程: 接收价格数据并刷新表格"""
        self._prices.update(prices)
        self._refresh_table()

    # ========== 添加/删除 ==========

    def _on_stock_selected(self, code, name, sector):
        self._selected_code = code
        self._selected_name = name
        self.price_display.setText("加载中...")
        self.detail_card.show()
        Thread(target=self._fetch_single_price, args=(code,), daemon=True).start()

    @pyqtSlot(str)
    def _update_price_display(self, text):
        """主线程槽：更新价格显示（从后台线程通过 invokeMethod 安全调用）"""
        # 检查控件是否仍然存在（防止面板关闭后信号到达）
        if self.price_display and not self.price_display.isHidden():
            self.price_display.setText(text)

    def _fetch_single_price(self, code):
        if not self._router:
            log.error("data", f"实时行情获取失败: {code}", "数据源未就绪")
            QMetaObject.invokeMethod(self, "_update_price_display",
                                     Qt.QueuedConnection, Q_ARG(str, "\u6570\u636e\u6e90\u672a\u5c31\u7eea"))
            return
        data = self._router.get_realtime([code])
        if code in data:
            price = data[code].get("price", 0)
            if price > 0:
                QMetaObject.invokeMethod(self, "_update_price_display",
                                         Qt.QueuedConnection, Q_ARG(str, f"\u00a5 {price:.2f}"))
                return
        log.warning("data", f"实时行情获取失败: {code}", "所有数据源均未返回有效价格")
        QMetaObject.invokeMethod(self, "_update_price_display",
                                 Qt.QueuedConnection, Q_ARG(str, "\u83b7\u53d6\u5931\u8d25"))

    def _hide_detail_card(self):
        if not self._selected_code and not self.detail_card.isVisible():
            return
        self.detail_card.hide()
        self.volume_input.clear()
        self.cost_input.clear()
        self.price_display.setText("--")
        self._selected_code = ""
        self._selected_name = ""

    def _parse_search_input(self):
        if self._selected_code:
            return self._selected_code, self._selected_name
        text = self.search_input.text().strip()
        if not text:
            return "", ""
        parts = text.split()
        if len(parts) >= 2:
            return parts[0], parts[1]
        if text.isdigit() and len(text) == 6:
            return text, text
        return "", text

    def _show_error(self, msg):
        self._error_label.setText(f"\u26a0 {msg}")
        self._error_label.setVisible(True)

    def _clear_error(self):
        self._error_label.setVisible(False)

    def _add_position(self):
        self._clear_error()
        code, name = self._parse_search_input()
        vol_text = self.volume_input.text().strip()
        cost_text = self.cost_input.text().strip()

        if not code:
            self._show_error("请输入股票代码或从下拉列表选择")
            return
        if not code.isdigit() or len(code) != 6:
            self._show_error("股票代码必须是6位数字")
            return
        if not vol_text or not vol_text.isdigit() or int(vol_text) <= 0:
            self._show_error("持仓数量必须是正整数")
            return
        if int(vol_text) % 100 != 0:
            self._show_error("A股持仓数量必须是100的整数倍(1手=100股)")
            return
        if not cost_text:
            self._show_error("请输入成本价")
            return
        try:
            cost = float(cost_text)
            if cost <= 0:
                self._show_error("成本价必须大于0")
                return
        except ValueError:
            self._show_error("成本价格式不正确")
            return

        # 添加时间戳（加锁保护）
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._positions_lock:
            self._positions.append((code, name, int(vol_text), cost, timestamp))

        # 同步缓存管理器：更新持仓跟踪列表
        self._sync_cache_manager()

        self._refresh_table()
        self._save_to_file()
        self._refresh_prices()

        log.signal_log("position", f"添加持仓: {code} {name}",
                       f"{vol_text}股 @ ¥{cost:.2f}")

        # 自动下载数据 (后台线程)
        Thread(target=self._auto_download, args=(code, name), daemon=True).start()

        self.positions_changed.emit()

        self.search_input.clear()
        self.volume_input.clear()
        self.cost_input.clear()
        self.price_display.setText("--")
        self._selected_code = ""
        self._selected_name = ""
        self.detail_card.hide()

    def _on_row_click(self, row, column):
        """表格行点击"""
        if row in self._row_to_index_map:
            # 数据行
            original_idx = self._row_to_index_map[row]
            if 0 <= original_idx < len(self._positions):
                code, name = self._positions[original_idx][0], self._positions[original_idx][1]
                self.stock_clicked.emit(code, name)

    # ========== 表格渲染 ==========

    @pyqtSlot()
    def _refresh_table(self):
        if not self._positions:
            self.table.setRowCount(0)
            return

        from collections import defaultdict

        # 按股票代码分组，保持添加顺序，保留原始索引
        grouped = defaultdict(list)
        for i, pos in enumerate(self._positions):
            grouped[pos[0]].append((i, pos))  # code -> [(original_idx, record), ...]

        # 计算总行数: 所有记录 + 每组汇总 + 总汇总
        total_rows = sum(len(recs) + 1 for recs in grouped.values()) + 1  # +1 总汇总
        self.table.setRowCount(total_rows)

        # 全局汇总
        global_total_vol = 0
        global_total_cost = 0.0
        global_total_value = 0.0

        row = 0
        self._row_to_index_map = {}  # 表格行 -> _positions 索引

        for code, records in grouped.items():
            name = records[0][1][1]
            stock_total_vol = 0
            stock_total_cost = 0.0
            stock_total_value = 0.0
            has_price = code in self._prices
            current_price = self._prices.get(code, 0.0)

            # 渲染该股票的每条记录
            for original_idx, (c, n, vol, cost, timestamp) in records:
                if has_price:
                    value = current_price * vol
                    profit = (current_price - cost) * vol
                    rate = (current_price - cost) / cost * 100 if cost > 0 else 0
                else:
                    value = 0.0
                    profit = 0.0
                    rate = 0.0

                stock_total_vol += vol
                stock_total_cost += cost * vol
                stock_total_value += value
                global_total_vol += vol
                global_total_cost += cost * vol
                global_total_value += value

                self._row_to_index_map[row] = original_idx

                self._render_data_row(row, timestamp, c, n, vol, cost,
                                      current_price if has_price else None,
                                      profit, rate, original_idx)
                row += 1

            # ---- 该股票小计行 ----
            stock_profit = stock_total_value - stock_total_cost
            stock_rate = (stock_profit / stock_total_cost * 100) if stock_total_cost > 0 else 0

            self._render_summary_row(row, f" 小计", stock_total_vol,
                                     stock_total_cost / stock_total_vol if stock_total_vol > 0 else 0,
                                     current_price if has_price else None,
                                     stock_profit, stock_rate)
            row += 1

        # ---- 全局总汇总行 ----
        global_profit = global_total_value - global_total_cost
        global_rate = (global_profit / global_total_cost * 100) if global_total_cost > 0 else 0

        self._render_summary_row(row, " 总汇总", global_total_vol,
                                 global_total_cost / global_total_vol if global_total_vol > 0 else 0,
                                 None,  # 总汇总无统一现价
                                 global_profit, global_rate)

    def _render_data_row(self, row, time_str, code, name, vol, cost, current, profit, rate, del_idx):
        """渲染数据行（带删除按钮）"""
        self._set_item(row, 0, time_str)
        self._set_item(row, 1, code)
        self._set_item(row, 2, name)
        self._set_item(row, 3, str(vol))
        self._set_item(row, 4, f"{cost:.2f}")

        if current is not None and current > 0:
            self._set_item(row, 5, f"{current:.2f}")
            p_text = f"{profit:+,.2f}"
            r_text = f"{rate:+.2f}%"
        else:
            self._set_item(row, 5, "—")
            p_text = "—"
            r_text = "—"

        p_item = QTableWidgetItem(p_text)
        r_item = QTableWidgetItem(r_text)
        if current is not None and current > 0:
            color = QColor("#d32f2f") if profit < 0 else QColor("#2e7d32")
        else:
            color = QColor("#999")
        p_item.setForeground(color)
        r_item.setForeground(color)
        self.table.setItem(row, 6, p_item)
        self.table.setItem(row, 7, r_item)

        # 删除按钮
        del_btn = QPushButton("-")
        del_btn.setFixedSize(26, 26)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setStyleSheet("""
            QPushButton {
                background-color: #f5f5f5; border: 1px solid #e0e0e0;
                border-radius: 13px; color: #bbb;
                font-size: 16px; font-weight: bold;
            }
            QPushButton:hover { background-color: #ffebee; border-color: #ef5350; color: #ef5350; }
        """)
        del_btn.clicked.connect(lambda checked, idx=del_idx: self._delete_row(idx))
        self.table.setCellWidget(row, 8, del_btn)

    def _render_summary_row(self, row, label, vol, avg_cost, current_price, profit, rate):
        """渲染汇总行 - 显示当前价格，成本=加权平均"""
        if current_price is not None:
            price_str = f"{current_price:.2f}"
        else:
            price_str = "—"  # 总汇总无统一价格

        items_data = [
            "",           # 时间(空)
            "",           # 代码(空)
            label,        # 名称: "小计" 或 "总汇总"
            str(vol),     # 数量
            f"{avg_cost:.2f}",   # 成本(加权平均)
            price_str,    # 当前价格
            f"{profit:+,.2f}",
            f"{rate:+.2f}%",
            "",          # 操作列空
        ]

        for col, text in enumerate(items_data):
            item = QTableWidgetItem(text)
            item.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            if col in (6, 7):  # 盈亏、盈亏率着色
                color = "#d32f2f" if profit < 0 else "#2e7d32"
                item.setForeground(QColor(color))
            self.table.setItem(row, col, item)

    def _set_item(self, row, col, text):
        """设置普通表格项"""
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setItem(row, col, item)

    def _delete_row(self, original_idx):
        """根据原始索引删除单条记录"""
        with self._positions_lock:
            if not (0 <= original_idx < len(self._positions)):
                return
            code, name, vol, cost, _ = self._positions[original_idx]
            self._positions.pop(original_idx)

        # 同步缓存管理器：更新持仓跟踪列表
        self._sync_cache_manager()

        self._refresh_table()
        self._save_to_file()
        log.signal_log("position", f"删除持仓: {code} {name}",
                       f"{vol}股 @ ¥{cost:.2f}")

        # 自动清理数据 (后台线程)
        Thread(target=self._auto_cleanup, args=(code, name), daemon=True).start()

        self.positions_changed.emit()

    def _auto_download(self, code, name):
        """后台线程: 自动下载数据"""
        try:
            result = auto_download_stock_data(code, name, scene="add")
            main_msg, detail_msg = format_download_report(result)
            log.signal_log("data", main_msg, detail_msg)
        except Exception as e:
            log.warning("data", f"{code} 数据下载异常: {e}")

    def _auto_cleanup(self, code, name):
        """后台线程: 自动清理数据"""
        try:
            result = auto_cleanup_stock_data(code)
            log.signal_log("data", format_cleanup_report(result), "")
        except Exception as e:
            log.warning("data", f"{code} 数据清理异常: {e}")
