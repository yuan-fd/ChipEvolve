from openroad_platform_analysis.parsers.opensta_timing import parse_opensta_paths


def test_opensta_parser_extracts_only_labelled_paths_and_accounts_for_loss(tmp_path):
    report = tmp_path / "setup_timing.rpt"
    report.write_text(
        """Startpoint: u_reg/Q
Endpoint: out
Path Type: max
data arrival time 1.250
slack (VIOLATED) -0.125

Startpoint: incomplete/Q
this block has no endpoint or slack

Startpoint: in
Endpoint: u_reg/D
Path Group: clk
slack (MET) 0.040
""", encoding="utf-8")
    parsed = parse_opensta_paths(report)
    assert parsed["total_startpoint_blocks"] == 3
    assert parsed["unparsed_blocks"] == 1
    assert [item["slack_ns"] for item in parsed["paths"]] == [-0.125, 0.04]
    assert parsed["paths"][0]["startpoint"] == "u_reg/Q"
    assert parsed["paths"][0]["delay_ns"] == 1.25


def test_opensta_parser_accepts_native_orfs_value_before_label(tmp_path):
    report = tmp_path / "6_finish.rpt"
    report.write_text(
        """Startpoint: in_reg/Q
Endpoint: out_reg/D
Path Type: max
  3.22   data arrival time
  6.81   slack (MET)
""", encoding="utf-8")
    parsed = parse_opensta_paths(report)
    assert parsed["unparsed_blocks"] == 0
    assert parsed["paths"][0]["delay_ns"] == 3.22
    assert parsed["paths"][0]["slack_ns"] == 6.81
