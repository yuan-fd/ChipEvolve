"""Deterministic post-processing for a completed TaiWei ORD 3D run.

Platform and design parameterised: the original adapter was hard-coded to the
asap7_3D/gcd acceptance case; all paths, LEF selections and the DEF top cell
now follow the requested tech/case. asap7_3D keeps its strict custom-via
verification; other 3D platforms verify every VIA_* referenced by the final
DEF instead of a fixed via name list.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

# Per-platform stream-out LEF selection (relative to platforms/<tech>/).
# Each 3D platform ships a tech LEF plus bottom/upper standard-cell LEFs.
_PLATFORM_LEFS = {
    "asap7_3D": {
        "tech": "lef/asap7_tech_1x_2A6M7M.lef",
        "bottom": "lef_bottom/asap7sc7p5t_28_R_1x_220121a.bottom.lef",
        "upper": "lef_upper/asap7sc7p5t_28_R_1x_220121a.upper.lef",
    },
    "nangate45_3D": {
        "tech": "lef/NangateOpenCellLibrary.tech21.lef",
        "bottom": "lef_bottom/NangateOpenCellLibrary.macro.mod.bottom.lef",
        "upper": "lef_upper/NangateOpenCellLibrary.macro.mod.upper.lef",
    },
    "asap7_nangate45_3D": {
        "tech": "lef/asap7_nangate45_2A6M10M.lef",
        "bottom": "lef_bottom/NangateOpenCellLibrary.macro.mod.bottom.processed.lef",
        "upper": "lef_upper/asap7sc7p5t_28_R_1x_220121a.upper.processed.lef",
    },
}


def stream_out_gds(staged: Path, final_def: Path, output: Path,
                   tech: str = "asap7_3D", top_cell: str = "gcd") -> dict:
    """Convert the routed DEF to a real GDSII stream with the pinned 3D LEFs."""
    import pya  # KLayout's Python API; imported lazily for fixture compatibility.

    layout_spec = _PLATFORM_LEFS.get(tech)
    if layout_spec is None:
        raise ValueError(f"No stream-out LEF mapping for 3D platform {tech!r}")
    platform = staged / "platforms" / tech
    lefs = [platform / layout_spec["tech"],
            platform / layout_spec["bottom"],
            platform / layout_spec["upper"]]
    missing = [str(path) for path in lefs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"3D stream-out LEF missing: {missing}")

    options = pya.LoadLayoutOptions()
    config = options.lefdef_config
    config.read_lef_with_def = True
    config.lef_files = [str(path) for path in lefs]
    config.produce_routing = True
    config.produce_via_geometry = True
    config.produce_pins = True
    config.produce_labels = True
    # KLayout returns a copy of LEFDEFReaderConfiguration.  It must be stored
    # back on the options object or the LEFs above are silently ignored.
    options.lefdef_config = config

    layout = pya.Layout()
    layout.read(str(final_def), options)
    top = layout.cell(top_cell)
    if top is None:
        raise RuntimeError(f"KLayout did not create the expected {top_cell} top cell")
    via_geometry = _verify_vias(layout, top, final_def, tech)
    output.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(output))
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("KLayout produced an empty GDSII stream")
    _verify_streamed_vias(pya, output, via_geometry, top_cell)
    return {
        "producer": "KLayout pya",
        "producer_version": getattr(pya, "__version__", "unknown"),
        "source_def": str(final_def.relative_to(staged)),
        "lef_files": [str(path.relative_to(staged)) for path in lefs],
        "top_cell": top_cell,
        "output": str(output.relative_to(staged)),
        "abstract_cell_geometry": True,
        "custom_via_geometry": via_geometry,
    }


def _shape_stats(cell, layer_index: int) -> tuple[int, int]:
    iterator = cell.begin_shapes_rec(layer_index)
    count = 0
    area = 0
    while not iterator.at_end():
        count += 1
        area += iterator.shape().area()
        iterator.next()
    return count, area


def _verify_vias(layout, top, final_def: Path, tech: str) -> dict:
    """Prove that cross-tier DEF vias became LEF geometry.

    asap7_3D keeps the audited fixed via-name verification (VIA_M1m_M2add /
    VIA_M2add_M3add). Other platforms verify every via referenced by the final
    DEF (case-insensitive: nangate45_3D emits lower-case names like
    via1_2_...) so no geometry is silently dropped when naming differs.
    """
    def_text = final_def.read_text(encoding="utf-8", errors="replace")
    if tech == "asap7_3D":
        specs = {
            "VIA_M1m_M2add": "V1_add",
            "VIA_M2add_M3add": "V2_add",
        }
    else:
        # Parse the DEF VIAS block: each entry is
        #   - <name> + VIARULE ... + LAYERS Mx <cut> My ...
        via_block = re.search(r"^VIAS\b.*?;\s*(?P<body>.*?)^END VIAS\b", def_text,
                              re.M | re.S)
        if not via_block:
            raise RuntimeError(f"Final DEF for {tech} has no VIAS block")
        specs = {}
        for entry in re.finditer(
                r"^\s*-\s+(\S+)\s+\+ VIARULE\s+\S+\s+.*?\+\s+LAYERS\s+\S+\s+(\S+)\s+\S+",
                via_block.group("body"), re.M):
            specs[entry.group(1)] = entry.group(2)
        if not specs:
            raise RuntimeError(f"Final DEF for {tech} declares no routed vias")
    evidence = {}
    for via_name, cut_layer in specs.items():
        references = len(re.findall(rf"\b{re.escape(via_name)}\b", def_text))
        if references <= 0:
            raise RuntimeError(f"Final DEF contains no required custom via {via_name}")
        matches = [index for index in layout.layer_indexes()
                   if layout.get_info(index).name == cut_layer]
        if len(matches) != 1:
            raise RuntimeError(f"KLayout did not resolve unique cut layer {cut_layer}")
        layer_index = matches[0]
        info = layout.get_info(layer_index)
        count, area = _shape_stats(top, layer_index)
        # A DEF via reference expands into one or more physical cut shapes
        # (array vias like ROWCOL 1x5 produce multiple shapes per reference),
        # so layout count must be >= DEF references, never fewer.  The audited
        # asap7_3D single-cut vias keep the strict equality check.
        insufficient = (count != references) if tech == "asap7_3D" else (count < references)
        if insufficient or area <= 0:
            raise RuntimeError(
                f"Incomplete {via_name} geometry: DEF={references}, layout={count}, area={area}"
            )
        evidence[via_name] = {
            "def_references": references,
            "cut_layer": cut_layer,
            "gds_layer": info.layer,
            "gds_datatype": info.datatype,
            "shape_count": count,
            "area_dbu2": area,
        }
    return evidence


def _verify_streamed_vias(pya, output: Path, evidence: dict, top_cell: str) -> None:
    """Re-open the GDS and fail closed if custom via geometry was dropped."""
    streamed = pya.Layout()
    streamed.read(str(output))
    top = streamed.cell(top_cell)
    if top is None:
        raise RuntimeError(f"Streamed GDSII is missing the {top_cell} top cell")
    for via_name, record in evidence.items():
        matches = [index for index in streamed.layer_indexes()
                   if streamed.get_info(index).layer == record["gds_layer"]
                   and streamed.get_info(index).datatype == record["gds_datatype"]]
        if len(matches) != 1:
            raise RuntimeError(f"Streamed GDSII is missing the {via_name} cut layer")
        count, area = _shape_stats(top, matches[0])
        if count != record["shape_count"] or area != record["area_dbu2"]:
            raise RuntimeError(
                f"Streamed {via_name} mismatch: shapes={count}, area={area}"
            )
        record["streamed_shape_count"] = count
        record["streamed_area_dbu2"] = area
        record["verified"] = True


def render_tier_view(final_def: Path, output: Path, design: str = "gcd") -> dict:
    """Render an evidence SVG directly from final DEF placement coordinates."""
    text = final_def.read_text(encoding="utf-8", errors="replace")
    die_match = re.search(
        r"DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)",
        text,
    )
    if die_match:
        x0, y0, x1, y1 = (int(value) for value in die_match.groups())
    else:
        x0, y0, x1, y1 = 0, 0, 1, 1
    component_match = re.search(r"COMPONENTS\b.*?;(?P<body>.*?)END COMPONENTS", text, re.S)
    body = component_match.group("body") if component_match else ""
    pattern = re.compile(
        r"-\s+(\S+)\s+(\S+).*?\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)",
        re.S,
    )
    instances = [
        {"name": name, "master": master, "x": int(x), "y": int(y)}
        for name, master, x, y in pattern.findall(body)
    ]
    tiers = {
        "upper": [item for item in instances if item["master"].lower().endswith("_upper")],
        "bottom": [item for item in instances if item["master"].lower().endswith("_bottom")],
    }
    tiers["other"] = [item for item in instances if item not in tiers["upper"] and item not in tiers["bottom"]]
    width, height, panel_width, panel_height = 1200, 700, 500, 500
    colors = {"upper": "#e75d32", "bottom": "#163c51", "other": "#7b858a"}

    def point(item: dict, panel_x: int) -> tuple[float, float]:
        dx, dy = max(1, x1 - x0), max(1, y1 - y0)
        px = panel_x + 30 + (item["x"] - x0) * (panel_width - 60) / dx
        py = 105 + panel_height - 30 - (item["y"] - y0) * (panel_height - 60) / dy
        return px, py

    elements = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="700" viewBox="0 0 1200 700">',
        '<rect width="1200" height="700" fill="#f4f2ec"/>',
        f'<text x="50" y="45" font-family="monospace" font-size="24" fill="#142027">TaiWei {html.escape(design)} · final 3D tier placement</text>',
        f'<text x="50" y="72" font-family="monospace" font-size="13" fill="#68747a">Source: {html.escape(final_def.name)} · DIEAREA {x0},{y0} — {x1},{y1}</text>',
    ]
    for tier, panel_x in (("upper", 60), ("bottom", 640)):
        elements.extend([
            f'<rect x="{panel_x}" y="105" width="{panel_width}" height="{panel_height}" fill="#fffefa" stroke="#d5d0c6"/>',
            f'<text x="{panel_x}" y="635" font-family="monospace" font-size="18" fill="{colors[tier]}">{tier.upper()} TIER · {len(tiers[tier])} placed instances</text>',
        ])
        for item in tiers[tier]:
            px, py = point(item, panel_x)
            radius = 2.2 if "hbt" in item["master"].lower() else 1.4
            fill = "#267866" if "hbt" in item["master"].lower() else colors[tier]
            elements.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{radius}" fill="{fill}" opacity="0.82"/>')
    elements.append('<text x="50" y="680" font-family="monospace" font-size="12" fill="#68747a">Orange: upper · navy: bottom · green: HBT-named physical instance</text>')
    elements.append("</svg>")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(elements) + "\n", encoding="utf-8")
    return {
        "source_def": final_def.name,
        "diearea_dbu": [x0, y0, x1, y1],
        "placed_instances": len(instances),
        "upper_instances": len(tiers["upper"]),
        "bottom_instances": len(tiers["bottom"]),
        "other_instances": len(tiers["other"]),
        "hbt_named_instances": sum("hbt" in item["master"].lower() for item in instances),
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def render_gds_views(gds: Path, result_dir: Path, design: str = "gcd") -> dict:
    """Render exact 2D and sampled layer-stack 3D views with mature libraries."""
    from openroad_platform_visualization import render_gds, render_gds_3d

    two_d = result_dir / f"{design}_final_layout_2d.png"
    three_d = result_dir / f"{design}_final_gds_3d_stack.png"
    render_gds(gds, two_d, dpi=150)
    render_gds_3d(gds, three_d, dpi=150, max_layers=10)
    return {
        "two_d": two_d.name,
        "three_d": three_d.name,
        "two_d_renderer": "KLayout pya.LayoutView",
        "three_d_renderer": "KLayout pya + Matplotlib Poly3DCollection",
        "three_d_z_scale": "visual layer order; not physical process thickness",
    }
