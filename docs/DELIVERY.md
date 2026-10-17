# 2026-10-17 Backtrader 修正版

下方 d489cf5 及之前的交付记录为历史。由于发现旧引擎在次日开盘反算股数，当前主运行改用 Backtrader。机构数值复现及旧账本保留，但不再把原验收数字解释为无前视偏差的证明。

新主报告 output/runs/spy-backtrader-v1/report.html；全套新复现输出 evidence/backtrader-reproduction-v1/；技术说明 docs/HARNESS_REVIEW.md。回退可在独立 checkout 查看 d489cf5，但不能把该版本恢复为“当前已验证无前视”的主方案。无真实系统迁移；不得覆盖已有运行或失败。

计算代码提交 `42a6175`；从 `851b465` 的干净副本完成新版完整 12 步复现和 61 项测试。实际命令、退出码、stdout/stderr、环境及文件哈希位于 `evidence/backtrader-clean-clone-v1/`。复用已安装锁定依赖的 Python 3.11 环境；克隆后的代码与冻结数据为全新副本。重算 input_id/result_id 与正式主运行完全一致。旧 spy-v2 历史完整性退出 0，当前适用性检查退出 2。

新 input_id `85b32457b145d4d8506fff23a7d89d0d21cf4ca4781382633966e583e19bae8f`；result_id `ffed5204f5f7251bef8430c8fa03e0fb157954c510400e29dffa76e4b54a67a3`。真实历史 SPY 结果已通过同名 `spy-backtrader-v1` 冻结案例、研究提交与读取接口，回执在 `output/submissions/spy-backtrader-v1/`，调用轨迹追加在 `output/calls.jsonl`；仅研究实验，无真实持仓写入。

源码变更另存 `evidence/backtrader-migration.patch`，对比基线 d489cf5。旧代码/旧结果仍保留，旧自写引擎仅作诊断反例。当前主适配器不支持拆股，明确拒绝；不宣称所有前视偏差已消除。

---
以下为原交付归档：

# 最终交付记录 · 2026-10-17

计算源码版本：`244fe1fa3f05b23c373230456510137f0dd40ffd`。实验分支 `experiment/spy-backtest-reliability`；GitHub 私有仓库 [ninglinLiu/etf-cio-reliability](https://github.com/ninglinLiu/etf-cio-reliability)。交付文件所属提交可由 `git rev-parse HEAD` 查看；未合并主分支。

任务二按用户明确确认改为独立研究结果可靠性验收。机构验收仍保留原文三项精确基准，未放宽。

| 交付 | 证据 |
|---|---|
| 42 项自动测试 | `evidence/tests-v2.txt` |
| 21 项相邻项目既有回归 | `evidence/adjacent-finresearch-tests-v2.txt`；原项目没有改动 |
| 主回测、逐笔账本和图表 | `output/runs/spy-v2/` |
| 独立审计最大差额 0 | `evidence/audit-v2.txt` |
| 当前输入/历史完整性校验 | `evidence/verify-v2.txt`、`history-verify-v1.txt` |
| 真实旧算法比较 | `evidence/existing-comparison-v2/`；原源码和提交在 `vendor/finresearch-lab/` |
| 机构三项完整对标 | `evidence/paired-replication-v1/`；精确原文值、45 笔买卖、2,517 日账本 |
| 独立可靠性三场景 | `evidence/reliability-v1/`；8 成功交接、6 拒绝、2 次模拟失败，17 条断言记录 |
| 实际演示截图 | `evidence/screenshots/` 中三个场景、主报告和机构报告 |
| 实施 diff | `evidence/implementation-v2.patch` 固定计算版本；`evidence/final-code.patch` 含最终代码、脚本、测试、配置和说明，不重复展开大型数据/报告 |
| 第三方复现入口 | `scripts/reproduce.py`；干净副本 11 / 11 步骤通过，实际输出位于 `evidence/clean-reproduction-v2/` |

最新主运行 input_id：`2ae4005170c9b048069902655f50f1cfeb9c48bcd0449040c42dc13fd024a703`；result_id：`bc29247fb6a85db7430dfea5e7645b96d1cdabe8a32844a550468c52375f23ba`。财务指标与首版相同；新输入身份反映新增接口/报告源码版本，旧运行完整保留。

机构报告以 Alpha Architect 的研究报道为入口，数字取自其讨论的 Glenn 原文在 IFTA 2021 第 95 页表 1 第一行。累计收益/年化/回撤/订单数相对偏差分别 0.428112%、0.201261%、0.187517%、0%。首次按原固定参数执行即通过，没有参数搜索。未采用 Cambria 候选作为通过证据；其缺交易次数的 NOT_ACCEPTED 文件保留。

## 复现与回退

按 README 安装锁定依赖，运行 `python scripts/reproduce.py --output scratch/reproduce-001`。该命令不联网获取数据，逐步保存实际命令、退出码和输出。成功读取必须退出 0；变更当前案例、消费失败回执和消费篡改副本必须退出 2。

2026-10-17 从提交 `ecebd2b1ebaec65466799248361265317520bd9f` 建立干净 Git 副本，复用已安装锁定依赖的 Python 3.11 环境执行完整命令。11 个步骤全部通过，包含再次运行的 42 项测试；副本执行前后 Git 工作区均干净。重算输入与结果标识和 `spy-v2` 完全一致。`evidence/clean-reproduction-v2/provenance.json` 保存版本、环境、文件哈希和标识，stdout/stderr 与实际退出码同时保留。后续交付提交仅添加这些证据、文档、截图与补丁文件的换行检查属性，未改变计算代码。

回退：停止使用独立研究交接命令即可，无真实系统迁移需要撤销。如需恢复代码，在新的实验提交中 `git revert 244fe1f`（可能需按后续依赖处理冲突），保留所有已存在运行/失败/案例/提交证据；或从历史提交建立另一个隔离 checkout 做历史复核。不要重写原分支历史或删除失败以伪造通过。代码变更后重新冻结案例并新建运行，不能继续使用旧的“当前有效”判断。

限制：不是原 CIO 集成；调用方绕过接口直读文件不受保护；未实现分布式事务或管理员绝对防篡改；SPY 分红按除息日到账为近似；ETF 样板不外推选股。论文模型的同收盘成交、当前复权数据及未量化原费用的 $1 近似单独披露。

---

## 首版归档（以下为 2026-10-16 当时状态，已被上面的新版交付取代）

# 本地首版交付记录

2026-10-16；计算源码提交 `11321ef`；实验分支 `experiment/spy-backtest-reliability`，没有主分支合并或远端推送。

## 实际运行

| 检查 | 实际结果 |
|---|---|
| 新项目自动测试 | 30 / 30 通过，含五类金融边界与本地产物前置条件 |
| 独立逐日重建 | 2,766 日、53 笔成交，最大现金/股数/净值差额 0 |
| 相邻 finresearch-lab 既有测试 | 21 / 21 通过；该仓库工作区保持干净；不是 CIO 回归 |
| 本地三场景演示 | 参数、费用、快照、来源更改拒用旧结果；两次模拟失败留痕并以新 ID 恢复；同键复用、异输入冲突、篡改拒绝 |
| 报告视觉检查 | 已在本地浏览器打开并检查标题、指标、图表、逐笔表和待验收说明 |
| 干净副本复现 | 从实验分支重新 clone，历史校验、离线重算与独立对账均退出 0；输入标识与结果标识和原运行完全一致 |
| CIO 接入 | 未执行，缺原项目与契约 |
| 指定旧算法与机构 ≤10% | 未完成，不得打勾 |

证据见 `evidence/tests.txt`、`evidence/adjacent-finresearch-tests.txt`、`evidence/audit.json`、`evidence/verify.json`、`evidence/clean-clone.txt`、`evidence/local-scenarios/summary.json` 和对应运行文件。

## SPY 结果（当前冻结假设下）

评估期 2015-01-02 至 2025-12-31，初始资金 $100,000，SMA 20/50，滑点 5 bp、手续费 $1/笔；完整数据包含 2014 年预热，共 3,018 根日线。

| 指标 | 数值 |
|---|---:|
| 累计收益 | 134.945889% |
| 年化收益 | 8.092984% |
| 最大回撤 | -28.774538% |
| 夏普（无风险年化 0） | 0.754216 |
| 已平仓胜率 | 61.538462% |
| 成交笔数 / 完整交易 | 53 / 26 |
| 期末净值 | $234,945.889194 |
| 期末未平仓 | 341 股 |
| 手续费 / 滑点 / 冲击 | $53 / $3,926.332564 / $0 |

分红除息日现金入账是明确简化；未模拟实际支付日。此表不是投资建议或策略有效性的外推证明。

输入标识 `9dccbd646aa8265ec7b1b23b66649e1ea5a20a84cfd2d67827836ad278e33e55`；结果内容标识 `88f4a2b03d7c32778096535c336358326cb5b48dcb8086333b2a86102fc8d85d`。结果内容相同不代表适用输入相同，必须同时检查 input_id / run_id。

## 可追溯的首次失败

首轮下载取得行情后，在交易日历日期范围检查触发 `DateOutOfBounds`。已在日期范围两侧扩展日历边界，并新增元旦非交易日起点回归测试。`data/download-failed-*.json` 保留原失败。该错误属于本地日历边界处理，**不是真实供应商故障**。

## 完整性、源码差异与回退

数据、产物和证据路径使用 Git `-text` 属性保留原始字节，防止平台换行转换破坏校验。源码单独使用 LF。`evidence/source.patch` 为计算源码提交的完整新增 diff；没有原 CIO baseline，所以不存在声称“已修改原 CIO”的 diff。

回退无需迁移真实系统。停止使用本地实验入口即可；如后续撤销实验代码，以新提交 revert 保留历史，原失败和产物不删除、不改成成功。
