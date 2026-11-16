# 通用因子挖掘架构（factor_mining）

## 定位

这个核心框架服务于任意因子挖掘流程：研究者/Agent/搜索算法提出候选，外部执行器验证，并按用户指定的评价口径筛选。无内置市场、标的、交易频率、因子语法、数据表、模型供应商、因子测试平台或自改进循环的假设。

**这是通用研究管线，不是 SEAM 自改进本身。** 旧 `seam/` 是独立的实验性元改进模块，可在后续通过适配器集成；主包 `factor_mining/` 不导入它。

## 依赖方向

```text
   研究策略层                         业务适配层
  List / Agent / Tree / BO    DataProvider -> FactorExecutor -> MetricCalculator
               \                /
                \              /   或 CallableEvaluator -> 已有平台
                 \            /
                  MiningEngine
             (去重/预算/结果/审计)
                       |
                   Objective
              (排序+可插拔约束)
                       |
                JSONL Recorder
                       |
                  Holdout Check
          (搜索结束后单向运行,不回流)
```

## 五组最小契约

| 组件 | 输入 | 输出 | 责任 |
|---|---|---|---|
| `ProposalStrategy.propose` | `MiningTask` + 历史 `Trial` | `FactorCandidate | None` | 任何候选生成算法 |
| `FactorEvaluator.evaluate` | 候选 + 任务 | `EvaluationResult` | 真平台执行、校验与多指标 |
| `FactorObjective.judge` | 测试结果 | `ScoreDecision` | 指标解释和准入标准 |
| `MiningEngine.run` | 任务 + 研究策略 | `MiningResult` | 顺序编排与预算 |
| `TrialRecorder.write_trial` | 运行 ID + 任务 + 每次尝试 | 无 | 可追溯研究轨迹 |

`MiningTask.context` 自由承载 `horizon`、`universe`、`target`、`frequency`、`research_window`、`cost_model` 等，无强制字段。真正的因子语义、对齐、滞后、数据频率、标签时间戳必须由平台适配层验证。

## 重要语义

1. **去重按内容**：指纹包含 `definition`、`language`、`parameters`，不包含改名或描述。缓存仅在单次运行内生效，不能跨数据窗/任务复用。
2. **预算不是只有成功次数**：`max_evaluations` 限制真实评估调用数，`max_proposals` 防止重复候选死循环。模型 token/墙钟/平台配额等必须由具体适配器的可信外部层进一步计量。
3. **指标不内置**：默认 `MetricObjective` 只是单指标排序+约束；自定义目标可以实现 `FactorObjective.judge`，包括多目标、风险惩罚、收益/成本或组合增量。
4. **错误不吞掉**：默认将执行器异常记为失败记录；`fail_fast=True` 可选择直接中断。
5. **独立验证**：`evaluate_holdout` 是 run 完成后的单向调用；结果不进入策略的 `history`。严格的时间窗口、隔离与权限由外部评估环境承担，不能把这个辅助函数当作安全沙箱。
6. **不是回测框架**：不内置市场数据、期货/股票交易规则或 IC 数学实现。已存在的因子平台可经 `CallableEvaluator` 直接复用。

## 可替换研究策略示例

- 人工构建的假设库：`ListStrategy`。
- Agent：在 `CallbackStrategy` 里调用模型、解析候选，并根据 history 选择下一步。
- 树搜索：在策略对象内维护节点、父子关系与扩展算子。
- 遗传/贝叶斯搜索：在策略对象内运行优化程序，向通用引擎逐一返回待测候选。
- 系统自改进：作为独立上层策略生成/选择的实验机制，不影响底层引擎。

## 当前非目标和后续扩展

本次提供可运行的最小核心与标准接口，不虚构真实行情数据或成功的生产回测。尚未实现：并发任务调度、进程级沙箱、分布式预算、实验数据库、多数据集交叉验证、模型会话管控、自动去未来函数检测、组合层风险归因。扩展这些能力时应分别接入执行器/评估器/运行服务，而非改变候选对象或把某种 Agent 算法写死在内核。

原项目 `docs/ARCHITECTURE.md` 记录了 SEAM 的原有实验设计，不作为当前通用包的使用说明。
