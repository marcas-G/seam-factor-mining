"""sealed_exec 占位 —— 封存窗真执行评分的抽象。

内部实现未开源: 它用 PlatformAdapter 在封存日期窗上重跑同一平台
hash(内容寻址的优势: 挖矿期与封存期评的是同一个对象),提取
rank_ic / res_ic / r2_x,带瞬态重试与 per-hash 并发锁。

协议:
  sealed_grade_factor(fhash, spec, fold_window, feeds, horizon,
                      with_res=True) → dict
  返回 {rank_ic, icir, res_ic, r2_x, library_id, cost} 或 {error}
  失败时 rank_ic=None(绝不抛异常——评分失败是可观测事件,不是崩溃)。
"""
from __future__ import annotations


def sealed_grade_factor(fhash: str, spec: dict, fold_window: dict,
                        feeds: list, horizon: int,
                        with_res: bool = True,
                        poll_timeout: int = 3600) -> dict:
    raise NotImplementedError(
        "接入你的平台(见 platform_adapter.py);fhash 是挖矿期写回的"
        "内容 hash,fold_window={start_date,end_date} 是封存窗")
