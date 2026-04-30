#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全局配置
========

集中管理所有硬编码参数，避免散落各处。
支持 config.yaml / 环境变量覆盖。
"""

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class HttpConfig:
    """HTTP 请求配置"""
    timeout: int = 8
    retries: int = 2
    retry_delay: float = 0.5
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"


@dataclass
class MLConfig:
    """ML 策略配置"""
    enabled: bool = True
    confidence_threshold: float = 0.7
    strong_threshold: float = 0.85
    lookback: int = 20
    n_estimators: int = 200
    learning_rate: float = 0.05
    max_depth: int = 5


@dataclass
class BacktestConfig:
    """回测配置"""
    initial_capital: float = 1_000_000
    base_position: int = 10_000
    trade_unit: int = 100
    commission_rate: float = 0.00015
    stamp_tax: float = 0.001
    # 滑点区分：ETF 流动性好滑点更低，股票滑点更高
    slippage_etf: float = 0.0002     # 0.02% — ETF 流动性好
    slippage_stock: float = 0.0005   # 0.05% — 股票滑点更大
    slippage: float = 0.0            # 自动计算（deprecated，保留兼容）
    market_type: str = "etf"         # etf(T+0) 或 stock(T+1)
    max_position_multiplier: float = 2.0
    tail_no_trade_minutes: int = 5
    min_trade_interval: int = 5
    min_signal_confidence: float = 70.0


@dataclass
class StopLossConfig:
    """止损配置"""
    type: str = "trailing"
    initial_stop_pct: float = 0.03   # 初始止损 3%
    trailing_pct: float = 0.02       # 跟踪回撤 2%
    take_profit_pct: float = 0.06    # 止盈 6%


@dataclass
class LabelConfig:
    """标注配置"""
    lookback: int = 3
    min_return: float = 0.008
    max_hold_bars: int = 20
    min_spacing: int = 5
    noise_filter: float = 0.003
    max_drawdown: float = 0.03       # 入场后最大回撤阈值 3%


@dataclass
class DataConfig:
    """数据采集配置"""
    kline_count: int = 800           # 训练拉取日K条数 (800条 ≈ 3.3年, 最低500)
    kline_period: str = "day"        # K线周期: day/week/month
    fetch_delay: float = 0.3         # 请求间隔(秒), 防限频
    auto_train: bool = True          # 添加/删除持仓后自动触发增量训练
    auto_train_delay: int = 10       # 自动训练防抖延迟(秒), 操作后等这么久再训练


@dataclass
class ChanLunConfig:
    """缠论参数配置"""
    bi_min_gap: int = 3              # 笔内最少K线间隔（经典=5，调小捕捉更多小波动）
    bi_strict: bool = False          # 严格模式关闭，保留更多笔
    bi_min_bars: int = 2             # 严格模式: 笔内最少独立K线
    seg_min_strokes: int = 3         # 线段最少笔数
    divergence_rate: float = 0.8     # MACD背驰阈值
    volume_divergence: bool = True   # 成交量背驰
    hub_window: int = 9              # 中枢扩展最大笔数


@dataclass
class AppConfig:
    """应用总配置"""
    http: HttpConfig = field(default_factory=HttpConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    stop_loss: StopLossConfig = field(default_factory=StopLossConfig)
    label: LabelConfig = field(default_factory=LabelConfig)
    data: DataConfig = field(default_factory=DataConfig)
    chanlun: ChanLunConfig = field(default_factory=ChanLunConfig)

    # 路径 — 指向项目根目录（core/ 的上一级）
    data_dir: str = str(Path(__file__).resolve().parent.parent / "data")
    model_dir: str = ""

    def __post_init__(self):
        """后初始化 — 自动计算派生字段"""
        if not self.model_dir:
            self.model_dir = os.path.join(self.data_dir, "ml", "models")


# 全局单例
_config: Optional[AppConfig] = None
_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.yaml"
_config_mtime: float = 0.0  # 配置文件最后修改时间（用于热重载）


def _config_file_modified() -> bool:
    """检查 config.yaml 是否被修改"""
    global _config_mtime
    try:
        current_mtime = _CONFIG_FILE.stat().st_mtime
        if current_mtime > _config_mtime:
            _config_mtime = current_mtime
            return True
    except (OSError, FileNotFoundError):
        pass
    return False


def _invalidate_config() -> None:
    """使配置缓存失效，下次 get_config() 将重新加载"""
    global _config
    _config = None


def _load_yaml_overrides() -> Dict:
    """从 config.yaml 加载覆盖参数"""
    if not _CONFIG_FILE.exists():
        return {}
    try:
        import yaml
        with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # 无 PyYAML，尝试简单解析
        try:
            raw = {}
            with open(_CONFIG_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and ':' in line:
                        key, _, val = line.partition(':')
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        try:
                            val = json.loads(val)
                        except (json.JSONDecodeError, ValueError):
                            pass
                        raw[key] = val
            return raw
        except Exception as e:
            logging.getLogger(__name__).debug(f"配置加载回退: {e}")
            return {}
    except Exception as e:
        logging.getLogger(__name__).debug(f"配置加载回退: {e}")
        return {}


def _deep_apply(cfg: AppConfig, overrides: Dict) -> None:
    """递归应用 YAML 覆盖参数到 AppConfig（含类型校验）"""
    for key, value in overrides.items():
        if key == 'http' and isinstance(value, dict):
            for k, v in value.items():
                if hasattr(cfg.http, k):
                    _safe_setattr(cfg.http, k, v)
        elif key == 'ml' and isinstance(value, dict):
            for k, v in value.items():
                if hasattr(cfg.ml, k):
                    _safe_setattr(cfg.ml, k, v)
        elif key == 'backtest' and isinstance(value, dict):
            for k, v in value.items():
                if hasattr(cfg.backtest, k):
                    _safe_setattr(cfg.backtest, k, v)
        elif key == 'stop_loss' and isinstance(value, dict):
            for k, v in value.items():
                if hasattr(cfg.stop_loss, k):
                    _safe_setattr(cfg.stop_loss, k, v)
        elif key == 'label' and isinstance(value, dict):
            for k, v in value.items():
                if hasattr(cfg.label, k):
                    _safe_setattr(cfg.label, k, v)
        elif key == 'data' and isinstance(value, dict):
            for k, v in value.items():
                if hasattr(cfg.data, k):
                    _safe_setattr(cfg.data, k, v)
        elif key == 'chanlun' and isinstance(value, dict):
            for k, v in value.items():
                if hasattr(cfg.chanlun, k):
                    _safe_setattr(cfg.chanlun, k, v)
        elif hasattr(cfg, key):
            _safe_setattr(cfg, key, value)


def _safe_setattr(obj, key: str, value) -> None:
    """安全 setattr：类型不匹配时尝试转换，失败则跳过并警告"""
    existing = getattr(obj, key, None)
    if existing is not None and not isinstance(value, type(existing)):
        try:
            value = type(existing)(value)
        except (ValueError, TypeError) as e:
            logging.getLogger(__name__).warning(
                f"配置类型不匹配: {key} 期望 {type(existing).__name__}, "
                f"得到 {type(value).__name__}={value}, 跳过: {e}"
            )
            return
    setattr(obj, key, value)


def get_config(force_reload: bool = False) -> AppConfig:
    """
    获取全局配置 (首次加载时读取 config.yaml)
    
    Args:
        force_reload: 强制重新加载配置文件
    
    支持热重载：检测到 config.yaml 修改后自动重新加载
    """
    global _config
    if _config is None or force_reload or _config_file_modified():
        _config = AppConfig()
        overrides = _load_yaml_overrides()
        if overrides:
            _deep_apply(_config, overrides)
    return _config
