#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""GitHub Releases integration for downloading API specifications.

This module provides utilities for fetching exact release metadata and downloading
assets from GitHub Releases. Supports authentication via GITHUB_TOKEN
for higher rate limits (5000/hr vs 60/hr).

Example:
    Basic usage without authentication:
        release = get_latest_release("owner", "repo")
        download_release_asset(release["assets"][0]["url"], Path("output.zip"))

    With authentication for higher rate limits:
        token = os.getenv("GITHUB_TOKEN")
        release = get_latest_release("owner", "repo", token=token)
"""

import fnmatch
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn

console = Console()

# GitHub API configuration
GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

# Rate limit thresholds
RATE_LIMIT_WARNING_THRESHOLD = 10  # Warn if < 10 requests remaining
UNAUTHENTICATED_RATE_LIMIT = 60  # Per hour
AUTHENTICATED_RATE_LIMIT = 5000  # Per hour
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_CANONICAL_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
RELEASE_RECEIPT_FIELDS = (
    "version",
    "tag_name",
    "published_at",
    "asset_name",
    "asset_size",
    "asset_digest",
)
RELEASE_RECEIPT_KEYS = frozenset(RELEASE_RECEIPT_FIELDS)


def _get_headers(token: str | None = None) -> dict[str, str]:
    """Build HTTP headers for GitHub API requests.

    Args:
        token: Optional GitHub personal access token for authentication.

    Returns:
        Dictionary of HTTP headers including Accept, API version, and optional auth.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _check_rate_limit(response: requests.Response) -> None:
    """Check GitHub API rate limit status and warn if approaching limit.

    Args:
        response: HTTP response from GitHub API containing rate limit headers.
    """
    remaining = int(response.headers.get("X-RateLimit-Remaining", "-1"))
    limit = int(response.headers.get("X-RateLimit-Limit", "-1"))

    if remaining != -1 and limit != -1 and remaining < RATE_LIMIT_WARNING_THRESHOLD:
        reset_time = int(response.headers.get("X-RateLimit-Reset", "0"))
        reset_dt = datetime.fromtimestamp(reset_time, tz=UTC)
        console.print(
            f"[yellow]⚠️  Rate limit approaching: {remaining}/{limit} "
            f"requests remaining (resets at {reset_dt.strftime('%H:%M:%S UTC')})[/yellow]",
        )
        if limit == UNAUTHENTICATED_RATE_LIMIT:
            console.print(
                "[yellow]💡 Tip: Set GITHUB_TOKEN environment variable for "
                f"{AUTHENTICATED_RATE_LIMIT}/hr rate limit[/yellow]",
            )


def get_latest_release(
    repo_owner: str,
    repo_name: str,
    token: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch latest release metadata from GitHub repository.

    Args:
        repo_owner: GitHub repository owner/organization.
        repo_name: GitHub repository name.
        token: Optional GitHub token for authentication (increases rate limit).
        timeout: HTTP request timeout in seconds.

    Returns:
        Dictionary containing release metadata:
            - tag_name: Release version tag (e.g., "v2026.01.22-2")
            - published_at: ISO 8601 timestamp
            - assets: List of release assets with download URLs
            - name: Release title
            - body: Release notes/changelog

    Raises:
        requests.RequestException: If API request fails (network, auth, not found).
        ValueError: If no releases found or invalid response format.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/releases/latest"
    headers = _get_headers(token)

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        _check_rate_limit(response)
        response.raise_for_status()

        release_data = response.json()

        # Validate required fields
        required_fields = ["tag_name", "published_at", "assets"]
        missing_fields = [f for f in required_fields if f not in release_data]
        if missing_fields:
            raise ValueError(f"Invalid release data: missing {', '.join(missing_fields)}")
        require_immutable_release(release_data)

        return release_data

    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise ValueError(
                f"No releases found for {repo_owner}/{repo_name}. "
                "Repository may not exist or has no releases.",
            ) from e
        raise
    except requests.RequestException as e:
        console.print(f"[red]Error fetching release: {e}[/red]")
        raise


def get_release_by_tag(
    repo_owner: str,
    repo_name: str,
    tag_name: str,
    token: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch one immutable release by its exact tag, never by ``latest``."""
    if not isinstance(tag_name, str) or not tag_name:
        raise ValueError("release tag must be a non-empty string")
    encoded_tag = quote(tag_name, safe="")
    url = f"{GITHUB_API_BASE}/repos/{repo_owner}/{repo_name}/releases/tags/{encoded_tag}"
    headers = _get_headers(token)
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        _check_rate_limit(response)
        response.raise_for_status()
        release_data = response.json()
        if not isinstance(release_data, dict):
            raise TypeError("GitHub release response must be an object")
        required_fields = ["tag_name", "published_at", "assets"]
        missing_fields = [field for field in required_fields if field not in release_data]
        if missing_fields:
            raise ValueError(f"Invalid release data: missing {', '.join(missing_fields)}")
        if release_data["tag_name"] != tag_name:
            raise ValueError("exact-tag lookup returned a different release tag")
        require_immutable_release(release_data)
        return release_data
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise ValueError(
                f"Release tag {tag_name!r} was not found for {repo_owner}/{repo_name}"
            ) from exc
        raise
    except requests.RequestException as exc:
        console.print(f"[red]Error fetching release: {exc}[/red]")
        raise


def parse_release_version(tag_name: str) -> str:
    """Parse version string from GitHub release tag.

    Converts tag format (v2026.01.22-2) to version string (2026.01.22-2).

    Args:
        tag_name: GitHub release tag (e.g., "v2026.01.22-2", "v1.0.0").

    Returns:
        Version string without 'v' prefix (e.g., "2026.01.22-2", "1.0.0").
    """
    return tag_name.removeprefix("v")


def validate_release_receipt(document: Any) -> dict[str, Any]:
    """Validate and canonicalize the complete identity of one upstream asset."""
    if not isinstance(document, dict) or set(document) != RELEASE_RECEIPT_KEYS:
        raise ValueError(f"release receipt must contain exactly {sorted(RELEASE_RECEIPT_KEYS)}")

    for field in ("version", "tag_name", "published_at", "asset_name", "asset_digest"):
        if not isinstance(document[field], str) or not document[field]:
            raise ValueError(f"release receipt {field!r} must be a non-empty string")
    if document["version"].startswith("v") or document["tag_name"] != f"v{document['version']}":
        raise ValueError("release receipt version does not match tag_name")
    if not _CANONICAL_UTC_TIMESTAMP.fullmatch(document["published_at"]):
        raise ValueError("release receipt published_at must be canonical UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.strptime(document["published_at"], "%Y-%m-%dT%H:%M:%S%z")
    except ValueError as exc:
        raise ValueError("release receipt published_at is not a valid UTC timestamp") from exc
    if Path(document["asset_name"]).name != document["asset_name"]:
        raise ValueError("release receipt asset_name must be a filename")
    if (
        not isinstance(document["asset_size"], int)
        or isinstance(document["asset_size"], bool)
        or document["asset_size"] <= 0
    ):
        raise ValueError("release receipt asset_size must be a positive integer")
    if not _SHA256_DIGEST.fullmatch(document["asset_digest"]):
        raise ValueError("release receipt asset_digest must be a SHA-256 digest")

    return {key: document[key] for key in RELEASE_RECEIPT_FIELDS}


def release_receipt(release_data: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    """Create the canonical receipt for one release asset."""
    return validate_release_receipt(
        {
            "version": parse_release_version(release_data["tag_name"]),
            "tag_name": release_data["tag_name"],
            "published_at": release_data["published_at"],
            "asset_name": asset["name"],
            "asset_size": asset["size"],
            "asset_digest": validated_asset_digest(asset),
        }
    )


def load_release_receipt(receipt_file: Path) -> dict[str, Any]:
    """Read one exact release receipt from disk."""
    try:
        document = json.loads(receipt_file.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read release receipt {receipt_file}: {exc}") from exc
    return validate_release_receipt(document)


def write_release_receipt(receipt_file: Path, receipt: dict[str, Any]) -> None:
    """Write a validated receipt deterministically."""
    canonical = validate_release_receipt(receipt)
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_text(json.dumps(canonical, indent=2) + "\n")


def resolve_release_receipt(
    repo_owner: str,
    repo_name: str,
    receipt: dict[str, Any],
    *,
    token: str | None = None,
    asset_pattern: str = "*.zip",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve and verify the exact remote release named by *receipt*."""
    expected = validate_release_receipt(receipt)
    if not fnmatch.fnmatch(expected["asset_name"], asset_pattern):
        raise ValueError("release receipt asset_name does not match the configured asset pattern")
    release_data = get_release_by_tag(
        repo_owner,
        repo_name,
        expected["tag_name"],
        token=token,
    )
    raw_assets = release_data.get("assets")
    if not isinstance(raw_assets, list):
        raise TypeError("exact release assets must be a list")
    zip_assets = [
        asset
        for asset in raw_assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and asset["name"].lower().endswith(".zip")
    ]
    if len(zip_assets) != 1:
        raise ValueError(
            f"exact release must contain exactly one ZIP asset; found {len(zip_assets)}"
        )
    asset = zip_assets[0]
    if asset["name"] != expected["asset_name"]:
        raise ValueError("exact release's sole ZIP asset is not the receipt-named asset")
    actual = release_receipt(release_data, asset)
    mismatches = sorted(field for field in RELEASE_RECEIPT_KEYS if actual[field] != expected[field])
    if mismatches:
        raise ValueError(f"release receipt identity mismatch: {', '.join(mismatches)}")
    return release_data, asset


def require_immutable_release(release_data: dict[str, Any]) -> None:
    """Reject a release whose assets are not immutable at the source."""
    if release_data.get("immutable") is not True:
        tag = release_data.get("tag_name", "<unknown>")
        raise ValueError(f"release {tag!r} is not immutable")


def validated_asset_digest(asset: dict[str, Any]) -> str:
    """Return GitHub's content digest for an asset, failing closed if absent."""
    digest = asset.get("digest")
    if not isinstance(digest, str) or not _SHA256_DIGEST.fullmatch(digest):
        name = asset.get("name", "<unnamed>")
        raise ValueError(f"release asset {name!r} has no valid SHA-256 digest")
    return digest


def file_sha256_digest(path: Path) -> str:
    """Return *path*'s SHA-256 digest in GitHub's release-asset format."""
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def save_release_metadata(
    release_data: dict[str, Any],
    asset_name: str,
    asset_size: int,
    asset_digest: str,
    version_file: Path,
) -> None:
    """Save release metadata to local tracking file.

    Creates .github_release JSON file with version, timestamp, and asset information
    for cache validation on subsequent downloads.

    Args:
        release_data: GitHub release metadata from get_latest_release().
        asset_name: Name of downloaded asset file.
        asset_size: Size of downloaded asset in bytes.
        asset_digest: Verified SHA-256 identity supplied by GitHub.
        version_file: Path to .github_release tracking file.
    """
    version_file.parent.mkdir(parents=True, exist_ok=True)
    metadata = release_receipt(
        release_data,
        {"name": asset_name, "size": asset_size, "digest": asset_digest},
    )
    write_release_receipt(version_file, metadata)


def find_release_asset(release_data: dict[str, Any], pattern: str) -> dict[str, Any] | None:
    """Find release asset matching filename pattern.

    Args:
        release_data: GitHub release metadata from get_latest_release().
        pattern: Glob-style pattern to match asset names (e.g., "*.zip").

    Returns:
        Asset dictionary with 'name', 'browser_download_url', 'size', etc.
        Returns None if no matching asset found.
    """
    for asset in release_data.get("assets", []):
        if fnmatch.fnmatch(asset.get("name", ""), pattern):
            return asset

    return None


def require_release_asset(release_data: dict[str, Any], pattern: str) -> dict[str, Any]:
    """Return the sole selected release asset, rejecting absent or ambiguous input."""
    raw_assets = release_data.get("assets")
    if not isinstance(raw_assets, list):
        raise TypeError("release assets are not a list")
    matches = [
        asset
        for asset in raw_assets
        if isinstance(asset, dict)
        and isinstance(asset.get("name"), str)
        and fnmatch.fnmatch(asset["name"], pattern)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"release must contain exactly one asset matching {pattern!r}; found {len(matches)}"
        )
    return matches[0]


def download_release_asset(
    asset_url: str,
    output_path: Path,
    token: str | None = None,
    timeout: int = 300,
) -> bool:
    """Download GitHub release asset with progress indication.

    Args:
        asset_url: Browser download URL from asset metadata.
        output_path: Local path where asset will be saved.
        token: Optional GitHub token for authentication.
        timeout: HTTP request timeout in seconds.

    Returns:
        True if download succeeded, False otherwise.

    Security:
        Validates asset_url is from github.com domain before downloading.
    """
    # Security: Validate URL is from GitHub
    if not asset_url.startswith("https://github.com/"):
        console.print(f"[red]Security: Rejecting non-GitHub URL: {asset_url}[/red]")
        return False
    headers = _get_headers(token)

    try:
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            response = requests.get(asset_url, headers=headers, stream=True, timeout=timeout)
            _check_rate_limit(response)
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))
            task = progress.add_task(
                f"Downloading {output_path.name}...",
                total=total_size,
            )

            output_path.parent.mkdir(parents=True, exist_ok=True)

            with output_path.open("wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

        console.print(f"[green]✅ Downloaded to {output_path}[/green]")
        return True

    except requests.RequestException as e:
        console.print(f"[red]Error downloading asset: {e}[/red]")
        output_path.unlink(missing_ok=True)  # Clean up partial download
        return False
