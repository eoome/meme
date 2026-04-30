#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能持仓管理系统 — 主程序入口
"""

import sys
import traceback
from datetime import datetime


def main() -> None:
    """启动 PyQt5 主窗口 + 信号顾问"""
    try:
        from PyQt5.QtWidgets import QApplication
        from ui.main_window import MainWindow
        from core.advisor import start_advisor

        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()

        # 启动信号顾问（后台线程，每5分钟扫描）
        start_advisor()

        sys.exit(app.exec_())
    except Exception as e:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("error.log", "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{ts}] {type(e).__name__}: {e}\n")
            f.write(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
