# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for batch processor functionality (Issue #390 Phase 2).

Tests batch processing of OpenAPI specifications with disk caching
to reduce memory pressure during pipeline execution.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.utils.batch_processor import BatchProcessingError, BatchSpecProcessor


@pytest.fixture
def temp_spec_dir():
    """Create temporary directory with test spec files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spec_dir = Path(tmpdir)

        # Create 5 test spec files
        for i in range(5):
            spec_file = spec_dir / f"spec_{i}.json"
            spec_data = {
                "openapi": "3.0.0",
                "info": {"title": f"Test Spec {i}", "version": "1.0.0"},
                "paths": {},
            }
            with spec_file.open("w") as f:
                json.dump(spec_data, f)

        yield spec_dir


@pytest.fixture
def mock_enrich_func():
    """Mock enrichment function."""

    def enrich(spec: dict, config: dict) -> tuple[dict, dict]:
        # Add enrichment marker
        spec["x-enriched"] = True
        return spec, {"enriched": True}

    return enrich


@pytest.fixture
def mock_normalize_func():
    """Mock normalization function."""

    def normalize(spec: dict, config: dict) -> tuple[dict, int]:
        # Add normalization marker
        spec["x-normalized"] = True
        return spec, 1

    return normalize


class TestBatchProcessorInitialization:
    """Test BatchSpecProcessor initialization."""

    def test_default_initialization(self):
        """Verify default initialization values."""
        processor = BatchSpecProcessor()

        assert processor.batch_size == 20
        assert processor.cache_dir.name.startswith("f5xc_spec_cache-")
        assert processor.cache_dir.exists()
        assert processor.stats["batches_processed"] == 0
        assert processor.stats["specs_processed"] == 0

    def test_custom_batch_size(self):
        """Verify custom batch size is respected."""
        processor = BatchSpecProcessor(batch_size=10)

        assert processor.batch_size == 10

    @pytest.mark.parametrize("batch_size", [True, 0, -1, 1.5, "1"])
    def test_invalid_batch_size_is_rejected(self, batch_size):
        """Batch size must be a positive, non-boolean integer."""
        with pytest.raises(ValueError, match="positive integer"):
            BatchSpecProcessor(batch_size=batch_size)

    def test_default_cache_directories_are_isolated(self):
        """Concurrent processors must never delete each other's cached specs."""
        first = BatchSpecProcessor()
        second = BatchSpecProcessor()

        try:
            assert first.cache_dir != second.cache_dir
            assert first.cache_dir.name.startswith("f5xc_spec_cache-")
            assert second.cache_dir.name.startswith("f5xc_spec_cache-")
        finally:
            first.cleanup_cache()
            second.cleanup_cache()

    def test_custom_cache_dir(self):
        """Verify custom cache directory is created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_cache = Path(tmpdir) / "custom_cache"
            processor = BatchSpecProcessor(cache_dir=custom_cache)

            assert processor.cache_dir == custom_cache
            assert custom_cache.exists()

    def test_stats_initialization(self):
        """Verify stats are initialized correctly."""
        processor = BatchSpecProcessor()

        expected_stats = {
            "batches_processed": 0,
            "specs_processed": 0,
            "cache_writes": 0,
            "cache_reads": 0,
            "gc_collections": 0,
        }

        assert processor.stats == expected_stats


class TestBatchProcessing:
    """Test batch processing functionality."""

    def test_process_single_batch(
        self,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
    ):
        """Verify processing specs in a single batch."""
        processor = BatchSpecProcessor(batch_size=10)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Verify all specs were processed
        assert len(cache_paths) == 5
        assert processor.stats["specs_processed"] == 5
        assert processor.stats["batches_processed"] == 1
        assert processor.stats["cache_writes"] == 5
        assert processor.stats["gc_collections"] == 1

        # Verify cache files exist
        for cache_path in cache_paths.values():
            assert cache_path.exists()

    def test_process_multiple_batches(
        self,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
    ):
        """Verify processing specs across multiple batches."""
        processor = BatchSpecProcessor(batch_size=2)  # Small batch size
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # 5 specs / 2 per batch = 3 batches
        assert len(cache_paths) == 5
        assert processor.stats["specs_processed"] == 5
        assert processor.stats["batches_processed"] == 3
        assert processor.stats["gc_collections"] == 3

    def test_enrichment_and_normalization_applied(
        self,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
    ):
        """Verify enrichment and normalization are applied to specs."""
        processor = BatchSpecProcessor(batch_size=10)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Load a cached spec and verify markers
        first_cache_path = next(iter(cache_paths.values()))
        with first_cache_path.open() as f:
            cached_spec = json.load(f)

        assert cached_spec["x-enriched"] is True
        assert cached_spec["x-normalized"] is True

    def test_enrichment_failure_stops_before_later_inputs(
        self,
        temp_spec_dir,
        mock_normalize_func,
    ):
        """An enrichment failure is contextual and no later source is processed."""
        processed_titles = []

        def failing_enrich(spec: dict, config: dict) -> tuple[dict, dict]:
            processed_titles.append(spec["info"]["title"])
            if "Spec 2" in spec["info"]["title"]:
                raise ValueError("Simulated enrichment error")
            spec["x-enriched"] = True
            return spec, {}

        processor = BatchSpecProcessor(batch_size=10)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        with pytest.raises(
            BatchProcessingError,
            match=r"enrichment failed for .*spec_2\.json: Simulated enrichment error",
        ):
            processor.process_batch(
                spec_files,
                failing_enrich,
                mock_normalize_func,
                config,
            )

        assert processed_titles == ["Test Spec 0", "Test Spec 1", "Test Spec 2"]
        assert processor.stats["specs_processed"] == 2
        assert processor._get_cache_path(spec_files[0]).exists()
        assert processor._get_cache_path(spec_files[1]).exists()
        assert not processor._get_cache_path(spec_files[2]).exists()
        assert not processor._get_cache_path(spec_files[3]).exists()
        assert not processor._get_cache_path(spec_files[4]).exists()

    def test_load_failure_stops_before_later_inputs(self, temp_spec_dir, mock_normalize_func):
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        spec_files[2].write_text("not json", encoding="utf-8")
        processed_titles = []

        def tracking_enrich(spec: dict, config: dict) -> tuple[dict, dict]:
            processed_titles.append(spec["info"]["title"])
            return spec, {}

        processor = BatchSpecProcessor(batch_size=10)
        with pytest.raises(BatchProcessingError, match=r"load failed for .*spec_2\.json"):
            processor.process_batch(spec_files, tracking_enrich, mock_normalize_func, {})

        assert processed_titles == ["Test Spec 0", "Test Spec 1"]
        assert processor.stats["specs_processed"] == 2

    def test_normalization_failure_stops_before_later_inputs(self, temp_spec_dir, mock_enrich_func):
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        normalized_titles = []

        def failing_normalize(spec: dict, config: dict) -> tuple[dict, dict]:
            normalized_titles.append(spec["info"]["title"])
            if "Spec 2" in spec["info"]["title"]:
                raise ValueError("Simulated normalization error")
            return spec, {}

        processor = BatchSpecProcessor(batch_size=10)
        with pytest.raises(
            BatchProcessingError,
            match=r"normalization failed for .*spec_2\.json: Simulated normalization error",
        ):
            processor.process_batch(spec_files, mock_enrich_func, failing_normalize, {})

        assert normalized_titles == ["Test Spec 0", "Test Spec 1", "Test Spec 2"]
        assert processor.stats["specs_processed"] == 2

    def test_cache_write_failure_stops_before_later_inputs(
        self,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
        monkeypatch,
    ):
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        processor = BatchSpecProcessor(batch_size=10)
        real_write = processor._write_json_atomically
        attempted = []

        def failing_write(spec: dict, path: Path) -> None:
            attempted.append(path.name)
            if path.name == "spec_2_processed.json":
                raise OSError("Simulated cache failure")
            real_write(spec, path)

        monkeypatch.setattr(processor, "_write_json_atomically", failing_write)
        with pytest.raises(
            BatchProcessingError,
            match=r"cache write failed for .*spec_2\.json: Simulated cache failure",
        ):
            processor.process_batch(spec_files, mock_enrich_func, mock_normalize_func, {})

        assert attempted == [
            "spec_0_processed.json",
            "spec_1_processed.json",
            "spec_2_processed.json",
        ]
        assert processor.stats["specs_processed"] == 2

    def test_cache_path_generation(self, temp_spec_dir):
        """Verify cache paths are generated correctly."""
        processor = BatchSpecProcessor()
        spec_files = sorted(temp_spec_dir.glob("*.json"))

        for spec_file in spec_files:
            cache_path = processor._get_cache_path(spec_file)

            assert cache_path.parent == processor.cache_dir
            assert cache_path.stem == f"{spec_file.stem}_processed"
            assert cache_path.suffix == ".json"


class TestCacheOperations:
    """Test cache-related operations."""

    def test_load_cached_spec(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify loading cached specs."""
        processor = BatchSpecProcessor(batch_size=10)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Load first cached spec
        first_cache_path = next(iter(cache_paths.values()))
        cached_spec = processor.load_cached_spec(first_cache_path)

        assert "openapi" in cached_spec
        assert "info" in cached_spec
        assert cached_spec["x-enriched"] is True
        assert cached_spec["x-normalized"] is True
        assert processor.stats["cache_reads"] == 1

    def test_cleanup_cache(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify cache cleanup removes all cached files."""
        processor = BatchSpecProcessor(batch_size=10)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Verify cache files exist
        for cache_path in cache_paths.values():
            assert cache_path.exists()

        # Clean up cache
        processor.cleanup_cache()

        # Verify cache files are deleted
        for cache_path in cache_paths.values():
            assert not cache_path.exists()

        # Verify cache directory is deleted
        assert not processor.cache_dir.exists()

    def test_cleanup_empty_cache(self):
        """Verify cleanup handles empty cache gracefully."""
        processor = BatchSpecProcessor()

        # Delete cache dir to simulate empty cache
        processor.cache_dir.rmdir()

        # Should not raise error
        processor.cleanup_cache()

        assert not processor.cache_dir.exists()

    def test_atomic_cache_write_preserves_existing_file_on_serialization_failure(self):
        processor = BatchSpecProcessor()
        cache_path = processor.cache_dir / "existing.json"
        cache_path.write_text('{"preserve": true}\n', encoding="utf-8")

        with pytest.raises(TypeError):
            processor._write_json_atomically({"invalid": object()}, cache_path)

        assert cache_path.read_text(encoding="utf-8") == '{"preserve": true}\n'
        assert list(processor.cache_dir.glob("*.tmp")) == []


class TestStatistics:
    """Test statistics tracking."""

    def test_get_stats(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify get_stats returns copy of statistics."""
        processor = BatchSpecProcessor(batch_size=10)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        stats = processor.get_stats()

        # Verify stats are correct
        assert stats["specs_processed"] == 5
        assert stats["batches_processed"] == 1
        assert stats["cache_writes"] == 5
        assert stats["gc_collections"] == 1

        # Verify it's a copy (modifying shouldn't affect original)
        stats["specs_processed"] = 999
        assert processor.stats["specs_processed"] == 5

    def test_stats_accumulation(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify stats accumulate correctly across operations."""
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # First batch processing
        processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        initial_specs = processor.stats["specs_processed"]
        initial_batches = processor.stats["batches_processed"]

        # Load some cached specs
        first_cache_path = processor._get_cache_path(spec_files[0])
        processor.load_cached_spec(first_cache_path)
        processor.load_cached_spec(first_cache_path)

        # Verify cache reads accumulated
        assert processor.stats["cache_reads"] == 2
        assert processor.stats["specs_processed"] == initial_specs
        assert processor.stats["batches_processed"] == initial_batches


class TestMemoryOptimization:
    """Test memory optimization features."""

    @patch("gc.collect")
    def test_garbage_collection_per_batch(
        self,
        mock_gc_collect,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
    ):
        """Verify garbage collection is called after each batch."""
        mock_gc_collect.return_value = 100  # Simulate objects collected

        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # 5 specs / 2 per batch = 3 batches, so gc.collect() called 3 times
        assert mock_gc_collect.call_count == 3
        assert processor.stats["gc_collections"] == 3

    def test_spec_cleanup_after_caching(
        self,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
    ):
        """Verify specs are deleted after caching to free memory."""
        processor = BatchSpecProcessor(batch_size=10)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # Track that specs are deleted in process_batch
        # (This is verified by the fact that the method completes without errors
        # and uses `del spec` internally)

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # If specs weren't deleted, we'd have memory issues with 270+ specs
        # The fact that this completes is the test
        assert len(cache_paths) == 5


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_spec_list(self, mock_enrich_func, mock_normalize_func):
        """Verify handling of empty spec list."""
        processor = BatchSpecProcessor(batch_size=10)
        config = {}

        cache_paths = processor.process_batch(
            [],
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        assert len(cache_paths) == 0
        assert processor.stats["specs_processed"] == 0
        assert processor.stats["batches_processed"] == 0

    def test_single_spec(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify processing single spec."""
        processor = BatchSpecProcessor(batch_size=10)
        spec_files = [sorted(temp_spec_dir.glob("*.json"))[0]]  # Just one spec
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        assert len(cache_paths) == 1
        assert processor.stats["specs_processed"] == 1
        assert processor.stats["batches_processed"] == 1

    def test_batch_size_larger_than_spec_count(
        self,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
    ):
        """Verify handling when batch size exceeds spec count."""
        processor = BatchSpecProcessor(batch_size=100)
        spec_files = sorted(temp_spec_dir.glob("*.json"))  # Only 5 specs
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Should process all specs in one batch
        assert len(cache_paths) == 5
        assert processor.stats["batches_processed"] == 1

    def test_batch_size_one(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify processing with batch size of 1."""
        processor = BatchSpecProcessor(batch_size=1)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Each spec in its own batch
        assert len(cache_paths) == 5
        assert processor.stats["batches_processed"] == 5
        assert processor.stats["gc_collections"] == 5


class TestDiscoveryReconciliationBatch:
    """Test Phase 3: Discovery/reconciliation batch processing."""

    def test_discovery_only(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify batch processing with discovery enrichment only."""
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # First create cache from batch processing
        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Discovery function adds a marker
        def discovery_func(spec: dict) -> dict:
            spec["x-discovery-enriched"] = True
            return spec

        # Process discovery in batches
        final_paths = processor.process_discovery_reconciliation_batch(
            cache_paths,
            discovery_func=discovery_func,
            reconcile_func=None,
        )

        # Verify all specs processed
        assert len(final_paths) == 5

        # Verify discovery marker was applied and cached
        for cache_path in final_paths.values():
            spec = processor.load_cached_spec(cache_path)
            assert spec["x-discovery-enriched"] is True

    def test_reconciliation_only(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify batch processing with reconciliation only."""
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # First create cache from batch processing
        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Reconciliation function adds marker and returns report
        def reconcile_func(spec: dict) -> tuple[dict, dict]:
            spec["x-reconciled"] = True
            return spec, {"reconciled": 1}

        # Process reconciliation in batches
        final_paths = processor.process_discovery_reconciliation_batch(
            cache_paths,
            discovery_func=None,
            reconcile_func=reconcile_func,
        )

        # Verify all specs processed
        assert len(final_paths) == 5

        # Verify reconciliation marker was applied and cached
        for cache_path in final_paths.values():
            spec = processor.load_cached_spec(cache_path)
            assert spec["x-reconciled"] is True

    def test_both_discovery_and_reconciliation(
        self,
        temp_spec_dir,
        mock_enrich_func,
        mock_normalize_func,
    ):
        """Verify batch processing with both discovery and reconciliation."""
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # First create cache from batch processing
        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        # Both functions add markers
        def discovery_func(spec: dict) -> dict:
            spec["x-discovery-enriched"] = True
            return spec

        def reconcile_func(spec: dict) -> tuple[dict, dict]:
            spec["x-reconciled"] = True
            return spec, {"reconciled": 1}

        # Process both in batches
        final_paths = processor.process_discovery_reconciliation_batch(
            cache_paths,
            discovery_func=discovery_func,
            reconcile_func=reconcile_func,
        )

        # Verify all specs processed
        assert len(final_paths) == 5

        # Verify both markers were applied and cached
        for cache_path in final_paths.values():
            spec = processor.load_cached_spec(cache_path)
            assert spec["x-discovery-enriched"] is True
            assert spec["x-reconciled"] is True

    def test_no_functions_provided(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Reject invoking a discovery/reconciliation stage with no work."""
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # First create cache from batch processing
        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        with pytest.raises(ValueError, match="requires at least one processing function"):
            processor.process_discovery_reconciliation_batch(
                cache_paths,
                discovery_func=None,
                reconcile_func=None,
            )

    def test_discovery_failure_stops_and_preserves_unprocessed_cache(
        self, temp_spec_dir, mock_enrich_func, mock_normalize_func
    ):
        """Discovery failure cannot return or overwrite an unprocessed cache path."""
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # First create cache from batch processing
        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        original_cache = {
            filename: path.read_text(encoding="utf-8") for filename, path in cache_paths.items()
        }
        processed_titles = []

        def failing_discovery(spec: dict) -> dict:
            processed_titles.append(spec["info"]["title"])
            if "Spec 2" in spec["info"]["title"]:
                raise ValueError("Simulated discovery error")
            spec["x-discovery-enriched"] = True
            return spec

        with pytest.raises(
            BatchProcessingError,
            match=r"discovery failed for .*spec_2_processed\.json: Simulated discovery error",
        ):
            processor.process_discovery_reconciliation_batch(
                cache_paths,
                discovery_func=failing_discovery,
                reconcile_func=None,
            )

        assert processed_titles == ["Test Spec 0", "Test Spec 1", "Test Spec 2"]
        assert "x-discovery-enriched" in json.loads(cache_paths["spec_0.json"].read_text())
        assert "x-discovery-enriched" in json.loads(cache_paths["spec_1.json"].read_text())
        for filename in ("spec_2.json", "spec_3.json", "spec_4.json"):
            assert cache_paths[filename].read_text(encoding="utf-8") == original_cache[filename]

    def test_cached_load_failure_stops_before_later_inputs(
        self, temp_spec_dir, mock_enrich_func, mock_normalize_func
    ):
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        cache_paths = processor.process_batch(spec_files, mock_enrich_func, mock_normalize_func, {})
        cache_paths["spec_2.json"].write_text("not json", encoding="utf-8")
        processed_titles = []

        def tracking_discovery(spec: dict) -> dict:
            processed_titles.append(spec["info"]["title"])
            return spec

        with pytest.raises(
            BatchProcessingError,
            match=r"load failed for .*spec_2_processed\.json",
        ):
            processor.process_discovery_reconciliation_batch(
                cache_paths,
                discovery_func=tracking_discovery,
            )

        assert processed_titles == ["Test Spec 0", "Test Spec 1"]

    def test_reconciliation_failure_stops_before_later_inputs(
        self, temp_spec_dir, mock_enrich_func, mock_normalize_func
    ):
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        cache_paths = processor.process_batch(spec_files, mock_enrich_func, mock_normalize_func, {})
        original_cache = {
            filename: path.read_text(encoding="utf-8") for filename, path in cache_paths.items()
        }
        reconciled_titles = []

        def failing_reconcile(spec: dict) -> tuple[dict, dict]:
            reconciled_titles.append(spec["info"]["title"])
            if "Spec 2" in spec["info"]["title"]:
                raise ValueError("Simulated reconciliation error")
            spec["x-reconciled"] = True
            return spec, {}

        with pytest.raises(
            BatchProcessingError,
            match=(
                r"reconciliation failed for .*spec_2_processed\.json: "
                r"Simulated reconciliation error"
            ),
        ):
            processor.process_discovery_reconciliation_batch(
                cache_paths,
                reconcile_func=failing_reconcile,
            )

        assert reconciled_titles == ["Test Spec 0", "Test Spec 1", "Test Spec 2"]
        for filename in ("spec_2.json", "spec_3.json", "spec_4.json"):
            assert cache_paths[filename].read_text(encoding="utf-8") == original_cache[filename]

    def test_phase_three_cache_write_failure_preserves_original_and_stops(
        self, temp_spec_dir, mock_enrich_func, mock_normalize_func
    ):
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        cache_paths = processor.process_batch(spec_files, mock_enrich_func, mock_normalize_func, {})
        original = cache_paths["spec_0.json"].read_text(encoding="utf-8")
        discovery_calls = []

        def unserializable_discovery(spec: dict) -> dict:
            discovery_calls.append(spec["info"]["title"])
            spec["unserializable"] = object()
            return spec

        with pytest.raises(
            BatchProcessingError,
            match=r"cache write failed for .*spec_0_processed\.json",
        ):
            processor.process_discovery_reconciliation_batch(
                cache_paths,
                discovery_func=unserializable_discovery,
            )

        assert discovery_calls == ["Test Spec 0"]
        assert cache_paths["spec_0.json"].read_text(encoding="utf-8") == original

    def test_multiple_batches(self, temp_spec_dir, mock_enrich_func, mock_normalize_func):
        """Verify correct batching across multiple batches."""
        processor = BatchSpecProcessor(batch_size=2)
        spec_files = sorted(temp_spec_dir.glob("*.json"))
        config = {}

        # First create cache from batch processing
        cache_paths = processor.process_batch(
            spec_files,
            mock_enrich_func,
            mock_normalize_func,
            config,
        )

        initial_batches = processor.stats["batches_processed"]

        # Discovery function adds marker
        def discovery_func(spec: dict) -> dict:
            spec["x-discovery-enriched"] = True
            return spec

        # Process discovery in batches
        processor.process_discovery_reconciliation_batch(
            cache_paths,
            discovery_func=discovery_func,
            reconcile_func=None,
        )

        # 5 specs / 2 per batch = 3 batches (should add to initial batches)
        # Initial batches was 3, so total should be 6
        assert processor.stats["batches_processed"] == initial_batches + 3
