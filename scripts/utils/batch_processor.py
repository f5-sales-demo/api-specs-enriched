# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Batch processor for memory-efficient spec processing.

Processes OpenAPI specifications in batches to reduce memory pressure during
pipeline execution. Instead of loading all 270+ specs into memory at once,
processes them in configurable batches with disk caching between batches.

This is Phase 2 of Issue #390: Memory Optimization.

Example:
    >>> processor = BatchSpecProcessor(batch_size=20)
    >>> processed_paths = processor.process_batch(
    ...     spec_files,
    ...     enrich_func=enrich_spec,
    ...     normalize_func=normalize_spec,
    ...     config=config,
    ... )
"""

import gc
import json
import logging
import tempfile
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


class BatchError(TypedDict):
    """One per-file processing failure."""

    file: str
    error: str


class BatchStats(TypedDict):
    """Counters and failures collected across batch processing."""

    batches_processed: int
    specs_processed: int
    specs_failed: int
    errors: list[BatchError]
    cache_writes: int
    cache_reads: int
    gc_collections: int


def _process_spec_file(
    args: tuple[
        Path,
        Path,
        Callable[[dict, dict], tuple[dict, dict]],
        Callable[[dict, dict], tuple[dict, dict]],
        dict[str, Any],
    ],
) -> tuple[str, Path | None, str | None]:
    """Process one spec in a worker and persist its deterministic cache entry."""
    spec_file, cache_path, enrich_func, normalize_func, config = args
    try:
        with spec_file.open() as file_handle:
            spec = json.load(file_handle)
        spec, _ = enrich_func(spec, config)
        spec, _ = normalize_func(spec, config)
        with cache_path.open("w") as file_handle:
            json.dump(spec, file_handle, indent=2)
        return spec_file.name, cache_path, None
    except Exception as exc:  # Workers return failures so the parent can aggregate them.
        return spec_file.name, None, str(exc)


class BatchSpecProcessor:
    """Process specs in batches to reduce memory pressure.

    Provides batch processing with disk caching to limit peak memory usage.
    Instead of loading all specs into memory, processes them in batches and
    caches results to disk, allowing memory to be freed between batches.

    Attributes:
        batch_size: Number of specs to process per batch
        cache_dir: Temporary directory for caching processed specs
        stats: Processing statistics (batches, specs, cache hits/misses)
    """

    def __init__(
        self,
        batch_size: int = 20,
        cache_dir: Path | None = None,
        worker_count: int = 1,
    ) -> None:
        """Initialize batch processor.

        Args:
            batch_size: Number of specs to process per batch (default: 20)
            cache_dir: Optional custom cache directory (defaults to system temp)
            worker_count: Maximum number of worker processes (default: 1)
        """
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        self.batch_size = batch_size
        self.worker_count = worker_count
        self.cache_dir = (
            Path(cache_dir)
            if cache_dir is not None
            else Path(tempfile.mkdtemp(prefix="f5xc_spec_cache-"))
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.stats: BatchStats = {
            "batches_processed": 0,
            "specs_processed": 0,
            "specs_failed": 0,
            "errors": [],
            "cache_writes": 0,
            "cache_reads": 0,
            "gc_collections": 0,
        }

        logger.info(
            "Initialized BatchSpecProcessor: batch_size=%d, worker_count=%d, cache_dir=%s",
            batch_size,
            worker_count,
            self.cache_dir,
        )

    def process_batch(
        self,
        spec_files: list[Path],
        enrich_func: Callable[[dict, dict], tuple[dict, dict]],
        normalize_func: Callable[[dict, dict], tuple[dict, dict]],
        config: dict[str, Any],
    ) -> dict[str, Path]:
        """Process specs in batches, caching to disk.

        Processes spec files in batches to reduce memory pressure. After each
        batch, processed specs are written to disk cache and memory is freed
        via garbage collection.

        Args:
            spec_files: List of spec file paths to process
            enrich_func: Enrichment function (spec, config) -> (enriched_spec, stats)
            normalize_func: Normalization function (spec, config) -> (normalized_spec, stats)
            config: Pipeline configuration dictionary

        Returns:
            Dictionary mapping original filenames to cached file paths

        Example:
            >>> processor = BatchSpecProcessor(batch_size=20)
            >>> cache_paths = processor.process_batch(
            ...     spec_files,
            ...     enrich_spec,
            ...     normalize_spec,
            ...     config,
            ... )
            >>> # Load cached specs for merging
            >>> for filename, cache_path in cache_paths.items():
            ...     with cache_path.open() as f:
            ...         spec = json.load(f)
        """
        processed_paths: dict[str, Path] = {}
        total_specs = len(spec_files)
        continue_on_error = config.get("processing", {}).get("continue_on_error", True)

        logger.info(
            "Processing %d specs in batches of %d with %d worker(s)",
            total_specs,
            self.batch_size,
            self.worker_count,
        )

        executor = (
            ProcessPoolExecutor(max_workers=min(self.worker_count, total_specs))
            if self.worker_count > 1 and total_specs
            else None
        )
        try:
            for batch_idx in range(0, total_specs, self.batch_size):
                batch = spec_files[batch_idx : batch_idx + self.batch_size]
                batch_num = (batch_idx // self.batch_size) + 1
                tasks = [
                    (
                        spec_file,
                        self._get_cache_path(spec_file),
                        enrich_func,
                        normalize_func,
                        config,
                    )
                    for spec_file in batch
                ]
                results = (
                    executor.map(_process_spec_file, tasks)
                    if executor
                    else map(_process_spec_file, tasks)
                )

                for filename, cache_path, error in results:
                    if error is not None:
                        logger.error("Error processing %s: %s", filename, error)
                        self.stats["specs_failed"] += 1
                        self.stats["errors"].append({"file": filename, "error": error})
                        if not continue_on_error:
                            raise RuntimeError(f"Failed to process {filename}: {error}")
                        continue
                    if cache_path is None:
                        raise RuntimeError(f"Worker returned no cache path for {filename}")
                    processed_paths[filename] = cache_path
                    self.stats["cache_writes"] += 1
                    self.stats["specs_processed"] += 1

                collected = gc.collect()
                self.stats["gc_collections"] += 1
                self.stats["batches_processed"] += 1
                logger.info(
                    "Batch %d complete: %d specs, %d objects collected",
                    batch_num,
                    len(batch),
                    collected,
                )
        finally:
            if executor:
                executor.shutdown(cancel_futures=True)

        logger.info(
            "Batch processing complete: %d succeeded, %d failed in %d batches",
            self.stats["specs_processed"],
            self.stats["specs_failed"],
            self.stats["batches_processed"],
        )

        return processed_paths

    def load_cached_spec(self, cache_path: Path) -> dict[str, Any]:
        """Load a spec from cache.

        Args:
            cache_path: Path to cached spec file

        Returns:
            Loaded spec dictionary
        """
        with cache_path.open() as f:
            spec = json.load(f)

        self.stats["cache_reads"] += 1
        return spec

    def cleanup_cache(self) -> None:
        """Remove all cached files from disk.

        Call this after pipeline completion to clean up temporary files.
        """
        if not self.cache_dir.exists():
            return

        cache_files = list(self.cache_dir.glob("*.json"))
        for cache_file in cache_files:
            try:
                cache_file.unlink()
            except OSError:
                logger.warning("Failed to delete cache file: %s", cache_file)

        try:
            self.cache_dir.rmdir()
        except OSError:
            logger.warning("Failed to delete cache directory: %s", self.cache_dir)

        logger.info("Cache cleanup complete: %d files removed", len(cache_files))

    def process_discovery_reconciliation_batch(
        self,
        cache_paths: dict[str, Path],
        discovery_func: Callable[[dict], dict] | None = None,
        reconcile_func: Callable[[dict], tuple[dict, dict]] | None = None,
    ) -> dict[str, Path]:
        """Process discovery and reconciliation in batches without accumulating in memory.

        Phase 3 optimization: Processes cached specs in batches, applying discovery
        enrichment and constraint reconciliation, then writing back to cache immediately.
        This avoids loading all specs into memory at once.

        Args:
            cache_paths: Dictionary mapping filenames to cache paths from Phase 2
            discovery_func: Optional discovery enrichment function (spec) -> enriched_spec
            reconcile_func: Optional reconciliation function (spec) -> (reconciled_spec, report)

        Returns:
            Dictionary mapping filenames to final cache paths (same as input for Phase 3)

        Example:
            >>> # After Phase 2 batch processing
            >>> final_paths = processor.process_discovery_reconciliation_batch(
            ...     cache_paths,
            ...     discovery_func=lambda s: discovery_enricher.enrich_with_discoveries(s, data),
            ...     reconcile_func=lambda s: reconciler.reconcile_spec(s),
            ... )
        """
        if not discovery_func and not reconcile_func:
            logger.warning("No discovery or reconciliation functions provided, skipping")
            return cache_paths

        total_specs = len(cache_paths)
        logger.info(
            "Processing discovery/reconciliation for %d specs in batches of %d",
            total_specs,
            self.batch_size,
        )

        processed_paths: dict[str, Path] = {}
        cache_items = list(cache_paths.items())

        # Process in batches
        for batch_idx in range(0, total_specs, self.batch_size):
            batch = cache_items[batch_idx : batch_idx + self.batch_size]
            batch_num = (batch_idx // self.batch_size) + 1

            logger.info(
                "Processing discovery/reconciliation batch %d (%d specs)",
                batch_num,
                len(batch),
            )

            for filename, cache_path in batch:
                try:
                    # Load cached spec
                    spec = self.load_cached_spec(cache_path)

                    # Apply discovery enrichment if provided
                    if discovery_func:
                        spec = discovery_func(spec)

                    # Apply constraint reconciliation if provided
                    if reconcile_func:
                        spec, _ = reconcile_func(spec)

                    # Write back to cache immediately (overwrite existing)
                    with cache_path.open("w") as f:
                        json.dump(spec, f, indent=2)

                    processed_paths[filename] = cache_path
                    self.stats["cache_writes"] += 1

                    # Explicit cleanup
                    del spec

                except Exception:
                    logger.exception("Error processing discovery/reconciliation for %s", filename)
                    # Continue processing other specs
                    # Keep original cache path if processing fails
                    processed_paths[filename] = cache_path

            # Garbage collection after each batch
            collected = gc.collect()
            self.stats["gc_collections"] += 1
            self.stats["batches_processed"] += 1

            logger.info(
                "Discovery/reconciliation batch %d complete: %d specs, %d objects collected",
                batch_num,
                len(batch),
                collected,
            )

        logger.info(
            "Discovery/reconciliation complete: %d specs processed",
            len(processed_paths),
        )

        return processed_paths

    def get_stats(self) -> BatchStats:
        """Get processing statistics.

        Returns:
            Dictionary with processing metrics
        """
        return self.stats.copy()

    def _get_cache_path(self, spec_file: Path) -> Path:
        """Get cache file path for a spec file.

        Args:
            spec_file: Original spec file path

        Returns:
            Path to cache file
        """
        # Ensure cache directory exists before returning path
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_filename = f"{spec_file.stem}_processed.json"
        return self.cache_dir / cache_filename
