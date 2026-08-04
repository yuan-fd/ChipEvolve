#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/gds_preview.py — GDS → PNG 图像预览。

用 gdspy 读取 GDS 文件，提取各层多边形，用 matplotlib 渲染为图像。
不依赖 KLayout。

CLI:
    python3 tools/gds_preview.py demo_output/gds/xxx/results/.../base/6_final.gds
    python3 tools/gds_preview.py demo_output/gds/xxx/results/.../base/6_final.gds -o preview.png
    python3 tools/gds_preview.py demo_output/gds/xxx/results/.../base/6_final.gds -o preview.png --dpi 200
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import numpy as np

try:
    import gdspy
except ImportError:
    gdspy = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon
except ImportError:
    plt = None


# 常用 layer 颜色
LAYER_COLORS = {
    0:  ("#1f77b4", 0.6),   # 蓝色
    1:  ("#ff7f0e", 0.6),   # 橙色
    2:  ("#2ca02c", 0.6),   # 绿色
    3:  ("#d62728", 0.5),   # 红色
    4:  ("#9467bd", 0.5),   # 紫色
    5:  ("#8c564b", 0.5),   # 棕色
    10: ("#e377c2", 0.3),   # 粉色
    11: ("#7f7f7f", 0.3),   # 灰色
    12: ("#bcbd22", 0.3),   # 黄绿
    13: ("#17becf", 0.3),   # 青色
    14: ("#aec7e8", 0.4),   # 浅蓝
    15: ("#ffbb78", 0.4),   # 浅橙
}


def render_gds(gds_path: Path, output: Path | None = None,
               dpi: int = 150, max_layers: int = 8) -> bytes | None:
    """渲染 GDS 文件为 PNG 图像，返回 bytes 或写入文件。"""
    if gdspy is None:
        raise RuntimeError("需要 gdspy 库：pip3 install gdspy")
    if plt is None:
        raise RuntimeError("需要 matplotlib 库：pip3 install matplotlib")

    lib = gdspy.GdsLibrary(infile=str(gds_path))
    top_cells = lib.top_level()
    if not top_cells:
        raise ValueError(f"GDS 文件 {gds_path} 没有顶层 cell")

    cell = top_cells[0]
    fig, ax = plt.subplots(1, 1, figsize=(8, 8))
    ax.set_aspect("equal")
    ax.set_title(f"GDS Preview: {cell.name}", fontsize=10, pad=8)
    ax.set_xlabel("X (µm)")
    ax.set_ylabel("Y (µm)")

    all_polys = cell.get_polygons(by_spec=True)
    layers_used = sorted(all_polys.keys(), key=lambda k: (k[0], k[1]))[:max_layers]

    for layer, datatype in layers_used:
        polys = all_polys.get((layer, datatype), all_polys.get(layer, []))
        if not polys:
            continue
        color, alpha = LAYER_COLORS.get(layer, ("#999999", 0.3))
        for pts in polys:
            patch = Polygon(pts, facecolor=color, edgecolor="none",
                            alpha=alpha, linewidth=0)
            ax.add_patch(patch)

    # 自动缩放
    all_pts = [v for polys in all_polys.values() for poly in polys for v in poly]
    if all_pts:
        arr = np.array(all_pts)
        x_min, y_min = arr.min(axis=0)
        x_max, y_max = arr.max(axis=0)
        margin = max((x_max - x_min) * 0.05, 1)
        ax.set_xlim(x_min - margin, x_max + margin)
        ax.set_ylim(y_min - margin, y_max + margin)

    ax.grid(True, alpha=0.3, linestyle=":")

    # 图例
    legend_entries = []
    for layer, datatype in layers_used:
        color, alpha = LAYER_COLORS.get(layer, ("#999999", 0.3))
        legend_entries.append(plt.Rectangle((0, 0), 1, 1, fc=color, alpha=alpha,
                                            label=f"Layer {layer}/{datatype}"))
    if legend_entries:
        ax.legend(handles=legend_entries, fontsize=7, loc="upper right",
                  framealpha=0.7)

    plt.tight_layout()

    if output:
        fig.savefig(str(output), dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        print(f"GDS 预览已保存: {output} ({output.stat().st_size / 1024:.0f} KB)")
        return None

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_def(def_path: Path, output: Path | None = None, dpi: int = 150) -> bytes | None:
    """Render cell placement from DEF when a merged GDS is unavailable."""
    if plt is None:
        raise RuntimeError("需要 matplotlib 库：pip3 install matplotlib")
    text = def_path.read_text(encoding="utf-8", errors="replace")
    units_m = re.search(r"UNITS\s+DISTANCE\s+MICRONS\s+(\d+)", text)
    units = float(units_m.group(1)) if units_m else 1000.0
    die_m = re.search(r"DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", text)
    if not die_m:
        raise ValueError("DEF 中没有 DIEAREA")
    x0, y0, x1, y1 = (float(v) / units for v in die_m.groups())
    section_m = re.search(r"COMPONENTS\b.*?;(?P<body>.*?)END COMPONENTS", text, re.S)
    body = section_m.group("body") if section_m else ""
    placed = []
    for item in body.split(";"):
        m = re.search(r"-\s+(\S+)\s+(\S+).*?\+\s+(PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", item, re.S)
        if m:
            placed.append((m.group(3), float(m.group(4)) / units, float(m.group(5)) / units))

    fig, ax = plt.subplots(figsize=(9, 7))
    ax.set_facecolor("#f6f8fa")
    ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                               edgecolor="#1f2328", linewidth=1.5))
    if placed:
        colors = ["#cf222e" if p[0] == "FIXED" else "#0969da" for p in placed]
        size = max(4, min(24, 1600 / len(placed)))
        ax.scatter([p[1] for p in placed], [p[2] for p in placed], s=size,
                   c=colors, alpha=0.7, marker="s", linewidths=0)
    ax.set(xlim=(x0, x1), ylim=(y0, y1), xlabel="X (µm)", ylabel="Y (µm)")
    ax.set_aspect("equal")
    ax.set_title(f"DEF Placement: {def_path.stem} ({len(placed)} cells)")
    ax.grid(True, alpha=0.18, linestyle=":")
    plt.tight_layout()
    if output:
        fig.savefig(output, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return None
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def render_layout(path: Path, output: Path | None = None,
                  dpi: int = 150, max_layers: int = 8) -> bytes | None:
    if path.suffix.lower() == ".def":
        return render_def(path, output=output, dpi=dpi)
    return render_gds(path, output=output, dpi=dpi, max_layers=max_layers)


def main(argv=None):
    ap = argparse.ArgumentParser(description="GDS → PNG 预览")
    ap.add_argument("gds", type=Path, help="6_final.gds 路径")
    ap.add_argument("-o", "--output", type=Path, default=None, help="输出 PNG 路径（默认打印到 stdout）")
    ap.add_argument("--dpi", type=int, default=150, help="渲染分辨率（默认 150）")
    ap.add_argument("--max-layers", type=int, default=8, help="最多渲染的层数（默认 8）")
    a = ap.parse_args(argv)

    if not a.gds.is_file():
        print(f"错误：GDS 文件不存在 {a.gds}", file=sys.stderr)
        return 1

    try:
        data = render_gds(a.gds, a.output, dpi=a.dpi, max_layers=a.max_layers)
        if data and not a.output:
            sys.stdout.buffer.write(data)
        return 0
    except Exception as e:
        print(f"渲染失败：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
