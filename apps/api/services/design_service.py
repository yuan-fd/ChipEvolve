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


DESIGN_EXAMPLES: tuple[dict[str, str], ...] = (
    {
        "id": "adder8", "name": "8-bit Adder", "level": "starter",
        "description": "Combinational 8-bit adder with carry output.",
        "filename": "adder8.v",
        "rtl_source": "module adder8(input [7:0] a, input [7:0] b, input cin, output [7:0] sum, output cout);\n  assign {cout, sum} = a + b + cin;\nendmodule\n",
    },
    {
        "id": "decoder3to8", "name": "3-to-8 Decoder", "level": "starter",
        "description": "Enabled one-hot decoder.", "filename": "decoder3to8.v",
        "rtl_source": "module decoder3to8(input [2:0] a, input en, output [7:0] y);\n  assign y = en ? (8'b1 << a) : 8'b0;\nendmodule\n",
    },
    {
        "id": "mux4", "name": "4-way Multiplexer", "level": "starter",
        "description": "Parameterized-width four-input multiplexer.", "filename": "mux4.v",
        "rtl_source": "module mux4 #(parameter W=8)(input [W-1:0] a,b,c,d, input [1:0] sel, output reg [W-1:0] y);\n  always @* case(sel) 2'd0:y=a; 2'd1:y=b; 2'd2:y=c; default:y=d; endcase\nendmodule\n",
    },
    {
        "id": "counter16", "name": "16-bit Counter", "level": "starter",
        "description": "Synchronous enabled counter with reset.", "filename": "counter16.v",
        "rtl_source": "module counter16(input clk, input reset, input enable, output reg [15:0] count);\n  always @(posedge clk) begin if(reset) count <= 16'b0; else if(enable) count <= count + 1'b1; end\nendmodule\n",
    },
    {
        "id": "gcd", "name": "GCD Engine", "level": "advanced",
        "description": "Iterative Euclidean greatest-common-divisor engine; the module name also permits linking to the pinned TaiWei 3D acceptance case.",
        "filename": "gcd.v",
        "rtl_source": "module gcd(input clk, input reset, input start, input [31:0] a_in, input [31:0] b_in, output reg [31:0] result, output reg done);\n  reg [31:0] a, b;\n  always @(posedge clk) begin\n    if (reset) begin a <= 0; b <= 0; result <= 0; done <= 0; end\n    else if (start) begin a <= a_in; b <= b_in; done <= 0; end\n    else if (!done) begin\n      if (a == 0) begin result <= b; done <= 1; end\n      else if (b == 0) begin result <= a; done <= 1; end\n      else if (a > b) a <= a - b;\n      else b <= b - a;\n    end\n  end\nendmodule\n",
    },
    {
        "id": "alu8", "name": "8-bit ALU", "level": "advanced",
        "description": "Arithmetic and logic unit with zero and carry flags.", "filename": "alu8.v",
        "rtl_source": "module alu8(input [7:0] a,b, input [2:0] op, output reg [7:0] y, output zero, output reg carry);\n  reg [8:0] t; always @* begin t=9'b0; carry=1'b0; case(op) 3'd0:begin t={1'b0,a}+{1'b0,b};y=t[7:0];carry=t[8];end 3'd1:begin t={1'b0,a}-{1'b0,b};y=t[7:0];carry=t[8];end 3'd2:y=a&b; 3'd3:y=a|b; 3'd4:y=a^b; 3'd5:y=a<<b[2:0]; 3'd6:y=a>>b[2:0]; default:y=8'b0; endcase end assign zero=(y==8'b0);\nendmodule\n",
    },
    {
        "id": "traffic_controller", "name": "Traffic Controller", "level": "advanced",
        "description": "Finite-state traffic-light controller.", "filename": "traffic_controller.v",
        "rtl_source": "module traffic_controller(input clk, input reset, input timer_done, output reg [2:0] lights);\n  reg [1:0] state,next; localparam GREEN=0,YELLOW=1,RED=2; always @(posedge clk) if(reset) state<=RED; else state<=next; always @* begin next=state; case(state) GREEN:begin lights=3'b100;if(timer_done)next=YELLOW;end YELLOW:begin lights=3'b010;if(timer_done)next=RED;end default:begin lights=3'b001;if(timer_done)next=GREEN;end endcase end\nendmodule\n",
    },
    {
        "id": "uart_tx", "name": "UART Transmitter", "level": "advanced",
        "description": "Compact 8-N-1 UART transmitter with configurable divisor.", "filename": "uart_tx.v",
        "rtl_source": "module uart_tx #(parameter DIV=16)(input clk,reset,start,input [7:0] data,output reg tx,output reg busy);\n  reg [9:0] frame; reg [3:0] bit_no; reg [15:0] count; always @(posedge clk) begin if(reset) begin tx<=1'b1;busy<=0;count<=0;bit_no<=0;frame<=10'h3ff;end else if(start&&!busy) begin frame<={1'b1,data,1'b0};busy<=1;count<=0;bit_no<=0;tx<=1'b0;end else if(busy) begin if(count==DIV-1) begin count<=0;bit_no<=bit_no+1'b1;frame<={1'b1,frame[9:1]};tx<=frame[1];if(bit_no==9)begin busy<=0;tx<=1'b1;end end else count<=count+1'b1;end end\nendmodule\n",
    },
    {
        "id": "mini_riscv", "name": "Mini RISC-V Core", "level": "advanced",
        "description": "Small RV32I-style single-cycle teaching core datapath.", "filename": "mini_riscv.v",
        "rtl_source": "module mini_riscv(input clk,reset,input [31:0] instr,input [31:0] rdata,output reg [31:0] pc,output [31:0] addr,wdata,output we);\n  reg [31:0] regs[0:7]; wire [2:0] rs1=instr[17:15],rs2=instr[22:20],rd=instr[9:7]; wire [6:0] opcode=instr[6:0]; wire [31:0] imm={{20{instr[31]}},instr[31:20]}; wire [31:0] lhs=regs[rs1],rhs=regs[rs2]; assign addr=lhs+imm;assign wdata=rhs;assign we=(opcode==7'b0100011); integer i; always @(posedge clk) begin if(reset)begin pc<=0;for(i=0;i<8;i=i+1)regs[i]<=0;end else begin pc<=pc+4;if(opcode==7'b0010011&&rd!=0)regs[rd]<=lhs+imm;else if(opcode==7'b0110011&&rd!=0)regs[rd]<=lhs+rhs;else if(opcode==7'b0000011&&rd!=0)regs[rd]<=rdata;regs[0]<=0;end end\nendmodule\n",
    },
)


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

    @staticmethod
    def examples() -> list[dict[str, str]]:
        """Return audited synthesizable examples used by the web workspace."""
        return [dict(item) for item in DESIGN_EXAMPLES]

    def list(self, limit: int = 30, *, owner_id: str | None = None,
             include_legacy: bool = False) -> list[dict[str, Any]]:
        manifests = []
        for path in self.root.glob("design-*/manifest.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if owner_id is not None and not self._owned(
                    item, owner_id, include_legacy=include_legacy
                ):
                    continue
                manifests.append(item)
            except (OSError, ValueError):
                continue
        manifests.sort(key=lambda item: item.get("created_at", 0), reverse=True)
        return manifests[:max(1, min(limit, 100))]

    def get(self, design_id: str, *, include_source: bool = False,
            owner_id: str | None = None, include_legacy: bool = False) -> dict[str, Any]:
        directory = self._directory(design_id)
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            raise KeyError(f"Unknown design: {design_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if owner_id is not None and not self._owned(
            manifest, owner_id, include_legacy=include_legacy
        ):
            raise KeyError(f"Unknown design: {design_id}")
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

    def rtl_path(self, design_id: str, *, owner_id: str | None = None,
                 include_legacy: bool = False) -> Path:
        manifest = self.get(design_id, owner_id=owner_id, include_legacy=include_legacy)
        return self._directory(design_id) / manifest["rtl_file"]

    def source(self, design_id: str, kind: str, *, owner_id: str | None = None,
               include_legacy: bool = False) -> str:
        manifest = self.get(design_id, owner_id=owner_id, include_legacy=include_legacy)
        key = "rtl_file" if kind == "rtl" else "netlist_file"
        return (self._directory(design_id) / manifest[key]).read_text(
            encoding="utf-8", errors="replace"
        )

    def schematic(self, design_id: str, *, owner_id: str | None = None,
                  include_legacy: bool = False) -> str:
        manifest = self.get(design_id, owner_id=owner_id, include_legacy=include_legacy)
        netlist_path = self._directory(design_id) / manifest["netlist_file"]
        return self._render_schematic(netlist_path)

    def import_rtl(
        self,
        *,
        filename: str,
        source: str,
        description: str | None = None,
        owner_id: str | None = None,
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
                owner_id=owner_id,
            )
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def generate(self, description: str, *, owner_id: str | None = None) -> dict[str, Any]:
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
            owner_id=owner_id,
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
        owner_id: str | None = None,
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
        if owner_id:
            manifest["owner_id"] = owner_id
        if generation_log:
            log_path = directory / "generation.log"
            log_path.write_text(generation_log, encoding="utf-8")
            manifest["generation_log"] = log_path.name
        self._write_json(directory / "manifest.json", manifest)
        return manifest

    @staticmethod
    def _render_schematic(netlist_path: Path) -> str:
        text = netlist_path.read_text(encoding="utf-8", errors="replace")
        overview = parse_ports_and_gates(text)
        # Graphviz produces a useful connected gate diagram for small and medium
        # netlists.  Large teaching cores can contain hundreds of simple cells;
        # use the deterministic type/port overview there so the UI remains
        # responsive while still visualizing synthesized netlist data.
        if int(overview.get("instances", 0)) > 120:
            return generate_svg(overview)
        try:
            return generate_schematic_svg(text)
        except Exception:
            # Keep the deterministic overview available if Graphviz is absent.
            return generate_svg(overview)

    def _directory(self, design_id: str) -> Path:
        if not SAFE_ID.fullmatch(design_id):
            raise KeyError(f"Invalid design id: {design_id}")
        return self.root / design_id

    @staticmethod
    def _owned(manifest: dict[str, Any], owner_id: str,
               *, include_legacy: bool) -> bool:
        recorded = str(manifest.get("owner_id") or "")
        return recorded == owner_id or (include_legacy and not recorded)

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
        # Scan for the first real module declaration, ignoring commented-out
        # ones (e.g. "//module GcdUnit" in ORFS designs would otherwise win).
        lines = []
        in_block = False
        for line in source.splitlines():
            stripped = line.strip()
            if in_block:
                if "*/" in stripped:
                    in_block = False
                continue
            if stripped.startswith("/*"):
                in_block = True
                continue
            if stripped.startswith("//"):
                continue
            lines.append(line)
        match = MODULE_RE.search("\n".join(lines))
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
