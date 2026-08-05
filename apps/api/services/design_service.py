from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from openroad_platform_analysis.netlist import summarize_netlist
from openroad_platform_visualization import (
    generate_schematic_svg, generate_svg, parse_ports_and_gates,
)


SAFE_ID = re.compile(r"^design-[0-9]+-[a-f0-9]{8}$")
MODULE_RE = re.compile(r"\bmodule\s+([A-Za-z_]\w*)")


class DesignService:
    """Owns generated/imported design artifacts behind a stable API."""

    def __init__(
        self,
        root: Path,
        *,
        legacy_root: Path,
        yosys_bin: Path,
        generation_timeout: int = 360,
    ):
        self.root = root.expanduser().resolve()
        self.legacy_root = legacy_root.expanduser().resolve()
        self.yosys_bin = yosys_bin.expanduser().resolve()
        self.generation_timeout = generation_timeout
        self._generation_lock = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)

    def readiness(self) -> dict[str, Any]:
        generator = self.legacy_root / "generate_and_analyze.py"
        config = self.legacy_root / "config.yaml"
        return {
            "generator_ready": generator.is_file() and config.is_file(),
            "legacy_adapter_root": str(self.legacy_root),
            "yosys_ready": self.yosys_bin.is_file() and os.access(self.yosys_bin, os.X_OK),
        }

    def list(self, limit: int = 30) -> list[dict[str, Any]]:
        manifests = []
        for path in self.root.glob("design-*/manifest.json"):
            try:
                manifests.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        manifests.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return manifests[:max(1, min(limit, 100))]

    def get(self, design_id: str, *, include_source: bool = False) -> dict[str, Any]:
        directory = self._directory(design_id)
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            raise KeyError(f"Unknown design: {design_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        netlist_path = directory / manifest["netlist_file"]
        refreshed_analysis = summarize_netlist(netlist_path)
        if refreshed_analysis != manifest.get("analysis"):
            manifest["analysis"] = refreshed_analysis
            self._write_json(manifest_path, manifest)
        if include_source:
            manifest["rtl_source"] = (directory / manifest["rtl_file"]).read_text(
                encoding="utf-8", errors="replace"
            )
            manifest["netlist_source"] = netlist_path.read_text(
                encoding="utf-8", errors="replace"
            )
        return manifest

    def rtl_path(self, design_id: str) -> Path:
        manifest = self.get(design_id)
        return self._directory(design_id) / manifest["rtl_file"]

    def source(self, design_id: str, kind: str) -> str:
        manifest = self.get(design_id)
        key = "rtl_file" if kind == "rtl" else "netlist_file"
        return (self._directory(design_id) / manifest[key]).read_text(
            encoding="utf-8", errors="replace"
        )

    def schematic(self, design_id: str) -> str:
        manifest = self.get(design_id)
        netlist_path = self._directory(design_id) / manifest["netlist_file"]
        return self._render_schematic(netlist_path)

    def import_rtl(
        self,
        *,
        filename: str,
        source: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        if not source.strip():
            raise ValueError("RTL source is empty")
        module = self._module_name(source)
        design_id, directory = self._new_directory()
        safe_filename = self._safe_filename(filename, module)
        rtl_path = directory / safe_filename
        rtl_path.write_text(source, encoding="utf-8")
        try:
            netlist_path = self._synthesize(rtl_path, module, directory)
            return self._record(
                design_id,
                directory,
                module=module,
                description=description or f"Imported {safe_filename}",
                origin="upload",
                rtl_path=rtl_path,
                netlist_path=netlist_path,
            )
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def generate(self, description: str) -> dict[str, Any]:
        description = description.strip()
        if not description:
            raise ValueError("description is empty")
        if len(description) > 2000:
            raise ValueError("description is too long")
        readiness = self.readiness()
        if not readiness["generator_ready"]:
            raise RuntimeError("legacy generation adapter is not configured")

        script = self.legacy_root / "generate_and_analyze.py"
        env = os.environ.copy()
        env.setdefault("YOSYS_BIN", str(self.yosys_bin))
        with self._generation_lock:
            try:
                result = subprocess.run(
                    [sys.executable, str(script), description],
                    cwd=str(self.legacy_root),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=self.generation_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Circuit generation exceeded {self.generation_timeout} seconds"
                ) from exc
        if result.returncode != 0:
            detail = "\n".join((result.stderr or result.stdout).splitlines()[-12:])
            raise RuntimeError(detail or "Circuit generation failed")

        output_directory = self._output_directory(result.stdout)
        rtl_path, netlist_path = self._find_generated_files(output_directory)
        rtl_source = rtl_path.read_text(encoding="utf-8", errors="replace")
        module = self._module_name(rtl_source)
        design_id, directory = self._new_directory()
        copied_rtl = directory / f"{module}.v"
        copied_netlist = directory / f"{module}_gates.v"
        shutil.copy2(rtl_path, copied_rtl)
        shutil.copy2(netlist_path, copied_netlist)
        return self._record(
            design_id,
            directory,
            module=module,
            description=description,
            origin="natural_language",
            rtl_path=copied_rtl,
            netlist_path=copied_netlist,
            generation_log=result.stdout[-12000:],
        )

    def _synthesize(self, rtl_path: Path, module: str, directory: Path) -> Path:
        if not self.readiness()["yosys_ready"]:
            raise RuntimeError(f"Yosys is unavailable: {self.yosys_bin}")
        netlist_path = directory / f"{module}_gates.v"
        script_path = directory / "synth.ys"
        script_path.write_text(
            f"read_verilog -sv {rtl_path}\n"
            f"synth -top {module}\n"
            "abc\n"
            "simplemap\n"
            "opt_clean\n"
            f"write_verilog -noattr -noexpr {netlist_path}\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(self.yosys_bin), "-Q", "-s", str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0 or not netlist_path.is_file():
            detail = "\n".join(result.stdout.splitlines()[-12:])
            raise RuntimeError(detail or "Yosys did not produce a gate-level netlist")
        return netlist_path

    def _record(
        self,
        design_id: str,
        directory: Path,
        *,
        module: str,
        description: str,
        origin: str,
        rtl_path: Path,
        netlist_path: Path,
        generation_log: str | None = None,
    ) -> dict[str, Any]:
        analysis = summarize_netlist(netlist_path)
        schematic_path = directory / "schematic.svg"
        schematic_path.write_text(self._render_schematic(netlist_path), encoding="utf-8")
        manifest = {
            "id": design_id,
            "module": module,
            "description": description,
            "origin": origin,
            "created_at": time.time(),
            "rtl_file": rtl_path.name,
            "netlist_file": netlist_path.name,
            "schematic_file": schematic_path.name,
            "analysis": analysis,
        }
        if generation_log:
            log_path = directory / "generation.log"
            log_path.write_text(generation_log, encoding="utf-8")
            manifest["generation_log"] = log_path.name
        self._write_json(directory / "manifest.json", manifest)
        return manifest

    @staticmethod
    def _render_schematic(netlist_path: Path) -> str:
        text = netlist_path.read_text(encoding="utf-8", errors="replace")
        try:
            return generate_schematic_svg(text)
        except Exception:
            # Keep the deterministic overview available if Graphviz is absent.
            return generate_svg(parse_ports_and_gates(text))

    def _directory(self, design_id: str) -> Path:
        if not SAFE_ID.fullmatch(design_id):
            raise KeyError(f"Invalid design id: {design_id}")
        return self.root / design_id

    def _new_directory(self) -> tuple[str, Path]:
        design_id = f"design-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        directory = self.root / design_id
        directory.mkdir(parents=True, exist_ok=False)
        return design_id, directory

    @staticmethod
    def _safe_filename(filename: str, module: str) -> str:
        name = Path(filename or f"{module}.v").name
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:v|sv)", name, re.IGNORECASE):
            raise ValueError("filename must be a simple .v or .sv filename")
        return name

    @staticmethod
    def _module_name(source: str) -> str:
        match = MODULE_RE.search(source)
        if not match:
            raise ValueError("RTL does not contain a valid module declaration")
        return match.group(1)

    def _output_directory(self, stdout: str) -> Path:
        match = re.search(r"输出目录:\s*(\S+)", stdout)
        if not match:
            raise RuntimeError("Generator output did not identify its artifact directory")
        path = Path(match.group(1).rstrip("/")).expanduser().resolve()
        try:
            path.relative_to(self.legacy_root)
        except ValueError as exc:
            raise RuntimeError("Generator returned an unsafe output directory") from exc
        if not path.is_dir():
            raise RuntimeError(f"Generator artifact directory is missing: {path}")
        return path

    @staticmethod
    def _find_generated_files(directory: Path) -> tuple[Path, Path]:
        rtl_files = [path for path in directory.glob("*.v") if not path.stem.endswith("_gates")]
        netlist_files = list(directory.glob("*_gates.v"))
        if not rtl_files or not netlist_files:
            raise RuntimeError("Generator did not produce both RTL and gate-level netlist")
        return max(rtl_files, key=lambda path: path.stat().st_mtime), max(
            netlist_files, key=lambda path: path.stat().st_mtime
        )

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(path)
