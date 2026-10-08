from pathlib import Path

WORKFLOW = Path(".github/workflows/github-pages-deploy.yml")
REJECTED_DOCS_CONTROL_REVISION = "58ce2a9b09aa28f6a37b53c6cce445216bc46670"
IMMUTABLE_SELECTOR_REVISION = "276b522810ed9fb3d91c9762d874a600fa637bbe"
BUILDER_DIGEST = "sha256:049219671eb53fb884af5a529ab638ba01894492ac6918da76bc17a069607e72"


def test_release_pages_wrapper_uses_the_immutable_selector_repair() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert (
        "uses: f5-sales-demo/docs-control/.github/workflows/github-pages-deploy.yml"
        f"@{IMMUTABLE_SELECTOR_REVISION}"
    ) in workflow
    assert REJECTED_DOCS_CONTROL_REVISION not in workflow
    assert "content-ref: ${{ inputs.target-commit }}" in workflow
    assert f"builder-image: ghcr.io/f5-sales-demo/docs-builder@{BUILDER_DIGEST}" in workflow
