"""Regression tests for fail-loud workflow monitoring."""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from scripts import monitor_workflow as monitor


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        run_id="123",
        workflow="Publish",
        branch="main",
        commit="a" * 40,
    )


def test_explicit_needs_results_report_each_measured_failure() -> None:
    failures = monitor.failures_from_job_results(
        {
            "JOB_RECONCILE": "success",
            "JOB_VERIFY_PAGES": "failure",
            "JOB_NOTIFY_DOWNSTREAM": "cancelled",
            "JOB_SKIPPED": "skipped",
        },
        _args(),
    )

    assert [(item.job_name, item.conclusion) for item in failures] == [
        ("Notify Downstream", "cancelled"),
        ("Skipped", "skipped"),
        ("Verify Pages", "failure"),
    ]


def test_explicit_audit_failure_uses_captured_authority_measurement() -> None:
    failures = monitor.failures_from_job_results(
        {"JOB_AUDIT_DOWNSTREAM": "failure"},
        _args(),
        failure_details={
            "JOB_AUDIT_DOWNSTREAM": ("RuntimeError: npm tarball bytes disagree for @f5/xcsh@3.2.1")
        },
    )

    assert len(failures) == 1
    assert failures[0].error_message == (
        "Job 'Audit Downstream' failure: "
        "RuntimeError: npm tarball bytes disagree for @f5/xcsh@3.2.1"
    )


def test_only_explicitly_expected_skips_are_ignored() -> None:
    failures = monitor.failures_from_job_results(
        {
            "JOB_DEPLOY_DOCS": "skipped",
            "JOB_VERIFY_PAGES": "skipped",
        },
        _args(),
        frozenset({"JOB_DEPLOY_DOCS"}),
    )

    assert [(item.job_name, item.error_message) for item in failures] == [
        ("Verify Pages", "Job 'Verify Pages' was unexpectedly skipped"),
    ]


def test_expected_skip_must_name_a_measured_job() -> None:
    with pytest.raises(RuntimeError, match="not measured"):
        monitor.failures_from_job_results(
            {"JOB_RECONCILE": "success"},
            _args(),
            frozenset({"JOB_MISSING"}),
        )


def test_unknown_job_result_is_reported_not_ignored() -> None:
    failures = monitor.failures_from_job_results(
        {"JOB_RECONCILE": ""},
        _args(),
    )

    assert len(failures) == 1
    assert failures[0].conclusion == "failure"
    assert "unsupported result" in failures[0].error_message


def test_github_command_failure_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["gh"], 1, stdout="", stderr="API unavailable"
        ),
    )

    with pytest.raises(RuntimeError, match="API unavailable"):
        monitor.run_gh_command(["issue", "list"])


def test_issue_search_rejects_invalid_api_json(monkeypatch) -> None:
    monkeypatch.setattr(
        monitor,
        "run_gh_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["gh"], 0, stdout="not-json", stderr=""
        ),
    )

    with pytest.raises(RuntimeError, match="invalid JSON"):
        monitor.search_existing_issue("fingerprint")


def test_issue_creation_requires_returned_url(monkeypatch) -> None:
    monkeypatch.setattr(
        monitor,
        "run_gh_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(["gh"], 0, stdout="", stderr=""),
    )

    with pytest.raises(RuntimeError, match="without an issue URL"):
        monitor.create_issue(
            monitor.WorkflowFailure(
                job_name="Publish",
                step_name=None,
                conclusion="failure",
                error_message="failed",
                run_id="123",
                workflow="Publish",
                branch="main",
                commit="a" * 40,
            )
        )


def test_unexpected_skipped_job_files_issue_and_fails_monitor(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "monitor-workflow",
            "--run-id",
            "123",
            "--workflow",
            "Producer",
            "--event",
            "push",
            "--branch",
            "main",
            "--commit",
            "a" * 40,
        ],
    )
    monkeypatch.setenv("JOB_CHECK_UPDATES", "success")
    monkeypatch.setenv("JOB_SYNC_ENRICH", "skipped")
    monkeypatch.delenv("EXPECTED_SKIPPED_JOBS", raising=False)
    monkeypatch.setattr(monitor, "search_existing_issue", lambda _fingerprint: None)
    monkeypatch.setattr(monitor, "create_issue", lambda _failure: "https://example.invalid/1")

    assert monitor.main() == 1


def test_explicit_expected_skip_keeps_monitor_successful(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "monitor-workflow",
            "--run-id",
            "123",
            "--workflow",
            "Producer",
            "--event",
            "schedule",
            "--branch",
            "main",
            "--commit",
            "a" * 40,
        ],
    )
    monkeypatch.setenv("JOB_CHECK_UPDATES", "success")
    monkeypatch.setenv("JOB_SYNC_ENRICH", "skipped")
    monkeypatch.setenv("EXPECTED_SKIPPED_JOBS", "JOB_SYNC_ENRICH")

    assert monitor.main() == 0
