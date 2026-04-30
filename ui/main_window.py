#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主窗口 v2.0 - 六页标签布局
========================

特性:
- 页面间联动
- 主题切换
- 动画过渡
- 全局状态管理
"""

from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QSplitter, QStackedWidget, QApplication,
                             QFrame, QLabel)
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtGui import QFont
from typing import Optional

from data_sources import DataRouter
from core.logger import log
from ui.theme import ThemeManager, get_current_colors, init_theme


from ui.panels import (HeaderPanel, ChartPanel, PositionPanel,
                       SignalPanel, StrategyPanel, LogPanel,
                       BacktestPanel, SettingsPanel, ETFPoolPanel)


# ═══════════════════════════════════════════════════════════════
# Python ↔ JS 桥接
# ═══════════════════════════════════════════════════════════════

class Bridge(QObject):
    """QWebChannel 桥接对象"""
    minuteReceived = pyqtSignal(list, list, list)  # times, prices, volumes
    klineReceived = pyqtSignal(list)


# ═══════════════════════════════════════════════════════════════
# 导航按钮
# ═══════════════════════════════════════════════════════════════

class NavButton(QPushButton):
    """自定义导航按钮"""
    
    def __init__(self, text: str, icon: str = "", parent=None):
        """初始化"""
        super().__init__(parent)
        self.setText(f"{icon}  {text}" if icon else text)
        self.setCheckable(True)
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self._apply_style()
    
    def _apply_style(self):
        c = get_current_colors()
        self.setStyleSheet(f"""
            NavButton {{
                background-color: transparent;
                color: {c.text_secondary};
                border: none;
                border-bottom: 3px solid transparent;
                padding: 0 20px;
                font-size: 14px;
                font-family: "Microsoft YaHei";
                font-weight: 500;
            }}
            NavButton:hover {{
                color: {c.text_primary};
            }}
            NavButton:checked {{
                color: {c.accent};
                border-bottom: 3px solid {c.accent};
                font-weight: bold;
            }}
        """)


# ═══════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    """主窗口 - 六页标签布局 v2.0"""
    
    def __init__(self, router: Optional[DataRouter] = None):
        """初始化"""
        super().__init__()
        self.setWindowTitle("Xm-LH 智能持仓管理系统")
        
        # 初始化主题
        init_theme()
        
        # 数据路由器 (支持依赖注入)
        self.router = router or DataRouter()
        
        # 自适应屏幕尺寸
        self._setup_window_size()
        
        self._init_ui()
        self._setup_connections()
        self._start_initialization()
    
    def _setup_window_size(self):
        """设置窗口大小"""
        try:
            from PyQt5.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen().availableGeometry()
            w = int(screen.width() * 0.7)
            h = int(screen.height() * 0.75)
            self.resize(w, h)
            self.move(
                screen.x() + (screen.width() - w) // 2,
                screen.y() + (screen.height() - h) // 2
            )
        except Exception:
            self.resize(1280, 800)
    
    def _init_ui(self):
        """初始化UI"""
        c = get_current_colors()
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        
        # ═══ 顶部状态栏 ═══
        self.header = HeaderPanel()
        root_layout.addWidget(self.header)
        
        # ═══ 内容区域 ═══
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(0)
        
        # ═══ 导航栏 ═══
        nav_container = QFrame()
        nav_container.setStyleSheet(f"""
            QFrame {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 12px;
                margin-bottom: 16px;
            }}
        """)
        nav_bar = QHBoxLayout(nav_container)
        nav_bar.setSpacing(0)
        nav_bar.setContentsMargins(8, 4, 8, 4)
        
        self.nav_buttons = []
        nav_items = [
            ("我的持仓", "💰", 0),
            ("行情走势", "📈", 1),
            ("ETF T+0", "🔄", 2),
            ("智能策略", "🧠", 3),
            ("日志", "📋", 4),
            ("回测分析", "📊", 5),
            ("系统设置", "⚙️", 6),
        ]
        
        for text, icon, idx in nav_items:
            btn = NavButton(text, icon)
            btn.clicked.connect(lambda checked, i=idx: self._switch_page(i))
            nav_bar.addWidget(btn)
            self.nav_buttons.append(btn)
        
        nav_bar.addStretch()
        content_layout.addWidget(nav_container)
        
        # ═══ 页面容器 ═══
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"""
            QStackedWidget {{
                background: {c.bg_surface};
                border: 1px solid {c.border};
                border-radius: 16px;
            }}
        """)
        
        # 页1: 我的持仓
        self._init_page_home()
        
        # 页2: 行情走势
        self._init_page_chart()
        
        # 页3: ETF T+0
        self._init_page_etf()
        
        # 页4: 智能策略
        self._init_page_strategy()
        
        # 页5: 日志
        self._init_page_log()
        
        # 页6: 回测分析
        self._init_page_backtest()
        
        # 页7: 系统设置
        self._init_page_settings()
        
        content_layout.addWidget(self.stack, stretch=1)
        root_layout.addWidget(content, stretch=1)
        
        # 默认选中第一个
        self.nav_buttons[0].setChecked(True)
    
    def _init_page_home(self):
        """初始化持仓页"""
        page_home = QWidget()
        page_home.setStyleSheet("background: transparent;")
        home_layout = QVBoxLayout(page_home)
        home_layout.setSpacing(0)
        home_layout.setContentsMargins(16, 16, 16, 16)
        
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(8)
        splitter.setChildrenCollapsible(False)
        
        self.position_panel = PositionPanel()
        self.position_panel.set_router(self.router)
        
        self.signal_panel = SignalPanel()
        
        splitter.addWidget(self.position_panel)
        splitter.addWidget(self.signal_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 200])
        
        home_layout.addWidget(splitter)
        self.stack.addWidget(page_home)
    
    def _init_page_chart(self):
        """初始化行情页"""
        self.chart_panel = ChartPanel()
        self.chart_panel.set_router(self.router)
        
        # QWebChannel 桥接
        self._bridge = Bridge()
        self._web_channel = QWebChannel()
        self._web_channel.registerObject("bridge", self._bridge)
        self.chart_panel.chart_view.page().setWebChannel(self._web_channel)
        self.chart_panel._bridge = self._bridge
        
        self.stack.addWidget(self.chart_panel)
    
    def _init_page_etf(self):
        """初始化 ETF T+0 页"""
        self.etf_panel = ETFPoolPanel()
        self.etf_panel.etf_clicked.connect(self._on_etf_clicked)
        # ETF 信号卡 → 推送到信号面板（带完整解释）
        self.etf_panel.new_signal.connect(self._on_etf_signal)
        self.stack.addWidget(self.etf_panel)
    
    def _init_page_strategy(self):
        """初始化策略页"""
        self.strategy_panel = StrategyPanel()
        self.stack.addWidget(self.strategy_panel)
    
    def _init_page_log(self):
        """初始化日志页"""
        self.log_panel = LogPanel()
        self.stack.addWidget(self.log_panel)
    
    def _init_page_backtest(self):
        """初始化回测页"""
        self.backtest_panel = BacktestPanel()
        self.backtest_panel.set_router(self.router)
        self.stack.addWidget(self.backtest_panel)
    
    def _init_page_settings(self):
        """初始化设置页"""
        self.settings_panel = SettingsPanel()
        self.stack.addWidget(self.settings_panel)
    
    def _setup_connections(self):
        """设置信号连接"""
        # 持仓点击 -> 跳转行情页
        self.position_panel.stock_clicked.connect(self._on_stock_clicked)

        # 回测股票选择 -> 跳转行情页
        self.backtest_panel.stock_selected.connect(self._on_stock_clicked)

        # 持仓变化 -> 刷新回测卡片
        self.position_panel.positions_changed.connect(self._refresh_backtest)

        # 持仓变化 -> 自动增量训练 (防抖)
        self.position_panel.positions_changed.connect(self.strategy_panel.on_positions_changed)

        # 持仓变化 -> 延迟扫描信号（等数据下载完成）
        self.position_panel.positions_changed.connect(
            lambda: QTimer.singleShot(8000, self._advisor_scan_now)
        )

        # 主题切换
        self.settings_panel.theme_changed.connect(self._on_theme_changed)

        # 策略训练完成 -> 刷新回测 + 立即扫描信号
        self.strategy_panel.training_completed.connect(
            lambda: QTimer.singleShot(2000, self._refresh_backtest)
        )
        self.strategy_panel.training_completed.connect(
            lambda: QTimer.singleShot(3000, self._advisor_scan_now)
        )

        # Logger 信号 → SignalPanel 实时显示（advisor 的信号也会推到这里）
        if log.signal:
            log.signal.connect(self._on_log_entry)

    def _on_log_entry(self, entry) -> None:
        """Logger 新日志 → 推送到 SignalPanel"""
        level_map = {
            "signal": "info",
            "warning": "warning",
            "error": "error",
        }
        sig_type = level_map.get(entry.level, "info")

        # 策略信号按内容区分颜色
        msg_text = entry.message if hasattr(entry, 'message') else str(entry)
        if "🟢" in msg_text or "买入" in msg_text or "BUY" in msg_text.upper():
            sig_type = "buy"
        elif "🔴" in msg_text or "卖出" in msg_text or "SELL" in msg_text.upper():
            sig_type = "sell"
        elif "🛑" in msg_text or "止损" in msg_text:
            sig_type = "warning"
        elif "🎯" in msg_text or "止盈" in msg_text:
            sig_type = "success"

        msg = f"[{entry.category}] {entry.message}"
        if entry.detail:
            msg += f" | {entry.detail}"
        self.signal_panel.add_signal(msg, sig_type)
    
    def _start_initialization(self):
        """启动初始化"""
        # 应用主题
        ThemeManager._apply()
        
        # 刷新市场状态
        status = self.router.get_market_status()
        self.header.set_market_status(status)
        
        # 显示数据源状态
        src = self.router.get_source_status()
        sources = []
        if src.get("tencent"):
            sources.append("腾讯")
        if src.get("eastmoney"):
            sources.append("东方财富")
        if src.get("sina"):
            sources.append("新浪")
        if src.get("akshare"):
            sources.append("AkShare")
        self.header.set_source_label(" + ".join(sources) if sources else "未连接")
        
        # 启动日志
        pos_count = self.position_panel.get_position_count()
        log.signal_log("system", "系统启动",
                       f"数据源: {' + '.join(sources) if sources else '未连接'}，"
                       f"加载持仓 {pos_count} 支")
        
        if not src.get("akshare"):
            log.warning("data", "AkShare 未安装",
                        "分钟级K线和全市场快照功能不可用，"
                        "请运行: pip install akshare")
        
        self.signal_panel.add_signal("系统启动成功", "info")
        self.signal_panel.add_signal(f"数据源: {' + '.join(sources) if sources else '未连接'}", "info")
        self.signal_panel.add_signal("准备就绪，等待行情数据...", "info")
    
    def _on_stock_clicked(self, code, name):
        """股票点击 - 跳转到行情页"""
        self.chart_panel.load_stock(code, name)
        self._switch_page(1)  # 跳转到行情页
        
        # 高亮信号
        self.signal_panel.add_signal(f"查看 {name} ({code}) 行情", "info")
    
    def _on_etf_clicked(self, code, name):
        """ETF 双击 - 跳转到行情页"""
        self.chart_panel.load_stock(code, name)
        self._switch_page(1)  # 跳转到行情页
        self.signal_panel.add_signal(f"查看 ETF {name} ({code}) 行情", "info")

    def _on_etf_signal(self, code, name, signal_obj, price):
        """ETF 扫描发现买卖信号 → 信号卡推送到信号面板"""
        self.signal_panel.add_signal_card(code, name, signal_obj, price)
    
    def _switch_page(self, index):
        """切换页面"""
        self.stack.setCurrentIndex(index)

        # 更新导航按钮
        for i, btn in enumerate(self.nav_buttons):
            btn.setChecked(i == index)
    
    def _on_theme_changed(self, theme_key):
        """主题切换"""
        c = get_current_colors()
        
        # 更新导航按钮样式
        for btn in self.nav_buttons:
            btn._apply_style()
        
        # 更新 HeaderPanel
        self.header.setStyleSheet(f"""
            HeaderPanel {{
                background-color: {c.bg_surface};
                border-bottom: 1px solid {c.border};
            }}
        """)
        
        # 刷新图表
        if self.chart_panel._current_code:
            self.chart_panel.load_stock(
                self.chart_panel._current_code,
                self.chart_panel._current_name
            )
        
        log.signal_log("system", f"主题切换: {theme_key}",
                       ThemeManager.get_meta().get('name', theme_key))
    
    def _advisor_scan_now(self):
        """立即触发顾问扫描（持仓变化/训练完成后）"""
        try:
            from core.advisor import get_advisor
            advisor = get_advisor()
            advisor.scan_now()
        except Exception as e:
            from core.logger import log
            log.debug("advisor", f"即时扫描触发失败: {e}")

    def _refresh_backtest(self):
        """刷新回测卡片（跟随持仓变化）"""
        if hasattr(self.backtest_panel, '_rebuild_cards'):
            self.backtest_panel._rebuild_cards()


if __name__ == "__main__":
    import sys
    
    app = QApplication(sys.argv)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())
