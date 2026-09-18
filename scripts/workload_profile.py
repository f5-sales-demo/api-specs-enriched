"""Create versioned workload timing and deterministic evidence artifacts.

The command is deliberately a wrapper: it neither changes the command being
measured nor selects a runner.  That keeps profiling safe to add to existing
fork-protected jobs and makes the resulting JSON useful for paired benchmarks.
"""

from __future__ import annotations

import argparse
import cProfile
import hashlib
import json
import runpy
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def canonical_json(value: Any) -> bytes:
    """Return the canonical representation used for all evidence digests."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256(value: bytes) -> str:
    """Return a prefixed SHA-256 digest."""
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Write canonical JSON, creating its parent directory when necessary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def tree_digest(paths: list[Path]) -> str:
    """Digest a sorted file manifest, independent of filesystem traversal order."""
    manifest = [
        {"path": path.as_posix(), "sha256": sha256(path.read_bytes())}
        for path in sorted(paths, key=lambda item: item.as_posix())
        if path.is_file()
    ]
    return sha256(canonical_json(manifest))


def run_workload(phase: str, command: list[str], output: Path) -> int:
    """Run one command and emit a schema-v1 phase measurement."""
    started = time.monotonic()
    completed = subprocess.run(command, check=False).returncode
    duration = time.monotonic() - started
    identity = {"schema_version": SCHEMA_VERSION, "phase": phase, "command": command}
    result = {
        **identity,
        "duration_seconds": round(duration, 6),
        "exit": {"code": completed},
        "identity_digest": sha256(canonical_json(identity)),
    }
    write_json(output, result)
    return completed


def profile_module(module: str, module_args: list[str], output: Path, stats: Path) -> int:
    """Run a Python module under cProfile and retain the same evidence schema."""
    started = time.monotonic()
    original_argv = sys.argv
    profiler = cProfile.Profile()
    code = 0
    try:
        sys.argv = [module, *module_args]
        profiler.enable()
        runpy.run_module(module, run_name="__main__")
    except SystemExit as exc:
        code = int(exc.code) if isinstance(exc.code, int) else 1
    finally:
        profiler.disable()
        sys.argv = original_argv
        stats.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(stats)
    duration = time.monotonic() - started
    identity = {"schema_version": SCHEMA_VERSION, "phase": module, "module": module}
    write_json(
        output,
        {
            **identity,
            "duration_seconds": round(duration, 6),
            "exit": {"code": code},
            "profile_stats": stats.as_posix(),
            "identity_digest": sha256(canonical_json(identity)),
        },
    )
    return code


def parse_args() -> argparse.Namespace:
    """Parse the workload wrapper command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    run = subparsers.add_parser("run", help="time an existing command")
    run.add_argument("--phase", required=True)
    run.add_argument("--output", required=True, type=Path)
    run.add_argument("command", nargs=argparse.REMAINDER)
    profile = subparsers.add_parser("profile-module", help="manually cProfile a Python module")
    profile.add_argument("--module", required=True)
    profile.add_argument("--output", required=True, type=Path)
    profile.add_argument("--stats", required=True, type=Path)
    profile.add_argument("args", nargs=argparse.REMAINDER)
    evidence = subparsers.add_parser("evidence", help="write deterministic output evidence")
    evidence.add_argument("--output", required=True, type=Path)
    evidence.add_argument("paths", nargs="+", type=Path)
    return parser.parse_args()


def main() -> int:
    """Run the selected profiling operation."""
    args = parse_args()
    if args.operation == "run":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            raise SystemExit("run requires a command after --")
        return run_workload(args.phase, command, args.output)
    if args.operation == "profile-module":
        module_args = args.args[1:] if args.args[:1] == ["--"] else args.args
        return profile_module(args.module, module_args, args.output, args.stats)
    write_json(
        args.output, {"schema_version": SCHEMA_VERSION, "output_digest": tree_digest(args.paths)}
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
