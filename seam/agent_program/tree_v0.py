"""agent_program.tree_v0 —— 现有研究算法的忠实迁移(P0:行为一致)。

从 runtime.SearchLoop + strategy.* + retry_classifier 迁移,
逻辑不变,只改职责归属:
  - 主循环/解析/选择 = 研究算法(可被外层整体重写)
  - 模型调用/测试/计费 = 经 api 网关(程序不再持有 llm/evaluator)
  - 失败分类 = 研究启发式(可改);免费资格 = 网关判定(不可改)

回放验收: test_replay.py 用固定事件流断言重构前后决策轨迹一致。
"""
from __future__ import annotations

import json
import re

from seam.contracts.contracts import (BudgetExhausted, FactorCandidate,
                         ResearchAPI, ResearchProgram, ResearchResult)

# ---- 数据结构(从 strategy.py 迁移,语义不变) --------------------------

from seam.agent_program.strategy_v0 import Node, TreeState          # noqa: F401 (contracts 侧冻结)


# ---- 失败解释(研究层,可改;注意:只解释,不授权) ----------------------

def interpret_failure(error: str | None) -> tuple[str, str]:
    """研究层的失败解读——决定研究行动(修/弃/重试请求),不授予免费资格。"""
    if not error:
        return "unknown", "无失败签名"
    sig = error.lower()
    if "no available provider" in sig or "timeout" in sig or "429" in sig:
        return "transient", "平台瞬态——值得请求重测"
    if "compile" in sig or "execution_failed" in sig or "local_compile" in sig:
        return "code", "实现问题——debug 修代码,保假设"
    if "全零" in error or "无有效行" in error:
        return "quality", "输出退化——查 warmup/数据边界"
    return "unknown", "读原始签名自行判断"


# ---- v0 研究程序(与旧 SearchLoop.run() 行为一致) ----------------------

class TreeSearchProgram(ResearchProgram):
    """v0: AIDE 树搜索(现 DraftPolicy/FailureMemory/PromptBuilder 驱动)。"""

    async def run(self, task: dict, api: ResearchAPI) -> ResearchResult:
        from seam.agent_program.strategy_v0 import DraftPolicy, FailureMemory, PromptBuilder
        tree = TreeState(budget_limit=10**9)   # 预算由网关管,树只记结构
        policy = DraftPolicy()
        memory = FailureMemory()
        builder = PromptBuilder(policy, memory)
        result = ResearchResult()

        while True:
            try:
                operator, parent_id, arm = policy.next_action(tree)
            except Exception:
                break
            parent = tree.nodes.get(parent_id) if parent_id is not None else None
            prompt = builder.build(operator, parent, tree, task)
            policy.record(arm)

            try:
                raw = await api.call_model(prompt)
            except BudgetExhausted:
                break

            spec, err = self._parse(raw)
            if spec is not None and not spec.get("source"):
                # file_path 路线: 模型写文件后 JSON 只给路径(合法路线,
                # SKILL允许)——经网关读回,程序不碰文件系统
                _need_path = spec.get("_needs_artifact_read")
                if not _need_path:
                    for key in ("file_path", "h_path", "path", "output"):
                        pv = spec.get(key)
                        if pv and str(pv).endswith(".h"):
                            _need_path = str(pv)
                            break
                if _need_path:
                    content = await api.read_artifact(str(_need_path))
                    if content:
                        spec["source"] = content
                spec.pop("_needs_artifact_read", None)
            if spec is None or not spec.get("source"):
                tree.budget_spent += 1
                tree.add(Node(parent=parent_id, operator=operator,
                              spec={"name": "_parse_failed_", "formula": ""},
                              status="failed",
                              error_sig="parse: " + (err or "no source "
                                        "(read_artifact returned empty)")))
                result.trace.append({"op": operator, "outcome": "parse_failed"})
                continue

            node = Node(parent=parent_id, operator=operator, spec=spec,
                        strategy_arm=arm)
            tree.add(node)
            tree.budget_spent += 1

            cand = FactorCandidate(name=spec.get("name", ""),
                                   source=spec.get("source", ""),
                                   formula=spec.get("formula", ""),
                                   intuition=spec.get("intuition", ""),
                                   tags=spec.get("tags", []),
                                   spec=spec)
            try:
                ev = await api.evaluate_factor(cand, task)
            except BudgetExhausted:
                break

            score, error = ev["score"], ev["error"]
            if score is None:
                layer, _hint = interpret_failure(error)
                node.failure_layer = layer
                if layer == "transient":
                    # 程序请求重测;免费与否由网关的 error_kind 决定
                    # (ev["error_kind"] 由网关独立判定)
                    if ev.get("error_kind") == "platform_transient":
                        for _ in range(2):   # 研究决策:最多再试2次
                            try:
                                ev = await api.evaluate_factor(cand, task)
                            except BudgetExhausted:
                                break
                            if ev["score"] is not None:
                                break
                if ev["score"] is None:
                    node.status = "failed"
                    node.error_sig = f"[{layer}] {error}"
                    result.trace.append({"op": operator, "outcome": "failed",
                                         "layer": layer})
                else:
                    node.status, node.pub_score = "scored", ev["score"]
            else:
                node.status, node.pub_score = "scored", score

            if node.status == "scored":
                result.candidates.append(cand)
                result.trace.append({"op": operator, "outcome": "scored",
                                     "score": node.pub_score,
                                     "name": cand.name})

        return result

    def _parse(self, raw: str, api: ResearchAPI | None = None) -> tuple[dict | None, str | None]:
        """解析模型输出;文件读回经 api.read_artifact(不再直接 open)。

        旧版 open() 违反安全契约(P1 预检抓出)——agent 会话写的 .h
        由网关读回。async 读取在 run() 主循环内 await。
        """
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None, "no_json_found"
        try:
            spec = json.loads(m.group(0))
            # 文件读回由主循环处理(需要 async)——此处只标记
            if not spec.get("source"):
                for key in ("file_path", "h_path", "path", "output"):
                    p = spec.get(key)
                    if p and str(p).endswith(".h"):
                        spec["_needs_artifact_read"] = str(p)
                        break
            if not spec.get("name") or not spec.get("formula"):
                return None, "missing name/formula"
            return spec, None
        except json.JSONDecodeError as e:
            msg = str(e)
            if "Unterminated string" in msg or "Expecting ," in msg:
                return None, "truncated_output(increase max_tokens or shorten source)"
            return None, f"json_error: {e}"
