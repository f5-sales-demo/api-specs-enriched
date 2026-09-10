"""Contract tests for workload-profile artifacts (issue #1648)."""

import json
import sys
from pathlib import Path

from scripts.workload_profile import (
    SCHEMA_VERSION,
    canonical_json,
    profile_module,
    run_workload,
    tree_digest,
)


def test_canonical_json_is_stable() -> None:
    assert canonical_json({"b": 1, "a": 2}) == b'{"a":2,"b":1}\n'


def test_tree_digest_is_order_independent(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first")
    second.write_text("second")
    assert tree_digest([second, first]) == tree_digest([first, second])


def test_run_workload_writes_versioned_phase_evidence(tmp_path: Path) -> None:
    output = tmp_path / "profile.json"
    assert run_workload("pytest", [sys.executable, "-c", "pass"], output) == 0
    value = json.loads(output.read_text())
    assert value["schema_version"] == SCHEMA_VERSION
    assert value["phase"] == "pytest"
    assert value["exit"] == {"code": 0}
    assert value["identity_digest"].startswith("sha256:")


def test_profile_module_writes_stats_and_evidence(tmp_path: Path) -> None:
    output = tmp_path / "profile.json"
    stats = tmp_path / "profile.pstats"
    assert profile_module("scripts.stamp_release_version", ["--help"], output, stats) == 0
    assert stats.is_file()
    assert json.loads(output.read_text())["profile_stats"] == stats.as_posix()
