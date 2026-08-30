from pathlib import Path

import yaml

WORKFLOW_PATH = Path(".github/workflows/workload-benchmark.yml")


def workflow() -> dict:
    """Load the benchmark workflow without YAML 1.1 coercing the on key."""
    value = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    value["on"] = value.pop(True)
    return value


def test_pull_request_benchmark_is_label_and_same_repository_gated() -> None:
    value = workflow()
    assert "pull_request" in value["on"]
    authorize = value["jobs"]["authorize"]
    script = authorize["steps"][0]["run"]
    assert authorize["permissions"] == {}
    assert "HEAD_REPOSITORY" in script
    assert "BASE_REPOSITORY" in script
    assert "autoresearch" in script
    assert value["jobs"]["seed"]["if"] == "needs.authorize.outputs.approved == 'true'"
    assert value["permissions"] == {"contents": "read"}


def test_runner_classes_are_serialized_independently() -> None:
    jobs = workflow()["jobs"]
    assert jobs["benchmark-d8"]["strategy"]["max-parallel"] == 1
    assert jobs["benchmark-d16"]["strategy"]["max-parallel"] == 1
    assert jobs["benchmark-d8"]["runs-on"] == "managed-socketless"
    assert jobs["benchmark-d16"]["runs-on"] == "api-specs-enriched-compute"


def test_cprofile_is_single_process_and_retained_for_seven_days() -> None:
    for name in ("manual-d8", "manual-d16"):
        steps = workflow()["jobs"][name]["steps"]
        capture = next(step for step in steps if step["name"] == "Capture single-process cProfile")
        upload = next(step for step in steps if step["name"] == "Upload manual cProfile data")
        assert "--workers 1" in capture["run"]
        assert upload["with"]["retention-days"] == 7
