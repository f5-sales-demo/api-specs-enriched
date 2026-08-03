# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Clean-break contracts for optional, non-production discovery enrichment."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from scripts import enrich, pipeline
from scripts.utils.discovery_enricher import DiscoveryData, DiscoveryEnricher
from scripts.utils.extension_constants import X_F5XC_DISCOVERED_RESPONSE_TIME


def test_release_pipeline_rejects_every_discovery_configuration(tmp_path: Path) -> None:
    """The release graph has exactly one source: the immutable upstream artifact."""
    with pytest.raises(ValueError, match="live discovery snapshots are not release inputs"):
        pipeline.run_pipeline(
            input_dir=tmp_path / "unused-input",
            output_dir=tmp_path / "unused-output",
            report_dir=tmp_path / "unused-reports",
            config={"discovery_enrichment": {"enabled": False}},
            version="1.2.3",
        )


def test_release_pipeline_has_no_discovery_environment_escape_hatch() -> None:
    """A workflow environment variable cannot reintroduce a second release source."""
    source = Path(pipeline.__file__).read_text()
    assert "DISCOVERY_ENRICHMENT_ENABLED" not in source
    assert "specs/discovered" not in source
    config = yaml.safe_load(Path("config/enrichment.yaml").read_text())
    assert "discovery_enrichment" not in config
    assert not Path("config/latency_estimates.yaml").exists()


def test_discovery_enrichment_cannot_write_the_canonical_publishable_tree(tmp_path: Path) -> None:
    canonical = tmp_path / "docs" / "specifications" / "api"

    with pytest.raises(ValueError, match="explicit noncanonical"):
        enrich.require_noncanonical_discovery_output(
            canonical,
            canonical,
            explicitly_selected=False,
        )
    for forbidden in (canonical, canonical / "discovery"):
        with pytest.raises(ValueError, match="canonical publishable"):
            enrich.require_noncanonical_discovery_output(
                forbidden,
                canonical,
                explicitly_selected=True,
            )

    enrich.require_noncanonical_discovery_output(
        tmp_path / "scratch" / "discovery-output",
        canonical,
        explicitly_selected=True,
    )


@pytest.mark.parametrize("missing_name", ["openapi.json", "session.json"])
def test_explicit_discovery_requires_both_snapshot_files(
    tmp_path: Path,
    missing_name: str,
) -> None:
    """Explicit local enrichment fails before processing an incomplete snapshot."""
    files = {
        "openapi.json": {
            "openapi": "3.0.3",
            "info": {"title": "Discovery", "version": "1.0.0"},
            "paths": {"/probe": {"get": {}}},
        },
        "session.json": {"started_at": "2026-08-02T00:00:00Z"},
    }
    for name, document in files.items():
        if name != missing_name:
            (tmp_path / name).write_text(json.dumps(document))

    with pytest.raises(FileNotFoundError, match=missing_name):
        DiscoveryEnricher({}).load_discovery_data(tmp_path)


def test_explicit_loader_does_not_downgrade_missing_inputs_to_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An enabled local run fails instead of silently returning no enricher."""
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Discovery", "version": "1.0.0"},
                "paths": {"/probe": {"get": {}}},
            }
        )
    )
    monkeypatch.setattr(
        enrich,
        "_DISCOVERY_CACHE",
        {"enricher": None, "config": None, "signature": None},
    )

    with pytest.raises(FileNotFoundError, match=r"session\.json"):
        enrich.load_discovery_enricher(
            {
                "discovery_enrichment": {
                    "enabled": True,
                    "discovered_specs_dir": str(tmp_path),
                }
            }
        )


def test_explicit_loader_uses_the_requested_snapshot_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detailed enrichment settings cannot redirect an explicitly selected snapshot."""
    (tmp_path / "openapi.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Discovery", "version": "1.0.0"},
                "paths": {"/probe": {"get": {}}},
            }
        )
    )
    (tmp_path / "session.json").write_text(json.dumps({"started_at": "2026-08-02T00:00:00Z"}))
    monkeypatch.setattr(
        enrich,
        "_DISCOVERY_CACHE",
        {"enricher": None, "config": None, "signature": None},
    )

    enricher = enrich.load_discovery_enricher(
        {
            "discovery_enrichment": {
                "enabled": True,
                "discovered_specs_dir": str(tmp_path),
            }
        }
    )

    assert enricher is not None
    assert enricher.config["discovered_specs_dir"] == str(tmp_path)
    assert set(enricher.discovery_data.paths) == {"/probe"}


def test_enrichment_without_loaded_data_fails_closed() -> None:
    """Calling the explicit enricher without evidence is never a successful no-op."""
    with pytest.raises(ValueError, match="requires loaded discovery data"):
        DiscoveryEnricher({}).enrich_with_discoveries({"paths": {}})


def test_discovery_enrichment_is_deterministic_and_emits_no_legacy_extensions() -> None:
    """Measured inputs produce stable canonical output without compatibility aliases."""
    measured = {
        "x-response-time-ms": 25,
        "x-response-time-percentiles": {
            "p50": 20,
            "p95": 35,
            "p99": 40,
            "sample_count": 50,
            "last_measured": "2026-08-02T00:00:00Z",
        },
    }
    discoveries = DiscoveryData(
        openapi_spec={"openapi": "3.0.3"},
        paths={"/probe": {"get": measured}},
        schemas={},
    )
    original = {
        "openapi": "3.0.3",
        "info": {"title": "Probe", "version": "1.0.0"},
        "paths": {"/probe": {"get": {"operationId": "getProbe"}}},
        "components": {"schemas": {}},
    }
    config = {
        "performance": {"add_response_times": True, "add_percentiles": True},
        "rate_limits": {"enabled": False},
        "errors": {"enabled": False},
    }

    first = DiscoveryEnricher(config).enrich_with_discoveries(copy.deepcopy(original), discoveries)
    second = DiscoveryEnricher(config).enrich_with_discoveries(copy.deepcopy(original), discoveries)

    assert first == second
    assert first["info"] == original["info"]
    operation = first["paths"]["/probe"]["get"]
    assert operation[X_F5XC_DISCOVERED_RESPONSE_TIME] == {
        "p50": 20,
        "p95": 35,
        "p99": 40,
        "sample_count": 50,
        "last_measured": "2026-08-02T00:00:00Z",
    }
    assert "x-discovered-response-time-ms" not in operation
    assert "x-discovered-sample-size" not in operation


def test_discovery_enricher_rejects_wrapped_compatibility_config() -> None:
    """There is one configuration shape, not a legacy wrapped alternative."""
    with pytest.raises(ValueError, match="section itself"):
        DiscoveryEnricher({"discovery_enrichment": {}})
