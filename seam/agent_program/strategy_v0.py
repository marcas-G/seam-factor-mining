"""策略层 —— 外环的可重写面(AIDE² harness 层)。

v0 策略从老 brainstorm 规则编译(接续优先/dead_end 避让/五要素输出),
但一旦编译即独立:外环重写的是这些类,不再回头改老 skill。

三类可重写组件(对齐 AIDE₈₅ 被接受的三大改进):
  DraftPolicy    搜索策略(AIDE₀贪心 → v0 带策略臂的简化版)
  FailureMemory  上下文管理(AIDE₈₅ bug-rate-gated 失败记忆)
  EvalConsumer   评估消费(怎么读 proxy 信号决定下一节点)

设计规则(给外环改写者的约束,也是 v0 的自述):
  - build_* 方法返回 str(prompt 片段)或结构化决策,prompt 是投影
  - 状态全部显式(无闭包/全局),diff 后行为可静态审查
  - 新策略臂=加类;改门控=改阈值常量;重构=移动代码——
    三类改动的 diff 语义清晰,冒烟可单测
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------- 数据结构

@dataclass
class Node:
    """AIDE solution tree 的节点:一个候选因子。"""
    node_id: int = -1                 # 由 TreeState.add 分配
    parent: int | None = None
    operator: str = "draft"           # 'draft' | 'debug' | 'improve'
    spec: dict = field(default_factory=dict)
    status: str = "pending"          # pending/running/scored/failed
    pub_score: float | None = None   # r_pub 上的表现(树内爬山唯一信号)
    error_sig: str | None = None     # 失败签名(debug 算子输入)
    failure_layer: str | None = None  # 失败分层(transient/code/quality)
    strategy_arm: str = "default"    # 产出本节点的策略臂标签


@dataclass
class TreeState:
    """搜索树 + 预算账本。冻结面持有,策略层只读。"""
    nodes: dict[int, Node] = field(default_factory=dict)
    next_id: int = 0
    budget_spent: int = 0            # 评估次数计费
    budget_limit: int = 24           # AIDE² b_t 的因子版:节点评估次数

    def add(self, node: Node) -> int:
        nid = self.next_id
        node.node_id = nid
        self.nodes[nid] = node
        self.next_id += 1
        return nid

    def scored(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.pub_score is not None]

    def budget_left(self) -> int:
        return self.budget_limit - self.budget_spent


# ---------------------------------------------------------------- 搜索策略(v1)

class DraftPolicy:
    """多样性感知的树策略:周期分叉 + UCB 父节点选择。

    AG@300 的近零得分说明纯贪心反复精修同一结构,候选覆盖不足。v1 只改
    搜索策略:保留首次 draft 和失败叶 debug,每三个已评估节点开一个新根,
    其余预算用 UCB 在浅层高分节点间分配。深度上限避免窗口/包装算子无限
    堆叠,同时保留局部精修能力。
    """

    ARMS = ["default"]
    BRANCH_INTERVAL = 3
    MAX_IMPROVE_DEPTH = 2
    EXPLORATION_WEIGHT = 0.10

    def __init__(self, arms: list[str] | None = None):
        self.arms = arms or self.ARMS
        self._arm_calls = {a: 0 for a in self.arms}

    @staticmethod
    def _depth(node: Node, tree: TreeState) -> int:
        """节点距 draft 根的 improve/debug 边数;坏父链按当前深度截断。"""
        depth, current = 0, node
        seen = {node.node_id}
        while current.parent is not None and current.parent in tree.nodes:
            if current.parent in seen:
                break
            seen.add(current.parent)
            depth += 1
            current = tree.nodes[current.parent]
        return depth

    def next_action(self, tree: TreeState) -> tuple[str, int | None, str]:
        """返回 (operator, parent_node_id|None, strategy_arm)。"""
        arm = self.arms[0]
        if not tree.nodes:
            return "draft", None, arm

        # 只修失败叶。已产生修复子节点的旧失败不应永久劫持搜索。
        expanded = {n.parent for n in tree.nodes.values() if n.parent is not None}
        failed_leaves = [n for n in tree.nodes.values()
                         if n.status == "failed" and n.error_sig
                         and n.node_id not in expanded]
        if failed_leaves:
            return "debug", failed_leaves[-1].node_id, arm

        scored = tree.scored()
        if not scored:
            return "draft", None, arm

        # 固定低频分叉:先允许一次局部精修,再按节点评估数周期性探索新根。
        if (len(tree.nodes) >= self.BRANCH_INTERVAL
                and len(tree.nodes) % self.BRANCH_INTERVAL == 0):
            return "draft", None, arm

        # UCB 在可继续精修的浅层节点间分预算。子节点数充当访问次数。
        child_counts = {n.node_id: 0 for n in scored}
        for node in tree.nodes.values():
            if node.parent in child_counts:
                child_counts[node.parent] += 1
        candidates = [n for n in scored
                      if self._depth(n, tree) < self.MAX_IMPROVE_DEPTH]
        if not candidates:
            return "draft", None, arm
        total = max(1, sum(child_counts.values()))

        def ucb(node: Node) -> float:
            visits = child_counts[node.node_id]
            bonus = self.EXPLORATION_WEIGHT * math.sqrt(
                math.log(total + 1.0) / (visits + 1.0))
            return float(node.pub_score) + bonus

        # 保留原基线块形状,使既有外环 rewrite/smoke 样例仍可作用。
        scored = [max(candidates, key=ucb)]
        # improve 贪心(AIDE₀ 行为,外环改写点 #1)
        best = max(scored, key=lambda n: n.pub_score)
        return "improve", best.node_id, self.arms[0]

    def record(self, arm: str) -> None:
        self._arm_calls[arm] = self._arm_calls.get(arm, 0) + 1


# ---------------------------------------------------------------- 失败记忆(v0)

class FailureMemory:
    """AIDE₈₅ 的 bug-rate-gated failure memory 翻译。

    v0 门控:近期节点失败率 ≥ gate_rate(0.15,AIDE₈₅ 同值)时,
    注入最近 failure_k 条去重失败签名到 draft/improve 的 prompt。
    低于阈值休眠(省上下文,预算换更多步)。
    外环改写点 #2:阈值/条数/签名提取逻辑。
    """

    def __init__(self, gate_rate: float = 0.15, failure_k: int = 3,
                 window: int = 10):
        self.gate_rate = gate_rate
        self.failure_k = failure_k
        self.window = window

    def build_injection(self, tree: TreeState) -> str | None:
        recent = sorted(tree.nodes.values(),
                        key=lambda n: n.node_id)[-self.window:]
        if not recent:
            return None
        fail_rate = sum(1 for n in recent if n.status == "failed") / len(recent)
        if fail_rate < self.gate_rate:
            return None  # 门控未触发,休眠
        sigs, seen = [], set()
        for n in reversed(recent):
            if n.status == "failed" and n.error_sig:
                sig = n.error_sig.strip().splitlines()[-1][:120]
                if sig not in seen:
                    seen.add(sig)
                    sigs.append(f"- [{n.spec.get('name','?')}] {sig}")
            if len(sigs) >= self.failure_k:
                break
        if not sigs:
            return None
        return ("近期失败率 %.0f%%,避免重复以下失败模式:\n" % (fail_rate * 100)
                + "\n".join(sigs))


# ---------------------------------------------------------------- prompt 构造(v0)

# 五要素(老 brainstorm 硬要求编译为常量模板;外环改模板=改这里的代码)
SPEC_TEMPLATE = """你是量化因子研究员。基于以下上下文产出 1 个候选因子。

## 任务
{task_desc}

## 树上下文
{tree_ctx}

## 要求(输出 JSON)
{{
  "name": "类名(PascalCase)",
  "formula": "公式或伪代码",
  "intuition": "市场机制层面的假设(具体,不泛泛)",
  "diff_check": "去掉关键维度后退化为哪个标准因子?",
  "tags": ["3-5个英文标签"],
  "source": "完整 .h 文件内容(C++,含 xname/class_name 注释头)。或写文件后填 "file_path"
}}
输出前必须 Read <FACTOR_SPEC_DOC> (factor-writing spec the inner agent must read) 遵循规范。"""

OP_HINTS = {
    "draft": ("operator=draft:提出与树上已有候选差异化方向的新因子"
              "(算子/窗口/变换至少一维不同)。"),
    "debug": ("operator=debug:上一版本失败,诊断失败签名并修复——"
              "保持原假设,只修实现问题。同一失败节点修复次数受限。"),
    "improve": ("operator=improve:以父节点公式为基础做【恰好一个原子改动】"
                "(AIDE 原文纪律:如只调窗口、只换变换、只加归一化——"
                "让效果可直接归因;禁止同时改多处)。"),
}


class PromptBuilder:
    """prompt = 策略层的运行时投影。外环重写策略类,本类跟着变。"""

    def __init__(self, policy: DraftPolicy, memory: FailureMemory):
        self.policy = policy
        self.memory = memory

    def build(self, operator: str, parent: Node | None,
              tree: TreeState, task: dict) -> str:
        parts = []
        parts.append(OP_HINTS[operator])
        if parent is not None:
            parts.append("## 父节点\nname=%s formula=%s pub_score=%s" % (
                parent.spec.get("name"), parent.spec.get("formula"),
                parent.pub_score))
        inj = self.memory.build_injection(tree)
        if inj:
            parts.append("## 失败记忆\n" + inj)
        scored = tree.scored()[-5:]
        if scored:
            ctx = "\n".join("%s: %s (score=%.3f)" % (
                n.spec.get("name"), n.spec.get("formula", "")[:80],
                n.pub_score) for n in scored)
            parts.append("## 已评估节点(最近5个)\n" + ctx)
        return SPEC_TEMPLATE.format(
            task_desc=task.get("desc", ""),
            tree_ctx="\n".join(parts))
