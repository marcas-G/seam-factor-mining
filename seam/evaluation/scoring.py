"""封存评分核心:r_priv 的五分项计算 + 自适应门槛 + 蜜罐。

数学合同(检验以此为准):
  daily_rank_ic(X, Y, t): 第 t 日截面 Spearman(X_t, Y_t) —— 逐日独立算
  ic_tstat: mean(ic)/std(ic)*sqrt(n_days)  (H0: ic 均值=0, 逐日近似独立)
  res_ic: target 日级 IC 序列对冻结基底做岭回归,取残差序列重算 mean/std/tstat
  decay_halflife: |ic(h短)|→|ic(h长)| 的指数拟合半衰期(仅多 horizon 时)
  adaptive_threshold(N): block bootstrap 零假设下 max|t| 的 95 分位
      —— N = 全局评估计数;N 越大门槛越高(deflated 思想的 IC 版)

全部函数只依赖 numpy/scipy,无 pandas —— 合成检验可完全复现。
"""
from __future__ import annotations

import numpy as np
from scipy import stats as sps


# ---------------------------------------------------------------- 基础 IC

def daily_rank_ic(x_by_day: list[np.ndarray], y_by_day: list[np.ndarray]) -> list[float]:
    """逐日截面 Spearman。x/y 按日对齐;每日截面 n≥3 才有意义,否则 NaN。"""
    out = []
    for x, y in zip(x_by_day, y_by_day):
        if len(x) != len(y) or len(x) < 3:
            out.append(float("nan"))
            continue
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() < 3:
            out.append(float("nan"))
            continue
        rho = sps.spearmanr(x[mask], y[mask]).statistic
        out.append(float(rho) if np.isfinite(rho) else float("nan"))
    return out


def ic_summary(ic: np.ndarray) -> dict:
    """IC 序列 → mean/std/tstat/icir。NaN 日剔除。"""
    v = ic[np.isfinite(ic)]
    n = len(v)
    if n < 5:
        return {"n": n, "mean": None, "std": None, "tstat": None, "icir": None}
    m, s = float(v.mean()), float(v.std(ddof=1))
    if s == 0:
        return {"n": n, "mean": m, "std": s, "tstat": None, "icir": None}
    return {"n": n, "mean": m, "std": s, "tstat": m / s * np.sqrt(n), "icir": m / s}


# ---------------------------------------------------------------- 正交化

def residual_ic(target_ic: np.ndarray, basis_ics: list[np.ndarray],
                ridge: float = 1e-3) -> np.ndarray:
    """target 日级 IC 对基底 IC(逐列 NaN→0 对齐后)岭回归,返回残差序列。

    基底冻结由调用方保证(basis_snapshot);这里只做算。
    共线基底由 ridge 兜底;空基底 → 原样返回(新颖性=1)。
    """
    t = np.asarray(target_ic, dtype=float)
    if not basis_ics:
        return t.copy()
    B = np.column_stack([np.asarray(b, dtype=float) for b in basis_ics])
    if B.shape[0] != len(t):
        raise ValueError("基底与 target 长度不一致(必须同场景同日历)")
    # NaN 对齐:凡 target NaN 的行,基底也置 0;基底 NaN 置 0(轻微低估解释力,保守)
    B = np.nan_to_num(B, nan=0.0)
    t0 = np.nan_to_num(t, nan=0.0)
    A = B.T @ B + ridge * np.eye(B.shape[1])
    w = np.linalg.solve(A, B.T @ t0)
    resid = t0 - B @ w
    resid[np.isnan(t)] = np.nan  # target NaN 日恢复 NaN
    return resid


# ---------------------------------------------------------------- 衰减

def decay_halflife(ic_short: float, ic_long: float, horizon_short: int,
                   horizon_long: int) -> float | None:
    """两 horizon 的 |IC| 指数衰减半衰期(bar 数)。IC≤0 或跨号时返回 None。"""
    if ic_short is None or ic_long is None or ic_short <= 0 or ic_long <= 0:
        return None
    if horizon_long <= horizon_short or ic_long >= ic_short:
        return None  # 无衰减可拟合
    lam = -np.log(ic_long / ic_short) / (horizon_long - horizon_short)
    return float(np.log(2) / lam)


# ---------------------------------------------------------------- 成本后净 IC

def net_ic(rank_ic: float, turnover_daily: float, cost_per_unit_ic: float = 0.02) -> float:
    """净 IC = rank IC − 换手×单位成本(IC 单位)。线性一阶近似,合同从简。"""
    return rank_ic - turnover_daily * cost_per_unit_ic


# ---------------------------------------------------------------- 自适应门槛

def adaptive_threshold(null_tstats_pool: np.ndarray, n_evals: int,
                       quantile: float = 0.95, floor: float = 2.0,
                       max_t: int = 50_000) -> float:
    """多重检验自适应门槛:deflated 思想的 IC 版。

    null_tstats_pool: block bootstrap 打出来的零假设单次 |t| 池(评分服务预热时生成)。
    n_evals: 全局评估计数 N。门槛 = pool 中 max_N 的分位数 —— 用解析近似:
        E[max of N] ≈ pool.quantile(1 - 1/N) 的稳健替代 = sqrt(2*ln(N)) 尺度修正。
    实现:N 的等效分位 = min(1 - 1/max(N,2), 0.9995),对池取分位,再与 floor 取 max。
    """
    if n_evals < 2 or len(null_tstats_pool) < 100:
        return max(floor, 2.576)  # 单次检验 99% 水平兜底
    q = min(1.0 - 1.0 / n_evals, 0.9995)
    thr = float(np.quantile(null_tstats_pool, q))
    return max(thr, floor)


# ---------------------------------------------------------------- 蜜罐

def honeypot_factors(rng: np.random.Generator, n_days: int, n_cross: int,
                     horizon: int) -> dict[str, list[np.ndarray]]:
    """生成四类已知无效/作弊因子(与 y 无预测关系或作弊性使用未来):

    random:       纯噪声 —— 数学期望 IC=0,门槛内必须不显著
    lookahead:    y 自身的滞后错位(把未来当现在) —— 若评分管线切窗正确,
                  它在封存窗上与 y 对齐关系被打断,IC 应≈0
                  (合成检验中我们用"未来值填当天"构造,正确管线应使其不显著)
    price_identity: 恒定序列/重复价格 —— 截面无方差,IC=NaN,必须不显著
    sign_flip:    随机符号翻转的真实信号 —— 期望 IC=0 且方差与真实因子同量级
    """
    days = []
    for d in range(n_days):
        # y: 日间有微弱自相关结构的"收益",截面 iid 正态
        y = rng.standard_normal(n_cross)
        x_random = rng.standard_normal(n_cross)
        x_lookahead = y * 0.8 + rng.standard_normal(n_cross) * 0.2  # 直接含未来
        x_const = np.ones(n_cross)
        x_sign = np.where(rng.random(n_cross) < 0.5, 1.0, -1.0) * y
        days.append({"y": y, "random": x_random, "lookahead": x_lookahead,
                     "price_identity": x_const, "sign_flip": x_sign})
    return days
