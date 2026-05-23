#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自定义异常
==========
定义项目中使用的自定义异常
"""


class StrategyError(Exception):
    """策略相关错误"""

    def __init__(self, message: str, code: str = None, details: dict = None):
        """初始化"""
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self):
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class ModelError(Exception):
    """模型相关错误"""

    def __init__(self, message: str, model_name: str = None,
                 error_type: str = None, details: dict = None):
        self.message = message
        self.model_name = model_name
        self.error_type = error_type
        self.details = details or {}
        super().__init__(self.message)

    def __str__(self):
        parts = [self.message]
        if self.model_name:
            parts.append(f"模型: {self.model_name}")
        if self.error_type:
            parts.append(f"错误类型: {self.error_type}")
        return " | ".join(parts)
