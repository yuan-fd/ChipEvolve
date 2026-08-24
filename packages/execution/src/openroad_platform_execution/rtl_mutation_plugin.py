"""Isolated mutation-testing plugin for a frozen RTL verification oracle."""
from __future__ import annotations
import hashlib, os, platform, sys, uuid
from pathlib import Path
from openroad_platform_contracts import PluginManifest, TaskSpec

RTL_MUTATION_PLUGIN_ID = "rtl-mutation"
RTL_MUTATION_PLUGIN_VERSION = "1.0.0"

def build_rtl_mutation_task(*, project_id: str, design_id: str, rtl_path: str | Path, testbench_path: str | Path,
                            testbench_top: str, spec_id: str, verification_id: str, verifier_identity: str,
                            maximum_mutants: int = 32, minimum_score: float = .80,
                            per_mutant_timeout_seconds: int = 30, timeout_seconds: int = 900,
                            labels: dict[str, str] | None = None) -> TaskSpec:
    rtl, tb = Path(rtl_path).expanduser().resolve(), Path(testbench_path).expanduser().resolve()
    if not rtl.is_file() or not tb.is_file() or not testbench_top or not verifier_identity.strip(): raise ValueError("RTL, frozen testbench, top and verifier identity are required")
    if (not 1 <= maximum_mutants <= 128 or not 0 < minimum_score <= 1
            or not 1 <= per_mutant_timeout_seconds <= 300): raise ValueError("mutation bounds are invalid")
    task = TaskSpec(task_id=f"rtl-mutation-{uuid.uuid4().hex}", project_id=project_id, design_id=design_id, plugin_id=RTL_MUTATION_PLUGIN_ID,
        inputs={"rtl": _input(rtl), "testbench": _input(tb), "testbench_top": testbench_top, "spec_id": spec_id, "verification_id": verification_id},
        parameters={"maximum_mutants": maximum_mutants, "minimum_score": minimum_score,
                    "per_mutant_timeout_seconds": per_mutant_timeout_seconds, "verifier_identity": verifier_identity},
        resources={"toolchain_profile": "rtl-mutation"}, timeout_seconds=timeout_seconds,
        expected_artifacts=("mutation_report", "log"), labels=dict(labels or {}))
    task.validate(); return task

def rtl_mutation_plugin_manifest(*, iverilog_bin: str | Path, vvp_bin: str | Path,
                                 python_executable: str | Path = sys.executable, default_timeout_seconds: int = 1200) -> PluginManifest:
    iverilog, vvp = Path(iverilog_bin).expanduser().resolve(), Path(vvp_bin).expanduser().resolve()
    if not iverilog.is_file() or not vvp.is_file(): raise FileNotFoundError("Icarus toolchain is unavailable")
    manifest = PluginManifest(plugin_id=RTL_MUTATION_PLUGIN_ID, plugin_version=RTL_MUTATION_PLUGIN_VERSION,
        adapter_entry=(str(Path(python_executable).resolve()), str(Path(__file__).with_name("rtl_mutation_adapter.py").resolve())),
        capabilities=("eda.rtl.mutation_test",), supported_arch=(platform.machine(),),
        input_schema={"type": "object", "required": ["rtl", "testbench", "testbench_top"]}, output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("iverilog", "vvp"), default_timeout_seconds=default_timeout_seconds,
        artifact_rules=({"kind": "mutation_report", "required": True}, {"kind": "log", "required": True}),
        environment={"IVERILOG_BIN": str(iverilog), "VVP_BIN": str(vvp), "PATH": os.pathsep.join((str(iverilog.parent), str(vvp.parent), "/usr/bin", "/bin"))})
    manifest.validate(); return manifest

def _input(path: Path) -> dict[str, object]: return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}
