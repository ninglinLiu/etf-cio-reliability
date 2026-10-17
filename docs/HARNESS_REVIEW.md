# Harness 技术复核与修正（2026-10-17）

上一版的“全部完成”把测试通过和回测可信说得太接近，应收回这一外推。
`artifacts.py` / `handoff.py` 是研究运行与结果交接的可靠性层，不是 Agent 运行时，也不是防未来数据访问的沙箱。
这些代码执行失败拒绝、输入版本绑定、幂等和完整性检查，不依靠提示词；但哈希只能证明复用了哪些字节，不能证明这些字节在历史时点已经可得，更不能证明计算方法正确。

## 本次检查发现

- 旧 `engine.run_backtest` 在收盘交叉后，用次日实际开盘价计算最大可买股数；对“前收盘提交固定股数订单”的策略，这是未来定价信息参与下单。既有测试只改变成交日之后的数据，没有验证成交日开盘变化不能改变此前订单股数。
- 旧信号函数虽然按前缀计算，整个策略执行函数仍拥有完整行情数组。不能声称任意策略代码都无法访问未来。
- 当前下载的 Yahoo 快照不是历史当时版本的数据。禁止 `shift(-1)` 无法解决供应商历史修订、财报发布时间、历史成分股等问题。
- 机构文章是独立的数值复现，使用文中同收盘成交近似。误差小不证明主策略没有前视偏差，也不证明论文的成交假设可执行。

## 修改位置与验收命令（实施前记录）

复用现有快照、CLI、报告、冻结案例、提交/消费校验；用固定版本 Backtrader 替换主运行的自写撮合和现金持仓更新。

| 文件 | 修改 |
|---|---|
| `etf_lab/backtrader_engine.py` | 流式 feed、框架 SMA、固定数量 Market 订单、BackBroker、手续费/冲击/分红适配、框架账本导出 |
| `etf_lab/artifacts.py` | 主运行调用 Backtrader，绑定框架版本与实现摘要 |
| `etf_lab/engine.py` | 保留旧算法作历史诊断；增加明确的前收盘仓位配置 |
| `tests/test_backtrader.py` | 次日开盘/高低收/成交量扰动、未来尾段扰动、截断重放、拒单、现金和成本等自动断言 |
| `etf_lab/report.py` / `scripts/compare_existing.py` | 展示框架、固定股数、拒单及适用边界；现有算法对照切换到新主运行 |
| `docs/*` / `scripts/reproduce.py` | 更正上一版结论并记录新证据、复现入口 |

验收：`python -m unittest discover -s tests -v`；`python scripts/reproduce.py --output scratch/reproduce-backtrader-001`；额外检查新冻结运行与旧运行仍分别可追溯，旧案例不能作为新代码当前结果。

## 采用的约束

收盘 t 时策略只使用已推进到 t 的数据；`preload=False`、`runonce=False`，关闭 cheat-on-open/close。订单股数在 t 收盘使用 t 收盘价、当时现金和预设成本固定，默认使用 95% 可用现金，留出的 5% 只是缓冲，不是成交保证。t+1 跳空后现金不足由框架拒单，绝不回头缩小股数伪造成交。更改成交日开盘必须允许成交价/是否成交改变，但不得改变前一日提交的数量。

框架负责 Market 订单状态机、实际成交、现金、持仓及费用扣除；本项目仅提供数据映射、策略、成本/分红适配和证据导出。拆股数据在这个适配器中明确拒绝，未声称 Backtrader 自动解决美股公司行动。SPY 样本无拆股；分红仍为已披露的除息日到账近似。策略没有 LLM，也没有面向不可信任意 Python 的进程/网络/文件系统沙箱。

Backtrader 官方规则：Market 订单在下一根 bar 开盘尝试成交；完整配置和测试仍由使用方负责。参考：https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/ 、https://www.backtrader.com/docu/broker/ 、https://github.com/mementum/backtrader 。
