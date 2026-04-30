#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统设置页 v3.0
==============

特性:
- 主题切换 (MetricCard 风格卡片)
- 数据管理
- 通知设置
- 关于信息
"""

import os
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QPushButton, QFrame, QWidget,
                             QScrollArea, QCheckBox, QComboBox,
                             QMessageBox)
from PyQt5.QtCore import Qt, pyqtSignal, QSettings
from PyQt5.QtGui import QFont

from ui.theme import ThemeManager, get_current_colors, get_all_themes
from core.logger import log


# ═══════════════════════════════════════════════════════════════
# 设置项组件
# ═══════════════════════════════════════════════════════════════

class SettingItem(QFrame):
    """设置项组件"""

    clicked = pyqtSignal()

    def __init__(self, icon: str, title: str, desc: str,
                 value_widget=None, parent=None):
        super().__init__(parent)
        self._init_ui(icon, title, desc, value_widget)

    def _init_ui(self, icon, title, desc, value_widget):
        c = get_current_colors()

        self.setStyleSheet(f"""
            SettingItem {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
            }}
            SettingItem:hover {{
                border-color: {c.border_strong};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(16)

        # 左侧: 图标 + 文字
        left = QVBoxLayout()
        left.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        if icon:
            icon_lbl = QLabel(icon)
            icon_lbl.setFont(QFont("Microsoft YaHei", 16))
            title_row.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        title_lbl.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        left.addLayout(title_row)

        desc_lbl = QLabel(desc)
        desc_lbl.setFont(QFont("Microsoft YaHei", 11))
        desc_lbl.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        left.addWidget(desc_lbl)

        layout.addLayout(left, stretch=1)
        layout.addStretch()

        # 右侧: 值控件
        if value_widget:
            layout.addWidget(value_widget)

    def mousePressEvent(self, event):
        self.clicked.emit()


# ═══════════════════════════════════════════════════════════════
# 主题卡片 (MetricCard 风格)
# ═══════════════════════════════════════════════════════════════

class ThemeCard(QFrame):
    """主题选择卡片 — 与策略页 MetricCard 统一风格"""

    selected = pyqtSignal(str)

    def __init__(self, theme_key: str, parent=None):
        """初始化"""
        super().__init__(parent)
        self.theme_key = theme_key
        self._is_selected = False
        self._init_ui()

    def _init_ui(self):
        meta = get_all_themes()[self.theme_key]
        c = get_current_colors()

        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        # 标题行: 图标 + 名称 (和 MetricCard 一样)
        title_row = QHBoxLayout()

        icon_lbl = QLabel(meta.get('icon', '●'))
        icon_lbl.setFont(QFont("Microsoft YaHei", 14))
        title_row.addWidget(icon_lbl)

        title_lbl = QLabel(meta.get('name', self.theme_key))
        title_lbl.setFont(QFont("Microsoft YaHei", 11))
        title_lbl.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        layout.addLayout(title_row)

        # 预览色条 (代替 MetricCard 的大数值)
        preview = QFrame()
        preview.setFixedHeight(28)
        preview.setStyleSheet(f"""
            QFrame {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {meta.get('preview_bg', '#fff')},
                    stop:0.5 {meta.get('preview_surface', '#fafbfc')},
                    stop:1 {meta.get('preview_accent', '#3b82f6')});
                border-radius: 6px;
            }}
        """)
        layout.addWidget(preview)

        # 描述 (小字)
        desc_lbl = QLabel(meta.get('desc', ''))
        desc_lbl.setFont(QFont("Microsoft YaHei", 10))
        desc_lbl.setStyleSheet(f"color: {c.text_muted}; background: transparent;")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        self._apply_style()

    def _apply_style(self):
        c = get_current_colors()

        if self._is_selected:
            self.setStyleSheet(f"""
                ThemeCard {{
                    background: {c.bg_surface};
                    border: 2px solid {c.accent};
                    border-radius: 12px;
                    padding: 16px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                ThemeCard {{
                    background: {c.bg_surface};
                    border: 1px solid {c.border};
                    border-radius: 12px;
                    padding: 16px;
                }}
                ThemeCard:hover {{
                    border-color: {c.border_strong};
                }}
            """)

    def set_selected(self, selected: bool):
        self._is_selected = selected
        self._apply_style()

    def mousePressEvent(self, event):
        self.selected.emit(self.theme_key)


# ═══════════════════════════════════════════════════════════════
# 主设置面板
# ═══════════════════════════════════════════════════════════════

class SettingsPanel(QFrame):
    """系统设置页 v3.0"""

    theme_changed = pyqtSignal(str)
    data_cleared = pyqtSignal()

    def __init__(self):
        """初始化"""
        super().__init__()
        self._settings = QSettings("XmLH", "StockManager")
        self._theme_cards = {}
        self._init_ui()

        # 注册主题变化
        ThemeManager.on_change(self._on_theme_changed)

    def _init_ui(self):
        c = get_current_colors()

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        # 内容容器
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(24)
        layout.setContentsMargins(0, 0, 0, 0)

        # ═══ 标题 ═══
        title = QLabel("⚙️ 系统设置")
        title.setFont(QFont("Microsoft YaHei", 22, QFont.Bold))
        title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(title)

        # ═══ 主题设置 ═══
        theme_title = QLabel("🎨 主题外观")
        theme_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        theme_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(theme_title)

        # 主题卡片网格 — 和策略页模型状态一样的 QGridLayout + 4列
        theme_grid = QFrame()
        grid_layout = QGridLayout(theme_grid)
        grid_layout.setSpacing(12)

        current = ThemeManager.get_theme()
        for i, key in enumerate(["light", "dark", "blue"]):
            card = ThemeCard(key)
            card.selected.connect(self._on_theme_selected)
            grid_layout.addWidget(card, 0, i)
            self._theme_cards[key] = card
            if key == current:
                card.set_selected(True)

        layout.addWidget(theme_grid)

        # ═══ 数据管理 ═══
        data_title = QLabel("💾 数据管理")
        data_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        data_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(data_title)

        # 清除缓存按钮
        clear_cache_btn = QPushButton("🗑️ 清除缓存数据")
        clear_cache_btn.setFixedHeight(40)
        clear_cache_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                border-radius: 8px;
                font-size: 13px;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                background: {c.red_bg};
                color: {c.red};
                border-color: {c.red};
            }}
        """)
        clear_cache_btn.clicked.connect(self._clear_cache)

        clear_cache_item = SettingItem(
            "", "清除缓存", "清除临时数据和下载的K线缓存",
            clear_cache_btn
        )
        layout.addWidget(clear_cache_item)

        # 重置设置按钮
        reset_btn = QPushButton("↺ 重置所有设置")
        reset_btn.setFixedHeight(40)
        reset_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                border-radius: 8px;
                font-size: 13px;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                background: {c.orange_bg};
                color: {c.orange};
                border-color: {c.orange};
            }}
        """)
        reset_btn.clicked.connect(self._reset_settings)

        reset_item = SettingItem(
            "", "重置设置", "恢复所有设置为默认值",
            reset_btn
        )
        layout.addWidget(reset_item)

        # ═══ 止损策略 ═══
        stoploss_title = QLabel("🛡️ 止损策略")
        stoploss_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        stoploss_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(stoploss_title)

        self._stoploss_combo = QComboBox()
        self._stoploss_combo.addItems([
            "跟踪止损 (默认)",
            "固定止损",
            "ATR 波动率止损",
        ])
        self._stoploss_combo.setFixedWidth(180)
        self._stoploss_combo.setStyleSheet(f"""
            QComboBox {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                border-radius: 6px;
                padding: 6px 10px;
                font-family: "Microsoft YaHei";
                font-size: 12px;
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background: {c.bg_surface};
                color: {c.text_primary};
                selection-background-color: {c.accent_light};
            }}
        """)

        # 读取当前配置
        from core.config import get_config
        _cfg = get_config()
        _type_map = {"trailing": 0, "fixed": 1, "atr": 2}
        self._stoploss_combo.setCurrentIndex(_type_map.get(_cfg.stop_loss.type, 0))
        self._stoploss_combo.currentIndexChanged.connect(self._on_stoploss_changed)

        stoploss_desc = QLabel("跟踪止损: 从最高点回撤触发 | 固定止损: 跌破固定比例触发 | ATR止损: 根据波动率自适应")
        stoploss_desc.setFont(QFont("Microsoft YaHei", 10))
        stoploss_desc.setStyleSheet(f"color: {c.text_muted}; background: transparent;")
        stoploss_desc.setWordWrap(True)

        stoploss_item = SettingItem(
            "🛡️", "止损类型", "选择止损策略（重启后生效）",
            self._stoploss_combo
        )
        layout.addWidget(stoploss_item)
        layout.addWidget(stoploss_desc)

        # ═══ 通知设置 ═══
        notif_title = QLabel("🔔 通知设置")
        notif_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        notif_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(notif_title)

        # 涨跌幅预警
        self._alert_checkbox = QCheckBox("启用涨跌幅预警")
        self._alert_checkbox.setChecked(
            self._settings.value("alert_enabled", True, type=bool)
        )
        self._alert_checkbox.setFont(QFont("Microsoft YaHei", 12))
        self._alert_checkbox.setStyleSheet(f"""
            QCheckBox {{
                color: {c.text_primary};
            }}
            QCheckBox::indicator {{
                width: 20px;
                height: 20px;
            }}
        """)
        self._alert_checkbox.stateChanged.connect(self._on_alert_changed)

        alert_item = SettingItem(
            "📊", "涨跌幅预警", "当持仓涨跌幅超过阈值时通知",
            self._alert_checkbox
        )
        layout.addWidget(alert_item)

        # 预警阈值
        threshold_layout = QHBoxLayout()
        threshold_layout.setSpacing(8)

        threshold_label = QLabel("预警阈值:")
        threshold_label.setFont(QFont("Microsoft YaHei", 12))
        threshold_label.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        threshold_layout.addWidget(threshold_label)

        self._threshold_combo = QComboBox()
        self._threshold_combo.addItems(["3%", "5%", "7%", "10%"])
        self._threshold_combo.setCurrentText(
            self._settings.value("alert_threshold", "5%")
        )
        self._threshold_combo.setFixedWidth(80)
        self._threshold_combo.setStyleSheet(f"""
            QComboBox {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                border-radius: 6px;
                padding: 4px 8px;
            }}
        """)
        self._threshold_combo.currentTextChanged.connect(self._on_threshold_changed)
        threshold_layout.addWidget(self._threshold_combo)
        threshold_layout.addStretch()

        threshold_item = SettingItem(
            "⚡", "预警阈值", "设置触发预警的涨跌幅",
            QWidget()
        )
        threshold_item_layout = threshold_item.layout()
        threshold_item_layout.addLayout(threshold_layout)
        layout.addWidget(threshold_item)

        # ═══ 数据源设置 ═══
        source_title = QLabel("📡 数据源")
        source_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        source_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(source_title)

        sources = [
            ("腾讯行情", "实时数据，稳定性高"),
            ("东方财富", "实时数据，覆盖面广"),
            ("新浪财经", "实时数据，响应快速"),
            ("AkShare", "分钟级K线，需要安装"),
        ]

        for name, desc in sources:
            source_item = SettingItem(
                "✓", name, desc,
                QLabel("已启用")
            )
            layout.addWidget(source_item)

        # ═══ 关于 ═══
        about_title = QLabel("ℹ️ 关于")
        about_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        about_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(about_title)

        about_card = QFrame()
        about_card.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
            }}
        """)
        about_layout = QVBoxLayout(about_card)
        about_layout.setContentsMargins(20, 20, 20, 20)
        about_layout.setSpacing(12)

        # Logo
        logo_row = QHBoxLayout()
        logo = QLabel("Xm")
        logo.setFixedSize(50, 50)
        logo.setAlignment(Qt.AlignCenter)
        logo.setFont(QFont("Consolas", 18, QFont.Bold))
        logo.setStyleSheet(f"""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {c.accent}, stop:1 {c.accent_hover});
            color: white;
            border-radius: 12px;
        """)
        logo_row.addWidget(logo)

        name_layout = QVBoxLayout()
        name_layout.setSpacing(4)

        name = QLabel("Xm-LH 智能持仓管理系统")
        name.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        name.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        name_layout.addWidget(name)

        version = QLabel("版本 2.1.0")
        version.setFont(QFont("Microsoft YaHei", 11))
        version.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        name_layout.addWidget(version)

        logo_row.addLayout(name_layout)
        logo_row.addStretch()
        about_layout.addLayout(logo_row)

        # 描述
        about_desc = QLabel(
            "基于机器学习的智能股票持仓管理系统，\n"
            "提供实时行情、ML策略信号、回测分析等功能。"
        )
        about_desc.setFont(QFont("Microsoft YaHei", 11))
        about_desc.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        about_layout.addWidget(about_desc)

        # 链接
        links_row = QHBoxLayout()
        links_row.setSpacing(16)

        github_btn = QPushButton("GitHub")
        github_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.accent_light};
                color: {c.accent};
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }}
        """)
        links_row.addWidget(github_btn)

        doc_btn = QPushButton("文档")
        doc_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.accent_light};
                color: {c.accent};
                border: none;
                border-radius: 6px;
                padding: 6px 16px;
                font-size: 12px;
            }}
        """)
        links_row.addWidget(doc_btn)

        links_row.addStretch()
        about_layout.addLayout(links_row)

        layout.addWidget(about_card)

        layout.addStretch()

        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def _on_theme_selected(self, theme_key: str):
        """主题选择"""
        for key, card in self._theme_cards.items():
            card.set_selected(key == theme_key)

        ThemeManager.set_theme(theme_key)
        self.theme_changed.emit(theme_key)

        log.signal_log("settings", f"主题切换: {theme_key}",
                       ThemeManager.get_meta().get('name', theme_key))

    def _on_theme_changed(self, colors):
        """主题变化回调"""
        current = ThemeManager.get_theme()
        for key, card in self._theme_cards.items():
            card.set_selected(key == current)

    def _clear_cache(self):
        """清除缓存"""
        reply = QMessageBox.question(
            self, "确认清除",
            "确定要清除所有缓存数据吗？\n这将删除下载的K线数据。",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            cache_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "klines"
            )
            if os.path.exists(cache_dir):
                import shutil
                shutil.rmtree(cache_dir)
                os.makedirs(cache_dir, exist_ok=True)

            self.data_cleared.emit()
            log.signal_log("settings", "缓存已清除", "")
            QMessageBox.information(self, "完成", "缓存数据已清除")

    def _reset_settings(self):
        """重置设置"""
        reply = QMessageBox.question(
            self, "确认重置",
            "确定要重置所有设置吗？\n这将恢复默认主题和通知设置。",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self._settings.clear()
            ThemeManager.set_theme("light")
            self._init_ui()
            log.signal_log("settings", "设置已重置", "")
            QMessageBox.information(self, "完成", "设置已重置为默认值")

    def _on_alert_changed(self, state):
        """预警设置变化"""
        self._settings.setValue("alert_enabled", state == Qt.Checked)

    def _on_threshold_changed(self, text):
        """阈值变化"""
        self._settings.setValue("alert_threshold", text)

    def _on_stoploss_changed(self, index):
        """止损类型变化 — 写入 config.yaml"""
        type_map = {0: "trailing", 1: "fixed", 2: "atr"}
        new_type = type_map.get(index, "trailing")

        try:
            from core.config import get_config
            cfg = get_config()
            old_type = cfg.stop_loss.type
            cfg.stop_loss.type = new_type

            # 持久化到 config.yaml
            import yaml
            from pathlib import Path
            config_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            data = {}
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
            if 'stop_loss' not in data:
                data['stop_loss'] = {}
            data['stop_loss']['type'] = new_type
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

            log.signal_log("settings", f"止损策略: {old_type} → {new_type}",
                           "重启后生效")
        except Exception as e:
            log.warning("settings", f"保存止损配置失败: {e}")


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys

    app = QApplication(sys.argv)

    panel = SettingsPanel()
    panel.setFixedSize(800, 600)
    panel.show()

    sys.exit(app.exec_())
