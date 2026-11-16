# Generic Factor Mining

**通用因子挖掘框架**：将候选生成、因子计算、平台测试、指标筛选、实验记录拆成独立组件，供人工策略、LLM Agent、树搜索、遗传/贝叶斯搜索以及已有因子平台共同使用。

> 核心包：`factor_mining/`。原仓库的 `seam/` 保留为独立的**实验性程序级自改进实现**，不是通用框架的前置依赖或默认搜索方法。部分 SEAM 平台/运行时模块仍是未开源的占位接口，不能把它当作开箱即用的真实平台。

## 架构

```text
Strategy.propose(task, history) -> FactorCandidate
                              |
                       MiningEngine
                 去重 / 预算 / 逐次记录
                              |
             FactorEvaluator.evaluate(candidate, task)
                   |                      |
           现有因子平台适配器      Data -> Execute -> Metrics
                   \                      /
                     EvaluationResult
                              |
                Objective.judge(metrics)
                              |
                Trial / 最优候选 / JSONL
                              |
                  holdout 独立检查(可选)
```

核心层不要求任何特定市场、数据格式、时间尺度、因子表达式语法、评价指标或模型厂商。

## 安装与运行

Python 3.10+：

```bash
pip install -e .
python -m factor_mining demo
# 测试（包含原 SEAM 测试所需的统计依赖）：
pip install -e ".[dev]"
python -m pytest tests/ -q
```

`demo` 只使用合成分数，不连接真实市场数据。

## 最小使用示例

```python
from factor_mining import (
    FactorCandidate, MiningTask, EvaluationResult, CallableEvaluator,
    ListStrategy, MiningEngine, MetricObjective, MetricConstraint,
    JsonlRecorder,
)

candidates = ListStrategy([
    FactorCandidate("momentum", "rank(close / delay(close, 20) - 1)"),
    FactorCandidate("volume", "rank(volume / delay(volume, 5) - 1)"),
])

def platform_evaluate(factor, task):
    # 替换为你的平台 API: 编译/执行/对齐/防未来函数检查/计算真实指标
    metrics = {"ic": 0.03, "turnover": 0.25}  # 示例值，不是真实回测
    return EvaluationResult(metrics=metrics)

engine = MiningEngine(
    evaluator=CallableEvaluator(platform_evaluate),
    objective=MetricObjective(
        primary="ic",
        constraints=(MetricConstraint("turnover", maximum=0.5),),
    ),
    recorder=JsonlRecorder("runs/research.jsonl"),
)
result = engine.run(
    MiningTask("daily-stock", {"horizon": 5, "universe": "custom"}),
    candidates,
)
print(result.best.candidate.name if result.best else "no eligible factor")
```

- 换研究算法：实现 `propose(task, history)`（或使用 `CallbackStrategy`），无需修改引擎。
- 换执行平台：实现 `evaluate(candidate, task)`，或组合 `DataProvider`、`FactorExecutor`、`MetricCalculator`。
- 换评价口径：注入自己的 `FactorObjective.judge`；IC、收益、换手、冗余等不是框架的硬编码规则。
- 独立样本测试：运行结束后调用 `evaluate_holdout(result, holdout_task, evaluator)`，结果不会回流搜索。

**注意**：防未来函数、交易日对齐、停牌处理、成交成本、标签泄漏和封存评估权限必须在实际平台适配层执行。本核心库不宣称自动保证这些研究纪律。

进一步说明：[通用架构文档](docs/GENERIC_ARCHITECTURE.md)。原 SEAM 技术记录见 [SEAM 架构归档](docs/ARCHITECTURE.md)。

## License

MIT
