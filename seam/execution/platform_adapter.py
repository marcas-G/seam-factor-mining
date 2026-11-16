"""PlatformAdapter —— 因子评估平台的抽象接口(占位)。

SEAM 架构中,内层程序对平台的全部触达都经 ExecutionGateway,
而 Gateway 对平台的调用收敛到本接口。接入你自己的因子平台时
实现这个协议即可,网关/评价/配对判定层不需要改。

内部实现未开源——它封装了:
  - 因子 .h 的平台创建(内容寻址 hash,幂等)
  - CSVIC/rank-IC 测试的提交/等待/结果提取
  - RES(对既有因子库的残差回归)双通道
  - Execution-ID 与测试记录 ID 的区分协议
  - 瞬态故障(限流/连接)的识别与重试

协议要点(实现方必须遵守):
  evaluate(spec, task) → (score, err)
    spec: {"name", "formula", "source"(完整.h), ...}
    成功时必须把平台内容 hash 写回 spec["_fhash"] —— sealed
    评分与挖矿评分复用同一个平台对象,这是全链路的锚。
  日期窗必须显式传入,禁止默认值(防静默触达封存窗)。
"""
from __future__ import annotations


class PlatformAdapter:
    """抽象: spec → 平台真测 → (rank_ic 或 None, err 或 None)。"""

    def evaluate(self, spec: dict, task: dict) -> tuple[float | None, str | None]:
        raise NotImplementedError("接入你的因子平台(见本文件 docstring 协议)")
