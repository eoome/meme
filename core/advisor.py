#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号顾问 — 定时拉数据、跑模型、生成买卖建议、推送提醒

核心循环:
  每 N 分钟 → 读持仓 → 拉行情 → 跑 ML → 算止损 → 推送信号

使用方式:
  from core.advisor import Advisor
  advisor = Advisor()
  advisor.start()           # 后台线程
  advisor.run_once()        # 立即跑一次
  advisor.stop()
"""

import json
import os
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


def _is_macos() -> bool:
    """检测是否为 macOS"""
    import sys
    return sys.platform == 'darwin'

from core.config import get_config
from core.logger import log

# ═══════════════════════════════════════════════════════════════
# 持仓数据缓存管理 — 仅缓存持仓，自选不缓存
# ═══════════════════════════════════════════════════════════════
from data.cache_manager import get_cache_manager

# ═══════════════════════════════════════════════════════════════
# UI 线程安全：advisor 运行在后台线程，所有UI更新通过 Logger 的
# pyqtSignal 转发。本模块增加执行锁防止 UI 线程与后台线程并发。
# ═══════════════════════════════════════════════════════════════

# ─── 路径 ───

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_POSITIONS_FILE = _DATA_DIR / "positions.json"
_ADVISOR_LOG_FILE = _DATA_DIR / "advisor_log.json"

# ─── 默认参数 ───

DEFAULT_INTERVAL_MINUTES = 5       # 扫描间隔
DEFAULT_STOP_LOSS_PCT = 0.03       # 默认止损 3%
DEFAULT_TAKE_PROFIT_PCT = 0.06     # 默认止盈 6%
SIGNAL_COOLDOWN_MINUTES = 30       # 同一股票信号冷却时间
COOLDOWN_CLEANUP_INTERVAL = 24 * 3600  # 冷却字典清理间隔(秒): 每天一次
ADVICE_LOG_MAX_ENTRIES = 200       # 建议日志最大保留条数


@dataclass
class StockAdvice:
    """单只股票的买卖建议"""
    code: str
    name: str
    action: str                    # BUY / SELL / HOLD / STOP_LOSS / TAKE_PROFIT
    confidence: float              # 0-100
    current_price: float
    cost_price: float
    stop_loss_price: float
    take_profit_price: float
    pnl_pct: float                 # 当前盈亏 %
    reason: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        """后初始化 — 自动计算派生字段"""
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class AdvisorStatus:
    """顾问运行状态"""
    running: bool = False
    cycle_count: int = 0
    last_run: str = ""
    last_signal_count: int = 0
    errors: List[str] = field(default_factory=list)


class Advisor:
    """
    信号顾问

    定时扫描持仓股票，生成买卖建议和止损提醒。
    """

    def __init__(
        self,
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        stop_loss_pct: float = DEFAULT_STOP_LOSS_PCT,
        take_profit_pct: float = DEFAULT_TAKE_PROFIT_PCT,
    ) -> None:
        self.interval = interval_minutes * 60
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

        self._status = AdvisorStatus()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # 信号冷却按方向区分: {code: {"BUY": timestamp, "SELL": timestamp}}
        # BUY 和 SELL 独立冷却，避免止损信号被买入冷却阻塞
        self._last_signal_time: Dict[str, Dict[str, str]] = {}
        self._signal_time_lock = threading.Lock()     # 保护 _last_signal_time 的线程安全
        self._advice_log: List[Dict] = []
        self._advice_log_lock = threading.Lock()      # 保护 _advice_log 的线程安全
        self._run_lock = threading.Lock()             # 防止并发执行扫描

        # 内存泄漏防护: 冷却字典定期清理
        self._last_cleanup_time: float = 0.0
        self._chanlun_fail_count: Dict[str, int] = {}  # 缠论失败计数，首次 warning，后续 debug

        # 延迟导入，避免循环依赖
        self._router = None
        self._engine = None
        self._engine_load_attempted = False  # 是否已尝试加载引擎

    # ─── 懒加载依赖 ───

    def _get_router(self):
        if self._router is None:
            from data_sources.router import DataRouter
            self._router = DataRouter()
        return self._router

    def _get_engine(self):
        if self._engine is None and not self._engine_load_attempted:
            self._engine_load_attempted = True
            try:
                from strategies.engine import MLEngine
                self._engine = MLEngine()
            except ImportError as e:
                log.warning("advisor", f"ML 引擎不可用: {e}", "将使用纯规则建议")
        return self._engine

    # ─── 核心循环 ───

    def run_once(self) -> List[StockAdvice]:
        """
        扫描一次，返回所有持仓 + 自选股的建议列表
        线程安全：通过 _run_lock 防止 UI 线程与后台线程并发执行
        """
        if not self._run_lock.acquire(blocking=False):
            log.warning("advisor", "扫描被跳过：上一次扫描尚未完成")
            return []

        try:
            return self._run_once_impl()
        finally:
            self._run_lock.release()

    def _run_once_impl(self) -> List[StockAdvice]:
        """实际扫描逻辑（被 run_once 的锁保护）"""
        advices: List[StockAdvice] = []
        positions = self._load_positions()

        router = self._get_router()
        engine = self._get_engine()  # 可能为 None（ML 不可用）

        # ── 扫描持仓 ──
        if positions:
            log.signal_log("advisor", f"开始扫描 {len(positions)} 只持仓")
            for pos in positions:
                code = pos.get("code", "")
                name = pos.get("name", code)
                cost = float(pos.get("cost", 0))
                volume = int(pos.get("volume", 0))

                if not code or cost <= 0 or volume <= 0:
                    continue

                try:
                    advice = self._analyze_stock(router, engine, code, name, cost)
                    if advice:
                        advices.append(advice)
                        self._handle_advice(advice)
                except Exception as e:
                    err = f"分析 {code}({name}) 失败: {e}"
                    log.warning("advisor", err)
                    self._status.errors.append(err)

        # ── 扫描自选股池 (纯信号模式，无成本价) ──
        watch_count = 0
        watch_signal_count = 0
        try:
            from data.watchlist import load_watchlist, get_watchlist_codes
            watchlist = load_watchlist()
            watch_count = len(watchlist)

            if watch_count == 0:
                log.warning("advisor", "自选池为空，无法扫描自选池",
                            "请在 ETF T+0 页面点击「全量导入」或手动添加 ETF")
            else:
                # 排除已在持仓中的
                hold_codes = {p.get("code") for p in positions}
                watch_items = [w for w in watchlist if w["code"] not in hold_codes]

                if watch_items:
                    log.signal_log("advisor", f"开始扫描 {len(watch_items)} 只自选池")

                    # 批量拉行情，减少请求次数
                    watch_codes = [w["code"] for w in watch_items]
                    batch_realtime = router.get_realtime(watch_codes)

                    if not batch_realtime:
                        log.warning("advisor",
                                    f"无法扫描自选池：批量行情请求返回空 (共 {len(watch_codes)} 只)",
                                    "请检查网络连接或数据源状态")
                    else:
                        for item in watch_items:
                            code = item["code"]
                            name = item.get("name", code)
                            try:
                                advice = self._analyze_watchlist_stock(
                                    router, engine, code, name, batch_realtime
                                )
                                if advice and advice.action != "HOLD":
                                    advices.append(advice)
                                    watch_signal_count += 1
                                    self._handle_advice(advice)
                                elif advice:
                                    # HOLD 信号也保存到日志（但不通知）
                                    advices.append(advice)
                            except Exception as e:
                                log.warning("advisor", f"自选池分析失败 {code}: {e}")
        except Exception as e:
            log.warning("advisor", f"加载自选池失败: {e}",
                        "无法扫描自选池，请检查 data/watchlist.json 文件")

        self._status.cycle_count += 1
        self._status.last_run = datetime.now().isoformat()
        self._status.last_signal_count = len([a for a in advices if a.action != "HOLD"])

        # 保存建议日志
        self._save_advisor_log(advices)

        total = len(positions) if positions else 0
        if watch_count == 0:
            log.signal_log("advisor",
                           f"扫描完成，持仓 {total} + 自选池 0，共 {len(advices)} 只，"
                           f"{self._status.last_signal_count} 个信号",
                           "⚠️ 自选池为空，请先导入 ETF")
        elif watch_count > 0 and watch_signal_count == 0 and watch_count > len(positions):
            log.signal_log("advisor",
                           f"扫描完成，持仓 {total} + 自选池 {watch_count}，共 {len(advices)} 只，"
                           f"{self._status.last_signal_count} 个信号",
                           "⚠️ 自选池未产生信号，请检查行情数据源")
        else:
            log.signal_log("advisor",
                           f"扫描完成，持仓 {total} + 自选池 {watch_count}，共 {len(advices)} 只，"
                           f"{self._status.last_signal_count} 个信号")

        return advices

    def _analyze_stock(
        self,
        router,
        engine,
        code: str,
        name: str,
        cost: float,
    ) -> Optional[StockAdvice]:
        """分析单只股票，生成建议"""

        # 1. 获取实时价格
        realtime = router.get_realtime([code])
        if code not in realtime:
            log.warning("advisor", f"无法获取 {code} 实时行情")
            return None

        current_price = float(realtime[code].get("price", 0))
        if current_price <= 0:
            return None

        # 2. 计算止损/止盈价
        stop_loss_price = round(cost * (1 - self.stop_loss_pct), 3)
        take_profit_price = round(cost * (1 + self.take_profit_pct), 3)
        pnl_pct = (current_price - cost) / cost * 100

        # 3. 检查是否触发止损/止盈
        if current_price <= stop_loss_price:
            return StockAdvice(
                code=code, name=name,
                action="STOP_LOSS",
                confidence=95,
                current_price=current_price,
                cost_price=cost,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                pnl_pct=pnl_pct,
                reason=f"触发止损！当前 {current_price:.2f} ≤ 止损价 {stop_loss_price:.2f}，"
                       f"亏损 {abs(pnl_pct):.1f}%",
            )

        if current_price >= take_profit_price:
            return StockAdvice(
                code=code, name=name,
                action="TAKE_PROFIT",
                confidence=90,
                current_price=current_price,
                cost_price=cost,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price,
                pnl_pct=pnl_pct,
                reason=f"触发止盈！当前 {current_price:.2f} ≥ 止盈价 {take_profit_price:.2f}，"
                       f"盈利 {pnl_pct:.1f}%",
            )

        # 4. 动态调整止损价（跟踪止损：使用 config 中的 trailing_pct）
        #    盈利 > trailing_pct * 2 时，止损上移到最高价下方 trailing_pct 处
        #    保底：盈利超过 initial_stop_pct 时，止损至少到成本价
        cfg = get_config()
        trailing_pct = cfg.stop_loss.trailing_pct   # e.g. 0.02
        initial_pct = cfg.stop_loss.initial_stop_pct  # e.g. 0.03
        stop_type = cfg.stop_loss.type  # "trailing" / "fixed"

        if stop_type == "trailing":
            if pnl_pct > initial_pct * 100:
                # 盈利超过初始止损幅度 → 保本止损
                trailing_stop = cost
                if trailing_stop > stop_loss_price:
                    stop_loss_price = trailing_stop
            if pnl_pct > trailing_pct * 100:
                # 盈利 > trailing% → 跟踪止损 = 当前价 × (1 - trailing%)
                trailing_stop = round(current_price * (1 - trailing_pct), 3)
                if trailing_stop > stop_loss_price:
                    stop_loss_price = trailing_stop

        # 5. ML 引擎分析（如果可用）— 持仓走缓存，自选走网络
        if engine is not None:
            try:
                # 使用缓存管理器获取分钟数据（仅持仓会缓存）
                cache = get_cache_manager()
                minute_data = cache.get_minute_for_backtest(code, router)
                if minute_data and len(minute_data) >= 20:
                    signal = engine.analyze(minute_data=minute_data, code=code, name=name)
                    action_map = {
                        "STRONG_BUY": "BUY", "BUY": "BUY",
                        "HOLD": "HOLD", "SELL": "SELL", "STRONG_SELL": "SELL",
                    }
                    action = action_map.get(signal.signal.value, "HOLD")
                    return StockAdvice(
                        code=code, name=name,
                        action=action,
                        confidence=signal.confidence,
                        current_price=current_price,
                        cost_price=cost,
                        stop_loss_price=stop_loss_price,
                        take_profit_price=take_profit_price,
                        pnl_pct=pnl_pct,
                        reason=f"{signal.reason} | 盈亏 {pnl_pct:+.1f}% | "
                               f"止损 {stop_loss_price:.2f} | 止盈 {take_profit_price:.2f}",
                    )
            except Exception as e:
                log.warning("advisor", f"ML 分析失败 {code}: {e}", "降级规则建议")

        # 6. ML 不可用 — 缠论独立信号作为降级方案（含多级别联立）
        try:
            from strategies.data.chanlun import get_chanlun_signal, get_multi_timeframe_signal
            cache = get_cache_manager()
            minute_data = cache.get_minute_for_backtest(code, router)
            if minute_data and len(minute_data) >= 20:
                import pandas as pd
                df_cl = pd.DataFrame(minute_data)
                df_cl.columns = [c.lower().strip() for c in df_cl.columns]
                if 'open' in df_cl.columns and 'high' in df_cl.columns:
                    # 尝试多级别联立: 日线定方向 + 分钟线找入场
                    cl_sig = None
                    try:
                        daily_data = router.get_kline(code, period='day', count=120)
                        if daily_data and len(daily_data) >= 30:
                            df_daily = pd.DataFrame(daily_data)
                            df_daily.columns = [c.lower().strip() for c in df_daily.columns]
                            cl_sig = get_multi_timeframe_signal(df_daily, df_cl)
                            if cl_sig.get('signal') != 'HOLD':
                                log.signal_log("advisor",
                                    f"多级别共振: {code} {cl_sig['signal']}",
                                    cl_sig.get('reason', '')[:50])
                    except Exception as e:
                        log.debug("advisor", f"多级别缠论信号异常 {code}: {e}")

                    # 降级: 单级别缠论
                    if cl_sig is None or cl_sig.get('signal') == 'HOLD':
                        cl_sig = get_chanlun_signal(df_cl)

                    if cl_sig['signal'] in ('BUY', 'SELL') and cl_sig['confidence'] >= 60:
                        return StockAdvice(
                            code=code, name=name,
                            action=cl_sig['signal'],
                            confidence=cl_sig['confidence'] * 0.8,
                            current_price=current_price,
                            cost_price=cost,
                            stop_loss_price=stop_loss_price,
                            take_profit_price=take_profit_price,
                            pnl_pct=pnl_pct,
                            reason=f"缠论: {cl_sig['reason'][:30]} | 盈亏 {pnl_pct:+.1f}% | "
                                   f"止损 {stop_loss_price:.2f} | 止盈 {take_profit_price:.2f}",
                        )
        except Exception as e:
            # 首次失败 warning，后续 debug — 防止日志刷屏同时确保问题可见
            fail_count = self._chanlun_fail_count.get(code, 0) + 1
            self._chanlun_fail_count[code] = fail_count
            log_func = log.warning if fail_count <= 3 else log.debug
            log_func("advisor", f"缠论分析失败({fail_count}次): {code}", str(e)[:80])

        # 7. ML/缠论 均不可用 → 纯规则回退
        return self._rule_based_advice(code, name, current_price, cost,
                                       stop_loss_price, take_profit_price, pnl_pct)

    def _rule_based_advice(
        self,
        code: str,
        name: str,
        price: float,
        cost: float,
        stop_loss: float,
        take_profit: float,
        pnl_pct: float,
    ) -> StockAdvice:
        """规则回退建议（ML 不可用时）"""
        if pnl_pct > 3.0:
            action = "SELL"
            reason = f"规则建议：盈利 {pnl_pct:.1f}%，考虑落袋为安"
        elif pnl_pct < -2.0:
            action = "SELL"
            reason = f"规则建议：亏损 {abs(pnl_pct):.1f}%，考虑止损"
        else:
            action = "HOLD"
            reason = f"规则建议：盈亏 {pnl_pct:+.1f}%，继续持有"

        return StockAdvice(
            code=code, name=name,
            action=action,
            confidence=60,
            current_price=price,
            cost_price=cost,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            pnl_pct=pnl_pct,
            reason=reason,
        )

    # ─── 自选股池分析 (纯信号模式) ───

    def _analyze_watchlist_stock(
        self,
        router,
        engine,
        code: str,
        name: str,
        batch_realtime: Dict,
    ) -> Optional[StockAdvice]:
        """
        分析自选股池中的股票 — 纯 ML 信号，不涉及持仓/止损/止盈
        """
        if code not in batch_realtime:
            return None

        current_price = float(batch_realtime[code].get("price", 0))
        if current_price <= 0:
            return None

        change_pct = float(batch_realtime[code].get("change_pct", 0))

        # ML 引擎分析
        if engine is not None:
            try:
                cache = get_cache_manager()
                minute_data = cache.get_minute_for_backtest(code, router)
                if minute_data and len(minute_data) >= 20:
                    signal = engine.analyze(minute_data=minute_data, code=code, name=name)
                    action_map = {
                        "STRONG_BUY": "BUY", "BUY": "BUY",
                        "HOLD": "HOLD", "SELL": "SELL", "STRONG_SELL": "SELL",
                    }
                    action = action_map.get(signal.signal.value, "HOLD")
                    return StockAdvice(
                        code=code, name=name,
                        action=action,
                        confidence=signal.confidence,
                        current_price=current_price,
                        cost_price=0,
                        stop_loss_price=0,
                        take_profit_price=0,
                        pnl_pct=change_pct,
                        reason=f"[自选] {signal.reason} | 今日涨跌 {change_pct:+.1f}%",
                    )
            except Exception as e:
                log.warning("advisor", f"自选池 ML 分析失败 {code}: {e}")

        # 无 ML → 缠论信号降级
        try:
            from strategies.data.chanlun import get_chanlun_signal
            cache = get_cache_manager()
            minute_data = cache.get_minute_for_backtest(code, router)
            if minute_data and len(minute_data) >= 20:
                import pandas as pd
                df_cl = pd.DataFrame(minute_data)
                df_cl.columns = [c.lower().strip() for c in df_cl.columns]
                if 'open' in df_cl.columns and 'high' in df_cl.columns:
                    cl_sig = get_chanlun_signal(df_cl)
                    if cl_sig['signal'] in ('BUY', 'SELL') and cl_sig['confidence'] >= 60:
                        return StockAdvice(
                            code=code, name=name,
                            action=cl_sig['signal'],
                            confidence=cl_sig['confidence'] * 0.8,
                            current_price=current_price,
                            cost_price=0,
                            stop_loss_price=0,
                            take_profit_price=0,
                            pnl_pct=change_pct,
                            reason=f"[自选] 缠论: {cl_sig['reason'][:30]} | 今日涨跌 {change_pct:+.1f}%",
                        )
        except Exception as e:
            log.debug("advisor", f"自选池缠论分析失败 {code}: {e}")

        # 无 ML/缠论 → 只返回当前价格信息
        return StockAdvice(
            code=code, name=name,
            action="HOLD",
            confidence=50,
            current_price=current_price,
            cost_price=0,
            stop_loss_price=0,
            take_profit_price=0,
            pnl_pct=change_pct,
            reason=f"[自选] 今日涨跌 {change_pct:+.1f}%",
        )

    # ─── 信号处理 ───

    def _cleanup_expired_cooldown(self) -> None:
        """定期清理过期的冷却记录，防止内存泄漏（线程安全）"""
        now_ts = time.time()
        if now_ts - self._last_cleanup_time < COOLDOWN_CLEANUP_INTERVAL:
            return
        self._last_cleanup_time = now_ts

        cutoff = datetime.now() - timedelta(hours=48)
        with self._signal_time_lock:
            expired_codes = []
            for code, actions in self._last_signal_time.items():
                # 删除过期的时间戳
                expired_actions = []
                for action, ts_str in actions.items():
                    try:
                        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        if ts < cutoff:
                            expired_actions.append(action)
                    except (ValueError, TypeError):
                        expired_actions.append(action)
                for action in expired_actions:
                    del actions[action]
                if not actions:
                    expired_codes.append(code)
            for code in expired_codes:
                del self._last_signal_time[code]
                # 一并清理缠论失败计数器
                self._chanlun_fail_count.pop(code, None)

    def _handle_advice(self, advice: StockAdvice) -> None:
        """处理建议：记录日志 + 推送通知 + 监控统计（线程安全）"""

        # 先清理过期冷却记录
        self._cleanup_expired_cooldown()

        # 冷却检查 — 按方向独立冷却（BUY/SELL互不阻塞）
        if advice.action == "HOLD":
            return

        now = datetime.now()
        with self._signal_time_lock:
            code_record = self._last_signal_time.get(advice.code, {})
            last_time = code_record.get(advice.action)
            if last_time:
                try:
                    last_dt = datetime.strptime(last_time, "%Y-%m-%d %H:%M:%S")
                    if (now - last_dt).total_seconds() < SIGNAL_COOLDOWN_MINUTES * 60:
                        return  # 该方向还在冷却期
                except ValueError:
                    pass

            # 按方向记录冷却时间
            if advice.code not in self._last_signal_time:
                self._last_signal_time[advice.code] = {}
            self._last_signal_time[advice.code][advice.action] = advice.timestamp

        # ═══════════════════════════════════════════════════
        # 记录到信号监控器（用于当日统计面板）
        # ═══════════════════════════════════════════════════
        try:
            from strategies.monitor import record_signal
            record_signal(
                code=advice.code,
                name=advice.name,
                action=advice.action,
                confidence=advice.confidence,
                pnl_pct=advice.pnl_pct,
                reason=advice.reason[:50],
            )
        except Exception as e:
            log.debug("advisor", f"操作失败: {e}")  # 监控记录失败不影响主流程

        # 写入自定义 Logger（UI 面板实时显示）
        level_map = {
            "BUY": "strategy", "SELL": "strategy",
            "STOP_LOSS": "strategy", "TAKE_PROFIT": "strategy",
        }
        category = level_map.get(advice.action, "strategy")

        icon_map = {"BUY": "🟢", "SELL": "🔴", "STOP_LOSS": "🛑", "TAKE_PROFIT": "🎯"}
        icon = icon_map.get(advice.action, "⚪")

        log.signal_log(category, f"{icon} {advice.code} {advice.name}", advice.reason)

        # 桌面通知
        self._desktop_notify(advice)

    def _desktop_notify(self, advice: StockAdvice) -> None:
        """桌面通知（跨平台，无桌面环境自动跳过）"""
        try:
            title = f"{advice.action}: {advice.name} ({advice.code})"
            body = (f"当前价: {advice.current_price:.2f}\n"
                    f"成本价: {advice.cost_price:.2f}\n"
                    f"盈亏: {advice.pnl_pct:+.1f}%\n"
                    f"止损: {advice.stop_loss_price:.2f}\n"
                    f"止盈: {advice.take_profit_price:.2f}\n"
                    f"{advice.reason}")

            import subprocess

            # 检测是否有桌面环境
            has_display = bool(
                os.environ.get('DISPLAY') or
                os.environ.get('WAYLAND_DISPLAY') or
                os.environ.get('XDG_SESSION_TYPE') == 'x11'
            )

            if not has_display and not _is_macos():
                # 无桌面环境，直接写日志
                log.signal_log("advisor", f"通知(无桌面): {title}", body)
                return

            if _is_macos():
                # macOS: osascript（转义特殊字符防注入）
                safe_body = body.replace('\\', '\\\\').replace('"', '\\"')
                safe_title = title.replace('\\', '\\\\').replace('"', '\\"')
                script = f'display notification "{safe_body}" with title "{safe_title}"'
                subprocess.Popen(["osascript", "-e", script],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return

            # Linux: notify-send
            try:
                subprocess.Popen(
                    ["notify-send", "-u", "critical", title, body],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except FileNotFoundError:
                pass

            # 回退：写入日志
            log.signal_log("advisor", f"通知: {title}", body)

        except Exception as e:
            log.debug("advisor", f"操作失败: {e}")  # 通知失败不影响主流程

    # ─── 数据加载 ───

    def _load_positions(self) -> List[Dict]:
        """
        加载持仓数据，按代码合并同一只股票的多次买入
        返回: 每个 code 只有一条记录，volume 求和，cost 取加权均价
        """
        raw = []
        if _POSITIONS_FILE.exists():
            try:
                content = _POSITIONS_FILE.read_text("utf-8")
                if not content.strip():
                    log.warning("advisor", "持仓文件为空，将创建新文件",
                                f"文件路径: {_POSITIONS_FILE}")
                    return []
                raw = json.loads(content)
            except json.JSONDecodeError as e:
                log.error("advisor", f"持仓文件格式损坏(非有效JSON): {e}",
                          f"请检查或删除 {_POSITIONS_FILE} 后重启程序")
                return []
            except Exception as e:
                log.error("advisor", f"加载持仓失败: {e}")
                return []

        if not raw:
            return []

        # 按代码合并
        merged: Dict[str, Dict] = {}
        for pos in raw:
            code = pos.get("code", "")
            if not code:
                continue

            volume = int(pos.get("volume", 0))
            cost = float(pos.get("cost", 0))
            name = pos.get("name", code)

            if code not in merged:
                merged[code] = {
                    "code": code,
                    "name": name,
                    "total_cost_amount": cost * volume,  # 总成本金额
                    "total_volume": volume,
                    "cost": cost,  # 占位，后面算均价
                    "volume": volume,
                }
            else:
                m = merged[code]
                m["total_cost_amount"] += cost * volume
                m["total_volume"] += volume
                m["name"] = name  # 用最新一条的名称

        # 计算加权均价
        result = []
        for code, m in merged.items():
            if m["total_volume"] > 0:
                m["cost"] = round(m["total_cost_amount"] / m["total_volume"], 4)
            m["volume"] = m["total_volume"]
            del m["total_cost_amount"]
            del m["total_volume"]
            result.append(m)

        return result

    def _save_advisor_log(self, advices: List[StockAdvice]) -> None:
        """保存顾问建议日志（自动截断防内存泄漏，线程安全）"""
        with self._advice_log_lock:
            self._advice_log.extend([
                {
                    "code": a.code, "name": a.name, "action": a.action,
                    "confidence": a.confidence, "price": a.current_price,
                    "cost": a.cost_price, "pnl_pct": round(a.pnl_pct, 2),
                    "stop_loss": a.stop_loss_price, "take_profit": a.take_profit_price,
                    "reason": a.reason, "time": a.timestamp,
                }
                for a in advices
            ])
            # 严格限制内存占用，只保留最近 N 条
            if len(self._advice_log) > ADVICE_LOG_MAX_ENTRIES:
                self._advice_log = self._advice_log[-ADVICE_LOG_MAX_ENTRIES:]
            log_data = list(self._advice_log)  # 复制一份用于文件写入
        try:
            _ADVISOR_LOG_FILE.write_text(
                json.dumps(log_data, ensure_ascii=False, indent=2)
            )
        except Exception as e:
            log.debug("advisor", f"操作失败: {e}")

    # ─── 后台线程 ───

    @staticmethod
    def _is_trading_time() -> bool:
        """判断当前是否为 A 股交易时段"""
        from datetime import time as dtime
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        t = now.time()
        morning = dtime(9, 30) <= t <= dtime(11, 30)
        afternoon = dtime(13, 0) <= t <= dtime(15, 0)
        return morning or afternoon

    def _loop(self) -> None:
        """后台循环 — 启动时立即扫一次，之后仅在交易时段执行"""
        log.signal_log("advisor", f"顾问已启动，间隔 {self.interval // 60} 分钟")
        self._status.running = True

        # 启动后立即扫一次（无论是否交易时段，让用户看到系统在工作）
        try:
            self.run_once()
        except Exception as e:
            log.error("advisor", f"初始扫描异常: {e}")

        while not self._stop_event.is_set():
            if self._is_trading_time():
                try:
                    self.run_once()
                except Exception as e:
                    log.error("advisor", f"扫描异常: {e}")
                    self._status.errors.append(str(e))
            else:
                pass  # 非交易时段，静默跳过

            # 等待下次扫描（可中断）
            self._stop_event.wait(timeout=self.interval)

        self._status.running = False
        log.signal_log("advisor", "顾问已停止")

    def start(self) -> None:
        """启动后台线程"""
        if self._thread and self._thread.is_alive():
            log.warning("advisor", "顾问已在运行")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止后台线程 — 安全退出，等待线程结束"""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15)  # 最多等待15秒
            if self._thread.is_alive():
                log.warning("advisor", "线程未在超时内结束，强制继续")
            else:
                log.signal_log("advisor", "顾问线程已安全停止")
        self._thread = None

    def scan_now(self) -> None:
        """立即执行一次扫描（忽略交易时段限制，在后台线程执行）"""
        threading.Thread(target=self._scan_now_impl, daemon=True).start()

    def _scan_now_impl(self) -> None:
        """立即扫描的实现"""
        try:
            advices = self.run_once()
            if advices:
                actionable = [a for a in advices if a.action != "HOLD"]
                if actionable:
                    log.signal_log("advisor", f"即时扫描完成: {len(advices)} 只, {len(actionable)} 个信号")
                else:
                    log.signal_log("advisor", f"即时扫描完成: {len(advices)} 只, 全部 HOLD")
            else:
                log.signal_log("advisor", "即时扫描完成: 无持仓或数据")
        except Exception as e:
            log.warning("advisor", f"即时扫描异常: {e}")

    # ─── 状态查询 ───

    @property
    def status(self) -> AdvisorStatus:
        return self._status

    def get_recent_advice(self, count: int = 20) -> List[Dict]:
        """获取最近的建议记录（线程安全）"""
        with self._advice_log_lock:
            return list(self._advice_log[-count:])


# ─── 全局单例 ───

_advisor: Optional[Advisor] = None


def get_advisor() -> Advisor:
    """获取全局 Advisor 单例（懒加载）"""
    global _advisor
    if _advisor is None:
        cfg = get_config()
        _advisor = Advisor(
            interval_minutes=5,
            stop_loss_pct=cfg.stop_loss.initial_stop_pct,
            take_profit_pct=cfg.stop_loss.take_profit_pct,
        )
    return _advisor


def start_advisor() -> None:
    """启动信号顾问后台线程（供 main.py 调用）"""
    advisor = get_advisor()
    advisor.start()
    log.signal_log("system", "信号顾问已启动", f"扫描间隔 {advisor.interval // 60} 分钟")


def stop_advisor() -> None:
    """停止信号顾问（供程序退出时调用）"""
    global _advisor
    if _advisor is not None:
        _advisor.stop()
        _advisor = None