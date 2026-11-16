"""contracts —— 跨层接口协议(冻结:两侧都依赖它,改=破坏兼容)。

三层职责(2026-11-12 架构决策,五项已冻结):
  agent_program/  研究算法(外层可整体改写)
  execution/      可信执行环境(网关/计费/重试授权——冻结)
  sealed/         独立评价(不向候选程序开放)

关键原则(用户裁决): 研究策略可变,资源权益不可变。
  agent_program 可决定如何解释失败、采取什么研究行动;
  execution 独立决定哪些失败有资格免费重测(不信任程序自报标签)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------- 研究产物

@dataclass
class FactorCandidate:
    """研究程序产出的候选因子。"""
    name: str
    source: str                    # 完整 .h 内容
    formula: str = ""
    intuition: str = ""
    tags: list[str] = field(default_factory=list)
    spec: dict = field(default_factory=dict)  # 原始 LLM 输出全貌


@dataclass
class ResearchResult:
    """研究程序的一次运行产出。"""
    candidates: list[FactorCandidate] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)   # 决策轨迹(回放/审计)
    meta: dict = field(default_factory=dict)          # 程序自报信息(仅调试,
                                                      # 不作为计费依据)


# ---------------------------------------------------------------- 网关异常

class BudgetExhausted(Exception):
    """研究预算耗尽(由网关计费层抛出;程序不得自查预算)。"""
    def __init__(self, usage: dict | None = None):
        self.usage = usage or {}
        super().__init__("research budget exhausted")


# ---------------------------------------------------------------- API 接口

class ResearchAPI:
    """受控研究工具网关(冻结实现,agent_program 通过它使用一切外部能力)。

    设计原则:
    - 程序决定怎么用工具(何时测/测什么/先做什么)
    - 网关决定工具实际能做什么(权限/日期窗/计费/模型版本)
    - 一切调用计量,程序自报只作调试信息
    """

    async def call_model(self, prompt: str) -> str:
        """调用固定配置的研究模型(opencode 会话)。抛 BudgetExhausted。"""
        raise NotImplementedError

    async def evaluate_factor(self, candidate: FactorCandidate,
                              task: dict) -> dict:
        """执行因子测试(公开窗,窗口由网关从 task 绑定,程序不可指定)。

        返回 {score: float|None, error: str|None,
              error_kind: 'platform_transient'|'code'|'quality'|'unknown',
              execution_id: str|None}
        error_kind 由网关独立判定(不信任程序分类)。
        抛 BudgetExhausted。
        """
        raise NotImplementedError

    async def query_history(self, key: str) -> Any:
        """读本次运行允许访问的历史(树状态/上一次结果)。"""
        raise NotImplementedError

    async def read_artifact(self, path: str) -> str | None:
        """读取研究工作区内 agent 会话写出的文件(如 .h)。

        程序不能自己 open()(安全边界);agent 会话写的文件经网关读回。
        路径限制在工作区内(网关校验),越界返回 None。
        """
        raise NotImplementedError


# ---------------------------------------------------------------- 程序接口

class ResearchProgram:
    """研究程序接口(agent_program/ 的入口,外层可整体改写)。

    run() 内部没有任何固定算法要求——树搜索/多分支/全新方法都可以。
    预算耗尽时网关抛 BudgetExhausted,程序可以捕获做收尾(返回已有
    候选),但不能继续调 api。
    """

    async def run(self, task: dict, api: ResearchAPI) -> ResearchResult:
        raise NotImplementedError
