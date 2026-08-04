"""Deterministic post-processing for a completed TaiWei ORD 3D run."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path


def stream_out_gds(staged: Path, final_def: Path, output: Path) -> dict:
    """Convert the routed DEF to a real GDSII stream with the pinned 3D LEFs."""
    import pya  # KLayout's Python API; imported lazily for fixture compatibility.

    platform = staged / "platforms" / "asap7_3D"
    lefs = [
        # This is the ORD technology LEF selected by asap7_3D/config.mk.  The
        # 6M7M LEF is the non-ORD fallback and does not define M2_add/M3_add.
        platform / "lef" / "asap7_tech_1x_2A6M7M.lef",
        platform / "lef_bottom" / "asap7sc7p5t_28_R_1x_220121a.bottom.lef",
        platform / "lef_upper" / "asap7sc7p5t_28_R_1x_220121a.upper.lef",
    ]
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
    top = layout.cell("gcd")
    if top is None:
        raise RuntimeError("KLayout did not create the expected gcd top cell")
    via_geometry = _verify_custom_vias(layout, top, final_def)
    output.parent.mkdir(parents=True, exist_ok=True)
    layout.write(str(output))
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("KLayout produced an empty GDSII stream")
    _verify_streamed_vias(pya, output, via_geometry)
    return {
        "producer": "KLayout pya",
        "producer_version": getattr(pya, "__version__", "unknown"),
        "source_def": str(final_def.relative_to(staged)),
        "lef_files": [str(path.relative_to(staged)) for path in lefs],
        "top_cell": "gcd",
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


def _verify_custom_vias(layout, top, final_def: Path) -> dict:
    """Prove that every custom cross-tier DEF via became LEF geometry."""
    def_text = final_def.read_text(encoding="utf-8", errors="replace")
    specs = {
        "VIA_M1m_M2add": "V1_add",
        "VIA_M2add_M3add": "V2_add",
    }
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
        if count != references or area <= 0:
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


def _verify_streamed_vias(pya, output: Path, evidence: dict) -> None:
    """Re-open the GDS and fail closed if custom via geometry was dropped."""
    streamed = pya.Layout()
    streamed.read(str(output))
    top = streamed.cell("gcd")
    if top is None:
        raise RuntimeError("Streamed GDSII is missing the gcd top cell")
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


def render_tier_view(final_def: Path, output: Path) -> dict:
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
        '<text x="50" y="45" font-family="monospace" font-size="24" fill="#142027">TaiWei gcd · final 3D tier placement</text>',
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
