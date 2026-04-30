# Xm-LH 智能持仓管理系统

## 系统概述
基于 ML 驱动的股票买卖决策系统，集成 LightGBM 机器学习策略、多源数据路由、轻量日志系统和主题切换。

## 七大功能模块

| # | 模块 | 说明 |
|---|---|---|
| 1 | 💰 **我的持仓** | 持仓管理表格，支持搜索添加/删除，数据本地持久化 |
| 2 | 📈 **行情走势** | ECharts 实时 K 线图（分时/日K/周K/月K），MA均线 |
| 3 | 🧠 **ML 策略** | LightGBM 模型状态面板，信号配置，训练流水线 |
| 4 | 📋 **日志** | 交易日志 + 操作日志实时订阅，分级筛选 |
| 5 | 📊 **回测分析** | ML 驱动的历史回测，收益曲线，策略 vs 基准对比 |
| 6 | ⚙️ **系统设置** | 主题切换(浅色/深色/晴蓝) + 其他设置 |
| 7 | 🔍 **缠论信号** | 缠论信号融合面板，辅助 ML 决策 |

## ML 策略引擎

系统使用 LightGBM 机器学习模型生成 BUY / SELL / HOLD 信号，替代传统硬编码规则策略。

### 架构

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  K线数据     │───▶│  34维特征工程  │───▶│  ML 模型     │
│  OHLCV      │    │  features.py │    │  model.py   │
└─────────────┘    └──────────────┘    └──────┬──────┘
                                              │
                   ┌──────────────┐    ┌──────▼──────┐
                   │  自动标注     │───▶│  信号输出     │
                   │  labeler.py  │    │  BUY/SELL/  │
                   └──────────────┘    │  HOLD       │
                                       └──────┬──────┘
                                              │
                   ┌──────────────┐    ┌──────▼──────┐
                   │  缠论信号融合  │───▶│  最终决策     │
                   │  chanlun.py  │    │  综合信号     │
                   └──────────────┘    └─────────────┘
```

### 37 维特征（10维基础 + 24维扩展 + 3维缠论扩展）

#### 基础特征（10维）

| # | 特征 | 说明 |
|---|---|---|
| 1 | feat_return | 涨跌幅 |
| 2 | feat_body_ratio | K线实体占比 |
| 3 | feat_upper_shadow | 上影线比例 |
| 4 | feat_lower_shadow | 下影线比例 |
| 5 | feat_volume_ratio | 成交量/20均量 |
| 6 | feat_atr5 | 5日真实波幅 |
| 7 | feat_vwap_dev | VWAP偏离度 |
| 8 | feat_ma_aligned | 均线排列(MA5>MA10>MA20) |
| 9 | feat_time_sin | 日内时间正弦编码 |
| 10 | feat_vol_regime | 波动率状态 |

#### 扩展特征（24维）

| # | 特征 | 说明 |
|---|---|---|
| 11 | feat_rsi14 | 14日RSI |
| 12 | feat_macd | MACD柱状值 |
| 13 | feat_boll_width | 布林带宽度 |
| 14 | feat_boll_pos | 布林带位置 |
| 15 | feat_kdj_k | KDJ K值 |
| 16 | feat_kdj_d | KDJ D值 |
| 17 | feat_kdj_j | KDJ J值 |
| 18 | feat_cci | 商品通道指数 |
| 19 | feat_williams_r | 威廉指标 |
| 20 | feat_obv_slope | OBV斜率 |
| 21 | feat_mfi | 资金流量指数 |
| 22 | feat_adx | 趋势强度ADX |
| 23 | feat_di_plus | DI+方向指标 |
| 24 | feat_di_minus | DI-方向指标 |
| 25 | feat_ema_cross | EMA交叉信号 |
| 26 | feat_trend_strength | 趋势强度 |
| 27 | feat_price_accel | 价格加速度 |
| 28 | feat_volume_momentum | 成交量动量 |
| 29 | feat_high_low_range | 振幅比率 |
| 30 | feat_close_position | 收盘位置 |
| 31 | feat_gap | 跳空缺口 |
| 32 | feat_consecutive | 连续涨跌天数 |
| 33 | feat_price_entropy | 价格熵 |
| 34 | feat_vol_skew | 成交量偏度 |
| 35 | feat_cl_seg_dir | 缠论线段方向 |
| 36 | feat_cl_seg_has_hub | 缠论线段含中枢 |
| 37 | feat_cl_volume_div | 缠论成交量背驰 |

### 训练流水线

```bash
# 完整流水线: 标注 → 训练 → 测试
python scripts/train_pipeline.py

# 分步执行
python scripts/train_pipeline.py label    # 仅标注
python scripts/train_pipeline.py train    # 仅训练
python scripts/train_pipeline.py test     # 仅测试
```

### 模型回退机制

当没有训练好的模型时，系统自动降级为规则回退模式（简单趋势跟踪），确保策略始终可用：

```
有模型 → LightGBM 预测 → BUY/SELL/HOLD
无模型 → 规则回退 → 基于涨跌+VWAP偏离判断
```

## 多源数据路由

系统采用多源数据路由器（`DataRouter`），按数据类型分发到不同接口，避免单一来源限频：

```
┌──────────────────┬─────────────────────────┬───────────────────────────┐
│ 数据类型          │ 主数据源                 │ 备用降级                    │
├──────────────────┼─────────────────────────┼───────────────────────────┤
│ 股票/ETF列表      │ 东方财富 push2 (全量)    │ 本地缓存 stocks.json       │
│ 实时行情(批量)    │ 腾讯 qt (逗号拼接50+)    │ 新浪逐只降级                │
│ 实时行情(单只)    │ 腾讯 qt                  │ 新浪 hq.sinajs.cn          │
│ 搜索/自动补全     │ 腾讯 smartbox            │ 本地列表模糊匹配            │
│ K线 日/周/月      │ 腾讯 ifzq (前复权)       │ 东方财富 kline (备)         │
│ 分时数据          │ 腾讯 minute              │ AkShare 5min降级            │
│ 分钟K线(1/5/15)  │ AkShare hist_min_em      │ 腾讯 minute                │
│ 全市场快照        │ AkShare spot_em          │ 东方财富 push2              │
└──────────────────┴─────────────────────────┴───────────────────────────┘
```

### 降级机制

```
主数据源 ✅ → 返回数据
主数据源 ❌ → 备用数据源 ✅ → 返回数据
主数据源 ❌ → 备用数据源 ❌ → 本地缓存 / 空
```

## 日志系统

轻量级日志系统 (`logger.py`)，实时捕获异常和策略信号：

- **3 个级别**: `warning` (数据降级/超时) / `error` (全挂) / `signal` (策略触发/持仓变动)
- **4 个分类**: `data` / `strategy` / `position` / `system`
- **线程安全**: 单例 + 环形缓冲区 500 条
- **实时推送**: PyQt5 signal 连接 UI，绿色●指示灯

## 主题系统

三套主题即时切换，通过全局 QSS + 颜色令牌统一管理：

| 主题 | 背景 | 主色 | 风格 |
|---|---|---|---|
| 浅色 | `#ffffff` | `#1a73e8` | 清爽明亮，默认 |
| 深色 | `#1a1d24` | `#4a9eff` | 护眼低亮度 |
| 晴蓝 | `#f0f4fa` | `#2563eb` | 柔和蓝调，沉稳 |

图表 (ECharts) 也跟随主题切换：背景、坐标轴、网格线、tooltip、缩放条全部联动。

## 技术栈

- **UI 框架**: PyQt5 + QWebEngineView
- **图表渲染**: ECharts 5.4 (CDN 加载)
- **ML 引擎**: LightGBM + scikit-learn
- **特征工程**: pandas, numpy
- **数据获取**: 多源路由 (腾讯/东方财富/新浪/AkShare)
- **日志**: 轻量单例 Logger，PyQt5 signal 实时推送
- **主题**: 颜色令牌 + QSS 全局样式表
- **并发**: threading + pyqtSignal，线程安全

## 安装依赖

```bash
# 必需依赖
pip install PyQt5 PyQtWebEngine pandas numpy requests

# ML 依赖 (训练和推理)
pip install lightgbm scikit-learn

# 可选依赖 (解锁分钟级K线和全市场快照)
pip install akshare
```

## 运行方式

```bash
python main.py
```

## 项目结构

```
meme/
├── main.py                          # 程序入口
├── train_pipeline.py                # ML 训练流水线
├── requirements.txt                 # Python 依赖
├── logger.py                        # 轻量日志系统 (单例, 3级别)
├── test_all_features.py             # 全功能测试脚本
├── data/                            # 运行时数据 (自动生成)
│   ├── positions.json               # 持仓数据
│   ├── stocks.json                  # 股票列表缓存
│   ├── klines/                      # K线数据 (CSV)
│   ├── labeled/                     # 自动标注结果
│   └── ml/models/                   # 训练好的模型 (.pkl)
├── data_sources/                    # 多源数据层
│   ├── __init__.py
│   └── router.py                    # DataRouter 统一路由器
├── strategies/
│   ├── __init__.py                  # 模块导出
│   ├── signal.py                    # Signal / SignalType 统一信号格式
│   ├── engine.py                    # MLEngine 策略引擎
│   ├── backtest_engine.py           # ML 驱动的 T+0 回测引擎
│   ├── ml/
│   │   ├── model.py                 # LightGBM 模型封装 + 规则回退
│   │   └── trainer.py               # 模型训练与保存
│   └── data/
│       ├── features.py              # 34 维特征工程 (基础+扩展)
│       └── labeler.py               # 自动标注 (局部极值配对)
└── ui/
    ├── __init__.py
    ├── main_window.py               # 主窗口界面 (七页布局)
    ├── theme.py                     # 主题系统 (3套主题, QSS生成)
    └── panels/
        ├── __init__.py
        ├── strategy.py              # ML 模型状态面板
        ├── backtest.py              # 回测分析页
        ├── signal.py                # 策略信号面板
        ├── position.py              # 持仓管理
        ├── chart.py                 # K线图
        ├── log.py                   # 日志面板
        ├── header.py                # 顶部状态栏
        ├── search.py                # 搜索组件
        └── settings.py              # 设置页
```

### 核心模块说明

| 模块 | 职责 | 行数 |
|---|---|---|
| `ui/main_window.py` | PyQt5 主窗口，7 个页面组件 | ~2100 |
| `data_sources/router.py` | 统一数据路由器，多源获取+降级 | ~580 |
| `strategies/engine.py` | ML 策略引擎，统一信号生成 | ~130 |
| `strategies/backtest_engine.py` | ML 驱动的 T+0 回测引擎 | ~300 |
| `strategies/ml/model.py` | LightGBM 模型封装 + 规则回退 | ~120 |
| `strategies/ml/trainer.py` | 模型训练、评估、保存 | ~150 |
| `strategies/data/features.py` | 34 维特征工程（基础+扩展） | ~250 |
| `strategies/data/labeler.py` | 自动标注 (局部极值配对) | ~170 |
| `ui/theme.py` | 主题系统，颜色令牌+QSS生成 | ~230 |
| `logger.py` | 轻量日志系统，线程安全单例 | ~100 |
| `main.py` | 入口，异常捕获写 error.log | ~20 |

## 更新日志

### v2.0 (2026-04-15)
- **全面重构**: 从规则策略切换为 ML 机器学习策略
- 删除旧策略: `core_strategies.py` (动态成本摊薄/移动止盈/日内做T)
- 删除旧策略: `market_regime.py` (市场环境检测/自适应参数)
- 新增 `strategies/signal.py`: 统一信号格式 (Signal/SignalType)
- 新增 `strategies/engine.py`: MLEngine 策略引擎
- 新增 `strategies/ml/model.py`: LightGBM 模型封装 + 规则回退
- 新增 `strategies/ml/trainer.py`: LGBMClassifier 训练与保存
- 新增 `strategies/data/features.py`: 34 维特征工程 (10维基础 + 24维扩展)
- 新增 `strategies/data/labeler.py`: 自动标注 (局部极值配对)
- 重写 `strategies/backtest_engine.py`: ML 驱动的回测引擎
- 重写 `ui/panels/strategy.py`: ML 模型状态面板
- 新增 `train_pipeline.py`: 一键训练流水线 (标注→训练→测试)
- 支持模型回退: 无模型时自动降级为规则模式

### v1.2 (2026-04-12)
- 新增 `logger.py` 轻量日志系统，3级别(s/w/e) + 4分类，PyQt5 signal 实时推送
- 新增 `ui/theme.py` 主题系统，浅色/深色/晴蓝三套主题即时切换
- K线图和分时图跟随主题色（背景/坐标轴/网格/tooltip/缩放条）
- 设置页重做：主题卡片选择器 + 静态设置展示
- 修复搜索框快速删除字符导致的 UI 冻结（线程堆积）
- 线程安全审计：所有后台线程改用 signal/invokeMethod 回主线程

### v1.1 (2026-04-12)
- 新增 `data_sources/` 多源数据层，统一路由所有数据获取
- 合并 SearchInput / PopupSearchInput 为统一组件
- K线/搜索/行情 支持多源降级
- AkShare 集成 (可选)

### v1.0
- 初始版本，PyQt5 桌面应用 + 三大策略
