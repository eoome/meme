#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多源数据路由器
==============

按数据类型分发到不同接口，避免单一来源限频。

数据源分配策略：
┌──────────────────┬────────────────────────┬──────────────────────────┬──────────────┐
│ 数据类型          │ 主数据源                │ 备用数据源                │ 切换条件      │
├──────────────────┼────────────────────────┼──────────────────────────┼──────────────┤
│ 股票/ETF列表      │ 东方财富 push2          │ 本地缓存 data/stocks.json │ 网络失败      │
│ 实时快照(单只)    │ 腾讯 qt.gtimg.cn       │ 新浪 hq.sinajs.cn        │ 腾讯超时      │
│ 实时快照(批量)    │ 腾讯 qt.gtimg.cn       │ AkShare spot_em          │ 腾讯超时      │
│ 搜索/自动补全     │ 腾讯 smartbox          │ 本地模糊匹配              │ 腾讯超时      │
│ K线(日/周/月)     │ 腾讯 ifzq.gtimg.cn     │ 东方财富 kline           │ 腾讯无数据    │
│ 分时数据(当日)    │ 东方财富 trends2 (实时)  │ 腾讯minute → 东财K线兜底   │
│ 行情快照(全市场)  │ AkShare spot_em        │ 东方财富 push2           │ AkShare异常   │
│ 分钟K线           │ AkShare hist_min_em    │ 腾讯 minute              │ AkShare异常   │
└──────────────────┴────────────────────────┴──────────────────────────┴──────────────┘

限频策略：
- 腾讯 qt.gtimg.cn: 支持批量(逗号拼接)，单次可查50+只，建议间隔 ≥ 3s
- 新浪 hq.sinajs.cn: 需要 Referer 头，约50次/分钟，建议间隔 ≥ 2s
- 东方财富 push2: 较宽松，但建议间隔 ≥ 5s
- AkShare: 底层也是爬虫，建议间隔 ≥ 3s
"""

import json
import os
import time
import requests
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import List, Tuple, Optional, Dict
import logging

from core.logger import log
from utils.numeric import clean_num as _safe_float, clean_kline_list as _clean_kline_data, clean_minute_list as _clean_minute_data

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  限流保护（防止触发数据源限频）
# ──────────────────────────────────────────────

import functools
from collections import defaultdict


class RateLimiter:
    """
    调用频率限制器 — 保护数据源不被高频请求触发限频
    
    用法:
        @RateLimiter.min_interval(3.0)   # 两次调用至少间隔3秒
        def fetch_xxx(): ...
    """
    _last_call: Dict[str, float] = {}
    _lock = Lock()

    @classmethod
    def min_interval(cls, seconds: float):
        """装饰器：限制被装饰函数的调用间隔"""
        def decorator(func):
            """装饰器包装 — 用于RateLimiter"""
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                """包装函数 — 限频控制核心"""
                key = func.__name__
                with cls._lock:
                    now = time.time()
                    last = cls._last_call.get(key, 0)
                    wait = seconds - (now - last)
                    if wait > 0:
                        time.sleep(wait)
                    cls._last_call[key] = time.time()
                return func(*args, **kwargs)
            return wrapper
        return decorator


# 数据源最小请求间隔（秒）
MIN_INTERVAL_TENCENT = 3.0   # 腾讯建议 ≥3s
MIN_INTERVAL_SINA = 2.0      # 新浪建议 ≥2s
MIN_INTERVAL_EASTMONEY = 5.0 # 东财建议 ≥5s


# ──────────────────────────────────────────────
#  基础工具
# ──────────────────────────────────────────────

DATA_DIR = str(Path(__file__).resolve().parent.parent / "data")
CACHE_STOCKS = os.path.join(DATA_DIR, "stocks.json")
CACHE_PRICES = os.path.join(DATA_DIR, "prices_cache.json")

# 缓存有效期 (秒) — 7天
STOCK_LIST_CACHE_TTL = 7 * 24 * 3600

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

# 默认请求参数
DEFAULT_TIMEOUT = 8
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 0.5

# 全局 Session — 连接复用，扩大连接池适应高并发
_session = requests.Session()
_session.headers.update({"User-Agent": UA})
# 扩大连接池：pool_maxsize 从 10 → 50，增加自动重试
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=20,
    pool_maxsize=50,
    max_retries=2,
)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


def close_session() -> None:
    """关闭全局 Session，释放连接池资源 — 程序退出时调用（幂等，多次调用安全）"""
    global _session
    try:
        s = _session
        _session = None  # 先置空，避免重入或并发问题
        if s is not None:
            s.close()
    except Exception:
        pass


# 程序退出时自动关闭 Session
import atexit
atexit.register(close_session)


def _http_get(url: str, timeout: int = DEFAULT_TIMEOUT, encoding: str = "utf-8",
              referer: str = None, retries: int = DEFAULT_RETRIES,
              retry_delay: float = DEFAULT_RETRY_DELAY) -> str:
    """
    统一 HTTP GET，带 UA、可选 Referer、自动重试、连接复用
    """
    headers = {}
    if referer:
        headers["Referer"] = referer

    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = _session.get(url, headers=headers, timeout=timeout)
            resp.encoding = encoding
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(retry_delay * (attempt + 1))  # 指数退避
            else:
                raise last_error


def _ensure_dir():
    """确保数据目录存在"""
    os.makedirs(DATA_DIR, exist_ok=True)


# ──────────────────────────────────────────────
#  数据源 1: 股票/ETF 列表 — 东方财富
# ──────────────────────────────────────────────

@RateLimiter.min_interval(MIN_INTERVAL_EASTMONEY)
def fetch_stock_list_eastmoney() -> List[Tuple[str, str, str]]:
    """
    从东方财富获取全A股 + ETF 列表
    [(code, name, sector), ...]
    """
    stocks = []
    # 沪深主板 + 创业板 + 科创板 + 北交所
    markets = ["1.2", "0.3", "0.2", "0.6", "0.128"]
    for mid in markets:
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn=1&pz=10000&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:{mid}"
            f"&fields=f12,f14,f90"
        )
        try:
            text = _http_get(url, timeout=10)
            data = json.loads(text)
            for item in data.get("data", {}).get("diff", []):
                code = str(item.get("f12", ""))
                name = str(item.get("f14", ""))
                sector = str(item.get("f90", ""))
                if code and name:
                    stocks.append((code, name, sector if sector else "其他"))
        except Exception:
            continue

    # ETF
    for fund_mkt in ["1.ETF", "0.ETF"]:
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn=1&pz=5000&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:{fund_mkt}"
            f"&fields=f12,f14,f90"
        )
        try:
            text = _http_get(url, timeout=10)
            data = json.loads(text)
            for item in data.get("data", {}).get("diff", []):
                code = str(item.get("f12", ""))
                name = str(item.get("f14", ""))
                if code and name:
                    sector = str(item.get("f90", ""))
                    stocks.append((code, name, sector if sector else "ETF"))
        except Exception:
            continue

    return stocks


def load_stock_list_cached() -> List[Tuple[str, str, str]]:
    """加载本地缓存的股票列表"""
    if os.path.exists(CACHE_STOCKS):
        with open(CACHE_STOCKS, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [(d["code"], d["name"], d.get("sector", "")) for d in data]
    return []


def save_stock_list_cache(stocks: List[Tuple[str, str, str]]):
    """保存股票列表到本地缓存"""
    _ensure_dir()
    data = [{"code": c, "name": n, "sector": s} for c, n, s in stocks]
    with open(CACHE_STOCKS, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# ──────────────────────────────────────────────
#  数据源 2: 实时快照 — 腾讯(主) / 新浪(备)
# ──────────────────────────────────────────────

def _code_to_symbol(code: str) -> str:
    """股票代码 → 腾讯/新浪行情代码
    6xxxxx/5xxxxx → sh  (沪A/沪基金)
    0xxxxx/3xxxxx → sz  (深A/创业板)
    8xxxxx        → bj  (北交所: 83/87/92 开头)
    4xxxxx        → sz  (老三板/深B，挂到深交所)
    """
    if code.startswith(("6", "5")):
        return f"sh{code}"
    elif code.startswith(("8", "9")):
        return f"bj{code}"
    return f"sz{code}"


# _safe_float 已通过 from utils.numeric import clean_num as _safe_float 导入

@RateLimiter.min_interval(MIN_INTERVAL_TENCENT)
def fetch_realtime_tencent(codes: List[str]) -> Dict[str, dict]:
    """
    腾讯批量实时行情
    返回: {code: {"price": float, "name": str, "open": float, "high": float,
                  "low": float, "volume": float, "amount": float, ...}}
    """
    if not codes:
        return {}
    symbols = [_code_to_symbol(c) for c in codes]
    url = f"https://qt.gtimg.cn/q={','.join(symbols)}"
    result = {}
    try:
        text = _http_get(url, timeout=8, encoding="gbk")
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "~" not in line:
                continue
            parts = line.split("~")
            if len(parts) < 46:
                continue
            try:
                code = parts[2]
                price = _safe_float(parts[3])
                if price <= 0:
                    continue
                result[code] = {
                    "name": parts[1],
                    "code": code,
                    "price": price,
                    "open": _safe_float(parts[5]),
                    "high": _safe_float(parts[33]),
                    "low": _safe_float(parts[34]),
                    "volume": _safe_float(parts[6]),
                    "amount": _safe_float(parts[37]),
                    "change_pct": _safe_float(parts[32]),
                    "time": parts[30] if len(parts) > 30 else "",
                }
            except (ValueError, IndexError):
                continue
    except Exception as e:
        log.warning("data", f"腾讯实时行情异常: {e}")
    return result


@RateLimiter.min_interval(MIN_INTERVAL_SINA)
def fetch_realtime_sina(code: str) -> Optional[dict]:
    """
    新浪单只实时行情 (备用)
    需要 Referer 头，限频较严
    """
    symbol = _code_to_symbol(code)
    url = f"https://hq.sinajs.cn/list={symbol}"
    try:
        text = _http_get(url, timeout=6, encoding="gbk",
                         referer="https://finance.sina.com.cn")
        if "=" not in text:
            return None
        payload = text.split("=", 1)[1].strip().strip('";')
        parts = payload.split(",")
        if len(parts) < 32:
            return None
        return {
            "name": parts[0],
            "code": code,
            "open": _safe_float(parts[1]),
            "price": _safe_float(parts[3]),
            "high": _safe_float(parts[4]),
            "low": _safe_float(parts[5]),
            "volume": _safe_float(parts[8]),
            "amount": _safe_float(parts[9]),
            "time": f"{parts[30]} {parts[31]}" if len(parts) > 31 else "",
        }
    except Exception as e:
        log.warning("data", f"新浪实时行情异常: {e}")
        return None


# ──────────────────────────────────────────────
#  数据源 3: 搜索/自动补全 — 腾讯 smartbox
# ──────────────────────────────────────────────

@RateLimiter.min_interval(MIN_INTERVAL_TENCENT)
def fetch_search_tencent(keyword: str) -> List[Tuple[str, str, str]]:
    """
    腾讯 smartbox 搜索
    返回: [(code, name, market_label), ...]  最多5条
    """
    results = []
    try:
        from urllib.parse import quote as url_quote
        q = url_quote(keyword)
        url = f"https://smartbox.gtimg.cn/s3/?v=2&q={q}&t=all&c=1"
        text = _http_get(url, timeout=3)
        if "v_hint=" not in text:
            return results
        payload = text.split("=", 1)[1].strip().strip('"')
        try:
            payload = json.loads(f'"{payload}"')
        except Exception as e:
            logger.debug(f"操作失败: {e}")
        entries = payload.split("^")
        for entry in entries:
            fields = entry.split("~")
            if len(fields) < 3:
                continue
            market, code, name = fields[0], fields[1], fields[2]
            if market in ("sh", "sz", "bj") and code.isdigit() and len(code) == 6:
                label = {"sh": "沪A", "sz": "深A", "bj": "北交所"}.get(market, market)
                results.append((code, name, label))
            elif market == "hk" and code.isdigit():
                results.append((code.zfill(5), name, "港股"))
            elif market in ("fund_etf", "fund_etf_qt", "jj"):
                results.append((code, name, "ETF" if "etf" in market else "基金"))
            if len(results) >= 5:
                break
    except Exception as e:
        log.warning("data", f"腾讯搜索异常: {e}")
    return results


def search_local(keyword: str, stocks: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """本地模糊匹配 (搜索降级方案)"""
    keyword = keyword.strip().upper()
    if not keyword:
        return stocks[:5]
    results = []
    for code, name, sector in stocks:
        if keyword in code or keyword in name.upper() or keyword in sector.upper():
            results.append((code, name, sector))
            if len(results) >= 5:
                break
    return results


# ──────────────────────────────────────────────
#  数据源 4: K线数据 — 腾讯(主) / 东方财富(备)
# ──────────────────────────────────────────────

@RateLimiter.min_interval(MIN_INTERVAL_TENCENT)
def fetch_kline_tencent(code: str, period: str = "day", count: int = 300) -> List[dict]:
    """
    腾讯K线 (前复权)
    period: day / week / month
    返回: [{"date": str, "open": float, "close": float, "high": float, "low": float, "volume": float}, ...]
    """
    symbol = _code_to_symbol(code)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},{period},,,{count},qfq"
    try:
        text = _http_get(url, timeout=10)
        data = json.loads(text)
        stock_data = data.get("data", {}).get(symbol, {})
        kkey = f"qfq{period}"
        klines = stock_data.get(kkey) or stock_data.get(period) or []
        result = []
        for k in klines:
            try:
                if len(k) < 5:
                    continue
                o, c, h, l = _safe_float(k[1]), _safe_float(k[2]), _safe_float(k[3]), _safe_float(k[4])
                if o <= 0 or c <= 0:
                    continue
                result.append({
                    "date": k[0],
                    "open": o,
                    "close": c,
                    "high": h,
                    "low": l,
                    "volume": _safe_float(k[5]) if len(k) > 5 else 0,
                })
            except (ValueError, IndexError):
                continue
        return result
    except Exception as e:
        log.warning("data", f"腾讯K线异常: {e}")
        return []


@RateLimiter.min_interval(MIN_INTERVAL_EASTMONEY)
def fetch_kline_eastmoney(code: str, period: str = "daily", count: int = 300) -> List[dict]:
    """
    东方财富K线 (备用)
    period: daily / weekly / monthly
    """
    market = "1" if code.startswith("6") else "0"
    klt_map = {"daily": 101, "weekly": 102, "monthly": 103}
    klt = klt_map.get(period, 101)
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt={klt}&fqt=1&end=20500101&lmt={count}"
    )
    try:
        text = _http_get(url, timeout=10)
        data = json.loads(text)
        klines = data.get("data", {}).get("klines", [])
        result = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 6:
                o, c, h, l = _safe_float(parts[1]), _safe_float(parts[2]), _safe_float(parts[3]), _safe_float(parts[4])
                if o <= 0 or c <= 0:
                    continue
                result.append({
                    "date": parts[0],
                    "open": o,
                    "close": c,
                    "high": h,
                    "low": l,
                    "volume": _safe_float(parts[5]),
                })
        return result
    except Exception as e:
        log.warning("data", f"东方财富K线异常: {e}")
        return []


# ──────────────────────────────────────────────
#  数据源 5: 分时数据 — 腾讯(主) / 东方财富(备) / AkShare(兜底)
# ──────────────────────────────────────────────

@RateLimiter.min_interval(MIN_INTERVAL_TENCENT)
def fetch_minute_tencent(code: str) -> List[dict]:
    """
    腾讯分时数据 (使用 query 接口，get 接口已废弃)
    返回: [{"time": str, "price": float, "volume": float, "amount": float}, ...]
    """
    symbol = _code_to_symbol(code)
    url = f"https://ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}"
    try:
        text = _http_get(url, timeout=8)
        data = json.loads(text)
        if data.get("code") != 0:
            log.warning("data", f"腾讯分时接口返回错误: {data.get('msg', '')}")
            return []
        minute_data = (
            data.get("data", {})
            .get(symbol, {})
            .get("data", {})
            .get("data", [])
        )
        result = []
        for item in minute_data:
            # 格式: "0930 1444.00 234 33789600.00"
            parts = item.split(" ")
            if len(parts) >= 3:
                t = parts[0]
                # 补齐为 HH:MM 格式
                if len(t) == 4:
                    t = f"{t[:2]}:{t[2:]}"
                result.append({
                    "time": t,
                    "price": float(parts[1]),
                    "volume": float(parts[2]),
                    "amount": float(parts[3]) if len(parts) > 3 else 0,
                })
        return result
    except Exception as e:
        log.warning("data", f"腾讯分时异常: {e}")
        return []


def fetch_minute_akshare(code: str, period: str = "1") -> List[dict]:
    """
    AkShare 分钟K线 (备用)
    period: 1 / 5 / 15 / 30 / 60
    需要安装: pip install akshare
    """
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist_min_em(symbol=code, period=period, adjust="qfq")
        result = []
        for _, row in df.iterrows():
            result.append({
                "time": str(row.get("时间", "")),
                "open": float(row.get("开盘", 0)),
                "close": float(row.get("收盘", 0)),
                "high": float(row.get("最高", 0)),
                "low": float(row.get("最低", 0)),
                "volume": float(row.get("成交量", 0)),
            })
        return result
    except ImportError:
        log.warning("data", "AkShare 未安装, pip install akshare")
        return []
    except Exception as e:
        log.warning("data", f"AkShare 分钟线异常: {e}")
        return []


@RateLimiter.min_interval(MIN_INTERVAL_EASTMONEY)
def fetch_minute_eastmoney(code: str, period: int = 1, count: int = 240) -> List[dict]:
    """
    东方财富历史分钟K线 (历史数据)
    period: 1 / 5 / 15 / 30 / 60 (分钟)
    返回: [{"time": str, "open": float, "close": float, "high": float, "low": float, "volume": float}, ...]
    """
    market = "1" if code.startswith(("5", "6")) else "0"
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={market}.{code}&fields1=f1,f2,f3,f4,f5,f6"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57"
        f"&klt={period}&fqt=1&end=20500101&lmt={count}"
    )
    try:
        text = _http_get(url, timeout=10)
        data = json.loads(text)
        klines = data.get("data", {}).get("klines", [])
        result = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 6:
                result.append({
                    "time": parts[0],        # "2026-04-13 09:35"
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]),
                    "price": float(parts[2]), # close 作为当前价，兼容分时图格式
                })
        return result
    except Exception as e:
        log.warning("data", f"东方财富分钟K线异常: {e}")
        return []


@RateLimiter.min_interval(MIN_INTERVAL_EASTMONEY)
def fetch_minute_eastmoney_realtime(code: str) -> List[dict]:
    """
    东方财富当日实时分时数据 (trends2 接口)
    返回: [{"time": str, "price": float, "avg_price": float, "volume": float, "amount": float}, ...]
    这是当日逐笔分时，非历史K线，适合分时图展示和实时刷新

    API 实际返回字段 (fields2=f51,f52,f53,f54,f55,f56,f57,f58):
      [0] 时间  [1] 开盘(=价格)  [2] 均价  [3] 最高  [4] 最低  [5] 成交量  [6] 成交额  [7] 未知
    """
    market = "1" if code.startswith(("5", "6")) else "0"
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/trends2/get?"
        f"secid={market}.{code}"
        f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
        f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
        f"&iscr=0&iscca=0&ut=fa5fd1943c7b386f172d6893dbfba10b"
    )
    try:
        text = _http_get(url, timeout=10)
        data = json.loads(text)
        trends = data.get("data", {}).get("trends", [])
        if not trends:
            return []
        result = []
        for trend in trends:
            # 实际格式: "时间,价格,均价,最高,最低,成交量,成交额,..."
            parts = trend.split(",")
            if len(parts) >= 7:
                result.append({
                    "time": parts[0],
                    "price": _safe_float(parts[1]),
                    "avg_price": _safe_float(parts[2]),
                    "volume": _safe_float(parts[5]),
                    "amount": _safe_float(parts[6]),
                })
            elif len(parts) >= 5:
                # 兼容: 部分数据源可能只返回5个字段
                result.append({
                    "time": parts[0],
                    "price": _safe_float(parts[1]),
                    "avg_price": _safe_float(parts[2]),
                    "volume": _safe_float(parts[3]) if len(parts) > 3 else 0,
                    "amount": _safe_float(parts[4]) if len(parts) > 4 else 0,
                })
        return result
    except Exception as e:
        log.warning("data", f"东方财富实时分时异常: {e}")
        return []


# ──────────────────────────────────────────────
#  路由器: 统一调度入口
# ──────────────────────────────────────────────

class DataRouter:
    """
    统一数据路由器
    根据数据类型自动选择最优数据源，失败时降级到备用源
    """

    def __init__(self):
        """初始化"""
        self._stock_list: List[Tuple[str, str, str]] = []
        self._last_refresh = 0
        self._lock = Lock()
        self._akshare_available = self._check_akshare()

    @staticmethod
    def _check_akshare() -> bool:
        try:
            import akshare
            return True
        except ImportError:
            return False

    def close(self) -> None:
        """关闭数据路由，释放连接资源"""
        close_session()

    # ---- 股票列表 ----

    def get_stock_list(self, force_refresh: bool = False) -> List[Tuple[str, str, str]]:
        """
        获取股票列表 (优先本地缓存 → 东方财富 → 空)
        缓存有效期: 7天
        """
        with self._lock:
            now = time.time()

            # 强制刷新模式: 跳过所有缓存直接拉取
            if force_refresh:
                stocks = fetch_stock_list_eastmoney()
                if stocks:
                    self._stock_list = stocks
                    self._last_refresh = now
                    save_stock_list_cache(stocks)
                    return stocks
                # 强制刷新失败也降级到缓存
                return self._stock_list if self._stock_list else load_stock_list_cached()

            # 正常模式: 内存缓存 → 本地文件缓存 → 网络拉取
            # 1. 内存缓存 (最快)
            if self._stock_list and (now - self._last_refresh) < STOCK_LIST_CACHE_TTL:
                return self._stock_list

            # 2. 本地文件缓存
            cached = load_stock_list_cached()
            if cached:
                try:
                    file_age = now - os.path.getmtime(CACHE_STOCKS)
                except OSError:
                    file_age = float('inf')

                if file_age < STOCK_LIST_CACHE_TTL:
                    # 文件缓存有效，同步到内存
                    self._stock_list = cached
                    self._last_refresh = now
                    return cached
                # 文件过期，继续尝试网络

            # 3. 从东方财富拉取
            stocks = fetch_stock_list_eastmoney()
            if stocks:
                self._stock_list = stocks
                self._last_refresh = now
                save_stock_list_cache(stocks)
                return stocks

            # 4. 全部失败，降级到任何可用缓存
            if cached:
                self._stock_list = cached
                return cached
            return self._stock_list if self._stock_list else []

    # ---- 搜索 ----

    def search(self, keyword: str) -> List[Tuple[str, str, str]]:
        """
        搜索股票: 腾讯 smartbox → 本地模糊匹配
        支持 "518800" / "518800 黄金ETF国泰" / "黄金ETF国泰" 格式
        """
        # 腾讯 smartbox 不支持代码+名称混合查询，先提取纯代码
        import re
        code_match = re.search(r'\b(\d{6})\b', keyword)
        search_key = code_match.group(1) if code_match else keyword

        results = fetch_search_tencent(search_key)
        if results:
            return results

        # 二次尝试：用原始关键词（可能是纯名称）
        if search_key != keyword:
            results = fetch_search_tencent(keyword)
            if results:
                return results

        log.debug("data", f"腾讯搜索无结果: {keyword}", "降级到本地模糊匹配")
        stocks = self.get_stock_list()
        return search_local(keyword, stocks)

    # ---- 实时行情 ----

    def get_realtime(self, codes: List[str]) -> Dict[str, dict]:
        """
        获取实时行情: 腾讯批量 → 新浪逐只
        """
        result = fetch_realtime_tencent(codes)

        # 对腾讯没返回的，尝试新浪
        missing = [c for c in codes if c not in result]
        if missing:
            log.warning("data", f"\u817e\u8baf\u884c\u60c5\u7f3a\u5931 {len(missing)} \u53ea",
                        "\u964d\u7ea7\u5230\u65b0\u6d6a: " + ", ".join(missing))
            for code in missing:
                sina_data = fetch_realtime_sina(code)
                if sina_data:
                    result[code] = sina_data
                else:
                    log.error("data", f"\u65b0\u6d6a\u884c\u60c5\u4e5f\u5931\u8d25: {code}",
                              "\u8be5\u80a1\u7968\u65e0\u53ef\u7528\u5b9e\u65f6\u4ef7\u683c")
                time.sleep(0.3)

        return result

    # ---- K线 ----

    def get_kline(self, code: str, period: str = "day", count: int = 300) -> List[dict]:
        """
        获取K线: 腾讯 → 东方财富
        period: day / week / month
        返回的数据会进行数值清洗，防止字符串/None混入
        """
        klines = fetch_kline_tencent(code, period, count)
        if klines:
            return _clean_kline_data(klines)
        log.debug("data", f"腾讯K线为空: {code} {period}", "降级到东方财富")
        em_period = {"day": "daily", "week": "weekly", "month": "monthly"}
        em_klines = fetch_kline_eastmoney(code, em_period.get(period, "daily"), count)
        return _clean_kline_data(em_klines)

    # ---- 分时 ----

    # 全局超时: 降级链总耗时上限（秒），避免用户等待过久
    GLOBAL_TIMEOUT = 15

    def get_minute(self, code: str) -> List[dict]:
        """
        获取当日实时分时数据:
        东方财富trends2(当日实时分时) → 腾讯minute → 东方财富历史分钟K线 → AkShare
        全局超时 15 秒，超时时中止后续降级尝试
        返回的数据会进行数值清洗，防止字符串/None混入
        """
        import time
        start_time = time.time()

        def _timed_out() -> bool:
            return (time.time() - start_time) > self.GLOBAL_TIMEOUT

        # 1. 东方财富 trends2 接口 (当日实时分时，数据最全)
        data = fetch_minute_eastmoney_realtime(code)
        if data:
            return _clean_minute_data(data)
        if _timed_out():
            log.warning("data", f"{code} 全局超时", "第1级后中止")
            return []

        # 2. 腾讯分时
        log.debug("data", f"东方财富实时分时为空: {code}", "降级到腾讯minute")
        data = fetch_minute_tencent(code)
        if data:
            return _clean_minute_data(data)
        if _timed_out():
            log.warning("data", f"{code} 全局超时", "第2级后中止")
            return []

        # 3. 东方财富历史分钟K线 (兜底)
        log.debug("data", f"腾讯分时为空: {code}", "降级到东方财富1分钟K线")
        data = fetch_minute_eastmoney(code, period=1, count=240)
        if data:
            return _clean_minute_data(data)
        if _timed_out():
            log.warning("data", f"{code} 全局超时", "第3级后中止")
            return []

        # 4. AkShare (最后兜底)
        log.debug("data", f"东方财富分钟线为空: {code}", "降级到AkShare 5分钟K线")
        if self._akshare_available:
            data = fetch_minute_akshare(code, period="5")
            return _clean_minute_data(data)

        return []

    # ---- 市场状态 ----

    @staticmethod
    def get_market_status() -> str:
        """判断当前A股市场状态: open / closed"""
        from datetime import datetime, time as dtime
        now = datetime.now()
        if now.weekday() >= 5:
            return "closed"
        t = now.time()
        morning = dtime(9, 30) <= t <= dtime(11, 30)
        afternoon = dtime(13, 0) <= t <= dtime(15, 0)
        return "open" if (morning or afternoon) else "closed"

    # ---- 当日+昨日分时 (回测专用) ----

    @staticmethod
    def _build_backtest_record(item: dict, date_prefix: str = "") -> dict:
        """将原始分时数据转为回测标准格式（统一清洗）"""
        time_str = item.get("time", "")
        if date_prefix:
            time_str = f"{date_prefix} {time_str}"
        price = _safe_float(item.get("price"), 0.0)
        return {
            "time": time_str,
            "price": price,
            "open": _safe_float(item.get("open", price), 0.0),
            "close": _safe_float(item.get("close", price), 0.0),
            "high": _safe_float(item.get("high", price), 0.0),
            "low": _safe_float(item.get("low", price), 0.0),
            "volume": _safe_float(item.get("volume", 0), 0),
        }

    def _fetch_today_minute(self, code: str, now: datetime) -> List[dict]:
        """获取今日分时数据（腾讯），含时间校验"""
        if now.weekday() >= 5:
            return []
        market_open = now.replace(hour=9, minute=30, second=0)
        if now < market_open:
            return []

        today_str = now.strftime("%Y-%m-%d")
        today_raw = fetch_minute_tencent(code)
        if not today_raw:
            return []

        last_time = today_raw[-1].get("time", "")
        if not self._is_valid_today_data(last_time, now):
            log.warning("data", f"腾讯分时数据时间异常: 最后时间={last_time}，丢弃今日数据")
            return []

        result = [self._build_backtest_record(item, today_str) for item in today_raw]
        log.signal_log("data", f"回测今日: {code} 分时 × {len(result)}", "来源: 腾讯")
        return result

    def _fetch_fallback_minute(self, code: str) -> List[dict]:
        """降级获取分时数据：东财实时 → 空"""
        em_data = fetch_minute_eastmoney_realtime(code)
        if not em_data:
            return []
        result = [self._build_backtest_record(item) for item in em_data]
        log.signal_log("data", f"回测降级: {code} 分时 × {len(result)}", "来源: 东方财富")
        return result

    def get_minute_for_backtest(self, code: str, **kwargs) -> List[dict]:
        """
        获取当日 + 昨日分时数据供回测使用
        如果今日未开盘/数据不可用，自动只用昨日数据
        全局超时 20 秒，超时时中止后续降级尝试
        """
        import time as _time
        from datetime import datetime as _dt

        _start_time = _time.time()
        _GLOBAL_TIMEOUT = 20

        def _timed_out() -> bool:
            return (_time.time() - _start_time) > _GLOBAL_TIMEOUT

        now = _dt.now()
        result = []

        # ── 今日数据 ──
        result = self._fetch_today_minute(code, now)
        if _timed_out():
            log.warning("data", f"{code} 回测数据全局超时", "今日数据后中止")
            return result

        # ── 昨日数据 ──
        yesterday_raw = self._fetch_yesterday_minute(code)
        if yesterday_raw:
            cleaned = [self._build_backtest_record(item) for item in yesterday_raw if isinstance(item, dict)]
            result = cleaned + result  # 昨日在前，今日在后
            log.signal_log("data", f"回测昨日: {code} 分时 × {len(cleaned)}", "来源: 东方财富trends2")

        if _timed_out():
            log.warning("data", f"{code} 回测数据全局超时", "昨日数据后中止")
            return result

        # ── 降级: 东财实时 ──
        if not result:
            result = self._fetch_fallback_minute(code)

        total = len(result)
        if total:
            log.signal_log("data", f"回测数据总计: {code} {total} 根K线",
                           f"跨度: {result[0]['time']} ~ {result[-1]['time']}")
        return result

    @staticmethod
    def _is_valid_today_data(last_time_str: str, now: datetime) -> bool:
        """
        校验腾讯分时数据是否真实(非缓存)
        如果最后一条时间超过了当前时间，说明是缓存的旧数据
        """
        try:
            # last_time_str 格式: "HH:MM"
            if ":" not in last_time_str:
                return False
            parts = last_time_str.split(":")
            data_hour = int(parts[0])
            data_min = int(parts[1])
            # 数据时间不应超过当前时间(给5分钟容差)
            data_total_min = data_hour * 60 + data_min
            now_total_min = now.hour * 60 + now.minute
            return data_total_min <= now_total_min + 5
        except (ValueError, IndexError):
            return False

    @staticmethod
    def _fetch_yesterday_minute(code: str) -> List[dict]:
        """
        通过东财 trends2 iscr=1 获取昨日分时数据
        字段: [0]时间 [1]开盘 [2]均价 [3]最高 [4]最低 [5]成交量 [6]成交额 [7]未知
        """
        market = "1" if code.startswith(("5", "6")) else "0"
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/trends2/get?"
            f"secid={market}.{code}"
            f"&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
            f"&iscr=1&iscca=0"
        )
        try:
            text = _http_get(url, timeout=10, retries=1, retry_delay=1.0)
            data = json.loads(text)
            trends = data.get("data", {}).get("trends", [])
            if not trends:
                return []
            result = []
            for trend in trends:
                parts = trend.split(",")
                if len(parts) >= 6:
                    p = float(parts[1])
                    result.append({
                        "time": parts[0],
                        "price": p,
                        "open": float(parts[1]),
                        "close": p,
                        "high": float(parts[3]) if len(parts) > 3 else p,
                        "low": float(parts[4]) if len(parts) > 4 else p,
                        "volume": float(parts[5]) if parts[5] else 0,
                    })
            return result
        except Exception as e:
            log.warning("data", f"东财昨日分时异常: {e}")
            return []

    # ---- 数据源状态 ----

    def get_source_status(self) -> dict:
        """返回各数据源可用状态"""
        return {
            "akshare": self._akshare_available,
            "tencent": True,   # 动态检测太重，默认可用
            "sina": True,
            "eastmoney": True,
        }
