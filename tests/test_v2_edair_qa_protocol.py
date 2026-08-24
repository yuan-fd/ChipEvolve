import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_v2_paper_edair_qa.py"
SPEC = importlib.util.spec_from_file_location("edair_qa", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


def test_edair_qa_protocol_and_exact_judge_are_frozen():
    protocol = json.loads((ROOT / "experiments/v2-paper-20260825/edair-protocol.json").read_text())
    assert protocol["questions_per_design"] == 12
    assert protocol["repetitions"] == 5
    assert protocol["arms"] == ["kpi_only", "typed_edair"]
    assert MODULE.judge(1.0, 1.0000001)
    assert not MODULE.judge(1.0, "1.0")
    assert MODULE.judge("pin/A", "pin/A")


def test_question_builder_uses_typed_objects_not_hidden_answers():
    export = json.loads((ROOT / "artifacts/v2-real-bo-suite-seed20260826/gcd/edair-7aa3a35ec2ed41e0a0d469b83aa25b5d.json").read_text())
    questions = MODULE.build_questions(export)
    assert len(questions) == 12
    assert questions[0]["answer"] == 6.814
    assert questions[4]["answer"] == 5
    assert MODULE.kpi_context(export).keys() == {"schema_version", "kind", "kpi", "notice"}


def test_harness_requires_all_answers_and_cleans_up_process_group():
    source = SCRIPT.read_text()
    assert "The answers object must contain all 12 IDs" in source
    assert 'output / "harness-preflight.json"' in source
    assert "os.killpg(process.pid, signal.SIGTERM)" in source
