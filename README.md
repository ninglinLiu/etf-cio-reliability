# ETF 回测与研究结果可靠性验收

已完成可逐笔复核的 SPY 样板、真实现有算法对照、机构文章三项数值复现，以及独立研究交接的三个可靠性场景。

**范围变更（2026-10-17 用户确认）：** 用户没有任务书预设的原 CIO 项目，任务二改为独立研究结果可靠性验收。本项目没有接入既有 cio.py，不创建调度框架，也不连接真实投资审批或交易。

## 交付入口

- [SPY 回测 HTML](output/runs/spy-v2/report.html)：嵌入 PNG、完整指标、53 笔成交、2,766 日现金/持仓/净值账本。
- [真实现有算法对比](evidence/existing-comparison-v2/report.html)：固定 finresearch-lab 原提交；同 SPY、同时间端点，策略及成交口径差异明示。
- [机构三指标对标](evidence/paired-replication-v1/report.html)：Alpha Architect 讨论的 Glenn SPY/TLT 研究，对照 IFTA 原文表 1；收益、回撤、交易次数全部相对偏差 ≤10%。
- [独立可靠性三场景](evidence/reliability-v1/report.html)：全部使用隔离合成数据；实际回执、调用日志、失败恢复与篡改副本可查看。
- [验收清单](docs/ACCEPTANCE.md)、[交接契约与接入位置](docs/INTEGRATION_PLAN.md)、[方法边界](docs/METHODOLOGY.md)、[版本、复现与回退](docs/DELIVERY.md)。

## 安装并一次完成离线复现

Python 3.11，PowerShell；命令在仓库根目录执行。冻结数据已随仓库保存，不需要数据 API 密钥。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.venv\Scripts\python.exe scripts/reproduce.py --output scratch/reproduce-001
```

该命令执行 42 项测试、主回测、当前输入校验、独立现金/股数对账、真实旧算法比较、机构复现、三场景和实际 CLI 消费校验。每步的命令、退出码、stdout/stderr 保存在新目录；期望拒绝的步骤必须返回 2 才通过。目录已存在时拒绝重写。

## 单独运行与研究交接

```powershell
.venv\Scripts\python.exe -m etf_lab run --snapshot data/spy.json --config config/spy.json --run-id research-001 --output scratch/research
.venv\Scripts\python.exe -m etf_lab case-freeze --snapshot data/spy.json --config config/spy.json --case-id spy-case-001 --output scratch/research
.venv\Scripts\python.exe -m etf_lab research-submit --case-id spy-case-001 --run-id research-001 --submission-id submit-001 --output scratch/research
.venv\Scripts\python.exe -m etf_lab research-read --case-id spy-case-001 --submission-id submit-001 --output scratch/research
```

当前案例必须显式传给 research-read；它重新检查案例、代码版本、冻结输入及原产物。已经通过的交接记录不能绕过后来发生的内容篡改。变更输入需要新案例；主动重算使用新运行 ID；同一提交键带不同案例或运行会被拒绝。不可把缓存中的 downstream_eligible: true 当作永久许可。

```powershell
# 仅检查历史完整性；不代表当前适用
.venv\Scripts\python.exe -m etf_lab verify --run output/runs/spy-v1
# 同时检查当前代码、配置和快照；最新冻结运行应通过
.venv\Scripts\python.exe -m etf_lab verify --run output/runs/spy-v2 --snapshot data/spy.json --config config/spy.json
# 全部使用合成数据的独立三场景
.venv\Scripts\python.exe scripts/demo_reliability.py --output scratch/demo-001
```

正常成功退出 0，计算/模拟数据失败退出 1，冲突/失效/完整性拒绝退出 2。故障注入 --simulate-failure data 或 compute 明确标注 SIMULATED；不是实际供应商故障。旧失败 ID 不会被改写为成功。

## 结果与限制

主样板 2015-01-02—2025-12-31：累计收益 134.95%，年化 8.09%，最大回撤 -28.77%，夏普 0.7542，已平仓胜率 61.54%，53 笔成交 / 26 次完整交易。滑点 5 bp、手续费 $1/笔、流动性参与率超 1% 时附加冲击 10 bp；分红按除息日现金到账的明确简化处理。

机构模型与主样板独立：原文累计收益 294.1%，复现 295.3591%；原文回撤 18.9%，复现 18.8646%；原文与复现均 45 笔买卖。原文仅称“名义交易成本”，本地在重放前固定 $1/笔；该口径差异及同收盘成交假设在报告中完整说明。未用回测结果优化参数。Cambria 因缺原文精确交易次数被排除，失败候选记录仍保留。

ETF 样板不证明选股可信；不宣称消除历史成分股、退市、财报版本或 LLM 未来知识污染。哈希校验依赖可信原始清单/Git 提交，不是管理员绝对防篡改。实验分支 experiment/spy-backtest-reliability 未合并主分支；相邻项目没有改动。
