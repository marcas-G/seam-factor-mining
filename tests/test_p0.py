"""P0 验收测试:重构行为一致性(确定性回放)+ 资源权益不变量。

验收两级(用户定义):
  A. 确定性回放: 固定事件流 → 新旧决策轨迹一致
  B. 资源权益: 恶意分类不能骗免费重测(不变量)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seam.contracts.contracts import (BudgetExhausted, FactorCandidate,  # noqa: E402
                                  ResearchAPI)
from seam.agent_program.tree_v0 import TreeSearchProgram  # noqa: E402
from seam.execution.retry_policy import (RetryAuthorizer,  # noqa: E402
                                               spec_fingerprint)


# ---------------------------------------------------------------- 回放 API

class ReplayAPI(ResearchAPI):
    """固定事件流回放(模型输出/测试结果按序列喂)。"""

    def __init__(self, model_replies: list[str], eval_results: list[dict],
                 node_budget: int = 99):
        self.replies = list(model_replies)
        self.evals = list(eval_results)
        self.node_budget = node_budget
        self._nodes = 0
        self.calls = []

    async def call_model(self, prompt):
        self._nodes += 1
        if self._nodes > self.node_budget or not self.replies:
            raise BudgetExhausted()
        self.calls.append(("model", prompt[:60]))
        return self.replies.pop(0)

    async def evaluate_factor(self, cand, task):
        self._nodes += 1
        if self._nodes > self.node_budget or not self.evals:
            raise BudgetExhausted()
        self.calls.append(("eval", cand.name))
        return self.evals.pop(0)

    async def query_history(self, key):
        return None


def _spec_json(name, formula="f"):
    return json.dumps({"name": name, "formula": formula, "intuition": "i",
                       "diff_check": "d", "tags": ["t"], "source": "// s"})


# ---------------------------------------------------------------- 测试A: 回放一致

def test_replay_decision_trace():
    """固定事件流 → 程序决策轨迹确定(重构前后一致性的锚点)。

    事件流: draft成功 → draft失败(compile) → debug成功 → 预算尽
    期望轨迹: scored→failed(code)→debug scored→终止
    """
    api = ReplayAPI(
        model_replies=[_spec_json("F1"), _spec_json("F2"), _spec_json("F2fix")],
        eval_results=[
            {"score": 0.10, "error": None, "error_kind": "unknown",
             "execution_id": "h1"},
            {"score": None, "error": "local_compile: InitArgs error",
             "error_kind": "code", "execution_id": None},
            {"score": 0.12, "error": None, "error_kind": "unknown",
             "execution_id": "h2"},
        ],
        node_budget=6)
    prog = TreeSearchProgram()
    result = asyncio.run(prog.run({"desc": "t", "feeds": ["SHFE-AG"],
                                   "horizon": 5}, api))
    # 轨迹断言(重构不变量)
    outcomes = [t["outcome"] for t in result.trace]
    assert outcomes == ["scored", "failed", "scored"], outcomes
    assert result.trace[1]["layer"] == "code"
    assert [c.name for c in result.candidates] == ["F1", "F2fix"]


def test_replay_transient_retry_path():
    """平台瞬态 → 程序请求重测(网关error_kind=platform_transient)→恢复。"""
    api = ReplayAPI(
        model_replies=[_spec_json("F1")],
        eval_results=[
            {"score": None, "error": "no available provider",
             "error_kind": "platform_transient", "execution_id": None},
            {"score": 0.08, "error": None, "error_kind": "unknown",
             "execution_id": "h1"},
        ],
        node_budget=6)
    prog = TreeSearchProgram()
    result = asyncio.run(prog.run({"desc": "t"}, api))
    assert [t["outcome"] for t in result.trace] == ["scored"]


# ---------------------------------------------------------------- 测试B: 资源权益不变量

def test_malicious_classification_cannot_get_free_retry():
    """编译错误被程序标 transient → 网关 error_kind=code → 不给免费路径。

    程序侧 interpret_failure 可能被外层改写(把 compile 也归 transient),
    但网关的 error_kind 独立判定——程序的重试请求会走正常计费(扣节点)。
    """
    from seam.agent_program.tree_v0 import interpret_failure

    # 恶意改写后的分类器(假设外层把它改成全 transient)
    def evil_interpret(error):
        return "transient", "骗免费"

    err = "local_compile: error InitArgs"
    # 研究层分类: 恶意版说 transient
    assert evil_interpret(err)[0] == "transient"
    # 网关层判定: 独立判定为 code(信任的是网关 error_kind)
    from seam.execution.gateway import gateway_error_kind
    assert gateway_error_kind(err, 8) == "code"
    # RetryAuthorizer 只认网关 kind
    auth = RetryAuthorizer()
    cand = FactorCandidate(name="F", source="s")
    ok, why = auth.is_free_retry_eligible("code", cand, {}, {})
    assert not ok and "非平台瞬时" in why


def test_free_retry_counts_and_limits():
    auth = RetryAuthorizer(max_attempts=3)
    cand = FactorCandidate(name="F", source="same")
    ok1, _ = auth.is_free_retry_eligible("platform_transient", cand, {}, {})
    ok2, _ = auth.is_free_retry_eligible("platform_transient", cand, {}, {})
    ok3, _ = auth.is_free_retry_eligible("platform_transient", cand, {}, {})
    ok4, why4 = auth.is_free_retry_eligible("platform_transient", cand, {}, {})
    assert ok1 and ok2 and ok3
    assert not ok4 and "超上限" in why4


def test_modified_source_is_new_request_not_free():
    """改了 source = 新指纹 = 新的计费尝试(不能借重试改候选)。"""
    auth = RetryAuthorizer(max_attempts=3)
    c1 = FactorCandidate(name="F", source="original")
    c2 = FactorCandidate(name="F", source="modified!!")
    assert spec_fingerprint(c1) != spec_fingerprint(c2)
    auth.is_free_retry_eligible("platform_transient", c1, {}, {})  # c1 第1次
    ok, _ = auth.is_free_retry_eligible("platform_transient", c2, {}, {})
    # c2 是新请求,第1次——eligible=True 但它不是"重试"(计数独立)
    assert ok
    assert auth._attempts[spec_fingerprint(c2)] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
