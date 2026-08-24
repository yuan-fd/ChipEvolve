from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_causal_runner_freezes_and_pairs_real_openroad_seeds():
    runner = (ROOT / "scripts/run_v2_causal_holdout.py").read_text(encoding="utf-8")
    audit = (ROOT / "scripts/audit_v2_causal_holdout.py").read_text(encoding="utf-8")
    assert "paired_replica_seeds(args.seed, args.repetitions)" in runner
    assert "or_seed=replica_or_seeds[replica]" in runner
    assert '"paired_replica_or_seeds"' in runner
    assert '"paired_distinct_openroad_seeds"' in audit
    assert "all(values == seed_sets[0]" in audit
