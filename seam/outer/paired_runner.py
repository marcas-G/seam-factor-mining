"""paired_runner —— 参数化的配对评估入口(orchestration 收口)。

此前每次跑配对都新写 run_pair*.py——orchestration 没收口的失误。
本模块把"跑一次配对"的全部参数收进一个 PairConfig:

  PairConfig(
      models=Models(outer=..., inner=...),
      tasks=[...],                    # 任务族(scheduler 的或自定义)
      scenes_pickle="...",            # 封存场景数据
      n_repeats=3, budget=3,
      fold_window=(start,end),        # 封存窗
      workers=2,                      # 任务级并行度
      lineage_db=..., sealed_db=..., snapshot=...,
  )
  r = run_paired_evaluation(incumbent_src, candidate_src, pc)

CLI:
  python3 -m aide_miner.outer.paired_runner --config pair_conf.json
"""
from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path

import sys
_REPO = Path(__file__).resolve().parent.parent.parent
_DB_WRITE_LOCK = threading.Lock()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from seam.outer import paired_eval as pe  # noqa: E402
from seam.outer import load_strategy_version  # noqa: E402
from seam.evaluation import hce, meta, schema  # noqa: E402


@dataclass
class Models:
    outer: str = "<OUTER_MODEL_ID>"       # 外环 agent(强,high reasoning)
    inner: str = "<INNER_MODEL_ID>"          # 内环挖矿(快)
    outer_reasoning: str = "high"            # 推理档位(API 侧参数)
    frozen_inner: bool = True                # 评估期锁模型(AIDE² 附录C)


@dataclass
class FoldWindow:
    """封存窗(HCE folds 从这里生成)。"""
    start_date: str = "20250102"
    end_date: str = "20250701"
    n_folds: int = 4
    embargo_days: int = 5


@dataclass
class PairConfig:
    models: Models = field(default_factory=Models)
    tasks: list[dict] = field(default_factory=list)   # [{key,desc,feeds,horizon}]
    scenes_pickle: str = ""
    n_repeats: int = 3
    budget: int = 3
    fold: FoldWindow = field(default_factory=FoldWindow)
    workers: int = 2                                  # 任务级并行度
    lineage_db: str = ""
    sealed_db: str = ""
    snapshot: str = "snap"
    gate_seed: int = 7
    task_family_version: str = "v1"   # v2断代标记(谱系/workspace路由用)

    @classmethod
    def from_json_file(cls, path: str | Path) -> "PairConfig":
        d = json.loads(Path(path).read_text())
        return cls(models=Models(**d.get("models", {})),
                   fold=FoldWindow(**d.get("fold", {})),
                   **{k: v for k, v in d.items()
                      if k not in ("models", "fold")})

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)


def _build_folds(pc: PairConfig) -> tuple[list[dict], dict]:
    """从 FoldWindow 生成 folds + 加载 scenes。"""
    import pickle
    days = pickle.load(open(pc.scenes_pickle, "rb"))
    from seam.evaluation import adapter
    scenes = {}
    for t in pc.tasks:
        # 按 task.key 找 xname(约定: key 形如 "AG@5" → x 列 "SHFE-AG@...")
        feed = t["feeds"][0]
        horizon = t["horizon"]
        names = days[min(days)]["x_names"]
        xname = next((n for n in names
                      if n.startswith(feed + "@") is False
                      and n.startswith(feed)), names[0])
        scene_full = adapter.to_scene_days(days, x_index=names.index(xname),
                                           horizon=horizon)
        scenes[t["key"]] = {k: v for k, v in scene_full.items()
                            if k < int(pc.fold.end_date)}
    keys = sorted(scenes[pc.tasks[0]["key"]])
    n = len(keys)
    S = pc.fold.n_folds
    bounds = [keys[n * i // S] for i in range(S)] + [pc.fold.end_date]
    folds = [{"fold": i, "role": hce.role_for_fold(i, S),
              "test_start": bounds[i], "test_end": bounds[i + 1]}
             for i in range(S)]
    folds = hce.apply_embargo(folds, embargo_days=pc.fold.embargo_days)
    return folds, scenes


def run_paired_evaluation(incumbent_src: str, candidate_src: str,
                          pc: PairConfig) -> dict:
    """参数化的配对评估入口。返回完整结果 dict。"""
    mod_inc = load_strategy_version(incumbent_src, "inc_" + pc.snapshot)
    mod_new = load_strategy_version(candidate_src, "cand_" + pc.snapshot)
    folds, scenes = _build_folds(pc)

    sdb = Path(pc.sealed_db)
    if sdb.exists():
        sdb.unlink()
    sconn = schema.get_conn(str(sdb)).__enter__()
    meta.ensure_miner_schema(sconn)

    # 全并行铺开(网关不限流):所有 (miner × task) 组合同时提交,
    # 组合内部的 repeats 由 grade_one_miner 串行(树搜索因果链不可拆)。
    # 若要 repeats 也并行,用 n_parallel_repeats>1 的单任务 Grade。
    def grade_one_combo(miner_src: str, tag: str, task: dict) -> tuple[str, dict]:
        mod = load_strategy_version(miner_src, f"{pc.snapshot}_{tag}")
        llm = _make_llm(pc.models, frozen=(tag == "inc" and pc.models.frozen_inner))
        res = pe.grade_one_miner(sconn, mod, llm, [task], scenes,
                                 pc.snapshot, folds, pc.n_repeats, pc.budget)
        return task["key"], res

    combos = []
    for tag, src in (("inc", incumbent_src), ("cand", candidate_src)):
        for t in pc.tasks:
            combos.append((tag, src, t))
    n_workers = max(pc.workers, len(combos))
    per_inc, per_cand = {}, {}
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futs = {ex.submit(grade_one_combo, src, tag, t): (tag, t["key"])
                for tag, src, t in combos}
        for fut, (tag, key) in futs.items():
            k, res = fut.result()
            if tag == "inc":
                per_inc[k] = res["g"]
            else:
                per_cand[k] = res["g"]

    keys = sorted(set(per_inc) & set(per_cand))
    # 容忍 None(部分组合评分失败时给诊断而非崩溃):
    missing = [k for k in keys
               if per_inc.get(k) is None or per_cand.get(k) is None]
    if missing:
        print(f"[WARN] {len(missing)} 个任务缺评分(inc_none="
              f"{sum(1 for k in keys if per_inc.get(k) is None)}, "
              f"cand_none={sum(1 for k in keys if per_cand.get(k) is None)}): "
              f"{missing}")
    deltas = [per_cand[k] - per_inc[k] for k in keys
              if per_inc.get(k) is not None and per_cand.get(k) is not None]
    if not deltas:
        return {"g_incumbent_per_task": per_inc,
                "g_candidate_per_task": per_cand,
                "deltas": {}, "gate": {"verdict": "no_data",
                                       "reason": "所有配对差均缺失"
                                       "(sealed评分批量失败?)"},
                "dsr": {"verdict": "no_data"},
                "folds_fingerprint": hce.split_fingerprint(folds)}
    gate = meta.paired_gate(deltas, seed=pc.gate_seed)
    trial_scores = list(per_inc.values()) + list(per_cand.values())

    from seam.evaluation.dsr import selection_adjusted_gate
    dsr = selection_adjusted_gate(deltas, n_trials=max(1, len(pc.tasks) * 10),
                                  base_lo=gate.get("lo") or -1,
                                  base_hi=gate.get("hi") or 1,
                                  trial_scores=trial_scores)

    sconn.__exit__(None, None, None)
    return {"g_incumbent_per_task": per_inc, "g_candidate_per_task": per_cand,
            "deltas": {k: per_cand[k] - per_inc[k] for k in keys},
            "gate": gate, "dsr": dsr,
            "folds_fingerprint": hce.split_fingerprint(folds)}


def _make_llm(models: Models, frozen: bool):
    from aide_miner.agent_runtime import AgentHarness
    h = AgentHarness(model=models.inner, directory=str(_REPO))
    if frozen:
        h.frozen_model = True
    return h


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--incumbent", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--out", default="pair_result.json")
    a = ap.parse_args()
    pc = PairConfig.from_json_file(a.config)
    r = run_paired_evaluation(Path(a.incumbent).read_text(),
                              Path(a.candidate).read_text(), pc)
    Path(a.out).write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print(json.dumps({k: r[k] for k in ("deltas", "gate", "dsr")},
                     ensure_ascii=False, indent=1))


def run_paired_evaluation_programs(inc_program, cand_program,
                                   pc: PairConfig) -> dict:
    """P1 接口:配对评估两个程序实例(ResearchProgram)。

    与 run_paired_evaluation 的区别: 输入是已加载的程序实例
    (目录级改写的产物),不再是源码字符串。
    """
    import numpy as np
    folds, scenes = _build_folds(pc)

    sdb = Path(pc.sealed_db)
    if sdb.exists():
        sdb.unlink()
    sconn = schema.get_conn(str(sdb)).__enter__()
    meta.ensure_miner_schema(sconn)

    import asyncio
    from seam.execution.gateway import ExecutionGateway, ResourceLedger
    from seam.outer.paired_eval import grade_one_miner

    def _grade_combo(program, tag: str, task: dict) -> tuple[str, dict]:
        ledger = ResourceLedger(node_budget=pc.budget * pc.n_repeats)
        gw = ExecutionGateway(
            ledger, model=pc.models.inner,
            task_window={"start_date": task.get("start_date", "20250901"),
                         "end_date": task.get("end_date", "20250930"),
                         "feeds": task.get("feeds", ["SHFE-AG"])})
        # ResearchProgram 是 async; 在网关上跑 repeats 次
        per_rep_ics = []
        import traceback as _tb
        for rep in range(pc.n_repeats):
            try:
                result = asyncio.run(program.run(task, gw))
            except Exception:
                # 静默吞异常 = incumbent全None却无日志的根因(gen3事故)
                print(f"[paired:{tag}:{task['key']}:r{rep}] PROGRAM CRASH:\n"
                      + _tb.format_exc()[-1500:], flush=True)
                continue
            # sealed 评分
            from seam.evaluation.sealed_exec import sealed_grade_factor
            from seam.evaluation.service import composite_g
            from seam.evaluation import service as svc
            rep_ics = []
            for ci, cand in enumerate(result.candidates):
                # hash 从 gateway 回写的 candidate.spec["_fhash"] 直取
                # (evaluate_factor 成功时网关已缓存——不再按名字反查平台,
                #  那是新接口的断链根因: 程序产出未经 create 就到 sealed)
                fh = cand.spec.get("_fhash")
                if not fh:
                    print(f"[paired:{tag}:{task['key']}:r{rep}] cand#{ci} "
                          f"{cand.name[:30]} no _fhash (never created)", flush=True)
                    continue
                for f in folds:
                    # final折泄漏修复(2026-11-16设计审计发现): role字段
                    # 此前从未被使用——4折全进g=外环对着终点线爬山。
                    # 正确语义: search折进g(外环可见); final折只在门通过后
                    # 的转正复核用(其结果不进本g,不回流外环证据包)。
                    if f.get("role") == "final":
                        continue
                    m = sealed_grade_factor(
                        fh, {"name": cand.name, "class_name": cand.name},
                        {"start_date": str(f["test_start"]),
                         "end_date": str(f["test_end"])},
                        feeds=task.get("feeds", ["SHFE-AG"]),
                        horizon=task.get("horizon", 5), with_res=True)
                    if m.get("rank_ic") is None:
                        print(f"[paired:{tag}:{task['key']}:r{rep}] "
                              f"{cand.name[:30]} fold{f.get('fold')} sealed "
                              f"rank_ic=None err={str(m.get('error'))[:150]}",
                              flush=True)
                        continue
                    g_val = composite_g(m["rank_ic"], m.get("res_ic"),
                                        m.get("r2_x"))
                    rep_ics.append(g_val if g_val is not None else 0.0)
            if rep_ics:
                per_rep_ics.append(float(np.mean(rep_ics)))
        g = float(np.mean(per_rep_ics)) if per_rep_ics else None
        return task["key"], {"g": g}

    combos = []
    for tag, prog in (("inc", inc_program), ("cand", cand_program)):
        for t in pc.tasks:
            combos.append((tag, prog, t))

    from concurrent.futures import ThreadPoolExecutor
    per_inc, per_cand = {}, {}
    with ThreadPoolExecutor(max_workers=max(pc.workers, len(combos))) as ex:
        futs = {ex.submit(_grade_combo, prog, tag, t): (tag, t["key"])
                for tag, prog, t in combos}
        for fut, (tag, key) in futs.items():
            k, res = fut.result()
            if tag == "inc":
                per_inc[k] = res["g"]
            else:
                per_cand[k] = res["g"]

    sconn.__exit__(None, None, None)
    keys = sorted(set(per_inc) & set(per_cand))
    deltas = [per_cand[k] - per_inc[k] for k in keys
              if per_inc.get(k) is not None and per_cand.get(k) is not None]
    gate = meta.paired_gate(deltas, seed=pc.gate_seed)
    trial_scores = [v for v in list(per_inc.values()) + list(per_cand.values())
                    if v is not None]
    from seam.evaluation.dsr import selection_adjusted_gate
    dsr = selection_adjusted_gate(
        deltas, n_trials=max(1, len(pc.tasks) * 10),
        base_lo=gate.get("lo") or -1, base_hi=gate.get("hi") or 1,
        trial_scores=trial_scores)
    return {"g_incumbent_per_task": per_inc, "g_candidate_per_task": per_cand,
            "deltas": {k: (per_cand[k] - per_inc[k]) if per_inc.get(k) is not None
                       and per_cand.get(k) is not None else None for k in keys},
            "gate": gate, "dsr": dsr,
            "folds_fingerprint": hce.split_fingerprint(folds)}
