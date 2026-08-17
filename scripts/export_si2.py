#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export platform learning observations as Si2 AI-for-EDA-Ontology-style structures.

Turns the platform's "dialect" records into a standard-ish shape aligned with
the Si2 AI for EDA Ontology concepts (net / cell / timing path / PDK /
workflow steps / tradeoffs / verification). The mapping table mirrors the
tutorial 06 comparison.

Outputs:
  var/export/si2_export.json   - one "observation record" per learning observation
  var/export/si2_field_map.md  - platform field -> Si2 concept mapping
"""
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("EXPORT_OUT", ROOT / "var" / "export"))

LIVE_DB = ROOT / "var" / "public" / "tenant-learning.db"
P14_DB = ROOT / "artifacts" / "p14-real-20260806" / "learning_observations.db"


def _rows(db: Path, table: str) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table}").fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _obs(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        payload = row.get("payload_json")
        if payload:
            try:
                data = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                data = {}
        else:
            data = dict(row)
        out.append(data)
    return out


def to_si2_style(obs: dict) -> dict:
    """Map one platform observation onto Si2-style sections."""
    ctx = obs.get("context") or {}
    metrics = obs.get("metrics") or {}
    design_id = ctx.get("design_id") or obs.get("design_id") or "unknown"
    return {
        "record_type": "si2_ai_eda_observation_v1",
        "record_id": obs.get("observation_id") or obs.get("observationId"),
        "design": {
            "design_id": design_id,
            "design_fingerprint": ctx.get("design_fingerprint"),
            "design_intent": {"clock_period_ns": metrics.get("clock_period_ns")},
        },
        "flow": {
            "flow_stage": ctx.get("flow_stage", "finish"),
            "workflow_steps": (metrics.get("stage_summary") or
                               [{"stage": ctx.get("flow_stage", "finish")}]),
            "dependencies": None,
        },
        "pdk_library": {
            "pdk_id": ctx.get("pdk_id"),
            "platform": ctx.get("platform"),
            "toolchain_id": ctx.get("toolchain_id"),
        },
        "netlist": {
            "net_count": metrics.get("net_count"),
            "cross_tier_nets": metrics.get("cross_tier_nets"),
        },
        "timing": {
            "wns_ns": metrics.get("wns_ns"),
            "tns_ns": metrics.get("tns_ns"),
            "hbt_count": metrics.get("hbt_count"),
        },
        "physical": {
            "area_um2": metrics.get("area"),
            "die_area_um2": metrics.get("die_area"),
            "core_utilization_pct": metrics.get("core_utilization_pct"),
        },
        "power": {"total_power_w": metrics.get("total_power")},
        "verification": {
            "drc_errors": metrics.get("drc_errors"),
            "drv_violations": metrics.get("drv_violations"),
            "evidence_sha256": ctx.get("artifact_fingerprint"),
        },
        "tradeoffs": {
            "objective_multi": bool(metrics),
            "note": "multi-objective BO considers area/timing/power together",
        },
        "source": "observed",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for db, tag in ((LIVE_DB, "live"), (P14_DB, "p14-historical")):
        for obs in _obs(_rows(db, "tenant_observations_v1" if tag == "live"
                              else "learning_observations_v1")):
            records.append({"source": tag, **to_si2_style(obs)})

    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "schema": "si2_ai_eda_observation_v1 (platform-native mapping; full TTL/OWL alignment pending Si2 ontology download)",
        "mapping_note": ("platform metric names map to Si2 concepts per tutorial 06; "
                         "explicit vocabulary mapping + ontology + MCP are follow-ups."),
    }
    with open(OUT / "si2_export.json", "w", encoding="utf-8") as f:
        json.dump({"manifest": manifest, "records": records}, f,
                  ensure_ascii=False, indent=2, default=str)
    print(f"Si2-style export: {OUT / 'si2_export.json'} ({len(records)} records)")

    field_map = """# 平台字段 ↔ Si2 AI for EDA Ontology 概念映射（v1）

| 平台字段（观测） | Si2 概念 | 说明 |
| --- | --- | --- |
| context.design_id / design_fingerprint | Design / Design Intent | 设计身份与指纹 |
| context.platform / pdk_id | PDK & Library | 工艺/库 |
| context.toolchain_id | Toolchain | 工具链版本快照 |
| context.flow_stage | Workflow Steps | 流程阶段 |
| metrics.wns_ns / tns_ns / hbt_count | Timing Path | 时序 |
| metrics.net_count / cross_tier_nets | Net | 网/跨层网络（3D） |
| metrics.area / die_area / core_utilization_pct | Physical Design Metrics | 面积/利用率 |
| metrics.drc_errors / drv_violations | Verification / Signoff | DRC/DRV 验证 |
| artifact fingerprint / sha256 | Evidence | 证据链 |
| multi-objective BO | Tradeoffs | 权衡优化 |

> 待办：① 指标名→Si2 词汇映射 JSON；② 下载 Si2 本体（TTL/OWL）做机器可读对齐；③ MCP 发现接口。
"""
    with open(OUT / "si2_field_map.md", "w", encoding="utf-8") as f:
        f.write(field_map)
    print(f"Field map: {OUT / 'si2_field_map.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
