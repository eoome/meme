#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""python -m meme 入口"""
import sys
import os

# 确保项目根目录在 sys.path 中（支持从任意位置运行）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main

if __name__ == "__main__":
    main()
