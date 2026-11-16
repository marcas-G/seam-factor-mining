"""outer_program —— 外层改写完整 agent_program 目录(P1 核心)。

取代单文件 strategy.py 改写:外环 agent 的 workspace 里 incumbent/
是整个研究程序目录,它可以增删模块/换搜索结构/重写一切——
唯一硬约束是入口契约(ResearchProgram 子类)+安全规则(预检把关)。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .program_precheck import full_precheck

# 候选程序目录的修改规则(写进 TASK.md,外环 agent 的约束)
PROGRAM_RULES = """
## 修改规则(硬约束)
- 入口契约: 必须有 ResearchProgram 的子类且可无参实例化
  (from seam.contracts.contracts import ResearchProgram)
- **禁相对导入**(from .xxx): 你的目录会被独立加载,跨文件一律
  绝对导入或同文件定义
- 禁 import: os/sys/subprocess/socket/shutil/requests/urllib
- 禁调用: open()/eval()/exec()/__import__()
- 外部能力只经 api 参数(contracts.ResearchAPI):
  call_model / evaluate_factor / read_artifact / query_history
- 预算耗尽 = api 调用抛 BudgetExhausted,捕获后收尾返回即可

## 设计自由度(鼓励探索——形态不是前提,是选择)
- 单 agent 树搜索只是 v0 的历史默认,不是必然形态。以下全部合法:
  · 多 agent 协作: 假设者/批判者/实现者流水线,或同任务多视角
    竞争择优(api.call_model 每次是独立会话,程序内自由编排)
  · 上下文组织: 给模型看什么(高分节点/失败模式/家族覆盖/
    文献线索)、何时压缩、按算子定制——历代改进的高频方向
  · 会话内指令: call_model 的 prompt 里可以让会话自己读规范、
    自检、多轮推理——prompt 是程序的一部分,随便改
  · 预算分配: 前期广撒网后期精修、按任务自适应、甚至按
    实时反馈动态调整
  · 失败处理: 分类标准、重试策略、失败信息如何回流成上下文
- 评估只认封存分: 形态没有偏好,贵(多耗预算)的方案必须
  用分数证明自己值得
- 不可以: 改变 api 的使用协议之外的一切执行环境
"""


def prepare_program_workspace(workdir: str | Path,
                              incumbent_dir: str | Path,
                              history: list[dict],
                              g_breakdown: dict,
                              data_spec: dict | None = None,
                              factor_summary: dict | None = None) -> Path:
    """备外环 workspace:incumbent/ = 完整程序目录副本。"""
    wd = Path(workdir)
    inc_dst = wd / "incumbent"
    prop_dst = wd / "proposed"
    (wd / "history").mkdir(parents=True, exist_ok=True)
    prop_dst.mkdir(exist_ok=True)

    # 现任程序目录整体复制(含 __init__.py 等全部)
    if inc_dst.exists():
        shutil.rmtree(inc_dst)
    shutil.copytree(incumbent_dir, inc_dst,
                    ignore=shutil.ignore_patterns("__pycache__"))

    # 契约文档(外环 agent 需要知道接口)
    contracts_src = Path(__file__).resolve().parent.parent / "contracts.py"
    shutil.copy(contracts_src, wd / "contracts_reference.py")

    # 历史/证据
    lines = [json.dumps(h, ensure_ascii=False) for h in history]
    if len(lines) > 10:
        agg = {"compressed": True, "n": len(history)}
        lines = [json.dumps(agg)] + lines[-10:]
    (wd / "history" / "lineage.jsonl").write_text("\n".join(lines))
    (wd / "g_breakdown.json").write_text(
        json.dumps(g_breakdown, ensure_ascii=False, indent=1))
    if data_spec:
        (wd / "data_spec.json").write_text(
            json.dumps(data_spec, ensure_ascii=False, indent=1))
    if factor_summary:
        (wd / "factor_summary.json").write_text(
            json.dumps(factor_summary, ensure_ascii=False, indent=1))

    # 冒烟脚本(agent 自跑)
    (wd / "smoke.sh").write_text(
        "#!/bin/bash\n"
        "python3 -c \"\n"
        "import sys; sys.path.insert(0, '<REPO_ROOT>')\n"
        "from seam.outer.program_precheck import full_precheck\n"
        "from pathlib import Path\n"
        "import json\n"
        "r = full_precheck(Path('proposed'))\n"
        "print(json.dumps(r, ensure_ascii=False, default=str))\n"
        "sys.exit(0 if r['verdict']=='passed' else 1)\n"
        "\"\n")
    (wd / "smoke.sh").chmod(0o755)

    # TASK.md
    (wd / "TASK.md").write_text(
        "# 外环任务:重写内层研究程序\n\n"
        "## 你是什么\n"
        "递归自改进的外环。内层是一套完整的研究程序(怎么挖因子);\n"
        "你的工作是重写它,让它更会挖。\n\n"
        "## 工作目录\n"
        "- incumbent/ — 现任研究程序(完整目录,可自由参考)\n"
        "- contracts_reference.py — 接口契约(ResearchProgram/API)\n"
        "- history/lineage.jsonl — 谱系(最近10行)\n"
        "- g_breakdown.json / data_spec.json / factor_summary.json — 证据\n"
        "- smoke.sh — 预检验证(必须自跑通过)\n\n"
        "## 工作流程\n"
        "1. 读证据(g_breakdown/factor_summary)→诊断弱点\n"
        "2. 读 incumbent/ 全部源码,理解现算法\n"
        "3. 复制到 proposed/ 并修改(可增删文件)\n"
        "4. bash smoke.sh 修到 passed\n"
        "5. 写 proposed/verdict.json: "
        "{rationale, target_weak, risk, changed_files}\n\n"
        + PROGRAM_RULES + "\n")
    return wd


def collect_proposal(ws: Path) -> dict:
    """收外环产物:预检 proposed/ → 返回 {ok, program_dir, verdict_doc}。"""
    prop = ws / "proposed"
    vdoc_path = prop / "verdict.json"
    result = {"workspace": str(ws), "ok": False}
    if not (prop / "__init__.py").exists():
        result["reason"] = "proposed/ 缺 __init__.py(入口契约)"
        return result
    pre = full_precheck(prop)
    result["precheck"] = pre
    if pre["verdict"] != "passed":
        result["reason"] = f"precheck: {pre.get('violations') or pre.get('load_msg') or pre.get('smoke_msg')}"
        return result
    if vdoc_path.exists():
        try:
            result["verdict_doc"] = json.loads(vdoc_path.read_text())
        except json.JSONDecodeError:
            result["verdict_doc"] = None
    result["ok"] = True
    result["program_dir"] = str(prop)
    return result


def run_program_outer_agent(ws: Path, model: str = "<OUTER_MODEL_ID>",
                            timeout_s: int = 1800) -> dict:
    """起外环会话(目录级改写)。"""
    prompt = (
        "你的工作目录(唯一合法workspace)是 {ws}。只在这里工作:\n"
        "1. Read {ws}/TASK.md —— 只读这一份(其他路径的 TASK.md 一律忽略,"
        "包括仓库或其他 ws_* 目录里的);\n"
        "2. 严格按其流程: 探索 → cp -r incumbent/. proposed/ → 修改 → "
        "自跑 bash {ws}/smoke.sh 到 passed → 写 {ws}/proposed/verdict.json;\n"
        "3. 全程不要离开 {ws}。完成后只回复 DONE。").format(ws=ws)
    proc = subprocess.run(
        ["opencode", "run", "--model", model, "--format", "json", prompt],
        capture_output=True, text=True, cwd=str(ws), timeout=timeout_s)
    return collect_proposal(ws) | {
        "stdout_tail": proc.stdout[-400:]}
