# Xm-LH 智能持仓管理系统

基于 LightGBM 机器学习的股票买卖决策系统，集成多源数据路由、实时行情、回测分析和主题切换。

## 功能模块

| 模块 | 说明 |
|---|---|
| 💰 我的持仓 | 持仓管理表格，搜索添加/删除，数据本地持久化 |
| 📈 行情走势 | ECharts 实时 K 线图（分时/日K/周K/月K），MA 均线 |
| 🧠 ML 策略 | LightGBM 模型训练、信号配置、参数微调、自动优化 |
| 📋 日志 | 交易日志 + 操作日志实时订阅，分级筛选 |
| 📊 回测分析 | ML 驱动的历史回测，收益曲线，蒙特卡洛模拟 |
| ⚙️ 系统设置 | 主题切换（浅色/深色/晴蓝）、止损策略、通知设置 |

## 技术栈

- **UI**: PyQt5 + QWebEngineView + ECharts 5.4
- **ML**: LightGBM + scikit-learn
- **数据**: 多源路由（腾讯/东方财富/新浪/AkShare）
- **特征**: 34 维特征工程（基础 + 扩展 + 缠论）
- **并发**: threading + pyqtSignal，线程安全

## 安装

```bash
pip install -r requirements.txt

# 可选：分钟级K线和全市场快照
pip install akshare
```

## 运行

```bash
python main.py
```

## 项目结构

```
meme/
├── main.py                    # 入口
├── config.yaml                # 配置文件
├── requirements.txt           # 依赖
├── core/
│   ├── config.py              # 配置管理
│   └── logger.py              # 日志系统（单例，3级别4分类）
├── data_sources/
│   └── router.py              # 多源数据路由器
├── strategies/
│   ├── signal.py              # 统一信号格式
│   ├── engine.py              # ML 策略引擎
│   ├── backtest_engine_v2.py  # 回测引擎
│   ├── monitor.py             # 信号监控
│   ├── ml/
│   │   ├── model.py           # LightGBM 模型封装
│   │   └── trainer.py         # 模型训练
│   ├── data/
│   │   ├── features.py        # 34 维特征工程
│   │   └── labeler.py         # 自动标注
│   └── optimization/
│       └── param_optimizer.py # 参数网格搜索
├── ui/
│   ├── main_window.py         # 主窗口（7 页布局）
│   ├── theme.py               # 主题系统（3 套主题）
│   └── panels/                # 各页面面板
├── data/                      # 运行时数据（自动生成）
│   ├── positions.json         # 持仓数据
│   ├── klines/                # K 线 CSV
│   ├── labeled/               # 标注结果
│   └── ml/models/             # 训练好的模型
└── scripts/                   # 工具脚本
```

## ML 训练流水线

系统内置 9 步训练流水线（面板中一键触发）：

1. 数据采集 → 2. 数据质检 → 3. 自动标注 → 4. 构建样本 → 5. 特征筛选 → 6. 数据划分 → 7. 模型训练 → 8. 模型评估 → 9. 保存部署

无训练模型时自动降级为规则回退模式。

## 数据源

| 数据类型 | 主数据源 | 备用 |
|---|---|---|
| 实时行情 | 腾讯 qt | 新浪 hq |
| K 线日/周/月 | 腾讯 ifzq | 东方财富 kline |
| 分时数据 | 腾讯 minute | AkShare 5min |
| 搜索/自动补全 | 腾讯 smartbox | 本地模糊匹配 |
