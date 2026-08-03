import re
from pathlib import Path

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
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

    for workflow in sorted(WORKFLOWS.glob("*.y*ml")):
        for line_number, line in enumerate(workflow.read_text().splitlines(), start=1):
            match = USES_LINE.match(line)
            if match is None:
                continue
            reference = match.group(1)
            if reference.startswith("./"):
                continue
            if IMMUTABLE_REMOTE_USE.fullmatch(reference) is None:
                mutable.append(
                    f"{workflow.relative_to(WORKFLOWS.parent.parent)}:{line_number}: {reference}"
                )

    assert not mutable, "Mutable remote action references:\n" + "\n".join(mutable)


def test_privileged_dispatch_has_no_third_party_action_dependency() -> None:
    workflow = (WORKFLOWS / "sync-and-enrich.yml").read_text()

    assert "peter-evans/repository-dispatch@" not in workflow
