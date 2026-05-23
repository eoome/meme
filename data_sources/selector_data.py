#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
选股数据源 - 全市场股票筛选
==========================

对接东方财富/腾讯/AkShare获取全市场实时行情，
支持按技术面条件筛选。
"""

import json
import time
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 东方财富全市场行情（选股核心数据源）
# ═══════════════════════════════════════════════════════════════

def _safe_float(val, default=0.0):
    """安全转换float，处理 '-' 和 None"""
    if val is None or val == '-' or val == '':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def fetch_all_stocks_eastmoney(page_size: int = 5000) -> List[dict]:
    """
    东方财富全A股实时行情快照
    返回字段: code, name, price, change_pct, volume, amount,
              turnover_rate, amplitude, volume_ratio, high, low, open, pre_close
    """
    import requests

    all_stocks = []
    markets = [
        ("1.2", "沪A"),
        ("0.3", "深A主板"),
        ("0.2", "创业板"),
        ("0.6", "科创板"),
        ("0.128", "北交所"),
    ]

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        "Referer": "https://quote.eastmoney.com/",
    }

    for mkt_id, mkt_name in markets:
        page = 1
        while True:
            url = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"pn={page}&pz={page_size}&po=1&np=1&fltt=2&invt=2"
                f"&fid=f3&fs=m:{mkt_id}"
                f"&fields=f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18"
            )

            # 带 retry 的请求（最多重试 3 次，递增等待）
            data = None
            for attempt in range(3):
                try:
                    resp = requests.get(url, timeout=15, headers=headers)
                    data = resp.json()
                    break
                except Exception as e:
                    if attempt < 2:
                        wait = (attempt + 1) * 1.5
                        logger.debug(f"东财行情重试 {mkt_name} p{page} ({attempt+1}/3): {e}")
                        time.sleep(wait)
                    else:
                        logger.warning(f"东财行情获取失败 {mkt_name} p{page}: {e}")

            if data is None:
                break

            diff = data.get("data", {}).get("diff", [])
            if not diff:
                break

            for item in diff:
                code = str(item.get("f12", ""))
                name = str(item.get("f14", ""))
                if not code or not name:
                    continue
                # 跳过ST/退市
                if "ST" in name or "退" in name:
                    continue

                price = _safe_float(item.get("f2"))
                if price <= 0:
                    continue

                all_stocks.append({
                    "code": code,
                    "name": name,
                    "market": mkt_name,
                    "price": price,
                    "change_pct": _safe_float(item.get("f3")),
                    "volume": _safe_float(item.get("f5")) / 10000,  # 手 → 万手
                    "amount": _safe_float(item.get("f6")) / 1e8,    # 元 → 亿元
                    "amplitude": _safe_float(item.get("f7")),
                    "turnover_rate": _safe_float(item.get("f8")),
                    "volume_ratio": _safe_float(item.get("f10")),
                    "pe": _safe_float(item.get("f9")),
                    "high": _safe_float(item.get("f15")),
                    "low": _safe_float(item.get("f16")),
                    "open": _safe_float(item.get("f17")),
                    "pre_close": _safe_float(item.get("f18")),
                })

            if len(diff) < page_size:
                break
            page += 1
            time.sleep(0.5)

    return all_stocks


def fetch_kline_for_stock(code: str, count: int = 120) -> List[dict]:
    """获取单只股票K线（用于计算连涨连跌等），带 retry"""
    import requests

    market = "1" if code.startswith(("5", "6", "9")) else "0"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&fqt=1&end=20500101&lmt={count}"
    )
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

    data = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=10, headers=headers)
            data = resp.json()
            break
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))

    if data is None:
        return []

    klines = data.get("data", {}).get("klines", [])
    result = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 6:
            o = _safe_float(parts[1])
            c = _safe_float(parts[2])
            if o <= 0 or c <= 0:
                continue
            result.append({
                    "date": parts[0],
                    "open": o,
                    "close": c,
                    "high": _safe_float(parts[3]),
                    "low": _safe_float(parts[4]),
                    "volume": _safe_float(parts[5]),
                })
    return result


def fetch_main_fund_flow(code: str) -> Dict[str, float]:
    """获取主力资金流向（1日/5日）"""
    import requests

    market = "1" if code.startswith(("5", "6", "9")) else "0"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?"
        f"secid={market}.{code}&fields1=f1,f2,f3,f7"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt=101&lmt=10"
    )
    try:
        resp = requests.get(url, timeout=8,
                            headers={"User-Agent": "Mozilla/5.0",
                                     "Referer": "https://quote.eastmoney.com/"})
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return {"main_fund_1d": 0, "main_fund_5d": 0}

        last = klines[-1].split(",")
        fund_1d = _safe_float(last[1]) if len(last) > 1 else 0

        fund_5d = 0
        for k in klines[-5:]:
            parts = k.split(",")
            if len(parts) > 1:
                fund_5d += _safe_float(parts[1])

        return {"main_fund_1d": fund_1d, "main_fund_5d": fund_5d}
    except Exception:
        return {"main_fund_1d": 0, "main_fund_5d": 0}


# ═══════════════════════════════════════════════════════════════
# 批量获取K线 + 资金流向（并发）
# ═══════════════════════════════════════════════════════════════

def enrich_stocks_with_kline(stocks: List[dict], max_workers: int = 8,
                              progress_cb=None) -> List[dict]:
    """
    批量补充K线衍生指标：连涨天数、连跌天数、是否创历史新高新低、阶段新高新低
    """
    total = len(stocks)
    done = 0

    def _enrich_one(stock):
        code = stock["code"]
        klines = fetch_kline_for_stock(code, count=120)
        if not klines:
            return stock

        closes = [k["close"] for k in klines]
        highs = [k["high"] for k in klines]
        lows = [k["low"] for k in klines]
        current = stock["price"]

        # 连涨/连跌天数
        up_days = 0
        down_days = 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                if down_days > 0:
                    break
                up_days += 1
            elif closes[i] < closes[i - 1]:
                if up_days > 0:
                    break
                down_days += 1
            else:
                break

        stock["consecutive_up"] = up_days
        stock["consecutive_down"] = down_days

        # 今日创历史新高/新低
        if highs:
            stock["today_history_high"] = current >= max(highs)
            stock["today_history_low"] = current <= min(lows)
        else:
            stock["today_history_high"] = False
            stock["today_history_low"] = False

        # 阶段新高/新低（近N日）
        for period in [10, 20, 30, 60, 120]:
            recent_highs = highs[-period:] if len(highs) >= period else highs
            recent_lows = lows[-period:] if len(lows) >= period else lows
            stock[f"stage_high_{period}d"] = current >= max(recent_highs) if recent_highs else False
            stock[f"stage_low_{period}d"] = current <= min(recent_lows) if recent_lows else False

        # 近期创历史新高/新低（近N日内出现过）
        for period in [3, 5, 10, 20]:
            recent_highs = highs[-period:] if len(highs) >= period else highs
            recent_lows = lows[-period:] if len(lows) >= period else lows
            all_time_high = max(highs) if highs else 0
            all_time_low = min(lows) if lows else 999999
            stock[f"recent_history_high_{period}d"] = any(h >= all_time_high for h in recent_highs)
            stock[f"recent_history_low_{period}d"] = any(l <= all_time_low for l in recent_lows)

        # 连续涨停（涨幅>=9.8%视为涨停）
        limit_up_days = 0
        for i in range(len(closes) - 1, 0, -1):
            pct = (closes[i] - closes[i - 1]) / closes[i - 1] * 100
            if pct >= 9.8:
                limit_up_days += 1
            else:
                break
        stock["consecutive_limit_up"] = limit_up_days

        return stock

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_enrich_one, s): s for s in stocks}
        for future in as_completed(futures):
            done += 1
            if progress_cb:
                progress_cb(done, total)
            try:
                future.result()
            except Exception:
                pass

    return stocks


def enrich_stocks_with_fund_flow(stocks: List[dict], max_workers: int = 4,
                                  progress_cb=None) -> List[dict]:
    """批量补充主力资金流向"""
    total = len(stocks)
    done = 0

    def _enrich_one(stock):
        flow = fetch_main_fund_flow(stock["code"])
        stock.update(flow)
        return stock

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_enrich_one, s): s for s in stocks}
        for future in as_completed(futures):
            done += 1
            if progress_cb:
                progress_cb(done, total)
            try:
                future.result()
            except Exception:
                pass

    return stocks


# ═══════════════════════════════════════════════════════════════
# 条件筛选引擎
# ═══════════════════════════════════════════════════════════════

def filter_stocks(stocks: List[dict], conditions: dict) -> List[dict]:
    """
    按条件筛选股票
    
    conditions 格式:
    {
        "price": (min, max),           # 范围
        "change_pct": (min, max),
        "today_history_high": True,    # 布尔
        "today_stage_high": "近10日",  # 选择
        "consecutive_up": "3天",       # 选择
        ...
    }
    """
    results = []

    for stock in stocks:
        match = True

        for key, value in conditions.items():
            if value is None:
                continue

            # 范围条件
            if isinstance(value, tuple) and len(value) == 2:
                min_val, max_val = value
                stock_val = stock.get(key, 0)
                # 检查是否与默认范围不同（用户未修改则跳过）
                if not (min_val <= stock_val <= max_val):
                    match = False
                    break

            # 布尔条件
            elif isinstance(value, bool):
                if value and not stock.get(key, False):
                    match = False
                    break

            # 选择条件
            elif isinstance(value, str):
                if value == "不限":
                    continue

                # 连涨/连跌天数
                if key == "consecutive_up":
                    days = int(value.replace("天", ""))
                    if stock.get("consecutive_up", 0) < days:
                        match = False
                        break

                elif key == "consecutive_down":
                    days = int(value.replace("天", ""))
                    if stock.get("consecutive_down", 0) < days:
                        match = False
                        break

                elif key == "consecutive_limit_up":
                    days = int(value.replace("天", ""))
                    if stock.get("consecutive_limit_up", 0) < days:
                        match = False
                        break

                # 今日创阶段新高/新低
                elif key == "today_stage_high":
                    period = int(value.replace("近", "").replace("日", ""))
                    if not stock.get(f"stage_high_{period}d", False):
                        match = False
                        break

                elif key == "today_stage_low":
                    period = int(value.replace("近", "").replace("日", ""))
                    if not stock.get(f"stage_low_{period}d", False):
                        match = False
                        break

                # 近期创历史新高/新低
                elif key == "recent_history_high":
                    period = int(value.replace("近", "").replace("日", ""))
                    if not stock.get(f"recent_history_high_{period}d", False):
                        match = False
                        break

                elif key == "recent_history_low":
                    period = int(value.replace("近", "").replace("日", ""))
                    if not stock.get(f"recent_history_low_{period}d", False):
                        match = False
                        break

        if match:
            results.append(stock)

    return results


def rank_stocks(stocks: List[dict], method: str = "composite") -> List[dict]:
    """
    对筛选后的股票进行智能排序（替代纯 change_pct 排序）

    Args:
        stocks: 已筛选的股票列表
        method: 排序方法
            - "composite": 综合评分（默认，推荐）
            - "momentum": 动量优先
            - "value": 价值优先（低换手+合理涨幅）
            - "volume": 量价配合优先

    Returns:
        排序后的股票列表（新增 _rank_score 字段）
    """
    if not stocks:
        return stocks

    for s in stocks:
        s["_rank_score"] = _calc_composite_score(s, method)

    stocks.sort(key=lambda x: x["_rank_score"], reverse=True)
    return stocks


def _calc_composite_score(stock: dict, method: str = "composite") -> float:
    """
    计算单只股票的综合排序得分

    综合评分维度:
    1. 动量得分 (30%): 涨跌幅，但惩罚极端涨跌
    2. 量价得分 (25%): 量比+换手率配合
    3. 趋势得分 (20%): 连涨+新高
    4. 资金得分 (15%): 主力资金流向
    5. 稳健得分 (10%): 振幅小、换手率适中
    """
    if method == "momentum":
        return stock.get("change_pct", 0)
    elif method == "value":
        chg = stock.get("change_pct", 0)
        turnover = stock.get("turnover_rate", 0)
        # 低换手+温和上涨 = 高分
        return chg * 0.6 - turnover * 0.4
    elif method == "volume":
        vol_ratio = stock.get("volume_ratio", 1)
        chg = stock.get("change_pct", 0)
        return chg * 0.4 + min(vol_ratio, 5) * 0.6

    # composite 综合评分
    score = 0.0

    # 1. 动量得分 (30%) — 涨幅适中最佳，极端涨停/跌停惩罚
    chg = stock.get("change_pct", 0)
    if 1.0 <= chg <= 5.0:
        momentum = chg * 0.3
    elif 0.0 < chg < 1.0:
        momentum = chg * 0.2
    elif 5.0 < chg < 9.8:
        momentum = 5.0 * 0.3 + (chg - 5.0) * 0.1  # 超过5%边际递减
    elif chg >= 9.8:
        momentum = 5.0 * 0.3 + 4.8 * 0.1  # 涨停封板，不再加分
    elif -2.0 <= chg <= 0:
        momentum = chg * 0.05  # 小幅下跌，轻微扣分
    else:
        momentum = chg * 0.15  # 大幅下跌，适度扣分
    score += momentum

    # 2. 量价得分 (25%) — 量比温和放大 + 换手率适中
    vol_ratio = stock.get("volume_ratio", 1.0)
    turnover = stock.get("turnover_rate", 0)
    if 1.2 <= vol_ratio <= 3.0:
        vol_score = (vol_ratio - 1.0) * 0.25  # 温和放量
    elif vol_ratio > 3.0:
        vol_score = 0.5 * 0.25 - (vol_ratio - 3.0) * 0.05  # 过度放量，警惕
    else:
        vol_score = (vol_ratio - 1.0) * 0.1  # 缩量，低分
    # 换手率: 3%~10% 最佳
    if 3.0 <= turnover <= 10.0:
        vol_score += 0.15
    elif turnover > 10.0:
        vol_score += 0.05  # 换手过高，警惕
    elif turnover >= 1.0:
        vol_score += 0.08
    score += vol_score

    # 3. 趋势得分 (20%) — 连涨天数 + 新高
    up_days = stock.get("consecutive_up", 0)
    if 2 <= up_days <= 5:
        trend = up_days * 0.04
    elif up_days > 5:
        trend = 5 * 0.04  # 连涨过多，不再加分
    else:
        trend = 0
    # 新高加分
    if stock.get("today_history_high", False):
        trend += 0.08
    elif stock.get("stage_high_20d", False):
        trend += 0.04
    score += trend

    # 4. 资金得分 (15%) — 主力资金净流入
    fund_1d = stock.get("main_fund_1d", 0)
    fund_5d = stock.get("main_fund_5d", 0)
    if fund_1d > 0:
        score += min(fund_1d / 1e6, 0.1)  # 正向流入，上限0.1
    if fund_5d > 0:
        score += min(fund_5d / 5e6, 0.05)
    elif fund_1d < -1e5:
        score -= 0.05  # 大幅流出，扣分

    # 5. 稳健得分 (10%) — 振幅小、无跌停
    amp = stock.get("amplitude", 0)
    if 0 < amp <= 5.0:
        score += 0.05  # 振幅适中
    elif amp > 10.0:
        score -= 0.03  # 振幅过大

    return round(score, 4)


# ═══════════════════════════════════════════════════════════════
# 选股参数回测 & 自动优化
# ═══════════════════════════════════════════════════════════════

class SelectorBacktestEngine:
    """
    选股策略回测引擎
    
    逻辑：用历史数据模拟"某日用选股条件选出股票 → 买入 → 持有N天 → 卖出"
    统计收益率、胜率、最大回撤等指标。
    """

    def __init__(self, hold_days: int = 5, top_n: int = 10,
                 commission: float = 0.00015, stamp_tax: float = 0.001,
                 slippage: float = 0.001, rank_method: str = "composite"):
        """
        Args:
            hold_days: 选出后持有天数
            top_n: 每次选出前N只
            commission: 佣金费率 (双边，买卖各收)
            stamp_tax: 印花税 (卖出时收取)
            slippage: 滑点估算
            rank_method: 排序方法 (composite/momentum/volume/value)
        """
        self.hold_days = hold_days
        self.top_n = top_n
        self.commission = commission
        self.stamp_tax = stamp_tax
        self.slippage = slippage
        self.rank_method = rank_method

    def run(self, stocks_data: Dict[str, List[dict]],
            conditions: dict, start_idx: int = 60) -> dict:
        """
        运行选股回测
        
        Args:
            stocks_data: {code: [kline_data, ...]} 所有股票的历史K线
            conditions: 选股条件
            start_idx: 从第几根K线开始（需要前置数据计算指标）
        
        Returns:
            回测结果字典
        """
        if not stocks_data:
            return {"error": "无数据"}

        # 找到所有股票中最短的K线长度
        min_len = min(len(v) for v in stocks_data.values())
        if min_len <= start_idx + self.hold_days:
            return {"error": "数据不足"}

        trades = []
        equity = 1.0
        equity_curve = [1.0]
        max_equity = 1.0
        max_drawdown = 0

        # 每隔 hold_days 天执行一次选股
        for day_idx in range(start_idx, min_len - self.hold_days, self.hold_days):
            # 计算每只股票在该日的指标
            candidates = []
            for code, klines in stocks_data.items():
                if day_idx >= len(klines):
                    continue

                kline = klines[day_idx]
                prev_kline = klines[day_idx - 1] if day_idx > 0 else kline

                # 计算当日指标
                stock_info = self._calc_day_metrics(klines, day_idx)
                if stock_info is None:
                    continue

                stock_info["code"] = code
                candidates.append(stock_info)

            # 按条件筛选
            selected = filter_stocks(candidates, conditions)

            # 按综合评分排序取前N（替代纯涨跌幅排序）
            selected = rank_stocks(selected, method=self.rank_method)
            selected = selected[:self.top_n]

            if not selected:
                equity_curve.append(equity)
                continue

            # 计算持有期收益（含交易成本）
            period_return = 0
            for stock in selected:
                code = stock["code"]
                klines = stocks_data[code]
                buy_price = klines[day_idx]["close"]
                sell_idx = min(day_idx + self.hold_days, len(klines) - 1)
                sell_price = klines[sell_idx]["close"]

                if buy_price > 0:
                    # 扣除交易成本: 买入佣金 + 滑点 + 卖出佣金 + 印花税 + 滑点
                    gross_ret = (sell_price - buy_price) / buy_price
                    cost = self.commission * 2 + self.stamp_tax + self.slippage * 2
                    ret = gross_ret - cost
                    period_return += ret

                    trades.append({
                        "code": code,
                        "buy_date": klines[day_idx].get("date", ""),
                        "sell_date": klines[sell_idx].get("date", ""),
                        "buy_price": buy_price,
                        "sell_price": sell_price,
                        "gross_return": gross_ret,
                        "cost": cost,
                        "return": ret,
                    })

            avg_return = period_return / len(selected)
            equity *= (1 + avg_return)
            equity_curve.append(equity)

            max_equity = max(max_equity, equity)
            dd = (max_equity - equity) / max_equity
            max_drawdown = max(max_drawdown, dd)

        # 统计
        if not trades:
            return {"error": "无交易", "trades": 0}

        returns = [t["return"] for t in trades]
        gross_returns = [t.get("gross_return", t["return"]) for t in trades]
        total_cost = sum(t.get("cost", 0) for t in trades)
        win_trades = [r for r in returns if r > 0]

        return {
            "total_trades": len(trades),
            "win_rate": len(win_trades) / len(trades) if trades else 0,
            "avg_return": sum(returns) / len(returns) if returns else 0,
            "avg_gross_return": sum(gross_returns) / len(gross_returns) if gross_returns else 0,
            "total_return": equity - 1,
            "total_cost": total_cost,
            "cost_per_trade": total_cost / len(trades) if trades else 0,
            "max_drawdown": max_drawdown,
            "sharpe": (sum(returns) / len(returns)) / (max(0.001, self._std(returns)))
                      if returns else 0,
            "profit_factor": sum(r for r in returns if r > 0) / abs(sum(r for r in returns if r < 0))
                             if any(r < 0 for r in returns) else 999,
            "equity_curve": equity_curve,
            "trades": trades,
            "hold_days": self.hold_days,
            "top_n": self.top_n,
            "rank_method": self.rank_method,
        }

    def _calc_day_metrics(self, klines: List[dict], idx: int) -> Optional[dict]:
        """计算某只股票在某日的筛选指标"""
        if idx < 1 or idx >= len(klines):
            return None

        kline = klines[idx]
        prev = klines[idx - 1]
        close = kline["close"]
        prev_close = prev["close"]

        if prev_close <= 0 or close <= 0:
            return None

        # 涨跌幅
        change_pct = (close - prev_close) / prev_close * 100

        # 振幅
        amplitude = (kline["high"] - kline["low"]) / prev_close * 100

        # 成交额（近似）
        amount = kline.get("volume", 0) * close / 10000  # 万元

        # 连涨/连跌
        up_days = 0
        down_days = 0
        for i in range(idx, max(idx - 30, 0), -1):
            if i < 1:
                break
            if klines[i]["close"] > klines[i - 1]["close"]:
                if down_days > 0:
                    break
                up_days += 1
            elif klines[i]["close"] < klines[i - 1]["close"]:
                if up_days > 0:
                    break
                down_days += 1
            else:
                break

        # 历史新高/新低
        all_highs = [k["high"] for k in klines[:idx + 1]]
        all_lows = [k["low"] for k in klines[:idx + 1]]

        # 阶段新高/新低
        stage_data = {}
        for period in [10, 20, 30, 60]:
            recent = klines[max(0, idx - period + 1):idx + 1]
            if recent:
                stage_data[f"stage_high_{period}d"] = close >= max(k["high"] for k in recent)
                stage_data[f"stage_low_{period}d"] = close <= min(k["low"] for k in recent)

        # 近期创历史新高/新低
        recent_data = {}
        for period in [3, 5, 10, 20]:
            recent = klines[max(0, idx - period + 1):idx + 1]
            if recent:
                all_time_high = max(all_highs)
                all_time_low = min(all_lows)
                recent_data[f"recent_history_high_{period}d"] = any(k["high"] >= all_time_high for k in recent)
                recent_data[f"recent_history_low_{period}d"] = any(k["low"] <= all_time_low for k in recent)

        # 连续涨停
        limit_up_days = 0
        for i in range(idx, max(idx - 10, 0), -1):
            if i < 1:
                break
            pct = (klines[i]["close"] - klines[i - 1]["close"]) / klines[i - 1]["close"] * 100
            if pct >= 9.8:
                limit_up_days += 1
            else:
                break

        return {
            "price": close,
            "change_pct": change_pct,
            "amplitude": amplitude,
            "volume": kline.get("volume", 0),
            "amount": amount,
            "turnover_rate": 0,  # 需要流通股数据
            "volume_ratio": 0,
            "consecutive_up": up_days,
            "consecutive_down": down_days,
            "consecutive_limit_up": limit_up_days,
            "today_history_high": close >= max(all_highs),
            "today_history_low": close <= min(all_lows),
            **stage_data,
            **recent_data,
        }

    @staticmethod
    def _std(values: list) -> float:
        """标准差"""
        if len(values) < 2:
            return 1.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return variance ** 0.5


class SelectorParamOptimizer:
    """
    选股参数自动优化器
    
    支持:
    - 网格搜索: 遍历所有参数组合
    - 随机搜索: 随机采样参数
    - 贝叶斯优化: 使用optuna（可选）

    参数空间格式:
    - 范围型: (min, max) → 随机均匀采样
    - 离散列表: [val1, val2, ...] → 随机选择
    - 字典型: {"type": "float"/"int"/"categorical", ...} → optuna 格式
    """

    def __init__(self, method: str = "grid", n_iter: int = 50,
                 rank_method: str = "composite"):
        """
        Args:
            method: "grid" / "random" / "bayesian"
            n_iter: 随机/贝叶斯搜索的迭代次数
            rank_method: 股票排序方法 (composite/momentum/volume/value)
        """
        self.method = method
        self.n_iter = n_iter
        self.rank_method = rank_method
        self.results = []

    def optimize(self, stocks_data: Dict[str, List[dict]],
                 param_space: dict, hold_days: int = 5,
                 top_n: int = 10, metric: str = "sharpe",
                 progress_cb=None) -> dict:
        """
        执行参数优化
        
        Args:
            stocks_data: 全市场历史K线数据
            param_space: 参数空间定义（支持范围型+离散型混合）
            hold_days: 持有天数
            top_n: 每次选前N只
            metric: 优化目标 ("sharpe" / "total_return" / "win_rate" / "profit_factor")
            progress_cb: 进度回调 fn(current, total, msg)
        
        Returns:
            {
                "best_params": {...},
                "best_score": float,
                "all_results": [...],
                "method": str,
                "rank_method": str,
            }
        """
        self.results = []

        if self.method == "grid":
            return self._grid_search(stocks_data, param_space, hold_days, top_n, metric, progress_cb)
        elif self.method == "random":
            return self._random_search(stocks_data, param_space, hold_days, top_n, metric, progress_cb)
        elif self.method == "bayesian":
            return self._bayesian_search(stocks_data, param_space, hold_days, top_n, metric, progress_cb)
        else:
            return {"error": f"未知优化方法: {self.method}"}

    def _create_engine(self, hold_days, top_n):
        """创建回测引擎（统一配置）"""
        return SelectorBacktestEngine(
            hold_days=hold_days, top_n=top_n,
            rank_method=self.rank_method,
        )

    def _grid_search(self, stocks_data, param_space, hold_days, top_n, metric, progress_cb):
        """网格搜索"""
        import itertools

        keys = list(param_space.keys())
        values = list(param_space.values())
        combinations = list(itertools.product(*values))
        total = len(combinations)

        best_score = -999
        best_params = None

        for i, combo in enumerate(combinations):
            params = dict(zip(keys, combo))
            if progress_cb:
                progress_cb(i, total, f"评估 {i+1}/{total}: {params}")

            engine = self._create_engine(hold_days, top_n)
            result = engine.run(stocks_data, params)
            score = result.get(metric, -999)

            self.results.append({
                "params": params,
                "score": score,
                "total_return": result.get("total_return", 0),
                "win_rate": result.get("win_rate", 0),
                "sharpe": result.get("sharpe", 0),
                "max_drawdown": result.get("max_drawdown", 0),
                "trades": result.get("total_trades", 0),
            })

            if score > best_score:
                best_score = score
                best_params = params

        return {
            "best_params": best_params,
            "best_score": best_score,
            "all_results": self.results,
            "method": "grid",
            "total_evaluated": total,
            "rank_method": self.rank_method,
        }

    def _random_search(self, stocks_data, param_space, hold_days, top_n, metric, progress_cb):
        """随机搜索 — 同时支持范围型和离散型参数"""
        import random

        best_score = -999
        best_params = None

        for i in range(self.n_iter):
            params = {}
            for key, space in param_space.items():
                if isinstance(space, dict):
                    # optuna 格式: {"type": "float"/"int"/"categorical", ...}
                    if space.get("type") == "float":
                        params[key] = random.uniform(space["low"], space["high"])
                    elif space.get("type") == "int":
                        params[key] = random.randint(space["low"], space["high"])
                    elif space.get("type") == "categorical":
                        params[key] = random.choice(space["choices"])
                elif isinstance(space, list):
                    # 离散列表: ["3天", "5天", "10天"] 或 [0.6, 0.7, 0.8]
                    params[key] = random.choice(space)
                elif isinstance(space, tuple) and len(space) == 2:
                    # 范围: (min, max)
                    if isinstance(space[0], int) and isinstance(space[1], int):
                        params[key] = random.randint(space[0], space[1])
                    else:
                        params[key] = random.uniform(space[0], space[1])

            if progress_cb:
                progress_cb(i, self.n_iter, f"随机采样 {i+1}/{self.n_iter}")

            engine = self._create_engine(hold_days, top_n)
            result = engine.run(stocks_data, params)
            score = result.get(metric, -999)

            self.results.append({
                "params": params,
                "score": score,
                "total_return": result.get("total_return", 0),
                "win_rate": result.get("win_rate", 0),
                "sharpe": result.get("sharpe", 0),
                "max_drawdown": result.get("max_drawdown", 0),
                "trades": result.get("total_trades", 0),
            })

            if score > best_score:
                best_score = score
                best_params = params

        return {
            "best_params": best_params,
            "best_score": best_score,
            "all_results": self.results,
            "method": "random",
            "total_evaluated": self.n_iter,
            "rank_method": self.rank_method,
        }

    def _bayesian_search(self, stocks_data, param_space, hold_days, top_n, metric, progress_cb):
        """贝叶斯优化（需要optuna）"""
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
        except ImportError:
            return self._random_search(stocks_data, param_space, hold_days, top_n, metric, progress_cb)

        best_score = -999

        def objective(trial):
            nonlocal best_score
            params = {}
            for key, space in param_space.items():
                if isinstance(space, dict):
                    if space.get("type") == "float":
                        params[key] = trial.suggest_float(key, space["low"], space["high"])
                    elif space.get("type") == "int":
                        params[key] = trial.suggest_int(key, space["low"], space["high"])
                    elif space.get("type") == "categorical":
                        params[key] = trial.suggest_categorical(key, space["choices"])
                elif isinstance(space, list):
                    params[key] = trial.suggest_categorical(key, space)
                elif isinstance(space, tuple) and len(space) == 2:
                    if isinstance(space[0], int) and isinstance(space[1], int):
                        params[key] = trial.suggest_int(key, space[0], space[1])
                    else:
                        params[key] = trial.suggest_float(key, space[0], space[1])

            engine = self._create_engine(hold_days, top_n)
            result = engine.run(stocks_data, params)
            score = result.get(metric, -999)

            self.results.append({
                "params": params,
                "score": score,
                "total_return": result.get("total_return", 0),
                "win_rate": result.get("win_rate", 0),
                "sharpe": result.get("sharpe", 0),
                "max_drawdown": result.get("max_drawdown", 0),
                "trades": result.get("total_trades", 0),
            })

            if score > best_score:
                best_score = score

            return score

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=self.n_iter, show_progress_bar=False)

        return {
            "best_params": study.best_params,
            "best_score": study.best_value,
            "all_results": self.results,
            "method": "bayesian",
            "total_evaluated": self.n_iter,
            "rank_method": self.rank_method,
        }


# ═══════════════════════════════════════════════════════════════
# 优化参数持久化 & 应用
# ═══════════════════════════════════════════════════════════════

import os
import json
from pathlib import Path

_OPT_PARAMS_FILE = Path(__file__).resolve().parent.parent / "data" / "selector_opt_params.json"


def save_optimized_params(params: dict, score: float = 0, method: str = "") -> str:
    """
    持久化保存优化后的选股参数
    
    Args:
        params: 优化后的参数字典
        score: 最优得分
        method: 优化方法
    
    Returns:
        保存路径
    """
    _OPT_PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    record = {
        "params": params,
        "score": score,
        "method": method,
        "saved_at": datetime.now().isoformat(),
    }
    
    with open(_OPT_PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    
    logger.info(f"优化参数已保存: {_OPT_PARAMS_FILE}")
    return str(_OPT_PARAMS_FILE)


def load_optimized_params() -> Optional[dict]:
    """
    加载已保存的优化参数
    
    Returns:
        {"params": {...}, "score": float, "method": str, "saved_at": str} 或 None
    """
    if not _OPT_PARAMS_FILE.exists():
        return None
    try:
        with open(_OPT_PARAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载优化参数失败: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# MACD 金叉 / 死叉扫描
# ═══════════════════════════════════════════════════════════════

def fetch_macd_golden_cross() -> List[dict]:
    """
    扫描今日 MACD 金叉股票（东方财富选股器接口，秒出）

    Returns:
        [{"code": "000001", "name": "平安银行", "price": 12.5,
          "change_pct": 1.23, "volume_ratio": 1.5, "turnover_rate": 2.3}, ...]
    """
    import requests

    url = "https://data.eastmoney.com/dataapi/xuangu/list"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://data.eastmoney.com/xuangu/",
    }
    params = {
        "st": "CHANGE_RATE",
        "sr": "-1",
        "ps": "5000",
        "p": "1",
        "sty": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,NEW_PRICE,"
               "CHANGE_RATE,VOLUME_RATIO,TURNOVERRATE",
        "filter": '(MACD_GOLDEN_FORK="1")',
        "source": "SELECT_SECURITIES",
        "client": "WEB",
        "hyversion": "v2",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        data = resp.json()
        results = []
        golden_codes = set()

        if data.get("result") and data["result"].get("data"):
            for item in data["result"]["data"]:
                code = item.get("SECURITY_CODE", "")
                if not code:
                    continue
                golden_codes.add(code)
                results.append({
                    "code": code,
                    "name": item.get("SECURITY_NAME_ABBR", ""),
                    "cross_type": "golden",
                    "price": _safe_float(item.get("NEW_PRICE")),
                    "change_pct": _safe_float(item.get("CHANGE_RATE")),
                    "volume_ratio": _safe_float(item.get("VOLUME_RATIO")),
                    "turnover_rate": _safe_float(item.get("TURNOVERRATE")),
                })

        return results, golden_codes
    except Exception as e:
        logger.warning(f"MACD金叉扫描失败: {e}")
        return [], set()


def fetch_macd_death_cross(golden_codes: set = None,
                            progress_cb=None,
                            max_workers: int = 12,
                            all_stocks: List[dict] = None) -> List[dict]:
    """
    扫描今日 MACD 死叉股票（用东方财富K线计算，无需 BaoStock）

    Args:
        golden_codes: 金叉股票代码集合（排除用）
        progress_cb: 进度回调 fn(done, total)
        max_workers: 并发数
        all_stocks: 已有全市场行情数据（传入则跳过重复拉取）

    Returns:
        [{"code": "000001", "name": "平安银行", "cross_type": "death",
          "price": 12.5, "change_pct": 1.23}, ...]
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if golden_codes is None:
        golden_codes = set()

    # 复用已有数据或重新拉取
    if all_stocks is None:
        all_stocks = fetch_all_stocks_eastmoney(page_size=5000)
    candidates = [s for s in all_stocks if s["code"] not in golden_codes]

    if not candidates:
        return []

    results = []
    total = len(candidates)
    done = [0]

    def _check_death_cross(stock):
        code = stock["code"]
        klines = fetch_kline_for_stock(code, count=60)
        if not klines or len(klines) < 30:
            return None

        closes = [k["close"] for k in klines]
        # 计算 MACD
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
        dea = _ema(dif, 9)

        # 判断死叉状态：当前 DIF < DEA 即为处于死叉
        if len(dif) < 1 or len(dea) < 1:
            return None

        if dif[-1] < dea[-1]:
            return {
                "code": code,
                "name": stock.get("name", ""),
                "cross_type": "death",
                "price": stock.get("price", 0),
                "change_pct": stock.get("change_pct", 0),
                "volume_ratio": stock.get("volume_ratio", 0),
                "turnover_rate": stock.get("turnover_rate", 0),
            }
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_check_death_cross, s): s for s in candidates}
        for future in as_completed(futures):
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], total)
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception:
                pass

    return results


def _ema(data: list, span: int) -> list:
    """计算 EMA（指数移动平均）"""
    if not data:
        return []
    ema_vals = [data[0]]
    multiplier = 2.0 / (span + 1)
    for i in range(1, len(data)):
        ema_vals.append(data[i] * multiplier + ema_vals[-1] * (1 - multiplier))
    return ema_vals


def scan_macd_cross(progress_cb=None) -> dict:
    """
    一键扫描今日 MACD 金叉 + 死叉

    Args:
        progress_cb: 进度回调 fn(phase, done, total)

    Returns:
        {
            "golden": [...],
            "death": [...],
            "golden_count": int,
            "death_count": int,
            "total": int,
        }
    """
    # Phase 0: 预拉全市场行情（共用，避免重复拉取）
    if progress_cb:
        progress_cb("获取全市场行情...", 0, 0)
    all_stocks = fetch_all_stocks_eastmoney()

    # Phase 1: 金叉（秒出）
    if progress_cb:
        progress_cb("扫描MACD金叉...", 0, 0)
    golden, golden_codes = fetch_macd_golden_cross()
    if progress_cb:
        progress_cb(f"金叉: {len(golden)} 只", len(golden), len(golden))

    # Phase 2: 死叉（复用行情数据）
    def _death_progress(done, total):
        if progress_cb:
            progress_cb(f"扫描死叉 ({done}/{total})...", done, total)

    death = fetch_macd_death_cross(golden_codes, progress_cb=_death_progress,
                                    all_stocks=all_stocks)
    if progress_cb:
        progress_cb(f"完成 — 金叉 {len(golden)} 只, 死叉 {len(death)} 只",
                     len(golden) + len(death), len(golden) + len(death))

    return {
        "golden": golden,
        "death": death,
        "golden_count": len(golden),
        "death_count": len(death),
        "total": len(golden) + len(death),
    }


def convert_opt_params_to_conditions(params: dict) -> dict:
    """
    将优化器输出的连续值参数转换为选股条件格式
    
    处理逻辑:
    - 范围型条件 (price, change_pct, ...): 以最优值为中心，±5%范围
    - 离散型条件 (consecutive_up, today_stage_high, ...): 映射到最近的选项
    - 布尔型条件 (today_history_high, ...): 直接使用
    
    Args:
        params: 优化器输出 {"price": 15.3, "consecutive_up": 3, "change_pct": 2.1, ...}
    
    Returns:
        选股条件字典，可直接传给 filter_stocks()
    """
    # 条件配置映射表 — 从 CONDITIONS_CONFIG 提取
    # 格式: key → (type, extra_info)
    _CONDITION_MAP = {
        "price": ("range", (0.16, 1575.0, 0.01)),
        "change_pct": ("range", (-29.99, 29.97, 0.01)),
        "amplitude": ("range", (0.0, 33.48, 0.01)),
        "turnover_rate": ("range", (0.0, 68.63, 0.01)),
        "volume": ("range", (2.59, 232535.37, 1.0)),
        "amount": ("range", (0.0, 225.79, 0.01)),
        "volume_ratio": ("range", (0.0, 20.98, 0.01)),
        "main_fund_1d": ("range", (-253169.04, 200329.36, 1.0)),
        "main_fund_5d": ("range", (-702749.66, 241916.49, 1.0)),
        "bid_ask_ratio": ("range", (-100.0, 100.0, 0.01)),
        # 离散型: 可选值列表
        "consecutive_up": ("select", ["不限", "3天", "5天", "10天"]),
        "consecutive_down": ("select", ["不限", "3天", "5天", "10天"]),
        "consecutive_limit_up": ("select", ["不限", "3天", "5天", "10天"]),
        "today_stage_high": ("select", ["不限", "近10日", "近20日", "近30日", "近60日", "近120日"]),
        "today_stage_low": ("select", ["不限", "近10日", "近20日", "近30日", "近60日", "近120日"]),
        "recent_history_high": ("select", ["不限", "近3日", "近5日", "近10日", "近20日"]),
        "recent_history_low": ("select", ["不限", "近3日", "近5日", "近10日", "近20日"]),
        # 布尔型
        "today_history_high": ("bool", None),
        "today_history_low": ("bool", None),
    }

    conditions = {}

    for key, value in params.items():
        if key not in _CONDITION_MAP:
            continue

        ctype, extra = _CONDITION_MAP[key]

        if ctype == "range":
            min_v, max_v, step = extra
            # 以最优值为中心，±5%范围
            margin = (max_v - min_v) * 0.05
            cond_min = max(min_v, value - margin)
            cond_max = min(max_v, value + margin)
            conditions[key] = (cond_min, cond_max)

        elif ctype == "select":
            options = extra
            if isinstance(value, str) and value in options:
                # 已经是选项字符串（如 "3天"）
                conditions[key] = value
            elif isinstance(value, (int, float)):
                # 连续值 → 映射到最近的选项
                # 提取选项中的数字部分进行匹配
                best_option = "不限"
                best_dist = float("inf")
                for opt in options:
                    if opt == "不限":
                        continue
                    # 提取数字: "3天" → 3, "近10日" → 10
                    import re
                    nums = re.findall(r'\d+', opt)
                    if nums:
                        dist = abs(float(nums[0]) - value)
                        if dist < best_dist:
                            best_dist = dist
                            best_option = opt
                conditions[key] = best_option

        elif ctype == "bool":
            conditions[key] = bool(value) if not isinstance(value, bool) else value

    return conditions
