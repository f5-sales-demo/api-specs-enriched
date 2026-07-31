#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""GitHub Releases integration for downloading API specifications.

This module provides utilities for fetching release metadata, comparing versions,
and downloading assets from GitHub Releases. Supports authentication via GITHUB_TOKEN
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
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
        reset_dt = datetime.fromtimestamp(reset_time, tz=timezone.utc)
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


def parse_release_version(tag_name: str) -> str:
    """Parse version string from GitHub release tag.

    Converts tag format (v2026.01.22-2) to version string (2026.01.22-2).

    Args:
        tag_name: GitHub release tag (e.g., "v2026.01.22-2", "v1.0.0").

    Returns:
        Version string without 'v' prefix (e.g., "2026.01.22-2", "1.0.0").
    """
    return tag_name.lstrip("v")


def get_local_release_version(version_file: Path) -> str | None:
    """Read stored release version from local tracking file.

    Args:
        version_file: Path to .github_release JSON file.

    Returns:
        Version string from local file (e.g., "2026.01.22-2"), or None if file
        doesn't exist or is invalid.
    """
    if not version_file.exists():
        return None

    try:
        with version_file.open() as f:
            data = json.load(f)
            return data.get("version")
    except (json.JSONDecodeError, KeyError, OSError) as e:
        console.print(f"[yellow]Warning: Could not read version file: {e}[/yellow]")
        return None


def save_release_metadata(
    release_data: dict[str, Any],
    asset_name: str,
    asset_size: int,
    version_file: Path,
) -> None:
    """Save release metadata to local tracking file.

    Creates .github_release JSON file with version, timestamp, and asset information
    for cache validation on subsequent downloads.

    Args:
        release_data: GitHub release metadata from get_latest_release().
        asset_name: Name of downloaded asset file.
        asset_size: Size of downloaded asset in bytes.
        version_file: Path to .github_release tracking file.
    """
    version_file.parent.mkdir(parents=True, exist_ok=True)

    # Every field here identifies WHICH release is in the tree. A `downloaded_at`
    # wall-clock stamp used to sit alongside them; it was written here and read
    # nowhere, and because .github_release is tracked it made each download dirty the
    # working tree with a change that says nothing about the specs. Same rule as the
    # artifact stamps in build_stamp.py: a committed value must be a function of the
    # input.
    metadata = {
        "version": parse_release_version(release_data["tag_name"]),
        "tag_name": release_data["tag_name"],
        "published_at": release_data["published_at"],
        "asset_name": asset_name,
        "asset_size": asset_size,
    }

    with version_file.open("w") as f:
        json.dump(metadata, f, indent=2)
        f.write("\n")


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


def check_for_updates(
    repo_owner: str,
    repo_name: str,
    version_file: Path,
    token: str | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Check if a newer release is available compared to local version.

    Args:
        repo_owner: GitHub repository owner.
        repo_name: GitHub repository name.
        version_file: Path to .github_release tracking file.
        token: Optional GitHub token for authentication.

    Returns:
        Tuple of (has_updates, release_data):
            - has_updates: True if remote version differs from local.
            - release_data: Latest release metadata, or None if check failed.
    """
    try:
        release_data = get_latest_release(repo_owner, repo_name, token=token)
        remote_version = parse_release_version(release_data["tag_name"])

        local_version = get_local_release_version(version_file)

        if local_version == remote_version:
            console.print(
                f"[blue]✅ No updates available (version: {remote_version})[/blue]",
            )
            return False, release_data

        if local_version:
            console.print("[green]🆕 Update available![/green]")
            console.print(f"  Local version:  {local_version}")
            console.print(f"  Remote version: {remote_version}")
        else:
            console.print("[green]🆕 First download - no local version found[/green]")
            console.print(f"  Remote version: {remote_version}")

        return True, release_data

    except (requests.RequestException, ValueError) as e:
        console.print(f"[yellow]Could not check for updates: {e}[/yellow]")
        return True, None  # Assume update needed if check fails
