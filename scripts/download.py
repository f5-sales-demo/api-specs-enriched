#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Download and extract F5 XC API specifications from GitHub Releases.

This script downloads pre-validated API specifications from the api-specs
repository's GitHub releases. It compares the selected asset's tag, name, and
SHA-256 digest to avoid both unnecessary downloads and stale same-tag content.

Architecture:
    Source: GitHub Releases (f5-sales-demo/api-specs)
    Format: ZIP archive with domains/*.json files
    Caching: .github_release asset digest tracking
    Extraction: Secure ZIP processing with validation

Usage:
    # Download the exact committed receipt (uses cache)
    python -m scripts.download

    # Force exact receipt download and digest verification
    python -m scripts.download --force

Environment:
    GITHUB_TOKEN: Optional authentication token for higher rate limits
                    (5000/hr vs 60/hr unauthenticated)
"""

import argparse
import fnmatch
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from contextlib import suppress
from pathlib import Path, PurePosixPath

import requests
import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from scripts.package_config import load_packaged_yaml
from scripts.utils.github_release import (
    download_release_asset,
    file_sha256_digest,
    get_latest_release,
    load_release_receipt,
    parse_release_version,
    release_receipt,
    require_release_asset,
    resolve_release_receipt,
    save_release_metadata,
    write_release_receipt,
)
from scripts.utils.raw_manifest import (
    RawManifestError,
    create_raw_manifest,
    validate_raw_manifest,
)

console = Console()

_MANIFEST_NAME = "manifest.json"


def _validate_download_config(config: dict, canonical_source: object) -> None:
    """Fail closed unless config preserves the sole correction-layer source."""
    for section in ("source", "paths", "extraction"):
        if not isinstance(config.get(section), dict):
            raise TypeError(f"download configuration requires a {section!r} mapping")
    if config["source"] != canonical_source:
        raise ValueError(
            "download source must exactly match config/download.yaml "
            "(f5-sales-demo/api-specs immutable releases)"
        )
    for field in ("original", "version_file"):
        if not isinstance(config["paths"].get(field), str) or not config["paths"][field]:
            raise ValueError(f"download paths.{field} must be a non-empty string")
    extraction = config["extraction"]
    for field in ("include_patterns", "exclude_patterns"):
        value = extraction.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"download extraction.{field} must be an array of strings")
    for field in ("max_file_size", "max_total_size", "max_compression_ratio", "max_file_count"):
        value = extraction.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"download extraction.{field} must be a positive integer")


def load_config(config_path: Path | None = None) -> dict:
    """Load explicit YAML or the configuration shipped with the package.

    Args:
        config_path: Path to download.yaml configuration file.

    Returns:
        Configuration dictionary with source, paths, and extraction settings.
    """
    canonical = load_packaged_yaml("download.yaml")
    if config_path is None:
        config = canonical
    else:
        if not config_path.is_file():
            raise FileNotFoundError(f"download configuration not found: {config_path}")
        with config_path.open() as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            raise TypeError("download configuration must be an object")

    canonical_source = canonical.get("source")
    if not isinstance(canonical_source, dict):
        raise TypeError("packaged config/download.yaml has no canonical source mapping")
    _validate_download_config(config, canonical_source)
    return config


def validate_zip_member_path(member_name: str) -> bool:
    """Validate ZIP member path for security.

    Rejects:
    - Absolute paths
    - Relative path components (..)
    - Hidden paths with directory traversal attempts

    Args:
        member_name: ZIP member path to validate.

    Returns:
        True if path is safe, False otherwise.
    """
    if not member_name or "\x00" in member_name or "\\" in member_name:
        return False

    # ZIP member names are POSIX paths regardless of the host platform. Reject
    # aliases as well as direct traversal so two names cannot flatten onto the
    # same destination through path normalisation.
    path = PurePosixPath(member_name)
    if path.is_absolute() or member_name.startswith("/"):
        return False
    if any(part in {"", ".", ".."} for part in member_name.split("/")):
        return False
    return not (path.parts and path.parts[0].endswith(":"))


def validate_zip_member_size(info: zipfile.ZipInfo, limits: dict) -> tuple[bool, str]:
    """Validate ZIP member for size-based attacks.

    Args:
        info: ZIP member metadata.
        limits: Configuration dict with max_file_size and max_compression_ratio.

    Returns:
        Tuple of (is_valid, error_message).
    """
    max_file_size = limits.get("max_file_size", 10 * 1024 * 1024)
    max_compression_ratio = limits.get("max_compression_ratio", 100)

    # Check file size limit
    if info.file_size > max_file_size:
        return False, f"File too large: {info.filename} ({info.file_size} bytes)"

    if info.file_size < 0 or info.compress_size < 0:
        return False, f"Invalid archive size metadata: {info.filename}"

    if info.file_size == 0:
        return True, ""

    # Check compression ratio (zip bomb detection)
    if info.compress_size == 0:
        return False, f"Invalid compressed size: {info.filename}"
    ratio = info.file_size / info.compress_size
    if ratio > max_compression_ratio:
        return False, f"Suspicious compression ratio: {info.filename} ({ratio:.0f}:1)"

    return True, ""


def matches_pattern(filename: str, patterns: list[str]) -> bool:
    """Check if filename matches any pattern in list.

    Args:
        filename: File path to check.
        patterns: List of glob-style patterns (e.g., ["domains/*.json"]).

    Returns:
        True if filename matches any pattern, False otherwise.
    """
    return any(fnmatch.fnmatch(filename, pattern) for pattern in patterns)


def extract_zip(zip_path: Path, output_dir: Path, config: dict) -> list[str]:
    """Extract ZIP file to output directory with pattern filtering.

    Args:
        zip_path: Path to ZIP archive.
        output_dir: Destination directory for extracted files.
        config: Configuration dict with extraction settings.

    Returns:
        List of extracted filenames.

    Security:
        - Validates paths for traversal attacks
        - Enforces file size and compression ratio limits
        - Tracks total extraction size
        - Limits file count
    """
    extraction_config = config.get("extraction", {})
    include_patterns = extraction_config.get("include_patterns", ["*.json"])
    exclude_patterns = extraction_config.get("exclude_patterns", [])
    max_total_size = extraction_config.get("max_total_size", 500 * 1024 * 1024)
    max_file_count = extraction_config.get("max_file_count", 1000)

    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError(f"Extraction target must be empty: {output_dir}")

    extracted_files: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting specifications...", total=None)

        with zipfile.ZipFile(zip_path, "r") as zf:
            total_size = 0  # Track cumulative size

            seen_targets: set[str] = set()
            for info in zf.infolist():
                member = info.filename
                # Apply inclusion patterns
                if not matches_pattern(member, include_patterns):
                    continue

                # Apply exclusion patterns
                if matches_pattern(member, exclude_patterns):
                    continue

                if not validate_zip_member_path(member):
                    raise ValueError(f"Unsafe included archive member: {member}")

                # An included member is part of the asserted input set. Invalid
                # members fail the whole candidate instead of silently yielding
                # a partial source tree.
                if info.is_dir() or not member.endswith(".json"):
                    raise ValueError(f"Invalid included archive member: {member}")
                unix_mode = info.external_attr >> 16
                file_type = stat.S_IFMT(unix_mode)
                if file_type not in {0, stat.S_IFREG}:
                    raise ValueError(f"Included archive member is not a regular file: {member}")
                if info.flag_bits & 0x1:
                    raise ValueError(f"Encrypted archive member is not supported: {member}")

                # Security: Validate file size and compression ratio
                is_valid, error_msg = validate_zip_member_size(info, extraction_config)
                if not is_valid:
                    raise ValueError(error_msg)

                # Security: Check total extracted size
                total_size += info.file_size
                if total_size > max_total_size:
                    raise ValueError(
                        f"Total extraction size exceeds limit: {total_size} > {max_total_size}",
                    )

                # Security: Check file count
                if len(extracted_files) >= max_file_count:
                    raise ValueError(f"File count exceeds limit: {max_file_count}")

                filename = Path(member).name
                if filename == _MANIFEST_NAME:
                    raise ValueError(f"Archive member collides with generated manifest: {member}")
                if filename in seen_targets:
                    raise ValueError(f"Archive members flatten to duplicate filename: {filename}")
                seen_targets.add(filename)
                target_path = output_dir / filename

                with zf.open(member) as source, target_path.open("wb") as target:
                    written = 0
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > info.file_size:
                            raise ValueError(f"Archive member exceeded declared size: {member}")
                        target.write(chunk)
                    if written != info.file_size:
                        raise ValueError(
                            f"Archive member size mismatch: {member} ({written} != {info.file_size})",
                        )

                extracted_files.append(filename)

            progress.update(
                task,
                description=f"Extracted {len(extracted_files)} specification files",
            )

    console.print(f"[green]✅ Extracted {len(extracted_files)} files to {output_dir}[/green]")
    return extracted_files


def _assert_no_unknown_destination_files(output_dir: Path) -> dict | None:
    """Refuse unknown files and return the validated existing-tree receipt."""
    if output_dir.is_symlink():
        raise ValueError(f"Managed output path must not be a symlink: {output_dir}")
    if not output_dir.exists():
        return None
    if not output_dir.is_dir():
        raise ValueError(f"Managed output path is not a directory: {output_dir}")

    entries = list(output_dir.iterdir())
    if not entries:
        return None

    manifest_path = output_dir / _MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError(
            f"Refusing to replace non-empty output without a regular {_MANIFEST_NAME}",
        )
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot establish managed output ownership: {exc}") from exc
    try:
        contract = validate_raw_manifest(manifest, source_dir=output_dir)
    except RawManifestError as exc:
        raise ValueError(f"Cannot establish managed output ownership: {exc}") from exc
    managed = set(contract.files) | {_MANIFEST_NAME}
    unknown = sorted(path.name for path in entries if path.name not in managed)
    if unknown:
        raise ValueError(f"Refusing to delete unknown output files: {', '.join(unknown)}")
    invalid = sorted(path.name for path in entries if path.is_symlink() or not path.is_file())
    if invalid:
        raise ValueError(f"Managed output contains non-regular files: {', '.join(invalid)}")
    return contract.release_receipt


def validate_staged_tree(output_dir: Path, files: list[str]) -> None:
    """Validate that a staged source tree is complete and internally consistent."""
    managed_files = sorted(files)
    expected = set(managed_files) | {_MANIFEST_NAME}
    entries = list(output_dir.iterdir())
    actual = {path.name for path in entries}
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            f"Staged source tree is incomplete (missing={missing}, unexpected={unexpected})",
        )

    for path in entries:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Staged source tree contains non-regular file: {path.name}")

    for filename in managed_files:
        path = output_dir / filename
        try:
            document = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Extracted specification is not valid JSON: {filename}") from exc
        if not isinstance(document, dict):
            raise TypeError(f"Extracted specification is not a JSON object: {filename}")

    try:
        manifest = json.loads((output_dir / _MANIFEST_NAME).read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Generated manifest is not valid JSON") from exc
    try:
        contract = validate_raw_manifest(manifest, source_dir=output_dir)
    except RawManifestError as exc:
        raise ValueError(f"Generated manifest violates the raw contract: {exc}") from exc
    if contract.files != tuple(managed_files):
        raise ValueError("Generated manifest does not describe the staged files")


def _require_extracted_files(files: list[str]) -> None:
    """Fail a candidate that contains no selected specifications."""
    if not files:
        raise ValueError("No specifications were extracted")


def generate_manifest(
    output_dir: Path,
    files: list[str],
    receipt: dict,
) -> None:
    """Generate the exact upstream receipt and per-file byte manifest.

    Args:
        output_dir: Directory containing extracted specs.
        files: List of extracted filenames.
        receipt: Validated six-field identity of the immutable api-specs asset.
    """
    manifest = create_raw_manifest(
        release_receipt=receipt,
        source_dir=output_dir,
        files=files,
    ).as_document()

    manifest_path = output_dir / "manifest.json"
    with manifest_path.open("w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    console.print(f"[green]✅ Generated manifest: {manifest_path}[/green]")


def _reserve_sibling_path(parent: Path, prefix: str) -> Path:
    """Reserve a unique non-existent path on *parent*'s filesystem."""
    reserved = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    reserved.rmdir()
    return reserved


def _replace_path(source: Path, destination: Path) -> None:
    """Replace one path atomically; isolated as a failure-injection seam."""
    source.replace(destination)


def _prepare_release_metadata(
    release_data: dict,
    asset: dict,
    asset_digest: str,
    version_file: Path,
) -> Path:
    """Write and sync release metadata beside its final destination."""
    version_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{version_file.name}.",
        suffix=".tmp",
        dir=version_file.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        save_release_metadata(
            release_data,
            asset["name"],
            asset["size"],
            asset_digest,
            temporary,
        )
        mode = stat.S_IMODE(version_file.stat().st_mode) if version_file.exists() else 0o644
        temporary.chmod(mode)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _promote_tree_and_metadata(
    staged_dir: Path,
    output_dir: Path,
    staged_metadata: Path,
    version_file: Path,
) -> None:
    """Promote a validated tree, then its metadata, rolling back on failure."""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_unknown_destination_files(output_dir)

    if output_dir.exists():
        staged_dir.chmod(stat.S_IMODE(output_dir.stat().st_mode))
    else:
        staged_dir.chmod(0o755)

    backup = _reserve_sibling_path(output_dir.parent, f".{output_dir.name}.backup-")
    rejected = _reserve_sibling_path(output_dir.parent, f".{output_dir.name}.rejected-")
    had_previous_tree = output_dir.exists()
    previous_tree_moved = False
    candidate_moved = False

    try:
        if had_previous_tree:
            _replace_path(output_dir, backup)
            previous_tree_moved = True
        _replace_path(staged_dir, output_dir)
        candidate_moved = True
        _replace_path(staged_metadata, version_file)
    except Exception:
        rollback_error: Exception | None = None
        try:
            if candidate_moved and output_dir.exists():
                _replace_path(output_dir, rejected)
            if previous_tree_moved and backup.exists():
                _replace_path(backup, output_dir)
        except Exception as exc:  # pragma: no cover - catastrophic filesystem failure
            rollback_error = exc
        if rejected.exists():
            shutil.rmtree(rejected, ignore_errors=True)
        if rollback_error is not None:
            raise RuntimeError(
                "Download promotion failed and rollback was incomplete"
            ) from rollback_error
        raise

    if backup.exists():
        try:
            shutil.rmtree(backup)
        except OSError as exc:  # The promoted tree and metadata are already committed.
            console.print(f"[yellow]⚠️  Could not remove old source-tree backup: {exc}[/yellow]")


def download_from_github_release(
    config: dict,
    force: bool = False,
    receipt_path: Path | None = None,
) -> tuple[bool, dict | None]:
    """Download specifications from GitHub releases.

    Args:
        config: Configuration dict with source and paths.
        force: Force download even if no updates detected.
        receipt_path: Exact upstream receipt to consume. Defaults to the
            repository's committed ``.github_release`` receipt.

    Returns:
        Tuple of (success, release_data).
    """
    source_config = config["source"]
    paths_config = config["paths"]

    repo_owner = source_config["repository"]["owner"]
    repo_name = source_config["repository"]["name"]
    asset_pattern = source_config.get("asset_pattern", "*.zip")
    version_file = Path(paths_config["version_file"])
    output_dir = Path(paths_config["original"])

    try:
        tree_receipt = _assert_no_unknown_destination_files(output_dir)
    except (OSError, TypeError, ValueError) as exc:
        console.print(f"[red]❌ Refusing unsafe source-tree replacement: {exc}[/red]")
        return False, None

    # Get GitHub token from environment (optional)
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        console.print("[blue]🔑 Using GITHUB_TOKEN for authentication[/blue]")

    selected_receipt_path = receipt_path or version_file
    try:
        selected_receipt = load_release_receipt(selected_receipt_path)
        release_data, asset = resolve_release_receipt(
            repo_owner,
            repo_name,
            selected_receipt,
            token=github_token,
            asset_pattern=asset_pattern,
        )
    except (OSError, TypeError, ValueError, requests.RequestException) as exc:
        console.print(f"[red]❌ Exact release receipt failed validation: {exc}[/red]")
        return False, None

    local_receipt = None
    with suppress(ValueError):
        local_receipt = load_release_receipt(version_file)
    if local_receipt == selected_receipt and tree_receipt == selected_receipt and not force:
        console.print("[blue]✅ Exact release receipt is already materialized.[/blue]")
        return True, release_data

    console.print(
        f"[green]📦 Found asset: {asset['name']} ({asset['size'] / 1024 / 1024:.1f} MB)[/green]",
    )

    remote_digest = selected_receipt["asset_digest"]

    # Download asset. The unique temporary file is always removed, including
    # downloader failures before archive validation begins.
    with tempfile.NamedTemporaryFile(prefix="f5xc-api-specs-", suffix=".zip", delete=False) as tmp:
        temp_zip = Path(tmp.name)
    staging_dir: Path | None = None
    staged_metadata: Path | None = None
    try:
        success = download_release_asset(
            asset["browser_download_url"],
            temp_zip,
            token=github_token,
        )
        if not success:
            return False, None

        downloaded_digest = file_sha256_digest(temp_zip)
        if downloaded_digest != remote_digest:
            console.print(
                f"[red]❌ Downloaded asset digest mismatch: expected {remote_digest}, "
                f"found {downloaded_digest}[/red]",
            )
            return False, None

        downloaded_size = temp_zip.stat().st_size
        expected_size = selected_receipt["asset_size"]
        if downloaded_size != expected_size:
            console.print(
                f"[red]❌ Downloaded asset size mismatch: expected {expected_size}, "
                f"found {downloaded_size}[/red]",
            )
            return False, None

        output_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent),
        )
        extracted_files = extract_zip(temp_zip, staging_dir, config)
        _require_extracted_files(extracted_files)

        release_version = parse_release_version(release_data["tag_name"])
        generate_manifest(
            staging_dir,
            extracted_files,
            selected_receipt,
        )
        validate_staged_tree(staging_dir, extracted_files)

        # Metadata is fully prepared before promotion but becomes authoritative
        # only after the validated tree is active.
        staged_metadata = _prepare_release_metadata(
            release_data,
            asset,
            remote_digest,
            version_file,
        )
        _promote_tree_and_metadata(staging_dir, output_dir, staged_metadata, version_file)

        console.print(
            f"\n[bold green]✅ Successfully downloaded {len(extracted_files)} specs![/bold green]",
        )
        console.print(f"  Release: {release_version}")
        console.print(f"  Output:  {output_dir}")

        return True, release_data

    except (OSError, RuntimeError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        console.print(f"[red]❌ Downloaded release failed validation: {exc}[/red]")
        return False, None

    finally:
        temp_zip.unlink(missing_ok=True)
        if staging_dir is not None and staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        if staged_metadata is not None:
            staged_metadata.unlink(missing_ok=True)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Download F5 XC API specifications from GitHub Releases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Download the exact committed receipt (uses cache)
    python -m scripts.download

    # Force download (bypass cache)
    python -m scripts.download --force

Environment Variables:
    GITHUB_TOKEN    Optional GitHub token for authentication (5000/hr vs 60/hr)
        """,
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to configuration file (default: packaged config/download.yaml)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force download even if no updates detected",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        help="Exact release receipt to consume (default: configured .github_release)",
    )
    parser.add_argument(
        "--resolve-latest-receipt",
        type=Path,
        metavar="PATH",
        help="Resolve latest once and write a pinned receipt; do not download specs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override output directory for extracted specs",
    )

    args = parser.parse_args()

    # Load configuration
    config = load_config(args.config)

    # Override output directory if specified
    if args.output_dir:
        config["paths"]["original"] = str(args.output_dir)

    # Get GitHub token (optional)
    github_token = os.getenv("GITHUB_TOKEN")

    if args.resolve_latest_receipt:
        if args.receipt or args.output_dir or args.force:
            parser.error("--resolve-latest-receipt cannot be combined with download options")
        source_config = config["source"]
        repository = source_config["repository"]
        try:
            latest = get_latest_release(
                repository["owner"],
                repository["name"],
                token=github_token,
            )
            asset = require_release_asset(
                latest,
                source_config.get("asset_pattern", "*.zip"),
            )
            write_release_receipt(args.resolve_latest_receipt, release_receipt(latest, asset))
        except (OSError, TypeError, ValueError, requests.RequestException) as exc:
            console.print(f"[red]❌ Could not resolve latest immutable receipt: {exc}[/red]")
            return 1
        console.print(
            f"[green]✅ Wrote pinned release receipt: {args.resolve_latest_receipt}[/green]"
        )
        return 0

    # Download
    source_type = config["source"]["type"]

    if source_type == "github_release":
        success, release_data = download_from_github_release(
            config,
            force=args.force,
            receipt_path=args.receipt,
        )

        # Set GitHub Actions output
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output and release_data:
            with Path(github_output).open("a") as f:
                f.write(f"updated={'true' if success else 'false'}\n")
                f.write(f"version={parse_release_version(release_data['tag_name'])}\n")

        return 0 if success else 1

    console.print(f"[red]❌ Unsupported source type: {source_type}[/red]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
