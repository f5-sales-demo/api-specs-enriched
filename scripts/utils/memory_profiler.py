# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Memory profiling utilities for pipeline optimization (Issue #390).

Provides memory tracking and reporting capabilities to identify
memory hotspots and validate optimization improvements.
"""

import gc
import json
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class MemoryCheckpoint:
    """Memory usage snapshot at a specific point in execution.

    Attributes:
        name: Checkpoint identifier
        current_mb: Current memory usage in MB
        peak_mb: Peak memory usage since tracking started in MB
        timestamp: When checkpoint was recorded
    """

    name: str
    current_mb: float
    peak_mb: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    elapsed_seconds: float = 0.0
    phase_duration_seconds: float = 0.0


@dataclass
class MemoryStats:
    """Aggregate memory statistics for a pipeline run.

    Attributes:
        started_at: When tracking started
        completed_at: When tracking completed
        peak_memory_mb: Peak memory usage during run
        checkpoints: List of memory checkpoints
        gc_collections: Garbage collection statistics
    """

    started_at: str
    completed_at: str | None = None
    peak_memory_mb: float = 0.0
    checkpoints: list[MemoryCheckpoint] = field(default_factory=list)
    gc_collections: dict[str, Any] = field(default_factory=dict)


class MemoryProfiler:
    """Memory profiling context manager for pipeline operations.

    Tracks memory usage throughout pipeline execution with checkpoint support.

    Example:
        with MemoryProfiler() as profiler:
            profiler.checkpoint("load_specs")
            # ... work ...
            profiler.checkpoint("enrich_specs")
            # ... work ...
            profiler.save_report(Path("reports/memory-profile.json"))
    """

    def __init__(self) -> None:
        """Initialize memory profiler."""
        self.stats = MemoryStats(
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._tracking = False
        self._started_monotonic = 0.0
        self._last_checkpoint_monotonic = 0.0

    def __enter__(self) -> "MemoryProfiler":
        """Enter context and start memory tracking."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context and stop memory tracking."""
        self.stop()

    def start(self) -> None:
        """Start memory tracking."""
        if not self._tracking:
            tracemalloc.start()
            gc.enable()
            self._started_monotonic = time.perf_counter()
            self._last_checkpoint_monotonic = self._started_monotonic
            self._tracking = True

    def stop(self) -> None:
        """Stop memory tracking."""
        if self._tracking:
            self.stats.completed_at = datetime.now(timezone.utc).isoformat()
            tracemalloc.stop()
            self._tracking = False

    def checkpoint(self, name: str, force_gc: bool = False) -> MemoryCheckpoint:
        """Record memory usage at a specific checkpoint.

        Args:
            name: Checkpoint identifier (e.g., "loaded_specs", "enriched")
            force_gc: Whether to force garbage collection before measuring

        Returns:
            MemoryCheckpoint with current memory stats
        """
        if force_gc:
            gc.collect()

        if not self._tracking:
            # Return empty checkpoint if not tracking
            return MemoryCheckpoint(name=name, current_mb=0.0, peak_mb=0.0)

        current, peak = tracemalloc.get_traced_memory()
        now = time.perf_counter()
        checkpoint = MemoryCheckpoint(
            name=name,
            current_mb=current / (1024 * 1024),
            peak_mb=peak / (1024 * 1024),
            elapsed_seconds=now - self._started_monotonic,
            phase_duration_seconds=now - self._last_checkpoint_monotonic,
        )
        self._last_checkpoint_monotonic = now

        self.stats.checkpoints.append(checkpoint)
        self.stats.peak_memory_mb = max(self.stats.peak_memory_mb, checkpoint.peak_mb)

        return checkpoint

    def get_gc_stats(self) -> dict[str, Any]:
        """Get garbage collection statistics.

        Returns:
            Dictionary with GC stats including collection counts per generation
        """
        stats = gc.get_stats()
        counts = gc.get_count()

        return {
            "collections": {
                "generation_0": counts[0] if len(counts) > 0 else 0,
                "generation_1": counts[1] if len(counts) > 1 else 0,
                "generation_2": counts[2] if len(counts) > 2 else 0,
            },
            "detailed_stats": stats or [],
        }

    def save_report(self, output_path: Path) -> None:
        """Save memory profiling report to JSON.

        Args:
            output_path: Path where report should be saved
        """
        # Update GC stats before saving
        self.stats.gc_collections = self.get_gc_stats()

        report = {
            "started_at": self.stats.started_at,
            "completed_at": self.stats.completed_at,
            "peak_memory_mb": round(self.stats.peak_memory_mb, 2),
            "checkpoints": [
                {
                    "name": cp.name,
                    "current_mb": round(cp.current_mb, 2),
                    "peak_mb": round(cp.peak_mb, 2),
                    "timestamp": cp.timestamp,
                    "elapsed_seconds": round(cp.elapsed_seconds, 6),
                    "phase_duration_seconds": round(cp.phase_duration_seconds, 6),
                }
                for cp in self.stats.checkpoints
            ],
            "gc_collections": self.stats.gc_collections,
            "summary": self._generate_summary(),
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w") as f:
            json.dump(report, f, indent=2)
            f.write("\n")

    def _generate_summary(self) -> dict[str, Any]:
        """Generate summary statistics from checkpoints.

        Returns:
            Dictionary with summary metrics like memory growth, largest increases
        """
        if len(self.stats.checkpoints) < 2:
            return {
                "total_checkpoints": len(self.stats.checkpoints),
                "memory_growth_mb": 0.0,
                "largest_increase": None,
            }

        checkpoints = self.stats.checkpoints
        first_current = checkpoints[0].current_mb
        last_current = checkpoints[-1].current_mb
        memory_growth = last_current - first_current

        # Find largest memory increase between consecutive checkpoints
        largest_increase = None
        max_delta = 0.0

        for i in range(1, len(checkpoints)):
            prev = checkpoints[i - 1]
            curr = checkpoints[i]
            delta = curr.current_mb - prev.current_mb

            if delta > max_delta:
                max_delta = delta
                largest_increase = {
                    "from": prev.name,
                    "to": curr.name,
                    "increase_mb": round(delta, 2),
                }

        return {
            "total_checkpoints": len(checkpoints),
            "memory_growth_mb": round(memory_growth, 2),
            "largest_increase": largest_increase,
            "average_checkpoint_memory_mb": round(
                sum(cp.current_mb for cp in checkpoints) / len(checkpoints),
                2,
            ),
        }


def setup_memory_tracking() -> None:
    """Initialize memory profiling for standalone use.

    Use this function when not using the MemoryProfiler context manager.
    Must be called before any checkpointing.
    """
    tracemalloc.start()
    gc.enable()


def checkpoint_memory(name: str) -> MemoryCheckpoint:
    """Record current memory usage (standalone function).

    Args:
        name: Checkpoint identifier

    Returns:
        MemoryCheckpoint with current stats

    Note:
        Requires setup_memory_tracking() to be called first
    """
    if not tracemalloc.is_tracing():
        return MemoryCheckpoint(name=name, current_mb=0.0, peak_mb=0.0)

    current, peak = tracemalloc.get_traced_memory()
    return MemoryCheckpoint(
        name=name,
        current_mb=current / (1024 * 1024),
        peak_mb=peak / (1024 * 1024),
    )


def save_memory_report(
    checkpoints: list[MemoryCheckpoint],
    output_path: Path,
) -> None:
    """Save memory report from checkpoint list (standalone function).

    Args:
        checkpoints: List of memory checkpoints
        output_path: Path where report should be saved
    """
    peak_mb = max((cp.peak_mb for cp in checkpoints), default=0.0)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "peak_memory_mb": round(peak_mb, 2),
        "checkpoints": [
            {
                "name": cp.name,
                "current_mb": round(cp.current_mb, 2),
                "peak_mb": round(cp.peak_mb, 2),
                "timestamp": cp.timestamp,
            }
            for cp in checkpoints
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
