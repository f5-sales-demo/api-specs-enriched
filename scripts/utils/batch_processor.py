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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BatchProcessingError(RuntimeError):
    """One source failed a required batch-processing stage."""

    def __init__(self, stage: str, source: Path, error: Exception) -> None:
        """Identify the failed stage and exact source while preserving its cause."""
        self.stage = stage
        self.source = source
        super().__init__(f"{stage} failed for {source}: {error}")


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
    ) -> None:
        """Initialize batch processor.

        Args:
            batch_size: Number of specs to process per batch (default: 20)
            cache_dir: Optional custom cache directory (defaults to system temp)
        """
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        self.batch_size = batch_size
        self.cache_dir = (
            Path(cache_dir)
            if cache_dir is not None
            else Path(tempfile.mkdtemp(prefix="f5xc_spec_cache-"))
        )
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.stats = {
            "batches_processed": 0,
            "specs_processed": 0,
            "cache_writes": 0,
            "cache_reads": 0,
            "gc_collections": 0,
        }

        logger.info(
            "Initialized BatchSpecProcessor: batch_size=%d, cache_dir=%s",
            batch_size,
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
        self._validate_source_cache_targets(spec_files)

        logger.info("Processing %d specs in batches of %d", total_specs, self.batch_size)

        # Process specs in batches
        for batch_idx in range(0, total_specs, self.batch_size):
            batch = spec_files[batch_idx : batch_idx + self.batch_size]
            batch_num = (batch_idx // self.batch_size) + 1

            logger.info(
                "Processing batch %d (%d specs)",
                batch_num,
                len(batch),
            )

            for spec_file in batch:
                spec = self._run_stage("load", spec_file, self._load_json, spec_file)
                spec = self._run_stage(
                    "enrichment",
                    spec_file,
                    self._apply_config_transform,
                    enrich_func,
                    spec,
                    config,
                )
                spec = self._run_stage(
                    "normalization",
                    spec_file,
                    self._apply_config_transform,
                    normalize_func,
                    spec,
                    config,
                )

                cache_path = self._get_cache_path(spec_file)
                self._run_stage(
                    "cache write",
                    spec_file,
                    self._write_json_atomically,
                    spec,
                    cache_path,
                )

                processed_paths[spec_file.name] = cache_path
                self.stats["cache_writes"] += 1
                self.stats["specs_processed"] += 1

                del spec

            # Garbage collection after each batch
            collected = gc.collect()
            self.stats["gc_collections"] += 1
            self.stats["batches_processed"] += 1

            logger.info(
                "Batch %d complete: %d specs processed, %d objects collected",
                batch_num,
                len(batch),
                collected,
            )

        logger.info(
            "Batch processing complete: %d specs in %d batches",
            self.stats["specs_processed"],
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
        spec = self._load_json(cache_path)

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
        if discovery_func is None and reconcile_func is None:
            raise ValueError(
                "discovery/reconciliation batch requires at least one processing function"
            )
        self._validate_cached_paths(cache_paths)

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
                spec = self._run_stage("load", cache_path, self.load_cached_spec, cache_path)

                if discovery_func is not None:
                    spec = self._run_stage(
                        "discovery",
                        cache_path,
                        self._apply_discovery,
                        discovery_func,
                        spec,
                    )

                if reconcile_func is not None:
                    spec = self._run_stage(
                        "reconciliation",
                        cache_path,
                        self._apply_reconciliation,
                        reconcile_func,
                        spec,
                    )

                self._run_stage(
                    "cache write",
                    cache_path,
                    self._write_json_atomically,
                    spec,
                    cache_path,
                )

                processed_paths[filename] = cache_path
                self.stats["cache_writes"] += 1

                del spec

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

    def get_stats(self) -> dict[str, int]:
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

    def _validate_source_cache_targets(self, spec_files: list[Path]) -> None:
        targets: dict[Path, Path] = {}
        for spec_file in spec_files:
            target = self._get_cache_path(spec_file)
            if target in targets:
                previous = targets[target]
                raise ValueError(
                    f"source cache target collision: {previous} and {spec_file} both map to {target}"
                )
            targets[target] = spec_file

    def _validate_cached_paths(self, cache_paths: dict[str, Path]) -> None:
        seen: dict[Path, str] = {}
        cache_root = self.cache_dir.resolve()
        for filename, cache_path in cache_paths.items():
            path = Path(cache_path)
            if path.parent.resolve() != cache_root or path.suffix != ".json" or path.is_symlink():
                raise ValueError(
                    f"cache path for {filename!r} is outside the processor cache: {path}"
                )
            if path in seen:
                previous = seen[path]
                raise ValueError(f"duplicate cache path for {previous!r} and {filename!r}: {path}")
            seen[path] = filename

    @staticmethod
    def _run_stage(
        stage: str,
        source: Path,
        function: Callable[..., Any],
        *args: Any,
    ) -> Any:
        try:
            return function(*args)
        except Exception as error:
            raise BatchProcessingError(stage, source, error) from error

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open() as input_file:
            spec = json.load(input_file)
        if not isinstance(spec, dict):
            raise TypeError("specification root must be an object")
        return spec

    @staticmethod
    def _apply_config_transform(
        function: Callable[[dict, dict], tuple[dict, Any]],
        spec: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        transformed, _ = function(spec, config)
        if not isinstance(transformed, dict):
            raise TypeError("transformation must return a specification object")
        return transformed

    @staticmethod
    def _apply_discovery(function: Callable[[dict], dict], spec: dict[str, Any]) -> dict[str, Any]:
        transformed = function(spec)
        if not isinstance(transformed, dict):
            raise TypeError("discovery must return a specification object")
        return transformed

    @staticmethod
    def _apply_reconciliation(
        function: Callable[[dict], tuple[dict, dict]], spec: dict[str, Any]
    ) -> dict[str, Any]:
        transformed, _ = function(spec)
        if not isinstance(transformed, dict):
            raise TypeError("reconciliation must return a specification object")
        return transformed

    @staticmethod
    def _write_json_atomically(spec: dict[str, Any], cache_path: Path) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{cache_path.name}.",
                suffix=".tmp",
                dir=cache_path.parent,
                delete=False,
            ) as output_file:
                temporary_path = Path(output_file.name)
                json.dump(spec, output_file, indent=2)
                output_file.flush()
            temporary_path.replace(cache_path)
        except Exception as error:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    raise RuntimeError(
                        f"cache write failed and temporary cleanup failed: {cleanup_error}"
                    ) from error
            raise
