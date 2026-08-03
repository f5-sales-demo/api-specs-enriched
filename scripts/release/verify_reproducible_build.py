# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Build the publishable release twice and require byte-identical output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.utils.raw_manifest import RawManifestError, validate_raw_manifest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SEMANTIC_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
PYTHON_VERSION = "3.13.14"
UV_VERSION = "0.12.1"
BIOME_VERSION = "2.5.6"
NODE_VERSION = "22.23.2"
NPM_VERSION = "10.9.8"
SPECTRAL_VERSION = "6.16.3"
BUILDER_IMAGE = (
    "python:3.13.14-bookworm@"
    "sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779"
)
PUBLISHABLE_DIRECTORIES = (
    Path("docs/specifications/api"),
    Path("docs/api-reference"),
)
PUBLISHABLE_FILES = (
    Path(".github_release"),
    Path("docs/openapi-specs-config.json"),
    Path("release/api-catalog.json"),
)


class ReproducibilityError(RuntimeError):
    """Two builds from one frozen input did not produce the same release."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publishable_paths(root: Path) -> list[tuple[str, Path]]:
    if not root.is_dir() or root.is_symlink():
        raise ReproducibilityError(f"release root is missing or unsafe: {root}")
    collected: dict[str, Path] = {}
    folded: dict[str, str] = {}

    for relative_directory in PUBLISHABLE_DIRECTORIES:
        directory = root / relative_directory
        if not directory.is_dir() or directory.is_symlink():
            raise ReproducibilityError(
                f"publishable directory is missing or unsafe: {relative_directory}"
            )
        entries = sorted(directory.rglob("*"), key=lambda path: path.relative_to(root).as_posix())
        if any(path.is_symlink() for path in entries):
            raise ReproducibilityError(f"publishable tree contains a symlink: {relative_directory}")
        files = [path for path in entries if path.is_file()]
        if not files:
            raise ReproducibilityError(f"publishable directory is empty: {relative_directory}")
        for path in files:
            relative = path.relative_to(root).as_posix()
            collected[relative] = path

    for relative_file in PUBLISHABLE_FILES:
        path = root / relative_file
        if path.is_symlink() or not path.is_file():
            raise ReproducibilityError(f"publishable file is missing or unsafe: {relative_file}")
        collected[relative_file.as_posix()] = path

    for relative in sorted(collected):
        casefolded = relative.casefold()
        previous = folded.get(casefolded)
        if previous is not None:
            raise ReproducibilityError(
                f"publishable paths collide by case: {previous!r} and {relative!r}"
            )
        folded[casefolded] = relative
    return [(relative, collected[relative]) for relative in sorted(collected)]


def release_manifest(root: Path) -> dict[str, Any]:
    """Return a canonical digest manifest for every publishable release byte."""
    try:
        files = [
            {
                "path": relative,
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
            for relative, path in _publishable_paths(root)
        ]
    except OSError as exc:
        raise ReproducibilityError(f"could not read publishable output under {root}") from exc
    return {"schema_version": 1, "file_count": len(files), "files": files}


def _manifest_entries(manifest: Mapping[str, Any]) -> dict[str, tuple[int, str]]:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise ReproducibilityError("release manifest has no file list")
    return {
        str(entry["path"]): (int(entry["size"]), str(entry["sha256"]))
        for entry in entries
        if isinstance(entry, dict)
    }


def compare_release_builds(first_root: Path, second_root: Path) -> dict[str, Any]:
    """Return the shared manifest or raise with every differing relative path."""
    first = release_manifest(first_root)
    second = release_manifest(second_root)
    if first == second:
        return first

    first_entries = _manifest_entries(first)
    second_entries = _manifest_entries(second)
    different = sorted(
        path
        for path in first_entries.keys() | second_entries.keys()
        if first_entries.get(path) != second_entries.get(path)
    )
    raise ReproducibilityError(
        "frozen release builds differ byte-for-byte: " + ", ".join(different)
    )


def compare_committed_candidate(committed_root: Path, candidate_root: Path) -> dict[str, Any]:
    """Require every committed release byte to equal the isolated candidate."""
    try:
        return compare_release_builds(committed_root, candidate_root)
    except ReproducibilityError as exc:
        detail = str(exc).removeprefix("frozen release builds differ byte-for-byte: ")
        raise ReproducibilityError(
            "committed release differs from isolated candidate byte-for-byte: " + detail
        ) from exc


def _write_source_receipt(root: Path, input_dir: Path) -> None:
    manifest_path = input_dir / "manifest.json"
    try:
        document = json.loads(manifest_path.read_bytes())
        source = validate_raw_manifest(document, source_dir=input_dir)
        (root / ".github_release").write_text(json.dumps(source.release_receipt, indent=2) + "\n")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RawManifestError) as exc:
        raise ReproducibilityError(
            "could not bind isolated release to the exact raw source receipt"
        ) from exc


def require_isolated_python_environments(first_python: Path, second_python: Path) -> None:
    """Require two separately addressed fresh Python environments."""
    if first_python.absolute() == second_python.absolute():
        raise ReproducibilityError("builds require two distinct fresh Python environments")


def _python_toolchain(python: Path) -> tuple[dict[str, Any], str]:
    """Measure the byte-affecting Python runtime and its isolated environment."""
    program = """
import json
import platform
import sys
import zlib

import yaml

print(json.dumps({
    "cache_tag": sys.implementation.cache_tag,
    "environment_prefix": sys.prefix,
    "implementation": platform.python_implementation(),
    "libyaml": bool(getattr(yaml, "__with_libyaml__", False)),
    "libyaml_version": yaml._yaml.get_version_string(),
    "libc": platform.libc_ver(),
    "machine": platform.machine(),
    "python": platform.python_version(),
    "pyyaml": yaml.__version__,
    "system": platform.system(),
    "zlib_compile": zlib.ZLIB_VERSION,
    "zlib_runtime": zlib.ZLIB_RUNTIME_VERSION,
}, sort_keys=True))
"""
    try:
        result = subprocess.run(
            [str(python), "-c", program],
            check=True,
            capture_output=True,
            text=True,
        )
        measured = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ReproducibilityError(f"could not measure Python toolchain: {python}") from exc
    if not isinstance(measured, dict):
        raise ReproducibilityError(f"Python toolchain evidence is not an object: {python}")
    prefix = measured.pop("environment_prefix", None)
    if not isinstance(prefix, str) or not prefix:
        raise ReproducibilityError(f"Python toolchain has no environment prefix: {python}")
    if measured.get("python") != PYTHON_VERSION:
        raise ReproducibilityError(
            f"Python {PYTHON_VERSION} is required; {python} reported {measured.get('python')!r}"
        )
    return measured, prefix


def _required_command_version(command: tuple[str, ...], pattern: str, expected: str) -> str:
    """Measure one pinned command and reject any version drift."""
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReproducibilityError(f"could not measure toolchain command: {command[0]}") from exc
    output = " ".join((result.stdout + " " + result.stderr).split())
    match = re.search(pattern, output)
    if match is None or match.group(1) != expected:
        raise ReproducibilityError(
            f"{command[0]} {expected} is required; measured output was {output!r}"
        )
    return match.group(1)


def _command_digest(command: str) -> str:
    """Return the digest of the exact executable or script resolved on PATH."""
    resolved = shutil.which(command)
    if resolved is None:
        raise ReproducibilityError(f"toolchain command is absent from PATH: {command}")
    try:
        return _sha256(Path(resolved).resolve())
    except OSError as exc:
        raise ReproducibilityError(f"could not hash toolchain command: {command}") from exc


def toolchain_evidence(
    *,
    first_python: Path,
    second_python: Path,
    project_root: Path,
) -> dict[str, Any]:
    """Require and record two equivalent locked build environments."""
    require_isolated_python_environments(first_python, second_python)
    first, first_prefix = _python_toolchain(first_python)
    second, second_prefix = _python_toolchain(second_python)
    if first_prefix == second_prefix:
        raise ReproducibilityError("builds do not use two distinct fresh Python environments")
    if first != second:
        raise ReproducibilityError("fresh Python build environments have different toolchains")
    first_binary = _sha256(first_python.resolve())
    second_binary = _sha256(second_python.resolve())
    if first_binary != second_binary:
        raise ReproducibilityError("fresh Python build environments use different binaries")

    builder_image = os.environ.get("BUILDER_IMAGE")
    if builder_image != BUILDER_IMAGE:
        raise ReproducibilityError(
            f"builder image must be {BUILDER_IMAGE}; measured {builder_image!r}"
        )

    required_files = ("pyproject.toml", "uv.lock", "package.json", "package-lock.json")
    try:
        locks = {name: _sha256(project_root / name) for name in required_files}
    except OSError as exc:
        raise ReproducibilityError("could not hash the committed toolchain locks") from exc
    return {
        "schema_version": 1,
        "environment_count": 2,
        "builder_image": builder_image,
        "python": {**first, "binary_sha256": first_binary},
        "commands": {
            "biome": _required_command_version(
                ("biome", "--version"), r"(?:Version:\s*)?(\d+\.\d+\.\d+)", BIOME_VERSION
            ),
            "node": _required_command_version(
                ("node", "--version"), r"v(\d+\.\d+\.\d+)", NODE_VERSION
            ),
            "npm": _required_command_version(("npm", "--version"), r"(\d+\.\d+\.\d+)", NPM_VERSION),
            "spectral": _required_command_version(
                ("spectral", "--version"), r"(\d+\.\d+\.\d+)", SPECTRAL_VERSION
            ),
            "uv": _required_command_version(("uv", "--version"), r"uv (\d+\.\d+\.\d+)", UV_VERSION),
        },
        "lock_sha256": locks,
        "command_sha256": {
            name: _command_digest(name) for name in ("biome", "node", "npm", "spectral", "uv")
        },
        "orchestrator": {
            "machine": platform.machine(),
            "system": platform.system(),
        },
    }


def build_commands(
    *,
    root: Path,
    input_dir: Path,
    version: str,
    python: str,
    biome: str,
) -> tuple[tuple[str, ...], ...]:
    """Return one invocation of the sole production build entry point."""
    return (
        (
            python,
            "-m",
            "scripts.release.build_release_tree",
            "--version",
            version,
            "--root",
            str(root),
            "--input-dir",
            str(input_dir),
            "--biome",
            biome,
        ),
    )


def _validate_catalog_version(path: Path, expected: str) -> None:
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReproducibilityError(f"generated catalog is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(document, dict) or document.get("version") != expected:
        raise ReproducibilityError(f"generated catalog version does not equal {expected}")


def build_release(
    *,
    root: Path,
    input_dir: Path,
    version: str,
    python: str = sys.executable,
    biome: str = "biome",
) -> None:
    """Build one isolated release without inheriting a formatter bypass."""
    if not SEMANTIC_VERSION.fullmatch(version):
        raise ReproducibilityError(f"build version is not semantic: {version!r}")
    if root.exists() or root.is_symlink():
        raise ReproducibilityError(f"build root already exists: {root}")
    if not input_dir.is_dir() or input_dir.is_symlink():
        raise ReproducibilityError(f"input directory is missing or unsafe: {input_dir}")
    root.mkdir(parents=True)
    _write_source_receipt(root, input_dir)
    environment = dict(os.environ)
    environment.pop("API_SPECS_SKIP_BIOME", None)
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
            "TZ": "UTC",
        }
    )
    commands = build_commands(
        root=root,
        input_dir=input_dir,
        version=version,
        python=python,
        biome=biome,
    )
    try:
        for command in commands:
            subprocess.run(command, check=True, env=environment)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReproducibilityError(f"release build command failed: {command[1]}") from exc
    _validate_catalog_version(root / "release" / "api-catalog.json", version)


def verify_reproducible_build(
    *,
    first_root: Path,
    second_root: Path,
    input_dir: Path,
    version: str,
    manifest_path: Path,
    first_python: Path,
    second_python: Path,
    committed_root: Path | None = None,
) -> dict[str, Any]:
    """Build twice, compare all release bytes, and persist the measured manifest."""
    if first_root.resolve() == second_root.resolve():
        raise ReproducibilityError("first and second build roots must be distinct")
    measured_toolchain = toolchain_evidence(
        first_python=first_python,
        second_python=second_python,
        project_root=Path.cwd(),
    )
    build_release(
        root=first_root,
        input_dir=input_dir,
        version=version,
        python=str(first_python),
    )
    build_release(
        root=second_root,
        input_dir=input_dir,
        version=version,
        python=str(second_python),
    )
    manifest = compare_release_builds(first_root, second_root)
    if committed_root is not None:
        compare_committed_candidate(committed_root, first_root)
    try:
        source_document = json.loads((input_dir / "manifest.json").read_bytes())
        source_receipt = validate_raw_manifest(
            source_document,
            source_dir=input_dir,
        ).release_receipt
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RawManifestError) as exc:
        raise ReproducibilityError("could not record the exact raw source receipt") from exc
    evidence = {
        **manifest,
        "source_receipt": source_receipt,
        "toolchain": measured_toolchain,
    }
    manifest_bytes = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode()
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(manifest_bytes)
    except OSError as exc:
        raise ReproducibilityError(
            f"could not write reproducibility manifest: {manifest_path}"
        ) from exc
    return evidence


def _semantic_version(value: str) -> str:
    if not SEMANTIC_VERSION.fullmatch(value):
        raise argparse.ArgumentTypeError(f"not a semantic version: {value!r}")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse deterministic-build verification arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, type=_semantic_version)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--first-root", required=True, type=Path)
    parser.add_argument("--second-root", required=True, type=Path)
    parser.add_argument("--first-python", required=True, type=Path)
    parser.add_argument("--second-python", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--committed-root",
        type=Path,
        help="Require this committed release tree to match the isolated candidate",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run two builds and emit measured reproducibility evidence."""
    args = parse_args(argv)
    try:
        manifest = verify_reproducible_build(
            first_root=args.first_root,
            second_root=args.second_root,
            input_dir=args.input_dir,
            version=args.version,
            manifest_path=args.manifest,
            first_python=args.first_python,
            second_python=args.second_python,
            committed_root=args.committed_root,
        )
    except ReproducibilityError as exc:
        print(f"Reproducibility verification failed: {exc}", file=sys.stderr)
        return 1
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    print(
        f"Verified {manifest['file_count']} byte-identical release files "
        f"(manifest sha256:{hashlib.sha256(canonical).hexdigest()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
