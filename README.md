# Backtrader ETF 回测与研究结果验收

本项目做两件事：用事件驱动回测验证策略收益怎么产生；用代码约束哪些运行结果可以被后续研究流程读取。没有 LLM 提示词链，也不是完整的 Agent harness。

**2026-10-17 更正：** 上一版自写引擎用次日开盘价反算可买股数，不能代表“前收盘已提交固定数量订单”。已将主运行替换为 **Backtrader 1.9.78.123**，旧结果保留为历史。测试通过、哈希一致、机构数字接近，都不等于“所有前视偏差已消除”。

## 两个任务各负责什么

| 层 | 责任 | 核心实现 |
|---|---|---|
| 回测执行 | 按时间推进信号、订单、成交、现金、持仓 | Backtrader Cerebro / SMA / Market / BackBroker；`etf_lab/backtrader_engine.py` 做适配 |
| 执行契约 | 检查信号日固定数量、下一交易日成交、禁用作弊模式 | `etf_lab/execution_contract.py`；每次运行、提交和读取重新检查 |
| 结果可靠性 | 冻结输入、版本匹配、失败拒绝、幂等、历史和完整性 | `etf_lab/artifacts.py`、`etf_lab/handoff.py` |
| 测试 harness | 故障注入、时间前缀对照、负面场景及实际退出码 | `tests/`、`scripts/reproduce.py` |

没有任务书假设的原 CIO 项目；经用户确认，任务二为独立研究结果可靠性验收。没有交易连接、投资判断、CRO 或审批改动。完整 Agent 的模型循环、工具权限、进程沙箱不在本次实现中。

## 数据时序如何约束

1. 固定策略读取流式日线，关闭 `preload`、`runonce`、cheat-on-open、cheat-on-close；不存在预先加载到框架 line buffer 的未来日线。
2. t 日收盘算 SMA20/50 并提交固定数量 Market 订单。股数只依赖当时现金、当时收盘和既定成本，默认用 95% 现金，余量仅作跳空缓冲。
3. t+1 日框架按开盘加声明滑点/冲击尝试成交；钱不够就 Margin 拒单，不事后缩量、不重试凑成交。执行日高低收和成交量不能决定当日开盘成交。
4. 专项测试：未来尾段扰动、多个日期截断重放、次日开盘变化不影响此前订单、未来 line 正向访问失败、同日成交/事后改量拒绝、重新封存的错误策略配置仍拒绝交接。

这是**固定受审策略的因果执行约束**。任意 Python 仍可另读文件/联网；尚无进程级数据沙箱。当前下载的 Yahoo 行情不是历史 point-in-time 数据，分红实际支付日不可得，股票历史成分/退市/财报版本未解决。主适配器拒绝拆股数据。ETF 样板不能外推成选股或历史 LLM 回测可信。

## 交付入口

- [总览](output/index.html)、[新版 Backtrader 主报告](output/runs/spy-backtrader-v1/report.html)。
- [技术复核与问题定位](docs/HARNESS_REVIEW.md)：原实现局限、替换位置和测试边界。
- [新版算法对照](evidence/backtrader-reproduction-v1/existing-comparison/report.html)、[新版可靠性场景](evidence/backtrader-reproduction-v1/isolated-reliability/report.html)。
- [机构文章复现](evidence/paired-replication-v1/report.html)：原文收益、回撤、交易次数均有精确数字，三项偏差低于 10%。**其同收盘成交近似仅用于论文数值重放，不作为无前视偏差的证据。**
- [验收及范围](docs/ACCEPTANCE.md)、[交接契约](docs/INTEGRATION_PLAN.md)、[历史版本与回退](docs/DELIVERY.md)。

## 安装和复现

Python 3.11，PowerShell；私有仓库需要已有访问权限。离线计算使用已冻结数据，不需要行情 API 密钥。

```powershell
git clone --branch experiment/spy-backtest-reliability https://github.com/ninglinLiu/etf-cio-reliability.git
cd etf-cio-reliability
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.venv\Scripts\python.exe scripts/reproduce.py --output scratch/reproduce-001
```

完整命令执行自动测试、回测、独立对账、旧算法对照、论文数值复现和三个可靠性场景。实际命令、退出码和 stdout/stderr 都保留。目录已存在时拒绝覆盖。

```powershell
.venv\Scripts\python.exe -m etf_lab run --snapshot data/spy.json --config config/spy.json --run-id research-001 --output scratch/research
.venv\Scripts\python.exe -m etf_lab case-freeze --snapshot data/spy.json --config config/spy.json --case-id spy-case-001 --output scratch/research
.venv\Scripts\python.exe -m etf_lab research-submit --case-id spy-case-001 --run-id research-001 --submission-id submit-001 --output scratch/research
.venv\Scripts\python.exe -m etf_lab research-read --case-id spy-case-001 --submission-id submit-001 --output scratch/research
```

成功退出 0，计算/模拟数据失败退出 1，失效/冲突/完整性/执行契约拒绝退出 2。必须每次传入当前案例；不能缓存一次 `downstream_eligible=true` 后永远跳过校验。更改框架、参数、配置、数据或费用必须新建案例/运行。

`output/runs/spy-v1`、`spy-v2` 及原有证据仍可按历史完整性校验，但不再是当前有效主回测。实验分支不合并主分支。
