#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Download and extract F5 XC API specifications from GitHub Releases.

This script downloads pre-validated API specifications from the api-specs
repository's GitHub releases. It uses release version-based caching to avoid
unnecessary downloads.

Architecture:
    Source: GitHub Releases (f5-sales-demo/api-specs)
    Format: ZIP archive with domains/*.json files
    Caching: .github_release version tracking (replaces ETag)
    Extraction: Secure ZIP processing with validation

Usage:
    # Check for updates without downloading
    python -m scripts.download --check-only

    # Download latest release (uses cache)
    python -m scripts.download

    # Force download (bypass cache)
    python -m scripts.download --force

Environment:
    GITHUB_TOKEN: Optional authentication token for higher rate limits
                    (5000/hr vs 60/hr unauthenticated)
"""

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from scripts.utils.github_release import (
    check_for_updates,
    download_release_asset,
    find_release_asset,
    parse_release_version,
    save_release_metadata,
)
from scripts.utils.version_calculator import get_version_from_tags

console = Console()


def verify_release_asset_digest(archive: Path, asset: dict) -> str:
    """Return the archive SHA-256 after matching GitHub's immutable digest."""
    expected = asset.get("digest")
    if not isinstance(expected, str) or not expected.startswith("sha256:"):
        raise ValueError("release asset is missing a SHA-256 digest")

    hasher = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if expected.removeprefix("sha256:") != actual:
        raise ValueError("downloaded release asset digest does not match GitHub metadata")
    return actual

# Default configuration (fallback if config file not found)
DEFAULT_CONFIG = {
    "source": {
        "type": "github_release",
        "repository": {
            "owner": "f5-sales-demo",
            "name": "api-specs",
        },
        "asset_pattern": "api-specs-v*.zip",
    },
    "paths": {
        "original": "specs/original",
        "version_file": ".github_release",
    },
    "extraction": {
        "include_patterns": ["domains/*.json"],
        "exclude_patterns": ["openapi.json", "openapi.yaml"],
        "max_file_size": 10 * 1024 * 1024,  # 10 MB
        "max_total_size": 500 * 1024 * 1024,  # 500 MB
        "max_compression_ratio": 100,
        "max_file_count": 1000,
    },
}


def load_config(config_path: Path | None = None) -> dict:
    """Load configuration from YAML file or use defaults.

    Args:
        config_path: Path to download.yaml configuration file.

    Returns:
        Configuration dictionary with source, paths, and extraction settings.
    """
    if config_path and config_path.exists():
        with config_path.open() as f:
            config = yaml.safe_load(f)
            # Merge with defaults for missing keys
            for key, value in DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = value
                elif isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        if subkey not in config[key]:
                            config[key][subkey] = subvalue
            return config
    return DEFAULT_CONFIG


def get_version() -> str:
    """Get version from git tags.

    Uses tag-based versioning to eliminate race conditions from file-based versioning.

    Returns:
        Version string in semver format (e.g., "2.0.38").
    """
    return get_version_from_tags()


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
    # Reject absolute paths
    if member_name.startswith("/"):
        return False

    # Reject path traversal attempts
    if ".." in member_name.split("/"):
        return False

    # Reject paths starting with traversal
    return not member_name.startswith("../")


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

    # Check compression ratio (zip bomb detection)
    if info.file_size > 0 and info.compress_size > 0:
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

    # Clear existing files
    for existing_file in output_dir.glob("*.json"):
        existing_file.unlink()

    extracted_files: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting specifications...", total=None)

        with zipfile.ZipFile(zip_path, "r") as zf:
            total_size = 0  # Track cumulative size

            for member in zf.namelist():
                # Apply inclusion patterns
                if not matches_pattern(member, include_patterns):
                    continue

                # Apply exclusion patterns
                if matches_pattern(member, exclude_patterns):
                    continue

                # Only extract JSON files
                if not member.endswith(".json"):
                    continue

                # Security: Validate path for traversal attempts
                if not validate_zip_member_path(member):
                    console.print(f"[yellow]⚠️  Skipping suspicious path: {member}[/yellow]")
                    continue

                # Security: Validate file size and compression ratio
                info = zf.getinfo(member)
                is_valid, error_msg = validate_zip_member_size(info, extraction_config)
                if not is_valid:
                    console.print(f"[yellow]⚠️  {error_msg}[/yellow]")
                    continue

                # Security: Check total extracted size
                total_size += info.file_size
                if total_size > max_total_size:
                    raise ValueError(
                        f"Total extraction size exceeds limit: {total_size} > {max_total_size}",
                    )

                # Security: Check file count
                if len(extracted_files) >= max_file_count:
                    raise ValueError(f"File count exceeds limit: {max_file_count}")

                # Extract directly to output dir (flatten structure)
                filename = Path(member).name
                target_path = output_dir / filename

                with zf.open(member) as source, target_path.open("wb") as target:
                    target.write(source.read())

                extracted_files.append(filename)

            progress.update(
                task,
                description=f"Extracted {len(extracted_files)} specification files",
            )

    console.print(f"[green]✅ Extracted {len(extracted_files)} files to {output_dir}[/green]")
    return extracted_files


def generate_manifest(
    output_dir: Path,
    files: list[str],
    version: str,
    release_version: str,
    release_published_at: str,
) -> None:
    """Generate manifest file with metadata about extracted specs.

    Args:
        output_dir: Directory containing extracted specs.
        files: List of extracted filenames.
        version: Git-based version from tags (e.g., "2.0.38").
        release_version: GitHub release version (e.g., "2026.01.22-2").
        release_published_at: When the upstream release was published (e.g.,
            "2026-07-30T08:27:17Z"). Committed artifacts are stamped from this, so it
            must describe the RELEASE and never this download.
    """
    manifest = {
        "version": version,
        "release_version": release_version,
        # When THIS machine downloaded — informational only. Nothing derived from it
        # may reach a committed artifact: two machines fetching the same release get
        # different values, which is how every generated spec came to differ between
        # a local rebuild and CI's (#1156). See scripts/utils/build_stamp.py.
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        # When the upstream release was published — a property of the INPUT, and the
        # same value for everyone who downloads it. This is what artifacts stamp.
        "release_published_at": release_published_at,
        "file_count": len(files),
        "files": sorted(files),
    }

    manifest_path = output_dir / "manifest.json"
    with manifest_path.open("w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    console.print(f"[green]✅ Generated manifest: {manifest_path}[/green]")


def download_from_github_release(config: dict, force: bool = False) -> tuple[bool, dict | None]:
    """Download specifications from GitHub releases.

    Args:
        config: Configuration dict with source and paths.
        force: Force download even if no updates detected.

    Returns:
        Tuple of (success, release_data).
    """
    source_config = config["source"]
    paths_config = config["paths"]

    repo_owner = source_config["repository"]["owner"]
    repo_name = source_config["repository"]["name"]
    asset_pattern = source_config.get("asset_pattern", "*.zip")
    version_file = Path(paths_config["version_file"])

    # Get GitHub token from environment (optional)
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        console.print("[blue]🔑 Using GITHUB_TOKEN for authentication[/blue]")

    # Check for updates
    has_updates, release_data = check_for_updates(
        repo_owner,
        repo_name,
        version_file,
        token=github_token,
    )

    if not has_updates and not force:
        console.print("[blue]✅ No updates needed. Use --force to download anyway.[/blue]")
        return True, release_data

    if not release_data:
        console.print("[red]❌ Could not fetch release data[/red]")
        return False, None

    # Find matching asset
    asset = find_release_asset(release_data, asset_pattern)
    if not asset:
        console.print(f"[red]❌ No asset matching '{asset_pattern}' found in release[/red]")
        return False, None

    console.print(
        f"[green]📦 Found asset: {asset['name']} ({asset['size'] / 1024 / 1024:.1f} MB)[/green]",
    )

    # Download asset
    temp_zip = Path(tempfile.gettempdir()) / "f5xc-api-specs-github.zip"
    success = download_release_asset(
        asset["browser_download_url"],
        temp_zip,
        token=github_token,
    )

    if not success:
        return False, None

    try:
        asset_sha256 = verify_release_asset_digest(temp_zip, asset)
    except ValueError as error:
        console.print(f"[red]❌ {error}[/red]")
        temp_zip.unlink(missing_ok=True)
        return False, None

    # Extract
    output_dir = Path(paths_config["original"])
    try:
        extracted_files = extract_zip(temp_zip, output_dir, config)

        if not extracted_files:
            console.print("[red]❌ No files were extracted![/red]")
            return False, None

        # Save release metadata
        save_release_metadata(
            release_data,
            asset["name"],
            asset["size"],
            asset_sha256,
            version_file,
        )

        # Generate manifest
        version = get_version()
        release_version = parse_release_version(release_data["tag_name"])
        generate_manifest(
            output_dir,
            extracted_files,
            version,
            release_version,
            release_data["published_at"],
        )

        console.print(
            f"\n[bold green]✅ Successfully downloaded {len(extracted_files)} specs![/bold green]",
        )
        console.print(f"  Version: {version}")
        console.print(f"  Release: {release_version}")
        console.print(f"  Output:  {output_dir}")

        return True, release_data

    finally:
        # Cleanup temp file
        temp_zip.unlink(missing_ok=True)


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Download F5 XC API specifications from GitHub Releases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Check for updates without downloading
    python -m scripts.download --check-only

    # Download latest release (uses cache)
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
        default=Path("config/download.yaml"),
        help="Path to configuration file (default: config/download.yaml)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check for updates, don't download",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force download even if no updates detected",
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

    # Check-only mode
    if args.check_only:
        source_config = config["source"]
        if source_config["type"] != "github_release":
            console.print(
                f"[yellow]⚠️  Source type '{source_config['type']}' not supported for check-only[/yellow]",
            )
            return 1

        repo_owner = source_config["repository"]["owner"]
        repo_name = source_config["repository"]["name"]
        version_file = Path(config["paths"]["version_file"])

        has_updates, _ = check_for_updates(
            repo_owner,
            repo_name,
            version_file,
            token=github_token,
        )

        # Set GitHub Actions output
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with Path(github_output).open("a") as f:
                f.write(f"updated={'true' if has_updates or args.force else 'false'}\n")

        return 0 if not has_updates else 1

    # Download
    source_type = config["source"]["type"]

    if source_type == "github_release":
        success, release_data = download_from_github_release(config, force=args.force)

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
