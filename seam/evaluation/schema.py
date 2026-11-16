"""sealed.db —— 封存评分库(因子 RSI 外环的 r_priv,防作弊地基)。

与 factors.db 的关系:
  factors.db = 挖矿环可见(r_pub 侧):提交/评估/知识图,现状不动。
  sealed.db  = 挖矿环不可见(r_priv 侧):封存窗评测快照 + 蜜罐记录 + 作弊判定。
  单向依赖:本模块只按 factors.db 的表名惯例读它(因子清单/日级IC),
  factors.db 侧任何代码不 import 本模块(权限边界由部署保证:不同 venv/挂载)。

版本迁移: 内嵌 SEALED_SCHEMA_VERSION + migrate(),仿 factor_db/migrations.py 惯例。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SEALED_SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

-- ============ sealed_windows(封存窗登记)===========
-- 每个评测域(symbol, freq, y_horizon)一段封存测试窗。训练侧数据的公开边界
-- 由 train_end_ns 声明:挖矿环的数据导出必须截到该点,评分在 [test_start, test_end]。
CREATE TABLE IF NOT EXISTS sealed_windows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol         TEXT NOT NULL,
    freq           TEXT NOT NULL,
    y_horizon      INTEGER NOT NULL,
    fold           INTEGER NOT NULL,          -- 滚动 fold 序号(0..K-1)
    train_end_ns   INTEGER NOT NULL,          -- 公开/封存边界(ns 时间戳,开区间:训练数据 < 此值)
    test_start_ns  INTEGER NOT NULL,          -- 封存窗起点(含) ≥ train_end_ns + gap
    test_end_ns    INTEGER NOT NULL,          -- 封存窗终点(不含)
    gap_ns         INTEGER NOT NULL,          -- ≥ y_horizon 最长标签前视跨度(purged split)
    snapshot_ver   TEXT NOT NULL,             -- 快照版本:跨代比较只在同快照内做
    created_ts     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, freq, y_horizon, fold)
);

-- ============ sealed_evals(封存评分快照)===========
-- 一次 sealed 评估 = 1 因子 × 1 场景 × 1 fold 的五分项 + 聚合。
CREATE TABLE IF NOT EXISTS sealed_evals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_xname        TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    freq                TEXT NOT NULL,
    y_horizon           INTEGER NOT NULL,
    fold                INTEGER NOT NULL,
    -- 五分项
    rank_ic             REAL,     -- Spearman 日均
    icir                REAL,     -- mean(IC)/std(IC)
    res_ic              REAL,     -- 对冻结基底集正交化后 IC(residual IC)
    decay_halflife      REAL,     -- IC 衰减半衰期(bar 数,越久越好,缺失=NULL)
    net_ic              REAL,     -- 成本后净 IC(扣换手×成本)
    -- 元数据
    n_days              INTEGER,  -- 测试窗交易日数
    ic_tstat            REAL,     -- rank_ic 的 t 统计量
    eval_snapshot       TEXT NOT NULL,          -- 评分时 sealed_windows.snapshot_ver
    basis_snapshot      TEXT,                   -- res_ic 基底集快照 id(冻结正交基准)
    eval_count_at_score INTEGER,                -- 打分时全局评估计数(自适应门槛审计用)
    created_ts          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(factor_xname, symbol, freq, y_horizon, fold, eval_snapshot)
);

-- ============ honeypot_runs(蜜罐自检)===========
-- 评分服务部署/变更后必须先跑蜜罐:已知无效因子若显著 = 评分管线有 bug,拒绝出分。
CREATE TABLE IF NOT EXISTS honeypot_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    snapshot_ver  TEXT NOT NULL,
    honeypot      TEXT NOT NULL,     -- 'random' | 'lookahead' | 'price_identity' | 'sign_flip'
    scenario      TEXT NOT NULL,     -- symbol|freq|h
    max_tstat     REAL,              -- 蜜罐因子在所有 fold 的最大 |t|
    threshold     REAL,              -- 当时生效的显著性门槛
    passed        INTEGER NOT NULL,  -- 1 = 未显著(通过)
    detail        TEXT
);

-- ============ eval_counter(全局评估计数,自适应多重检验门槛)===========
-- 计数从系统上线累计,不重置。门槛 = block bootstrap 零假设分位数,随 N 增长。
CREATE TABLE IF NOT EXISTS eval_counter (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    reason       TEXT NOT NULL,      -- 'grade' | 'honeypot' | 'backfill'
    created_ts   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ============ hack_verdicts(因子级作弊判定)===========
-- proxy(train 侧 IC,挖矿可见) vs 封存真值的衰减判定。作弊协议的落库点。
CREATE TABLE IF NOT EXISTS hack_verdicts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_xname     TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    freq             TEXT NOT NULL,
    y_horizon        INTEGER NOT NULL,
    proxy_ic         REAL,        -- 挖矿时可见 IC(factor_evals)
    sealed_ic        REAL,        -- 真值A:封存窗 res_ic(或 rank_ic,按 mode)
    later_ic         REAL,        -- 真值B:错后一季窗的 IC
    decay_ratio      REAL,        -- (proxy - sealed)/|proxy| 衰减比例
    keep_ratio       REAL,        -- sealed/later 保持率(仅 later 非 NULL 时)
    is_hack          INTEGER,     -- 判定:proxy显著 且(衰减>60% 或 后窗保持<30%)
    basis_note       TEXT,
    created_ts       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(factor_xname, symbol, freq, y_horizon)
);

CREATE INDEX IF NOT EXISTS idx_sev_factor  ON sealed_evals(factor_xname);
CREATE INDEX IF NOT EXISTS idx_sev_scene   ON sealed_evals(symbol, freq, y_horizon, fold);
CREATE INDEX IF NOT EXISTS idx_hv_factor   ON hack_verdicts(factor_xname);
"""


def get_conn(db_path: str | Path):
    """打开 sealed.db(幂等建表+迁移)。用法同 factor_db.db.get_conn:with get_conn(p) as c."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    # check_same_thread=False: 四路并行的 worker 需要共用评分库连接
    # (单写者模式: 写入点在 persist_eval/bump_counter,加锁串行化见
    #  paired_runner._db_lock;读操作天然并发安全)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return _CtxWrapper(conn)


class _CtxWrapper:
    """with 语法兼容(同 factor_db 惯例:with get_conn(p) as c → commit+close)。"""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()
        return False


def migrate(conn: sqlite3.Connection) -> None:
    """幂等迁移到 SEALED_SCHEMA_VERSION。"""
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sealed_schema_version (version INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT version FROM sealed_schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO sealed_schema_version VALUES (?)", (SEALED_SCHEMA_VERSION,))
    else:
        v = row[0]
        if v > SEALED_SCHEMA_VERSION:
            raise RuntimeError(f"sealed.db 版本 {v} 高于代码支持 {SEALED_SCHEMA_VERSION}")
        # v1 即当前版;未来版本在此追加 if v < 2: ...; v = 2
    conn.commit()
