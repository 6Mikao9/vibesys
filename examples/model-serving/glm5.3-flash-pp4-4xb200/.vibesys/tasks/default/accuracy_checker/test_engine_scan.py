"""Hermetic tests for the engine-code scan (no GPU, no server).

uv run pytest examples/model-serving/glm5.3-flash-pp4-4xb200/.vibesys/tasks/default/accuracy_checker/test_engine_scan.py -q --no-cov -p no:tach
"""

from __future__ import annotations

import itertools
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import engine_scan

KERNEL_MODULES = (
    "vllm._custom_ops",
    "vllm._C_stable_libtorch",
    "vllm._moe_C_stable_libtorch",
    "vllm._flashkda_C",
    "vllm._deepselect_C",
    "vllm.vllm_flash_attn.cute",
    "vllm.third_party.flash_linear_attention.ops",
    "vllm.v1.attention.ops.common",
    "vllm.model_executor.layers.mamba.ops.causal_conv1d",
    "vllm.models.glm5next.nvidia.ops.kpool_compress",
    "vllm.utils.flashinfer",
    "vllm.utils.deep_gemm",
    "flashinfer",
    "flashinfer.gemm",
    "deep_gemm",
    "deep_select",
    "causal_conv1d",
    "tilelang.language",
    "sgl_kernel",
    "triton.language",
    "torch.distributed",
)
ENGINE_MODULES = (
    "vllm",
    "vllm.LLM",
    "vllm.engine.arg_utils",
    "vllm.v1.core.sched.scheduler",
    "vllm.v1.core.kv_cache_manager",
    "vllm.v1.worker.gpu_model_runner",
    "vllm.entrypoints.openai.api_server",
    "vllm.model_executor.models.deepseek_v2",
    "vllm.models.glm5next.common.model",
    "vllm.distributed.parallel_state",
    "vllm.config",
    "vllm.utils",
    "vllm._custom_ops_shim",
    "sglang",
    "sglang.srt.managers.scheduler",
    "sglang_router",
    "tensorrt_llm.runtime",
)


def _scan(files: dict[str, str]) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return engine_scan.scan(root)


def _import_forms(module: str) -> list[str]:
    parent, _, leaf = module.rpartition(".")
    forms = [
        f"import {module}\n",
        f"import {module} as m\n",
        f"import importlib\nm = importlib.import_module({module!r})\n",
        f"m = __import__({module!r})\n",
    ]
    if parent:
        forms.append(f"from {parent} import {leaf}\n")
    forms.append(f"from {module} import thing\n")
    return forms


class ClassifyTest(unittest.TestCase):
    def test_kernel_modules_are_allowed(self) -> None:
        for module in KERNEL_MODULES:
            with self.subTest(module=module):
                self.assertFalse(engine_scan.is_disallowed_module(module))

    def test_engine_modules_are_denied(self) -> None:
        for module in ENGINE_MODULES:
            with self.subTest(module=module):
                self.assertTrue(engine_scan.is_disallowed_module(module))

    def test_allowlist_entries_and_their_submodules_are_allowed(self) -> None:
        for allowed, suffix in itertools.product(
            sorted(engine_scan.ALLOWED_VLLM_MODULES), ("", ".x", ".x.y")
        ):
            with self.subTest(module=allowed + suffix):
                self.assertFalse(engine_scan.is_disallowed_module(allowed + suffix))


class ScanTest(unittest.TestCase):
    def test_every_import_form_of_a_kernel_module_is_clean(self) -> None:
        for module in KERNEL_MODULES:
            for source in _import_forms(module):
                with self.subTest(source=source):
                    self.assertEqual(_scan({"server.py": source}), [])

    def test_every_import_form_of_an_engine_module_is_flagged(self) -> None:
        for module in ENGINE_MODULES:
            for source in _import_forms(module):
                with self.subTest(source=source):
                    self.assertTrue(_scan({"server.py": source}))

    def test_from_vllm_import_resolves_each_name(self) -> None:
        self.assertEqual(_scan({"a.py": "from vllm import _custom_ops as ops\n"}), [])
        self.assertTrue(_scan({"a.py": "from vllm import _custom_ops, LLM\n"}))

    def test_plain_strings_naming_an_engine_are_not_imports(self) -> None:
        self.assertEqual(
            _scan({"a.py": "NOTE = 'unlike vllm, this server ...'\nprint('vllm')\n"}), []
        )

    def test_launch_scripts(self) -> None:
        flagged = (
            "exec python -m vllm.entrypoints.openai.api_server --port 1\n",
            "python3 -u -m sglang.launch_server --port 1\n",
            "vllm serve nvidia/GLM-5.3-Flash-NVFP4\n",
            "cd x && sglang serve m\n",
        )
        for line in flagged:
            with self.subTest(line=line):
                self.assertTrue(_scan({"vibesys_serve.sh": line}))
        clean = (
            'exec python -m server --port "$PORT"\n',
            "python -m vllm._custom_ops\n",
            "# vllm serve was the old baseline\n",
            "python -m glm_server.main\n",
        )
        for line in clean:
            with self.subTest(line=line):
                self.assertEqual(_scan({"vibesys_serve.sh": line}), [])

    def test_subprocess_argv_running_an_engine_module(self) -> None:
        self.assertTrue(
            _scan({"run.py": "subprocess.run(['python', '-m', 'vllm.entrypoints.cli.main'])\n"})
        )
        self.assertTrue(_scan({"run.py": "argv = ('python3', '-m', 'sglang.launch_server')\n"}))
        self.assertEqual(_scan({"run.py": "subprocess.run(['python', '-m', 'server'])\n"}), [])

    def test_installs(self) -> None:
        for line in (
            "pip install vllm\n",
            "uv pip install sglang[all]\n",
            "pip install tensorrt-llm==1.0\n",
            "uv add sglang-router\n",
        ):
            with self.subTest(line=line):
                self.assertTrue(_scan({"setup.sh": line}))
        for line in (
            "pip install flashinfer-python\n",
            "uv pip install sgl-kernel\n",
            "pip install vllm-kernels-are-not-a-thing-but-not-vllm\n",
            "# pip install vllm\n",
        ):
            with self.subTest(line=line):
                self.assertEqual(_scan({"setup.sh": line}), [])

    def test_vendored_engine_directory(self) -> None:
        self.assertTrue(_scan({"third_party/vllm/v1/core/sched/scheduler.py": "x = 1\n"}))
        self.assertTrue(_scan({"sglang/srt/server.py": "x = 1\n"}))
        self.assertEqual(_scan({"third_party/sgl_kernel/ops.py": "x = 1\n"}), [])

    def test_task_and_state_directories_are_skipped(self) -> None:
        files = {
            ".vibesys/tasks/default/accuracy_checker/make_reference.py": "import vllm\n",
            ".vibesys/tasks/default/README.md": "vllm serve x\n",
            ".venv/lib/vllm/__init__.py": "import vllm.engine\n",
            "server.py": "import torch\nimport flashinfer\n",
        }
        self.assertEqual(_scan(files), [])


if __name__ == "__main__":
    unittest.main()
