# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for the dependency-free downstream repository dispatch helper."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "release" / "dispatch-downstream.sh"
_WORKFLOW = _ROOT / ".github" / "workflows" / "sync-and-enrich.yml"
_REQUIRED_ENV = {
    "GH_TOKEN": "test-token",
    "TARGET_OWNER": "f5-sales-demo",
    "TARGET_REPO": "xcsh",
    "EVENT_TYPE": "enriched-specs-updated",
    "VERSION": "2.1.220",
    "SOURCE_REPOSITORY": "f5-sales-demo/api-specs-enriched",
    "SOURCE_TARGET_COMMIT": "b3130b919b421a60b3f527c1cea686aebdd59bc3",
}

_EXPECTED_DELIVERIES = [
    (
        "xcsh",
        "enriched-specs-updated",
        "c49ba43a076c3d2ed9ba1145807988c7b5dd60630e590199ddc392a5752c0933",
    ),
    (
        "vscode-xcsh",
        "enriched-specs-updated",
        "4365baedb6569423277a419da3b91a63f7bd6923541fcb0c118fb1e770e43679",
    ),
    (
        "terraform-provider-xcsh",
        "enriched-specs-updated",
        "efd8880c11be1d07c73da28125adbe15dc0ec0c97d8aeed93bcf462dffbc27db",
    ),
    (
        "console",
        "upstream-enrichment-changed",
        "255c767bcbc7bb5bd8921760cbf9699f2c14d294cd2bcba11a7f87deba6dfa0f",
    ),
]


def _fake_gh(tmp_path: Path) -> tuple[Path, Path, Path]:
    args_path = tmp_path / "args"
    input_path = tmp_path / "input.json"
    fake = tmp_path / "gh"
    fake.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$@" > "$FAKE_GH_ARGS"
cat > "$FAKE_GH_INPUT"
"""
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake, args_path, input_path


def _run(
    tmp_path: Path,
    *,
    overrides: dict[str, str] | None = None,
    remove: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    fake, args_path, input_path = _fake_gh(tmp_path)
    env = {
        **os.environ,
        **_REQUIRED_ENV,
        "DISPATCH_GH": str(fake),
        "FAKE_GH_ARGS": str(args_path),
        "FAKE_GH_INPUT": str(input_path),
    }
    if overrides is not None:
        env.update(overrides)
    if remove is not None:
        env.pop(remove)

    result = subprocess.run(
        ["bash", str(_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=10,
    )
    return result, args_path, input_path


@pytest.mark.parametrize(("target_repo", "event_type", "delivery_id"), _EXPECTED_DELIVERIES)
def test_dispatch_uses_exact_endpoint_payload_and_delivery_identity(
    tmp_path: Path, target_repo: str, event_type: str, delivery_id: str
) -> None:
    result, args_path, input_path = _run(
        tmp_path,
        overrides={"TARGET_REPO": target_repo, "EVENT_TYPE": event_type},
    )

    assert result.returncode == 0, result.stderr
    assert args_path.read_text().splitlines() == [
        "api",
        "--method",
        "POST",
        f"repos/f5-sales-demo/{target_repo}/dispatches",
        "--input",
        "-",
    ]
    assert json.loads(input_path.read_text()) == {
        "event_type": event_type,
        "client_payload": {
            "delivery_id": delivery_id,
            "release_tag": "v2.1.220",
            "release_url": (
                "https://github.com/f5-sales-demo/api-specs-enriched/releases/tag/v2.1.220"
            ),
            "target_commit": "b3130b919b421a60b3f527c1cea686aebdd59bc3",
            "trigger_source": "f5-sales-demo/api-specs-enriched",
            "version": "2.1.220",
        },
    }


def test_delivery_identity_changes_with_target_or_event_type(tmp_path: Path) -> None:
    ids = set()
    for overrides in (
        {},
        {"TARGET_REPO": "vscode-xcsh"},
        {"EVENT_TYPE": "upstream-enrichment-changed"},
    ):
        result, _, input_path = _run(tmp_path, overrides=overrides)
        assert result.returncode == 0, result.stderr
        ids.add(json.loads(input_path.read_text())["client_payload"]["delivery_id"])

    assert len(ids) == 3


@pytest.mark.parametrize("name", sorted(_REQUIRED_ENV))
def test_dispatch_fails_closed_when_input_is_missing(tmp_path: Path, name: str) -> None:
    result, args_path, _ = _run(tmp_path, remove=name)

    assert result.returncode != 0
    assert name in result.stderr
    assert not args_path.exists()


def test_dispatch_fails_closed_when_target_commit_is_empty(tmp_path: Path) -> None:
    result, args_path, _ = _run(tmp_path, overrides={"SOURCE_TARGET_COMMIT": ""})

    assert result.returncode != 0
    assert "SOURCE_TARGET_COMMIT" in result.stderr
    assert not args_path.exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TARGET_OWNER", "f5-sales-demo/escape"),
        ("TARGET_REPO", "../escape"),
        ("EVENT_TYPE", "invalid event"),
        ("VERSION", "2.1.220/escape"),
        ("SOURCE_REPOSITORY", "missing-slash"),
        ("SOURCE_TARGET_COMMIT", "not-a-commit"),
        ("SOURCE_TARGET_COMMIT", "B3130B919B421A60B3F527C1CEA686AEBDD59BC3"),
    ],
)
def test_dispatch_rejects_malformed_input(tmp_path: Path, name: str, value: str) -> None:
    result, args_path, _ = _run(tmp_path, overrides={name: value})

    assert result.returncode != 0
    assert name in result.stderr
    assert not args_path.exists()


def test_workflow_delegates_dispatch_without_a_third_party_action() -> None:
    workflow = _WORKFLOW.read_text()

    assert "run: bash scripts/release/dispatch-downstream.sh" in workflow
    assert "peter-evans/repository-dispatch@" not in workflow


def test_notify_job_checks_out_the_dispatch_helper_before_running_it() -> None:
    workflow = _WORKFLOW.read_text()
    notify_job = workflow.split("\n  notify-downstream:\n", maxsplit=1)[1].split(
        "\n  # ==========================================================================",
        maxsplit=1,
    )[0]

    checkout = "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    dispatch = "run: bash scripts/release/dispatch-downstream.sh"
    assert notify_job.index(checkout) < notify_job.index(dispatch)
