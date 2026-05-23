#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
策略信号实时监控模块
===================
记录当日信号并生成统计报告，支持持久化存储。

使用方式:
    from strategies.monitor import SignalMonitor, get_monitor
    
    # 在信号生成时记录
    monitor = get_monitor()
    monitor.record_signal(code, name, action, confidence, pnl)
    
    # 获取统计
    stats = monitor.get_daily_stats()
    print(f"今日信号: {stats['total_signals']}, 胜率: {stats['win_rate']:.1%}")
"""

import json
import logging
import threading
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class SignalRecord:
    """单条信号记录"""
    code: str
    name: str
    action: str          # BUY / SELL / HOLD / STOP_LOSS / TAKE_PROFIT
    confidence: float
    pnl_pct: float       # 当时盈亏
    timestamp: str
    reason: str = ""


@dataclass
class DailyStats:
    """当日统计"""
    date: str
    total_signals: int = 0
    buy_signals: int = 0
    sell_signals: int = 0
    hold_signals: int = 0
    stop_loss_signals: int = 0
    take_profit_signals: int = 0
    
    # 盈亏统计
    avg_pnl: float = 0.0
    max_pnl: float = 0.0
    min_pnl: float = 0.0
    total_pnl: float = 0.0
    
    # 信号质量
    avg_confidence: float = 0.0
    win_signals: int = 0  # 盈利信号数 (pnl > 0 的 SELL/TAKE_PROFIT)


class SignalMonitor:
    """信号监控器 — 线程安全"""
    
    def __init__(self, data_dir: Path = None):
        """初始化"""
        self._data_dir = data_dir or Path(__file__).resolve().parent.parent / "data" / "monitor"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        
        self._records: List[SignalRecord] = []
        self._lock = threading.Lock()
        self._today = date.today().isoformat()
        
        # 加载今日已有记录
        self._load_today()
    
    def record_signal(self, code: str, name: str, action: str, 
                      confidence: float = 0, pnl_pct: float = 0,
                      reason: str = "") -> None:
        """
        记录一条信号
        
        Args:
            code: 股票代码
            name: 股票名称
            action: 信号类型 BUY/SELL/HOLD/STOP_LOSS/TAKE_PROFIT
            confidence: 置信度 0-100
            pnl_pct: 当时盈亏百分比
            reason: 信号原因
        """
        record = SignalRecord(
            code=code,
            name=name,
            action=action.upper(),
            confidence=confidence,
            pnl_pct=pnl_pct,
            timestamp=datetime.now().strftime("%H:%M:%S"),
            reason=reason,
        )
        
        with self._lock:
            # 检查是否是新的一天
            current_date = date.today().isoformat()
            if current_date != self._today:
                self._save_day(self._today)
                self._records = []
                self._today = current_date
            
            self._records.append(record)
            # 每10条自动保存
            if len(self._records) % 10 == 0:
                self._save_day(self._today)
    
    def get_daily_stats(self) -> Dict:
        """
        获取当日信号统计
        
        Returns:
            dict: 包含 total_signals, buy_signals, sell_signals, 
                  win_rate, avg_confidence, avg_pnl 等
        """
        with self._lock:
            records = list(self._records)
        
        if not records:
            return {
                'date': date.today().isoformat(),
                'total_signals': 0,
                'buy_signals': 0,
                'sell_signals': 0,
                'hold_signals': 0,
                'stop_loss_signals': 0,
                'take_profit_signals': 0,
                'win_rate': 0.0,
                'avg_confidence': 0.0,
                'avg_pnl': 0.0,
                'total_pnl': 0.0,
                'max_pnl': 0.0,
                'min_pnl': 0.0,
                'recent_signals': [],
            }
        
        buys = sum(1 for r in records if r.action == 'BUY')
        sells = sum(1 for r in records if r.action == 'SELL')
        holds = sum(1 for r in records if r.action == 'HOLD')
        stop_losses = sum(1 for r in records if r.action == 'STOP_LOSS')
        take_profits = sum(1 for r in records if r.action == 'TAKE_PROFIT')
        
        # 盈亏统计（只对卖出类信号计算胜率）
        exit_signals = [r for r in records if r.action in ('SELL', 'TAKE_PROFIT', 'STOP_LOSS')]
        if exit_signals:
            wins = sum(1 for r in exit_signals if r.pnl_pct > 0)
            win_rate = wins / len(exit_signals)
            avg_pnl = sum(r.pnl_pct for r in exit_signals) / len(exit_signals)
            max_pnl = max(r.pnl_pct for r in exit_signals)
            min_pnl = min(r.pnl_pct for r in exit_signals)
        else:
            win_rate = 0.0
            avg_pnl = 0.0
            max_pnl = 0.0
            min_pnl = 0.0
        
        total_pnl = sum(r.pnl_pct for r in records if r.action in ('SELL', 'TAKE_PROFIT'))
        
        # 置信度
        non_hold = [r for r in records if r.action != 'HOLD']
        avg_conf = sum(r.confidence for r in non_hold) / len(non_hold) if non_hold else 0
        
        return {
            'date': date.today().isoformat(),
            'total_signals': len(records),
            'buy_signals': buys,
            'sell_signals': sells,
            'hold_signals': holds,
            'stop_loss_signals': stop_losses,
            'take_profit_signals': take_profits,
            'win_rate': win_rate,
            'avg_confidence': avg_conf,
            'avg_pnl': avg_pnl,
            'total_pnl': total_pnl,
            'max_pnl': max_pnl,
            'min_pnl': min_pnl,
            'recent_signals': [asdict(r) for r in records[-20:]],
        }
    
    def get_recent_signals(self, count: int = 10) -> List[Dict]:
        """获取最近N条信号"""
        with self._lock:
            return [asdict(r) for r in self._records[-count:]]
    
    def clear_today(self) -> None:
        """清空今日记录"""
        with self._lock:
            self._records = []
        self._save_day(date.today().isoformat())
    
    # ─── 持久化 ───
    
    def _save_day(self, day: str) -> None:
        """保存某日记录到文件"""
        path = self._data_dir / f"signals_{day}.json"
        with self._lock:
            data = [asdict(r) for r in self._records]
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.debug(f"监控操作失败: {e}")
    
    def _load_today(self) -> None:
        """加载今日记录"""
        path = self._data_dir / f"signals_{self._today}.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._records = [SignalRecord(**r) for r in data]
        except Exception as e:
            logger.debug(f"监控操作失败: {e}")
    
    def load_history(self, day: str) -> List[Dict]:
        """加载某日的历史记录"""
        path = self._data_dir / f"signals_{day}.json"
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text())
        except Exception:
            return []


# ─── 全局单例 ───

_monitor_instance: Optional[SignalMonitor] = None
_monitor_lock = threading.Lock()


def get_monitor() -> SignalMonitor:
    """获取全局监控器实例（线程安全）"""
    global _monitor_instance
    if _monitor_instance is None:
        with _monitor_lock:
            if _monitor_instance is None:
                _monitor_instance = SignalMonitor()
    return _monitor_instance


def record_signal(code: str, name: str, action: str, 
                  confidence: float = 0, pnl_pct: float = 0,
                  reason: str = "") -> None:
    """便捷函数：记录信号到全局监控器"""
    get_monitor().record_signal(code, name, action, confidence, pnl_pct, reason)
