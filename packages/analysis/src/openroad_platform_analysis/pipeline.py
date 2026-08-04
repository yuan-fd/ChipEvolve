#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis/pipeline.py — 一次调用跑完整条分析链，给 rtl_to_gds.py / web-demo 用。

    stage_json  →  cell_coords  →  diagnosis  →  reporter
    （读 ORFS JSON）（版图密度）  （规则判定）  （组装 + LLM prompt）

集成方式（rtl_to_gds.py 末尾，report() 之前加 4 行）：

    from openroad_platform_analysis.pipeline import analyze_run
    rep = analyze_run(workdir, platform=args.platform, design=design,
                      runtime_seconds=elapsed, image=args.image)
    print(rep["summary_html"])

web-demo/app.py 里同理，把 rep 直接塞进接口返回即可。

CLI:
    python3 -m analysis.pipeline --workdir demo_output/gds/four_bit_counter_1783935437
    python3 -m analysis.pipeline --workdir ... --prompt      # 只打印 LLM prompt
    python3 -m analysis.pipeline --workdir ... --extract-cells  # 额外跑 TCL 取真实密度
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Callable

from openroad_platform_analysis.parsers import cell_coords, stage_json
from openroad_platform_analysis.diagnosis import diagnose
from openroad_platform_analysis.reporter import build_report, build_llm_prompt

DOCKER_IMAGE = os.environ.get("ORFS_IMAGE", "openroad/orfs:latest")
TCL_PATH = Path(__file__).resolve().parent / "assets" / "extract_cells.tcl"


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


# ──────────────────────────────────────────────────────────────────────
def extract_cells_via_openroad(workdir: Path, platform: str, design: str,
                               project_root: Path, stage_odb: str = "5_route.odb",
                               timeout: int = 300,
                               openroad_bin: str | Path | None = None) -> Path | None:
    """
    用当前用户的 OpenROAD 读取 ODB，把每个单元的 bbox 导成 CSV。
    """
    rdir = workdir / "results" / platform / design / "base"
    odb = rdir / stage_odb
    if not odb.is_file():                       # 布线的 ODB 没有就退到放置后的
        for alt in ("5_route.odb", "4_cts.odb", "3_place.odb", "2_floorplan.odb"):
            if (rdir / alt).is_file():
                odb = rdir / alt
                break
        else:
            return None

    out_csv = workdir / "analysis" / "cells.csv"
    out_meta = workdir / "analysis" / "layout_meta.json"
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    tcl = TCL_PATH
    if not tcl.is_file():
        return None

    binary = Path(str(openroad_bin or os.environ.get("OPENROAD_BIN") or Path.home() / "bin/openroad")).expanduser()
    if not binary.is_file():
        return None
    env = os.environ.copy()
    env.update({"ODB_FILE": str(odb.resolve()), "OUT_CSV": str(out_csv.resolve()),
                "OUT_META": str(out_meta.resolve())})
    cmd = [str(binary), "-exit", str(tcl.resolve())]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if r.returncode != 0 or not out_csv.is_file():
        return None
    return out_csv


def extract_cells_via_docker(workdir: Path, platform: str, design: str,
                             project_root: Path, image: str = DOCKER_IMAGE,
                             stage_odb: str = "5_route.odb", timeout: int = 300) -> Path | None:
    """Backward-compatible alias; extraction now uses native OpenROAD."""
    del image
    return extract_cells_via_openroad(workdir, platform, design, project_root,
                                      stage_odb=stage_odb, timeout=timeout)


# ──────────────────────────────────────────────────────────────────────
def analyze_run(workdir, platform: str | None = None, design: str | None = None,
                runtime_seconds: float | None = None,
                extract_cells: bool = False, project_root: Path | None = None,
                image: str = DOCKER_IMAGE, expected_stage: str = "finish",
                llm_analyzer: Callable[[dict], str] | None = None) -> dict:
    """跑完整条分析链，返回 reporter 的最终报告 JSON。任何一环失败都不抛异常。"""
    workdir = Path(workdir).resolve()
    project_root = Path(project_root or Path(__file__).resolve().parent.parent)

    metrics = stage_json.extract_metrics(workdir, platform, design,
                                         expected_stage=expected_stage)
    platform = metrics.get("platform") or platform
    design = metrics.get("design") or design

    # 版图密度：优先真实面积密度（需要跑一次 TCL），否则退到 DEF 近似
    csv_path = None
    if extract_cells and platform and design:
        try:
            csv_path = extract_cells_via_openroad(workdir, platform, design, project_root)
        except Exception:
            csv_path = None

    found = cell_coords.find_inputs(workdir)
    density = cell_coords.analyze(csv_path or found["csv"], found["def"], found["meta"])
    density["source_kind"] = "odb_bbox" if csv_path else ("def_approximation" if found["def"] else "none")
    density["confidence"] = "high" if csv_path else ("medium" if found["def"] else "none")

    diag = diagnose(metrics, density)
    rep = build_report(design, platform, metrics, diag, density, runtime_seconds)
    for name, key in (("run_manifest.json", "run_manifest"),
                      ("stage_deltas.json", "stage_deltas"),
                      ("causal_evidence.json", "causal_evidence"),
                      ("graybox.json", "graybox"),
                      ("parameter_provenance.json", "parameter_provenance"),
                      ("artifact_registry.json", "artifact_registry"),
                      ("log_events.json", "log_events"),
                      ("evaluation.json", "evaluation")):
        path = workdir / "analysis" / name
        if path.is_file():
            try:
                rep[key] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    rep["llm_prompt"] = build_llm_prompt(rep)      # 调用方拿去发给 DeepSeek 即可

    # Narrative generation is optional; deterministic evidence remains authoritative.
    if llm_analyzer is not None:
        try:
            rep["llm_analysis"] = llm_analyzer(rep)
        except Exception as exc:
            rep["llm_analysis"] = f"[LLM analysis failed: {exc}]"

    rep["ai_report_metadata"] = {
        "schema_version": 2,
        "prompt_schema": "graybox-evidence-v2",
        "generator": "caller-provided" if llm_analyzer is not None else None,
        "status": "generated" if "llm_analysis" in rep else "not_requested",
    }
    if isinstance(rep.get("run_manifest"), dict):
        rep["run_manifest"]["ai_report"] = rep["ai_report_metadata"]
        try:
            _write_json_atomic(workdir / "analysis/run_manifest.json", rep["run_manifest"])
        except OSError:
            pass

    # 顺手落盘一份，方便 web-demo 直接以文件形式提供下载
    try:
        out = workdir / "analysis" / "report.json"
        _write_json_atomic(out, rep)
        rep["report_path"] = str(out)
    except OSError:
        pass
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description="RTL-to-GDS 分析管线（指标→密度→诊断→报告）")
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--platform", default=None)
    ap.add_argument("--design", default=None)
    ap.add_argument("--runtime", type=float, default=None)
    ap.add_argument("--extract-cells", action="store_true",
                    help="额外跑一次 OpenROAD TCL，从 ODB 取真实面积密度（需要 Docker）")
    ap.add_argument("--image", default=DOCKER_IMAGE)
    ap.add_argument("--prompt", action="store_true", help="只打印给 DeepSeek 的 prompt")
    ap.add_argument("--brief", action="store_true", help="只打印结论与建议")
    a = ap.parse_args(argv)

    rep = analyze_run(a.workdir, a.platform, a.design, a.runtime,
                      extract_cells=a.extract_cells, image=a.image)

    if a.prompt:
        print(rep["llm_prompt"])
        return 0
    if a.brief:
        print(f"设计: {rep['design']} ({rep['platform']})  结论: {rep['verdict']}")
        print(f"  {rep['diagnosis']['summary']}")
        for v in rep["diagnosis"]["violations"]:
            print(f"  [{v['severity']:7}] {v['message']}")
        if rep["recommendations"]:
            print("  建议:")
            for r in rep["recommendations"]:
                print(f"    · {r}")
        return 0

    slim = dict(rep)
    if slim.get("cell_density", {}).get("density_map"):
        slim["cell_density"] = {**slim["cell_density"],
                                "density_map": "<矩阵已省略，见 report.json>"}
    print(json.dumps(slim, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
