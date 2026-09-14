"""The OpenSTA path indexer.

The parser's contract is that it is an *index*, not a summary: it says how much
of the report it did not capture, and it never invents a value for a line it did
not understand.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from opensta import (
    DEFAULT_MAX_PATHS,
    MAX_PATHS_CEILING,
    PARSER_ID,
    parse_opensta_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "plugins" / "edair" / "adapter.py"

#: A report with two complete paths, one block missing its slack line, and
#: assorted unlabelled noise between them.
REPORT = """\
OpenSTA 2.5.0 timing report

Startpoint: u_core/reg_a
Endpoint: u_core/reg_b
Path Type: max
  data arrival time        12.345
  slack (VIOLATED)        -0.155

some unlabelled prose that is not part of any block

Startpoint: u_core/reg_c
Endpoint: u_core/reg_d
Path Group: min
  0.032 slack
  3.5 data arrival time

Startpoint: u_core/reg_e
Endpoint: u_core/reg_f
Path Type: max

Startpoint: u_core/reg_g
Endpoint: u_core/reg_h
Path Type: max
  slack 0.5

Startpoint: u_core/reg_i
Endpoint: u_core/reg_j
  slack 0.25
"""


@pytest.fixture()
def report(tmp_path: Path) -> Path:
    path = tmp_path / "6_finish.rpt"
    path.write_text(REPORT, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the parser
# --------------------------------------------------------------------------

def test_labelled_blocks_become_rows(report: Path):
    index = parse_opensta_paths(report)
    assert index["parser"] == PARSER_ID
    assert [row["startpoint"] for row in index["paths"]] == [
        "u_core/reg_a", "u_core/reg_c", "u_core/reg_g", "u_core/reg_i",
    ]


def test_both_slack_forms_are_accepted(report: Path):
    """OpenSTA prints slack before or after the number depending on version and
    verbosity.  Both are read; neither is inferred from position."""
    index = parse_opensta_paths(report)
    by_start = {row["startpoint"]: row for row in index["paths"]}
    assert by_start["u_core/reg_a"]["slack_ns"] == pytest.approx(-0.155)
    assert by_start["u_core/reg_c"]["slack_ns"] == pytest.approx(0.032)
    assert by_start["u_core/reg_g"]["slack_ns"] == pytest.approx(0.5)
    assert by_start["u_core/reg_i"]["slack_ns"] == pytest.approx(0.25)


def test_the_path_type_is_read_and_lowercased(report: Path):
    by_start = {row["startpoint"]: row
                for row in parse_opensta_paths(report)["paths"]}
    assert by_start["u_core/reg_a"]["path_type"] == "max"
    assert by_start["u_core/reg_c"]["path_type"] == "min"


def test_a_block_without_a_path_type_defaults_to_setup(report: Path):
    """A report that omits the line is read as a setup path, which is what an
    unlabelled path in an OpenSTA report is."""
    by_start = {row["startpoint"]: row
                for row in parse_opensta_paths(report)["paths"]}
    assert by_start["u_core/reg_i"]["path_type"] == "setup"


def test_both_data_arrival_forms_are_accepted(report: Path):
    by_start = {row["startpoint"]: row
                for row in parse_opensta_paths(report)["paths"]}
    assert by_start["u_core/reg_a"]["delay_ns"] == pytest.approx(12.345)
    assert by_start["u_core/reg_c"]["delay_ns"] == pytest.approx(3.5)


def test_a_block_without_a_delay_reports_none_not_zero(report: Path):
    """A missing measurement and a measured zero are indistinguishable once a
    default has been invented."""
    by_start = {row["startpoint"]: row
                for row in parse_opensta_paths(report)["paths"]}
    assert by_start["u_core/reg_g"]["delay_ns"] is None
    assert by_start["u_core/reg_i"]["delay_ns"] is None


def test_a_block_missing_its_slack_is_counted_not_emitted(report: Path):
    """reg_e has start and end but no slack line."""
    index = parse_opensta_paths(report)
    assert "u_core/reg_e" not in {row["startpoint"] for row in index["paths"]}
    assert index["unparsed_blocks"] == 1


def test_the_index_reports_how_many_blocks_existed(report: Path):
    index = parse_opensta_paths(report)
    assert index["total_startpoint_blocks"] == 5
    assert len(index["paths"]) == 4


def test_truncation_is_declared(report: Path):
    """A 400-path report read with a cap of 2 is not a report with 2 paths."""
    index = parse_opensta_paths(report, max_paths=2)
    assert len(index["paths"]) == 2
    assert index["truncated"] is True
    assert index["loss_manifest"]["truncated_at"] == 2


def test_an_uncut_report_is_not_marked_truncated(report: Path):
    index = parse_opensta_paths(report)
    assert index["truncated"] is False
    assert index["loss_manifest"]["truncated_at"] is None


def test_the_loss_manifest_states_what_was_not_represented(report: Path):
    """A derived view is an index with references, never a replacement."""
    manifest = parse_opensta_paths(report)["loss_manifest"]
    assert "path_points" in manifest
    assert "unlabelled_lines" in manifest


def test_point_detail_is_present_and_empty_rather_than_absent(report: Path):
    """An empty list says "defined and unsupported"; a missing key says nothing."""
    row = parse_opensta_paths(report)["paths"][0]
    assert row["points"] == []


def test_path_ids_identify_the_block_not_the_row(report: Path):
    """The id is the block's position in the report, so a skipped block does not
    renumber the rows after it.  A row-sequential id would shift whenever the
    parser learned to read one more block, and every reference to a path would
    silently point at a different one."""
    ids = [row["path_id"] for row in parse_opensta_paths(report)["paths"]]
    assert ids == ["path-0", "path-1", "path-3", "path-4"]


# --------------------------------------------------------------------------
# inputs it refuses
# --------------------------------------------------------------------------

def test_a_missing_file_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="invalid"):
        parse_opensta_paths(tmp_path / "absent.rpt")


def test_an_invalid_cap_is_refused(report: Path):
    for bad in (0, -1, MAX_PATHS_CEILING + 1):
        with pytest.raises(ValueError, match="max_paths"):
            parse_opensta_paths(report, max_paths=bad)


def test_the_ceiling_is_what_the_parser_claims():
    assert DEFAULT_MAX_PATHS <= MAX_PATHS_CEILING


def test_a_report_with_no_labelled_blocks_yields_an_empty_index(tmp_path: Path):
    path = tmp_path / "empty.rpt"
    path.write_text("just prose\nand more prose\n", encoding="utf-8")
    index = parse_opensta_paths(path)
    assert index["paths"] == []
    assert index["total_startpoint_blocks"] == 0
    assert index["unparsed_blocks"] == 0


def test_an_exponent_literal_is_read(tmp_path: Path):
    path = tmp_path / "exp.rpt"
    path.write_text(
        "Startpoint: a\nEndpoint: b\nPath Type: max\nslack 1.5e-2\n",
        encoding="utf-8",
    )
    assert parse_opensta_paths(path)["paths"][0]["slack_ns"] == pytest.approx(0.015)


# --------------------------------------------------------------------------
# the adapter, as a process
# --------------------------------------------------------------------------

def run_adapter(tmp_path: Path, *, inputs: dict, prepare=True) -> tuple[dict, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    if prepare:
        (workspace / "6_finish.rpt").write_text(REPORT, encoding="utf-8")
    request = workspace / "adapter_request.json"
    result = workspace / "adapter_result.json"
    request.write_text(json.dumps({
        "schema_version": 1,
        "plugin": {"plugin_id": "edair", "plugin_version": "1.0.0"},
        "task": {"schema_version": 3, "task_id": "t", "project_id": "p",
                 "design_id": "d", "plugin_id": "edair", "inputs": inputs},
    }), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(ADAPTER), "--request", str(request),
         "--result", str(result)],
        cwd=str(workspace), capture_output=True, text=True, timeout=60,
    )
    assert result.is_file(), completed.stderr
    return json.loads(result.read_text(encoding="utf-8")), workspace


def test_the_adapter_produces_an_index_and_keeps_the_raw_report(tmp_path):
    result, workspace = run_adapter(
        tmp_path, inputs={"timing_report": "6_finish.rpt"})
    assert result["status"] == "succeeded"
    kinds = {a["path"]: a["kind"] for a in result["artifacts"]}
    # The index...
    assert kinds["timing_paths.index.json"] == "report"
    # ...and its source.  An index whose source is not kept cannot be checked.
    assert kinds["6_finish.rpt"] == "log"

    index = json.loads(
        (workspace / "timing_paths.index.json").read_text(encoding="utf-8")
    )
    assert len(index["paths"]) == 4


def test_the_adapter_reports_progress_without_naming_a_stage_to_the_platform(
    tmp_path,
):
    result, _ = run_adapter(tmp_path, inputs={"timing_report": "6_finish.rpt"})
    assert result["status"] == "succeeded"


def test_the_adapter_passes_the_cap_through(tmp_path):
    _, workspace = run_adapter(
        tmp_path, inputs={"timing_report": "6_finish.rpt", "max_paths": 1})
    index = json.loads(
        (workspace / "timing_paths.index.json").read_text(encoding="utf-8")
    )
    assert len(index["paths"]) == 1
    assert index["truncated"] is True


def test_the_adapter_refuses_a_path_outside_the_workspace(tmp_path):
    result, _ = run_adapter(
        tmp_path, inputs={"timing_report": "../../etc/hosts"})
    assert result["status"] == "failed"
    assert result["failure"]["category"] == "invalid_input"
    assert "escapes the workspace" in result["failure"]["message"]


def test_the_adapter_reports_a_missing_report_without_inventing_one(tmp_path):
    result, _ = run_adapter(
        tmp_path, inputs={"timing_report": "not-there.rpt"}, prepare=False)
    assert result["status"] == "failed"
    assert result["failure"]["category"] == "missing_input"
    assert result["failure"]["retryable"] is False


def test_the_adapter_requires_the_input(tmp_path):
    result, _ = run_adapter(tmp_path, inputs={})
    assert result["status"] == "failed"
    assert result["failure"]["category"] == "invalid_input"


def test_a_failed_adapter_declares_no_artifacts(tmp_path):
    result, _ = run_adapter(
        tmp_path, inputs={"timing_report": "not-there.rpt"}, prepare=False)
    assert result["artifacts"] == []
