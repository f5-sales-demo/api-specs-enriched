from pathlib import Path

WORKFLOW = Path(".github/workflows/github-pages-deploy.yml")
REJECTED_DOCS_CONTROL_REVISION = "58ce2a9b09aa28f6a37b53c6cce445216bc46670"
IMMUTABLE_SELECTOR_REVISION = "37b1cf98f29b92bb5f4bf5a727e5fcd025b7899e"
BUILDER_DIGEST = "sha256:72ab6ad2488d391b86d737c1375f782876cce16501f567d327db1caae0f3a7ae"


def test_release_pages_wrapper_uses_the_immutable_selector_repair() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert (
        "uses: f5-sales-demo/docs-control/.github/workflows/github-pages-deploy.yml"
        f"@{IMMUTABLE_SELECTOR_REVISION}"
    ) in workflow
    assert REJECTED_DOCS_CONTROL_REVISION not in workflow
    assert "content-ref: ${{ inputs.target-commit }}" in workflow
    assert f"builder-image: ghcr.io/f5-sales-demo/docs-builder@{BUILDER_DIGEST}" in workflow
