"""execution/gateway —— ResearchAPI 的可信实现(冻结)。

封装现有能力,不改行为,只改职责归属:
  call_model      ← 现 AgentHarness 的 subprocess 部分(会话/解析/计量)
  evaluate_factor ← 现 FeaturehubEval 的协议部分(编译门禁/CLI/ID/提取)
                    error_kind 由本层独立判定(平台信号,不信程序)
  日期窗从 task 绑定注入——程序没有指定窗口的通道(结构化废掉 sealed 可达)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..contracts.contracts import (BudgetExhausted, FactorCandidate, ResearchAPI)


class ResourceLedger:
    """真实资源计量(token/平台调用/墙钟)。免费重试也照计。"""

    def __init__(self, node_budget: int, token_budget: int = 10**9):
        self.node_budget = node_budget
        self.token_budget = token_budget
        self.nodes_spent = 0
        self.platform_calls = 0
        self.tokens = {"input": 0, "output": 0, "reasoning": 0}

    def charge_node(self) -> None:
        if self.nodes_spent >= self.node_budget:
            raise BudgetExhausted(self.usage())
        self.nodes_spent += 1

    def charge_platform(self) -> None:
        self.platform_calls += 1

    def charge_tokens(self, usage: dict) -> None:
        for k in self.tokens:
            self.tokens[k] += usage.get(k, 0)
        if sum(self.tokens.values()) > self.token_budget:
            raise BudgetExhausted(self.usage())

    def usage(self) -> dict:
        return {"nodes": f"{self.nodes_spent}/{self.node_budget}",
                "platform_calls": self.platform_calls, "tokens": dict(self.tokens)}


def gateway_error_kind(cli_output: str, exit_code: int) -> str:
    """从网关可见的原始信号独立判定失败类别(不信程序自报)。

    判定依据: CLI 的 exit code + 错误文本特征(平台侧协议事实)。
    """
    low = (cli_output or "").lower()
    if "no available provider" in low or "cooldown" in low \
            or "rate limit" in low or "429" in low:
        return "platform_transient"
    if ("execution_failed" in low or "compile" in low
            or "test failed" in low          # CLI exit 8 的文本特征
            or "local_compile" in low
            or exit_code == 8):
        return "code"
    if "all" in low and "zero" in low or "无有效行" in cli_output:
        return "quality"
    return "unknown"


class ExecutionGateway(ResearchAPI):
    """同步实现(当前管线无 async 基础,先以同步语义满足接口)。"""

    def __init__(self, ledger: ResourceLedger, model: str = "<INNER_MODEL_ID>",
                 eval_factory=None, task_window: dict | None = None):
        """eval_factory: () → FeaturehubEval 实例(测试注入 fake)。
        task_window: {feeds,start_date,end_date} 由 supervisor 绑定,
        程序不可传(窗口作为网关状态,evaluate_factor 只收 candidate+task)。"""
        self.ledger = ledger
        self.model = model
        self._eval_factory = eval_factory
        self.task_window = task_window or {}
        # agent 会话写文件的工作区(read_artifact 的信任根)
        self.artifact_root = Path("<ARTIFACT_ROOT>")

    async def read_artifact(self, path: str) -> str | None:
        """读取 agent 会话写出的工作区文件(如 .h)。

        程序不能自己 open()(安全边界);文件经网关读回。
        路径限制在工作区内,越界/不存在返回 None。
        (gen5 事故: 此方法曾缺失,基类直接 NotImplementedError,
        tree_v0 的 file_path 路线全炸——PROGRAM CRASH 留痕抓到)
        """
        p = Path(path)
        root = self.artifact_root.resolve()
        if not p.is_absolute():
            p = self.artifact_root / p
        rp = p.resolve()
        if root not in rp.parents and rp != root:
            # 越界路径: 模型常把文件写到 cwd(repo根)等其他位置。
            # 只取 basename 在信任根内查找——边界不放松(永远只读信任根),
            # 只是把"模型写错位置"从致命降为可恢复(gen8: 30次输出里
            # 大量写到 factors/ proposed/,全被拒→0候选)。
            cand = self.artifact_root / p.name
            rpc = cand.resolve()
            if root in rpc.parents and rpc.is_file():
                rp = rpc
            else:
                return None
        if not rp.is_file():
            return None
        return rp.read_text(encoding="utf-8", errors="replace")

    async def call_model(self, prompt: str) -> str:
        from .agent_runtime import AgentHarness, HarnessSpec
        self.ledger.charge_node()
        # 会话 cwd = artifact_root: agent 写的任何文件天然落在信任根内
        # (gen8事故: cwd曾是repo根,模型把.h写到factors//proposed/,
        #  read_artifact越界拒绝→parse_failed→0候选→平台零测试)
        harness = AgentHarness(harness=HarnessSpec(),
                               model=self.model,
                               directory=str(self.artifact_root))
        harness.frozen_model = True   # 网关锁模型(评价协议一部分)
        out = harness.complete(prompt)
        usage = getattr(harness, "token_usage", {})
        self.ledger.charge_tokens(usage)
        return out

    async def evaluate_factor(self, candidate: FactorCandidate,
                              task: dict) -> dict:
        self.ledger.charge_node()
        if self._eval_factory is None:
            from .platform_adapter import PlatformAdapter as FeaturehubEval
            win = {**self.task_window,
                   "feeds": task.get("feeds", self.task_window.get("feeds", ["SHFE-AG"]))}
            ev = FeaturehubEval(
                workdir=Path("<ARTIFACT_ROOT>"),
                feeds=win.get("feeds"), start_date=win.get("start_date"),
                end_date=win.get("end_date"))
        else:
            ev = self._eval_factory()
        spec = {"name": candidate.name, "formula": candidate.formula,
                "source": candidate.source, **candidate.spec}
        self.ledger.charge_platform()
        score, err = ev.evaluate(spec, task)
        kind = gateway_error_kind(err or "", 0) if err else "unknown"
        # hash 缓存: FeaturehubEval.evaluate 成功后写 spec["_fhash"];
        # execution_id 返回真实平台hash(sealed 评分直接复用,不反查)
        fh = spec.get("_fhash") or candidate.spec.get("_fhash")
        if fh:
            # 回写到 candidate(后续 sealed 评分从这取)
            candidate.spec["_fhash"] = fh
        return {"score": score, "error": err, "error_kind": kind,
                "execution_id": fh}

    async def query_history(self, key: str):
        return None  # P0: 单次运行无跨会话历史;P2 扩展
