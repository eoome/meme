#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动画效果模块
==========
为PyQt5组件提供丰富的动画效果

包含:
- 淡入淡出动画
- 滑动动画
- 缩放动画
- 脉冲动画
- 颜色过渡动画
- 数字滚动动画
"""

from PyQt5.QtWidgets import QWidget, QGraphicsOpacityEffect, QLabel
from PyQt5.QtCore import (QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
                          QSequentialAnimationGroup, QPoint, QSize, Qt, pyqtSignal)
from PyQt5.QtGui import QColor

from ui.design_tokens import get_animations, get_colors


class AnimationManager:
    """动画管理器 - 统一管理组件动画"""
    
    _instance = None
    
    def __new__(cls):
        """单例模式 — 确保全局唯一实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._animations = {}
        return cls._instance
    
    def register(self, widget: QWidget, animation: QPropertyAnimation):
        """注册动画"""
        widget_id = id(widget)
        if widget_id not in self._animations:
            self._animations[widget_id] = []
        self._animations[widget_id].append(animation)
    
    def stop_all(self, widget: QWidget):
        """停止组件的所有动画"""
        widget_id = id(widget)
        if widget_id in self._animations:
            for anim in self._animations[widget_id]:
                anim.stop()
            self._animations[widget_id].clear()


# ═══════════════════════════════════════════════════════════════
# 淡入淡出动画
# ═══════════════════════════════════════════════════════════════

class FadeAnimation:
    """淡入淡出动画"""
    
    @staticmethod
    def fade_in(widget: QWidget, duration_ms: int = 250, 
                easing: QEasingCurve = QEasingCurve.OutCubic) -> QPropertyAnimation:
        """淡入动画"""
        # 确保有opacity effect
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        
        effect.setOpacity(0)
        
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(duration_ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(easing)
        
        AnimationManager().register(widget, anim)
        return anim
    
    @staticmethod
    def fade_out(widget: QWidget, duration_ms: int = 200,
                 easing: QEasingCurve = QEasingCurve.InCubic) -> QPropertyAnimation:
        """淡出动画"""
        effect = widget.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
        
        anim = QPropertyAnimation(effect, b"opacity")
        anim.setDuration(duration_ms)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(easing)
        
        AnimationManager().register(widget, anim)
        return anim


# ═══════════════════════════════════════════════════════════════
# 滑动动画
# ═══════════════════════════════════════════════════════════════

class SlideAnimation:
    """滑动动画"""
    
    @staticmethod
    def slide_in_from_bottom(widget: QWidget, distance: int = 30,
                             duration_ms: int = 300) -> QPropertyAnimation:
        """从底部滑入"""
        start_pos = widget.pos() + QPoint(0, distance)
        end_pos = widget.pos()
        
        widget.move(start_pos)
        
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(duration_ms)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        
        AnimationManager().register(widget, anim)
        return anim
    
    @staticmethod
    def slide_in_from_top(widget: QWidget, distance: int = 30,
                          duration_ms: int = 300) -> QPropertyAnimation:
        """从顶部滑入"""
        start_pos = widget.pos() - QPoint(0, distance)
        end_pos = widget.pos()
        
        widget.move(start_pos)
        
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(duration_ms)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        
        AnimationManager().register(widget, anim)
        return anim
    
    @staticmethod
    def slide_in_from_left(widget: QWidget, distance: int = 30,
                           duration_ms: int = 300) -> QPropertyAnimation:
        """从左侧滑入"""
        start_pos = widget.pos() - QPoint(distance, 0)
        end_pos = widget.pos()
        
        widget.move(start_pos)
        
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(duration_ms)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        
        AnimationManager().register(widget, anim)
        return anim
    
    @staticmethod
    def slide_in_from_right(widget: QWidget, distance: int = 30,
                            duration_ms: int = 300) -> QPropertyAnimation:
        """从右侧滑入"""
        start_pos = widget.pos() + QPoint(distance, 0)
        end_pos = widget.pos()
        
        widget.move(start_pos)
        
        anim = QPropertyAnimation(widget, b"pos")
        anim.setDuration(duration_ms)
        anim.setStartValue(start_pos)
        anim.setEndValue(end_pos)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        
        AnimationManager().register(widget, anim)
        return anim


# ═══════════════════════════════════════════════════════════════
# 缩放动画
# ═══════════════════════════════════════════════════════════════

class ScaleAnimation:
    """缩放动画"""
    
    @staticmethod
    def scale_in(widget: QWidget, duration_ms: int = 250) -> QPropertyAnimation:
        """缩放进入"""
        start_size = QSize(int(widget.width() * 0.95), int(widget.height() * 0.95))
        end_size = widget.size()
        
        # 保存中心点
        center = widget.geometry().center()
        
        anim = QPropertyAnimation(widget, b"size")
        anim.setDuration(duration_ms)
        anim.setStartValue(start_size)
        anim.setEndValue(end_size)
        anim.setEasingCurve(QEasingCurve.OutBack)
        
        # 保持中心点不变
        def update_pos():
            """更新位置"""
            new_geo = widget.geometry()
            new_geo.moveCenter(center)
            widget.setGeometry(new_geo)
        
        anim.valueChanged.connect(update_pos)
        
        AnimationManager().register(widget, anim)
        return anim
    
    @staticmethod
    def pulse(widget: QWidget, duration_ms: int = 2000) -> QPropertyAnimation:
        """脉冲动画 - 持续缩放"""
        anim = QPropertyAnimation(widget, b"minimumSize")
        anim.setDuration(duration_ms)
        anim.setStartValue(widget.minimumSize())
        anim.setEndValue(QSize(
            int(widget.minimumWidth() * 1.02),
            int(widget.minimumHeight() * 1.02)
        ))
        anim.setEasingCurve(QEasingCurve.InOutSine)
        anim.setLoopCount(-1)  # 无限循环
        
        AnimationManager().register(widget, anim)
        return anim


# ═══════════════════════════════════════════════════════════════
# 数字滚动动画
# ═══════════════════════════════════════════════════════════════

class NumberRollAnimation(QLabel):
    """数字滚动动画标签"""
    
    value_changed = pyqtSignal(float)
    
    def __init__(self, parent=None, decimals: int = 2, prefix: str = "", suffix: str = ""):
        """初始化"""
        super().__init__(parent)
        self._decimals = decimals
        self._prefix = prefix
        self._suffix = suffix
        self._current_value = 0.0
        self._target_value = 0.0
        
        self._animation = QPropertyAnimation(self, b"value")
        self._animation.setDuration(800)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
    
    def get_value(self) -> float:
        """获取当前值"""
        return self._current_value
    
    def set_value(self, value: float):
        """设置当前值"""
        self._current_value = value
        self._update_text()
        self.value_changed.emit(value)
    
    value = property(get_value, set_value)
    
    def _update_text(self):
        format_str = f"{self._prefix}{{:.{self._decimals}f}}{self._suffix}"
        self.setText(format_str.format(self._current_value))
    
    def animate_to(self, target: float, duration_ms: int = 800):
        """动画滚动到目标值"""
        self._target_value = target
        self._animation.stop()
        self._animation.setDuration(duration_ms)
        self._animation.setStartValue(self._current_value)
        self._animation.setEndValue(target)
        self._animation.start()
    
    def set_format(self, decimals: int = None, prefix: str = None, suffix: str = None):
        """设置格式"""
        if decimals is not None:
            self._decimals = decimals
        if prefix is not None:
            self._prefix = prefix
        if suffix is not None:
            self._suffix = suffix
        self._update_text()


# ═══════════════════════════════════════════════════════════════
# 组合动画
# ═══════════════════════════════════════════════════════════════

class CombinedAnimation:
    """组合动画"""
    
    @staticmethod
    def fade_slide_in(widget: QWidget, direction: str = "bottom",
                      duration_ms: int = 350) -> QParallelAnimationGroup:
        """淡入 + 滑动组合动画"""
        group = QParallelAnimationGroup()
        
        # 淡入
        fade = FadeAnimation.fade_in(widget, duration_ms)
        group.addAnimation(fade)
        
        # 滑动
        if direction == "bottom":
            slide = SlideAnimation.slide_in_from_bottom(widget, 30, duration_ms)
        elif direction == "top":
            slide = SlideAnimation.slide_in_from_top(widget, 30, duration_ms)
        elif direction == "left":
            slide = SlideAnimation.slide_in_from_left(widget, 30, duration_ms)
        else:
            slide = SlideAnimation.slide_in_from_right(widget, 30, duration_ms)
        
        group.addAnimation(slide)
        return group
    
    @staticmethod
    def staggered_fade_in(widgets: list, delay_ms: int = 50,
                          duration_ms: int = 300) -> QSequentialAnimationGroup:
        """错开淡入动画"""
        group = QSequentialAnimationGroup()
        
        for i, widget in enumerate(widgets):
            fade = FadeAnimation.fade_in(widget, duration_ms)
            if i > 0:
                group.addPause(delay_ms)
            group.addAnimation(fade)
        
        return group


# ═══════════════════════════════════════════════════════════════
# 卡片动画效果
# ═══════════════════════════════════════════════════════════════

class CardAnimator:
    """卡片动画器 - 为卡片添加交互动画"""
    
    @staticmethod
    def apply_hover_effect(card: QWidget, scale: float = 1.02,
                          shadow_on_hover: bool = True):
        """应用悬停效果"""
        original_geometry = card.geometry()
        
        def on_enter(event):
            """鼠标进入事件 — 显示悬停效果"""
            # 轻微放大
            center = card.geometry().center()
            new_width = int(original_geometry.width() * scale)
            new_height = int(original_geometry.height() * scale)
            new_geo = card.geometry()
            new_geo.setSize(QSize(new_width, new_height))
            new_geo.moveCenter(center)
            card.setGeometry(new_geo)
        
        def on_leave(event):
            """鼠标离开事件 — 移除悬停效果"""
            card.setGeometry(original_geometry)
        
        card.enterEvent = on_enter
        card.leaveEvent = on_leave
    
    @staticmethod
    def apply_click_feedback(card: QWidget, callback=None):
        """应用点击反馈"""
        def on_press(event):
            """鼠标按下事件"""
            # 按下时缩小
            center = card.geometry().center()
            new_geo = card.geometry()
            new_geo.setSize(QSize(
                int(new_geo.width() * 0.98),
                int(new_geo.height() * 0.98)
            ))
            new_geo.moveCenter(center)
            card.setGeometry(new_geo)
        
        def on_release(event):
            """鼠标释放事件"""
            # 释放时恢复
            anim = ScaleAnimation.scale_in(card, 150)
            anim.start()
            
            if callback:
                callback()
        
        card.mousePressEvent = on_press
        card.mouseReleaseEvent = on_release


# ═══════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════

def animate_widget_appear(widget: QWidget, animation_type: str = "fade_slide"):
    """便捷函数 - 让组件以动画方式出现"""
    if animation_type == "fade":
        anim = FadeAnimation.fade_in(widget)
        anim.start()
    elif animation_type == "slide_up":
        anim = SlideAnimation.slide_in_from_bottom(widget)
        anim.start()
    elif animation_type == "scale":
        anim = ScaleAnimation.scale_in(widget)
        anim.start()
    else:  # fade_slide
        group = CombinedAnimation.fade_slide_in(widget, "bottom")
        group.start()


def animate_number(label: QLabel, from_val: float, to_val: float,
                   duration_ms: int = 800, decimals: int = 2,
                   prefix: str = "", suffix: str = ""):
    """便捷函数 - 数字滚动动画"""
    anim_label = NumberRollAnimation(label.parent(), decimals, prefix, suffix)
    anim_label.set_value(from_val)
    anim_label.animate_to(to_val, duration_ms)


if __name__ == "__main__":
    # 测试
    from PyQt5.QtWidgets import QApplication, QFrame, QVBoxLayout
    import sys
    
    app = QApplication(sys.argv)
    
    window = QFrame()
    window.setFixedSize(400, 300)
    layout = QVBoxLayout(window)
    
    # 测试数字滚动
    num_label = NumberRollAnimation(decimals=2, prefix="¥", suffix="")
    num_label.set_value(0)
    num_label.animate_to(12345.67)
    layout.addWidget(num_label)
    
    window.show()
    sys.exit(app.exec_())
