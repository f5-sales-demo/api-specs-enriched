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
    "SOURCE_TARGET_COMMIT": "b3130b919b421a60b3f527c1cea686aebdd59bc3",
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
    payload = json.loads(input_path.read_text())
    assert payload["event_type"] == "enriched-specs-updated"
    assert payload["client_payload"]["version"] == "2.1.211"
    assert payload["client_payload"]["release_tag"] == "v2.1.211"
    assert (
        payload["client_payload"]["release_url"]
        == "https://github.com/f5-sales-demo/api-specs-enriched/releases/tag/v2.1.211"
    )
    assert payload["client_payload"]["target_commit"] == "b3130b919b421a60b3f527c1cea686aebdd59bc3"
    assert payload["client_payload"]["trigger_source"] == "f5-sales-demo/api-specs-enriched"
    assert len(payload["client_payload"]["delivery_id"]) == 64


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
        ("SOURCE_TARGET_COMMIT", "not-a-sha"),
    ],
)
def test_dispatch_rejects_malformed_input(tmp_path: Path, name: str, value: str) -> None:
    result, args_path, _ = _run(tmp_path, overrides={name: value})

    assert result.returncode != 0
    assert name in result.stderr
    assert not args_path.exists()
