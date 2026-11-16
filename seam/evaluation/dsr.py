"""DSR 校正(Bailey & López de Prado 2014)——选择偏差的定量修正。

原文(Backtest Overfitting and Non-Normality, JPM 40th):
  holdout 不考虑试验次数 N → 选择偏差。N 次独立试验的最优 SR
  在零假设下期望为正,随 N 与试验方差 V 增长:
    E[max] = √V · [(1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e))]
  DSR = PSR(阈值=E[max])。通过标准 DSR ≥ 0.95。

我们两层都有选择:
  内环 select_return 从 N 因子挑最优(最优 IC 有正期望)
  外环 argmax g 选 incumbent(最优 g 有正期望)
  → 接受门的阈值必须是 N 的函数,不能只靠 CI。
"""
from __future__ import annotations

import numpy as np
from scipy.stats import norm

EULER_GAMMA = 0.5772156649


def expected_max_sharpe(var_trials: float, n_trials: int) -> float:
    """E[max of N] 解析式(零假设下 N 次试验最优 SR 的期望)。"""
    if n_trials < 2 or var_trials <= 0:
        return 0.0
    v = np.sqrt(var_trials)
    a = (1 - EULER_GAMMA) * norm.ppf(1 - 1.0 / n_trials)
    b = EULER_GAMMA * norm.ppf(1 - 1.0 / (n_trials * np.e))
    return float(v * (a + b))


def deflated_sharpe(sr: float, var_trials: float, n_trials: int,
                    n_obs: int, skew: float = 0.0,
                    kurtosis: float = 3.0) -> float:
    """DSR = P(真实 SR > E[max])。≥0.95 才算显著(文献标准)。"""
    if n_obs < 2:
        return 0.0
    sr0 = expected_max_sharpe(var_trials, n_trials)
    denom = np.sqrt(max(1e-12,
        (1 - skew * sr + (kurtosis - 1) / 4 * sr * sr) / (n_obs - 1)))
    z = (sr - sr0) / denom
    return float(norm.cdf(z))


def selection_adjusted_gate(deltas: list[float], n_trials: int,
                            base_lo: float, base_hi: float,
                            trial_scores: list[float] | None = None) -> dict:
    """选择校正后的接受门:CI 判据 AND DSR 判据 双过才 accepted。

    deltas: 配对 g 差(候选−现任)
    n_trials: 累计独立试验数(外环提案×内环候选的总选择规模)
    trial_scores: 各次试验的分数(算 V=Var(scores))——文献里 V 是
      **各次试验 SR 的方差**,不是差的方差。缺省时退化为差的方差
      (同质差时 V→0,E[max]→0,DSR 退化为 PSR——这是文献允许的退化,
      但会偏松,生产必须传 trial_scores)。
    """
    if not deltas:
        return {"verdict": "rejected", "reason": "no deltas"}
    d = np.asarray(deltas, dtype=float)
    mean_d = float(d.mean())
    # V = 各次试验分数的方差(原文:variance across trials' estimated SR)
    if trial_scores is not None and len(trial_scores) > 1:
        var_v = float(np.var(trial_scores, ddof=1))
    else:
        var_v = float(d.var(ddof=1)) if len(d) > 1 else 0.0
    # ⚠️ 量纲纪律:DSR 的 SR 是 Sharpe 量纲,不是 IC 量纲。
    # IC 差先转 t 统计量(t = mean·√n/std,与 Sharpe 同尺度)再做 DSR。
    std_d = float(d.std(ddof=1)) if len(d) > 1 else 0.0
    if std_d < 1e-12:
        # 零方差分支:deltas 全同值。t 不能爆炸(会骗过 DSR),
        # 按确定性差异处理:t 取均值本身的尺度(相当于"每次差异都这么大"),
        # DSR 再扣 E[max]——大 N 大 V 时 E[max] 大,即使同值正差也该被拦。
        t_stat = mean_d * 100.0  # 尺度因子,不爆炸但保持符号与量级
    else:
        t_stat = mean_d * np.sqrt(len(d)) / std_d
    dsr = deflated_sharpe(t_stat, var_v, n_trials, len(d))
    ci_pass = base_lo > 0
    dsr_pass = dsr >= 0.95
    verdict = "accepted" if (ci_pass and dsr_pass) else \
              ("observed" if base_hi > 0 and not dsr_pass else "rejected")
    return {"verdict": verdict, "dsr": round(dsr, 4),
            "t_stat": round(t_stat, 3),
            "expected_max": round(expected_max_sharpe(var_v, n_trials), 6),
            "ci_pass": ci_pass, "dsr_pass": dsr_pass,
            "note": "DSR:IC 差先转 t 统计量(Sharpe 同尺度)再评;"
                    "直接拿 IC 代入是量纲错误"}
