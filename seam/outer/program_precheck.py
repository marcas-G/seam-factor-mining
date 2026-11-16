"""program_precheck —— 目录级提案预检(P1: 外层改写完整 agent_program/)。

检查项(工程有效性,不判研究好坏):
  1. 入口存在: ResearchProgram 子类(可实例化,有 async run)
  2. 禁危险: os/sys/subprocess/socket/shutil import + open/eval/exec
     (agent_program 内不许直接碰系统;外部能力只经 contracts.ResearchAPI)
  3. 可加载: 目录作为包 import,入口可实例化
  4. 冒烟可跑: fake API 下能完成一次最小运行(在 smoke 阶段做)
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

BANNED_IMPORTS = {"os", "sys", "subprocess", "socket", "shutil",
                  "requests", "urllib"}
BANNED_CALLS = {"open", "eval", "exec", "__import__", "compile"}


def check_entrypoint(program_dir: Path) -> tuple[bool, str]:
    """检查入口契约。"""
    # 找定义了 ResearchProgram 子类的文件
    found_cls = False
    for py in program_dir.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            return False, f"{py.name}: syntax error {e}"
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "ResearchProgram":
                        found_cls = True
    if not found_cls:
        return False, "未找到 ResearchProgram 子类(入口契约缺失)"
    return True, "entry ok"


def check_safety(program_dir: Path) -> tuple[bool, list[str]]:
    """安全检查: 禁危险 import/调用(全目录扫描)。"""
    violations = []
    for py in program_dir.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            violations.append(f"{py.name}: unparseable")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    root = (a.name or "").split(".")[0]
                    if root in BANNED_IMPORTS:
                        violations.append(f"{py.name}: import {root}")
            if isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root in BANNED_IMPORTS:
                    violations.append(f"{py.name}: from {root} import")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id in BANNED_CALLS:
                violations.append(f"{py.name}: call {node.func.id}()")
    return (not violations), violations


def load_program(program_dir: Path, tag: str = "candidate"):
    """加载候选程序,返回 (实例或None, 消息)。

    实现说明: 候选目录不在 aide_miner 包 __path__ 下,动态挂包名
    会产生相对导入与缓存污染两类问题。改为直接 exec 目录内源码
    到受控命名空间(以 aide_miner 为可用名),从命名空间中找入口类。
    exec 只发生在预检通过 safety 之后(precheck 的防线在前)。
    """
    init = program_dir / "__init__.py"
    if not init.exists():
        return None, "缺 __init__.py"
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    mod_name = f"agent_program_{tag}"
    import types as _types
    shell = _types.ModuleType(mod_name)
    shell.__file__ = str(init)
    sys.modules[mod_name] = shell   # dataclass 需要 sys.modules[module] 存在
    ns: dict = shell.__dict__
    # 按依赖顺序 exec: 先非入口文件,后 __init__
    py_files = sorted(program_dir.glob("*.py"),
                      key=lambda p: (p.name == "__init__.py", p.name))
    for py in py_files:
        code = compile(py.read_text(encoding="utf-8"), str(py), "exec")
        try:
            exec(code, ns)
        except Exception as e:
            del sys.modules[mod_name]
            return None, f"加载失败({py.name}): {type(e).__name__}: {e}"
    # 命名空间里找入口(有 async run 的自定义类)
    from seam.contracts.contracts import ResearchProgram
    best = None
    for name, obj in list(ns.items()):
        if (isinstance(obj, type) and obj is not ResearchProgram
                and issubclass(obj, ResearchProgram)):
            try:
                inst = obj()
                if best is None:
                    best = inst
            except Exception:
                continue
    if best is None:
        # 鸭子兼容: 有 async run 方法的类
        for name, obj in list(ns.items()):
            if isinstance(obj, type) and hasattr(obj, "run") \
                    and not name.startswith("_") and name != "ResearchProgram":
                try:
                    inst = obj()
                    best = inst
                    break
                except Exception:
                    continue
    if best is None:
        return None, "入口实例化失败(无 ResearchProgram 子类/兼容类)"
    return best, "ok"


def program_smoke(program, max_steps: int = 3) -> tuple[bool, str]:
    """fake API 冒烟: 程序能跑通最小流程(不判研究质量)。"""
    import asyncio
    from seam.contracts.contracts import BudgetExhausted

    class SmokeAPI:
        def __init__(self):
            self.n = 0
        async def call_model(self, prompt):
            self.n += 1
            if self.n > max_steps:
                raise BudgetExhausted()
            import json
            return json.dumps({"name": f"S{self.n}", "formula": "f",
                               "source": "// s", "intuition": "i",
                               "diff_check": "d", "tags": ["t"]})
        async def evaluate_factor(self, cand, task):
            self.n += 1
            if self.n > max_steps:
                raise BudgetExhausted()
            return {"score": 0.1, "error": None, "error_kind": "unknown",
                    "execution_id": "h"}
        async def query_history(self, key):
            return None

    try:
        result = asyncio.run(program.run({"desc": "smoke"}, SmokeAPI()))
        return True, f"smoke ok: {len(result.candidates)} candidates, " \
                     f"{len(result.trace)} steps"
    except BudgetExhausted:
        return True, "smoke ok (budget exhausted during run)"
    except Exception as e:
        return False, f"smoke crash: {type(e).__name__}: {e}"


def full_precheck(program_dir: Path) -> dict:
    """P1 完整预检管线(完成后清理模块缓存防状态污染)。"""
    result = _full_precheck_impl(program_dir)
    _cleanup_module_cache("aide_miner.agent_program_")
    return result


def _full_precheck_impl(program_dir: Path) -> dict:
    ok_e, msg_e = check_entrypoint(program_dir)
    ok_s, viol = check_safety(program_dir)
    result = {"entrypoint": ok_e, "entry_msg": msg_e,
              "safety": ok_s, "violations": viol}
    if not (ok_e and ok_s):
        result["verdict"] = "rejected"
        return result
    inst, load_msg = load_program(program_dir)
    if inst is None:
        result["verdict"] = "rejected"
        result["load_msg"] = load_msg
        return result
    ok_sm, sm_msg = program_smoke(inst)
    result["smoke"] = ok_sm
    result["smoke_msg"] = sm_msg
    result["verdict"] = "passed" if ok_sm else "rejected"
    return result


def _cleanup_module_cache(prefix: str) -> None:
    """清理模块缓存(防跨测试/跨候选状态污染)。"""
    for k in [k for k in list(sys.modules)
              if k.startswith("agent_program_")
              or k.startswith(prefix)]:
        del sys.modules[k]
