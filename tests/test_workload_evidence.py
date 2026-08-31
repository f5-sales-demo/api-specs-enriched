import json
import zipfile
from pathlib import Path

import pytest

from scripts.workload_evidence import (
    build_evidence,
    enrich_profile,
    evaluate_group,
    normalized_pytest_outcomes,
    select_candidate,
)


def test_tree_and_archive_digests_are_deterministic(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "b.json").write_text('{"b":2}\n')
    (tree / "a.json").write_text('{"a":1}\n')
    first = build_evidence(tree=tree, archive=tmp_path / "first.zip")
    second = build_evidence(tree=tree, archive=tmp_path / "second.zip")
    assert first["generated_tree"]["digest"] == second["generated_tree"]["digest"]
    assert first["archive"]["digest"] == second["archive"]["digest"]
    with zipfile.ZipFile(tmp_path / "first.zip") as archive:
        assert archive.namelist() == ["a.json", "b.json"]


def test_pytest_digest_ignores_runtime_and_order(tmp_path: Path) -> None:
    one = tmp_path / "one.xml"
    two = tmp_path / "two.xml"
    one.write_text(
        '<testsuite><testcase classname="c" name="b" time="8"/><testcase classname="c" name="a"><skipped/></testcase></testsuite>'
    )
    two.write_text(
        '<testsuite time="99"><testcase classname="c" name="a" time="2"><skipped/></testcase><testcase classname="c" name="b"/></testsuite>'
    )
    assert normalized_pytest_outcomes(one) == normalized_pytest_outcomes(two)
    assert (
        build_evidence(pytest_xml=one)["output_digest"]
        == build_evidence(pytest_xml=two)["output_digest"]
    )


def test_profile_enrichment_merges_checkpoint_durations(tmp_path: Path) -> None:
    profile = tmp_path / "profile.json"
    evidence = tmp_path / "evidence.json"
    memory = tmp_path / "memory.json"
    profile.write_text(
        json.dumps({"schema_version": 1, "phase_timings": [], "output_digest": None})
    )
    evidence.write_text(json.dumps({"schema_version": 1, "output_digest": "sha256:abc"}))
    memory.write_text(
        json.dumps({"checkpoints": [{"name": "load", "phase_duration_seconds": 1.25}]})
    )
    enrich_profile(profile, evidence, memory)
    value = json.loads(profile.read_text())
    assert value["output_digest"] == "sha256:abc"
    assert value["phase_timings"] == [{"name": "load", "duration_seconds": 1.25}]

    memory.write_text(json.dumps({"checkpoints": [{"name": "legacy"}]}))
    with pytest.raises(KeyError, match="phase_duration_seconds"):
        enrich_profile(profile, evidence, memory)


def profile(pair: int, duration: float, digest: str = "same") -> dict:
    return {
        "pair_id": str(pair),
        "duration_seconds": duration,
        "output_digest": digest,
        "memory": {"peak_limit_ratio": 0.5, "events": {"oom": 0, "oom_kill": 0}},
        "exit": {"code": 0},
    }


def test_gate_requires_all_acceptance_conditions() -> None:
    baseline = [profile(pair, 10 + pair) for pair in range(1, 6)]
    candidate = [profile(pair, (10 + pair) * 0.7) for pair in range(1, 6)]
    assert evaluate_group(baseline, candidate)["qualifies"]
    candidate[-1]["output_digest"] = "different"
    assert not evaluate_group(baseline, candidate)["qualifies"]
    candidate[-1]["output_digest"] = "same"
    candidate[-1]["memory"]["events"]["oom_kill"] = 1
    assert not evaluate_group(baseline, candidate)["qualifies"]


def test_selection_prefers_d8_within_five_percent_then_fewer_workers() -> None:
    values = [
        {"variant": "d16-w8", "candidate_p95_seconds": 100.0, "qualifies": True},
        {"variant": "d8-w4", "candidate_p95_seconds": 104.9, "qualifies": True},
        {"variant": "d8-w2", "candidate_p95_seconds": 104.0, "qualifies": True},
    ]
    selected = select_candidate(values)
    assert selected is not None
    assert selected["variant"] == "d8-w2"


def test_malformed_profile_is_rejected(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    evidence = tmp_path / "evidence.json"
    profile_path.write_text('{"schema_version":2}')
    evidence.write_text('{"schema_version":1,"output_digest":"x"}')
    with pytest.raises(ValueError, match="unsupported"):
        enrich_profile(profile_path, evidence, None)
