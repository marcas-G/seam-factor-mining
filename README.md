# seam-factor-mining

**SEAM** — Sealed-Evaluation Autonomous Meta-improvement
(封存评估的自主元改进): 一个程序级自改进的因子挖掘框架。

> 外层 agent 重写内层的**完整研究程序**(不是调参),改进真假由
> 封存数据上的真执行评分裁决,统计门守底线。

## 核心思想

```
外层(更强的模型) ──重写──> 内层(完整研究程序 = 优化对象)
                             │ 唯一通道
                       可信执行网关(预算/窗口/重试授权 = 冻结)
                             │
                       内层会话挖因子 → 平台真测
                             │
                       封存窗评分(|IC| × 新颖度δ)
                             │
                       配对审判(bootstrap CI + DSR) → 谱系
```

**一句话设计原则:研究策略可变,资源权益不可变。**

外层可以重写内层的任何研究算法(搜索结构/上下文组织/失败处理/
甚至单 agent→多 agent 协作),但——评分规则、统计门、封存窗、
预算计量、免费重试授权全部在冻结侧,程序结构上无法触达。

## 为什么"程序级"

调参是在固定算法里找好参数;SEAM 的优化对象是**算法本身**。
实测中,外层曾把内层从贪心树搜索改写为机制配额制(删光旧模块、
写全新程序)——这种表达力是策略参数化不可及的。

三层不可作弊的保证:
1. 内层**结构上**接触不到封存数据(没有传窗口的参数位)
2. 评分与晋升规则冻结(外层只能改"怎么研究",不能改"怎么打分")
3. 资源外部计量(免费重试资格由网关独立判定,不信程序自报)

## 仓库结构

```
seam/
  contracts/contracts.py     ★ ResearchProgram/ResearchAPI/BudgetExhausted
                             三方接口(两侧都依赖,最稳定的一层)
  agent_program/             内层研究程序(外层的优化对象)
    tree_v0.py               v0: AIDE式树搜索(draft/debug/improve三算子)
    strategy_v0.py           调度策略/上下文构造/失败记忆
    failure_interpret.py     失败三层分类(transient/code/quality)
  execution/                 冻结的可信执行环境
    gateway.py               网关: call_model/evaluate_factor/read_artifact
                             /ResourceLedger/窗口绑定/error_kind独立判定
    retry_policy.py          免费重测五条件(资源授权与程序解释分离)
    platform_adapter.py      ◇ 因子平台抽象(接你自己的平台)
    agent_runtime.py         ◇ LLM agent 会话抽象(接你自己的运行时)
  outer/
    program_precheck.py      候选程序预检四关卡(入口/安全AST/加载/冒烟)
    outer_program.py         外层 workspace 与改写流程
    paired_runner.py         配对评估(同任务×同预算×同窗,双侧对比)
  evaluation/
    dsr.py                   多重试验校正(启发式,降格声明在docstring)
    hce.py                   折指纹 + embargo(防评估历史漂移)
    meta.py                  配对 bootstrap 门
    scoring.py               rank IC/ICIR 本地口径
    service.py               composite_g(|IC|×新颖度δ) + novelty_verdict
    sealed_exec.py           ◇ 封存窗真执行评分抽象
tests/                       核心检验(P0回放一致性/P1三类改写)
docs/ARCHITECTURE.md         完整架构文档(自 SEAM.md 脱敏精简)
```

◇ 标记的文件是**抽象占位**——本框架的核心资产是架构与协议,
平台对接层(因子测试/agent会话)留在使用者侧实现,协议见各文件
docstring。

## 快速开始

```bash
pip install numpy scipy   # 唯一外部依赖(统计层)
python -m pytest tests/ -q   # 核心检验应全绿(占位层不参与)
```

实现两个占位(见 docstring 协议)后即可接入你自己的因子平台与
LLM 运行时,跑通第一个配对代际。

## 设计决策摘要(ADR)

1. **优化对象 = 完整研究程序**(目录级),非策略参数
2. **外层自身冻结**——先证明内层改进有效,再考虑递归开放
   (解锁条件:内层改进在独立任务可重复验证)
3. **资源权益不可变**:预算三路计量(节点/token/平台调用),
   失败重试授权由网关按平台原始信号独立判定
4. **封存窗结构性不可达**:evaluate_factor 没有日期参数位,
   窗口是网关构造时注入的状态
5. **统计门宁拒不收**:配对 CI 下界>0 且样本≥5;DSR 做启发式
   多重试验校正(不宣称定理应用)

## 实测中踩过的坑(摘录,完整版见 docs/ARCHITECTURE.md)

| 坑 | 教训 |
|---|---|
| 静默吞异常导致整代评估无痕失效 | 所有 except 必须留痕 |
| 并发下"list 首行=最新"假设破产 | 共享资源按内容 hash 串行化 |
| 评价期模型回退污染归因 | frozen_model,禁回退 |
| 默认日期窗静默触达封存数据 | 日期必须显式传,无默认值 |
| 高 IC 因子 100% 被库存量解释 | IC 与组合增量是两回事(δ过滤) |

## License

MIT
