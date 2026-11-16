"""HCE 三分封存窗(AIRA₂ Hidden Consistent Evaluation 协议的因子版)。

背景(为什么从两分升级到三分):
  原设计 proxy(挖矿可见)/sealed(封存)两分。缺陷:sealed 同时承担
  外环爬山信号(g)与最终选择(哪个 incumbent 转正)——AIRA₂(arXiv:2603.26499
  §3.3/§4.3.2)实证:同一数据既做爬山又做最终选择,长程搜索必然退化;
  且退化主因是评估噪声(幸运切分),非数据记忆。解法=三分+解耦+一次固定:

    D_pub     挖矿可见(原 proxy 侧,不动)
    D_search  外环 g 计算用;标签对挖矿环隐藏;跨候选跨 seed 切分完全一致
    D_final   最终选择(转正)专用;对挖矿环**和外环搜索过程**都隐藏

  规则:
    1. 切分函数 role_for_fold() 一次定义,永不在运行中改;
    2. g(候选) 只聚合 role='search' 的 fold;
    3. final_select(转正) 只用 role='final' 的 fold,且只在谱系定版时算一次;
    4. assert_consistent_splits(): 每次评分前断言本次 fold 定义与登记的
       指纹一致——防"评估代码悄悄漂移导致 AIRA₂ 说的评估噪声"。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from . import service


# ---------------------------------------------------------------- 角色分配(一次定义)

def role_for_fold(fold: int, n_folds: int, final_frac: float = 0.25) -> str:
    """fold 角色:尾部的 final_frac 比例为 final(最新时段),其余 search。

    final 取尾部(时间上最新)而非随机:因子场景的最终选择应回答
    "这个 miner 在最近的市况下表现如何",随机分会在非平稳序列上
    制造 AIRA₂ 说的 lucky-split 噪声。
    """
    if n_folds < 4:
        raise ValueError("HCE 需要 ≥4 fold 才能三分(search 至少 3)")
    n_final = max(1, round(n_folds * final_frac))
    return "final" if fold >= n_folds - n_final else "search"


def split_fingerprint(folds: list[dict]) -> str:
    """fold 定义的指纹(角色+边界),评分前比对——一致性断言的锚。"""
    canon = json.dumps(
        [{"fold": f["fold"], "role": f["role"],
          "test_start": f["test_start"], "test_end": f["test_end"]}
         for f in sorted(folds, key=lambda x: x["fold"])],
        sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def register_split(conn: sqlite3.Connection, fingerprint: str,
                   snapshot: str) -> None:
    """登记切分指纹(一次);同快照二次登记不同指纹 = 漂移,拒。"""
    conn.execute("""CREATE TABLE IF NOT EXISTS hce_splits (
        fingerprint  TEXT PRIMARY KEY,
        snapshot     TEXT NOT NULL,
        created_ts   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_hce_snapshot ON hce_splits(snapshot)""")
    row = conn.execute("SELECT fingerprint FROM hce_splits WHERE snapshot=?",
                       (snapshot,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO hce_splits VALUES (?,?,CURRENT_TIMESTAMP)",
                     (fingerprint, snapshot))
    elif row["fingerprint"] != fingerprint:
        raise RuntimeError(
            f"HCE 切分漂移:快照 {snapshot} 已登记 {row['fingerprint']},"
            f"本次 {fingerprint}——评估定义变了,拒绝出分(AIRA₂ 评估噪声防线)")


def assert_consistent_splits(conn: sqlite3.Connection, folds: list[dict],
                             snapshot: str) -> bool:
    conn.execute("""CREATE TABLE IF NOT EXISTS hce_splits (
        fingerprint  TEXT PRIMARY KEY,
        snapshot     TEXT NOT NULL,
        created_ts   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    fp = split_fingerprint(folds)
    row = conn.execute("SELECT fingerprint FROM hce_splits WHERE snapshot=?",
                       (snapshot,)).fetchone()
    if row is None:
        register_split(conn, fp, snapshot)
        return True
    if row["fingerprint"] != fp:
        raise RuntimeError(f"HCE 切分不一致:登记 {row['fingerprint']} vs 本次 {fp}")
    return True


# ---------------------------------------------------------------- 解耦的 g 与 final

def grade_hce(conn: sqlite3.Connection, evals_rows: list, snapshot: str,
              folds: list[dict]) -> dict:
    """HCE 版评分:外环 g 只用 search fold;final fold 的分不回流。

    返回 {g_search, n_search, final_locked: True, fingerprint}。
    final fold 的行仍落库(转正时一次性读),但不进 g。
    """
    assert_consistent_splits(conn, folds, snapshot)
    roles = {f["fold"]: f["role"] for f in folds}
    search_rows = [r for r in evals_rows
                   if r["eval_snapshot"] == snapshot
                   and roles.get(r["fold"]) == "search"]
    g = service.dual_track_g(conn, search_rows, snapshot)
    return {"g_search": g["g_all"], "g_recent": g["g_recent"],
            "divergence": g["divergence"], "n_search": g["n"],
            "fingerprint": split_fingerprint(folds)}


def final_select(conn: sqlite3.Connection, evals_rows: list, snapshot: str,
                 folds: list[dict], candidates: list[str]) -> str:
    """最终选择(转正):只用 final fold,只在谱系定版时调用一次。

    candidates: 谱系里待选的 miner label 列表(≥2)。
    返回胜者 label。final fold 数据此前对搜索过程不可见——
    这保证选择信号与爬山信号解耦(AIRA₂ decoupled selection)。
    """
    if len(candidates) < 2:
        raise ValueError("final_select 需要 ≥2 候选")
    roles = {f["fold"]: f["role"] for f in folds}
    finals = {c: [] for c in candidates}
    for r in evals_rows:
        if r["eval_snapshot"] != snapshot or roles.get(r["fold"]) != "final":
            continue
        # evals_rows 需带 miner 标识;约定列名 miner_label(由调用方 join)
        ml = r["miner_label"] if "miner_label" in r.keys() else None
        if ml in finals and r["ic_tstat"] is not None and abs(r["ic_tstat"]) >= 2.0:
            v = r["res_ic"] if r["res_ic"] is not None else r["rank_ic"]
            if v is not None:
                finals[ml].append(v)
    means = {c: (sum(v) / len(v) if v else None) for c, v in finals.items()}
    scored = {c: m for c, m in means.items() if m is not None}
    if not scored:
        raise RuntimeError("final fold 无显著评分,无法选择")
    return max(scored, key=scored.get)


# ---------------------------------------------------------------- embargo(后沿缓冲)

def apply_embargo(windows: list[dict], embargo_days: int = 5) -> list[dict]:
    """给 fold 窗加后沿 embargo(purged CV 第二条泄漏通道)。

    AFML(López de Prado)双通道:
      purge:   测试窗前 h 天内训练标签含测试期信息 → 前 gap≥horizon
      embargo: 测试窗后沿的序列相关(波动聚集/微观结构噪声)
               → 测试窗后必须再留缓冲,挖矿数据不得覆盖

    每个窗返回新增字段:
      mine_end = test_end + embargo_days  挖矿侧数据边界(外推)
      embargo_days  缓冲长度(评分窗不变,挖矿边界后移)
    效果:封存评分只到 test_end;后沿 embargo 段对挖矿与评分都不可见。
    """
    out = []
    for w in windows:
        w2 = dict(w)
        te = w["test_end"]
        try:
            w2["mine_end"] = int(te) + embargo_days   # 数字日期窗
        except (TypeError, ValueError):
            w2["mine_end"] = te                       # 非数字窗原样
        w2["embargo_days"] = embargo_days
        out.append(w2)
    return out
