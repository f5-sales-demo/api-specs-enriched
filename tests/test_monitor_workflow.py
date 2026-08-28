"""Regression coverage for workflow failure monitoring."""

from pathlib import Path

import pytest

from scripts.monitor_workflow import (
    JOB_RESULT_ENV_VARS,
    WorkflowFailure,
    parse_failures,
    parse_fallback_failures,
)

RUN_ENV = {
    "RUN_ID": "12345",
    "WORKFLOW_NAME": "Sync and Enrich API Specs",
    "BRANCH": "main",
    "COMMIT_SHA": "0123456789abcdef",
}


def test_fully_cancelled_run_produces_no_failures() -> None:
    run_details = {
        "jobs": [
            {
                "name": name,
                "conclusion": "cancelled",
                "steps": [{"name": "Work", "conclusion": "cancelled"}],
            }
            for name in (
                "Check for Updates",
                "Sync and Enrich Specifications",
                "Deploy Documentation",
                "Build Downstream Matrix",
                "Notify Downstream Repositories",
            )
        ]
    }

    assert not parse_failures(run_details, RUN_ENV)


def test_cancelled_step_does_not_create_candidate_in_failed_job() -> None:
    run_details = {
        "jobs": [
            {
                "name": "Sync and Enrich Specifications",
                "conclusion": "failure",
                "steps": [
                    {"name": "Superseded work", "conclusion": "cancelled"},
                    {"name": "Validate specifications", "conclusion": "failure"},
                ],
            }
        ]
    }

    failures = parse_failures(run_details, RUN_ENV)

    assert len(failures) == 1
    assert failures[0].step_name == "Validate specifications"
    assert failures[0].conclusion == "failure"


def test_mixed_cancelled_and_failed_jobs_report_only_failure() -> None:
    run_details = {
        "jobs": [
            {"name": "Check for Updates", "conclusion": "cancelled", "steps": []},
            {
                "name": "Deploy Documentation",
                "conclusion": "failure",
                "steps": [{"name": "Deploy Pages", "conclusion": "failure"}],
            },
        ]
    }

    failures = parse_failures(run_details, RUN_ENV)

    assert [(failure.job_name, failure.step_name) for failure in failures] == [
        ("Deploy Documentation", "Deploy Pages")
    ]


@pytest.mark.parametrize("failed_variable", [item[0] for item in JOB_RESULT_ENV_VARS])
def test_fallback_detects_each_job_failure_only(
    monkeypatch: pytest.MonkeyPatch,
    failed_variable: str,
) -> None:
    for variable, _job_name in JOB_RESULT_ENV_VARS:
        monkeypatch.setenv(variable, "failure" if variable == failed_variable else "cancelled")

    failures = parse_fallback_failures(RUN_ENV)

    assert len(failures) == 1
    assert failures[0].job_name == dict(JOB_RESULT_ENV_VARS)[failed_variable]
    assert failures[0].conclusion == "failure"


def test_genuine_failure_contract_is_preserved() -> None:
    failure = WorkflowFailure(
        job_name="Deploy Documentation",
        step_name="Deploy Pages",
        conclusion="failure",
        error_message="Step 'Deploy Pages' failure in run_id: 12345",
        run_id="12345",
        workflow="Sync and Enrich API Specs",
        branch="main",
        commit="0123456789abcdef",
    )

    assert failure.fingerprint == "ab682e388257f5e2"
    assert failure.category == "deployment"
    assert failure.severity == "critical"


def test_workflow_gate_checks_all_failures_and_no_cancellations() -> None:
    workflow = Path(".github/workflows/sync-and-enrich.yml").read_text()
    monitor_job = workflow.split("\n  monitor-failures:\n", maxsplit=1)[1]
    gate = monitor_job.split("- name: Check for failures", maxsplit=1)[1].split(
        "- name: Checkout repository", maxsplit=1
    )[0]

    for result_variable in (
        "CHECK_UPDATES_RESULT",
        "SYNC_AND_ENRICH_RESULT",
        "DEPLOY_DOCS_RESULT",
        "BUILD_DOWNSTREAM_MATRIX_RESULT",
        "NOTIFY_DOWNSTREAM_RESULT",
    ):
        assert f'[ "${result_variable}" = "failure" ]' in gate
    assert "cancelled" not in gate
