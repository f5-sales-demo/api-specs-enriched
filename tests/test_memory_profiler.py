# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access,use-implicit-booleaness-not-comparison  # Tests inspect internals and explicit result shapes.

"""Unit tests for memory profiling utilities (Issue #390).

Tests verify that the memory profiler correctly tracks memory usage,
records checkpoints, and generates accurate reports.
"""

import json
import tempfile
from pathlib import Path

import pytest

from scripts.utils.memory_profiler import (
    MemoryCheckpoint,
    MemoryProfiler,
    MemoryStats,
    checkpoint_memory,
    save_memory_report,
    setup_memory_tracking,
)


class TestMemoryCheckpoint:
    """Test MemoryCheckpoint dataclass."""

    def test_checkpoint_creation(self):
        """Verify checkpoint creation with all fields."""
        checkpoint = MemoryCheckpoint(
            name="test_checkpoint",
            current_mb=100.5,
            peak_mb=150.75,
        )

        assert checkpoint.name == "test_checkpoint"
        assert checkpoint.current_mb == 100.5
        assert checkpoint.peak_mb == 150.75
        assert checkpoint.timestamp  # Should have auto-generated timestamp

    def test_checkpoint_timestamp_format(self):
        """Verify timestamp is ISO 8601 format."""
        checkpoint = MemoryCheckpoint(
            name="test",
            current_mb=10.0,
            peak_mb=20.0,
        )

        # Should be parseable as ISO format
        from datetime import datetime

        datetime.fromisoformat(checkpoint.timestamp.replace("+00:00", ""))


class TestMemoryStats:
    """Test MemoryStats dataclass."""

    def test_stats_creation(self):
        """Verify stats creation with defaults."""
        stats = MemoryStats(started_at="2026-01-14T00:00:00")

        assert stats.started_at == "2026-01-14T00:00:00"
        assert stats.completed_at is None
        assert stats.peak_memory_mb == 0.0
        assert stats.checkpoints == []
        assert stats.gc_collections == {}

    def test_stats_with_checkpoints(self):
        """Verify stats with checkpoint list."""
        checkpoint1 = MemoryCheckpoint("start", 10.0, 10.0)
        checkpoint2 = MemoryCheckpoint("end", 20.0, 25.0)

        stats = MemoryStats(
            started_at="2026-01-14T00:00:00",
            completed_at="2026-01-14T00:01:00",
            peak_memory_mb=25.0,
            checkpoints=[checkpoint1, checkpoint2],
        )

        assert len(stats.checkpoints) == 2
        assert stats.peak_memory_mb == 25.0


class TestMemoryProfiler:
    """Test MemoryProfiler context manager."""

    def test_profiler_initialization(self):
        """Verify profiler initializes correctly."""
        profiler = MemoryProfiler()

        assert profiler.stats is not None
        assert profiler.stats.started_at
        assert not profiler._tracking

    def test_context_manager_start_stop(self):
        """Verify context manager starts and stops tracking."""
        with MemoryProfiler() as profiler:
            assert profiler._tracking
            checkpoint = profiler.checkpoint("test")
            assert checkpoint.name == "test"

        # After context exit, tracking should be stopped
        assert not profiler._tracking

    def test_checkpoint_recording(self):
        """Verify checkpoints are recorded correctly."""
        with MemoryProfiler() as profiler:
            profiler.checkpoint("checkpoint1")
            profiler.checkpoint("checkpoint2")

            assert len(profiler.stats.checkpoints) == 2
            assert profiler.stats.checkpoints[0].name == "checkpoint1"
            assert profiler.stats.checkpoints[1].name == "checkpoint2"
            assert profiler.stats.checkpoints[0].elapsed_seconds >= 0
            assert profiler.stats.checkpoints[1].elapsed_seconds >= (
                profiler.stats.checkpoints[0].elapsed_seconds
            )
            assert profiler.stats.checkpoints[0].phase_duration_seconds >= 0
            assert profiler.stats.checkpoints[1].phase_duration_seconds >= 0

    def test_checkpoint_with_force_gc(self):
        """Verify checkpoint with forced garbage collection."""
        with MemoryProfiler() as profiler:
            # Create some garbage
            large_list = list(range(10000))
            del large_list

            checkpoint = profiler.checkpoint("after_gc", force_gc=True)
            assert checkpoint.name == "after_gc"
            assert isinstance(checkpoint.current_mb, float)

    def test_peak_memory_tracking(self):
        """Verify peak memory is tracked across checkpoints."""
        with MemoryProfiler() as profiler:
            profiler.checkpoint("start")

            # Allocate some memory
            data = [0] * 100000

            profiler.checkpoint("after_allocation")

            del data

            # Peak should be updated
            assert profiler.stats.peak_memory_mb > 0

    def test_checkpoint_without_tracking(self):
        """Verify checkpoint returns empty when tracking not started."""
        profiler = MemoryProfiler()
        # Don't start tracking
        checkpoint = profiler.checkpoint("test")

        assert checkpoint.name == "test"
        assert checkpoint.current_mb == 0.0
        assert checkpoint.peak_mb == 0.0

    def test_gc_stats_collection(self):
        """Verify garbage collection stats are collected."""
        with MemoryProfiler() as profiler:
            gc_stats = profiler.get_gc_stats()

            assert "collections" in gc_stats
            assert "generation_0" in gc_stats["collections"]
            assert "generation_1" in gc_stats["collections"]
            assert "generation_2" in gc_stats["collections"]
            assert "detailed_stats" in gc_stats

    def test_save_report(self):
        """Verify report is saved correctly to JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "test-memory-profile.json"

            with MemoryProfiler() as profiler:
                profiler.checkpoint("start")
                profiler.checkpoint("middle")
                profiler.checkpoint("end")

                profiler.save_report(output_path)

            # Verify file was created
            assert output_path.exists()

            # Verify JSON structure
            with output_path.open() as f:
                report = json.load(f)

            assert "started_at" in report
            assert "completed_at" in report
            assert "peak_memory_mb" in report
            assert "checkpoints" in report
            assert "gc_collections" in report
            assert "summary" in report

            # Verify checkpoints
            assert len(report["checkpoints"]) == 3
            assert report["checkpoints"][0]["name"] == "start"
            assert report["checkpoints"][1]["name"] == "middle"
            assert report["checkpoints"][2]["name"] == "end"
            assert report["checkpoints"][0]["elapsed_seconds"] >= 0
            assert report["checkpoints"][0]["phase_duration_seconds"] >= 0

            # Verify summary
            assert report["summary"]["total_checkpoints"] == 3
            assert "memory_growth_mb" in report["summary"]
            assert "largest_increase" in report["summary"]

    def test_summary_generation_single_checkpoint(self):
        """Verify summary with single checkpoint."""
        with MemoryProfiler() as profiler:
            profiler.checkpoint("only_one")

            summary = profiler._generate_summary()

            assert summary["total_checkpoints"] == 1
            assert summary["memory_growth_mb"] == 0.0
            assert summary["largest_increase"] is None

    def test_summary_generation_multiple_checkpoints(self):
        """Verify summary with multiple checkpoints."""
        with MemoryProfiler() as profiler:
            profiler.checkpoint("start")

            # Allocate memory
            data = [0] * 50000

            profiler.checkpoint("allocated")

            del data

            profiler.checkpoint("freed")

            summary = profiler._generate_summary()

            assert summary["total_checkpoints"] == 3
            assert summary["memory_growth_mb"] >= 0
            assert "largest_increase" in summary
            assert summary["average_checkpoint_memory_mb"] >= 0


class TestStandaloneFunctions:
    """Test standalone memory profiling functions."""

    def test_setup_memory_tracking(self):
        """Verify standalone memory tracking setup."""
        import tracemalloc

        # Stop if already running
        if tracemalloc.is_tracing():
            tracemalloc.stop()

        setup_memory_tracking()
        assert tracemalloc.is_tracing()

        # Cleanup
        tracemalloc.stop()

    def test_checkpoint_memory_standalone(self):
        """Verify standalone checkpoint function."""
        setup_memory_tracking()

        checkpoint = checkpoint_memory("standalone_test")

        assert checkpoint.name == "standalone_test"
        assert isinstance(checkpoint.current_mb, float)
        assert isinstance(checkpoint.peak_mb, float)
        assert checkpoint.current_mb >= 0
        assert checkpoint.peak_mb >= 0

        # Cleanup
        import tracemalloc

        tracemalloc.stop()

    def test_checkpoint_memory_without_setup(self):
        """Verify checkpoint returns empty when tracking not setup."""
        import tracemalloc

        # Ensure tracking is stopped
        if tracemalloc.is_tracing():
            tracemalloc.stop()

        checkpoint = checkpoint_memory("no_tracking")

        assert checkpoint.name == "no_tracking"
        assert checkpoint.current_mb == 0.0
        assert checkpoint.peak_mb == 0.0

    def test_save_memory_report_standalone(self):
        """Verify standalone save report function."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "standalone-report.json"

            checkpoint1 = MemoryCheckpoint("start", 10.0, 10.0)
            checkpoint2 = MemoryCheckpoint("middle", 20.0, 25.0)
            checkpoint3 = MemoryCheckpoint("end", 15.0, 25.0)

            checkpoints = [checkpoint1, checkpoint2, checkpoint3]

            save_memory_report(checkpoints, output_path)

            # Verify file was created
            assert output_path.exists()

            # Verify JSON structure
            with output_path.open() as f:
                report = json.load(f)

            assert "timestamp" in report
            assert "peak_memory_mb" in report
            assert "checkpoints" in report

            # Peak should be max of all checkpoints
            assert report["peak_memory_mb"] == 25.0

            # All checkpoints should be present
            assert len(report["checkpoints"]) == 3


class TestIntegration:
    """Integration tests for memory profiler."""

    def test_realistic_pipeline_simulation(self):
        """Simulate realistic pipeline memory usage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "pipeline-memory.json"

            with MemoryProfiler() as profiler:
                # Simulate pipeline stages
                profiler.checkpoint("pipeline_start")

                # Load specs (simulate)
                specs_data = [{"spec": i} for i in range(1000)]
                profiler.checkpoint("specs_loaded")

                # Process specs (simulate)
                processed = [str(spec) for spec in specs_data]
                profiler.checkpoint("specs_processed", force_gc=True)

                # Simulate merge step
                merged = {"merged": processed[:100]}
                profiler.checkpoint("specs_merged")

                del specs_data, processed, merged

                profiler.checkpoint("pipeline_complete")

                # Save report
                profiler.save_report(output_path)

            # Verify report
            with output_path.open() as f:
                report = json.load(f)

            # Should have 5 checkpoints
            assert len(report["checkpoints"]) == 5

            # Should have increasing memory up to processing
            checkpoint_names = [cp["name"] for cp in report["checkpoints"]]
            assert checkpoint_names == [
                "pipeline_start",
                "specs_loaded",
                "specs_processed",
                "specs_merged",
                "pipeline_complete",
            ]

            # Peak memory should be positive
            assert report["peak_memory_mb"] > 0

            # Summary should identify largest increase
            assert report["summary"]["largest_increase"] is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
