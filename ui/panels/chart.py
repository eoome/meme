#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
行情走势页 - 搜索框 + 周期切换 + ECharts K线/分时图 (增量刷新)
"""

import os
import json
import base64
from threading import Thread

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QLineEdit, QPushButton, QFrame)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QMetaObject, Q_ARG
from PyQt5.QtGui import QFont
from PyQt5.QtWebEngineWidgets import QWebEngineView

from data_sources import DataRouter
from core.logger import log
from ui.theme import ThemeManager
from utils.numeric import clean_num

# ── ECharts 本地加载（不依赖 CDN）──
_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets")
_ECHARTS_TAG_CACHE = None


def _echarts_script_tag():
    """ECharts <script> 标签：本地 base64 内联，零网络依赖，同步加载"""
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
            + '"><' + chr(92) + '/script>\');</script>'
        )
    else:
        _ECHARTS_TAG_CACHE = '<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>'
    return _ECHARTS_TAG_CACHE


class ChartPanel(QFrame):
    """行情走势 - 搜索框 + 周期切换 + ECharts K线/分时图 (增量刷新)"""
    _render_ready = pyqtSignal(str)
    _start_live_requested = pyqtSignal()  # 后台线程 → 主线程: 请求启动实时定时器

    # 分时增量刷新间隔 ms (腾讯建议>=3s，压到2.8s边缘)
    MINUTE_REFRESH_MS = 2800

    def __init__(self):
        """初始化"""
        super().__init__()
        self._current_code = ""
        self._current_name = ""
        self._current_period = "day"
        self._router: DataRouter = None
        self._bridge = None
        self._chanlun_extractor = None  # 缓存缠论提取器，支持增量计算
        self._chanlun_kline_hash = None  # 缓存对应的K线数据哈希
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._live_tick)
        # 缠论K线刷新定时器 (缠论开启时每60秒重新拉取K线+重算缠论)
        self._chanlun_timer = QTimer(self)
        self._chanlun_timer.timeout.connect(self._chanlun_refresh_tick)
        self._start_live_requested.connect(self._schedule_minute_live)
        self.init_ui()

    # ── 工具 ──

    @staticmethod
    def _chart_colors():
        c = ThemeManager.get_colors()
        return {
            "bg": c.bg_surface, "title": c.text_primary,
            "axis_label": c.text_secondary, "grid_line": c.divider,
            "axis_line": c.border, "tip_bg": c.bg_card,
            "tip_border": c.border_strong, "tip_text": c.text_primary,
            "slider_border": c.border_strong, "slider_fill": c.accent + "15",
            "slider_handle": c.accent, "slider_text": c.text_secondary,
            "vol_up": "rgba(239,83,80,0.65)", "vol_down": "rgba(38,166,154,0.65)",
            "area_start": "rgba(26,115,232,0.2)", "area_mid": "rgba(26,115,232,0.05)",
            "area_end": "rgba(26,115,232,0)",
        }

    def set_router(self, router: DataRouter):
        """设置数据路由器实例"""
        self._router = router

    def _stop_live(self):
        self._live_timer.stop()
        self._chanlun_timer.stop()

    def _start_minute_live(self):
        """启动分时增量刷新定时器"""
        self._stop_live()
        self._live_timer.start(self.MINUTE_REFRESH_MS)

    def _schedule_minute_live(self):
        """主线程槽：延迟启动分时增量刷新定时器 (从后台线程安全调用)"""
        QTimer.singleShot(600, self._start_minute_live)

    # ── UI ──

    def init_ui(self):
        """初始化UI布局"""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 8)
        top_bar.setSpacing(12)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("\u8f93\u5165\u80a1\u7968\u4ee3\u7801\u6216\u540d\u79f0\uff0c\u5982 600519 / \u8305\u53f0\uff0c\u6309\u56de\u8f66\u67e5\u8be2")
        self.search_input.setFixedHeight(42)
        self.search_input.setFont(QFont("Microsoft YaHei", 13))
        self.search_input.setStyleSheet("""
            QLineEdit {
                border: 2px solid #e0e0e0; border-radius: 8px;
                padding: 0 14px; background: white; font-size: 13px;
            }
            QLineEdit:focus { border-color: #1a73e8; }
        """)
        self.search_input.returnPressed.connect(self._lookup_stock)
        top_bar.addWidget(self.search_input, 1)

        search_btn = QPushButton("\u67e5\u8be2")
        search_btn.setFixedSize(60, 42)
        search_btn.setCursor(Qt.PointingHandCursor)
        search_btn.setStyleSheet("""
            QPushButton {
                background-color: #1a73e8; color: white; border: none;
                border-radius: 8px; font-size: 13px; font-family: "Microsoft YaHei";
            }
            QPushButton:hover { background-color: #1557b0; }
        """)
        search_btn.clicked.connect(self._lookup_stock)
        top_bar.addWidget(search_btn)

        self._period_btns = []
        for text, key in [("\u5206\u65f6", "time"), ("\u65e5K", "day"), ("\u5468K", "week"), ("\u6708K", "month")]:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedSize(52, 34)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent; color: #999; border: 1px solid #ddd;
                    border-radius: 6px; font-size: 12px; font-family: "Microsoft YaHei";
                }
                QPushButton:hover { color: #333; border-color: #bbb; }
                QPushButton:checked {
                    background: #1a73e8; color: white; border-color: #1a73e8;
                }
            """)
            btn.clicked.connect(lambda checked, k=key: self._switch_period(k))
            top_bar.addWidget(btn)
            self._period_btns.append((btn, key))

        # ── 缠论叠加开关 ──
        self._chanlun_btn = QPushButton("\U0001f4d0 \u7f20\u8bba")
        self._chanlun_btn.setCheckable(True)
        self._chanlun_btn.setFixedSize(68, 34)
        self._chanlun_btn.setCursor(Qt.PointingHandCursor)
        self._chanlun_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #999; border: 1px solid #ddd;
                border-radius: 6px; font-size: 12px; font-family: "Microsoft YaHei";
            }
            QPushButton:hover { color: #333; border-color: #bbb; }
            QPushButton:checked {
                background: #e67e22; color: white; border-color: #e67e22;
            }
        """)
        self._chanlun_btn.clicked.connect(self._on_chanlun_toggle)
        top_bar.addWidget(self._chanlun_btn)

        # ── 多级别联立按钮 ──
        self._multi_tf_btn = QPushButton("\u26d4 \u591a\u7ea7\u522b")
        self._multi_tf_btn.setCheckable(True)
        self._multi_tf_btn.setFixedSize(68, 34)
        self._multi_tf_btn.setCursor(Qt.PointingHandCursor)
        self._multi_tf_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #999; border: 1px solid #ddd;
                border-radius: 6px; font-size: 12px; font-family: "Microsoft YaHei";
            }
            QPushButton:hover { color: #333; border-color: #bbb; }
            QPushButton:checked {
                background: #8e44ad; color: white; border-color: #8e44ad;
            }
        """)
        self._multi_tf_btn.clicked.connect(self._on_multi_tf_toggle)
        top_bar.addWidget(self._multi_tf_btn)

        layout.addLayout(top_bar)

        self.chart_view = QWebEngineView()
        self.chart_view.setMinimumHeight(400)
        layout.addWidget(self.chart_view, 1)

        self._render_ready.connect(self._do_render)
        self.setLayout(layout)
        self.setStyleSheet("background-color: white;")
        self._show_placeholder()

    @pyqtSlot(str)
    def _do_render(self, html):
        self.chart_view.setHtml(html)

    def _lookup_stock(self):
        text = self.search_input.text().strip()
        if not text:
            return

        # 从输入中提取搜索关键词：
        # 支持格式: "518800" / "518800 黄金ETF国泰" / "黄金ETF国泰"
        import re
        code_match = re.search(r'\b(\d{6})\b', text)
        if code_match:
            # 输入包含6位代码，优先用代码搜索（腾讯smartbox不支持代码+名称混合查询）
            search_key = code_match.group(1)
        else:
            # 纯名称搜索
            search_key = text

        if self._router:
            results = self._router.search(search_key)
            if results:
                code, name, _ = results[0]
                self._on_stock_selected(code, name, "")
                return
        log.warning("data", f"\u672a\u627e\u5230\u80a1\u7968: {text}", "\u8bf7\u68c0\u67e5\u4ee3\u7801\u6216\u540d\u79f0\u662f\u5426\u6b63\u786e")

    def _on_stock_selected(self, code, name, market):
        self._current_code = code
        self._current_name = name
        self._chanlun_extractor = None  # 切换股票时清除缠论缓存
        self._chanlun_kline_hash = None
        self.load_stock(code, name)

    def _switch_period(self, period):
        self._current_period = period
        for btn, key in self._period_btns:
            btn.setChecked(key == period)
        if self._current_code:
            self.load_stock(self._current_code, self._current_name)

    def _on_chanlun_toggle(self):
        """缠论叠加开关切换"""
        if self._chanlun_btn.isChecked() and self._current_code and self._current_period != "time":
            # 开启缠论 → 启动K线定时刷新(每60秒重算缠论+推演)
            self._chanlun_timer.start(60000)
            self.load_stock(self._current_code, self._current_name)
        else:
            # 关闭缠论 → 停止刷新
            self._chanlun_timer.stop()
            if self._current_code:
                self.load_stock(self._current_code, self._current_name)

    def _on_multi_tf_toggle(self):
        """多级别联立开关切换"""
        if self._multi_tf_btn.isChecked():
            self._multi_tf_btn.setText("\u26d4 \u591a\u7ea7\u522b ON")
            # 自动开启缠论
            if not self._chanlun_btn.isChecked():
                self._chanlun_btn.setChecked(True)
        else:
            self._multi_tf_btn.setText("\u26d4 \u591a\u7ea7\u522b")
        if self._current_code:
            self.load_stock(self._current_code, self._current_name)

    def _compute_chanlun_overlay(self, klines, max_visible_bars=120):
        """
        缠论叠加: 全部历史结构统一显示 + 推演层预测未来

        - 历史+当前: 所有笔/中枢/线段统一风格高亮显示，不区分新旧
        - 推演层: 基于当前走势推算未来可能的笔/中枢/支撑阻力，用虚线/半透明表示
        - 多级别联立: 当开启多级别时，同时叠加低级别笔和中枢
        """
        try:
            import pandas as pd
            import numpy as np
            from strategies.data.chanlun import ChanLunFeatureExtractor

            clean_klines = []
            for k in klines:
                if not isinstance(k, dict):
                    continue
                ck = dict(k)
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in ck:
                        ck[col] = clean_num(ck[col], 0.0)
                clean_klines.append(ck)

            n_total = len(clean_klines)
            if n_total < 30:
                return None

            df = pd.DataFrame(clean_klines)
            df.columns = [c.lower().strip() for c in df.columns]
            if 'close' not in df.columns:
                return None
            for col in ['open', 'high', 'low']:
                if col not in df.columns:
                    df[col] = df['close']
            if 'volume' not in df.columns:
                df['volume'] = 0

            # 增量计算: 如果有缓存的 extractor 且K线数量增加了，用增量更新
            import hashlib
            kline_hash = hashlib.md5(str(n_total).encode() + str(df['close'].iloc[-1]).encode()).hexdigest()

            if (self._chanlun_extractor is not None and
                self._chanlun_kline_hash is not None and
                n_total > len(self._chanlun_extractor.df) and
                n_total - len(self._chanlun_extractor.df) < 10):
                # 增量路径: 新增K线 < 10 根
                cl = self._chanlun_extractor
                cl.compute_incremental(df)
            else:
                # 全量重算
                cl = ChanLunFeatureExtractor(df)
                cl.compute()

            # 缓存 extractor
            self._chanlun_extractor = cl
            self._chanlun_kline_hash = kline_hash

            all_dates = [k.get('date', k.get('time', '')) for k in clean_klines]
            current_price = float(df['close'].iloc[-1])
            cutoff_idx = max(0, n_total - max_visible_bars)

            overlay = {
                'strokes': [],
                'hubs': [],
                'segments': [],
                'buy_signals': [],
                'sell_signals': [],
                'projection': None,
                'n_total': n_total,
                'multi_tf': None,  # 多级别叠加数据
            }

            # ── 全部笔（历史+当前统一风格）──
            for s in cl.strokes:
                if s.end_idx < cutoff_idx:
                    continue
                start_date = all_dates[s.start_idx] if s.start_idx < len(all_dates) else ''
                end_date = all_dates[s.end_idx] if s.end_idx < len(all_dates) else ''
                if not start_date or not end_date:
                    continue
                if s.direction == 'up':
                    start_p = min(s.start_price, s.end_price)
                    end_p = max(s.start_price, s.end_price)
                else:
                    start_p = max(s.start_price, s.end_price)
                    end_p = min(s.start_price, s.end_price)
                overlay['strokes'].append({
                    'data': [
                        {'xAxis': start_date, 'yAxis': round(float(start_p), 3)},
                        {'xAxis': end_date, 'yAxis': round(float(end_p), 3)},
                    ],
                    'direction': s.direction,
                })

            # ── 全部中枢 ──
            for h in cl.hubs:
                if h.end_idx < cutoff_idx:
                    continue
                start_date = all_dates[h.start_idx] if h.start_idx < len(all_dates) else ''
                end_date = all_dates[h.end_idx] if h.end_idx < len(all_dates) else ''
                if not start_date or not end_date:
                    continue
                overlay['hubs'].append({
                    'top': round(float(h.top), 3),
                    'bottom': round(float(h.bottom), 3),
                    'start_date': start_date,
                    'end_date': end_date,
                    'center': round(float(h.center), 3),
                })

            # ── 全部线段 ──
            for seg in cl.segments:
                if seg.end_idx < cutoff_idx:
                    continue
                start_date = all_dates[seg.start_idx] if seg.start_idx < len(all_dates) else ''
                end_date = all_dates[seg.end_idx] if seg.end_idx < len(all_dates) else ''
                if not start_date or not end_date:
                    continue
                overlay['segments'].append({
                    'data': [
                        {'xAxis': start_date, 'yAxis': round(float(seg.start_price), 3)},
                        {'xAxis': end_date, 'yAxis': round(float(seg.end_price), 3)},
                    ],
                    'direction': seg.direction,
                })

            # ── 买卖点（全部可视范围内）──
            sig = cl.get_signal()
            visible_fractals = [f for f in cl.fractals if f.idx >= cutoff_idx]
            if sig.get('buy_point', 0) > 0:
                bottom_fractals = [f for f in visible_fractals if f.type == 'bottom']
                if bottom_fractals:
                    bf = bottom_fractals[-1]
                    if bf.idx < len(all_dates):
                        overlay['buy_signals'].append({
                            'date': all_dates[bf.idx],
                            'price': round(float(bf.low), 3),
                            'level': sig['buy_point'],
                            'reason': sig['reason'][:30],
                        })
            if sig.get('sell_point', 0) > 0:
                top_fractals = [f for f in visible_fractals if f.type == 'top']
                if top_fractals:
                    tf = top_fractals[-1]
                    if tf.idx < len(all_dates):
                        overlay['sell_signals'].append({
                            'date': all_dates[tf.idx],
                            'price': round(float(tf.high), 3),
                            'level': sig['sell_point'],
                            'reason': sig['reason'][:30],
                        })

            # ═══ 推演层: 使用增强版推演（中枢对齐 + 趋势强弱 + 成交量加权） ═══
            if cl.strokes and len(cl.strokes) >= 3:
                proj = cl.compute_projection()
                if proj:
                    proj['last_date'] = all_dates[-1] if all_dates else ''
                    overlay['projection'] = proj

            # ═══ 多级别联立 ═══
            show_multi_tf = self._multi_tf_btn.isChecked() and self._current_period != "time"
            if show_multi_tf and self._router and self._current_code:
                overlay['multi_tf'] = self._fetch_multi_tf_overlay(cl, sig, df)

            return overlay

        except Exception as e:
            from core.logger import log
            log.debug("chart", f"缠论叠加计算失败: {e}")
            return None

    def _fetch_multi_tf_overlay(self, higher_cl, higher_sig, higher_df):
        """
        多级别联立: 拉取低级别数据并叠加

        获取分钟级别的缠论结构，与高级别（日线）共振。

        Args:
            higher_cl: 高级别 ChanLunFeatureExtractor
            higher_sig: 高级别信号
            higher_df: 高级别 DataFrame

        Returns:
            低级别叠加数据字典，或 None
        """
        try:
            from strategies.data.chanlun import ChanLunFeatureExtractor, get_multi_timeframe_signal

            # 拉取分钟级别数据（默认30分钟）
            code = self._current_code
            minute_klines = self._router.get_kline(code, "30min", 300)
            if not minute_klines:
                return None

            import pandas as pd
            import numpy as np

            # 清洗分钟数据
            clean_min = []
            for k in minute_klines:
                if not isinstance(k, dict):
                    continue
                ck = dict(k)
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in ck:
                        try:
                            ck[col] = float(ck[col]) if ck[col] is not None else 0.0
                        except (ValueError, TypeError):
                            ck[col] = 0.0
                clean_min.append(ck)

            if len(clean_min) < 30:
                return None

            df_lower = pd.DataFrame(clean_min)
            df_lower.columns = [c.lower().strip() for c in df_lower.columns]

            # 计算多级别共振信号
            multi_sig = get_multi_timeframe_signal(higher_df, df_lower)

            # 计算低级别缠论结构
            cl_lower = ChanLunFeatureExtractor(df_lower)
            cl_lower.compute()

            lower_overlay = {
                'strokes': [],
                'hubs': [],
                'signal': multi_sig,
            }

            # 低级别笔（最近30根）
            min_dates = [k.get('date', k.get('time', '')) for k in clean_min]
            for s in cl_lower.strokes[-30:]:
                start_date = min_dates[s.start_idx] if s.start_idx < len(min_dates) else ''
                end_date = min_dates[s.end_idx] if s.end_idx < len(min_dates) else ''
                if not start_date or not end_date:
                    continue
                if s.direction == 'up':
                    start_p = min(s.start_price, s.end_price)
                    end_p = max(s.start_price, s.end_price)
                else:
                    start_p = max(s.start_price, s.end_price)
                    end_p = min(s.start_price, s.end_price)
                lower_overlay['strokes'].append({
                    'data': [
                        {'xAxis': start_date, 'yAxis': round(float(start_p), 3)},
                        {'xAxis': end_date, 'yAxis': round(float(end_p), 3)},
                    ],
                    'direction': s.direction,
                })

            # 低级别中枢
            for h in cl_lower.hubs[-10:]:
                start_date = min_dates[h.start_idx] if h.start_idx < len(min_dates) else ''
                end_date = min_dates[h.end_idx] if h.end_idx < len(min_dates) else ''
                if not start_date or not end_date:
                    continue
                lower_overlay['hubs'].append({
                    'top': round(float(h.top), 3),
                    'bottom': round(float(h.bottom), 3),
                    'start_date': start_date,
                    'end_date': end_date,
                })

            return lower_overlay

        except Exception as e:
            from core.logger import log
            log.debug("chart", f"多级别联立计算失败: {e}")
            return None

    # 注: _compute_projection 已迁移到 ChanLunFeatureExtractor.compute_projection()
    # 增强版推演包含: 中枢对齐、趋势强弱、成交量加权、置信度输出

    def _placeholder_html(self):
        cc = self._chart_colors()
        c_bg = cc.get("bg", "#fff")
        return f"""
        <!DOCTYPE html><html><head>
        <meta charset="utf-8">
        {_echarts_script_tag()}
        <style>body{{margin:0;overflow:hidden;background:{c_bg};}}</style>
        </head><body>
        <div id="main" style="width:100%;height:100vh;"></div>
        <script>
            var chart = echarts.init(document.getElementById('main'));
            chart.setOption({{
                title: {{ text: '\\u8bf7\\u641c\\u7d22\\u80a1\\u7968\\u67e5\\u770b\\u884c\\u60c5',
                         left: 'center', top: 'center',
                         textStyle: {{ color: '#ccc', fontSize: 16, fontWeight: 'normal' }} }}
            }});
            window.addEventListener('resize', function(){{ chart.resize(); }});
        </script></body></html>
        """

    def _show_placeholder(self):
        self.chart_view.setHtml(self._placeholder_html())

    # ── 数据加载 ──

    def load_stock(self, code, name):
        """加载股票数据 — 获取K线并渲染图表"""
        self._stop_live()
        self._current_code = code
        self._current_name = name
        self.search_input.setText(f"{code} {name}")
        for btn, key in self._period_btns:
            btn.setChecked(key == self._current_period)
        Thread(target=self._fetch_and_render, args=(code, name, self._current_period),
               daemon=True).start()

    def _fetch_and_render(self, code, name, period):
        if not self._router:
            return
        if period == "time":
            minute_data = self._router.get_minute(code)
            if self._current_code != code:
                return
            if minute_data:
                times = [d["time"] for d in minute_data]
                prices = [d["price"] for d in minute_data]
                volumes = [d["volume"] for d in minute_data]
                self._render_timeline(f"{code} {name} - \u5206\u65f6", times, prices, volumes)
                return
            log.warning("data", f"\u5206\u65f6\u6570\u636e\u4e3a\u7a7a: {code}", "\u5df2\u964d\u7ea7\u5230\u65e5K")
            period = "day"
        count_map = {"day": 300, "week": 150, "month": 60}
        klines = self._router.get_kline(code, period, count_map.get(period, 300))
        if self._current_code != code:
            return
        if klines:
            self._render_kline(code, name, period, klines)
        else:
            log.warning("data", f"K\u7ebf\u6570\u636e\u4e3a\u7a7a: {code} {period}",
                        "\u6240\u6709\u6570\u636e\u6e90\u5747\u672a\u8fd4\u56de\u6570\u636e")
            self._render_ready.emit(self._placeholder_html())

    # ── 实时定时器回调 ──

    def _live_tick(self):
        if not self._current_code or not self._router:
            return
        if self._current_period == "time":
            Thread(target=self._fetch_minute_append, daemon=True).start()

    def _chanlun_refresh_tick(self):
        """缠论K线定时刷新: 重新拉取K线数据，重算缠论+推演"""
        if not self._current_code or not self._router:
            return
        if self._current_period == "time":
            return
        if not self._chanlun_btn.isChecked():
            return
        Thread(target=self._fetch_and_render,
               args=(self._current_code, self._current_name, self._current_period),
               daemon=True).start()

    def _fetch_minute_append(self):
        code = self._current_code
        data = self._router.get_minute(code)
        if self._current_code != code or not data:
            return
        times = [d["time"] for d in data]
        prices = [d["price"] for d in data]
        volumes = [d["volume"] for d in data]
        if not prices:
            return
        # 检查控件是否仍然存在
        if not self.chart_view or self.chart_view.isHidden():
            return
        ref = prices[0]
        cc = self._chart_colors()
        vol_colored = [
            {"value": volumes[i] if i < len(volumes) else 0,
             "itemStyle": {"color": cc["vol_up"] if prices[i] >= ref else cc["vol_down"]}}
            for i in range(len(prices))
        ]
        if self._bridge:
            try:
                self._bridge.minuteReceived.emit(times, prices, vol_colored)
            except RuntimeError:
                # 桥接对象可能已被销毁
                self._bridge = None

    # ── K线图渲染 (首次完整渲染) ──

    def _render_kline(self, code, name, period, klines):
        import json as _json
        cc = self._chart_colors()
        c_bg = cc.get("bg", "#fff")
        c_title = cc.get("title", "#333")
        c_axis_label = cc.get("axis_label", "#999")
        c_axis_line = cc.get("axis_line", "#eee")
        c_tip_bg = cc.get("tip_bg", "#fff")
        c_tip_border = cc.get("tip_border", "#ddd")
        c_tip_text = cc.get("tip_text", "#333")
        c_divider = cc.get("divider", "#f0f0f0")
        c_grid_line = cc.get("grid_line", "#f0f0f0")
        c_vol_up = cc.get("vol_up", "rgba(239,83,80,0.65)")
        c_vol_down = cc.get("vol_down", "rgba(38,166,154,0.65)")
        c_slider_border = cc.get("slider_border", "#ddd")
        c_slider_fill = cc.get("slider_fill", "rgba(26,115,232,0.08)")
        c_slider_handle = cc.get("slider_handle", "#1a73e8")
        c_slider_text = cc.get("slider_text", "#999")

        dates = [k["date"] for k in klines]
        ohlc = [[k["open"], k["close"], k["low"], k["high"]] for k in klines]
        volumes = [k["volume"] for k in klines]

        def calc_ma(data, n):
            """计算移动平均线 — MA5/MA10/MA20/MA30/MA60"""
            ma = []
            for i in range(len(data)):
                if i < n - 1:
                    ma.append('-')
                else:
                    s = sum(data[j][1] for j in range(i - n + 1, i + 1))
                    ma.append(round(s / n, 2))
            return ma

        ma5 = calc_ma(ohlc, 5)
        ma10 = calc_ma(ohlc, 10)
        ma20 = calc_ma(ohlc, 20)

        vol_colored = []
        for i, v in enumerate(volumes):
            color = c_vol_up if ohlc[i][1] >= ohlc[i][0] else c_vol_down
            vol_colored.append({"value": v, "itemStyle": {"color": color}})

        # ── 缠论叠加计算 ──
        chanlun_extra_series = ""
        chanlun_legend_extra = ""
        chanlun_kline_mark = ""
        overlay = None  # 初始化，防止多级别引用时未定义
        show_chanlun = self._chanlun_btn.isChecked() and period != "time"
        show_multi_tf = self._multi_tf_btn.isChecked() and period != "time"

        if show_chanlun:
            overlay = self._compute_chanlun_overlay(klines, max_visible_bars=len(dates))
            if overlay:
                date_idx = {d: i for i, d in enumerate(dates)}
                extras = []

                # ── 笔: 连续折线串联所有端点 + 散点标注方向 ──
                # 按笔的顺序收集端点（每笔终点=下笔起点，只出现一次）
                stroke_points = []  # [(date, price, direction)]
                for s in overlay['strokes']:
                    sd = s['data']
                    d0, d1 = sd[0]['xAxis'], sd[1]['xAxis']
                    if d0 not in date_idx or d1 not in date_idx:
                        continue
                    p0, p1 = sd[0]['yAxis'], sd[1]['yAxis']
                    if not stroke_points:
                        stroke_points.append((d0, p0, s['direction']))
                    stroke_points.append((d1, p1, s['direction']))

                # 连续折线（灰色虚线）
                polyline_data = ['null'] * len(dates)
                for d, p, _ in stroke_points:
                    if d in date_idx:
                        polyline_data[date_idx[d]] = str(p)

                extras.append(
                    f"""{{ name: '笔', type: 'line', data: [{','.join(polyline_data)}],
                       xAxisIndex: 0, yAxisIndex: 0, connectNulls: true,
                       showSymbol: false, lineStyle: {{ color: '#888', width: 2, type: 'dashed' }},
                       itemStyle: {{ color: '#888' }}, z: 15,
                       tooltip: {{ show: false }}, silent: true }}"""
                )

                # 散点: 上升笔端点（橙色）+ 下降笔端点（蓝色）
                up_points = []
                down_points = []
                for d, p, direction in stroke_points:
                    if d not in date_idx:
                        continue
                    if direction == 'up':
                        up_points.append([date_idx[d], p])
                    else:
                        down_points.append([date_idx[d], p])

                if up_points:
                    extras.append(
                        f"""{{ name: '上升笔端点', type: 'scatter', data: {json.dumps(up_points)},
                           xAxisIndex: 0, yAxisIndex: 0, symbol: 'circle', symbolSize: 6,
                           itemStyle: {{ color: '#e67e22' }}, z: 16,
                           tooltip: {{ show: false }}, silent: true }}"""
                    )
                if down_points:
                    extras.append(
                        f"""{{ name: '下降笔端点', type: 'scatter', data: {json.dumps(down_points)},
                           xAxisIndex: 0, yAxisIndex: 0, symbol: 'circle', symbolSize: 6,
                           itemStyle: {{ color: '#3498db' }}, z: 16,
                           tooltip: {{ show: false }}, silent: true }}"""
                    )

                # ── 线段: 合并为1个series ──
                seg_data = ['null'] * len(dates)
                for seg in overlay['segments']:
                    sd = seg['data']
                    d0, d1 = sd[0]['xAxis'], sd[1]['xAxis']
                    if d0 not in date_idx or d1 not in date_idx:
                        continue
                    i0, i1 = date_idx[d0], date_idx[d1]
                    seg_data[i0] = str(sd[0]['yAxis'])
                    seg_data[i1] = str(sd[1]['yAxis'])

                extras.append(
                    f"""{{ name: '线段', type: 'line', data: [{','.join(seg_data)}],
                       xAxisIndex: 0, yAxisIndex: 0, connectNulls: true,
                       showSymbol: true, symbolSize: 8, symbol: 'diamond',
                       lineStyle: {{ color: '#8e44ad', width: 3.5, type: 'solid' }},
                       itemStyle: {{ color: '#8e44ad', borderColor: '#6c3483', borderWidth: 2 }}, z: 16,
                       tooltip: {{ show: false }}, silent: true }}"""
                )

                # ── 中枢 → markArea ──
                hub_mark_data = []
                for h in overlay['hubs']:
                    start_date = h['start_date'] if h['start_date'] in date_idx else dates[0]
                    end_date = h['end_date'] if h['end_date'] in date_idx else dates[-1]
                    hub_mark_data.append([
                        {'xAxis': start_date, 'yAxis': h['bottom'],
                         'itemStyle': {'color': 'rgba(142,68,173,0.15)',
                                       'borderColor': '#8e44ad', 'borderWidth': 1.5, 'borderType': 'solid'}},
                        {'xAxis': end_date, 'yAxis': h['top']},
                    ])

                if hub_mark_data:
                    hub_json = json.dumps(hub_mark_data, ensure_ascii=False)
                    chanlun_kline_mark = (
                        f", markArea: {{ data: {hub_json}, "
                        f"silent: false, "
                        f"tooltip: {{ formatter: function(p) {{ return '中枢区间: ' + p.data[0].yAxis.toFixed(3) + ' ~ ' + p.data[1].yAxis.toFixed(3); }} }} }}"
                    )

                # ── 买卖点 → scatter ──
                buy_data = []
                for b in overlay['buy_signals']:
                    if b['date'] in date_idx:
                        buy_data.append([date_idx[b['date']], b['price']])
                sell_data = []
                for s in overlay['sell_signals']:
                    if s['date'] in date_idx:
                        sell_data.append([date_idx[s['date']], s['price']])

                if buy_data:
                    extras.append(
                        f"""{{ name: 'B', type: 'scatter', data: {json.dumps(buy_data)},
                           xAxisIndex: 0, yAxisIndex: 0, symbol: 'triangle', symbolSize: 18, symbolRotate: 0,
                           itemStyle: {{ color: '#e74c3c', borderColor: '#c0392b', borderWidth: 2 }},
                           label: {{ show: true, formatter: 'B', position: 'bottom',
                                     color: '#e74c3c', fontSize: 13, fontWeight: 'bold' }},
                           z: 20 }}"""
                    )
                if sell_data:
                    extras.append(
                        f"""{{ name: 'S', type: 'scatter', data: {json.dumps(sell_data)},
                           xAxisIndex: 0, yAxisIndex: 0, symbol: 'diamond', symbolSize: 18,
                           itemStyle: {{ color: '#27ae60', borderColor: '#1e8449', borderWidth: 2 }},
                           label: {{ show: true, formatter: 'S', position: 'top',
                                     color: '#27ae60', fontSize: 13, fontWeight: 'bold' }},
                           z: 20 }}"""
                    )

                # ═══ 推演层: markLine 预测未来 ═══
                proj = overlay.get('projection')
                if proj:
                    proj_target_y = proj['end_target']
                    proj_color = '#e67e22' if proj['is_current_up'] else '#3498db'
                    proj_label = '推演↓回调' if proj['is_current_up'] else '推演↑反弹'
                    support_top = proj['support_zone_top']
                    support_bottom = proj['support_zone_bottom']

                    # 推演目标水平线（延伸到未来占位日期）
                    future_end = dates[-1]  # 最后一个占位日期
                    proj_line_data = [
                        [{'xAxis': dates[max(0, len(dates)-20)], 'yAxis': proj_target_y},
                         {'xAxis': future_end, 'yAxis': proj_target_y}]
                    ]

                    # 推演支撑/阻力水平线
                    proj_line_data.append(
                        [{'xAxis': dates[max(0, len(dates)-20)], 'yAxis': support_top},
                         {'xAxis': future_end, 'yAxis': support_top}]
                    )
                    proj_line_data.append(
                        [{'xAxis': dates[max(0, len(dates)-20)], 'yAxis': support_bottom},
                         {'xAxis': future_end, 'yAxis': support_bottom}]
                    )

                    # 推演中枢水平线（如果有）
                    if proj.get('next_hub_center'):
                        proj_line_data.append(
                            [{'xAxis': dates[max(0, len(dates)-20)], 'yAxis': proj['next_hub_top']},
                             {'xAxis': future_end, 'yAxis': proj['next_hub_top']}]
                        )
                        proj_line_data.append(
                            [{'xAxis': dates[max(0, len(dates)-20)], 'yAxis': proj['next_hub_bottom']},
                             {'xAxis': future_end, 'yAxis': proj['next_hub_bottom']}]
                        )

                    # 合并: 实际中枢 markArea + 推演 markLine
                    if hub_mark_data:
                        all_area_json = json.dumps(hub_mark_data, ensure_ascii=False)
                        chanlun_kline_mark = (
                            f", markArea: {{ data: {all_area_json}, silent: false, "
                            f"tooltip: {{ formatter: function(p) {{ "
                            f"return '📦 中枢: ' + p.data[0].yAxis.toFixed(3) + ' ~ ' + p.data[1].yAxis.toFixed(3); "
                            f"}} }} }}"
                        )

                    proj_line_json = json.dumps(proj_line_data, ensure_ascii=False)

                    # 每条推演线的标注名称
                    proj_line_labels = []
                    proj_line_labels.append(f"'目标位 ' + {proj_target_y}")
                    proj_line_labels.append(f"'支撑上沿 ' + {support_top}")
                    proj_line_labels.append(f"'支撑下沿 ' + {support_bottom}")
                    if proj.get('next_hub_center'):
                        proj_line_labels.append(f"'中枢上沿 ' + {proj['next_hub_top']}")
                        proj_line_labels.append(f"'中枢下沿 ' + {proj['next_hub_bottom']}")

                    labels_js = ','.join([
                        f"{{ show: true, formatter: function(p) {{ return {lbl}; }}, position: 'end', color: '{proj_color}', fontSize: 10 }}"
                        for lbl in proj_line_labels
                    ])

                    chanlun_kline_mark += (
                        f", markLine: {{ data: {proj_line_json}, "
                        f"silent: true, symbol: ['none', 'none'], "
                        f"lineStyle: {{ color: '{proj_color}66', type: 'dashed', width: 1.5 }}, "
                        f"label: [{labels_js}] }}"
                    )

                chanlun_extra_series = ",\n                    ".join(extras)
                chanlun_legend_extra = ", '笔', '线段'" + (", 'B', 'S'" if buy_data or sell_data else "")

                # ═══ 多级别联立: 叠加低级别笔和中枢 ═══
                if show_multi_tf and overlay.get('multi_tf'):
                    mt = overlay['multi_tf']
                    # 低级别笔（紫色/青色，与高级别区分）
                    up_mt_data = ['null'] * len(dates)
                    down_mt_data = ['null'] * len(dates)
                    for s in mt.get('strokes', []):
                        sd = s['data']
                        d0, d1 = sd[0]['xAxis'], sd[1]['xAxis']
                        if d0 not in date_idx or d1 not in date_idx:
                            continue
                        i0, i1 = date_idx[d0], date_idx[d1]
                        p0, p1 = sd[0]['yAxis'], sd[1]['yAxis']
                        if s['direction'] == 'up':
                            up_mt_data[i0] = str(p0)
                            up_mt_data[i1] = str(p1)
                        else:
                            down_mt_data[i0] = str(p0)
                            down_mt_data[i1] = str(p1)

                    extras.append(
                        f"""{{ name: '分钟↑笔', type: 'line', data: [{','.join(up_mt_data)}],
                           xAxisIndex: 0, yAxisIndex: 0, connectNulls: true,
                           showSymbol: false, lineStyle: {{ color: '#9b59b6', width: 1.5, type: 'dotted' }},
                           itemStyle: {{ color: '#9b59b6' }}, z: 14,
                           tooltip: {{ show: false }}, silent: true }}"""
                    )
                    extras.append(
                        f"""{{ name: '分钟↓笔', type: 'line', data: [{','.join(down_mt_data)}],
                           xAxisIndex: 0, yAxisIndex: 0, connectNulls: true,
                           showSymbol: false, lineStyle: {{ color: '#1abc9c', width: 1.5, type: 'dotted' }},
                           itemStyle: {{ color: '#1abc9c' }}, z: 14,
                           tooltip: {{ show: false }}, silent: true }}"""
                    )

                    # 低级别中枢（半透明紫色）
                    mt_hub_mark_data = []
                    for h in mt.get('hubs', []):
                        sd = h.get('start_date', '')
                        ed = h.get('end_date', '')
                        if sd not in date_idx or ed not in date_idx:
                            continue
                        mt_hub_mark_data.append([
                            {'xAxis': sd, 'yAxis': h['bottom'],
                             'itemStyle': {'color': 'rgba(155,89,182,0.1)',
                                           'borderColor': '#9b59b6', 'borderWidth': 1, 'borderType': 'dotted'}},
                            {'xAxis': ed, 'yAxis': h['top']},
                        ])

                    if mt_hub_mark_data:
                        # 合并到现有 markArea
                        existing_area = hub_mark_data if hub_mark_data else []
                        all_area = existing_area + mt_hub_mark_data
                        all_area_json = json.dumps(all_area, ensure_ascii=False)
                        chanlun_kline_mark = (
                            f", markArea: {{ data: {all_area_json}, silent: false, "
                            f"tooltip: {{ formatter: function(p) {{ "
                            f"var prefix = p.dataIndex < {len(existing_area)} ? '📦 中枢: ' : '⏱ 分钟中枢: '; "
                            f"return prefix + p.data[0].yAxis.toFixed(3) + ' ~ ' + p.data[1].yAxis.toFixed(3); "
                            f"}} }} }}"
                        )

                    chanlun_extra_series = ",\n                    ".join(extras)
                    chanlun_legend_extra += ", '分钟↑笔', '分钟↓笔'"

            # 推演延伸: 给 xAxis 添加15个占位日期，让推演线有足够空间往右画
            if overlay.get('projection'):
                _last = dates[-1] if dates else ''
                for _i in range(1, 16):
                    dates.append(f'→{_i}')
                ohlc.extend([[None, None, None, None]] * 15)
                vol_colored.extend([0] * 15)
                ma5.extend(['-'] * 15)
                ma10.extend(['-'] * 15)
                ma20.extend(['-'] * 15)

        period_labels = {"day": "\u65e5K", "week": "\u5468K", "month": "\u6708K"}
        title = f"{code} {name}  {period_labels.get(period, period)}"

        # 多级别信号显示（show_multi_tf 已在前面定义）
        if show_multi_tf and overlay and overlay.get('multi_tf'):
            mt_sig = overlay['multi_tf'].get('signal', {})
            mt_dir = mt_sig.get('signal', 'HOLD')
            mt_conf = mt_sig.get('confidence', 50)
            if mt_dir != 'HOLD':
                title += f"  \u591a\u7ea7\u522b\u5171\u632f: {mt_dir} {mt_conf:.0f}%"
            else:
                title += f"  \u591a\u7ea7\u522b: {mt_dir}"
        # 减去推演占位的15个日期来计算真实K线数
        real_len = len(dates) - 15 if show_chanlun and overlay and overlay.get('projection') else len(dates)
        start_pct = max(0, int((1 - 120 / real_len) * 100)) if real_len > 120 else 0

        html = f"""
        <!DOCTYPE html><html><head>
        <meta charset="utf-8">
        {_echarts_script_tag()}
        <style>body {{ margin:0; overflow:hidden; background:{c_bg}; }}</style>
        </head><body>
        <div id="main" style="width:100%;height:100vh;"></div>
        <script>
            var chart = echarts.init(document.getElementById('main'));
            var dates = {_json.dumps(dates)};
            var ohlc  = {_json.dumps(ohlc)};
            var vol   = {_json.dumps(vol_colored)};
            var ma5   = {_json.dumps(ma5)};
            var ma10  = {_json.dumps(ma10)};
            var ma20  = {_json.dumps(ma20)};
            var option = {{
                animation: false,
                progressive: 200,
                progressiveThreshold: 300,
                title: {{
                    text: {_json.dumps(title)}, left: 14, top: 10,
                    textStyle: {{ color: '{c_title}', fontSize: 14 }}
                }},
                legend: {{
                    top: 10, right: 14, data: ['MA5', 'MA10', 'MA20'{chanlun_legend_extra if show_chanlun else ''}],
                    textStyle: {{ color: '{c_axis_label}', fontSize: 11 }},
                    itemWidth: 24, itemHeight: 2,
                    selected: {{'笔': true, '线段': true}}
                }},
                tooltip: {{
                    trigger: 'axis',
                    axisPointer: {{ type: 'cross', lineStyle: {{ color: '{c_axis_line}' }} }},
                    backgroundColor: '{c_tip_bg}', borderColor: '{c_tip_border}', borderWidth: 1,
                    textStyle: {{ color: '{c_tip_text}', fontSize: 12 }},
                    formatter: function(params) {{
                        var d = params[0] ? params[0].axisValue : '';
                        var kData = null, vData = null, maVals = [];
                        for (var i = 0; i < params.length; i++) {{
                            var p = params[i];
                            if (p.seriesType === 'candlestick') kData = p.data;
                            if (p.seriesType === 'bar') vData = p.data;
                            if (p.seriesType === 'line' && p.data !== '-')
                                maVals.push('<span style="color:' + p.color + ';">' + p.seriesName + ': ' + p.data + '</span>');
                        }}
                        if (!kData) return '';
                        var o = kData[1], c = kData[2], lo = kData[3], hi = kData[4];
                        var chg = ((c - o) / o * 100).toFixed(2);
                        var clr = c >= o ? '#ef5350' : '#26a69a';
                        var vs = vData ? (vData.value >= 1e8 ? (vData.value/1e8).toFixed(2)+'\\u4ebf' : vData.value >= 1e4 ? (vData.value/1e4).toFixed(0)+'\\u4e07' : vData.value) : '';
                        var h = '<div style="margin-bottom:3px;color:{c_axis_label};">' + d + '</div>';
                        h += '<div style="display:flex;gap:14px;"><span>\\u5f00: <b>' + o + '</b></span><span>\\u6536: <b style="color:' + clr + ';">' + c + '</b></span></div>';
                        h += '<div style="display:flex;gap:14px;margin-top:1px;"><span>\\u9ad8: <b style="color:#ef5350;">' + hi + '</b></span><span>\\u4f4e: <b style="color:#26a69a;">' + lo + '</b></span></div>';
                        h += '<div style="margin-top:2px;"><span style="color:' + clr + ';font-weight:bold;">' + chg + '%</span> &nbsp; \\u91cf: ' + vs + '</div>';
                        if (maVals.length) h += '<div style="margin-top:4px;border-top:1px solid {c_divider};padding-top:4px;">' + maVals.join(' &nbsp; ') + '</div>';
                        return h;
                    }}
                }},
                axisPointer: {{
                    link: [{{ xAxisIndex: 'all' }}],
                    label: {{ backgroundColor: '{c_tip_bg}', color: '{c_tip_text}', borderColor: '{c_tip_border}' }}
                }},
                grid: [
                    {{ left: 60, right: 28, top: 48, height: '58%' }},
                    {{ left: 60, right: 28, top: '76%', height: '16%' }}
                ],
                xAxis: [
                    {{ type: 'category', data: dates, gridIndex: 0,
                       axisLine: {{ lineStyle: {{ color: '{c_axis_line}' }} }},
                       axisLabel: {{ color: '{c_axis_label}', fontSize: 10 }},
                       axisTick: {{ show: false }}, splitLine: {{ show: false }} }},
                    {{ type: 'category', data: dates, gridIndex: 1,
                       axisLine: {{ lineStyle: {{ color: '{c_axis_line}' }} }},
                       axisLabel: {{ show: false }}, axisTick: {{ show: false }}, splitLine: {{ show: false }} }}
                ],
                yAxis: [
                    {{ type: 'value', scale: true, gridIndex: 0,
                       axisLine: {{ show: false }},
                       axisLabel: {{ color: '{c_axis_label}', fontSize: 10 }},
                       axisTick: {{ show: false }},
                       splitLine: {{ lineStyle: {{ color: '{c_grid_line}' }} }},
                       position: 'right' }},
                    {{ type: 'value', scale: true, gridIndex: 1,
                       axisLine: {{ show: false }},
                       axisLabel: {{ color: '{c_axis_label}', fontSize: 9 }},
                       axisTick: {{ show: false }},
                       splitLine: {{ show: false }},
                       position: 'right' }}
                ],
                dataZoom: [{{ type: 'inside', xAxisIndex: [0, 1], start: {start_pct}, end: 100 }}],
                series: [
                    {{ name: 'K\\u7ebf', type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
                       itemStyle: {{ color: '#ef5350', color0: '#26a69a', borderColor: '#ef5350', borderColor0: '#26a69a' }}{chanlun_kline_mark} }},
                    {{ name: 'MA5', type: 'line', data: ma5, xAxisIndex: 0, yAxisIndex: 0,
                       smooth: true, showSymbol: false, lineStyle: {{ width: 1.2, color: '#e6a23c' }} }},
                    {{ name: 'MA10', type: 'line', data: ma10, xAxisIndex: 0, yAxisIndex: 0,
                       smooth: true, showSymbol: false, lineStyle: {{ width: 1.2, color: '#409eff' }} }},
                    {{ name: 'MA20', type: 'line', data: ma20, xAxisIndex: 0, yAxisIndex: 0,
                       smooth: true, showSymbol: false, lineStyle: {{ width: 1.2, color: '#9b59b6' }} }},
                    {{ name: '\\u6210\\u4ea4\\u91cf', type: 'bar', data: vol, xAxisIndex: 1, yAxisIndex: 1, barMaxWidth: 8 }}
                    {(',' + chr(10) + '                    ' + chanlun_extra_series) if chanlun_extra_series else ''}
                ]
            }};
            chart.setOption(option);
            window.addEventListener('resize', function(){{ chart.resize(); }});
        </script></body></html>
        """
        self._render_ready.emit(html)

    # ── 分时图渲染 (首次完整 + QWebChannel 增量桥接) ──

    def _render_timeline(self, title, times, prices, volumes):
        import json as _json
        cc = self._chart_colors()
        c_bg = cc.get("bg", "#fff")
        c_title = cc.get("title", "#333")
        c_axis_label = cc.get("axis_label", "#999")
        c_axis_line = cc.get("axis_line", "#eee")
        c_tip_bg = cc.get("tip_bg", "#fff")
        c_tip_border = cc.get("tip_border", "#ddd")
        c_tip_text = cc.get("tip_text", "#333")
        c_grid_line = cc.get("grid_line", "#f0f0f0")
        c_vol_up = cc.get("vol_up", "rgba(239,83,80,0.65)")
        c_vol_down = cc.get("vol_down", "rgba(38,166,154,0.65)")
        c_accent = cc.get("accent", "#1a73e8")
        c_area_start = cc.get("area_start", "rgba(26,115,232,0.2)")
        c_area_mid = cc.get("area_mid", "rgba(26,115,232,0.05)")
        c_area_end = cc.get("area_end", "rgba(26,115,232,0)")

        ref_price = prices[0] if prices else 0
        vol_colored = [
            {"value": volumes[i] if i < len(volumes) else 0,
             "itemStyle": {"color": c_vol_up if prices[i] >= ref_price else c_vol_down}}
            for i in range(len(prices))
        ]
        avg_price = round(sum(prices) / len(prices), 2) if prices else 0
        avg_line = [avg_price] * len(prices)

        html = f"""
        <!DOCTYPE html><html><head>
        <meta charset="utf-8">
        {_echarts_script_tag()}
        <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
        <style>body {{ margin:0; overflow:hidden; background:{c_bg}; }}</style>
        </head><body>
        <div id="main" style="width:100%;height:100vh;"></div>
        <script>
            var chart = echarts.init(document.getElementById('main'));
            var times = {_json.dumps(times)};
            var prices = {_json.dumps(prices)};
            var vol = {_json.dumps(vol_colored)};
            var avg = {_json.dumps(avg_line)};
            var ref = prices[0] || 0;

            var option = {{
                animation: false,
                title: {{
                    text: {_json.dumps(title)}, left: 14, top: 10,
                    textStyle: {{ color: '{c_title}', fontSize: 14 }}
                }},
                tooltip: {{
                    trigger: 'axis',
                    axisPointer: {{ type: 'cross', lineStyle: {{ color: '{c_axis_line}' }} }},
                    backgroundColor: '{c_tip_bg}', borderColor: '{c_tip_border}', borderWidth: 1,
                    textStyle: {{ color: '{c_tip_text}', fontSize: 12 }},
                    formatter: function(params) {{
                        var t = params[0] ? params[0].axisValue : '';
                        var p = null, v = null;
                        for (var i = 0; i < params.length; i++) {{
                            if (params[i].seriesType === 'line' && params[i].seriesName === '\\u5206\\u65f6') p = params[i].data;
                            if (params[i].seriesType === 'bar') v = params[i].data;
                        }}
                        if (p === null) return '';
                        var chg = ref ? ((p - ref) / ref * 100).toFixed(2) : '0.00';
                        var clr = p >= ref ? '#ef5350' : '#26a69a';
                        var vs = v ? (v >= 1e4 ? (v/1e4).toFixed(0) + '\\u4e07' : v) : '';
                        return '<div style="color:{c_axis_label};margin-bottom:2px;">' + t + '</div>' +
                               '<span style="color:' + clr + ';font-weight:bold;">' + p + '</span> ' +
                               '<span style="color:' + clr + ';">' + chg + '%</span>' +
                               ' &nbsp; \\u91cf: ' + vs;
                    }}
                }},
                axisPointer: {{
                    link: [{{ xAxisIndex: 'all' }}],
                    label: {{ backgroundColor: '{c_tip_bg}', color: '{c_tip_text}', borderColor: '{c_tip_border}' }}
                }},
                grid: [
                    {{ left: 60, right: 28, top: 48, height: '58%' }},
                    {{ left: 60, right: 28, top: '76%', height: '16%' }}
                ],
                xAxis: [
                    {{ type: 'category', data: times, gridIndex: 0,
                       axisLine: {{ lineStyle: {{ color: '{c_axis_line}' }} }},
                       axisLabel: {{ color: '{c_axis_label}', fontSize: 10, interval: Math.floor(times.length / 8) }},
                       axisTick: {{ show: false }}, splitLine: {{ show: false }} }},
                    {{ type: 'category', data: times, gridIndex: 1,
                       axisLine: {{ lineStyle: {{ color: '{c_axis_line}' }} }},
                       axisLabel: {{ show: false }}, axisTick: {{ show: false }}, splitLine: {{ show: false }} }}
                ],
                yAxis: [
                    {{ type: 'value', scale: true, gridIndex: 0,
                       axisLine: {{ show: false }},
                       axisLabel: {{ color: '{c_axis_label}', fontSize: 10 }},
                       axisTick: {{ show: false }},
                       splitLine: {{ lineStyle: {{ color: '{c_grid_line}' }} }},
                       position: 'right' }},
                    {{ type: 'value', scale: true, gridIndex: 1,
                       axisLine: {{ show: false }},
                       axisLabel: {{ color: '{c_axis_label}', fontSize: 9 }},
                       axisTick: {{ show: false }},
                       splitLine: {{ show: false }},
                       position: 'right' }}
                ],
                dataZoom: [{{ type: 'inside', xAxisIndex: [0, 1] }}],
                series: [
                    {{ name: '\\u5206\\u65f6', type: 'line', data: prices, xAxisIndex: 0, yAxisIndex: 0,
                       smooth: true, showSymbol: false,
                       lineStyle: {{ color: '{c_accent}', width: 1.5 }},
                       areaStyle: {{
                           color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                               {{ offset: 0, color: '{c_area_start}' }},
                               {{ offset: 0.6, color: '{c_area_mid}' }},
                               {{ offset: 1, color: '{c_area_end}' }}
                           ])
                       }} }},
                    {{ name: '\\u5747\\u4ef7', type: 'line', data: avg, xAxisIndex: 0, yAxisIndex: 0,
                       smooth: false, showSymbol: false,
                       lineStyle: {{ color: '#e6a23c', width: 1, type: 'dashed' }} }},
                    {{ name: '\\u6210\\u4ea4\\u91cf', type: 'bar', data: vol, xAxisIndex: 1, yAxisIndex: 1,
                       barMaxWidth: 3 }}
                ]
            }};
            chart.setOption(option);
            window.addEventListener('resize', function(){{ chart.resize(); }});

            // ===== QWebChannel 增量接收 =====
            window._minuteHandler = function(t, p, v) {{
                if (!t || !t.length) return;
                var sum = 0;
                for (var i = 0; i < p.length; i++) sum += p[i];
                var newAvg = p.length > 0 ? (sum / p.length).toFixed(2) : 0;
                var newAvgLine = [];
                for (var i = 0; i < p.length; i++) newAvgLine.push(newAvg);

                chart.setOption({{
                    xAxis: [{{ data: t }}, {{ data: t }}],
                    series: [
                        {{ data: p }},
                        {{ data: newAvgLine }},
                        {{ data: v }}
                    ]
                }}, false);
            }};

            if (typeof QWebChannel !== 'undefined') {{
                new QWebChannel(qt.webChannelTransport, function(channel) {{
                    window._bridge = channel.objects.bridge;
                    if (window._bridge) {{
                        window._bridge.minuteReceived.connect(function(t, p, v) {{
                            window._minuteHandler(t, p, v);
                        }});
                    }}
                }});
            }}
        </script></body></html>
        """
        self._render_ready.emit(html)
        self._start_live_requested.emit()
