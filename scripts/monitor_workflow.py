#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Workflow Monitoring Script.

Analyzes GitHub Actions workflow runs for failures and creates/updates issues.
Features:
- SHA256 fingerprinting for deduplication
- Failure categorization by type
- Suggested remediation actions
- Issue aggregation to prevent spam
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

JOB_RESULT_ENV_VARS = (
    ("JOB_CHECK_UPDATES", "Check for Updates"),
    ("JOB_SYNC_ENRICH", "Sync and Enrich Specifications"),
    ("JOB_DEPLOY", "Deploy Documentation"),
    ("JOB_BUILD_MATRIX", "Build Downstream Matrix"),
    ("JOB_NOTIFY_DOWNSTREAM", "Notify Downstream Repositories"),
)


@dataclass
class WorkflowFailure:
    """Represents a workflow failure with fingerprint for deduplication."""

    job_name: str
    step_name: str | None
    conclusion: str
    error_message: str
    run_id: str
    workflow: str
    branch: str
    commit: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def fingerprint(self) -> str:
        """Generate SHA256 fingerprint for deduplication."""
        components = [
            self.job_name,
            self.step_name or "",
            self._normalize_error(self.error_message),
        ]
        content = "|".join(components)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @staticmethod
    def _normalize_error(error: str) -> str:
        """Normalize error message for consistent fingerprinting."""
        # Remove timestamps, run IDs, and other variable parts
        error = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", "TIMESTAMP", error)
        error = re.sub(r"run[_-]?id[:\s]*\d+", "RUN_ID", error, flags=re.IGNORECASE)
        error = re.sub(r"[0-9a-f]{40}", "COMMIT_SHA", error)  # Git SHAs
        error = re.sub(r"[0-9a-f]{7,8}", "SHORT_SHA", error)  # Short SHAs
        return error.strip()[:500]  # Limit length

    @property
    def category(self) -> str:
        """Categorize failure type based on job/step name."""
        name = f"{self.job_name} {self.step_name or ''}".lower()
        if "download" in name:
            return "download"
        if "enrich" in name or "pipeline" in name:
            return "enrichment"
        if "lint" in name or "spectral" in name or "validate" in name:
            return "validation"
        if "git" in name or "commit" in name or "push" in name:
            return "git"
        if "release" in name or "package" in name:
            return "release"
        if "deploy" in name or "pages" in name:
            return "deployment"
        if "deprecat" in name.lower():
            return "deprecation"
        return "other"

    @property
    def severity(self) -> str:
        """Determine severity based on failure type."""
        if self.category in ("deployment", "release"):
            return "critical"
        if self.category == "deprecation":
            return "warning"
        return "warning"


def load_config() -> dict[str, Any]:
    """Load monitoring configuration."""
    config_path = Path("config/monitoring.yaml")
    if config_path.exists():
        with config_path.open() as f:
            return yaml.safe_load(f) or {}
    return {
        "deduplication": {"fingerprint_length": 16, "search_limit": 50},
        "labels": {
            "primary": ["workflow-failure", "auto-created"],
            "failure_types": [
                "failure:download",
                "failure:enrichment",
                "failure:validation",
                "failure:git",
                "failure:release",
                "failure:deployment",
                "failure:deprecation",
                "failure:other",
            ],
            "severity": ["severity:critical", "severity:warning", "severity:info"],
            "status": ["needs-investigation"],
        },
    }


def run_gh_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a GitHub CLI command."""
    cmd = ["gh", *args]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"Error running gh command: {' '.join(cmd)}", file=sys.stderr)
        print(f"stderr: {result.stderr}", file=sys.stderr)
    return result


def get_workflow_run_details(run_id: str) -> dict[str, Any] | None:
    """Fetch workflow run details via gh CLI."""
    result = run_gh_command(
        ["run", "view", run_id, "--json", "jobs,conclusion,status,url"],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def parse_failures(run_details: dict[str, Any], env_vars: dict[str, str]) -> list[WorkflowFailure]:
    """Parse workflow run details into failure objects."""
    failures: list[WorkflowFailure] = []

    jobs = run_details.get("jobs", [])
    for job in jobs:
        job_name = job.get("name", "unknown")
        conclusion = job.get("conclusion", "unknown")

        if conclusion == "failure":
            # Check steps for specific failure
            steps = job.get("steps", [])
            failed_step = None
            error_msg = ""

            for step in steps:
                if step.get("conclusion") == "failure":
                    failed_step = step.get("name")
                    error_msg = f"Step '{failed_step}' {step.get('conclusion')}"
                    break

            if not failed_step:
                error_msg = f"Job '{job_name}' {conclusion}"

            failure = WorkflowFailure(
                job_name=job_name,
                step_name=failed_step,
                conclusion=conclusion,
                error_message=error_msg,
                run_id=env_vars.get("RUN_ID", ""),
                workflow=env_vars.get("WORKFLOW_NAME", ""),
                branch=env_vars.get("BRANCH", ""),
                commit=env_vars.get("COMMIT_SHA", ""),
            )
            failures.append(failure)

    return failures


def parse_fallback_failures(env_vars: dict[str, str]) -> list[WorkflowFailure]:
    """Parse genuine job failures from workflow result environment variables."""
    failures: list[WorkflowFailure] = []
    for job_var, job_name in JOB_RESULT_ENV_VARS:
        result = os.environ.get(job_var, "success")
        if result != "failure":
            continue
        failures.append(
            WorkflowFailure(
                job_name=job_name,
                step_name=None,
                conclusion=result,
                error_message=f"Job '{job_name}' {result}",
                run_id=env_vars.get("RUN_ID", ""),
                workflow=env_vars.get("WORKFLOW_NAME", ""),
                branch=env_vars.get("BRANCH", ""),
                commit=env_vars.get("COMMIT_SHA", ""),
            )
        )
    return failures


def search_existing_issue(fingerprint: str) -> dict[str, Any] | None:
    """Search for existing open issue with matching fingerprint."""
    search_query = f"is:issue is:open label:workflow-failure fingerprint:{fingerprint}"
    result = run_gh_command(
        [
            "issue",
            "list",
            "--search",
            search_query,
            "--json",
            "number,title,body,url",
            "--limit",
            "5",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        issues = json.loads(result.stdout)
        for issue in issues:
            if fingerprint in issue.get("body", ""):
                return issue
    except json.JSONDecodeError:
        pass
    return None


def create_issue(failure: WorkflowFailure) -> str | None:
    """Create a new GitHub issue for the failure."""
    title = f"[Workflow Failure] {failure.job_name}"
    if failure.step_name:
        title += f" - {failure.step_name}"

    body = f"""## Workflow Failure Report

**Fingerprint**: `{failure.fingerprint}`

### Run Information
| Field | Value |
|-------|-------|
| Workflow | {failure.workflow} |
| Run ID | [{failure.run_id}](https://github.com/${{GITHUB_REPOSITORY}}/actions/runs/{failure.run_id}) |
| Branch | `{failure.branch}` |
| Commit | `{failure.commit[:7]}` |
| Trigger | {os.environ.get("TRIGGER_EVENT", "unknown")} |
| Actor | {os.environ.get("ACTOR", "unknown")} |

### Failure Details
| Field | Value |
|-------|-------|
| Job | {failure.job_name} |
| Step | {failure.step_name or "N/A"} |
| Conclusion | {failure.conclusion} |
| Category | {failure.category} |
| Severity | {failure.severity} |

### Error Message
```
{failure.error_message}
```

### Suggested Remediation
{get_remediation_suggestion(failure)}

---
*This issue was automatically created by the workflow monitoring system.*
*Fingerprint: `{failure.fingerprint}`*
"""

    labels = [
        "workflow-failure",
        "auto-created",
        f"failure:{failure.category}",
        f"severity:{failure.severity}",
        "needs-investigation",
    ]

    result = run_gh_command(
        ["issue", "create", "--title", title, "--body", body, "--label", ",".join(labels)],
        check=False,
    )

    if result.returncode == 0:
        # Extract issue URL from output
        url = result.stdout.strip()
        print(f"Created issue: {url}")
        return url
    print(f"Failed to create issue: {result.stderr}", file=sys.stderr)
    return None


def update_issue(issue: dict[str, Any], failure: WorkflowFailure) -> bool:
    """Update existing issue with new occurrence."""
    issue_number = issue.get("number")
    if not issue_number:
        return False

    comment = f"""## New Occurrence

**Run ID**: [{failure.run_id}](https://github.com/${{GITHUB_REPOSITORY}}/actions/runs/{failure.run_id})
**Branch**: `{failure.branch}`
**Commit**: `{failure.commit[:7]}`
**Time**: {failure.timestamp}
**Conclusion**: {failure.conclusion}

```
{failure.error_message}
```
"""

    result = run_gh_command(["issue", "comment", str(issue_number), "--body", comment], check=False)

    if result.returncode == 0:
        print(f"Updated issue #{issue_number} with new occurrence")
        return True
    print(f"Failed to update issue: {result.stderr}", file=sys.stderr)
    return False


def get_remediation_suggestion(failure: WorkflowFailure) -> str:
    """Generate remediation suggestion based on failure category."""
    suggestions = {
        "download": """
- Check whether the pinned upstream GitHub release and asset are accessible
- Verify the release asset digest matches the immutable receipt
- Check network connectivity and rate limits
- Try `make download-force` to refresh the same release identity
""",
        "enrichment": """
- Check `config/enrichment.yaml` for syntax errors
- Verify input specs exist in `specs/original/`
- Run `make pipeline` locally to reproduce
- Check `reports/pipeline-report.json` for details
""",
        "validation": """
- Check Spectral rules in `config/spectral.yaml`
- Run `make lint` locally to see specific errors
- Review `reports/lint-report.json` for details
- Ensure generated specs are valid OpenAPI
""",
        "git": """
- Check for merge conflicts
- Verify branch protection rules
- Ensure GITHUB_TOKEN has sufficient permissions
- Verify `.github_release` contains the immutable upstream release receipt
""",
        "release": """
- Verify version bumping logic
- Check if release already exists
- Ensure GITHUB_TOKEN can create releases
- Verify package generation succeeded
""",
        "deployment": """
- Check GitHub Pages configuration
- Verify `docs/` artifact was uploaded
- Ensure Pages environment is configured
- Check for deployment permission issues
""",
        "deprecation": """
- Review deprecation warnings in logs
- Update deprecated GitHub Actions
- Update deprecated API usage
- Check for package version updates needed
""",
        "other": """
- Review workflow logs for specific error
- Check environment variables are set
- Verify dependencies are installed
- Try running locally to reproduce
""",
    }
    return suggestions.get(failure.category, suggestions["other"])


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Monitor workflow failures and create issues")
    parser.add_argument("--run-id", required=True, help="Workflow run ID")
    parser.add_argument("--workflow", required=True, help="Workflow name")
    parser.add_argument("--event", required=True, help="Trigger event")
    parser.add_argument("--branch", required=True, help="Branch name")
    parser.add_argument("--commit", required=True, help="Commit SHA")
    parser.add_argument("--dry-run", action="store_true", help="Don't create issues, just print")
    args = parser.parse_args()

    # Load configuration (for future extensibility)
    load_config()

    # Collect environment variables
    env_vars = {
        "RUN_ID": args.run_id,
        "WORKFLOW_NAME": args.workflow,
        "TRIGGER_EVENT": args.event,
        "BRANCH": args.branch,
        "COMMIT_SHA": args.commit,
    }

    # Get workflow run details
    print(f"Fetching workflow run details for {args.run_id}...")
    run_details = get_workflow_run_details(args.run_id)

    if not run_details:
        # Fall back to environment variables for job results
        print("Could not fetch run details, using environment variables...")
        failures = parse_fallback_failures(env_vars)
    else:
        failures = parse_failures(run_details, env_vars)

    if not failures:
        print("No failures detected")
        return 0

    print(f"Found {len(failures)} failure(s)")

    # Process each failure
    issues_created = 0
    issues_updated = 0

    for failure in failures:
        print(f"\nProcessing: {failure.job_name} ({failure.category})")
        print(f"  Fingerprint: {failure.fingerprint}")

        if args.dry_run:
            print("  [DRY RUN] Would create/update issue")
            continue

        # Check for existing issue
        existing = search_existing_issue(failure.fingerprint)

        if existing:
            print(f"  Found existing issue: #{existing.get('number')}")
            if update_issue(existing, failure):
                issues_updated += 1
        else:
            print("  No existing issue, creating new one...")
            if create_issue(failure):
                issues_created += 1

    print(f"\nSummary: {issues_created} issues created, {issues_updated} issues updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
