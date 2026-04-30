#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统健康检查
============
检查系统各组件状态
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd
import numpy as np


@dataclass
class ComponentStatus:
    """组件状态"""
    name: str
    status: str  # ok, warning, error
    message: str
    details: Dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class SystemStatus:
    """系统状态"""
    overall_status: str
    components: List[ComponentStatus]
    timestamp: str
    uptime_seconds: float
    version: str = "2.1.0"


class HealthChecker:
    """
    系统健康检查器
    
    检查项:
    1. 数据目录和文件
    2. 模型文件
    3. 依赖包
    4. 数据源连接
    5. 内存和性能
    """
    
    def __init__(self):
        """初始化"""
        self.start_time = time.time()
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.data_dir = os.path.join(self.base_dir, "data")
        
    def check_all(self) -> SystemStatus:
        """执行所有检查"""
        components = []
        
        # 检查数据目录
        components.append(self._check_data_directory())
        
        # 检查模型文件
        components.append(self._check_model_files())
        
        # 检查依赖包
        components.append(self._check_dependencies())
        
        # 检查数据源
        components.append(self._check_data_sources())
        
        # 检查持仓数据
        components.append(self._check_positions())
        
        # 检查K线数据
        components.append(self._check_kline_data())
        
        # 确定整体状态
        error_count = sum(1 for c in components if c.status == 'error')
        warning_count = sum(1 for c in components if c.status == 'warning')
        
        if error_count > 0:
            overall = 'error'
        elif warning_count > 0:
            overall = 'warning'
        else:
            overall = 'ok'
        
        return SystemStatus(
            overall_status=overall,
            components=components,
            timestamp=datetime.now().isoformat(),
            uptime_seconds=time.time() - self.start_time
        )
    
    def _check_data_directory(self) -> ComponentStatus:
        """检查数据目录"""
        required_dirs = [
            'klines',
            'labeled',
            'ml/models',
            'backtest_results'
        ]
        
        missing = []
        for dir_name in required_dirs:
            dir_path = os.path.join(self.data_dir, dir_name)
            if not os.path.exists(dir_path):
                missing.append(dir_name)
                os.makedirs(dir_path, exist_ok=True)
        
        if missing:
            return ComponentStatus(
                name='数据目录',
                status='warning',
                message=f'已创建缺失目录: {", ".join(missing)}',
                details={'created': missing}
            )
        
        # 检查磁盘空间
        try:
            import shutil
            total, used, free = shutil.disk_usage(self.data_dir)
            free_gb = free / (1024**3)
            
            if free_gb < 1:
                return ComponentStatus(
                    name='数据目录',
                    status='warning',
                    message=f'磁盘空间不足: 剩余 {free_gb:.1f}GB',
                    details={'free_gb': free_gb}
                )
        except OSError as e:
            import logging
            logging.getLogger(__name__).debug(f"健康检查失败: {e}")
        
        return ComponentStatus(
            name='数据目录',
            status='ok',
            message='数据目录正常',
            details={'path': self.data_dir}
        )
    
    def _check_model_files(self) -> ComponentStatus:
        """检查模型文件"""
        model_dir = os.path.join(self.data_dir, "ml", "models")
        
        if not os.path.exists(model_dir):
            return ComponentStatus(
                name='模型文件',
                status='warning',
                message='模型目录不存在',
                details={'path': model_dir}
            )
        
        model_files = list(Path(model_dir).glob("model_v*.pkl")) + list(Path(model_dir).glob("model_v*.joblib"))
        
        if not model_files:
            return ComponentStatus(
                name='模型文件',
                status='warning',
                message='未找到训练好的模型，将使用规则回退',
                details={'model_dir': model_dir}
            )
        
        latest = max(model_files, key=lambda x: x.stat().st_mtime)
        
        # 检查模型是否过期 (超过30天)
        age_days = (time.time() - latest.stat().st_mtime) / (24 * 3600)
        
        if age_days > 30:
            return ComponentStatus(
                name='模型文件',
                status='warning',
                message=f'模型已过期 ({age_days:.0f}天)，建议重新训练',
                details={
                    'latest_model': str(latest),
                    'age_days': age_days,
                    'model_count': len(model_files)
                }
            )
        
        return ComponentStatus(
            name='模型文件',
            status='ok',
            message=f'模型正常: {latest.name}',
            details={
                'latest_model': str(latest),
                'age_days': age_days,
                'model_count': len(model_files)
            }
        )
    
    def _check_dependencies(self) -> ComponentStatus:
        """检查依赖包"""
        required = {
            'pandas': '数据处理',
            'numpy': '数值计算',
            'PyQt5': 'UI框架',
        }
        
        optional = {
            'lightgbm': 'ML模型',
            'sklearn': '机器学习',
            'akshare': '数据源',
            'optuna': '参数优化',
        }
        
        missing_required = []
        missing_optional = []
        
        for pkg, desc in required.items():
            try:
                __import__(pkg)
            except ImportError:
                missing_required.append(f"{pkg} ({desc})")
        
        for pkg, desc in optional.items():
            try:
                __import__(pkg)
            except ImportError:
                missing_optional.append(f"{pkg} ({desc})")
        
        if missing_required:
            return ComponentStatus(
                name='依赖包',
                status='error',
                message=f'缺少必要依赖: {", ".join(missing_required)}',
                details={'missing_required': missing_required}
            )
        
        if missing_optional:
            return ComponentStatus(
                name='依赖包',
                status='warning',
                message=f'缺少可选依赖: {", ".join(missing_optional)}',
                details={'missing_optional': missing_optional}
            )
        
        return ComponentStatus(
            name='依赖包',
            status='ok',
            message='所有依赖包已安装'
        )
    
    def _check_data_sources(self) -> ComponentStatus:
        """检查数据源连接"""
        try:
            sys.path.insert(0, self.base_dir)
            from data_sources.router import DataRouter
            
            router = DataRouter()
            status = router.get_source_status()
            
            available = [k for k, v in status.items() if v]
            
            if len(available) == 0:
                return ComponentStatus(
                    name='数据源',
                    status='error',
                    message='无可用数据源',
                    details=status
                )
            
            if len(available) < 2:
                return ComponentStatus(
                    name='数据源',
                    status='warning',
                    message=f'可用数据源较少: {", ".join(available)}',
                    details=status
                )
            
            return ComponentStatus(
                name='数据源',
                status='ok',
                message=f'可用数据源: {", ".join(available)}',
                details=status
            )
        except Exception as e:
            return ComponentStatus(
                name='数据源',
                status='error',
                message=f'检查失败: {str(e)}'
            )
    
    def _check_positions(self) -> ComponentStatus:
        """检查持仓数据"""
        positions_file = os.path.join(self.data_dir, "positions.json")
        
        if not os.path.exists(positions_file):
            return ComponentStatus(
                name='持仓数据',
                status='warning',
                message='持仓文件不存在',
                details={'path': positions_file}
            )
        
        try:
            with open(positions_file, 'r', encoding='utf-8') as f:
                positions = json.load(f)
            
            if not positions:
                return ComponentStatus(
                    name='持仓数据',
                    status='warning',
                    message='持仓为空',
                    details={'count': 0}
                )
            
            # 检查持仓有效性
            invalid = []
            for i, pos in enumerate(positions):
                if 'code' not in pos or 'volume' not in pos or 'cost' not in pos:
                    invalid.append(i)
            
            if invalid:
                return ComponentStatus(
                    name='持仓数据',
                    status='warning',
                    message=f'有 {len(invalid)} 条无效持仓记录',
                    details={'total': len(positions), 'invalid': len(invalid)}
                )
            
            return ComponentStatus(
                name='持仓数据',
                status='ok',
                message=f'持仓正常: {len(positions)} 只股票',
                details={'count': len(positions)}
            )
        except Exception as e:
            return ComponentStatus(
                name='持仓数据',
                status='error',
                message=f'读取失败: {str(e)}'
            )
    
    def _check_kline_data(self) -> ComponentStatus:
        """检查K线数据"""
        klines_dir = os.path.join(self.data_dir, "klines")
        
        if not os.path.exists(klines_dir):
            return ComponentStatus(
                name='K线数据',
                status='warning',
                message='K线目录不存在',
                details={'path': klines_dir}
            )
        
        csv_files = list(Path(klines_dir).glob("*.csv"))
        
        if not csv_files:
            return ComponentStatus(
                name='K线数据',
                status='warning',
                message='无K线数据',
                details={'path': klines_dir}
            )
        
        # 检查数据质量
        stale_files = []
        for f in csv_files:
            age_days = (time.time() - f.stat().st_mtime) / (24 * 3600)
            if age_days > 7:
                stale_files.append((f.name, age_days))
        
        if stale_files:
            return ComponentStatus(
                name='K线数据',
                status='warning',
                message=f'有 {len(stale_files)} 个文件超过7天未更新',
                details={
                    'total_files': len(csv_files),
                    'stale_files': stale_files[:5]  # 只显示前5个
                }
            )
        
        return ComponentStatus(
            name='K线数据',
            status='ok',
            message=f'K线数据正常: {len(csv_files)} 个文件',
            details={'count': len(csv_files)}
        )
    
    def print_report(self, status: SystemStatus):
        """打印健康报告"""
        print("\n" + "="*70)
        print("系统健康检查报告")
        print("="*70)
        print(f"检查时间: {status.timestamp}")
        print(f"运行时间: {status.uptime_seconds:.0f}秒")
        print(f"系统版本: {status.version}")
        print(f"整体状态: {self._status_emoji(status.overall_status)} {status.overall_status.upper()}")
        print("-"*70)
        
        for comp in status.components:
            emoji = self._status_emoji(comp.status)
            print(f"\n{emoji} {comp.name}: {comp.status.upper()}")
            print(f"   消息: {comp.message}")
            if comp.details:
                for key, value in comp.details.items():
                    if isinstance(value, list) and len(value) > 5:
                        print(f"   {key}: {value[:5]}... (共{len(value)}项)")
                    else:
                        print(f"   {key}: {value}")
        
        print("="*70)
    
    def _status_emoji(self, status: str) -> str:
        """状态表情"""
        return {'ok': '✅', 'warning': '⚠️', 'error': '❌'}.get(status, '❓')
    
    def save_report(self, status: SystemStatus, output_file: str = None):
        """保存健康报告"""
        if output_file is None:
            output_file = os.path.join(self.data_dir, "health_report.json")
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(asdict(status), f, ensure_ascii=False, indent=2)
        
        print(f"[HealthCheck] 报告已保存: {output_file}")


# 便捷函数
def check_system() -> SystemStatus:
    """检查系统健康"""
    checker = HealthChecker()
    return checker.check_all()


def print_health_report():
    """打印健康报告"""
    checker = HealthChecker()
    status = checker.check_all()
    checker.print_report(status)


if __name__ == "__main__":
    print("系统健康检查")
    print("="*60)
    
    print_health_report()
