"""因子级作弊判定(proxy→sealed 衰减协议)与外环谱系表。

依赖:sealed 模块(P0 已验)。本模块提供:
  1. hack 判定逻辑(纯函数,检验覆盖)
  2. factors.db 侧的 miner_versions 迁移(独立函数,不并入 factor_db/migrations.py
     的生产链——那是挖矿环的库,外环表由本模块幂等自建,权限上外环专用)
"""
from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------- 作弊判定(纯函数)

HACK_DECAY_RATIO = 0.60      # proxy→sealed 衰减超 60% 判 hack
HACK_KEEP_RATIO = 0.30       # sealed→later 保持率低于 30% 判 hack


def judge_hack(proxy_ic, sealed_ic, later_ic=None,
               proxy_significant: bool = True) -> dict:
    """单因子单场景 hack 判定。

    判据(预注册,双向都可攻击):
      is_hack = proxy显著 且 [ (proxy−sealed)/|proxy| > 60%  或  later 保持率 <30% ]
    proxy 不显著的因子不参与(hack 定义是"刷分",无分可刷不叫 hack)。
    sealed 为 None(没进封存评测)视同全衰减 —— 挖矿时显著、封存即失踪是典型 hack 前兆。
    """
    if proxy_ic is None or not proxy_significant:
        return {"is_hack": False, "decay_ratio": None, "keep_ratio": None,
                "reason": "proxy_not_significant"}
    denom = abs(proxy_ic)
    if denom < 1e-12:
        return {"is_hack": False, "decay_ratio": None, "keep_ratio": None,
                "reason": "proxy_zero"}
    if sealed_ic is None:
        return {"is_hack": True, "decay_ratio": 1.0, "keep_ratio": None,
                "reason": "sealed_missing"}
    decay = (proxy_ic - sealed_ic) / denom
    keep = (later_ic / sealed_ic) if (later_ic is not None and abs(sealed_ic) > 1e-12) else None
    is_hack = False
    reason = []
    if decay > HACK_DECAY_RATIO:
        is_hack = True
        reason.append(f"decay {decay:.2f}>{HACK_DECAY_RATIO}")
    if keep is not None and keep < HACK_KEEP_RATIO:
        is_hack = True
        reason.append(f"keep {keep:.2f}<{HACK_KEEP_RATIO}")
    return {"is_hack": is_hack, "decay_ratio": decay, "keep_ratio": keep,
            "reason": ";".join(reason) if reason else "clean"}


# ---------------------------------------------------------------- miner_versions(外环谱系)

MINER_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS miner_versions (
    version_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id      INTEGER,                -- 谱系树;根(v0=现人工harness)为 NULL
    label          TEXT NOT NULL,          -- 'v0' | 'v3' | ...(人可读)
    harness_diff   TEXT,                   -- .claude/skills+agents+constants 的 git diff
    change_note    TEXT,                   -- 外环自述:改什么/预期哪类任务起效
    status         TEXT NOT NULL CHECK (status IN
                    ('proposed','smoked','graded','accepted','rejected','observed')),
    g_score        REAL,                   -- sealed 聚合分(统计门后的接受依据)
    g_breakdown    TEXT,                   -- JSON: 分任务 g
    eval_snapshot  TEXT,                   -- sealed 快照版本(跨代比较同快照内)
    smoke_note     TEXT,                   -- 冒烟结果(1任务小预算)
    gate_detail    TEXT,                   -- 统计门明细(配对差 CI/p 值等)
    created_ts     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mv_status ON miner_versions(status);
CREATE INDEX IF NOT EXISTS idx_mv_parent ON miner_versions(parent_id);

-- lessons 表 scope 扩:挖矿机级教训(外环的失败记忆)
-- (若 factors.db 已有 lessons 且 CHECK 锁 'factor',本迁移重建表放宽)
CREATE TABLE IF NOT EXISTS miner_lessons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL CHECK (type IN ('dead_end','success_pattern')),
    scope       TEXT NOT NULL DEFAULT 'miner',
    text        TEXT NOT NULL,
    version_id  INTEGER,
    evidence    TEXT,
    created_ts  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (version_id) REFERENCES miner_versions(version_id)
);
"""


def ensure_miner_schema(conn: sqlite3.Connection) -> None:
    """幂等建外环谱系表(factors.db 或独立库均可;建议同库不同表,便于 join)。"""
    conn.executescript(MINER_SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------- 统计门(配对 block bootstrap)

def paired_gate(deltas: list[float], n_boot: int = 2000, seed: int = 0,
                ci_level: float = 0.90) -> dict:
    """外环接受门:候选 vs 现任的配对差 bootstrap。

    deltas: 每个配对(同任务同seed同快照) g_candidate − g_incumbent。
    判据:90% CI 下界 > 0 → accept;下界≤0<上界 → observed;上界≤0 → reject。
    样本 <5 直接 reject(证据不足,对应"宁拒不收")。
    """
    import numpy as np
    d = np.asarray([x for x in deltas if x is not None], dtype=float)
    if len(d) < 5:
        return {"verdict": "rejected", "reason": f"paired samples {len(d)}<5",
                "lo": None, "hi": None, "mean": float(d.mean()) if len(d) else None}
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, len(d), len(d))
        boots[b] = d[idx].mean()
    lo, hi = np.quantile(boots, [(1 - ci_level) / 2, 1 - (1 - ci_level) / 2])
    if lo > 0:
        verdict = "accepted"
    elif hi <= 0:
        verdict = "rejected"
    else:
        verdict = "observed"
    return {"verdict": verdict, "lo": float(lo), "hi": float(hi),
            "mean": float(d.mean()), "n": int(len(d))}
