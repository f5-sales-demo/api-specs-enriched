"""Release identity must agree across source, assets, and receipts."""

from __future__ import annotations

import copy

import pytest

from scripts.verify_release_version import ReleaseVersionError, verify_release_version_coherence


def _documents() -> dict[str, dict]:
    return {
        "openapi.json": {"openapi": "3.0.3", "info": {"version": "2.1.225"}},
        "index.json": {"version": "2.1.225"},
        "api-catalog.json": {"version": "2.1.225"},
        "concurrency_contracts.json": {"version": "2.1.225"},
        "smsv2_parity_manifest.json": {"version": "2.1.225"},
    }


def test_accepts_coherent_release_identity() -> None:
    verify_release_version_coherence(
        version="2.1.225",
        tag="v2.1.225",
        documents=_documents(),
        contract_manifest={"release": {"tag": "v2.1.225", "commit": "a" * 40}},
        receipt={"version": "2.1.225", "commit": "a" * 40},
        archive_name="f5xc-api-specs-v2.1.225.zip",
    )


@pytest.mark.parametrize(
    ("surface", "message"),
    [
        ("tag", "tag"),
        ("catalog", "api-catalog.json"),
        ("openapi", "openapi.json"),
        ("index", "index.json"),
        ("manifest", "manifest"),
        ("receipt", "receipt"),
        ("archive", "archive"),
    ],
)
def test_rejects_any_release_identity_mismatch(surface: str, message: str) -> None:
    documents = _documents()
    tag = "v2.1.225"
    manifest = {"release": {"tag": tag, "commit": "a" * 40}}
    receipt = {"version": "2.1.225", "commit": "a" * 40}
    archive = "f5xc-api-specs-v2.1.225.zip"
    if surface == "tag":
        tag = "v2.1.224"
    elif surface == "catalog":
        documents["api-catalog.json"]["version"] = "2.1.224"
    elif surface == "openapi":
        documents["openapi.json"]["info"]["version"] = "2.1.224"
    elif surface == "index":
        documents["index.json"]["version"] = "2.1.224"
    elif surface == "manifest":
        manifest["release"]["tag"] = "v2.1.224"
    elif surface == "receipt":
        receipt["version"] = "2.1.224"
    elif surface == "archive":
        archive = "f5xc-api-specs-v2.1.224.zip"

    with pytest.raises(ReleaseVersionError, match=message):
        verify_release_version_coherence(
            version="2.1.225",
            tag=tag,
            documents=copy.deepcopy(documents),
            contract_manifest=manifest,
            receipt=receipt,
            archive_name=archive,
        )
