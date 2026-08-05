#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/gds_preview.py — GDS → PNG 图像预览。

使用成熟的 KLayout `pya` 读取和渲染 GDS；3D 预览由 KLayout 提取真实
多边形，再交给 Matplotlib 3D 做层序堆叠。

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

import tempfile

try:
    import pya
except ImportError:
    pya = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
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
    """Render the exact hierarchical GDS view through KLayout."""
    if pya is None:
        raise RuntimeError("KLayout pya is required for GDS rendering")
    if not gds_path.is_file() or gds_path.stat().st_size == 0:
        raise FileNotFoundError(gds_path)
    target = output
    temporary = None
    if target is None:
        temporary = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        temporary.close()
        target = Path(temporary.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    view = pya.LayoutView()
    view.load_layout(str(gds_path))
    view.max_hier()
    for key in ("text-visible", "properties-visible"):
        try:
            view.set_config(key, "false")
        except Exception:
            pass
    view.zoom_fit()
    width = max(800, int(dpi * 7))
    view.save_image(str(target), width, width)
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("KLayout produced an empty GDS preview")
    if output is not None:
        return None
    data = target.read_bytes()
    target.unlink(missing_ok=True)
    return data


def render_gds_3d(
    gds_path: Path, output: Path | None = None, *, dpi: int = 150,
    max_layers: int = 10, max_polygons_per_layer: int = 180,
) -> bytes | None:
    """Render a sampled real-GDS layer stack; Z is visual layer order, not process thickness."""
    if pya is None or plt is None:
        raise RuntimeError("KLayout pya and matplotlib are required for 3D GDS rendering")
    layout = pya.Layout()
    layout.read(str(gds_path))
    top_cells = layout.top_cells()
    if not top_cells:
        raise ValueError(f"GDS file has no top cell: {gds_path}")
    top = top_cells[0]
    candidates = []
    for index in layout.layer_indexes():
        info = layout.get_info(index)
        region = pya.Region(top.begin_shapes_rec(index))
        if region.is_empty():
            continue
        polygons = []
        for polygon in region.each():
            points = [(point.x * layout.dbu, point.y * layout.dbu)
                      for point in polygon.each_point_hull()]
            if len(points) >= 3:
                polygons.append((abs(polygon.area()), points))
        if polygons:
            polygons.sort(key=lambda item: item[0], reverse=True)
            candidates.append((info.layer, info.datatype,
                               polygons[:max_polygons_per_layer]))
    if not candidates:
        raise ValueError("GDS file contains no renderable polygons")
    layers = sorted(candidates, key=lambda item: (item[0], item[1]))[-max_layers:]
    fig = plt.figure(figsize=(10, 8))
    axis = fig.add_subplot(111, projection="3d")
    for ordinal, (layer, datatype, polygons) in enumerate(layers):
        z = float(ordinal)
        vertices = [[(x, y, z) for x, y in points] for _, points in polygons]
        color, alpha = LAYER_COLORS.get(layer, ("#7f8c8d", 0.45))
        collection = Poly3DCollection(
            vertices, facecolors=color, edgecolors="none", alpha=max(0.25, alpha)
        )
        axis.add_collection3d(collection)
    bbox = top.bbox()
    axis.set_xlim(bbox.left * layout.dbu, bbox.right * layout.dbu)
    axis.set_ylim(bbox.bottom * layout.dbu, bbox.top * layout.dbu)
    axis.set_zlim(-0.5, len(layers) - 0.5)
    axis.set_xlabel("X (µm)")
    axis.set_ylabel("Y (µm)")
    axis.set_zlabel("GDS layer order")
    axis.view_init(elev=32, azim=-58)
    axis.set_title(
        f"GDS Layer Stack: {top.name}\n"
        "KLayout geometry · sampled polygons · Z not to physical scale"
    )
    labels = [f"{layer}/{datatype}" for layer, datatype, _ in layers]
    axis.set_zticks(range(len(labels)), labels=labels, fontsize=7)
    fig.subplots_adjust(left=0.05, right=0.93, bottom=0.06, top=0.90)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, dpi=dpi, bbox_inches="tight")
        plt.close(fig)
        return None
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()


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
