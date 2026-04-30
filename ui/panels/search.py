#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
搜索自动补全框
popup=False: 内嵌下拉 (用于持仓页)
popup=True:  弹出式下拉 (用于行情页，不挤压布局)
"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QListWidget, QApplication
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QEvent
from PyQt5.QtGui import QFont
from threading import Thread

from data_sources import DataRouter


class SearchInput(QWidget):
    """
    搜索自动补全框
    popup=False: 内嵌下拉 (用于持仓页)
    popup=True:  弹出式下拉 (用于行情页，不挤压布局)
    """
    stock_selected = pyqtSignal(str, str, str)  # code, name, market
    _results_ready = pyqtSignal(list)

    def __init__(self, popup=False, parent=None):
        """初始化"""
        super().__init__(parent)
        self._popup = popup
        self._on_empty_callback = None
        self._skip_hide_once = False
        self._router: DataRouter = None

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(0)

        # 输入框
        self._input = QLineEdit()
        self._input.setPlaceholderText("\U0001f50d 输入代码/名称/拼音搜索，如 600 / 茅台 / gzmt...")
        self._input.setFixedHeight(40)
        self._input.setFont(QFont("Microsoft YaHei", 13))
        self._input.setStyleSheet("""
            QLineEdit {
                border: 2px solid #e0e0e0; border-radius: 8px;
                padding: 0 14px; background: white; font-size: 13px;
            }
            QLineEdit:focus { border-color: #1a73e8; }
        """)
        self._main_layout.addWidget(self._input)

        # 下拉列表
        self._list = QListWidget()
        if popup:
            # Qt.ToolTip 不抢键盘焦点，避免搜索结果弹出时输入框失焦"卡死"
            self._list.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
            self._list.setFocusPolicy(Qt.NoFocus)
            # 安装事件过滤器：点击下拉列表外区域时关闭
            QApplication.instance().installEventFilter(self)
        else:
            self._list.setVisible(False)
        self._list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ddd; border-radius: 6px;
                background: white; outline: none;
            }
            QListWidget::item {
                padding: 8px 12px; border-bottom: 1px solid #f0f0f0;
            }
            QListWidget::item:selected {
                background-color: #e8f0fe; color: #1a73e8;
            }
            QListWidget::item:hover {
                background-color: #f5f8ff;
            }
        """)
        self._list.itemClicked.connect(self._on_select)
        if not popup:
            self._main_layout.addWidget(self._list)

        # 信号 & 定时器
        self._results_ready.connect(self._show_results)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._do_search)
        self._pending_text = ""
        self._search_epoch = 0  # 搜索版本号，新搜索覆盖旧搜索
        self._just_selected = False  # 选中标志：阻止 setText 触发的重复搜索
        self._input.textChanged.connect(self._on_text_changed)

    def set_router(self, router: DataRouter):
        """注入数据路由器"""
        self._router = router

    # ---- 公开接口 ----

    def text(self):
        """获取当前文本内容"""
        return self._input.text()

    def setText(self, text):
        """设置文本内容"""
        self._skip_hide_once = True
        self._input.setText(text)

    def clear(self):
        """清空内容"""
        self._input.clear()

    @property
    def line_edit(self):
        """获取内部的QLineEdit控件"""
        return self._input

    # ---- 搜索逻辑 ----

    def _on_text_changed(self, text):
        if self._just_selected:
            self._just_selected = False
            return
        if self._skip_hide_once:
            self._skip_hide_once = False
            self._pending_text = text.strip()
            if self._pending_text:
                self._search_timer.stop()
                self._search_timer.start(300)
            return
        self._pending_text = text.strip()
        self._search_timer.stop()  # 停止上一次定时器
        if not self._pending_text:
            self._search_epoch += 1  # 取消所有进行中的搜索
            self._list.setVisible(False)
            if self._on_empty_callback:
                self._on_empty_callback()
            return
        if self._on_empty_callback:
            self._on_empty_callback()
        self._search_timer.start(300)

    def _do_search(self):
        text = self._pending_text
        if not text:
            return
        self._search_epoch += 1
        epoch = self._search_epoch
        Thread(target=self._fetch_search, args=(text, epoch), daemon=True).start()

    def _fetch_search(self, keyword, epoch):
        """通过 DataRouter 搜索（epoch 版本号防过期）"""
        try:
            if self._router:
                results = self._router.search(keyword)
            else:
                results = []
            # 版本号不匹配 = 已被新搜索覆盖，丢弃结果
            if epoch != self._search_epoch:
                return
            self._results_ready.emit(results)
        except Exception:
            pass

    def _show_results(self, results):
        # 用户已清空输入或搜索已过期，不显示结果
        if not self._pending_text:
            self._list.setVisible(False)
            return
        self._list.clear()
        if not results:
            self._list.setVisible(False)
            return
        for code, name, market in results:
            self._list.addItem(f"{code}  {name}  ({market})")
        visible_rows = min(len(results), 5)
        list_h = visible_rows * 36 + 8

        if self._popup:
            # 弹出式: 定位到输入框下方
            pos = self._input.mapToGlobal(self._input.rect().bottomLeft())
            self._list.setFixedSize(self._input.width(), list_h)
            self._list.move(pos.x(), pos.y() + 4)
        else:
            self._list.setFixedHeight(list_h)

        self._list.setVisible(True)

    def _on_select(self, item):
        text = item.text()
        parts = text.split()
        if len(parts) >= 2:
            code = parts[0]
            name = parts[1]
            market = parts[2].strip("()") if len(parts) >= 3 else ""
            self._just_selected = True
            self._input.setText(f"{code} {name}")
            self._list.setVisible(False)
            self.stock_selected.emit(code, name, market)

    def eventFilter(self, obj, event):
        """点击下拉列表外区域时关闭列表（Qt.ToolTip 不会自动消失）"""
        if self._popup and self._list.isVisible():
            if event.type() == QEvent.MouseButtonPress:
                global_pos = event.globalPos()
                list_geo = self._list.frameGeometry()
                input_geo = self._input.mapToGlobal(self._input.rect().topLeft())
                input_rect = self._input.rect().translated(input_geo)
                # 点击位置不在下拉列表也不在输入框 → 关闭
                if not list_geo.contains(global_pos) and not input_rect.contains(global_pos):
                    self._list.setVisible(False)
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        """尺寸变更事件 — PyQt5布局回调"""
        super().resizeEvent(event)
