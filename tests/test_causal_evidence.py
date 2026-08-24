from openroad_platform_analysis import factorial_interaction_report, validate_holdout_interaction
from openroad_platform_analysis import followup_from_interaction, teacher_context_from_holdout

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
