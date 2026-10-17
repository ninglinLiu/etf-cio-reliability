# 接入前方案 · 2026-10-17 更新

## 已确认的范围变更（实施前记录）

用户确认没有任务书预设的原 CIO 项目，并选择“按独立可靠性验收完成”。任务二改为本仓库的研究结果可靠性验收；原 CIO 集成不再是本次依赖，也不宣称已完成。复用 `artifacts.py` 的不可覆盖运行、输入指纹、校验、失败与调用日志，不建立调度器、投资审批或交易系统。

新增 `etf_lab/handoff.py`：冻结案例、提交校验、消费时重新校验。一个案例绑定一个冻结输入；参数/数据/费用改变必须新建案例。同一提交键相同请求复用；不同请求冲突。失败提交保留拒绝回执，不产生成功交接记录。读取必须显式给出当前案例，拒绝历史案例的结果；每次重新检查原产物，不能复用缓存的通过状态。

拟修改 `etf_lab/__main__.py` 增加 `case-freeze`、`research-submit`、`research-read` 命令；新增 `tests/test_handoff.py` 和 `scripts/demo_reliability.py`，保存隔离成功/失败/恢复案例、实际输出与 HTML 演示。既有回测计算规则不变。另以固定版本的相邻 `finresearch-lab` 算法补充比较，不将其认作 CIO。

验收命令（输出目录必须新建）：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe scripts/demo_reliability.py --output scratch/reliability-new
.venv\Scripts\python.exe -m etf_lab research-read --output scratch/reliability-new --submission-id original --case-id baseline
```

以下保留首版检查历史，原“待原 CIO”状态由以上范围变更取代。

状态：本地新建实验项目。尚未取得原 CIO 仓库、`cio.py`、研究交接契约和指定简化算法；任务二尚未接入、未验收。

## 已检查的项目

相邻目录 `finresearch-lab` 位于 `main`，检查时提交为 `df92e8c`，工作区干净。
`finresearch/store.py` 已有 SHA-256、追加式 artifact、SQLite、来源修订和 lease；`benchmark.py` 已有次日开盘回放和成本计算。
该项目没有找到 `cio.py` 或指定研究交接契约，不能据此假定它是 CIO。未修改该项目。

## 本地范围与文件

- `etf_lab/data.py`：yfinance 日线快照、交易日历、来源与内容哈希。
- `etf_lab/engine.py`：规则、成交、现金、分红、逐笔及逐日账本、指标与本地简化参考。
- `etf_lab/artifacts.py`：单次研究运行的不可覆盖文件、输入指纹、失败结果与校验；无 DAG、Agent 调度、审批或交易功能。
- `etf_lab/report.py`：自包含 HTML 与嵌入 PNG。
- `etf_lab/__main__.py`：下载、单次回测、校验命令。
- `tests/`：五类金融边界案例与本地结果文件的适用性、失败恢复、重复请求、完整性测试。

本地文件校验只能作为 CIO 接入前测试，不能替代通过真实 `cio.py` 读取/提交的验收。

## 预定验收命令

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m etf_lab fetch --start 2014-01-01 --end 2026-01-01 --output data/spy.json
.venv\Scripts\python.exe -m etf_lab run --snapshot data/spy.json --config config/spy.json --run-id spy-v1 --output output
.venv\Scripts\python.exe -m etf_lab verify --run output/runs/spy-v1 --snapshot data/spy.json --config config/spy.json
```

## 取得原 CIO 项目后的接入位置

先检查 `cio.py` 命令分派 → 研究结果交接入口 → 当前案例/冻结上下文校验 → 下游读取/提交校验。
优先复用已有案例、冻结版本、结果存储、幂等、状态与拒绝机制。实际拟修改文件及真实验收命令必须在阅读原项目后再提交，不编造文件名或 CLI 参数。

结果绑定：规则与参数版本、数据来源、快照字节哈希、样本区间、下载/数据截止时点、费用/成交/分红/终值假设、代码文件哈希、Git 版本、依赖版本和其他配置。
旧结果保留其原始适用范围；新上下文校验拒绝旧结果。需要新冻结版本时新建原契约案例，不改旧案例。

## 仍需的验收输入

1. 原 CIO 仓库与契约、既有测试及基线版本。
2. 指定“现有简化算法”的源码与运行口径。本地新建参考算法不能冒充该交付。
3. 可复现的机构原文、明确的策略与样本、收益/回撤/交易次数、原始数据或许可内可得数据。
   相对偏差定义为 `abs(reproduced-reference)/abs(reference)`；参考为 0 或未披露时不得自动通过。
   SPY 20/50 日线规则不能与不同品种、不同期限或多资产期货策略强行比较。

所有改动留在 `experiment/spy-backtest-reliability`，不合并主分支。无真实持仓、成交、CRO、审批或交易执行访问。

## 独立研究交接契约 v1（实际实现）

存储位于调用者指定的隔离 output 目录，无数据库迁移。

| 路径 | 作用 | 不可覆盖约束 |
|---|---|---|
| cases/<case_id>/case.json + snapshot.json | 冻结 input_id 与完整快照；含 schema=research-case-v1 | 同案例不同输入拒绝，改输入必须新案例 |
| runs/<run_id>/ | 既有单次计算输入、结果、成功/失败回执、HTML、图表和校验清单 | 同键相同输入复用，不同输入拒绝；重算新键 |
| submissions/<submission_id>/ | 请求、成功或拒绝回执、校验清单 | 一次业务交接；重复验证后复用；冲突拒绝 |
| calls.jsonl | 调用及复用/冲突/读取拒绝日志 | 追加，不当作不可篡改审计链 |

提交请求为 research-submission-v1，包含 submission_id、case_id、run_id。回执成功才包含 input_id、result_id 和 downstream_eligible=true。拒绝回执无 result_id，保留 reason 和 downstream_eligible=false。

case-freeze 复用 artifacts 的规范 JSON/SHA-256 和源码身份；research-submit 调用原 verify，再检查当前案例。research-read 再次检查交接回执、当前 case_id、案例清单、当前代码/依赖和结果文件，拒绝将历史验收当成永久有效状态。调用方应只在命令退出 0 后消费结果，不应直接读缓存回执推进步骤。

主要拒绝码：INPUT_MISMATCH、CURRENT_CASE_MISMATCH、CASE_CODE_STALE、RUN_NOT_SUCCEEDED、SUBMISSION_NOT_SUCCEEDED、SUBMISSION_IDEMPOTENCY_CONFLICT、INTEGRITY_MISMATCH。代码/配置变化不修改旧回执；旧内容仍能单独做历史完整性检查。

故障注入 data 在运行数据取得边界模拟不可用，failed_stage=data_acquisition，明确写 SIMULATED；compute 在计算边界模拟异常。两者都不是真实供应商故障。真实供应商异常另由 test_data 的 mock 验证不泄露错误内容；敏感异常文字不进入回执。失败运行不借用旧 result_id，恢复使用新的 run_id 和 submission_id。

不提供审批、投资判断、工作流调度或交易执行。新 CIO 将来接入时，需把真实当前研究上下文映射到 case_id，并在研究提交和每次后续读取处调用该接口；其自身状态机、冻结上下文和业务幂等必须另行适配并验收。本次没有宣称覆盖不存在的外部系统。

一致性范围：目录独占创建拒绝同时抢占，崩溃或部分写入导致 fail-closed，可用新 ID 重试；不是分布式事务系统。管理员能同时改清单和文件，仍可伪造哈希；需以可信 Git 提交中的原清单作外部锚。原始示例结果均保留。
