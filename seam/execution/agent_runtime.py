"""agent_runtime 占位 —— LLM agent 会话的抽象。

内部实现未开源: 它封装 opencode CLI 会话(NDJSON 事件流、token
计量、frozen_model 评价期锁模型禁回退)。

协议:
  AgentHarness(harness=HarnessSpec(), model=<id>, directory=<cwd>)
  .complete(prompt) → str
  HarnessSpec.system_prompt(): 会话系统指令(研究纪律)
  frozen_model=True: 评价协议的一部分,模型版本锁定
"""
from __future__ import annotations


class HarnessSpec:
    def __init__(self, spec_dir=None, factors_dir=None, timeout_ms=600_000):
        self.timeout_ms = timeout_ms

    def system_prompt(self) -> str:
        return ""


class AgentHarness:
    def __init__(self, harness=None, model="", directory=None, fake_replies=None):
        self._fake = fake_replies
        self.token_usage = {}

    def complete(self, prompt: str) -> str:
        if self._fake is not None:
            return self._fake.pop(0) if self._fake else "{}"
        raise NotImplementedError("接入你的 agent 会话运行时")
