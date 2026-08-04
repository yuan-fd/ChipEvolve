#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analysis/parsers/cell_coords.py — 版图密度网格（热力图数据源）

数据来源两条路，按精度优先：
  ① CSV（首选）：flow/analysis_tcl/extract_cells.tcl 从 ODB 导出，含每个 cell 的
     完整 bbox → 可以算「面积占用率」，这是真正的密度。
  ② DEF（兜底）：ORFS 的 3_place.def / 5_route.def 只有摆放坐标、没有单元尺寸
     （尺寸在 LEF 里），因此只能算「单元数密度」。结果里会标 method=count，
     并如实告诉调用方这是近似值——不要把它当成面积密度去下结论。

本模块只做解析与统计，不跑 Docker。跑 TCL 取 CSV 的活儿交给 flow/runner 或
rtl_to_gds.py（见 analysis/pipeline.py 的 collect_layout）。

CLI:
    python3 -m analysis.parsers.cell_coords --csv cells.csv [--meta layout_meta.json]
    python3 -m analysis.parsers.cell_coords --def results/nangate45/x/base/3_place.def
    python3 -m analysis.parsers.cell_coords --workdir demo_output/gds/xxx     # 自动找
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

GRID = 50                 # 网格划分：GRID × GRID
HOTSPOT_THRESHOLD = 0.75  # 覆盖率超过此值算高密度热点


def empty_result(note: str = "") -> dict:
    return {"available": False, "method": None, "grid_size": GRID,
            "die_bounds": None, "core_bounds": None, "total_cells": 0,
            "density_map": [], "hotspots": [], "avg_density": None,
            "max_density": None, "note": note}


# ──────────────────────────────────────────────────────────────────────
# 输入解析
# ──────────────────────────────────────────────────────────────────────
def read_cells_csv(path: Path) -> list[dict]:
    """extract_cells.tcl 的输出：含完整 bbox。"""
    cells = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            try:
                x1, y1 = float(row["x1"]), float(row["y1"])
                x2, y2 = float(row["x2"]), float(row["y2"])
            except (KeyError, TypeError, ValueError):
                continue
            cells.append({"name": row.get("inst_name", ""),
                          "type": row.get("cell_type", ""),
                          "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                          "is_seq": row.get("is_sequential") in ("1", "true", "True")})
    return cells


DEF_COMP_RE = re.compile(
    r"^\s*-\s+(\S+)\s+(\S+)[^;]*?\b(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)",
    re.M | re.S)
DEF_UNITS_RE = re.compile(r"UNITS\s+DISTANCE\s+MICRONS\s+(\d+)")
DEF_DIE_RE = re.compile(
    r"DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)")


def read_def(path: Path) -> tuple[list[dict], dict | None]:
    """DEF 只给坐标，不给尺寸 → 单元退化为质点。"""
    text = path.read_text(errors="replace")
    m = DEF_UNITS_RE.search(text)
    dbu = float(m.group(1)) if m else 1000.0

    die = None
    m = DEF_DIE_RE.search(text)
    if m:
        x1, y1, x2, y2 = (float(v) / dbu for v in m.groups())
        die = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

    # 只在 COMPONENTS 段里找，避免误吃 NETS / PINS
    seg = text
    s = text.find("COMPONENTS")
    e = text.find("END COMPONENTS")
    if s != -1 and e != -1:
        seg = text[s:e]

    cells = []
    for name, cell_type, x, y in DEF_COMP_RE.findall(seg):
        px, py = float(x) / dbu, float(y) / dbu
        cells.append({"name": name, "type": cell_type,
                      "x1": px, "y1": py, "x2": px, "y2": py,
                      "is_seq": bool(re.search(r"DFF|LATCH|SDFF", cell_type, re.I))})
    return cells, die


# ──────────────────────────────────────────────────────────────────────
# 密度网格
# ──────────────────────────────────────────────────────────────────────
def build_density(cells: list[dict], die: dict | None, core: dict | None = None,
                  grid: int = GRID, method: str = "area") -> dict:
    if not cells:
        return empty_result("没有解析到任何单元。")

    if die is None:
        die = {"x1": min(c["x1"] for c in cells), "y1": min(c["y1"] for c in cells),
               "x2": max(c["x2"] for c in cells), "y2": max(c["y2"] for c in cells)}
    W = max(die["x2"] - die["x1"], 1e-9)
    H = max(die["y2"] - die["y1"], 1e-9)
    cw, ch = W / grid, H / grid
    cell_area = cw * ch

    acc = [[0.0] * grid for _ in range(grid)]

    for c in cells:
        if method == "area" and c["x2"] > c["x1"] and c["y2"] > c["y1"]:
            # 单元 bbox 可能横跨多个网格：按重叠面积摊到每个网格
            c0 = int((c["x1"] - die["x1"]) / cw)
            c1 = int((c["x2"] - die["x1"] - 1e-9) / cw)
            r0 = int((c["y1"] - die["y1"]) / ch)
            r1 = int((c["y2"] - die["y1"] - 1e-9) / ch)
            for r in range(max(r0, 0), min(r1, grid - 1) + 1):
                for col in range(max(c0, 0), min(c1, grid - 1) + 1):
                    gx1 = die["x1"] + col * cw
                    gy1 = die["y1"] + r * ch
                    ox = max(0.0, min(c["x2"], gx1 + cw) - max(c["x1"], gx1))
                    oy = max(0.0, min(c["y2"], gy1 + ch) - max(c["y1"], gy1))
                    acc[r][col] += ox * oy
        else:
            # 质点模式（DEF 兜底）：落到哪个格就给哪个格记一次
            col = min(grid - 1, max(0, int((c["x1"] - die["x1"]) / cw)))
            r = min(grid - 1, max(0, int((c["y1"] - die["y1"]) / ch)))
            acc[r][col] += 1.0

    if method == "area":
        dmap = [[min(1.0, v / cell_area) for v in row] for row in acc]
        unit = "单元面积 / 网格面积"
    else:
        peak = max(max(row) for row in acc) or 1.0
        dmap = [[v / peak for v in row] for row in acc]     # 归一化到 0~1
        unit = "单元数（按峰值归一化，非真实面积密度）"

    flat = [v for row in dmap for v in row]
    occupied = [v for v in flat if v > 0]
    hotspots = []
    for r in range(grid):
        for col in range(grid):
            if dmap[r][col] >= HOTSPOT_THRESHOLD:
                hotspots.append({
                    "row": r, "col": col, "density": round(dmap[r][col], 3),
                    "x_um": round(die["x1"] + (col + .5) * cw, 2),
                    "y_um": round(die["y1"] + (r + .5) * ch, 2)})
    hotspots.sort(key=lambda h: -h["density"])

    return {
        "available": True,
        "method": method,                 # area = 真实面积密度；count = 近似
        "density_unit": unit,
        "grid_size": grid,
        "grid_cell_um2": round(cell_area, 4),
        "die_bounds": {k: round(v, 3) for k, v in die.items()},
        "core_bounds": {k: round(v, 3) for k, v in core.items()} if core else None,
        "total_cells": len(cells),
        "sequential_cells": sum(1 for c in cells if c["is_seq"]),
        "density_map": [[round(v, 4) for v in row] for row in dmap],
        "hotspots": hotspots[:20],
        "hotspot_count": len(hotspots),
        "avg_density": round(sum(occupied) / len(occupied), 4) if occupied else 0.0,
        "max_density": round(max(flat), 4) if flat else 0.0,
        "note": None if method == "area" else
                "DEF 里没有单元尺寸（尺寸在 LEF 中），这是按单元数估算的近似密度；"
                "跑 extract_cells.tcl 拿到 CSV 后才是真实面积密度。",
    }


def analyze(csv_path: Path | None = None, def_path: Path | None = None,
            meta_path: Path | None = None, grid: int = GRID) -> dict:
    if csv_path and Path(csv_path).is_file():
        cells = read_cells_csv(Path(csv_path))
        die = core = None
        if meta_path and Path(meta_path).is_file():
            try:
                meta = json.loads(Path(meta_path).read_text())
                die, core = meta.get("die_bounds"), meta.get("core_bounds")
            except ValueError:
                pass
        return build_density(cells, die, core, grid, method="area")

    if def_path and Path(def_path).is_file():
        cells, die = read_def(Path(def_path))
        return build_density(cells, die, None, grid, method="count")

    return empty_result("既没有 cells.csv，也没有 DEF 文件。")


def find_inputs(workdir: Path) -> dict:
    """在一次运行的工作目录里找可用的输入（优先 CSV，其次 route/place 的 DEF）。"""
    workdir = Path(workdir)
    out = {"csv": None, "meta": None, "def": None}
    for c in workdir.rglob("cells.csv"):
        out["csv"] = c
        m = c.with_name("layout_meta.json")
        out["meta"] = m if m.is_file() else None
        break
    for pat in ("5_route.def", "5_*.def", "3_place.def", "3_*.def", "*.def"):
        hits = sorted(workdir.rglob(pat))
        if hits:
            out["def"] = hits[-1]
            break
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="从 CSV / DEF 计算版图密度网格")
    ap.add_argument("--csv", default=None, help="extract_cells.tcl 导出的 cells.csv")
    ap.add_argument("--meta", default=None, help="layout_meta.json（die/core 边界）")
    ap.add_argument("--def", dest="def_", default=None, help="DEF 文件（兜底）")
    ap.add_argument("--workdir", default=None, help="自动在工作目录里找输入")
    ap.add_argument("--grid", type=int, default=GRID)
    a = ap.parse_args(argv)

    csv_p, meta_p, def_p = a.csv, a.meta, a.def_
    if a.workdir:
        found = find_inputs(Path(a.workdir))
        csv_p = csv_p or found["csv"]
        meta_p = meta_p or found["meta"]
        def_p = def_p or found["def"]

    res = analyze(csv_p, def_p, meta_p, a.grid)
    slim = {k: v for k, v in res.items() if k != "density_map"}
    slim["density_map"] = f"<{len(res['density_map'])}×{a.grid} 矩阵已省略>"
    print(json.dumps(slim, indent=2, ensure_ascii=False))
    return 0 if res["available"] else 1


if __name__ == "__main__":
    sys.exit(main())
