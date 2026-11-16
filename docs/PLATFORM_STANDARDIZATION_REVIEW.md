# 因子挖掘标准化平台：架构审阅、RSI 文献与研究规范

> 版本：2026-11-16。本文将 RSI 解释为 Relative Strength Index（相对强弱指数）。该指标是**验证样例**，不是平台默认目标或搜索算法。平台与 Factor Autoloop 无关。

## 1. 当前实现审阅（分支 refactor/generic-factor-mining-core）

当前 `factor_mining/` 实现了一个轻量顺序编排内核，**尚非生产级因子挖掘平台**。

| 严重度 | 发现 | 建议 |
|---|---|---|
| P0 | `MiningTask.context` 为任意 dict；缺少数据集版本、标的池快照、目标构造、信息可得时间、费用模型、研究/验证窗口的强制契约 | 引入可验证 `TaskSpec` 和 `DatasetManifest`，使用确定性配置哈希 |
| P0 | `ExpressionPolicy` 只是字符串黑名单，不能防止 `shift(-1)`、索引穿越、动态调用或任意 Python 执行 | 使用 AST、类型系统、算子注册、时序信息流检查和受限执行器；隔离不能只靠 AST |
| P0 | `evaluate_holdout` / `ExperimentRunner` 在同一进程调用同一个 evaluator；“不回传历史”不等于数据封存 | 分离研究执行服务与 holdout 服务，权限控制、单向提交和访问审计 |
| P0 | 指标结果只有 `Mapping[str,float]`；无样本量、置信区间、收益曲线、窗口/市场维度、显著性或成本假设 | 标准 `MetricBundle`、`EvidenceArtifact`、`SplitResult`，保留原始评估序列 |
| P1 | 候选指纹仅使用原始表达式和参数，无法识别 `a+b` 与 `b+a`、别名、默认参数等语义等价式 | 解析 AST、归一化交换律与参数；区分 exact、semantic、behavioral 去重 |
| P1 | 仅有单因子最高分选择，无因子池、组合增量、相关性与残差化增益 | 引入 `FactorRegistry`、`PoolEvaluator`、`MarginalUtility` |
| P1 | `ProposalStrategy.propose` 是同步单步接口，无法自然表达异步 Agent、批次搜索、预算与中间工件 | 添加 `ResearchSession` 和可选异步/批次适配层，保留最小同步协议 |
| P1 | JSONL 每次 append 无锁/原子事务；没有运行恢复、幂等、并发和不可变工件索引 | SQLite 起步，之后 PostgreSQL + 对象存储；以 run/trial ID 幂等写入 |
| P1 | `MiningEngine` 对 evaluator 异常默认继续，但未定义可重试/不可重试/数据异常等错误分类 | 标准化 error_kind、retry policy、external metering |
| P1 | 只有固定单指标 `MetricObjective`；缺少目标方向、基准、经济显著性和多重检验 | 区分 hard gate / ranking / statistical validation / portfolio impact |
| P2 | 当前测试以合成分数为主；CI 文件存在不代表已经通过 | 增加固定数据回归、RSI 计算 golden tests、时序泄漏负例和真实平台适配集成测试 |

特别说明：原有 `seam/` 具备程序级自改进研究原型，但不应成为通用平台内核依赖。旧仓库部分运行时/平台适配模块是占位实现。

## 2. RSI 研究：需要区分指标、信号与交易规则

RSI 常用定义：

```text
delta[t] = close[t] - close[t-1]
gain[t] = max(delta[t], 0)
loss[t] = max(-delta[t], 0)
RSI[t] = 100 * avg_gain[t] / (avg_gain[t] + avg_loss[t])
```

`avg_gain/avg_loss` 的定义并不唯一。Wilder 常用递推平滑，其他论文可能使用滚动和或简单均值。必须记录：
- 平滑方法、窗口长度、初始化/warmup、零损失边界；
- 收盘信号何时可知、最早可交易时刻、成交价格；
- 是否采用交叉事件还是持续处于超买/超卖区间；
- 横截面排序信号 vs 单资产交易规则；
- 多空方向、持有期、费用与基准。

RSI 不等于“超卖买入”这个策略。RSI 本身可以成为连续特征；30/70 阈值策略只是其中一个可检验假设。

## 3. 文献清单（已核对标题与可访问来源）

| 研究 | 可迁移到平台的设计要求 | 来源 |
|---|---|---|
| Chong & Ng (2008), *Technical analysis and the London stock exchange: Testing the MACD and RSI rules using the FT30* | RSI 的规则与基准比较，跨长历史窗口 | https://doi.org/10.1080/13504850600993598 |
| Chong, Ng & Liew (2014), *Revisiting the Performance of MACD and RSI Oscillators* | 参数与市场敏感性；不得把 14/30/70 当作唯一配置 | https://doi.org/10.3390/jrfm7010001 |
| Anderson & Li (2015), *An investigation of the relative strength index* | 标准阈值可能无效，参数搜索必须纳入多重检验 | https://www.businessperspectives.org/images/pdf/applications/publishing/templates/article/assets/6381/BBS_en_2015_01_Anderson.pdf |
| Yamani (2021), *Can technical trading beat the foreign exchange market in times of crisis?* | 市场状态/危机前后分层检验 | https://doi.org/10.1016/j.gfj.2020.100550 |
| Kitkanasiri & Palahan (2025), *Market Efficiency and Technical Trading: An Empirical Study of MACD and RSI Indicators in Major Asian Stock Indices* | 样本外跨市场比较，负面结果也要记录 | https://doi.org/10.59865/abacj.2025.30 |
| Marshall, Cahan & Cahan (2008), *Does intraday technical analysis in the U.S. equity market have value?* | 大规模规则搜索后的数据窥探偏差（7846 规则） | https://doi.org/10.1016/j.jempfin.2006.05.003 |
| Kakushadze (2016), *101 Formulaic Alphas* | 表达式库、算子 DSL、因子相关性/冗余评估 | https://arxiv.org/abs/1601.00991 |
| Zhang et al. (2020), *AutoAlpha: an Efficient Hierarchical Evolutionary Algorithm for Mining Alpha Factors in Quantitative Investment* | 搜索算法插件化、quality-diversity、候选复用 | https://arxiv.org/abs/2002.08245 |
| Shi et al. (2024), *AlphaForge: A Framework to Mine and Dynamically Combine Formulaic Alpha Factors* | 因子发现与因子组合分层，动态组合评估 | https://arxiv.org/abs/2406.18394 |
| Yang et al. (2020), *Qlib: An AI-oriented Quantitative Investment Platform* | 数据/模型/工作流/回测解耦和标准化实验记录 | https://arxiv.org/abs/2009.11189 |
| Bailey & López de Prado (2014), *The Deflated Sharpe Ratio* | 多次尝试导致的选择偏差；不可把当前仓库的启发式 IC 校正冒充标准 DSR | https://doi.org/10.3905/jpm.2014.40.5.094 |

重要边界：Brock, Lakonishok & LeBaron (1992) 研究的是均线与交易区间突破，不是 RSI 的直接有效性证据，适合引用其 bootstrap 研究方法，而非 RSI 结论。https://doi.org/10.1111/j.1540-6261.1992.tb04681.x

## 4. 建议的标准平台分层

```text
research-api / cli / web-ui
        |
TaskSpec + DatasetManifest + RunSpec (不可变版本)
        |
Research Orchestrator (预算/状态/调度/审计)
        |------------------|
Strategy Plugins        Factor Registry / Hypothesis Registry
        |------------------|
Factor DSL / AST / Operator Registry / Type & Time Checker
        |
Data Adapters -> Execution Service -> Metric Engine
        |                              |
        |                          Factor Pool
        |                              |
        +--> Research Validation ------+
                     |
           Sealed Holdout Service
                     |
          Evidence & Artifact Store
```

**插件协议**：`DataProvider`、`FactorCompiler`、`FactorExecutor`、`ProposalStrategy`、`MetricCalculator`、`SelectionPolicy`、`ExperimentStore`、`HoldoutEvaluator`。所有插件有版本、配置哈希和能力声明。

**基础数据实体**：
- `DatasetManifest`: dataset_id, version, universe_snapshot, timezone, calendar, fields, availability_lag, corporate_action_policy。
- `TaskSpec`: market, frequency, target, horizon, split_plan, transaction_cost, benchmark, objective。
- `FactorSpec`: factor_id, canonical_ast, parameters, operator_versions, data_dependencies, availability_time, provenance。
- `EvaluationEvidence`: factor_id, task_hash, split_id, metric_bundle, signal_artifact, label_artifact, cost_assumptions, error_kind。
- `RunRecord`: run_id, seed, strategy_version, code_commit, dataset_hash, environment, attempted_trials, consumed_budget, artifacts。

## 5. RSI 作为首个端到端标准案例

**研究问题**：RSI 是否在某市场/频率/目标上具有独立、可迁移的预测信息？不预设方向与收益。

最小实验矩阵：
1. `RSI(n)` 连续值，n ∈ {7,14,21}；Wilder 与 simple-mean 两种实现分别记录。
2. 规则族：低阈值反转、高阈值反转、50 中线、RSI 动量/斜率、RSI 与价格背离（需事先严格定义）。
3. 目标：未来 h 期收益（h ∈ {1,5,10}），并区分单资产时间序列与横截面 IC。
4. 基线：价格动量、简单收益、波动率、随机/置换信号、买入持有（只适用于可交易策略比较）。
5. 验证：滚动时间分割、跨市场/跨阶段稳定性、成本后收益、信号覆盖率、IC/RankIC、换手、与已有因子相关性和增量贡献。
6. 统计：记录全部尝试数；对同一族规则做多重比较控制；按时间相关性选用 block bootstrap 等适当方法。

**禁止偷换结论**：RSI 的预测能力、交易收益、组合增量是三个不同问题；一个有效不代表另外两个有效。

## 6. 实施里程碑与验收门槛

### M0：可信核心
- 修复审阅 P0；TaskSpec 和 DatasetManifest 必填版本字段；研究窗/验证窗权限分离。
- Golden test：RSI Wilder 14 的初始化、零涨跌、常量价格、缺失值、跨资产边界。
- 验收：时间穿越测试必失败；相同配置+相同数据快照复现相同信号与指标。

### M1：可用因子执行平台
- AST/算子注册/类型检查；至少接一个真实数据适配器与评价器。
- 验收：RSI 与 momentum 两类因子端到端运行，指标附原始证据与配置哈希。

### M2：通用挖掘
- 人工、随机、树搜索、Agent 至少两类独立策略使用同一引擎/预算/评价接口。
- 验收：无需改引擎源码即可更换策略；重复候选和失败尝试可追踪。

### M3：可信因子池
- 相关性、残差增量、样本外组合、成本与多重检验。
- 验收：可以区分“单因子高 IC”和“对现有因子池有增量价值”。

当前状态：**架构方案与研究规范，不代表这些模块已实现或完成真实回测**。
