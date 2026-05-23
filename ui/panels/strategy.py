#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能策略页 v2.0 — ML 模型状态面板
================================

特性:
- 动态数据展示
- 丰富的交互动画
- 实时状态更新
- 模型性能指标可视化
"""

import os
import json
import math
from datetime import datetime
from threading import Thread

from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QGridLayout,
                             QLabel, QPushButton, QFrame, QProgressBar,
                             QWidget, QScrollArea, QSizePolicy)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSize
from PyQt5.QtGui import QFont, QColor, QPainter, QLinearGradient, QPen, QPainterPath

from ui.theme import get_current_colors
from ui.animations import NumberRollAnimation
from core.logger import log


# ═══════════════════════════════════════════════════════════════
# 圆形进度指示器
# ═══════════════════════════════════════════════════════════════

class CircularProgress(QFrame):
    """圆形进度指示器"""
    
    def __init__(self, parent=None, size: int = 80, line_width: int = 6):
        """初始化"""
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._value = 0
        self._max_value = 100
        self._line_width = line_width
        self._color = "#3b82f6"
        self._bg_color = "#e2e8f0"
        
        # 中心标签
        self._label = QLabel("0%", self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        self._label.setGeometry(0, 0, size, size)
    
    def set_value(self, value: float):
        """设置进度值"""
        self._value = max(0, min(value, self._max_value))
        self._label.setText(f"{self._value:.0f}%")
        self.update()
    
    def set_color(self, color: str):
        """设置进度颜色"""
        self._color = color
        self.update()
    
    def paintEvent(self, event):
        """绘制事件 — PyQt5重绘回调"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 背景圆环
        pen = QPen(QColor(self._bg_color))
        pen.setWidth(self._line_width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(self._line_width//2, self._line_width//2,
                       self.width() - self._line_width,
                       self.height() - self._line_width,
                       0, 360 * 16)
        
        # 进度圆环
        pen = QPen(QColor(self._color))
        pen.setWidth(self._line_width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        
        span = int(-self._value / self._max_value * 360 * 16)
        painter.drawArc(self._line_width//2, self._line_width//2,
                       self.width() - self._line_width,
                       self.height() - self._line_width,
                       90 * 16, span)


# ═══════════════════════════════════════════════════════════════
# 指标卡片
# ═══════════════════════════════════════════════════════════════

class MetricCard(QFrame):
    """指标卡片 - 带动画效果"""
    
    clicked = pyqtSignal()
    
    def __init__(self, title: str, icon: str = "", parent=None):
        """初始化"""
        super().__init__(parent)
        self._title = title
        self._icon = icon
        self._value_label = None
        self._init_ui()
        self._apply_hover_effect()
    
    def _init_ui(self):
        c = get_current_colors()
        
        self.setStyleSheet(f"""
            MetricCard {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
                padding: 16px;
            }}
            MetricCard:hover {{
                border-color: {c.border_strong};
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # 标题行
        title_row = QHBoxLayout()
        
        if self._icon:
            icon_lbl = QLabel(self._icon)
            icon_lbl.setFont(QFont("Microsoft YaHei", 14))
            title_row.addWidget(icon_lbl)
        
        title_lbl = QLabel(self._title)
        title_lbl.setFont(QFont("Microsoft YaHei", 11))
        title_lbl.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        
        layout.addLayout(title_row)
        
        # 数值
        self._value_label = QLabel("—")
        self._value_label.setFont(QFont("DIN Alternate", 24, QFont.Bold))
        self._value_label.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(self._value_label)
    
    def _apply_hover_effect(self):
        """应用悬停效果"""
        self.setCursor(Qt.PointingHandCursor)
    
    def set_value(self, value: str, color: str = None):
        """设置数值"""
        self._value_label.setText(value)
        if color:
            self._value_label.setStyleSheet(f"color: {color}; background: transparent;")
    
    def mousePressEvent(self, event):
        """鼠标按下事件 — PyQt5交互回调"""
        self.clicked.emit()


# ═══════════════════════════════════════════════════════════════
# 训练流水线步骤可视化 — 3层布局 + 曲线箭头
#
# 布局 (3列, 中排4个盒子):
#   上排:  ②  ⑤  ⑧
#   中排: ①  ④  ⑦  ⑨
#   下排:  ③  ⑥
#
# 连线: 1↕2↕3 → 4↕56 → 7→8→9
# ═══════════════════════════════════════════════════════════════

_PIPELINE_STEPS = [
    ("📦", "数据采集", "下载K线数据", False),
    ("📊", "数据质检", "覆盖度/标签分布/类别均衡", False),
    ("🏷️", "自动标注", "识别买卖信号", False),
    ("📐", "构建样本", "特征计算+滑窗+Winsorize", False),
    ("🔍", "特征筛选", "IC+LGBM重要性 30→N维", True),
    ("✂️", "数据划分", "时序80/20划分", True),
    ("🧠", "模型训练", "LightGBM + early_stopping", False),
    ("📈", "模型评估", "F1/精度/召回/Walk-forward", True),
    ("💾", "保存部署", "joblib打包 + reload", False),
]

# 坐标: {step_idx: (row, col)}
#   上排(row0): ②⑤⑧ → col 0,1,2
#   中排(row1): ①④⑦⑨ → col 0,1,2,3
#   下排(row2): ③⑥   → col 0,1
_STEP_POS = {
    0: (1, 0),   # ① 数据采集  中排col0
    1: (0, 1),   # ② 数据质检  上排col1
    2: (2, 1),   # ③ 自动标注  下排col1
    3: (1, 2),   # ④ 构建样本  中排col2
    4: (0, 3),   # ⑤ 特征筛选  上排col3
    5: (2, 4),   # ⑥ 数据划分  下排col4
    6: (1, 5),   # ⑦ 模型训练  中排col5
    7: (0, 6),   # ⑧ 模型评估  上排col6
    8: (1, 7),   # ⑨ 保存部署  中排col7
}

# 连线: 1↕23, 4↕56, 7→8→9, 3→4, 6→7
_STEP_EDGES = [
    (0, 1),  # ①→②  上
    (1, 2),  # ②→③  下
    (2, 3),  # ③→④  右下
    (3, 4),  # ④→⑤  上
    (4, 5),  # ⑤→⑥  下
    (5, 6),  # ⑥→⑦  右上
    (6, 7),  # ⑦→⑧  上
    (7, 8),  # ⑧→⑨  下
]

_STEP_PENDING = 0
_STEP_ACTIVE = 1
_STEP_DONE = 2
_STEP_ERROR = 3
_STEP_SKIPPED = 4


class PipelineStepper(QFrame):
    """训练流水线 — 3层布局 + 曲线箭头, 自适应宽度"""

    BOX_H = 48
    ROW_GAP = 20
    MARGIN = 16

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)
        self._statuses = [_STEP_PENDING] * 9
        self._elapsed = [""] * 9
        self._descriptions = [""] * 9
        self._start_time = None
        self._step_start = None
        self._step_times = {}
        self._step_start_times = {}  # 每步骤独立记录开始时间 (修复耗时=0)
        self._dash_offset = 0.0
        self._active_step = -1

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(50)
        self._anim_timer.timeout.connect(self._tick_animation)

        self._qtimer = QTimer(self)
        self._qtimer.setInterval(1000)
        self._qtimer.timeout.connect(self._tick_clock)

        self.setFixedHeight(self._calc_height())
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    # ── 自适应尺寸 ──

    TOTAL_COLS = 8

    def _calc_height(self):
        return self.MARGIN * 2 + self.BOX_H * 3 + self.ROW_GAP * 2

    def _box_w(self):
        """动态计算盒子宽度, 根据面板宽度自适应"""
        w = self.width() if self.width() > 50 else 800
        usable = w - self.MARGIN * 2
        bw = (usable - (self.TOTAL_COLS - 1) * 4) // self.TOTAL_COLS
        return max(72, min(bw, 130))  # 钳制在 72~130

    def _col_gap(self):
        """动态列间距"""
        bw = self._box_w()
        w = self.width() if self.width() > 50 else 800
        usable = w - self.MARGIN * 2 - bw * self.TOTAL_COLS
        return max(2, usable // (self.TOTAL_COLS - 1)) if self.TOTAL_COLS > 1 else 0

    def _col_x(self, col_idx):
        bw = self._box_w()
        gap = self._col_gap()
        return self.MARGIN + col_idx * (bw + gap)

    def sizeHint(self):
        """返回控件建议尺寸"""
        return QSize(800, self._calc_height())

    # ── 坐标 ──

    def _box_pos(self, idx):
        row, col = _STEP_POS[idx]
        x = self._col_x(col)
        y = self.MARGIN + row * (self.BOX_H + self.ROW_GAP)
        return x, y

    def _edge(self, idx, side):
        x, y = self._box_pos(idx)
        bw, bh = self._box_w(), self.BOX_H
        if side == 'right':   return (x + bw, y + bh // 2)
        if side == 'left':    return (x, y + bh // 2)
        if side == 'bottom':  return (x + bw // 2, y + bh)
        if side == 'top':     return (x + bw // 2, y)
        return (x + bw // 2, y + bh // 2)

    # ── 状态控制 ──

    def begin(self):
        """开始进度条 — 设置总步数"""
        self._statuses = [_STEP_PENDING] * 9
        self._elapsed = [""] * 9
        self._descriptions = [""] * 9
        self._start_time = datetime.now()
        self._step_start = datetime.now()
        self._step_times = {}
        self._step_start_times = {}  # 每步骤独立记录开始时间
        self._active_step = -1
        self._dash_offset = 0.0
        self._qtimer.start()
        self._anim_timer.start()
        self.update()

    def set_step(self, step_idx, log_text=""):
        """设置当前步骤 — 更新进度显示"""
        now = datetime.now()

        # 同一步骤重复调用只更新描述，不重置计时
        if step_idx == self._active_step and self._statuses[step_idx] == _STEP_ACTIVE:
            if log_text:
                self._descriptions[step_idx] = log_text
            self.update()
            return

        # 先保存当前活跃步骤的已用时间 (基于每步骤独立开始时间)
        if self._active_step >= 0 and self._active_step in self._step_start_times:
            step_start = self._step_start_times[self._active_step]
            secs = int((now - step_start).total_seconds())
            if secs < 60:
                self._elapsed[self._active_step] = f"{secs}s"
            else:
                m, s = divmod(secs, 60)
                self._elapsed[self._active_step] = f"{m}m{s:02d}s"

        for i in range(step_idx):
            if self._statuses[i] not in (_STEP_DONE, _STEP_SKIPPED):
                self._statuses[i] = _STEP_DONE
        if 0 <= step_idx < 9:
            self._statuses[step_idx] = _STEP_ACTIVE
            self._active_step = step_idx
            self._step_start = now
            # 只在该步骤第一次激活时记录开始时间，不重复重置
            if step_idx not in self._step_start_times:
                self._step_start_times[step_idx] = now
            if log_text:
                self._descriptions[step_idx] = log_text
        self.update()

    def step_done(self, step_idx, result=""):
        """标记步骤完成 — 绿色状态"""
        now = datetime.now()
        elapsed = ""
        # 优先从每步骤独立开始时间计算 (修复耗时=0问题)
        if step_idx in self._step_start_times:
            step_start = self._step_start_times[step_idx]
            secs = int((now - step_start).total_seconds())
            if secs < 60:
                elapsed = f"{secs}s"
            else:
                m, s = divmod(secs, 60)
                elapsed = f"{m}m{s:02d}s"
        elif self._step_start:
            secs = int((now - self._step_start).total_seconds())
            if secs < 60:
                elapsed = f"{secs}s"
            else:
                m, s = divmod(secs, 60)
                elapsed = f"{m}m{s:02d}s"
        elif step_idx in self._step_times:
            elapsed = self._step_times[step_idx]
        if elapsed:
            self._step_times[step_idx] = elapsed
        if 0 <= step_idx < 9:
            self._statuses[step_idx] = _STEP_DONE
            self._elapsed[step_idx] = elapsed
            self._descriptions[step_idx] = result
        self._step_start = now
        self.update()

    def step_skip(self, step_idx, reason=""):
        """标记步骤跳过 — 灰色状态"""
        if 0 <= step_idx < 9:
            # 保存当前步骤的已用时间 (基于每步骤独立开始时间)
            if self._active_step >= 0 and self._active_step in self._step_start_times:
                step_start = self._step_start_times[self._active_step]
                secs = int((datetime.now() - step_start).total_seconds())
                if secs > 0:
                    if secs < 60:
                        self._elapsed[self._active_step] = f"{secs}s"
                    else:
                        m, s = divmod(secs, 60)
                        self._elapsed[self._active_step] = f"{m}m{s:02d}s"
            self._statuses[step_idx] = _STEP_SKIPPED
            self._descriptions[step_idx] = reason or "已跳过"
        self._step_start = datetime.now()
        self.update()

    def step_error(self, step_idx, msg=""):
        """标记步骤出错 — 红色状态"""
        if 0 <= step_idx < 9:
            # 保存当前步骤的已用时间 (基于每步骤独立开始时间)
            if step_idx in self._step_start_times:
                step_start = self._step_start_times[step_idx]
                secs = int((datetime.now() - step_start).total_seconds())
                if secs > 0:
                    if secs < 60:
                        self._elapsed[step_idx] = f"{secs}s"
                    else:
                        m, s = divmod(secs, 60)
                        self._elapsed[step_idx] = f"{m}m{s:02d}s"
            elif self._step_start:
                secs = int((datetime.now() - self._step_start).total_seconds())
                if secs > 0:
                    if secs < 60:
                        self._elapsed[step_idx] = f"{secs}s"
                    else:
                        m, s = divmod(secs, 60)
                        self._elapsed[step_idx] = f"{m}m{s:02d}s"
            self._statuses[step_idx] = _STEP_ERROR
            self._descriptions[step_idx] = msg or "执行失败"
        self._qtimer.stop()
        self._anim_timer.stop()
        self.update()

    def finish(self):
        """完成进度条 — 全部步骤结束"""
        # 保存最后活跃步骤的已用时间 (基于每步骤独立开始时间)
        if self._active_step >= 0 and self._active_step in self._step_start_times:
            step_start = self._step_start_times[self._active_step]
            secs = int((datetime.now() - step_start).total_seconds())
            if secs > 0:
                if secs < 60:
                    self._elapsed[self._active_step] = f"{secs}s"
                else:
                    m, s = divmod(secs, 60)
                    self._elapsed[self._active_step] = f"{m}m{s:02d}s"
        for i in range(9):
            if self._statuses[i] != _STEP_SKIPPED:
                self._statuses[i] = _STEP_DONE
        self._active_step = 9
        self._qtimer.stop()
        self._anim_timer.stop()
        self.update()

    def _tick_animation(self):
        self._dash_offset = (self._dash_offset - 1.5) % 24
        self.update()

    def _tick_clock(self):
        """每秒刷新活跃步骤的实时耗时"""
        if self._active_step < 0 or self._active_step >= 9:
            return
        if self._statuses[self._active_step] != _STEP_ACTIVE:
            return
        if self._step_start:
            secs = int((datetime.now() - self._step_start).total_seconds())
            if secs < 60:
                self._elapsed[self._active_step] = f"{secs}s"
            else:
                m, s = divmod(secs, 60)
                self._elapsed[self._active_step] = f"{m}m{s:02d}s"
        self.update()

    # ── 颜色 ──

    def _colors_for(self, status):
        c = get_current_colors()
        if status == _STEP_DONE:
            return QColor("#dcfce7"), QColor("#22c55e"), QColor("#166534")
        elif status == _STEP_ACTIVE:
            return QColor("#dbeafe"), QColor("#3b82f6"), QColor("#1e40af")
        elif status == _STEP_ERROR:
            return QColor("#fee2e2"), QColor("#ef4444"), QColor("#991b1b")
        elif status == _STEP_SKIPPED:
            return QColor("#f1f5f9"), QColor("#94a3b8"), QColor("#64748b")
        else:
            return QColor("#f0f9ff"), QColor("#93c5fd"), QColor("#3b82f6")

    # ── 绘制 ──

    def paintEvent(self, event):
        """绘制事件 — PyQt5重绘回调"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = get_current_colors()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(c.bg_surface))
        painter.drawRoundedRect(0, 0, self.width(), self.height(), 14, 14)
        for fi, ti in _STEP_EDGES:
            self._draw_conn(painter, fi, ti)
        for i in range(9):
            self._draw_box(painter, i)

    # ── 连线 ──

    def _style(self, fi, ti):
        fs, ts = self._statuses[fi], self._statuses[ti]
        if fs == _STEP_DONE and ts in (_STEP_DONE, _STEP_ACTIVE):
            color, w, solid = QColor("#22c55e"), 2.5, True
        elif fs == _STEP_DONE:
            color, w, solid = QColor("#22c55e"), 2.0, True
        elif fs == _STEP_ACTIVE:
            color, w, solid = QColor("#3b82f6"), 2.0, False
        else:
            color, w, solid = QColor("#cbd5e1"), 1.5, False
        pen = QPen(color, w)
        pen.setCapStyle(Qt.RoundCap)
        if solid:
            pen.setStyle(Qt.SolidLine)
        else:
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern([6, 4])
            pen.setDashOffset(self._dash_offset)
        return pen, color

    def _draw_conn(self, painter, fi, ti):
        pen, color = self._style(fi, ti)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        fr, fc = _STEP_POS[fi]
        tr, tc = _STEP_POS[ti]

        # 同列 → 垂直曲线
        if fc == tc:
            p1 = self._edge(fi, 'bottom')
            p2 = self._edge(ti, 'top')
            dx = 15
            path = QPainterPath()
            path.moveTo(*p1)
            path.cubicTo(p1[0] + dx, (p1[1] + p2[1]) / 2,
                         p2[0] - dx, (p1[1] + p2[1]) / 2, *p2)
            painter.drawPath(path)
            self._arrow(painter, (p2[0] - dx, (p1[1] + p2[1]) / 2), p2, color)
            return

        # 同行 → 水平曲线
        if fr == tr:
            p1 = self._edge(fi, 'right') if fc < tc else self._edge(fi, 'left')
            p2 = self._edge(ti, 'left') if fc < tc else self._edge(ti, 'right')
            my = (p1[1] + p2[1]) / 2
            path = QPainterPath()
            path.moveTo(*p1)
            path.cubicTo(p1[0], my, p2[0], my, *p2)
            painter.drawPath(path)
            self._arrow(painter, (p2[0], my), p2, color)
            return

        # 跨行跨列 → L形曲线
        if fc < tc:
            # 右下: 从底部下到目标行，再右到目标左侧
            p1 = self._edge(fi, 'bottom')
            p2 = self._edge(ti, 'left')
            mid_y = p2[1]
            path = QPainterPath()
            path.moveTo(*p1)
            path.cubicTo(p1[0], mid_y, p2[0], mid_y, *p2)
            painter.drawPath(path)
            self._arrow(painter, (p2[0], mid_y), p2, color)
        else:
            # 右上: 从底部下到目标行，再左到目标右侧
            p1 = self._edge(fi, 'bottom')
            p2 = self._edge(ti, 'right')
            mid_y = p2[1]
            path = QPainterPath()
            path.moveTo(*p1)
            path.cubicTo(p1[0], mid_y, p2[0], mid_y, *p2)
            painter.drawPath(path)
            self._arrow(painter, (p2[0], mid_y), p2, color)

    def _arrow(self, painter, ctrl, tip, color):
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        dx = tip[0] - ctrl[0]
        dy = tip[1] - ctrl[1]
        ln = math.sqrt(dx * dx + dy * dy)
        if ln < 1: return
        udx, udy = dx / ln, dy / ln
        px, py = -udy, udx
        s = 6
        path = QPainterPath()
        path.moveTo(tip[0], tip[1])
        path.lineTo(tip[0] - udx * s + px * s * 0.45, tip[1] - udy * s + py * s * 0.45)
        path.lineTo(tip[0] - udx * s - px * s * 0.45, tip[1] - udy * s - py * s * 0.45)
        path.closeSubpath()
        painter.drawPath(path)

    # ── 步骤盒子 ──

    def get_active_description(self):
        """返回当前活跃步骤的描述文本, 供外部 Label 显示"""
        # 先找活跃步骤
        for i in range(9):
            icon, name, _, _ = _PIPELINE_STEPS[i]
            if self._statuses[i] == _STEP_ACTIVE and self._descriptions[i]:
                return f"{icon} {name}  Step{i+1}:  {self._descriptions[i]}"
            if self._statuses[i] == _STEP_ERROR and self._descriptions[i]:
                return f"✕ {name}  Step{i+1}:  {self._descriptions[i]}"
        # 无活跃步骤 → 显示最后完成步骤的结果
        for i in range(8, -1, -1):
            if self._statuses[i] == _STEP_DONE and self._descriptions[i]:
                icon, name, _, _ = _PIPELINE_STEPS[i]
                return f"✅ {name}: {self._descriptions[i]}"
        return ""

    def _draw_box(self, painter, idx):
        x, y = self._box_pos(idx)
        w, h = self._box_w(), self.BOX_H
        status = self._statuses[idx]
        icon, name, desc, is_branch = _PIPELINE_STEPS[idx]
        bg, border, text_c = self._colors_for(status)
        c = get_current_colors()
        r = 10

        if status == _STEP_ACTIVE:
            glow = QColor(border); glow.setAlpha(30)
            painter.setPen(Qt.NoPen); painter.setBrush(glow)
            painter.drawRoundedRect(x - 4, y - 4, w + 8, h + 8, r + 4, r + 4)

        painter.setPen(QPen(border, 2.0 if status == _STEP_ACTIVE else 1.5))
        painter.setBrush(bg)
        painter.drawRoundedRect(x, y, w, h, r, r)

        if status == _STEP_DONE:
            elapsed = self._elapsed[idx]
            if elapsed:
                painter.setPen(QColor("#166534"))
                painter.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
                painter.drawText(x, y + 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignBottom, name)
                painter.setPen(QColor("#22c55e"))
                painter.setFont(QFont("Consolas", 8))
                painter.drawText(x, y + h // 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignTop, elapsed)
            else:
                painter.setPen(QColor("#166534"))
                painter.setFont(QFont("Microsoft YaHei", 9))
                painter.drawText(x, y, w, h, Qt.AlignCenter, name)
        elif status == _STEP_ACTIVE:
            elapsed = self._elapsed[idx]
            painter.setPen(QColor("#1e40af"))
            painter.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
            painter.drawText(x, y + 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignBottom, name)
            painter.setPen(QColor("#3b82f6"))
            painter.setFont(QFont("Consolas", 8))
            painter.drawText(x, y + h // 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignTop, elapsed or "...")
        elif status == _STEP_ERROR:
            elapsed = self._elapsed[idx]
            if elapsed:
                painter.setPen(QColor("#991b1b"))
                painter.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
                painter.drawText(x, y + 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignBottom, f"✕ {name}")
                painter.setPen(QColor("#ef4444"))
                painter.setFont(QFont("Consolas", 8))
                painter.drawText(x, y + h // 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignTop, elapsed)
            else:
                painter.setPen(QColor("#991b1b"))
                painter.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
                painter.drawText(x, y + 2, w, h // 2, Qt.AlignCenter, "✕")
                painter.setFont(QFont("Microsoft YaHei", 8))
                painter.drawText(x, y + h // 2 - 2, w, h // 2, Qt.AlignCenter, name)
        elif status == _STEP_SKIPPED:
            elapsed = self._elapsed[idx]
            if elapsed:
                painter.setPen(QColor("#94a3b8"))
                painter.setFont(QFont("Microsoft YaHei", 8))
                painter.drawText(x, y + 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignBottom, f"– {name}")
                painter.setPen(QColor("#94a3b8"))
                painter.setFont(QFont("Consolas", 8))
                painter.drawText(x, y + h // 2, w, h // 2 - 2, Qt.AlignCenter | Qt.AlignTop, elapsed)
            else:
                painter.setPen(QColor("#94a3b8"))
                painter.setFont(QFont("Microsoft YaHei", 10))
                painter.drawText(x, y + 2, w, h // 2, Qt.AlignCenter, "–")
                painter.setFont(QFont("Microsoft YaHei", 8))
                painter.drawText(x, y + h // 2 - 2, w, h // 2, Qt.AlignCenter, name)
        else:
            painter.setPen(text_c)
            painter.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
            painter.drawText(x, y + 1, w, h // 2 + 2, Qt.AlignCenter, icon)
            lc = QColor(c.text_primary) if status == _STEP_ACTIVE else QColor("#3b82f6")
            painter.setPen(lc)
            painter.setFont(QFont("Microsoft YaHei", 8, QFont.Bold if status == _STEP_ACTIVE else QFont.Normal))
            painter.drawText(x, y + h // 2, w, h // 2 - 2, Qt.AlignCenter, name)


# ═══════════════════════════════════════════════════════════════
#  ML 策略页主面板
# ═══════════════════════════════════════════════════════════════

class StrategyPanel(QFrame):
    """ML 策略页 — 模型状态、训练流水线、信号配置"""

    model_status_changed = pyqtSignal(str, dict)   # (status, info)
    training_started = pyqtSignal()
    training_completed = pyqtSignal(dict)

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)
        self._model_info = None
        self.setObjectName("strategyPanel")

        c = get_current_colors()
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent;")
        
        # 内容容器
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(20)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # ═══ 按钮区域（开始训练 | 自动优化 | 选项框）═══
        title_row = QHBoxLayout()
        title_row.setSpacing(0)

        # 开始训练按钮 (带进度条)
        self._train_btn = QPushButton("▶ 开始训练")
        self._train_btn.setFixedSize(140, 40)
        self._train_btn.setCursor(Qt.PointingHandCursor)
        self._train_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.accent};
                color: white;
                border: none;
                border-radius: 10px;
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
        self._train_progress = QProgressBar()
        self._train_progress.setFixedSize(140, 6)
        self._train_progress.setRange(0, 100)
        self._train_progress.setValue(0)
        self._train_progress.setTextVisible(False)
        self._train_progress.setStyleSheet(f"""
            QProgressBar {{
                background: rgba(255,255,255,0.15);
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: white;
                border-radius: 3px;
            }}
        """)

        # 网格搜索档位选择
        from PyQt5.QtWidgets import QComboBox
        self._grid_preset_combo = QComboBox()
        self._grid_preset_combo.setFixedSize(180, 40)
        self._grid_preset_combo.addItems(["⚡ 快速(8组)", "📐 标准(27组)", "🔍 全面(81组)"])
        self._grid_preset_combo.setCurrentIndex(1)  # 默认标准
        self._grid_preset_combo.setCursor(Qt.PointingHandCursor)
        self._grid_preset_combo.setStyleSheet(f"""
            QComboBox {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                border-radius: 10px;
                padding: 0 12px;
                font-size: 12px;
                font-family: "Microsoft YaHei";
            }}
            QComboBox:hover {{
                border-color: {c.accent};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox QAbstractItemView {{
                background: {c.bg_surface};
                color: {c.text_primary};
                border: 1px solid {c.border};
                selection-background-color: {c.accent};
            }}
        """)

        # 自动优化按钮
        self._optimize_btn = QPushButton("🔧 自动优化")
        self._optimize_btn.setFixedSize(140, 40)
        self._optimize_btn.setCursor(Qt.PointingHandCursor)
        self._optimize_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.orange if hasattr(c, 'orange') else '#f59e0b'};
                color: white;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: bold;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                background: {c.orange_hover if hasattr(c, 'orange_hover') else '#d97706'};
            }}
            QPushButton:disabled {{
                background: {c.border};
                color: {c.text_secondary};
            }}
        """)
        self._optimize_btn.clicked.connect(self._start_optimize)

        self._optimize_progress = QProgressBar()
        self._optimize_progress.setFixedSize(140, 6)
        self._optimize_progress.setRange(0, 100)
        self._optimize_progress.setValue(0)
        self._optimize_progress.setTextVisible(False)
        self._optimize_progress.setStyleSheet(f"""
            QProgressBar {{
                background: rgba(255,255,255,0.15);
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: white;
                border-radius: 3px;
            }}
        """)

        # 创建按钮布局（两行：上排按钮+选项框，下排进度条）
        btn_container = QVBoxLayout()
        btn_container.setContentsMargins(0, 4, 0, 4)
        btn_container.setSpacing(6)

        # 上排: 开始训练 | 自动优化 | 选项框 — 全部同一水平线
        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)
        btn_row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        btn_row.addWidget(self._train_btn)
        btn_row.addWidget(self._optimize_btn)
        btn_row.addWidget(self._grid_preset_combo)
        btn_row.addStretch()
        btn_container.addLayout(btn_row)

        # 下排: 进度条 — 与按钮等宽
        progress_row = QHBoxLayout()
        progress_row.setSpacing(16)
        progress_row.setAlignment(Qt.AlignLeft)
        progress_row.addWidget(self._train_progress)
        progress_row.addWidget(self._optimize_progress)
        # 选项框下方留等宽空白
        spacer = QWidget()
        spacer.setFixedSize(self._grid_preset_combo.sizeHint())
        progress_row.addWidget(spacer)
        progress_row.addStretch()
        btn_container.addLayout(progress_row)

        title_row.addLayout(btn_container)

        self._train_btn.clicked.connect(self._start_training)

        # ═══ 自动训练：持仓变化后防抖触发 ═══
        from core.config import get_config
        self._auto_train_cfg = get_config().data
        self._auto_train_timer = QTimer(self)
        self._auto_train_timer.setSingleShot(True)
        self._auto_train_timer.setInterval(self._auto_train_cfg.auto_train_delay * 1000)
        self._auto_train_timer.timeout.connect(self._auto_train_trigger)
        self._auto_train_pending = False  # 是否有待执行的自动训练

        layout.addLayout(title_row)

        # ═══ 统一信息显示区域（训练和优化共用）═══
        info_frame = QFrame()
        info_frame.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 8px;
            }}
        """)
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(8)

        # 状态描述（训练和优化共用单个Label，避免框太小）
        self._pipeline_desc = QLabel("💡 点击「开始训练」执行9步ML流水线，或点击「自动优化」搜索最优参数")
        self._pipeline_desc.setFont(QFont("Microsoft YaHei", 11))
        self._pipeline_desc.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        self._pipeline_desc.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._pipeline_desc.setWordWrap(True)
        self._pipeline_desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._pipeline_desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._pipeline_desc.setMinimumHeight(36)
        info_layout.addWidget(self._pipeline_desc)

        layout.addWidget(info_frame)

        # ═══ 训练流水线步骤可视化 ═══
        self._pipeline = PipelineStepper()
        layout.addWidget(self._pipeline)

        # 定时刷新描述文本
        self._desc_timer = QTimer(self)
        self._desc_timer.setInterval(200)
        self._desc_timer.timeout.connect(self._refresh_pipeline_desc)

        # ═══ 模型状态网格 ═══
        status_title = QLabel("🤖 模型状态")
        status_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        status_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(status_title)

        self._model_status_grid = self._create_model_status_grid()
        layout.addWidget(self._model_status_grid)
        
        # ═══ 性能指标网格 ═══
        metrics_title = QLabel("📊 模型性能")
        metrics_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        metrics_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(metrics_title)
        
        self._metrics_grid = self._create_metrics_grid()
        layout.addWidget(self._metrics_grid)
        
        # ═══ 信号配置网格 ═══
        config_title = QLabel("⚙️ 信号配置")
        config_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        config_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(config_title)

        self._config_grid = self._create_signal_config_grid()
        layout.addWidget(self._config_grid)

        # ═══ 策略参数微调 ═══
        params_title = QLabel("🎛️ 策略参数")
        params_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        params_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(params_title)

        self._params_grid = self._create_strategy_params_grid()
        layout.addWidget(self._params_grid)

        # ═══ 当日信号统计 ═══
        stats_title = QLabel("📈 当日信号统计")
        stats_title.setFont(QFont("Microsoft YaHei", 14, QFont.Bold))
        stats_title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
        layout.addWidget(stats_title)

        self._stats_grid = self._create_signal_stats_grid()
        layout.addWidget(self._stats_grid)

        # ═══ 手工调参使用说明 ═══
        help_frame = QFrame()
        help_frame.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 10px;
            }}
        """)
        help_layout = QVBoxLayout(help_frame)
        help_layout.setContentsMargins(16, 12, 16, 12)
        help_layout.setSpacing(4)

        help_title = QLabel("📖 手工调参使用说明")
        help_title.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        help_title.setStyleSheet(f"color: {c.text_primary};")
        help_layout.addWidget(help_title)

        help_text = (
            "ML置信度: 模型预测置信度阈值(0.50-0.95)。值越高信号少但越可靠，建议0.65-0.75。\n"
            "强信号阈值: 强信号判定线(0.60-0.99)。需高于置信度，建议比置信度高0.10-0.15。\n"
            "扫描间隔: 顾问扫描市场的时间间隔(1-60分钟)。频繁扫描增加CPU负载。\n"
            "信号冷却: 同一标的连续信号间的最小间隔(5-120分钟)。防止过度交易。\n"
            "规则止盈/止损: 非ML信号的固定止盈止损百分比。与ML信号独立运作。\n"
            "特征窗口: 模型输入的K线滑窗长度(5-50根)。需与训练时保持一致。\n"
            "日交易上限: 单日最大交易次数限制(1-100次)。超过后当日不再开仓。\n"
            "趋势/震荡仓位: 根据市场状态自动调整仓位系数(0.1-1.0)。趋势行情可重仓。\n"
            "止损参数: 初始止损百分比和跟踪回撤百分比。建议初始3%-5%，跟踪1.5%-3%。\n"
            "保存参数: 修改参数后必须点击「保存参数」按钮才能持久化到config.yaml文件。\n"
            "自动优化: 点击「自动优化」将自动搜索最优参数组合，完成后自动应用到面板。"
        )
        help_body = QLabel(help_text)
        help_body.setFont(QFont("Microsoft YaHei", 10))
        help_body.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
        help_body.setWordWrap(True)
        help_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        help_layout.addWidget(help_body)

        help_tip = QLabel("💡 提示: 所有参数修改均为即时生效（运行时），但只有点击「保存参数」后才会在下次启动时保留。")
        help_tip.setFont(QFont("Microsoft YaHei", 10))
        help_tip.setStyleSheet(f"color: {c.text_secondary};")
        help_tip.setWordWrap(True)
        help_layout.addWidget(help_tip)

        layout.addWidget(help_frame)
        layout.addStretch()

        # 定时刷新统计（每30秒）
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_signal_stats)
        self._stats_timer.start(30000)
        # 首次刷新
        QTimer.singleShot(1000, self._refresh_signal_stats)
        
        scroll.setWidget(container)
        main_layout.addWidget(scroll)
    
    def _create_model_status_grid(self) -> QFrame:
        """创建模型状态网格 - 4个独立小卡片"""
        c = get_current_colors()

        grid = QFrame()
        layout = QGridLayout(grid)
        layout.setSpacing(12)

        self._status_cards = {}
        status_items = [
            ("模型版本", "—", "version", "📦"),
            ("模型类型", "—", "type", "🧠"),
            ("训练时间", "未训练", "updated", "🕐"),
            ("特征维度", "—", "samples", "📐"),
        ]

        for i, (title, value, key, icon) in enumerate(status_items):
            card = MetricCard(title, icon)
            card.set_value(value)
            self._status_cards[key] = card
            layout.addWidget(card, 0, i)

        return grid

    def _update_model_status(self, version, model_type, updated, feature_dim):
        """更新模型状态"""
        self._status_cards["version"].set_value(version or "—")
        self._status_cards["type"].set_value(model_type or "—")
        self._status_cards["updated"].set_value(updated if updated and updated != "—" else "未训练")
        self._status_cards["samples"].set_value(str(feature_dim) if feature_dim and feature_dim != "—" else "—")
    
    def _create_metrics_grid(self) -> QFrame:
        """创建性能指标网格"""
        c = get_current_colors()

        grid = QFrame()
        layout = QGridLayout(grid)
        layout.setSpacing(12)

        self._metric_cards = {}
        metrics = [
            ("特征数", "—", "accuracy", "📐"),
            ("类别数", "—", "f1", "🏷️"),
            ("迭代次数", "—", "precision", "🔄"),
            ("模型大小", "—", "recall", "💾"),
        ]

        for i, (title, value, key, icon) in enumerate(metrics):
            card = MetricCard(title, icon)
            card.set_value(value)
            self._metric_cards[key] = card
            layout.addWidget(card, 0, i)

        return grid
    
    def _create_signal_config_grid(self) -> QFrame:
        """创建信号配置网格 - 4个独立小卡片"""
        c = get_current_colors()

        grid = QFrame()
        layout = QGridLayout(grid)
        layout.setSpacing(12)

        config_items = [
            ("置信度阈值", "70%", "confidence", "🎯"),
            ("强信号阈值", "85%", "strong", "⚡"),
            ("特征窗口", "20 根", "window", "📊"),
            ("最小交易量", "100手", "min_volume", "📦"),
        ]

        self._config_cards = {}
        for i, (title, value, key, icon) in enumerate(config_items):
            card = MetricCard(title, icon)
            card.set_value(value, c.green)
            self._config_cards[key] = card
            layout.addWidget(card, 0, i)

        return grid

    def _create_strategy_params_grid(self) -> QFrame:
        """策略参数微调网格 — 4列2行布局: 上排信号/扫描, 下排风控"""
        from PyQt5.QtWidgets import QDoubleSpinBox, QSpinBox
        c = get_current_colors()

        grid = QFrame()
        grid.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
            }}
        """)
        layout = QGridLayout(grid)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 16, 20, 16)

        # 读取当前配置
        from core.config import get_config
        cfg = get_config()

        def _make_spin(label_text, value, min_val, max_val, step, suffix="", is_float=True):
            """创建一行: 标签 + 输入框"""
            row = QHBoxLayout()
            row.setSpacing(8)

            lbl = QLabel(label_text)
            lbl.setFont(QFont("Microsoft YaHei", 11))
            lbl.setStyleSheet(f"color: {c.text_secondary}; background: transparent;")
            lbl.setFixedWidth(100)
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
            spin.setFixedWidth(100)
            # 禁用上下箭头按钮和滚轮事件，改为纯手动输入
            spin.setButtonSymbols(QSpinBox.NoButtons)
            spin.wheelEvent = lambda event: None  # 忽略滚轮事件
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

        def _make_col(title_text, params):
            """创建一列: 标题 + N个参数行"""
            col = QVBoxLayout()
            col.setSpacing(10)

            title = QLabel(title_text)
            title.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
            title.setStyleSheet(f"color: {c.text_primary}; background: transparent;")
            col.addWidget(title)

            spins = []
            for label_text, value, min_val, max_val, step, suffix, is_float in params:
                r, spin = _make_spin(label_text, value, min_val, max_val, step, suffix, is_float)
                col.addLayout(r)
                spins.append(spin)

            return col, spins

        # ═══ 上排: 信号 & 扫描 (4列) ═══
        col_信号, spins_信号 = _make_col("📡 信号", [
            ("ML置信度",   cfg.ml.confidence_threshold, 0.5, 0.95, 0.05, "",      True),
            ("强信号阈值", cfg.ml.strong_threshold,     0.6, 0.99, 0.05, "",      True),
        ])
        col_扫描, spins_扫描 = _make_col("🔍 扫描", [
            ("扫描间隔", 5,  1, 60,  1, " 分钟", False),
            ("信号冷却", 30, 5, 120, 5, " 分钟", False),
        ])
        col_止盈止损, spins_止盈止损 = _make_col("💰 止盈止损", [
            ("规则止盈", 3.0, 1.0, 10.0, 0.5, "%", True),
            ("规则止损", 2.0, 0.5, 5.0,  0.5, "%", True),
        ])
        col_模型, spins_模型 = _make_col("🧠 模型", [
            ("特征窗口", 20, 5, 50, 5, " 根", False),
            ("N_estimators", cfg.ml.n_estimators, 50, 500, 50, "", False),
        ])

        self._param_confidence    = spins_信号[0]
        self._param_strong        = spins_信号[1]
        self._param_scan_interval = spins_扫描[0]
        self._param_cooldown      = spins_扫描[1]
        self._param_rule_sell_win = spins_止盈止损[0]
        self._param_rule_sell_loss= spins_止盈止损[1]
        self._param_lookback      = spins_模型[0]
        self._param_n_estimators  = spins_模型[1]

        layout.addLayout(col_信号,     0, 0)
        layout.addLayout(col_扫描,     0, 1)
        layout.addLayout(col_止盈止损, 0, 2)
        layout.addLayout(col_模型,     0, 3)

        # ═══ 下排: 风控 (4列) ═══
        col_交易限制, spins_交易限制 = _make_col("🚫 交易限制", [
            ("日交易上限", 20, 1, 100, 5, " 次", False),
            ("日亏损限额", 3.0, 1.0, 10.0, 0.5, "%", True),
        ])
        col_趋势仓位, spins_趋势仓位 = _make_col("📈 趋势仓位", [
            ("趋势行情", 1.0, 0.3, 1.0, 0.1, "", True),
            ("震荡行情", 0.6, 0.1, 1.0, 0.1, "", True),
        ])
        col_波动仓位, spins_波动仓位 = _make_col("⚡ 波动仓位", [
            ("高波动",   0.3, 0.1, 0.8, 0.1, "", True),
            ("止损类型", 0,   0,   2,   1,   "", False),  # 0=trailing, 1=fixed, 2=atr
        ])
        col_止损参数, spins_止损参数 = _make_col("🛡️ 止损参数", [
            ("初始止损", cfg.stop_loss.initial_stop_pct, 0.01, 0.10, 0.01, "", True),
            ("跟踪回撤", cfg.stop_loss.trailing_pct,     0.01, 0.05, 0.005, "", True),
        ])

        self._param_max_daily_trades = spins_交易限制[0]
        self._param_daily_loss_limit = spins_交易限制[1]
        self._param_trending_scale   = spins_趋势仓位[0]
        self._param_ranging_scale    = spins_趋势仓位[1]
        self._param_volatile_scale   = spins_波动仓位[0]
        self._param_stop_type        = spins_波动仓位[1]
        self._param_initial_stop     = spins_止损参数[0]
        self._param_trailing_pct     = spins_止损参数[1]

        layout.addLayout(col_交易限制, 1, 0)
        layout.addLayout(col_趋势仓位, 1, 1)
        layout.addLayout(col_波动仓位, 1, 2)
        layout.addLayout(col_止损参数, 1, 3)

        # ═══ 保存按钮 ═══
        self._save_btn = QPushButton("💾 保存参数")
        self._save_btn.setFixedHeight(36)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.accent};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 20px;
                font-size: 12px;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                background: {c.accent_hover};
            }}
        """)
        self._save_btn.clicked.connect(self._save_strategy_params)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(self._save_btn)
        layout.addLayout(save_row, 2, 0, 1, 4)

        return grid

    def _save_strategy_params(self):
        """保存策略参数到配置"""
        try:
            from core.config import get_config
            cfg = get_config()

            # 更新运行时配置
            cfg.ml.confidence_threshold = self._param_confidence.value()
            cfg.ml.strong_threshold = self._param_strong.value()
            cfg.ml.n_estimators = self._param_n_estimators.value()
            cfg.ml.lookback = self._param_lookback.value()
            cfg.stop_loss.initial_stop_pct = self._param_initial_stop.value()
            cfg.stop_loss.trailing_pct = self._param_trailing_pct.value()

            # 保存到 config.yaml
            import yaml
            from pathlib import Path
            config_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            data = {}
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}

            data.setdefault('ml', {})['confidence_threshold'] = self._param_confidence.value()
            data['ml']['strong_threshold'] = self._param_strong.value()
            data['ml']['n_estimators'] = self._param_n_estimators.value()
            data['ml']['lookback'] = self._param_lookback.value()
            data.setdefault('stop_loss', {})['initial_stop_pct'] = self._param_initial_stop.value()
            data['stop_loss']['trailing_pct'] = self._param_trailing_pct.value()

            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

            # ✅ 保存成功 — 按钮变绿 2 秒
            self._show_save_feedback(True)
            log.signal_log("strategy", "策略参数已保存",
                           f"置信度{self._param_confidence.value():.0%}, "
                           f"扫描{self._param_scan_interval.value()}分钟, "
                           f"止损{self._param_initial_stop.value():.1%}, "
                           f"跟踪{self._param_trailing_pct.value():.1%}")
        except Exception as e:
            self._show_save_feedback(False)
            log.warning("strategy", f"保存参数失败: {e}")

    def _show_save_feedback(self, success: bool):
        """保存后按钮颜色反馈：成功变绿，失败变红，2秒后恢复"""
        if success:
            self._save_btn.setText("✅ 已保存")
            self._save_btn.setStyleSheet("""
                QPushButton {
                    background: #22c55e;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 0 20px;
                    font-size: 12px;
                    font-family: "Microsoft YaHei";
                    font-weight: bold;
                }
            """)
        else:
            self._save_btn.setText("❌ 保存失败")
            self._save_btn.setStyleSheet("""
                QPushButton {
                    background: #ef4444;
                    color: white;
                    border: none;
                    border-radius: 8px;
                    padding: 0 20px;
                    font-size: 12px;
                    font-family: "Microsoft YaHei";
                    font-weight: bold;
                }
            """)
        QTimer.singleShot(2000, self._restore_save_btn)

    def _restore_save_btn(self):
        """恢复保存按钮原样"""
        c = get_current_colors()
        self._save_btn.setText("💾 保存参数")
        self._save_btn.setStyleSheet(f"""
            QPushButton {{
                background: {c.accent};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 0 20px;
                font-size: 12px;
                font-family: "Microsoft YaHei";
            }}
            QPushButton:hover {{
                background: {c.accent_hover};
            }}
        """)

    def _create_signal_stats_grid(self) -> QFrame:
        """创建当日信号统计网格 - 实时监控指标"""
        c = get_current_colors()

        grid = QFrame()
        layout = QGridLayout(grid)
        layout.setSpacing(12)

        self._stats_cards = {}
        stats_items = [
            ("今日信号", "—", "total", "📊"),
            ("买入/卖出", "—", "buy_sell", "🔄"),
            ("止盈/止损", "—", "stop_tp", "⚡"),
            ("胜率", "—", "win_rate", "🎯"),
        ]

        for i, (title, value, key, icon) in enumerate(stats_items):
            card = MetricCard(title, icon)
            card.set_value(value)
            self._stats_cards[key] = card
            layout.addWidget(card, 0, i)

        return grid

    def _refresh_pipeline_desc(self):
        """定时刷新流程图描述标签"""
        desc = self._pipeline.get_active_description()
        self._pipeline_desc.setText(desc)

    def _refresh_signal_stats(self):
        """刷新当日信号统计"""
        c = get_current_colors()
        try:
            from strategies.monitor import get_monitor
            monitor = get_monitor()
            stats = monitor.get_daily_stats()

            if stats['total_signals'] == 0:
                self._stats_cards["total"].set_value("0", c.text_secondary)
                self._stats_cards["buy_sell"].set_value("—", c.text_secondary)
                self._stats_cards["stop_tp"].set_value("—", c.text_secondary)
                self._stats_cards["win_rate"].set_value("—", c.text_secondary)
                return

            # 总信号数
            self._stats_cards["total"].set_value(str(stats['total_signals']), c.blue)

            # 买入/卖出
            buy_sell_text = f"{stats['buy_signals']} / {stats['sell_signals']}"
            self._stats_cards["buy_sell"].set_value(buy_sell_text, c.text_primary)

            # 止盈/止损
            stop_tp_text = f"{stats['take_profit_signals']} / {stats['stop_loss_signals']}"
            color = c.green if stats['take_profit_signals'] >= stats['stop_loss_signals'] else c.orange
            self._stats_cards["stop_tp"].set_value(stop_tp_text, color)

            # 胜率
            win_rate = stats['win_rate'] * 100
            win_color = c.green if win_rate >= 50 else c.red if win_rate < 30 else c.orange
            self._stats_cards["win_rate"].set_value(f"{win_rate:.0f}%", win_color)

        except Exception:
            pass  # 监控器未就绪时静默失败

    def _load_model_info(self):
        """加载模型信息"""
        try:
            from strategies.ml.model import get_model
            model = get_model()
            info = model.get_info()
            self._update_model_display(info)
        except Exception as e:
            log.error("strategy", f"模型信息加载异常: {e}", "")
            # 用默认值填充，不弹红报错
            fallback_info = {
                'version': 'rule_fallback',
                'type': 'rule_fallback',
                'loaded': False,
                'feature_count': 30,
            }
            try:
                self._update_model_display(fallback_info)
            except Exception:
                pass
    
    def _refresh_model_info(self):
        """刷新模型信息"""
        self._load_model_info()
    
    def _update_model_display(self, info: dict):
        """更新模型显示"""
        c = get_current_colors()

        is_loaded = info.get('loaded', False)

        # ── 模型状态卡片 ──
        version = info.get('version', '—')
        model_type = "LightGBM" if info.get('type') == 'lightgbm' else "规则回退"
        updated = info.get('updated_at', '')[:10] if info.get('updated_at') else '—'
        feature_dim = info.get('n_features') or info.get('feature_count', '—')

        self._update_model_status(version, model_type,
                                  updated if is_loaded else "未训练",
                                  feature_dim)

        # ── 模型性能卡片 ──
        # 特征数: 有模型取 n_features，无模型取 feature_count (扩展30维)
        # 类别数: 固定3 (BUY/NONE/SELL)
        # 迭代次数: 仅训练后有
        # 模型大小: 仅训练后有
        n_features = info.get('n_features') or info.get('feature_count', 30)
        n_classes = info.get('n_classes', 3)
        best_iter = info.get('best_iteration', '—')
        model_size = f"{info.get('model_size_kb', 0):.1f}KB" if info.get('model_size_kb') else '—'

        perf_items = [
            ("特征数", str(n_features), "accuracy"),
            ("类别数", str(n_classes), "f1"),
            ("迭代次数", str(best_iter), "precision"),
            ("模型大小", model_size, "recall"),
        ]
        for title, value, key in perf_items:
            if key in self._metric_cards:
                color = c.green if is_loaded else c.text_secondary
                self._metric_cards[key].set_value(value, color)

        # ── 信号配置卡片 (读取真实配置) ──
        try:
            from core.config import get_config
            ml_cfg = get_config().ml
            conf = ml_cfg.confidence_threshold
            strong = ml_cfg.strong_threshold
        except Exception:
            conf, strong = 0.7, 0.85

        config_items = [
            ("置信度阈值", f"{conf:.0%}", "confidence"),
            ("强信号阈值", f"{strong:.0%}", "strong"),
            ("特征窗口", "20 根", "window"),
            ("状态", "已启用" if info.get('loaded') else "规则回退", "min_volume"),
        ]
        for title, value, key in config_items:
            if key in self._config_cards:
                color = c.green if info.get('loaded') else c.orange
                self._config_cards[key].set_value(value, color)

        self._model_info = info
        self.model_status_changed.emit("ready" if info.get('loaded') else "fallback", info)
    
    def _set_error_state(self, error: str):
        """设置错误状态"""
        c = get_current_colors()
        # 更新模型状态卡片显示错误
        self._status_cards["version"].set_value("—", c.red)
        self._status_cards["type"].set_value("加载失败", c.red)
        log.error("strategy", "模型加载失败", error)
    
    # ═══ 自动训练 ═══

    def on_positions_changed(self):
        """持仓变化时调用 — 防抖后自动触发增量训练"""
        if not self._auto_train_cfg.auto_train:
            return
        # 如果正在训练，标记为待执行，等当前训练完成后再触发
        if hasattr(self, '_training_thread') and self._training_thread and self._training_thread.is_alive():
            self._auto_train_pending = True
            log.info("strategy", "持仓变化，等待当前训练完成后自动增量训练")
            return
        # 重置防抖计时器
        self._auto_train_pending = True
        self._auto_train_timer.start()
        log.info("strategy", f"持仓变化，{self._auto_train_cfg.auto_train_delay}秒后自动增量训练")

    def _auto_train_trigger(self):
        """防抖到期，执行自动增量训练"""
        if not self._auto_train_pending:
            return
        self._auto_train_pending = False
        # 如果正在训练，不重复触发
        if hasattr(self, '_training_thread') and self._training_thread and self._training_thread.is_alive():
            return
        log.info("strategy", "自动增量训练触发")
        self._start_training(incremental=True)

    def _start_training(self, incremental: bool = False):
        """开始训练
        Args:
            incremental: True=增量模式(只补下载缺失数据), False=全量模式
        """
        self._incremental_mode = incremental
        self.training_started.emit()
        self._train_btn.setEnabled(False)
        mode_label = "增量训练中..." if incremental else "训练中..."
        self._train_btn.setText(mode_label)
        self._train_progress.setValue(0)
        self._pipeline.begin()
        self._desc_timer.start()

        # 启动训练线程
        self._training_thread = Thread(target=self._run_training, daemon=True)
        self._training_thread.start()
    
    def _run_training(self):
        """运行真实训练流水线 — 9步 + 3个分支（支持增量/全量模式）"""
        import time
        from PyQt5.QtCore import QMetaObject, Qt, Q_ARG

        _step = 0
        _total = len(_PIPELINE_STEPS)

        def _emit(method, *args):
            """线程安全地调用主线程槽函数"""
            if method == 'set':
                target, slot, conn = self, "_pipeline_set_step", Qt.QueuedConnection
                qargs = [Q_ARG(int, args[0]), Q_ARG(str, args[1] if len(args) > 1 else "")]
            elif method == 'done':
                target, slot, conn = self, "_pipeline_step_done", Qt.QueuedConnection
                qargs = [Q_ARG(int, args[0]), Q_ARG(str, args[1] if len(args) > 1 else "")]
            elif method == 'skip':
                target, slot, conn = self, "_pipeline_step_skip", Qt.QueuedConnection
                qargs = [Q_ARG(int, args[0]), Q_ARG(str, args[1] if len(args) > 1 else "")]
            elif method == 'error':
                target, slot, conn = self, "_pipeline_step_error", Qt.QueuedConnection
                qargs = [Q_ARG(int, args[0]), Q_ARG(str, args[1] if len(args) > 1 else "")]
            elif method == 'finish':
                target, slot, conn = self, "_pipeline_finish", Qt.QueuedConnection
                qargs = []
            else:
                return
            QMetaObject.invokeMethod(target, slot, conn, *qargs)

        try:
            # ═══════════════════════════════════════
            # Step 1: 数据采集
            # ═══════════════════════════════════════
            _step = 0
            from pathlib import Path
            from data_sources import DataRouter
            import pandas as pd

            # Step 1: 数据采集 — 优先使用持仓，其次自选股池
            router = DataRouter()

            # 优先读取持仓
            import json as _json
            positions_file = Path(__file__).resolve().parent.parent.parent / "data" / "positions.json"
            targets = []
            if positions_file.exists():
                try:
                    positions = _json.loads(positions_file.read_text("utf-8"))
                    targets = [(p["code"], p.get("name", ""), "") for p in positions if p.get("code")]
                except Exception:
                    pass

            # 持仓为空则读自选股池
            if not targets:
                try:
                    from data.watchlist import load_watchlist
                    wl = load_watchlist()
                    targets = [(w["code"], w.get("name", ""), w.get("type", "")) for w in wl if w.get("code")]
                except Exception:
                    pass

            # 都为空则报错
            if not targets:
                raise RuntimeError("持仓和自选股池均为空，请先在「持仓管理」页添加股票")

            _emit('set', _step, f"从数据源拉取持仓/自选股日K线({len(targets)}只)")

            data_dir = Path(__file__).resolve().parent.parent.parent / "data" / "klines"
            data_dir.mkdir(parents=True, exist_ok=True)

            downloaded = 0
            skipped = 0

            # 从配置读取K线条数
            from core.config import get_config
            kline_count = get_config().data.kline_count
            incremental = getattr(self, '_incremental_mode', False)

            for idx, (code, name, sector) in enumerate(targets):
                try:
                    # 增量模式：已有足够数据的跳过
                    if incremental:
                        csv_path = data_dir / f"{code}.csv"
                        if csv_path.exists():
                            try:
                                existing = pd.read_csv(csv_path)
                                if len(existing) >= kline_count * 0.8:
                                    skipped += 1
                                    continue
                            except Exception:
                                pass

                    _emit('set', _step, f"[{idx+1}/{len(targets)}] 下载 {code} {name}...")
                    klines = router.get_kline(code, period="day", count=kline_count)
                    if klines:
                        df = pd.DataFrame(klines)
                        df.to_csv(data_dir / f"{code}.csv", index=False)
                        downloaded += 1
                    time.sleep(0.3)
                except Exception:
                    continue

            if downloaded == 0 and skipped == 0:
                raise RuntimeError("未下载到任何K线数据，请检查网络连接")

            if incremental and skipped > 0:
                _emit('done', _step, f"增量: 新下载 {downloaded} 只, 跳过 {skipped} 只(已有数据)")
            else:
                _emit('done', _step, f"已下载 {downloaded}/{len(targets)} 只股票日K线")

            # ═══════════════════════════════════════
            # Step 2: 数据质检
            # ═══════════════════════════════════════
            _step = 1
            _emit('set', _step, f"校验{downloaded}只股票: 覆盖度/标签分布/极端行情")
            from strategies.data.features import validate_data_sufficiency
            quality_ok = 0
            csv_files = list(data_dir.glob("*.csv"))
            for qi, csv_path in enumerate(csv_files):
                try:
                    _emit('set', _step, f"[{qi+1}/{len(csv_files)}] 校验 {csv_path.stem}...")
                    df_q = pd.read_csv(csv_path)
                    val = validate_data_sufficiency(df_q, freq='daily')
                    if val['ok']:
                        quality_ok += 1
                except Exception:
                    pass
            _emit('done', _step, f"通过 {quality_ok}/{downloaded} 只")

            # ═══════════════════════════════════════
            # Step 3: 自动标注
            # ═══════════════════════════════════════
            _step = 2
            _emit('set', _step, "局部极值配对: 识别BUY/SELL信号(含回撤过滤)")
            from strategies.data.labeler import batch_label
            batch_label()
            # 统计标注结果
            labeled_dir = Path(__file__).resolve().parent.parent.parent / "data" / "labeled"
            labeled_count = len(list(labeled_dir.glob("*_labeled.csv"))) if labeled_dir.exists() else 0
            _emit('done', _step, f"标注完成 {labeled_count} 个文件")

            # ═══════════════════════════════════════
            # Step 4: 构建样本
            # ═══════════════════════════════════════
            _step = 3
            _emit('set', _step, "34维特征计算 + 20根滑窗展平 + 5%极值截断")
            # _prepare_dataset 在 train_model 内部调用，这里提前确认数据可读
            from strategies.ml.trainer import _prepare_dataset
            X_preview, y_preview, _ = _prepare_dataset(
                lookback=20, use_extended_features=True, winsorize=True
            )
            if X_preview is None:
                raise RuntimeError("构建训练集失败: 无有效样本")
            sample_info = f"{len(X_preview)} 样本, {X_preview.shape[1]} 维"
            _emit('done', _step, sample_info)

            # ═══════════════════════════════════════
            # Step 5: 特征筛选 [分支: enable_feature_selection]
            # ═══════════════════════════════════════
            _step = 4
            _enable_feature_selection = True  # ← 从配置读取
            if _enable_feature_selection:
                _emit('set', _step, "IC相关性 + LGBM重要性 双重筛选, 34维→Top20")
            else:
                _emit('skip', _step, "未启用特征筛选, 使用全部34维")

            # ═══════════════════════════════════════
            # Step 6: 数据划分 [分支: time_series_split]
            # ═══════════════════════════════════════
            _step = 5
            if _step > 4 and _enable_feature_selection:
                _emit('done', 4, "IC+LGBM 筛选完成")
            _time_series_split = True  # ← 从配置读取
            if _time_series_split:
                _emit('set', _step, "时序划分: 前80%训练 + 后20%验证(防未来泄漏)")
            else:
                _emit('set', _step, "随机分层划分 80/20(仅实验用,不推荐实盘)")
            _emit('done', _step, "80/20 时序划分完成")

            # ═══════════════════════════════════════
            # Step 6.5: 数据量校验 (训练前预检)
            # ═══════════════════════════════════════
            from utils.data_fetcher import check_training_data_ready
            data_check = check_training_data_ready()
            if not data_check["ready"]:
                _emit('set', 6, f"⚠️ {data_check['message']}")
                log.warning("strategy", "训练数据不足", data_check['message'])
                # 仍然继续训练，但给用户警告

            # ═══════════════════════════════════════
            # Step 7: 模型训练
            # ═══════════════════════════════════════
            _step = 6
            _emit('set', _step, f"LightGBM多分类 + balanced权重 + early_stop(20)")
            from strategies.ml.trainer import train_model
            model_path, train_metrics = train_model(
                enable_feature_selection=_enable_feature_selection,
                time_series_split=_time_series_split,
            )
            if not model_path:
                raise RuntimeError("模型训练失败")
            f1 = train_metrics.get('val_f1', 0)
            n_train = train_metrics.get('n_train', 0)
            n_val = train_metrics.get('n_val', 0)
            n_feat = train_metrics.get('n_features', 0)
            _emit('done', _step, f"F1={f1:.3f} | {n_train}训练/{n_val}验证 | {n_feat}维")

            # ═══════════════════════════════════════
            # Step 8: 模型评估 [分支: walk_forward]
            # ═══════════════════════════════════════
            _step = 7
            wf = train_metrics.get('walk_forward', {})
            if wf.get('enabled'):
                wf_mean = wf.get('f1_mean', 0)
                wf_std = wf.get('f1_std', 0)
                _emit('set', _step, f"Walk-forward 5折滚动验证, F1={f1:.3f}±{wf_std:.3f}")
            else:
                _emit('set', _step, f"F1={f1:.3f}, 分类报告: 精度/召回/支持")
            wf_info = ""
            if wf.get('enabled'):
                wf_info = f"WF-F1={wf.get('f1_mean', 0):.3f}±{wf.get('f1_std', 0):.3f}"
            _emit('done', _step, wf_info or f"F1={f1:.3f}")

            # ═══════════════════════════════════════
            # Step 9: 保存部署
            # ═══════════════════════════════════════
            _step = 8
            _emit('set', _step, f"joblib压缩打包 → reload_model() 热加载")
            from strategies.ml.model import reload_model
            reload_model()
            time.sleep(0.2)
            _emit('done', _step, f"→ {os.path.basename(model_path)}")

            # ═══════════════════════════════════════
            # 全部完成
            # ═══════════════════════════════════════
            _emit('finish')

        except Exception as e:
            import traceback
            traceback.print_exc()
            _emit('error', _step, str(e))
    
    @pyqtSlot(int, str)
    def _pipeline_set_step(self, step_idx: int, log_text: str):
        """推进到某步骤 (主线程)"""
        self._pipeline.set_step(step_idx, log_text)
        self._train_progress.setValue(int((step_idx) / len(_PIPELINE_STEPS) * 100))

    @pyqtSlot(int, str)
    def _pipeline_step_done(self, step_idx: int, result: str = ""):
        """某步骤完成 (主线程)"""
        self._pipeline.step_done(step_idx, result)
        self._train_progress.setValue(int((step_idx + 1) / len(_PIPELINE_STEPS) * 100))

    @pyqtSlot(int, str)
    def _pipeline_step_skip(self, step_idx: int, reason: str):
        """某步骤跳过 (主线程)"""
        self._pipeline.step_skip(step_idx, reason)
        self._train_progress.setValue(int((step_idx + 1) / len(_PIPELINE_STEPS) * 100))

    @pyqtSlot(int, str)
    def _pipeline_step_error(self, step_idx: int, msg: str):
        """某步骤失败 (主线程)"""
        self._pipeline.step_error(step_idx, msg)
        self._train_btn.setEnabled(True)
        self._train_btn.setText("▶ 重新训练")
        self._desc_timer.stop()
        log.signal_log("strategy", f"训练 Step {step_idx+1} 失败", msg)

        # 自动训练：失败后也检查是否有待处理的持仓变化
        if self._auto_train_pending and self._auto_train_cfg.auto_train:
            log.info("strategy", "训练失败，但有持仓变化待处理，30秒后重试")
            QTimer.singleShot(30000, self._auto_train_trigger)

    @pyqtSlot()
    def _pipeline_finish(self):
        """全部完成 (主线程)"""
        self._pipeline.finish()
        self._train_btn.setEnabled(True)
        self._train_btn.setText("▶ 重新训练")
        self._train_progress.setValue(100)
        self._desc_timer.stop()
        self._pipeline_desc.setText("🎉 全部完成 (9/9)")

        QTimer.singleShot(500, self._refresh_model_info)
        self.training_completed.emit(self._model_info or {})
        log.signal_log("strategy", "模型训练完成", "")

        # 自动训练：训练完成后检查是否有新的持仓变化待处理
        if self._auto_train_pending and self._auto_train_cfg.auto_train:
            log.info("strategy", "训练期间有持仓变化，10秒后再次增量训练")
            QTimer.singleShot(10000, self._auto_train_trigger)

    # ═══ 自动参数优化 ═══

    def _start_optimize(self):
        """启动自动参数优化"""
        self._optimize_btn.setEnabled(False)
        self._optimize_btn.setText("优化中...")
        self._optimize_progress.setValue(0)
        self._pipeline_desc.setText("🔧 准备中")
        # 在主线程读取网格档位
        self._grid_preset_idx = self._grid_preset_combo.currentIndex()
        preset_names = ["快速", "标准", "全面"]
        preset_name = preset_names[self._grid_preset_idx] if 0 <= self._grid_preset_idx < len(preset_names) else "标准"
        log.signal_log("strategy", "参数优化启动", f"GridSearch 网格搜索 ({preset_name})")

        self._optimize_thread = Thread(target=self._run_optimize, daemon=True)
        self._optimize_thread.start()

    def _run_optimize(self):
        """后台运行参数优化 — 多维度参数网格搜索 + 自动应用结果"""
        from PyQt5.QtCore import QMetaObject, Q_ARG

        def _update_progress(val, text=""):
            QMetaObject.invokeMethod(
                self._optimize_progress, "setValue",
                Qt.QueuedConnection, Q_ARG(int, val)
            )
            if text:
                QMetaObject.invokeMethod(
                    self._pipeline_desc, "setText",
                    Qt.QueuedConnection, Q_ARG(str, text)
                )

        def _finish(success, msg):
            QMetaObject.invokeMethod(
                self, "_optimize_finish",
                Qt.QueuedConnection,
                Q_ARG(bool, success),
                Q_ARG(str, msg)
            )

        try:
            from strategies.optimization.param_optimizer import GridSearchOptimizer
            from strategies.backtest_engine_v2 import EnhancedBacktestEngine
            from data_sources.router import DataRouter

            router = DataRouter()

            # 用 ETF 池中的标的做优化
            from data.watchlist import load_watchlist
            watchlist = load_watchlist()
            if watchlist:
                test_codes = [w["code"] for w in watchlist[:3]]
            else:
                # 自选池为空，用持仓代替
                from data.cache_manager import get_cache_manager
                tracked = get_cache_manager().get_tracked_codes()
                test_codes = tracked[:3] if tracked else []
                if not test_codes:
                    # 再从 positions.json 读取
                    import json as _json
                    from pathlib import Path as _Path
                    pos_file = _Path(__file__).resolve().parent.parent.parent / "data" / "positions.json"
                    if pos_file.exists():
                        try:
                            positions = _json.loads(pos_file.read_text("utf-8"))
                            test_codes = [p["code"] for p in positions if p.get("code")][:3]
                        except Exception:
                            pass
                if not test_codes:
                    _finish(False, "自选池和持仓均为空，无法优化。请先添加持仓或导入 ETF")
                    return

            # ═══ 读取用户选择的网格档位 ═══
            _preset_idx = getattr(self, '_grid_preset_idx', 1)

            if _preset_idx == 0:
                # ⚡ 快速: 2×2×1×1 = 4 组，低门槛确保有交易信号
                param_grid = {
                    'min_signal_confidence':     [40, 60],      # 降低门槛让模型有信号
                    'initial_stop_pct':          [0.02, 0.04],
                    'tp_activate_pct':           [0.03],
                    'exit_cooldown_bars':        [5],
                }
            elif _preset_idx == 2:
                # 🔍 全面: 3×2×2×2 = 24 组（原81组太慢）
                param_grid = {
                    'min_signal_confidence':     [35, 50, 65],
                    'initial_stop_pct':          [0.02, 0.05],
                    'tp_activate_pct':           [0.02, 0.04],
                    'exit_cooldown_bars':        [3, 8],
                }
            else:
                # 📐 标准: 3×2×1×2 = 12 组（原27组）
                param_grid = {
                    'min_signal_confidence':     [35, 50, 65],  # 35%起步确保有信号
                    'initial_stop_pct':          [0.02, 0.05],
                    'tp_activate_pct':           [0.03],
                    'exit_cooldown_bars':        [3, 8],
                }

            engine = EnhancedBacktestEngine()
            optimizer = GridSearchOptimizer(engine, metric='sharpe_ratio')

            total_codes = len(test_codes)
            combos = 1
            for v in param_grid.values():
                combos *= len(v)
            total_evals = total_codes * combos
            eval_count = 0
            all_results = []

            def _on_progress(current, total, msg):
                nonlocal eval_count
                overall = min(95, int(((eval_count + current) / total_evals) * 90))
                friendly = f"🔧 正在评估参数组合 {eval_count + current + 1} / {total_evals}"
                _update_progress(overall, friendly)

            for i, code in enumerate(test_codes):
                _update_progress(int((i / total_codes) * 20), f"[{i+1}/{total_codes}] 正在获取 {code} 数据")

                # 获取回测数据：分钟优先，不足则用日K线兜底
                data = router.get_minute_for_backtest(code)
                if not data or len(data) < 30:
                    data = router.get_minute(code)
                data_source = "分钟"
                if not data or len(data) < 30:
                    # 分钟数据不足（刚开盘等），用日K线兜底
                    from pathlib import Path as _Path
                    kline_path = _Path(__file__).resolve().parent.parent.parent / "data" / "klines" / f"{code}.csv"
                    if kline_path.exists():
                        import pandas as _pd
                        df_k = _pd.read_csv(kline_path)
                        if len(df_k) >= 30:
                            data = df_k.to_dict('records')
                            data_source = "日K"
                            log.info("strategy", f"{code} 分钟数据不足，使用日K线回测 ({len(data)}条)")
                if data and len(data) >= 30:
                    # ═══ 预计算特征（只算一次，所有参数组合复用）═══
                    _update_progress(int((i / total_codes) * 20 + 5),
                                     f"[{i+1}/{total_codes}] {code} 预计算特征工程...")
                    try:
                        from strategies.backtest_engine_v2 import BacktestConfig
                        from strategies.data.features import calculate_features
                        _cfg = BacktestConfig()
                        _engine_for_prep = EnhancedBacktestEngine(config=_cfg)
                        precomputed_df = _engine_for_prep._prepare_data(data)
                        if precomputed_df is not None and len(precomputed_df) >= 20:
                            precomputed_df = calculate_features(precomputed_df)
                            optimizer.set_precomputed_df(precomputed_df)
                        else:
                            optimizer.set_precomputed_df(None)
                    except Exception as e:
                        log.warning("strategy", f"预计算特征失败: {e}，降级为每次重算")
                        optimizer.set_precomputed_df(None)

                    _update_progress(int((i / total_codes) * 20 + 10),
                                     f"[{i+1}/{total_codes}] {code} 回测 {combos} 组参数...")
                    try:
                        result = optimizer.optimize(param_grid, data, code, code,
                                                    progress_callback=_on_progress)
                        eval_count += combos
                        all_results.append({
                            'code': code,
                            'best_params': result.best_params,
                            'best_score': round(result.best_score, 4),
                        })
                    except Exception as e:
                        eval_count += combos
                        log.warning("strategy", f"优化 {code} 失败: {e}")
                else:
                    log.warning("strategy", f"优化跳过 {code}: 数据不足 ({len(data) if data else 0}条)，请稍后再试")

            _update_progress(95, "汇总结果并应用最优参数")

            if all_results:
                from collections import Counter
                param_votes = Counter()
                for r in all_results:
                    bp = r['best_params']
                    if bp is not None:
                        param_votes[str(bp)] += 1

                if not param_votes:
                    _finish(False, "优化失败: 所有参数组合评估均未通过，请检查数据或模型状态")
                    return

                best_combo = param_votes.most_common(1)[0][0]
                best_results = [r for r in all_results if r['best_params'] is not None and str(r['best_params']) == best_combo]
                if not best_results:
                    _finish(False, "优化失败: 无法确定最优参数")
                    return
                avg_score = sum(r['best_score'] for r in best_results) / len(best_results)
                best_params = best_results[0]['best_params']

                # 将最优参数自动应用到UI
                self._best_params_found = best_params
                QMetaObject.invokeMethod(
                    self, "_apply_optimized_params",
                    Qt.QueuedConnection,
                )

                summary = (
                    f"置信度{best_params.get('min_signal_confidence', '-')}% | "
                    f"止损{best_params.get('initial_stop_pct', '-')*100:.0f}% | "
                    f"止盈{best_params.get('tp_activate_pct', '-')*100:.0f}% | "
                    f"夏普{avg_score:.3f}"
                )
                _finish(True, summary)
            else:
                _finish(False, "优化失败: 所有标的均无可回测数据，请检查网络连接")

        except Exception as e:
            import traceback
            traceback.print_exc()
            _finish(False, f"优化异常: {str(e)[:80]}")

    @pyqtSlot()
    def _apply_optimized_params(self):
        """将优化后的最优参数自动应用到UI (主线程)"""
        params = getattr(self, '_best_params_found', None)
        if not params:
            return
        try:
            if 'min_signal_confidence' in params:
                self._param_confidence.setValue(params['min_signal_confidence'] / 100.0)
            if 'initial_stop_pct' in params:
                self._param_initial_stop.setValue(params['initial_stop_pct'])
            if 'tp_activate_pct' in params:
                self._param_rule_sell_win.setValue(params['tp_activate_pct'] * 100)
            log.signal_log("strategy", "最优参数已自动应用到面板", "请点击「保存参数」持久化到配置")
        except Exception as e:
            log.warning("strategy", f"应用优化参数到UI失败: {e}")

    @pyqtSlot(bool, str)
    def _optimize_finish(self, success: bool, msg: str):
        """优化完成回调 (主线程) — 成功/失败状态直观反馈"""
        self._optimize_btn.setEnabled(True)
        self._optimize_progress.setValue(100 if success else 0)
        self._pipeline_desc.setText("")

        if success:
            self._optimize_btn.setText("✅ 已优化")
            log.signal_log("strategy", "参数优化完成", msg)
            # 5秒后恢复为可再次优化的状态
            QTimer.singleShot(5000, lambda: self._optimize_btn.setText("🔧 自动优化"))
        else:
            self._optimize_btn.setText("❌ 优化失败")
            log.warning("strategy", msg)
            # 5秒后恢复
            QTimer.singleShot(5000, lambda: self._optimize_btn.setText("🔧 自动优化"))

    def _on_theme_changed(self, _colors):
        """主题变化回调 - 只刷新模型信息显示，不重建UI"""
        self._load_model_info()


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys
    
    app = QApplication(sys.argv)
    
    panel = StrategyPanel()
    panel.setFixedSize(800, 600)
    panel.show()
    
    sys.exit(app.exec_())
