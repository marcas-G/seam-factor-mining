# SEAM 架构(完整技术文档)

> 本文档由内部工作仓库脱敏导出;数值实证保留量级与结论,内部标识符已抽象化。

# SEAM v2: 程序级自改进的因子挖掘系统

## Sealed-Evaluation Autonomous Meta-improvement —— 架构与技术细节

> 版本: 2.0 (2026-11-12, P0+P1 架构改造完成)
> 实现: `factor-autoloop/aide_miner/`
> CLI: `seam status / run / propose / grade / lineage / report / watch / stop`
> 全部组件有检验锚定;实证数据来自真实平台执行。

---

## v2.0 核心变化: 从"改策略文件"到"改完整研究程序"

v1.x 的外环只能改写 `strategy.py`(单文件,树搜索框架内的策略参数)——
**搜索算法本身是人预先固定的**。v2 按照改造方案彻底重构:

```
v1: runtime.py(冻结的树搜索循环) + strategy.py(可改的策略类)
v2: agent_program/(可整体改写的完整研究程序)
    + execution/(冻结的可信执行环境)
    —— 搜索算法本身成为优化对象
```

一句话设计原则: **研究算法尽可能开放,研究环境和评价规则严格独立。**

---

## 目录

1. [设计原则](#1-设计原则)
2. [总体架构](#2-总体架构)
3. [contracts: 三方接口](#3-contracts-三方接口)
4. [agent_program: 可改写的研究程序](#4-agent_program-可改写的研究程序)
5. [execution: 可信执行环境](#5-execution-可信执行环境)
6. [外环: 程序级改写](#6-外环-程序级改写)
7. [评价与接受](#7-评价与接受)
8. [实证记录](#8-实证记录)
9. [失效模式与防御](#9-失效模式与防御)
10. [实施阶段(P0-P3)](#10-实施阶段)
11. [局限与开放问题](#11-局限与开放问题)

---

## 1. 设计原则

### 1.1 一句话原则

> **研究策略可变,资源权益不可变。**

一切设计由此推导: 外层可以重写内层的任何研究算法
(搜索结构/研究操作/上下文/记忆/候选选择),但——

- 不能改评分规则、统计门、晋升逻辑
- 不能改变哪些失败有资格免费重测(资源授权独立判定)
- 不能绕过工具网关直接触达数据/模型/平台
- 不能通过增加资源消耗取得优势(预算外部计量)

### 1.2 与 AIDE² 的关系

提取的是哲学而非实现: AIDE² 的关键不是采用了哪种搜索算法,
而是**让外层直接修改内层研究 agent 的完整程序,在固定预算下
评估修改后的整体能力**。我们在此基础上按自己的场景推导
(见 §7 的统计门偏离声明、§5 的资源授权),不逐条照搬。

### 1.3 五项冻结的架构决策

1. **优化对象**: 完整的内层研究程序(目录),不是少数策略参数
2. **外层职责**: 根据研究证据重写内层程序;外层自身当前冻结
   (先证明内层改进有效,再考虑递归开放外层——解锁条件与
   理由作为正式架构决定记录)
3. **执行方式**: 保留 agent CLI 会话(内层研究算法驱动它)
4. **可信边界**: 权限/预算/工具执行/知识可见性/评价均由
   固定环境控制
5. **实施方法**: 先逻辑分离与可回放验收(P0),再开放程序级
   改写(P1),后完善物理隔离(P2)与独立评价(P3)

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│ 外环 agent (外层模型, 冻结)                          │
│ 读证据 → 诊断 → 重写 agent_program/ 目录                 │
│ (可增删模块/换搜索结构/任何算法,自跑 smoke 到 passed)     │
└──────────────────┬──────────────────────────────────────┘
                   │ proposed/ 目录(完整程序)
┌──────────────────▼──────────────────────────────────────┐
│ 预检(冻结): program_precheck 四关卡                      │
│ ① 入口契约(ResearchProgram子类) ② 安全扫描(禁危险import  │
│ /open/eval) ③ 可加载(命名空间exec) ④ fake API 冒烟       │
└──────────────────┬──────────────────────────────────────┘
                   │ 通过
┌──────────────────▼──────────────────────────────────────┐
│ 配对评估(冻结): 同任务×同预算×同窗口,incumbent vs cand    │
│ → sealed 真执行评分(composite=|IC|×新颖度δ)              │
│ → paired CI + DSR(启发式) + 门                           │
│ → 谱系(argmax accepted 换血 incumbent)                   │
└──────────────────┬──────────────────────────────────────┘
                   │ 每代决定内层用哪版程序
┌──────────────────▼──────────────────────────────────────┐
│ agent_program/ (可整体改写的研究算法)                    │
│ TreeSearchProgram(v0) / 任何 ResearchProgram 实现        │
│ 通过 ResearchAPI 使用一切外部能力                        │
└──────────────────┬──────────────────────────────────────┘
                   │ 仅经网关
┌──────────────────▼──────────────────────────────────────┐
│ execution/ (冻结的可信网关)                              │
│ gateway: call_model(agent CLI会话)/evaluate_factor       │
│ (FactorPlatform真测,窗口由网关绑定)/read_artifact(工作区    │
│ 文件读回)/ResourceLedger(节点/token/平台调用计量)        │
│ retry_policy: 免费重测授权(五条件,独立判定)              │
└─────────────────────────────────────────────────────────┘
```

**数据流**: 程序调 api.call_model → 网关起 agent CLI 会话(内层模型,
write-factor skill 自动加载)→ 产因子 spec → 程序调
api.evaluate_factor → 网关经 FactorPlatform 真测(公开窗) → 分数回程序。
预算耗尽 = 网关抛 BudgetExhausted,程序捕获收尾。

---

## 3. contracts: 三方接口

`contracts.py`(冻结——两侧都依赖,改=破坏兼容):

```python
class ResearchProgram:
    async def run(self, task: dict, api: ResearchAPI) -> ResearchResult:
        """run() 内部没有任何固定算法要求。"""

class ResearchAPI:
    async def call_model(self, prompt: str) -> str
        # 固定配置的研究模型(网关锁版本,评价期禁回退)
    async def evaluate_factor(self, candidate, task) -> dict
        # {score, error, error_kind, execution_id}
        # error_kind 由网关独立判定(不信程序分类)
        # 日期窗由网关从 task 绑定——程序无指定窗口的通道
    async def read_artifact(self, path: str) -> str | None
        # 工作区文件读回(程序不能自己 open())
    async def query_history(self, key)

class BudgetExhausted(Exception):
    # 由网关计费层抛出;程序不得也不必自查预算
```

**反馈可见性矩阵**:

| 信息 | 内层程序 | 外层 | 独立评价 |
|---|---|---|---|
| 公开测试结果 | ✓ | 历史 | ✓ |
| 运行错误 | ✓ | ✓ | ✓ |
| 候选因子 | ✓ | ✓ | ✓ |
| 外层选择评分 | ✗ | 规定反馈 | ✓ |
| 最终独立测试 | ✗ | 搜索期不可见 | ✓ |
| 评分规则/权限 | 只读 | 只读 | 管理 |

---

## 4. agent_program: 可改写的研究程序

### 4.1 v0: TreeSearchProgram

从旧 runtime.SearchLoop 忠实迁移(行为一致验收):
AIDE 树搜索——draft/debug/improve 三算子、Σ(T) 摘要、
FailureMemory 门控注入、select_return 全返回。

### 4.2 外环可改什么(能力维度,非文件名)

| 能力 | 开放度 |
|---|---|
| 搜索算法(树/分支/重启/全新) | 完全开放 |
| 研究行动(生成/修复/改进/新操作) | 完全开放 |
| 候选选择(父节点/保留/放弃/最终提交) | 完全开放 |
| 上下文管理(压缩/证据/提示构造) | 完全开放 |
| 研究记忆 | 完全开放 |
| 工具调度(何时测什么) | 授权能力内开放 |
| 资源分配(内部预算调度) | 固定总预算内开放 |
| 评价规则/评分/统计检验 | **冻结** |
| 执行环境/权限/计费/模型版本 | **冻结** |

### 4.3 修改的硬约束(写进外环 TASK.md)

- 入口契约: ResearchProgram 子类,可无参实例化
- **禁相对导入**(加载方式为命名空间 exec,无包层级)
- 禁 import: os/sys/subprocess/socket/shutil/requests/urllib
- 禁调用: open()/eval()/exec()/__import__()
- 外部能力只经 api 参数
- 可增删文件、换任何搜索算法

### 4.4 P1 验收: 三类实质改写(行为差异可观测)

| 改写 | 行为差异证据 |
|---|---|
| A 换搜索结构(树→并行分支) | prompt 形态(operator vs branch)+ 轨迹结构(op 字段 vs branch 字段) |
| B 换上下文机制(自适应注入) | 失败后 prompt 头部出现警告(v0 没有) |
| C 换候选选择(全返回→top-1) | 候选数 2+ vs 恰好 1 |

目录形态全部通过预检(外环真实产出的样子)。

---

## 5. execution: 可信执行环境

### 5.1 gateway.py

- **call_model**: 封装 agent CLI 会话(subprocess/NDJSON 解析/
  token 计量);模型版本网关锁定(frozen,评价期禁回退——
  AIDE² 附录C: harness 增益与模型差异不可混淆)
- **evaluate_factor**: 封装 FactorPlatform 真测(编译门禁→create→
  dry-run→run→ID 协议→IC 提取);**error_kind 独立判定**
  (exit code+平台错误文本,不信程序自报)
- **read_artifact**: 工作区文件读回(路径限制在工作区)
- **ResourceLedger**: 节点/token/平台调用三路计量;
  BudgetExhausted 由这里抛

**窗口绑定**: 日期窗是网关状态(supervisor 从 task 注入),
evaluate_factor 只收 candidate+task——程序没有传窗口的参数位,
**sealed 窗可达性被结构性废除**。

### 5.2 retry_policy.py: 免费重测授权(冻结)

原则: 程序可以决定如何解释失败,**不能决定哪些失败免费**。
(否则: 外层改分类器把大量失败标 transient → 骗免费预算)

五条件全满足才免费:
1. 网关可信判定的 platform_transient(非程序标注)
2. 同一请求(spec 指纹 = 内容哈希;改 source = 新请求新计费)
3. 总尝试 ≤ 3
4. 真实资源照常记录(免费=不扣搜索节点;token/平台调用照计)
5. 结果不确定先查原执行状态(Execution-ID 协议,防重复提交)

恶意分类防不住免费(test_p0 锚定): 平台返回编译错误 →
程序标 transient → 网关 error_kind=code → 拒绝免费。

### 5.3 物理隔离(P2, 进行中)

当前为逻辑分离(P0/P1 完成面);P2 补:
- 候选程序+agent CLI+子进程在受限沙箱(bwrap 思路)
- 网络默认关,模型/平台经受控出口
- agent CLI 内 Bash/文件工具同样受权限契约约束
  (不能只限 Python 编排层——OpenCode 会话的完整工具面
   都是隔离对象)

---

## 6. 外环: 程序级改写

### 6.1 outer_program.py: 目录级 workspace

```
workspace/
  incumbent/          完整程序目录副本(多文件)
  contracts_reference.py  接口契约(外环知道边界)
  g_breakdown / data_spec / factor_summary  证据
  smoke.sh            预检自跑(agent 必须 bash 它到 passed)
  TASK.md             工作流程+修改硬约束
外环产物: proposed/ 目录(可增删模块) + verdict.json
```

### 6.2 预检四关卡(program_precheck.py)

① 入口契约 → ② 安全扫描(全目录 AST) → ③ 可加载
(命名空间 exec + module shell——dataclass 需要
sys.modules[module] 存在) → ④ fake API 冒烟

**实测战绩**: 第一个真实对象(agent_program 自身)就被抓出
tree_v0._parse 用 open() 读文件——违反"程序不碰文件系统"
契约,已改 api.read_artifact。

### 6.3 gen3 实录: 目录级改写的第一次真实使用

外环(外层模型)读 gen1+gen2 证据后:

```
诊断: gen2 的词元稀有度只奖励表面新颖,AG@5 丢了强机制
处方: 任务自适应机制组合——六类机制排固定根配额,
      先机制覆盖,再轮转精修(原子精修复保留)
操作: + program.py(全新入口) 
      - tree_v0.py - strategy_v0.py - failure_interpret.py(全删!)
自跑 smoke.sh → passed
```

**它删了三个旧模块、写了全新程序**——v1 单文件架构下不可能。

---

## 7. 评价与接受

### 7.1 两级评分

**因子级 h(x)**: |IC_seal| × δ(信息增量过滤)
- δ=0(R²>0.6 复刻) / 0.3(res_IC<0.01 薄) / 1(novel) / 0.5(未评)
- 方向可翻转(取绝对值——AdjacentSlot -0.24 事故修正)
- **定位声明**: δ 是冗余控制/信息增量过滤,**不是机制新颖性评价**
  (统计信息新 ≠ 经济机制新;两者都真但我们优化前者——
  因子库的价值是组合边际增量)
- 已知代理问题: RES 平台只产 h10/h60,h5 任务用 |Δh| 最近行
  (h10)代理——残差结构跨 horizon 平滑的假设未验证,已标记

**程序级 G(m)**: 当前实现 = 任务均值 × repeats 均值。
最终业务口径(产出率/组合增量)待用户拍板——这是独立的评价
研究问题,不与架构改造混(GradeProtocol 接口已留)。

### 7.2 接受门

- paired CI: 任务配对差 bootstrap,Δ_lo > 0,≥5 样本
- DSR: **启发式多重试验校正**(降格声明: IC差→t统计量不继承
  SR 的分布性质,Bailey-López de Prado 定理不背书;比裸 t 检验
  强——E[max] 罚项方向正确,但不宣称是 DSR 定理应用)
- PBO: **纯诊断**(降级——脆弱基线不自动放宽接受,
  现任不好应重新评它而非降候选及格线)
- 反馈泄漏: selection fold 分数逐代流出=外环对着它爬山
  (选择的本性,AIDE² 也承认);最终裁决需要 held-out 任务级
  (从未参与选择的任务)——已列入 P3

### 7.3 与 AIDE² 的明确偏离声明

| 偏离 | 理由(场景推导) |
|---|---|
| 统计门(论文是裸argmax) | IC 噪声 >> Kaggle 分数噪声,加门必然 |
| δ 信息增量过滤(论文无) | 我们有存量因子库,Kaggle 任务间无解库概念 |
| 任务内预算调度可改(论文无此维度) | 我们任务有内部调度的真实需求 |
| 程序目录改写 vs 论文的 agent 代码库 | 同构——都是"完整程序是优化对象" |

---

## 8. 实证记录

### 8.1 三代谱系

| 代 | 策略 | 结果 | 关键发现 |
|---|---|---|---|
| gen1 | v0 贪心树搜索 | observed g=0.0003 | 22因子/9高IC全复刻(R²>0.67)——内层模型 自然产出域=库覆盖域 |
| gen2 | 新颖性优先(60%开根+词元稀有度) | graded g=0.0289 | AU@1 爆发 +165×(0.0003→0.057);AG@5 小降;出现首个真 novel;门拒(2样本<5) |
| gen3 | 机制组合搜索(六机制根配额) | 评估中(P1架构首次) | 删三个旧模块写全新程序——目录级改写实证 |

### 8.2 回填体检(阶段0)

- 池 79 个有测试记录的因子;首批 5 个:Residual 系衰减 3-7%(真信号)
  但 R² 0.78-0.95(重度复刻)——"IC 真实但增量不足"的存量画像
- DiW3(管道验证样品): 30% 衰减;R²=0.77(复刻)

### 8.3 组件验证(摘要)

- 首个真实因子: AgPriceAlignedMlofi +0.1098(agent通道 vs 裸LLM
  三次全API幻觉——skill 自动加载的价值实证)
- 复现惩罚真实杀人: GapAdjusted 系封存IC 0.27-0.30 全被 R² 归零
- 数据规格抓前提错误: "3秒K线"描述 vs 实测500.37ms(系统自发现)
- 失败分层: CrossQueueLeadLag 4fold全死无人管 → 三类独立预算

---

## 9. 失效模式与防御

| 失效 | 防御 | 来源 |
|---|---|---|
| sealed=proxy同窗 | 翻转协议(H1↔H2) | 实测 |
| 本地scene评估与因子无关 | sealed真执行 | 实测 |
| Execution-ID混淆 | list --feature取记录ID | 实测 |
| 409 hash在stderr | 双格式解析 | 实测 |
| 裸LLM API幻觉 | agent通道(skill自动加载) | 实测 |
| max_tokens截断 | 24576+截断签名 | 实测 |
| 默认日期=封存窗 | 显式传参+网关窗口绑定(结构废除) | 实测 |
| SQLite跨线程 | check_same_thread+写锁 | 实测 |
| 谱系无限增长 | >10行压缩 | AIDE₀教训 |
| 前提错误(3秒) | data_spec测量+自动一致性检查 | 实测 |
| 复刻浪费预算 | RES并入pub_score+composite归零 | 实测 |
| 负IC强信号当毒药 | composite取绝对值 | 实测(AdjacentSlot) |
| res_ic全空 | RES horizon就近匹配(标记代理局限) | 实测 |
| 全零因子空值崩溃 | strip防护 | 实测 |
| sealed失败无处理 | 三类分层(transient重测/code修/quality查) | 用户指出 |
| 恶意分类骗免费 | 网关error_kind独立判定 | 用户指出 |
| 程序open()文件 | 预检安全扫描(实测抓到) | 预检 |
| 候选目录相对导入 | 禁相对导入规则(exec加载约束) | 加载器三踩坑 |
| PBO放宽接受 | 降级纯诊断 | 用户指出(DSR适用性) |

---

## 10. 实施阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P0** 逻辑分离 | 研究算法迁入agent_program;执行控制留execution;回放验收 | ✅ 完成(两级验收过) |
| **P1** 程序级改写 | 外层提交完整程序目录;预检管线;三类改写行为差异 | ✅ 完成(gen3使用中) |
| **P2** 物理隔离 | 沙箱进程/受控网络/工具面全覆盖/agent CLI子进程 | ⏳ 待做 |
| **P3** 有效性验证 | 冻结协议的正式对照实验+held-out任务级 | 待做 |

P1 内里程碑: 预检管线(抓到open违规)→目录workspace→
三类改写验证(行为差异可观测)→程序实例配对接口。

---

## 11. 局限与开放问题

1. **G 的业务口径未定**: 均值/产出率/组合增量——用户拍板后冻结
   (GradeProtocol 接口已留)
2. **点火未验证**: 改进的外层是否更会改进外层——AIDE² 自己也
   inconclusive;当前外层冻结是明确决策(解锁条件在案)
3. **任务异质性**: 4 任务(AG/AU/CU/RB)全部 500ms 盘口;
   数据形态异质性(日线等)未覆盖——任务族应按业务场景定
   (不按数据现成定——已纠正过的设计错误)
4. **held-out 任务级**: selection fold 泄漏的最终防线,
   P3 前必须建立
5. **P2 物理隔离未完成**: 当前逻辑分离+预检;
   正式评价不可信候选程序前 P2 必须就绪
6. **RES horizon 代理**: h5 用 h10 残差代理,假设未验证

---

## 附录A: 组件-文件映射

| 组件 | 文件 | 检验 |
|---|---|---|
| **contracts** | contracts.py | 接口契约(三方依赖) |
| **agent_program** | | |
| v0 树搜索 | agent_program/tree_v0.py | 回放一致(P0) |
| 策略/记忆/摘要 | agent_program/strategy_v0.py | 9项 |
| 失败解释 | agent_program/failure_interpret.py | 5项 |
| **execution** | | |
| 网关 | execution/gateway.py | 真链路验收 |
| 免费重测授权 | execution/retry_policy.py | 恶意分类不变量 |
| **outer** | | |
| 目录级预检 | outer/program_precheck.py | 6项(实测抓漏) |
| 目录级workspace | outer/outer_program.py | 4项 |
| 程序实例配对 | outer/paired_runner.py | 2项 |
| 单文件改写(旧) | outer/outer_agent.py | 保留(对照) |
| **评价** | | |
| sealed真执行 | sealed/sealed_exec.py | 平台真测 |
| composite+δ | sealed/service.py | DiW3锚定 |
| HCE+embargo | sealed/hce.py | 指纹漂移拒绝 |
| DSR(启发式) | sealed/dsr.py | 量纲+零方差 |
| PBO(诊断) | sealed/pbo.py | 过拟合判别 |
| 数据规格 | sealed/data_spec.py | 3秒失配实测 |
| **批量** | batch_harness/* | 17项 |
| **P0/P1验收** | test_p0/test_p1_rewrites | 8项 |
| CLI | bin/seam | 8子命令 |

累计检验 120+ 项。

## 附录B: P0 两级验收(用户定义)

**第一级(确定性回放)**: 固定事件流(模型输出/编译结果/测试
结果/错误类型)→ 归一化决策轨迹一致(排除session ID/时间戳等
非语义字段;保留工具调用序/参数/状态转移/重试/计费/终止原因)。
**不要求 LLM 确定性。**

**第二级(真实链路)**: 真 agent CLI 会话+真 FactorPlatform 测试,
验证契约完整(不要求两次真实会话产出相同因子)。

## 附录C: 模型配置

| 角色 | 模型 | 依据 |
|---|---|---|
| 内层(研究) | <内层模型> | 评估贵于提议 |
| 外层(改进) | openai/外层模型(high) | backbone bound |
| 评价期 | 网关锁定禁回退 | 增益归因干净性 |

## 附录D: 架构决策记录(ADR)

**ADR-001: 当前阶段仅优化内层研究程序**
- 决定: 外层可改写内层,不可改外层自身/执行环境/评价协议/晋升
- 原因: 先证明内层改进有效;同时改外层则收益来源不可分
- 解锁条件: 内层改进在独立任务上可重复验证后,
  单独设计外层改进器对照实验
- 工程约束: 候选只能提交内层程序版本;
  外层代码/Runtime/评分不属于提交范围

**ADR-002: 研究策略可变,资源权益不可变**
- 免费重测资格由网关独立判定,不信程序自报分类
- 预算三路计量(节点/token/平台调用),程序自报仅调试

**ADR-005(five frozen decisions)**: 见 §1.3
