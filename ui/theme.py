#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主题系统 v2.0
=============
支持多套主题切换，系统化设计令牌管理

特性:
- 设计令牌统一管理
- 动画效果支持
- 实时主题切换
- 主题持久化
"""

import os
import json
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QSettings

from ui.design_tokens import (
    get_colors, get_typography, get_spacing, 
    get_border_radius, get_shadows, get_animation_css
)


# ═══════════════════════════════════════════════════════════════
# 主题元信息
# ═══════════════════════════════════════════════════════════════

THEME_META = {
    "light": {
        "name": "浅色",
        "desc": "清爽明亮，适合日间使用",
        "icon": "☀️",
    },
    "dark": {
        "name": "深色",
        "desc": "护眼低亮度，适合夜间使用",
        "icon": "🌙",
    },
    "blue": {
        "name": "晴蓝",
        "desc": "柔和蓝调，沉稳专业",
        "icon": "🔷",
    },
}


# ═══════════════════════════════════════════════════════════════
# QSS 生成器
# ═══════════════════════════════════════════════════════════════

def _gen_qss(theme_name: str) -> str:
    """根据主题生成全局 QSS"""
    c = get_colors(theme_name)
    t = get_typography()
    s = get_spacing()
    r = get_border_radius()
    sh = get_shadows()
    
    return f"""
    /* ===== 动画关键帧 ===== */
    {get_animation_css()}
    
    /* ===== 全局 ===== */
    QMainWindow, QWidget {{
        background-color: {c.bg_app};
        color: {c.text_primary};
        font-family: {t.font_family_primary};
        font-size: {t.size_base}px;
    }}
    
    /* ===== 顶部状态栏 ===== */
    HeaderPanel {{
        background-color: {c.bg_surface};
        border-bottom: 1px solid {c.border};
    }}
    
    /* ===== 导航按钮 ===== */
    QPushButton#navBtn {{
        color: {c.text_secondary};
        border-bottom: 3px solid transparent;
        background: transparent;
        font-size: {t.size_md}px;
        font-weight: {t.weight_medium};
        padding: 0 {s.space_5}px;
        min-height: 42px;
    }}
    QPushButton#navBtn:hover {{
        color: {c.text_primary};
    }}
    QPushButton#navBtn:checked {{
        color: {c.accent};
        border-bottom: 3px solid {c.accent};
        font-weight: {t.weight_bold};
    }}
    
    /* ===== 卡片容器 ===== */
    QFrame#card {{
        background: {c.bg_surface};
        border: 1px solid {c.border};
        border-radius: {r.card}px;
    }}
    QFrame#card:hover {{
        border-color: {c.border_strong};
    }}
    
    /* ===== 渐变卡片 ===== */
    QFrame#gradientCard {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {c.accent_light}20, stop:1 {c.accent}10);
        border-left: 4px solid {c.accent};
        border-radius: {r.card}px;
    }}
    
    /* ===== 仪表盘卡片 ===== */
    QFrame#dashboard {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 #1a1d2e, stop:1 #252840);
        border-radius: {r.xl}px;
    }}
    
    /* ===== 输入框 ===== */
    QLineEdit {{
        background-color: {c.bg_input};
        color: {c.text_primary};
        border: 2px solid {c.border};
        border-radius: {r.input}px;
        padding: 0 {s.space_4}px;
        min-height: 36px;
        font-size: {t.size_md}px;
        selection-background-color: {c.accent};
    }}
    QLineEdit:focus {{
        border-color: {c.accent};
    }}
    QLineEdit::placeholder {{
        color: {c.text_muted};
    }}
    
    /* ===== 搜索下拉 ===== */
    QListWidget {{
        background-color: {c.bg_card};
        color: {c.text_primary};
        border: 1px solid {c.border_strong};
        border-radius: {r.md}px;
        padding: {s.space_2}px 0;
    }}
    QListWidget::item {{
        padding: {s.space_3}px {s.space_4}px;
        border-bottom: 1px solid {c.divider};
    }}
    QListWidget::item:last {{
        border-bottom: none;
    }}
    QListWidget::item:selected {{
        background-color: {c.accent_light};
        color: {c.accent};
    }}
    QListWidget::item:hover {{
        background-color: {c.bg_hover};
    }}
    
    /* ===== 表格 ===== */
    QTableWidget {{
        background-color: {c.bg_card};
        color: {c.text_primary};
        border: 1px solid {c.border};
        border-radius: {r.md}px;
        gridline-color: {c.divider};
        alternate-background-color: {c.bg_table_alt};
        selection-background-color: {c.accent_light};
    }}
    QTableWidget::item {{
        padding: {s.space_3}px;
        border: none;
    }}
    QTableWidget::item:selected {{
        background-color: {c.accent_light};
        color: {c.text_primary};
    }}
    QHeaderView::section {{
        background-color: {c.bg_header};
        color: {c.text_secondary};
        border: none;
        border-bottom: 2px solid {c.border};
        padding: {s.space_3}px {s.space_4}px;
        font-weight: {t.weight_bold};
        text-align: left;
    }}
    
    /* ===== 按钮 - 主按钮 ===== */
    QPushButton#primaryBtn {{
        background-color: {c.accent};
        color: {c.text_on_accent};
        border: none;
        border-radius: {r.button}px;
        padding: {s.space_2}px {s.space_5}px;
        min-height: 36px;
        font-weight: {t.weight_semibold};
        font-size: {t.size_md}px;
    }}
    QPushButton#primaryBtn:hover {{
        background-color: {c.accent_hover};
    }}
    QPushButton#primaryBtn:disabled {{
        background-color: {c.text_disabled};
    }}
    
    /* ===== 按钮 - 次按钮 ===== */
    QPushButton#secondaryBtn {{
        background-color: {c.bg_surface};
        color: {c.text_primary};
        border: 1px solid {c.border_strong};
        border-radius: {r.button}px;
        padding: {s.space_2}px {s.space_5}px;
        min-height: 36px;
        font-weight: {t.weight_medium};
    }}
    QPushButton#secondaryBtn:hover {{
        background-color: {c.bg_hover};
        border-color: {c.accent};
    }}
    
    /* ===== 按钮 - 危险按钮 ===== */
    QPushButton#dangerBtn {{
        background-color: {c.red};
        color: {c.text_on_accent};
        border: none;
        border-radius: {r.button}px;
        padding: {s.space_2}px {s.space_4}px;
        min-height: 36px;
        font-weight: {t.weight_semibold};
    }}
    QPushButton#dangerBtn:hover {{
        background-color: {c.red_light};
    }}
    
    /* ===== 文本编辑 (日志) ===== */
    QTextEdit {{
        background-color: {c.bg_card};
        color: {c.text_primary};
        border: 1px solid {c.border};
        border-radius: {r.md}px;
        padding: {s.space_3}px;
        font-family: {t.font_family_mono};
    }}
    
    /* ===== 标签 - 标题 ===== */
    QLabel#title {{
        color: {c.text_primary};
        font-size: {t.size_2xl}px;
        font-weight: {t.weight_bold};
    }}
    
    /* ===== 标签 - 副标题 ===== */
    QLabel#subtitle {{
        color: {c.text_secondary};
        font-size: {t.size_md}px;
    }}
    
    /* ===== 标签 - 徽章 ===== */
    QLabel#badge {{
        background-color: {c.accent_light};
        color: {c.accent};
        border-radius: {r.badge}px;
        padding: 2px 10px;
        font-size: {t.size_sm}px;
        font-weight: {t.weight_medium};
    }}
    
    /* ===== 标签 - 状态指示器 ===== */
    QLabel#statusIndicator {{
        border-radius: 50%;
        min-width: 8px;
        min-height: 8px;
    }}
    QLabel#statusIndicator[status="success"] {{
        background-color: {c.green};
    }}
    QLabel#statusIndicator[status="warning"] {{
        background-color: {c.orange};
    }}
    QLabel#statusIndicator[status="error"] {{
        background-color: {c.red};
    }}
    QLabel#statusIndicator[status="info"] {{
        background-color: {c.blue};
    }}
    
    /* ===== 进度条 ===== */
    QProgressBar {{
        background-color: {c.bg_hover};
        border: none;
        border-radius: {r.full}px;
        height: 6px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        background-color: {c.accent};
        border-radius: {r.full}px;
    }}
    
    /* ===== 滚动条 ===== */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c.border_strong};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {c.text_muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {c.border_strong};
        border-radius: 4px;
        min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {c.text_muted};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}
    
    /* ===== 分隔条 ===== */
    QSplitter::handle {{
        background: {c.border};
    }}
    QSplitter::handle:hover {{
        background: {c.accent};
    }}
    
    /* ===== 工具提示 ===== */
    QToolTip {{
        background-color: {c.bg_tooltip};
        color: {c.text_on_dark};
        border: none;
        border-radius: {r.md}px;
        padding: {s.space_2}px {s.space_3}px;
        font-size: {t.size_sm}px;
    }}
    """


# ═══════════════════════════════════════════════════════════════
# 主题管理器 (单例)
# ═══════════════════════════════════════════════════════════════

class ThemeManager:
    """主题管理器 - 单例模式"""
    
    _current = "light"
    _callbacks = []
    _settings = None
    
    @classmethod
    def _get_settings(cls):
        """获取QSettings实例"""
        if cls._settings is None:
            cls._settings = QSettings("XmLH", "StockManager")
        return cls._settings
    
    @classmethod
    def get_theme(cls) -> str:
        """获取当前主题"""
        return cls._current
    
    @classmethod
    def get_colors(cls):
        """获取当前主题的颜色令牌"""
        return get_colors(cls._current)
    
    @classmethod
    def get_meta(cls) -> dict:
        """获取当前主题的元信息"""
        return THEME_META.get(cls._current, {})
    
    @classmethod
    def get_all_themes(cls) -> dict:
        """获取所有可用主题"""
        return {
            key: {**THEME_META.get(key, {}), "key": key}
            for key in ["light", "dark", "blue"]
        }
    
    @classmethod
    def set_theme(cls, name: str, save: bool = True):
        """设置主题"""
        if name not in ["light", "dark", "blue"]:
            return False
        
        cls._current = name
        cls._apply()
        
        # 持久化保存
        if save:
            cls._get_settings().setValue("theme", name)
        
        return True
    
    @classmethod
    def load_saved_theme(cls):
        """加载保存的主题"""
        saved = cls._get_settings().value("theme", "light")
        if saved in ["light", "dark", "blue"]:
            cls._current = saved
            cls._apply()
    
    @classmethod
    def on_change(cls, callback):
        """注册主题切换回调"""
        cls._callbacks.append(callback)
    
    @classmethod
    def _apply(cls):
        """应用主题"""
        app = QApplication.instance()
        if not app:
            return
        
        # 应用QSS
        app.setStyleSheet(_gen_qss(cls._current))
        
        # 通知回调
        colors = cls.get_colors()
        import logging
        _logger = logging.getLogger(__name__)
        for cb in cls._callbacks:
            try:
                cb(colors)
            except Exception as e:
                _logger.warning(f"主题回调错误: {e}")
    
    @classmethod
    def get_style_for_widget(cls, widget_type: str, **kwargs) -> str:
        """获取特定组件的样式"""
        c = cls.get_colors()
        
        styles = {
            "metric_card": f"""
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
                padding: 16px;
            """,
            "success_card": f"""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c.green_bg}, stop:1 {c.green}10);
                border-left: 4px solid {c.green};
                border-radius: 12px;
            """,
            "warning_card": f"""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c.orange_bg}, stop:1 {c.orange}10);
                border-left: 4px solid {c.orange};
                border-radius: 12px;
            """,
            "error_card": f"""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c.red_bg}, stop:1 {c.red}10);
                border-left: 4px solid {c.red};
                border-radius: 12px;
            """,
            "info_card": f"""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 {c.blue_bg}, stop:1 {c.blue}10);
                border-left: 4px solid {c.blue};
                border-radius: 12px;
            """,
        }
        
        return styles.get(widget_type, "")


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

def get_current_theme() -> str:
    """获取当前主题名称"""
    return ThemeManager.get_theme()


def get_current_colors():
    """获取当前主题颜色"""
    return ThemeManager.get_colors()


def switch_theme(theme_name: str) -> bool:
    """切换主题"""
    return ThemeManager.set_theme(theme_name)


def get_all_themes() -> dict:
    """获取所有可用主题"""
    return ThemeManager.get_all_themes()


# ═══════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════

def init_theme():
    """初始化主题系统"""
    ThemeManager.load_saved_theme()


if __name__ == "__main__":
    # 测试
    from PyQt5.QtWidgets import QApplication
    import sys
    
    app = QApplication(sys.argv)
    
    # 测试主题切换
    print("当前主题:", ThemeManager.get_theme())
    print("主题颜色:", ThemeManager.get_colors().accent)
    
    ThemeManager.set_theme("dark")
    print("切换后主题:", ThemeManager.get_theme())
