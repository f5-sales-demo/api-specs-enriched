# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Every remote workflow dependency must resolve to immutable Git bytes."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)")
IMMUTABLE_REMOTE_USE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def test_uses_parser_accepts_job_and_inline_step_syntax() -> None:
    reference = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"

    for line in (f"    uses: {reference}", f"    - uses: {reference}"):
        match = USES_LINE.match(line)
        assert match is not None
        assert match.group(1) == reference


def test_every_remote_action_reference_is_immutable() -> None:
    mutable: list[str] = []
    workflow_files = sorted(
        [*(ROOT / ".github").rglob("*.yml"), *(ROOT / ".github").rglob("*.yaml")]
    )

    for workflow in workflow_files:
        for line_number, line in enumerate(workflow.read_text().splitlines(), start=1):
            match = USES_LINE.match(line)
            if match is None:
                continue
            reference = match.group(1)
            if reference.startswith(("./", "docker://")):
                continue
            if IMMUTABLE_REMOTE_USE.fullmatch(reference) is None:
                mutable.append(f"{workflow.relative_to(ROOT)}:{line_number}: {reference}")

    assert not mutable, "Mutable remote action references:\n" + "\n".join(mutable)


def test_privileged_dispatch_has_no_third_party_action_dependency() -> None:
    workflow = (WORKFLOWS / "sync-and-enrich.yml").read_text()

    assert "peter-evans/repository-dispatch@" not in workflow
