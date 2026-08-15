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
    "TARGET_REPO": "terraform-provider-xcsh",
    "EVENT_TYPE": "enriched-specs-updated",
    "VERSION": "2.1.211",
    "SOURCE_REPOSITORY": "f5-sales-demo/api-specs-enriched",
    "SOURCE_UPDATED_AT": "2026-08-03T03:10:54Z",
    "SOURCE_RUN_ID": "30781155338",
}


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


def test_dispatch_uses_exact_endpoint_and_payload(tmp_path: Path) -> None:
    result, args_path, input_path = _run(tmp_path)

    assert result.returncode == 0, result.stderr
    assert args_path.read_text().splitlines() == [
        "api",
        "--method",
        "POST",
        "repos/f5-sales-demo/terraform-provider-xcsh/dispatches",
        "--input",
        "-",
    ]
    assert json.loads(input_path.read_text()) == {
        "event_type": "enriched-specs-updated",
        "client_payload": {
            "version": "2.1.211",
            "release_tag": "v2.1.211",
            "release_url": (
                "https://github.com/f5-sales-demo/api-specs-enriched/releases/tag/v2.1.211"
            ),
            "timestamp": "2026-08-03T03:10:54Z",
            "trigger_source": "f5-sales-demo/api-specs-enriched",
            "run_id": "30781155338",
        },
    }


@pytest.mark.parametrize("name", sorted(_REQUIRED_ENV))
def test_dispatch_fails_closed_when_input_is_missing(tmp_path: Path, name: str) -> None:
    result, args_path, _ = _run(tmp_path, remove=name)

    assert result.returncode != 0
    assert name in result.stderr
    assert not args_path.exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TARGET_OWNER", "f5-sales-demo/escape"),
        ("TARGET_REPO", "../escape"),
        ("EVENT_TYPE", "invalid event"),
        ("VERSION", "2.1.211/escape"),
        ("SOURCE_REPOSITORY", "missing-slash"),
        ("SOURCE_UPDATED_AT", "not-a-timestamp"),
        ("SOURCE_RUN_ID", "not-a-run-id"),
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
