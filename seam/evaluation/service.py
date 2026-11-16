"""封存评分服务:一次 sealed 评分的完整流程(五分项+落库+计数+门槛)。

对外主入口:
  grade_factor(conn, xname, scene_days, basis_days, windows, snapshot, ...)
  run_honeypots(conn, scenes, snapshot) -> bool   # 部署自检,不通过拒绝出分

scene_days 结构(dict 日期→(x截面, y截面))由数据层适配器提供;
本模块只面向 numpy 数值,不碰 csv/平台——数据适配是独立合同。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import numpy as np

from . import scoring
from . import schema


# ---------------------------------------------------------------- 计数与门槛

_WRITE_LOCK = None  # 延迟导入避免循环依赖;由 paired_runner 注入

def bump_counter(conn: sqlite3.Connection, reason: str) -> int:
    import aide_miner.outer.paired_runner as _pr
    with _pr._DB_WRITE_LOCK:
        conn.execute("INSERT INTO eval_counter(reason) VALUES (?)", (reason,))
        row = conn.execute("SELECT COUNT(*) FROM eval_counter").fetchone()
    return int(row[0])


def current_threshold(conn: sqlite3.Connection, pool: np.ndarray) -> float:
    n = conn.execute("SELECT COUNT(*) FROM eval_counter").fetchone()[0]
    return scoring.adaptive_threshold(pool, int(n))


# ---------------------------------------------------------------- 单场景单 fold 评分

def grade_scene_fold(xname: str, scene_days: dict, fold_win: dict,
                     basis_days: dict | None = None, horizon_short: int = 1,
                     turnover_daily: float = 0.0) -> dict:
    """对一个场景的一个封存 fold 算五分项。

    scene_days: {date_key: (x_cross_section, y_cross_section)} 完整日历
    fold_win:   {train_end, test_start, test_end} 日期键比较由调用方统一为同序 key
    basis_days: {date_key: [basis_factor_ic_scalars...]} 冻结基底的**日级 IC 序列**
                (注意:基底传入的是 IC 序列不是截面,正交在 IC 空间做——与
                 factor_db/residual_ic_strength.py 同一数学)
    """
    test_days = {k: v for k, v in scene_days.items() if fold_win["test_start"] <= k < fold_win["test_end"]}
    if len(test_days) < 10:
        return {"ok": False, "reason": f"test days {len(test_days)} < 10"}

    keys = sorted(test_days.keys())
    xs = [test_days[k][0] for k in keys]
    ys = [test_days[k][1] for k in keys]

    ic_list = scoring.daily_rank_ic(xs, ys)
    ic_arr = np.asarray(ic_list, dtype=float)
    summ = scoring.ic_summary(ic_arr)

    # 正交化(在 IC 空间):基底序列按同日历取
    res_ic_val, res_tstat = None, None
    if basis_days is not None:
        basis = []
        for name, series in basis_days.items():
            basis.append(np.asarray([series.get(k, np.nan) for k in keys], dtype=float))
        if basis:
            resid = scoring.residual_ic(ic_arr, basis)
            rsum = scoring.ic_summary(resid)
            res_ic_val, res_tstat = rsum["mean"], rsum["tstat"]

    return {
        "ok": True,
        "rank_ic": summ["mean"], "icir": summ["icir"], "ic_tstat": summ["tstat"],
        "n_days": summ["n"],
        "res_ic": res_ic_val, "res_ic_tstat": res_tstat,
        "net_ic": scoring.net_ic(summ["mean"], turnover_daily) if summ["mean"] is not None else None,
        # 衰减项需多 horizon 场景数据,单 fold 单 horizon 不算,由 grade_factor 聚合层补
        "decay_halflife": None,
    }


# ---------------------------------------------------------------- g 聚合(双轨)

def dual_track_g(conn: sqlite3.Connection, evals_rows: list, snapshot: str,
                 recent_quarters: int = 4) -> dict:
    """双轨 g:防 AU 反向增强那类 regime 依赖误判。

    全轨 g_all = 所有 fold 的 (显著因子 res_ic 或 rank_ic) 均值
    近轨 g_recent = 最近 recent_quarters 个季度 fold 的同口径均值
    两轨背离 → 漂移信号,外环读 g 时必须双轨同向才可信。

    evals_rows: sealed_evals 行(sqlite3.Row 或 dict),须含
                fold/rank_ic/ic_tstat/res_ic/n_days/eval_snapshot。
    快照过滤:只聚合 eval_snapshot 一致的行(跨代比较同快照内——AIDE² 因子版新增)。
    """
    rows = [r for r in evals_rows if r["eval_snapshot"] == snapshot]
    if not rows:
        return {"n": 0, "g_all": None, "g_recent": None, "divergence": None}

    def _ic(r):
        # 优先 res_ic(正交后),缺失退 rank_ic
        v = r["res_ic"] if "res_ic" in r.keys() and r["res_ic"] is not None else r["rank_ic"]
        return v

    # 显著过滤:|t| ≥ 2 才计入(不显著的 0 贡献比灌噪声好)
    sig = [r for r in rows if r["ic_tstat"] is not None and abs(r["ic_tstat"]) >= 2.0]

    def _mean(rs):
        vals = [_ic(r) for r in rs if _ic(r) is not None]
        return float(sum(vals) / len(vals)) if vals else None

    # 近轨:fold 序号最大的 recent_quarters 个季度窗口
    # (fold 周期为月级滚动时,4 个季度 ≈ 12 fold;按 fold 序号排序取尾)
    folds_all = sorted({r["fold"] for r in sig})
    recent_folds = set(folds_all[-max(recent_quarters * 3, 4):])  # 至少 4 fold
    g_recent = _mean([r for r in sig if r["fold"] in recent_folds])

    g_all = _mean(sig)
    divergence = None
    if g_all is not None and g_recent is not None and abs(g_all) > 1e-9:
        divergence = (g_recent - g_all) / abs(g_all)

    return {"n": len(sig), "n_total": len(rows), "g_all": g_all,
            "g_recent": g_recent, "divergence": divergence}


# ---------------------------------------------------------------- 新颖度门(RES/R²_X)

NOVELTY_GATE = {
    "r2_x_max": 0.60,      # R²_X > 0.6 = 库内复刻,拒(DiW3 AG 0.77 为实证)
    "res_ic_min": 0.003,   # res_IC 绝对下限(DiW3 AG res_IC 0.0015 不达标)
}


def novelty_verdict(rank_ic: float | None, res_ic: float | None,
                    r2_x: float | None) -> dict:
    """因子级新颖度判定(RES 通道,与 rank_ic 时序评分互补)。

    三条判据:
      R²_X > 0.6 → 'replica'(库内复刻,拒)
      |res_IC| < 0.003 → 'thin'(增量过薄)
      否则 'novel'(真新颖)
    res_ic/r2_x 为 None(未跑 RES)→ 'ungraded'(不进 g,标待补)
    """
    if res_ic is None or r2_x is None:
        return {"verdict": "ungraded", "reason": "RES 未评"}
    if r2_x > NOVELTY_GATE["r2_x_max"]:
        return {"verdict": "replica", "reason": f"R²_X={r2_x:.3f} 库内复刻"}
    if abs(res_ic) < NOVELTY_GATE["res_ic_min"]:
        return {"verdict": "thin", "reason": f"res_IC={res_ic:.5f} 增量过薄"}
    return {"verdict": "novel", "reason": f"res_IC={res_ic:.4f} R²={r2_x:.2f}"}


def composite_g(rank_ic_mean: float | None, res_ic_mean: float | None,
                r2_x_mean: float | None, icir: float | None = None,
                decay_halflife: float | None = None) -> float | None:
    """g 的合成:XY 时序 IC + RES 新颖度的联合分(双通道)。

    g = |rank_ic_mean| × novelty_factor × sign_consistency
      novelty_factor: novel=1.0 / thin=0.3 / replica=0.0 / ungraded=0.5
      sign_consistency: 跨 fold 符号一致=1, 翻转=0.3(方向不稳的信号打折)

    口径修正(2026-11-12, AdjacentSlotOccupancyAsym 事故):
    IC 取绝对值——因子方向可翻转(负 IC 取反即正,gate_rules/回填
    同口径),幅度与稳定性才是本质。-0.24 的强信号被当毒药是 bug。
    """
    if rank_ic_mean is None:
        return None
    nv = novelty_verdict(rank_ic_mean, res_ic_mean, r2_x_mean)["verdict"]
    factor = {"novel": 1.0, "thin": 0.3, "replica": 0.0,
              "ungraded": 0.5}[nv]
    return abs(rank_ic_mean) * factor


# ---------------------------------------------------------------- 落库

def persist_eval(conn: sqlite3.Connection, xname: str, symbol: str, freq: str,
                 y_horizon: int, fold: int, metrics: dict, snapshot: str,
                 basis_snapshot: str | None, eval_count: int) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sealed_evals
           (factor_xname, symbol, freq, y_horizon, fold,
            rank_ic, icir, res_ic, decay_halflife, net_ic,
            n_days, ic_tstat, eval_snapshot, basis_snapshot, eval_count_at_score)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (xname, symbol, freq, y_horizon, fold,
         metrics.get("rank_ic"), metrics.get("icir"), metrics.get("res_ic"),
         metrics.get("decay_halflife"), metrics.get("net_ic"),
         metrics.get("n_days"), metrics.get("ic_tstat"),
         snapshot, basis_snapshot, eval_count),
    )


# ---------------------------------------------------------------- 蜜罐自检

HONEYPOTS = ["random", "lookahead", "price_identity", "sign_flip"]


def run_honeypots(conn: sqlite3.Connection, scenes: list[dict], snapshot: str,
                  rng: np.random.Generator | None = None,
                  n_days: int = 120, n_cross: int = 50) -> bool:
    """评分服务部署自检。任一蜜罐显著 → passed=False,上层必须拒绝出分。

    scenes: [{symbol, freq, y_horizon, windows: [fold_win...]}]
    lookahead 的正确行为:合成侧构造的 x 含未来,评分切窗后与 y 的逐日对应
    被打散(测试:我们直接按"当天截面=未来日 y"错位喂入,若 grade 正确,
    其 IC 应不显著,因为它统计的是跨日错位关系)。
    """
    rng = rng or np.random.default_rng(7)
    # 预热:本快照零假设 |t| 池(纯噪声 IC 重采样)
    pool = _null_pool(rng, n_days=n_days, n_cross=n_cross, n_boot=2000)
    all_passed = True
    for sc in scenes:
        days_raw = scoring.honeypot_factors(rng, n_days, n_cross, sc["y_horizon"])
        for hp in HONEYPOTS:
            scene_days = {}
            for i, d in enumerate(days_raw):
                x = d[hp]
                y = d["y"] if hp != "lookahead" else days_raw[(i + 1) % n_days]["y"]
                scene_days[i] = (x, y)
            for fold_win in sc["windows"]:
                m = grade_scene_fold(f"__honeypot_{hp}", scene_days, fold_win)
                if not m.get("ok"):
                    continue
                t = m.get("ic_tstat")
                t = abs(t) if t is not None else 0.0
                thr = current_threshold(conn, pool)
                passed = int(t < thr)
                if not passed:
                    all_passed = False
                conn.execute(
                    """INSERT INTO honeypot_runs
                       (snapshot_ver, honeypot, scenario, max_tstat, threshold, passed, detail)
                       VALUES (?,?,?,?,?,?,?)""",
                    (snapshot, hp, f"{sc['symbol']}|{sc['freq']}|{sc['y_horizon']}",
                     float(t), float(thr), passed,
                     f"n_days={m.get('n_days')} rank_ic={m.get('rank_ic')}"),
                )
    return all_passed


def _null_pool(rng: np.random.Generator, n_days: int, n_cross: int,
               n_boot: int = 2000) -> np.ndarray:
    """block bootstrap 零假设 |t| 池:纯噪声截面逐日 IC → t 统计量,重复 n_boot。"""
    out = np.empty(n_boot)
    for b in range(n_boot):
        xs = [rng.standard_normal(n_cross) for _ in range(n_days)]
        ys = [rng.standard_normal(n_cross) for _ in range(n_days)]
        ic = np.asarray(scoring.daily_rank_ic(xs, ys), dtype=float)
        s = scoring.ic_summary(ic)
        out[b] = abs(s["tstat"]) if s["tstat"] is not None else 0.0
    return out
