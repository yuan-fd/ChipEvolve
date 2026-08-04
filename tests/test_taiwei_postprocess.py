from __future__ import annotations

from pathlib import Path

import pytest

from openroad_platform_execution.taiwei_postprocess import stream_out_gds


TECH_LEF = """VERSION 5.8 ;
BUSBITCHARS "[]" ;
DIVIDERCHAR "/" ;
UNITS DATABASE MICRONS 1000 ; END UNITS
MANUFACTURINGGRID 0.001 ;
LAYER M1_m
  TYPE ROUTING ; DIRECTION VERTICAL ; PITCH 0.10 ; WIDTH 0.04 ;
END M1_m
LAYER V1_add
  TYPE CUT ; SPACING 0.04 ; WIDTH 0.04 ;
END V1_add
LAYER M2_add
  TYPE ROUTING ; DIRECTION HORIZONTAL ; PITCH 0.10 ; WIDTH 0.04 ;
END M2_add
LAYER V2_add
  TYPE CUT ; SPACING 0.04 ; WIDTH 0.04 ;
END V2_add
LAYER M3_add
  TYPE ROUTING ; DIRECTION VERTICAL ; PITCH 0.10 ; WIDTH 0.04 ;
END M3_add
VIA VIA_M1m_M2add DEFAULT
  LAYER M1_m ; RECT -0.02 -0.02 0.02 0.02 ;
  LAYER V1_add ; RECT -0.01 -0.01 0.01 0.01 ;
  LAYER M2_add ; RECT -0.02 -0.02 0.02 0.02 ;
END VIA_M1m_M2add
VIA VIA_M2add_M3add DEFAULT
  LAYER M2_add ; RECT -0.02 -0.02 0.02 0.02 ;
  LAYER V2_add ; RECT -0.01 -0.01 0.01 0.01 ;
  LAYER M3_add ; RECT -0.02 -0.02 0.02 0.02 ;
END VIA_M2add_M3add
END LIBRARY
"""

FINAL_DEF = """VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN gcd ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
COMPONENTS 0 ;
END COMPONENTS
PINS 0 ;
END PINS
NETS 1 ;
- n0
  + ROUTED M1_m ( 1000 1000 ) VIA_M1m_M2add
    NEW M2_add ( 1000 1000 ) ( 2000 1000 ) VIA_M2add_M3add ;
END NETS
END DESIGN
"""


def test_stream_out_proves_custom_vias_survive_gds(tmp_path: Path):
    pytest.importorskip("pya")
    staged = tmp_path / "taiwei-source"
    platform = staged / "platforms/asap7_3D"
    tech = platform / "lef/asap7_tech_1x_2A6M7M.lef"
    bottom = platform / "lef_bottom/asap7sc7p5t_28_R_1x_220121a.bottom.lef"
    upper = platform / "lef_upper/asap7sc7p5t_28_R_1x_220121a.upper.lef"
    for path, content in ((tech, TECH_LEF), (bottom, "END LIBRARY\n"),
                          (upper, "END LIBRARY\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    final_def = staged / "results/asap7_3D/gcd/test/6_final.def"
    final_def.parent.mkdir(parents=True)
    final_def.write_text(FINAL_DEF, encoding="utf-8")

    result = stream_out_gds(staged, final_def, final_def.with_suffix(".gds"))

    evidence = result["custom_via_geometry"]
    assert evidence["VIA_M1m_M2add"]["def_references"] == 1
    assert evidence["VIA_M2add_M3add"]["def_references"] == 1
    assert all(record["verified"] for record in evidence.values())
    assert result["lef_files"][0].endswith("asap7_tech_1x_2A6M7M.lef")
