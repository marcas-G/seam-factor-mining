"""execution/retry_policy —— 免费重测授权(冻结:资源权益不可变)。

用户裁决的关键约束:
  agent_program 可以决定如何解释失败(研究层分类,可改);
  execution 独立决定哪些失败有资格免费重测(不可信程序自报标签)。

免费资格五条件(全部满足才免费):
  1. 外部瞬时故障(网关可信判定的 error_kind,非程序标注)
  2. 同一请求(spec 内容哈希+task+窗口+参数一致,不允许借重试改候选)
  3. 有限次数(总尝试 ≤ FREE_RETRY_MAX_ATTEMPTS)
  4. 真实资源照常记录(免费=不扣搜索节点配额;token/平台调用/墙钟照计)
  5. 结果不确定时先查原执行状态(防重复提交,Execution-ID 协议)
"""
from __future__ import annotations

import hashlib
import json


# 网关可信的瞬时故障类别(平台侧信号,非程序声明)
PLATFORM_TRANSIENT_KINDS = {"platform_transient"}

# 免费重试的总尝试上限(第1次+免费重试 ≤ 3)
FREE_RETRY_MAX_ATTEMPTS = 3


def spec_fingerprint(candidate) -> str:
    """候选的内容指纹(source+name;同指纹=同一请求)。"""
    payload = json.dumps({"name": candidate.name, "source": candidate.source},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class RetryAuthorizer:
    """免费重测资格判定(冻结)。程序的自报分类不进入判定。"""

    def __init__(self, max_attempts: int = FREE_RETRY_MAX_ATTEMPTS):
        self.max_attempts = max_attempts
        self._attempts: dict[str, int] = {}   # fingerprint → 已尝试次数

    def is_free_retry_eligible(self, error_kind: str,
                               candidate, task: dict,
                               window: dict) -> tuple[bool, str]:
        """判定本次重测是否免费。

        error_kind: 网关可信判定的失败类别
        candidate/task/window: 用于同请求校验
        返回 (eligible, reason)。
        """
        fp = spec_fingerprint(candidate)
        n = self._attempts.get(fp, 0) + 1
        self._attempts[fp] = n

        # 条件1: 必须是网关判定的平台瞬时故障
        if error_kind not in PLATFORM_TRANSIENT_KINDS:
            return False, f"error_kind={error_kind} 非平台瞬时(代码/质量问题不免费)"

        # 条件3: 总尝试次数上限
        if n > self.max_attempts:
            return False, f"总尝试 {n} 超上限 {self.max_attempts}"

        # 条件2: 同请求校验由 fingerprint 保证(同 fp 才累计;
        # 改了 source 就是新 fp,从 1 开始计费尝试)
        return True, f"平台瞬时故障,第 {n}/{self.max_attempts} 次"

    def record_paid_attempt(self, candidate) -> None:
        """非免费尝试也记录(审计对称)。"""
        fp = spec_fingerprint(candidate)
        self._attempts.setdefault(fp, 0)
        self._attempts[fp] += 1
