#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设计令牌系统 (Design Tokens)
============================
系统化定义UI的视觉属性，确保一致性和可维护性

包含:
- 颜色令牌 (Colors)
- 字体令牌 (Typography)
- 间距令牌 (Spacing)
- 圆角令牌 (Border Radius)
- 阴影令牌 (Shadows)
- 动画令牌 (Animations)
- 断点令牌 (Breakpoints)
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


# ═══════════════════════════════════════════════════════════════
# 颜色令牌
# ═══════════════════════════════════════════════════════════════

@dataclass
class ColorTokens:
    """颜色令牌 - 语义化命名"""
    
    # 背景色
    bg_app: str           # 应用主背景
    bg_surface: str       # 卡片/面板背景
    bg_card: str          # 卡片背景
    bg_hover: str         # 悬停背景
    bg_active: str        # 激活背景
    bg_input: str         # 输入框背景
    bg_table_alt: str     # 表格交替行背景
    bg_header: str        # 表头背景
    bg_tooltip: str       # 提示框背景
    bg_overlay: str       # 遮罩层背景
    
    # 文字色
    text_primary: str     # 主要文字
    text_secondary: str   # 次要文字
    text_muted: str       # 弱化文字
    text_disabled: str    # 禁用文字
    text_on_accent: str   # 主题色上的文字
    text_on_dark: str     # 深色背景上的文字
    
    # 主题色
    accent: str           # 主主题色
    accent_hover: str     # 主题色悬停
    accent_light: str     # 主题色浅色
    accent_dark: str      # 主题色深色
    
    # 边框色
    border: str           # 普通边框
    border_strong: str    # 强调边框
    border_focus: str     # 聚焦边框
    border_error: str     # 错误边框
    border_success: str   # 成功边框
    
    # 状态色 - 红
    red: str
    red_light: str
    red_bg: str
    
    # 状态色 - 绿
    green: str
    green_light: str
    green_bg: str
    
    # 状态色 - 黄/橙
    orange: str
    orange_light: str
    orange_bg: str
    
    # 状态色 - 蓝
    blue: str
    blue_light: str
    blue_bg: str
    
    # 状态色 - 紫
    purple: str
    purple_light: str
    purple_bg: str
    
    # 其他
    shadow: str           # 阴影色
    divider: str          # 分隔线色
    mask: str             # 遮罩色


# 完整主题定义
THEME_COLORS = {
    "light": ColorTokens(
        # 背景
        bg_app="#f8fafc",
        bg_surface="#ffffff",
        bg_card="#ffffff",
        bg_hover="#f1f5f9",
        bg_active="#e2e8f0",
        bg_input="#ffffff",
        bg_table_alt="#f8fafc",
        bg_header="#f8fafc",
        bg_tooltip="#1e293b",
        bg_overlay="rgba(0,0,0,0.5)",
        
        # 文字
        text_primary="#0f172a",
        text_secondary="#64748b",
        text_muted="#94a3b8",
        text_disabled="#cbd5e1",
        text_on_accent="#ffffff",
        text_on_dark="#f1f5f9",
        
        # 主题色
        accent="#3b82f6",
        accent_hover="#2563eb",
        accent_light="#dbeafe",
        accent_dark="#1d4ed8",
        
        # 边框
        border="#e2e8f0",
        border_strong="#cbd5e1",
        border_focus="#3b82f6",
        border_error="#ef4444",
        border_success="#22c55e",
        
        # 状态色
        red="#ef4444",
        red_light="#fca5a5",
        red_bg="#fef2f2",
        
        green="#22c55e",
        green_light="#86efac",
        green_bg="#f0fdf4",
        
        orange="#f59e0b",
        orange_light="#fcd34d",
        orange_bg="#fffbeb",
        
        blue="#3b82f6",
        blue_light="#93c5fd",
        blue_bg="#eff6ff",
        
        purple="#8b5cf6",
        purple_light="#c4b5fd",
        purple_bg="#f5f3ff",
        
        # 其他
        shadow="rgba(0,0,0,0.08)",
        divider="#e2e8f0",
        mask="rgba(0,0,0,0.4)",
    ),
    
    "dark": ColorTokens(
        # 背景
        bg_app="#0f172a",
        bg_surface="#1e293b",
        bg_card="#1e293b",
        bg_hover="#334155",
        bg_active="#475569",
        bg_input="#334155",
        bg_table_alt="#1e293b",
        bg_header="#1e293b",
        bg_tooltip="#0f172a",
        bg_overlay="rgba(0,0,0,0.7)",
        
        # 文字
        text_primary="#f8fafc",
        text_secondary="#94a3b8",
        text_muted="#64748b",
        text_disabled="#475569",
        text_on_accent="#ffffff",
        text_on_dark="#f1f5f9",
        
        # 主题色
        accent="#60a5fa",
        accent_hover="#3b82f6",
        accent_light="#1e3a5f",
        accent_dark="#2563eb",
        
        # 边框
        border="#334155",
        border_strong="#475569",
        border_focus="#60a5fa",
        border_error="#f87171",
        border_success="#4ade80",
        
        # 状态色
        red="#f87171",
        red_light="#fca5a5",
        red_bg="#450a0a",
        
        green="#4ade80",
        green_light="#86efac",
        green_bg="#052e16",
        
        orange="#fbbf24",
        orange_light="#fcd34d",
        orange_bg="#451a03",
        
        blue="#60a5fa",
        blue_light="#93c5fd",
        blue_bg="#172554",
        
        purple="#a78bfa",
        purple_light="#c4b5fd",
        purple_bg="#2e1065",
        
        # 其他
        shadow="rgba(0,0,0,0.4)",
        divider="#334155",
        mask="rgba(0,0,0,0.6)",
    ),
    
    "blue": ColorTokens(
        # 背景
        bg_app="#eff6ff",
        bg_surface="#ffffff",
        bg_card="#ffffff",
        bg_hover="#dbeafe",
        bg_active="#bfdbfe",
        bg_input="#ffffff",
        bg_table_alt="#eff6ff",
        bg_header="#eff6ff",
        bg_tooltip="#1e3a5f",
        bg_overlay="rgba(0,0,0,0.5)",
        
        # 文字
        text_primary="#1e3a5f",
        text_secondary="#3b5998",
        text_muted="#64748b",
        text_disabled="#94a3b8",
        text_on_accent="#ffffff",
        text_on_dark="#f1f5f9",
        
        # 主题色
        accent="#2563eb",
        accent_hover="#1d4ed8",
        accent_light="#dbeafe",
        accent_dark="#1e40af",
        
        # 边框
        border="#bfdbfe",
        border_strong="#93c5fd",
        border_focus="#2563eb",
        border_error="#ef4444",
        border_success="#22c55e",
        
        # 状态色
        red="#ef4444",
        red_light="#fca5a5",
        red_bg="#fef2f2",
        
        green="#22c55e",
        green_light="#86efac",
        green_bg="#f0fdf4",
        
        orange="#f59e0b",
        orange_light="#fcd34d",
        orange_bg="#fffbeb",
        
        blue="#2563eb",
        blue_light="#93c5fd",
        blue_bg="#eff6ff",
        
        purple="#8b5cf6",
        purple_light="#c4b5fd",
        purple_bg="#f5f3ff",
        
        # 其他
        shadow="rgba(37,99,235,0.12)",
        divider="#bfdbfe",
        mask="rgba(0,0,0,0.4)",
    ),
}


# ═══════════════════════════════════════════════════════════════
# 字体令牌
# ═══════════════════════════════════════════════════════════════

@dataclass
class TypographyTokens:
    """字体令牌"""
    
    # 字体家族
    font_family_primary: str = '"Microsoft YaHei", "PingFang SC", sans-serif'
    font_family_mono: str = '"Consolas", "Monaco", "Courier New", monospace'
    font_family_number: str = '"DIN Alternate", "Helvetica Neue", sans-serif'
    
    # 字体大小 (px)
    size_xs: int = 11
    size_sm: int = 12
    size_base: int = 13
    size_md: int = 14
    size_lg: int = 16
    size_xl: int = 18
    size_2xl: int = 20
    size_3xl: int = 24
    size_4xl: int = 28
    size_5xl: int = 32
    
    # 字重
    weight_normal: int = 400
    weight_medium: int = 500
    weight_semibold: int = 600
    weight_bold: int = 700
    
    # 行高
    line_tight: float = 1.25
    line_normal: float = 1.5
    line_relaxed: float = 1.75
    
    # 字间距
    tracking_tight: float = -0.025
    tracking_normal: float = 0
    tracking_wide: float = 0.025


# ═══════════════════════════════════════════════════════════════
# 间距令牌
# ═══════════════════════════════════════════════════════════════

@dataclass
class SpacingTokens:
    """间距令牌"""
    
    # 基础间距 (px)
    space_0: int = 0
    space_1: int = 4
    space_2: int = 8
    space_3: int = 12
    space_4: int = 16
    space_5: int = 20
    space_6: int = 24
    space_8: int = 32
    space_10: int = 40
    space_12: int = 48
    space_16: int = 64
    
    # 常用组合
    @property
    def card_padding(self) -> int:
        """卡片内边距"""
        return self.space_5
    
    @property
    def section_gap(self) -> int:
        """区块间距"""
        return self.space_6
    
    @property
    def item_gap(self) -> int:
        """列表项间距"""
        return self.space_3


# ═══════════════════════════════════════════════════════════════
# 圆角令牌
# ═══════════════════════════════════════════════════════════════

@dataclass
class BorderRadiusTokens:
    """圆角令牌"""
    
    none: int = 0
    sm: int = 4
    base: int = 6
    md: int = 8
    lg: int = 12
    xl: int = 16
    full: int = 9999
    
    # 常用组合
    @property
    def card(self) -> int:
        """卡片组件样式 — 背景/圆角/阴影"""
        return self.lg
    
    @property
    def button(self) -> int:
        """按钮组件样式 — 背景/圆角/内边距"""
        return self.md
    
    @property
    def input(self) -> int:
        """输入框组件样式 — 背景/圆角/内边距"""
        return self.md
    
    @property
    def badge(self) -> int:
        """徽标组件样式 — 背景/圆角/内边距"""
        return self.base


# ═══════════════════════════════════════════════════════════════
# 阴影令牌
# ═══════════════════════════════════════════════════════════════

@dataclass
class ShadowTokens:
    """阴影令牌"""
    
    # 阴影定义
    none: str = "none"
    
    @staticmethod
    def xs(color: str = "rgba(0,0,0,0.05)") -> str:
        """超小字体尺寸"""
        return f"0 1px 2px 0 {color}"
    
    @staticmethod
    def sm(color: str = "rgba(0,0,0,0.05)") -> str:
        """小字体尺寸"""
        return f"0 1px 3px 0 {color}, 0 1px 2px -1px {color}"
    
    @staticmethod
    def md(color: str = "rgba(0,0,0,0.08)") -> str:
        """中等字体尺寸"""
        return f"0 4px 6px -1px {color}, 0 2px 4px -2px {color}"
    
    @staticmethod
    def lg(color: str = "rgba(0,0,0,0.1)") -> str:
        """大字体尺寸"""
        return f"0 10px 15px -3px {color}, 0 4px 6px -4px {color}"
    
    @staticmethod
    def xl(color: str = "rgba(0,0,0,0.12)") -> str:
        """超大字体尺寸"""
        return f"0 20px 25px -5px {color}, 0 8px 10px -6px {color}"
    
    @staticmethod
    def inner(color: str = "rgba(0,0,0,0.06)") -> str:
        """内阴影效果 — 凹陷卡片效果"""
        return f"inset 0 2px 4px 0 {color}"
    
    @staticmethod
    def glow(color: str, intensity: float = 0.5) -> str:
        """发光效果 — 霓虹风格装饰"""
        return f"0 0 20px {color}{int(intensity*255):02x}"


# ═══════════════════════════════════════════════════════════════
# 动画令牌
# ═══════════════════════════════════════════════════════════════

@dataclass
class AnimationTokens:
    """动画令牌"""
    
    # 持续时间 (ms)
    duration_fast: int = 150
    duration_normal: int = 250
    duration_slow: int = 350
    duration_slower: int = 500
    
    # 缓动函数
    ease_linear: str = "linear"
    ease_in: str = "cubic-bezier(0.4, 0, 1, 1)"
    ease_out: str = "cubic-bezier(0, 0, 0.2, 1)"
    ease_in_out: str = "cubic-bezier(0.4, 0, 0.2, 1)"
    ease_bounce: str = "cubic-bezier(0.68, -0.55, 0.265, 1.55)"
    ease_spring: str = "cubic-bezier(0.175, 0.885, 0.32, 1.275)"
    
    # 常用动画
    @staticmethod
    def fade_in(duration_ms: int = 250) -> str:
        """淡入动画 — 透明度0→100%"""
        return f"fadeIn {duration_ms}ms ease-out"
    
    @staticmethod
    def fade_out(duration_ms: int = 200) -> str:
        """淡出动画 — 透明度100%→0"""
        return f"fadeOut {duration_ms}ms ease-in"
    
    @staticmethod
    def slide_up(duration_ms: int = 300) -> str:
        """上滑动画 — 用于面板展开"""
        return f"slideUp {duration_ms}ms ease-out"
    
    @staticmethod
    def slide_down(duration_ms: int = 300) -> str:
        """下滑动画 — 用于面板折叠"""
        return f"slideDown {duration_ms}ms ease-out"
    
    @staticmethod
    def scale_in(duration_ms: int = 250) -> str:
        """缩放进入 — 元素从小变大"""
        return f"scaleIn {duration_ms}ms ease-out"
    
    @staticmethod
    def pulse(duration_ms: int = 2000) -> str:
        """脉冲动画 — 呼吸灯效果"""
        return f"pulse {duration_ms}ms ease-in-out infinite"
    
    @staticmethod
    def spin(duration_ms: int = 1000) -> str:
        """旋转动画 — 加载指示器"""
        return f"spin {duration_ms}ms linear infinite"


# ═══════════════════════════════════════════════════════════════
# Z-Index 令牌
# ═══════════════════════════════════════════════════════════════

@dataclass
class ZIndexTokens:
    """Z-Index 层级令牌"""
    
    base: int = 0
    dropdown: int = 100
    sticky: int = 200
    fixed: int = 300
    modal_backdrop: int = 400
    modal: int = 500
    popover: int = 600
    tooltip: int = 700
    toast: int = 800


# ═══════════════════════════════════════════════════════════════
# 便捷获取函数
# ═══════════════════════════════════════════════════════════════

def get_colors(theme_name: str = "light") -> ColorTokens:
    """获取指定主题的颜色令牌"""
    return THEME_COLORS.get(theme_name, THEME_COLORS["light"])


def get_typography() -> TypographyTokens:
    """获取字体令牌"""
    return TypographyTokens()


def get_spacing() -> SpacingTokens:
    """获取间距令牌"""
    return SpacingTokens()


def get_border_radius() -> BorderRadiusTokens:
    """获取圆角令牌"""
    return BorderRadiusTokens()


def get_shadows() -> ShadowTokens:
    """获取阴影令牌"""
    return ShadowTokens()


def get_animations() -> AnimationTokens:
    """获取动画令牌"""
    return AnimationTokens()


def get_z_index() -> ZIndexTokens:
    """获取Z-Index令牌"""
    return ZIndexTokens()


# ═══════════════════════════════════════════════════════════════
# CSS 动画关键帧
# ═══════════════════════════════════════════════════════════════

def get_animation_css() -> str:
    """获取CSS动画关键帧定义"""
    return """
    /* 淡入 */
    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
    
    /* 淡出 */
    @keyframes fadeOut {
        from { opacity: 1; }
        to { opacity: 0; }
    }
    
    /* 上滑进入 */
    @keyframes slideUp {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    /* 下滑进入 */
    @keyframes slideDown {
        from { opacity: 0; transform: translateY(-20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    /* 左滑进入 */
    @keyframes slideLeft {
        from { opacity: 0; transform: translateX(20px); }
        to { opacity: 1; transform: translateX(0); }
    }
    
    /* 右滑进入 */
    @keyframes slideRight {
        from { opacity: 0; transform: translateX(-20px); }
        to { opacity: 1; transform: translateX(0); }
    }
    
    /* 缩放进入 */
    @keyframes scaleIn {
        from { opacity: 0; transform: scale(0.95); }
        to { opacity: 1; transform: scale(1); }
    }
    
    /* 缩放弹出 */
    @keyframes scalePop {
        0% { transform: scale(1); }
        50% { transform: scale(1.05); }
        100% { transform: scale(1); }
    }
    
    /* 脉冲 */
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
    }
    
    /* 脉冲缩放 */
    @keyframes pulseScale {
        0%, 100% { transform: scale(1); }
        50% { transform: scale(1.02); }
    }
    
    /* 旋转 */
    @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
    
    /* 弹跳 */
    @keyframes bounce {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-10px); }
    }
    
    /* 闪烁 */
    @keyframes shimmer {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
    }
    
    /* 进度条动画 */
    @keyframes progress {
        0% { width: 0%; }
        100% { width: 100%; }
    }
    """


if __name__ == "__main__":
    # 测试
    colors = get_colors("light")
    print(f"主背景色: {colors.bg_app}")
    print(f"主题色: {colors.accent}")
    print(f"成功色: {colors.green}")
    
    shadows = get_shadows()
    print(f"\n小阴影: {shadows.sm()}")
    print(f"中阴影: {shadows.md()}")
    
    animations = get_animations()
    print(f"\n淡入动画: {animations.fade_in()}")
