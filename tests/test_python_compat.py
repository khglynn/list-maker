"""Ratchet: every pipeline module must import under the repo's own local Python (3.9).

CI runs 3.12, where `def f() -> str | None:` is legal at runtime. Locally the venv is
3.9, where that line raises TypeError the moment the module is imported — and two TAL
maintenance scripts sat like that for months, invisible to CI (which never imports them)
and to pytest (which never covered them), failing only on a manual run on Kevin's Mac.

Importing every module here would drag in side effects, so this checks the one thing
that actually bit: a `X | Y` annotation in a file without `from __future__ import
annotations`. Add the future import (it is free) or drop the union — don't relax this.
"""

from __future__ import annotations

import ast
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _union_annotations(tree: ast.Module) -> list[int]:
    """Line numbers of `A | B` annotations that Python 3.9 would evaluate at import."""
    lines: list[int] = []

    def check(node: ast.AST | None) -> None:
        if node is None:
            return
        for sub in ast.walk(node):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                lines.append(sub.lineno)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check(node.returns)
            for arg in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                check(arg.annotation)
            if node.args.vararg:
                check(node.args.vararg.annotation)
            if node.args.kwarg:
                check(node.args.kwarg.annotation)
        elif isinstance(node, ast.AnnAssign):
            check(node.annotation)
    return lines


def test_every_pipeline_module_is_importable_under_python_3_9() -> None:
    offenders: list[str] = []
    for path in sorted(PIPELINE.rglob("*.py")):
        if "venv" in path.parts or "_cache" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _has_future_annotations(tree):
            continue
        lines = _union_annotations(tree)
        if lines:
            offenders.append(f"{path.relative_to(PIPELINE.parent)}:{lines[0]}")
    assert offenders == [], (
        "PEP 604 union annotations without `from __future__ import annotations` "
        f"(crash on import under the local 3.9 venv): {offenders}"
    )


def test_runtime_requirements_carry_no_dev_tools() -> None:
    """test.yml audits requirements.txt as the production set; a dev tool in it would make
    the gate cry about things that never touch a live credential."""
    def packages(name: str) -> list[str]:
        out = []
        for line in (PIPELINE / name).read_text(encoding="utf-8").splitlines():
            spec = line.split("#", 1)[0].strip()
            if spec:
                out.append(spec.split(">=")[0].split("==")[0].strip().lower())
        return out

    runtime = packages("requirements.txt")
    dev = packages("requirements-dev.txt")
    assert "pytest" not in runtime and "pip-audit" not in runtime, runtime
    assert "-r requirements.txt" in dev and "pytest" in dev and "pip-audit" in dev, dev
