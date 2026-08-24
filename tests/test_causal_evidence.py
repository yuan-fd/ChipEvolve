from openroad_platform_analysis import factorial_interaction_report, validate_holdout_interaction
from openroad_platform_analysis import followup_from_interaction, teacher_context_from_holdout
from openroad_platform_analysis import EvidenceRAG, HypothesisLedger, reflection_hypothesis
from openroad_platform_contracts import LearningContext
from apps.api.app import ApiState
from types import SimpleNamespace
import pytest

def _view(util, density, value, replica, rtl="a" * 64, top="dut"):
    return {"run":{"status":"succeeded","task_spec":{"task_id":f"task-{util}-{density}-{replica}","design_id":"design","plugin_id":"orfs","inputs":{"rtl":{"sha256":rtl},"top":top},"parameters":{"platform":"sky130hd","target_stage":"finish","core_utilization_pct":util,"place_density":density},"resources":{"toolchain_profile":"pinned"},"labels":{"replica_index":str(replica)}}},"stages":[{"attempts":[{"metrics":[{"name":"area","value":value}]}]}]}

def test_repeated_factorial_exposes_a_parameter_interaction():
    # density is harmful only at high utilization: interaction = 20.
    views=[]
    for util,density,value in ((20,.4,100),(20,.6,100),(40,.4,110),(40,.6,130)):
        views.extend([_view(util,density,value,0),_view(util,density,value,1)])
    report=factorial_interaction_report(views,first="core_utilization_pct",second="place_density",metric="area")
    assert report["causal_eligible"] is True
    assert report["interaction_effect"] == 20
    assert "local" in report["claim"]


def test_interaction_becomes_a_falsifiable_holdout_study_not_an_auto_action():
    views=[]
    for util,density,value in ((20,.4,100),(20,.6,100),(40,.4,110),(40,.6,130)):
        views.extend([_view(util,density,value,0),_view(util,density,value,1)])
    report=factorial_interaction_report(views,first="core_utilization_pct",second="place_density",metric="area")
    followup=followup_from_interaction(report,first="core_utilization_pct",second="place_density",metric="area")
    assert followup["learning_eligible"] is True
    assert followup["hypothesis_kind"] == "interaction"
    assert followup["next_study"]["minimum_runs"] == 8
    assert followup["execution_allowed"] is False


def test_holdout_requires_new_design_but_same_non_design_context():
    def experiment(values, rtl, top):
        views = []
        for util, density, value in values:
            views.extend([_view(util, density, value, 0, rtl, top), _view(util, density, value, 1, rtl, top)])
        return factorial_interaction_report(views, first="core_utilization_pct", second="place_density", metric="area")
    source = experiment(((20, .4, 100), (20, .6, 100), (40, .4, 110), (40, .6, 130)), "a" * 64, "gcd")
    holdout = experiment(((20, .4, 80), (20, .6, 80), (40, .4, 90), (40, .6, 100)), "b" * 64, "ibex_alu")
    result = validate_holdout_interaction(source, holdout, first="core_utilization_pct", second="place_density", metric="area")
    assert result["eligible"] is True
    assert result["outcome"] == "validated"
    assert result["execution_allowed"] is False


def test_validated_compound_condition_becomes_teacher_context_not_auto_action():
    def experiment(values, rtl):
        views = []
        for util, density, value in values:
            views.extend([_view(util, density, value, 0, rtl), _view(util, density, value, 1, rtl)])
        return factorial_interaction_report(views, first="core_utilization_pct", second="place_density", metric="area")
    source = experiment(((20, .4, 100), (20, .6, 100), (40, .4, 110), (40, .6, 130)), "a" * 64)
    holdout = experiment(((20, .4, 80), (20, .6, 80), (40, .4, 90), (40, .6, 100)), "b" * 64)
    validation = validate_holdout_interaction(source, holdout, first="core_utilization_pct", second="place_density", metric="area")
    brief = teacher_context_from_holdout(source, holdout, validation, first="core_utilization_pct", second="place_density", metric="area")
    assert brief["evidence_class"] == "replicated_compound_condition"
    assert brief["compound_condition"]["source_interaction"] == 20
    assert brief["execution_allowed"] is False


def test_holdout_promotion_writes_a_traceable_card_and_retrievable_rule(tmp_path):
    source_views, holdout_views, mapping = [], [], {}
    index = 0
    for rtl, top, values, target in (
        ("a" * 64, "gcd", ((20,.4,100),(20,.6,100),(40,.4,110),(40,.6,130)), source_views),
        ("b" * 64, "ibex", ((20,.4,80),(20,.6,80),(40,.4,90),(40,.6,100)), holdout_views),
    ):
        for util, density, value in values:
            for replica in range(2):
                view = _view(util, density, value, replica, rtl, top)
                run_id = f"run-{index}"; index += 1
                view["run"]["run_id"] = run_id
                mapping[run_id] = view
                target.append(run_id)
    ledger = HypothesisLedger(tmp_path / "hypotheses.db")
    hypothesis = reflection_hypothesis(
        claim="utilization and density have a coupled area effect",
        mechanism="routing whitespace changes the marginal packing response",
        context={"design": "gcd"}, producer="diagnosis",
        proposed_intervention={"kind": "2x2"},
        evidence_refs=[{"ref": "run:run-0", "sha256": "c" * 64}])
    ledger.append(hypothesis)
    context = LearningContext(
        design_id="design", design_fingerprint="a" * 64, platform="sky130hd",
        pdk_id="sky130hd", toolchain_id="orfs-1", flow_stage="finish",
        metric_parser_version="web-evidence-v1")
    rag = EvidenceRAG(tmp_path / "rag.db")
    dummy = SimpleNamespace(
        hypothesis_ledger=ledger,
        runtime_store=SimpleNamespace(get_run=lambda _run_id: SimpleNamespace()),
        get_runtime_run=lambda run_id, **_kwargs: mapping[run_id],
        _learning_context_for_run=lambda _run: context,
        _evidence_rag_for_owner=lambda _owner: rag,
    )
    result = ApiState.validate_causal_holdout(dummy, {
        "source_run_ids": source_views, "holdout_run_ids": holdout_views,
        "first_parameter": "core_utilization_pct",
        "second_parameter": "place_density", "metric": "area",
        "hypothesis_id": hypothesis["hypothesis_id"],
        "expected_direction": "max",
    })
    assert result["promotion"]["promoted"] is True
    assert result["knowledge_card"]["status"] == "validated"
    assert result["knowledge_card"]["action_eligible"] is True
    assert result["knowledge_card"]["rag_record_id"].startswith("knowledge-v2-")
    bundle = rag.retrieve("utilization density interaction", context,
                          action_eligible_only=True)
    assert bundle.records[0]["knowledge_type"] == "validated_rule"
    assert len(ledger.history(hypothesis["hypothesis_id"])) == 3

    contradicting_ids = []
    for util, density, value in ((20,.4,80),(20,.6,100),(40,.4,100),(40,.6,80)):
        for replica in range(2):
            view = _view(util, density, value, replica, "c" * 64, "uart")
            run_id = f"run-{index}"; index += 1
            view["run"]["run_id"] = run_id
            mapping[run_id] = view
            contradicting_ids.append(run_id)
    retired = ApiState.validate_causal_holdout(dummy, {
        "source_run_ids": source_views, "holdout_run_ids": contradicting_ids,
        "first_parameter": "core_utilization_pct",
        "second_parameter": "place_density", "metric": "area",
        "hypothesis_id": hypothesis["hypothesis_id"],
        "expected_direction": "max",
    })
    assert retired["validation"]["outcome"] == "rejected"
    assert retired["knowledge_card"]["status"] == "retired"
    assert retired["knowledge_card"]["action_eligible"] is False
    assert retired["knowledge_card"]["retired_rag_record_ids"] == [
        result["knowledge_card"]["rag_record_id"]
    ]
    assert rag.retrieve(
        "utilization density interaction", context, action_eligible_only=True
    ).records == ()
    with pytest.raises(ValueError, match="missing or stale"):
        rag.replay(bundle, context)
