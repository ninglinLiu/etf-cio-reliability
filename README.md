# ETF 回测与 CIO 可靠性接入准备

本地独立实验仓库，分支 `experiment/spy-backtest-reliability`。已实现可逐笔复核的 SPY 样板、HTML 报告和本地证据检查；**未完成原 CIO 接入、指定旧算法对比或机构文章数字复现**。不应将本地测试通过写成整个任务验收通过。

## 先看结果

- [真实 SPY 回测报告](output/runs/spy-v1/report.html)：PNG 净值图、关键指标、53 笔成交、2,766 日账本、成本与数据声明。
- [本地三场景证据](evidence/local-scenarios/summary.json)：只验证研究产物前置条件，全部使用合成测试数据。
- [接入前方案](docs/INTEGRATION_PLAN.md)：已检查机制、拟接入位置、文件与验收命令。
- [验收清单与排期](docs/ACCEPTANCE.md)：哪些已完成、哪些仍缺依赖。
- [方法与限制](docs/METHODOLOGY.md)：成交、分红、股数、市场冲击、指标与复现定义。

## 安装与离线复现（PowerShell，Python 3.11）

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.venv\Scripts\python.exe -m unittest discover -s tests -v

# 查看交付结果的完整性与当前输入适用性
.venv\Scripts\python.exe -m etf_lab verify --run output/runs/spy-v1 --snapshot data/spy.json --config config/spy.json

# 新运行 ID，使用冻结数据离线重算；同一 ID 同一输入再次执行会复用。
.venv\Scripts\python.exe -m etf_lab run --snapshot data/spy.json --config config/spy.json --run-id my-reproduction-001 --output scratch/reproduction
.venv\Scripts\python.exe scripts/audit_result.py scratch/reproduction/runs/my-reproduction-001

# 三场景前置条件演示：使用新的隔离目录，保留原有证据。
.venv\Scripts\python.exe scripts/demo_local_evidence.py --output scratch/demo-001
```

`verify --run ...` 不带新输入只检查历史完整性；必须同时指定 `--snapshot` 和 `--config` 才检查当前输入适用性。当前代码文件、计算提交或依赖版本变化会导致旧结果不匹配，这是预期行为；可以保留旧结果作为历史证据并使用新运行 ID 重算。没有原 CIO 的 downstream gate，外部消费者仍必须调用并正确处理校验失败。

正常执行退出码 0；运行失败 1；冲突、完整性错误或输入拒绝 2。失败记录不包含成功 `result_id`，不能通过默认 `verify`。

## 重新获取行情

```powershell
.venv\Scripts\python.exe -m etf_lab fetch --start 2014-01-01 --end 2026-01-01 --output scratch/spy-new.json
```

`end` 不包含在下载区间中。2014 年用于预热，业绩区间固定为 2015-01-02 至 2025-12-31。新快照必须使用新路径；同一 URL 的数据变化会改变字节哈希。网络下载失败时单独保存失败记录，不伪造数据。

## 故障注入

```powershell
.venv\Scripts\python.exe -m etf_lab run --snapshot evidence/local-scenarios/fixtures/spy.json --config evidence/local-scenarios/fixtures/config.json --run-id simulated-data-001 --output scratch/failures --simulate-failure data
.venv\Scripts\python.exe -m etf_lab run --snapshot evidence/local-scenarios/fixtures/spy.json --config evidence/local-scenarios/fixtures/config.json --run-id simulated-compute-001 --output scratch/failures --simulate-failure compute
```

这两条应退出 1，报告 `simulated: true`。`data` 注入发生在单次运行的数据校验入口，不是真实 Yahoo 故障；供应商异常另由 `tests/test_data.py` mock 验证。去掉注入并使用新运行 ID 恢复。失败 ID 不会被覆盖为成功。

## 文件、版本与回退

`etf_lab/` 为单 ETF 计算、产物输出与校验，不是 Agent 框架或总控。`data/spy.json` 保存 yfinance 返回字段和规范化日线；每次运行拷贝完整输入并记录源码/配置/快照哈希。`integrity.json` 校验清单不包含自身，可信锚是保留在实验提交中的原清单；能同时改清单的人仍可伪造记录，未宣称管理员防篡改。

本项目不改相邻 `finresearch-lab`，也不访问真实投资账户。回退时停止使用本项目命令即可；若在后续实验提交撤销代码，使用 `git revert <commit>` 并保留原有结果目录。不要用 reset/覆盖输入方式消除失败证据。没有合并、推送、部署或数据库迁移步骤。
