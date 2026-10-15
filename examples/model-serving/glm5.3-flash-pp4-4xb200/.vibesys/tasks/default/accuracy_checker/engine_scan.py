"""Static scan for serving-engine code in a candidate workspace.

OBJECTIVE.md ("Rules") lets a candidate call kernel libraries, including vLLM's
compiled ops and kernel wrappers, but not vLLM's or SGLang's engine,
scheduler, KV-cache manager, model runner, model code or API server, nor
TensorRT-LLM. This scan is the mechanical part of that rule:

- A Python import of a `vllm` module is allowed only when the module is in
  `ALLOWED_VLLM_MODULES` (or below one of them), or sits in an `ops` package
  (vLLM keeps its kernels there: `vllm.v1.attention.ops`,
  `vllm.model_executor.layers.mamba.ops`, `vllm.models.glm5next.nvidia.ops`).
  Every other `vllm` module is engine code. `sglang`, `sglang_router`, `sgl_router` and `tensorrt_llm` are
  engine code entirely. `sgl_kernel`, `flashinfer`, `deep_gemm`, `deep_select`,
  `causal_conv1d`, `tilelang`, `triton` and `torch` are kernel libraries and
  are never flagged.
- `importlib.import_module("...")` and `__import__("...")` with a literal name,
  and a literal argv list holding `"-m", "<module>"`, are checked the same way.
- Shell, text and Python files: `python -m <module>` of a disallowed module,
  `vllm serve` / `sglang serve`, and installing vLLM, SGLang or TensorRT-LLM
  (the evaluation environment already provides vLLM's compiled ops).
- A directory named `vllm`, `sglang` or `tensorrt_llm` in the workspace is a
  vendored engine.

It cannot prove a candidate clean (copied code can be renamed); the judge
still reviews the source.

Usage: python3 engine_scan.py [--workspace DIR]   (exit 1 when findings exist)
"""

from __future__ import annotations

import argparse
import ast
import itertools
import re
import sys
from collections.abc import Iterator
from pathlib import Path

# vLLM's compiled extensions and the Python wrappers and helpers around its
# kernels, besides the `ops` packages. Everything else under `vllm` (engine,
# v1.core scheduler and KV-cache manager, v1.worker model runner, entrypoints,
# model code under models/ and model_executor/, the distributed state, config)
# is engine code.
ALLOWED_VLLM_MODULES = frozenset(
    {
        "vllm._custom_ops",
        "vllm._C",
        "vllm._C_stable_libtorch",
        "vllm._moe_C",
        "vllm._moe_C_stable_libtorch",
        "vllm._flashmla_C",
        "vllm._flashmla_extension_C",
        "vllm._deepselect_C",
        "vllm._flashkda_C",
        "vllm._qutlass_C",
        "vllm.kernels",
        "vllm.vllm_flash_attn",
        "vllm.third_party",
        "vllm.triton_utils",
        "vllm.tilelang_utils",
        "vllm.cute_utils",
        "vllm.scalar_type",
        "vllm.utils.deep_gemm",
        "vllm.utils.flashinfer",
        "vllm.model_executor.layers.quantization.utils",
    }
)
ENGINE_ROOTS = frozenset({"vllm", "sglang", "sglang_router", "sgl_router", "tensorrt_llm"})
# Directory names that mean a vendored engine tree.
VENDORED_ENGINE_DIRS = frozenset({"vllm", "sglang", "tensorrt_llm"})

_ENGINE_PACKAGES = r"vllm|sglang|sglang[-_]router|sgl[-_]router|tensorrt[-_]llm"
_INSTALL = re.compile(
    rf"\b(?:pip3?|uv\s+pip|uv\s+add|conda|mamba)\s+(?:install\s+)?[^\n#]*?"
    rf"(?<![\w-])({_ENGINE_PACKAGES})(?![\w-])"
)
_RUN_MODULE = re.compile(r"\bpython[\d.]*\s+(?:-[A-Za-z]+\s+)*-m\s+([A-Za-z_][\w.]*)")
_ENGINE_CLI = re.compile(r"(?:^|[\s;&|(`])(vllm|sglang)\s+serve\b")
# `.vibesys` holds the harness-owned task files, which legitimately name the engines.
_SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "__pycache__", "node_modules", ".vibesys", "progress"}
)
_TEXT_SUFFIXES = frozenset(
    {".sh", ".bash", ".txt", ".toml", ".cfg", ".yaml", ".yml", ".sbatch", ".py", ""}
)
_IMPORT_CALLS = frozenset({"import_module", "__import__"})
# Larger files are build outputs or data, not candidate source.
_MAX_SCANNED_BYTES = 2 * 1024 * 1024


def is_disallowed_module(name: str) -> bool:
    """True when importing the dotted module `name` uses engine code."""
    root = name.split(".")[0]
    if root not in ENGINE_ROOTS:
        return False
    if root != "vllm":
        return True
    if "ops" in name.split(".")[1:]:
        return False
    return not any(
        name == allowed or name.startswith(allowed + ".") for allowed in ALLOWED_VLLM_MODULES
    )


def _imported_names(node: ast.AST) -> Iterator[str]:
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        # `from vllm import _custom_ops` imports `vllm._custom_ops`; a name that is
        # not a submodule (`from vllm import LLM`) resolves to `vllm.LLM`, engine code.
        yield from (f"{node.module}.{alias.name}" for alias in node.names)
    elif isinstance(node, (ast.List, ast.Tuple)):
        # A subprocess argv such as ["python", "-m", "vllm.entrypoints.openai.api_server"].
        items = [item.value if isinstance(item, ast.Constant) else None for item in node.elts]
        yield from (
            module
            for flag, module in itertools.pairwise(items)
            if flag == "-m" and isinstance(module, str)
        )
    elif isinstance(node, ast.Call) and node.args:
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        first = node.args[0]
        if (
            called in _IMPORT_CALLS
            and isinstance(first, ast.Constant)
            and isinstance(first.value, str)
        ):
            yield first.value


def _python_findings(rel: Path, source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        f"{rel}:{getattr(node, 'lineno', 0)}: imports {name}"
        for node in ast.walk(tree)
        for name in _imported_names(node)
        if is_disallowed_module(name)
    ]


def _line_findings(rel: Path, text: str) -> list[str]:
    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith(("#", "//")):
            continue
        reasons = []
        if _INSTALL.search(line):
            reasons.append("installs an engine")
        reasons.extend(
            f"runs engine module {match.group(1)}"
            for match in _RUN_MODULE.finditer(line)
            if is_disallowed_module(match.group(1))
        )
        if _ENGINE_CLI.search(line):
            reasons.append("runs an engine CLI")
        findings.extend(f"{rel}:{number}: {reason}: {stripped[:120]}" for reason in reasons)
    return findings


def _files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            yield path


def scan(workspace: Path) -> list[str]:
    """Return human-readable findings; empty means no static evidence of engine code."""
    findings: list[str] = []
    for path in _files(workspace):
        rel = path.relative_to(workspace)
        if any(part in VENDORED_ENGINE_DIRS for part in rel.parts[:-1]):
            findings.append(f"{rel}: vendored engine directory")
            continue
        if path.suffix not in _TEXT_SUFFIXES or path.stat().st_size > _MAX_SCANNED_BYTES:
            continue
        text = path.read_text(errors="replace")
        if path.suffix == ".py":
            findings.extend(_python_findings(rel, text))
        findings.extend(_line_findings(rel, text))
    return findings


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    findings = scan(args.workspace)
    for finding in findings:
        print(f"DISALLOWED ENGINE CODE: {finding}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
