"""retry_classifier —— 失败分层诊断(run-factor 状态机移植)。

三类失败,三条处理路径(老 research-loop 的 failure_layer 语义):
  因子代码侧 → 重写代码(debug 算子定向修, 扣 budget)
  质量/输出侧 → 质量重试(扣 quality_budget)
  平台/瞬时侧 → 原样重测(扣 transient_budget, 不改代码)

在 SearchLoop 里的挂法: evaluate 返回的 error_sig 先过 classifier,
分类结果进 Node(failure_layer),DraftPolicy 按 layer 决定 debug 优先级:
  code 侧失败 → debug 修(现有行为,但现在带定向诊断)
  transient → 同一 spec 原样重测(新:不浪费一次 debug)
  quality → 标记弱候选,不占 debug 名额
"""
from __future__ import annotations

import re

# 失败签名 → 层(匹配 error_sig 文本;按老状态机的 locus+信号)
_RULES = [
    # 平台/瞬时侧(不动代码,重测)
    ("transient", [
        r"no available provider",
        r"network|timeout|timed? ?out",
        r"5\d\d|http 5",
        r"queued|running.*pending",
        r"rate.?limit|cooldown|429",
        r"disk I/O",
    ]),
    # 因子代码侧(定向修代码)
    ("code", [
        r"execution_failed",
        r"compile|syntax error|expected",
        r"SIGABRT|SIGSEGV|core dump",
        r"runtime.*error|divide by zero|nan|inf",
        r"local_compile",                # 本地 g++ 门禁的拦截
        r"no_json_found|parse",          # LLM 输出问题也归代码侧(修 prompt/模板)
    ]),
    # 质量侧(输出太小/全零,不是崩)
    ("quality", [
        r"ic-summary 无有效行|全零|all.?zero",
        r"active_rate.*0\.0",
        r"output_too_small",
    ]),
]


def classify_failure(error_sig: str | None) -> tuple[str, str]:
    """error_sig → (failure_layer, 定向提示)。

    layer: 'transient' | 'code' | 'quality' | 'unknown'
    返回的 hint 进 debug prompt(定向修复,不是盲改)。
    """
    if not error_sig:
        return "unknown", "无失败签名"
    sig = error_sig.lower()
    for layer, patterns in _RULES:
        for pat in patterns:
            if re.search(pat, sig):
                hints = {
                    "transient": "平台瞬态故障——原样重测,不要改代码",
                    "code": "因子代码/实现问题——读失败签名定位,"
                            "保假设只修实现(编译错/运行时错/输出格式)",
                    "quality": "因子输出退化(全零/NaN/覆盖不足)——"
                               "检查 warmup 逻辑与数据边界(尤其封存窗"
                               "的日期形态),不是平台问题",
                }
                return layer, hints[layer]
    return "unknown", "未识别的失败形态——读原始签名自行判断"


def retry_budget_policy(layer: str) -> dict:
    """各层的重试策略(老状态机的三类独立预算)。"""
    return {
        "transient": {"max": 3, "action": "resubmit_same",
                      "note": "同 spec 原样重测(不 debug)"},
        "code": {"max": 3, "action": "debug_rewrite",
                 "note": "定向修代码(debug 算子)"},
        "quality": {"max": 3, "action": "quality_retry",
                    "note": "诊断 warmup/边界后修"},
        "unknown": {"max": 1, "action": "debug_rewrite",
                    "note": "未知形态按 code 处理一次"},
    }[layer]
