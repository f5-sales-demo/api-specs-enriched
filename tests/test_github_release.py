#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for GitHub release utility module."""

import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest
import requests

from scripts.download import verify_release_asset_digest
from scripts.utils.github_release import (
    check_for_updates,
    download_release_asset,
    find_release_asset,
    get_latest_release,
    get_local_release_version,
    parse_release_version,
    save_release_metadata,
)


class TestParseReleaseVersion:
    """Test release version parsing."""

    def test_parse_version_with_v_prefix(self):
        """Test parsing version with 'v' prefix."""
        assert parse_release_version("v2026.01.22-2") == "2026.01.22-2"

    def test_parse_version_without_v_prefix(self):
        """Test parsing version without 'v' prefix."""
        assert parse_release_version("2026.01.22-2") == "2026.01.22-2"

    def test_parse_semver_version(self):
        """Test parsing semver-style version."""
        assert parse_release_version("v1.2.3") == "1.2.3"
        assert parse_release_version("1.2.3") == "1.2.3"


class TestGetLocalReleaseVersion:
    """Test reading local release version."""

    def test_file_not_exists(self, tmp_path):
        """Test when version file doesn't exist."""
        version_file = tmp_path / ".github_release"
        result = get_local_release_version(version_file)
        assert result is None

    def test_valid_version_file(self, tmp_path):
        """Test reading valid version file."""
        version_file = tmp_path / ".github_release"
        metadata = {
            "version": "2026.01.22-2",
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
        }
        version_file.write_text(json.dumps(metadata))

        result = get_local_release_version(version_file)
        assert result == "2026.01.22-2"

    def test_invalid_json(self, tmp_path):
        """Test handling of invalid JSON."""
        version_file = tmp_path / ".github_release"
        version_file.write_text("invalid json")

        result = get_local_release_version(version_file)
        assert result is None

    def test_missing_version_field(self, tmp_path):
        """Test handling of missing version field."""
        version_file = tmp_path / ".github_release"
        metadata = {"tag_name": "v2026.01.22-2"}
        version_file.write_text(json.dumps(metadata))

        result = get_local_release_version(version_file)
        assert result is None


class TestSaveReleaseMetadata:
    """Test saving release metadata."""

    def test_save_metadata(self, tmp_path):
        """Test saving metadata creates correct file structure."""
        version_file = tmp_path / ".github_release"
        release_data = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
        }

        save_release_metadata(
            release_data,
            "api-specs-v2026.01.22-2.zip",
            5971024,
            "a" * 64,
            version_file,
        )

        assert version_file.exists()
        metadata = json.loads(version_file.read_text())

        assert metadata["version"] == "2026.01.22-2"
        assert metadata["tag_name"] == "v2026.01.22-2"
        assert metadata["published_at"] == "2026-01-26T10:30:00Z"
        assert metadata["asset_name"] == "api-specs-v2026.01.22-2.zip"
        assert metadata["asset_size"] == 5971024
        assert metadata["asset_sha256"] == "a" * 64

        # .github_release is tracked, so a per-run wall-clock stamp made every
        # download dirty the working tree with a change that says nothing about the
        # specs. `downloaded_at` was written here and read nowhere — every other
        # field identifies WHICH release is in the tree, which is what the file is
        # for. Same reasoning as the artifact stamps in build_stamp.py: a committed
        # value must be a function of the input, or "is the tree what the pipeline
        # produces?" has no answer.
        assert "downloaded_at" not in metadata
        assert set(metadata) == {
            "version",
            "tag_name",
            "published_at",
            "asset_name",
            "asset_size",
            "asset_sha256",
        }


class TestReleaseAssetDigest:
    """Downloaded archive bytes must match the immutable release digest."""

    def test_matching_release_digest_is_recorded(self, tmp_path):
        archive = tmp_path / "api-specs.zip"
        archive.write_bytes(b"verified archive")

        digest = verify_release_asset_digest(
            archive,
            {"digest": "sha256:040a1170825ade3ff37b189dd280153ecfafb99ee929d1cbebb40fe135afdf26"},
        )

        assert digest == "040a1170825ade3ff37b189dd280153ecfafb99ee929d1cbebb40fe135afdf26"

    def test_missing_or_mismatched_release_digest_is_rejected(self, tmp_path):
        archive = tmp_path / "api-specs.zip"
        archive.write_bytes(b"verified archive")

        with pytest.raises(ValueError, match="digest"):
            verify_release_asset_digest(archive, {})
        with pytest.raises(ValueError, match="digest"):
            verify_release_asset_digest(archive, {"digest": "sha256:" + "0" * 64})


class TestFindReleaseAsset:
    """Test finding release assets by pattern."""

    def test_find_matching_asset(self):
        """Test finding asset that matches pattern."""
        release_data = {
            "assets": [
                {"name": "api-specs-v2026.01.22-2.zip", "size": 5971024},
                {"name": "checksums.txt", "size": 256},
            ],
        }

        asset = find_release_asset(release_data, "api-specs-v*.zip")
        assert asset is not None
        assert asset["name"] == "api-specs-v2026.01.22-2.zip"

    def test_no_matching_asset(self):
        """Test when no asset matches pattern."""
        release_data = {
            "assets": [
                {"name": "checksums.txt", "size": 256},
            ],
        }

        asset = find_release_asset(release_data, "*.zip")
        assert asset is None

    def test_empty_assets(self):
        """Test with empty assets list."""
        release_data = {"assets": []}
        asset = find_release_asset(release_data, "*.zip")
        assert asset is None


class TestGetLatestRelease:
    """Test fetching latest release from GitHub API."""

    @patch("scripts.utils.github_release.requests.get")
    def test_successful_fetch(self, mock_get):
        """Test successful API fetch."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "100",
            "X-RateLimit-Limit": "5000",
        }
        mock_response.json.return_value = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [{"name": "test.zip"}],
        }
        mock_get.return_value = mock_response

        result = get_latest_release("owner", "repo")

        assert result["tag_name"] == "v2026.01.22-2"
        assert "assets" in result
        mock_get.assert_called_once()

    @patch("scripts.utils.github_release.requests.get")
    def test_404_not_found(self, mock_get):
        """Test handling of repository not found."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.headers = {
            "X-RateLimit-Remaining": "100",
            "X-RateLimit-Limit": "5000",
        }
        mock_response.raise_for_status.side_effect = requests.HTTPError(response=mock_response)
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="No releases found"):
            get_latest_release("owner", "repo")

    @patch("scripts.utils.github_release.requests.get")
    def test_rate_limit_warning(self, mock_get, capsys):
        """Test rate limit warning when approaching limit."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "5",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Reset": str(int(datetime.now(timezone.utc).timestamp()) + 3600),
        }
        mock_response.json.return_value = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [{"name": "test.zip"}],
        }
        mock_get.return_value = mock_response

        get_latest_release("owner", "repo")

        # Should print rate limit warning
        # Note: This test verifies the function runs without error
        # Console output testing would require additional setup

    @patch("scripts.utils.github_release.requests.get")
    def test_with_authentication(self, mock_get):
        """Test API call with authentication token."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "5000",
            "X-RateLimit-Limit": "5000",
        }
        mock_response.json.return_value = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [{"name": "test.zip"}],
        }
        mock_get.return_value = mock_response

        get_latest_release("owner", "repo", token="test-token")

        # Verify Authorization header was included
        call_args = mock_get.call_args
        headers = call_args[1]["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-token"


class TestDownloadReleaseAsset:
    """Test downloading release assets."""

    @patch("scripts.utils.github_release.requests.get")
    def test_successful_download(self, mock_get, tmp_path):
        """Test successful asset download."""
        output_path = tmp_path / "test.zip"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-length": "1024",
            "X-RateLimit-Remaining": "100",
            "X-RateLimit-Limit": "5000",
        }
        mock_response.iter_content = lambda chunk_size: [b"test data"]
        mock_get.return_value = mock_response

        result = download_release_asset(
            "https://github.com/owner/repo/releases/download/v1.0.0/test.zip",
            output_path,
        )

        assert result is True
        assert output_path.exists()
        assert output_path.read_bytes() == b"test data"

    @patch("scripts.utils.github_release.requests.get")
    def test_non_github_url_rejected(self, mock_get, tmp_path):
        """Test rejection of non-GitHub URLs."""
        output_path = tmp_path / "test.zip"

        result = download_release_asset(
            "https://malicious-site.com/file.zip",
            output_path,
        )

        assert result is False
        mock_get.assert_not_called()

    @patch("scripts.utils.github_release.requests.get")
    def test_network_error(self, mock_get, tmp_path):
        """Test handling of network errors."""
        output_path = tmp_path / "test.zip"

        mock_get.side_effect = requests.RequestException("Network error")

        result = download_release_asset(
            "https://github.com/owner/repo/releases/download/v1.0.0/test.zip",
            output_path,
        )

        assert result is False
        assert not output_path.exists()  # Partial download should be cleaned up


class TestCheckForUpdates:
    """Test checking for release updates."""

    @patch("scripts.utils.github_release.get_latest_release")
    def test_no_updates_same_version(self, mock_get_release, tmp_path):
        """Test when local and remote versions match."""
        version_file = tmp_path / ".github_release"
        metadata = {
            "version": "2026.01.22-2",
            "tag_name": "v2026.01.22-2",
        }
        version_file.write_text(json.dumps(metadata))

        mock_get_release.return_value = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [],
        }

        has_updates, release_data = check_for_updates(
            "owner",
            "repo",
            version_file,
        )

        assert has_updates is False
        assert release_data is not None

    @patch("scripts.utils.github_release.get_latest_release")
    def test_updates_available(self, mock_get_release, tmp_path):
        """Test when newer version is available."""
        version_file = tmp_path / ".github_release"
        metadata = {
            "version": "2026.01.22-1",
            "tag_name": "v2026.01.22-1",
        }
        version_file.write_text(json.dumps(metadata))

        mock_get_release.return_value = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [],
        }

        has_updates, release_data = check_for_updates(
            "owner",
            "repo",
            version_file,
        )

        assert has_updates is True
        assert release_data["tag_name"] == "v2026.01.22-2"

    @patch("scripts.utils.github_release.get_latest_release")
    def test_first_download(self, mock_get_release, tmp_path):
        """Test when no local version exists."""
        version_file = tmp_path / ".github_release"
        # File doesn't exist

        mock_get_release.return_value = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [],
        }

        has_updates, release_data = check_for_updates(
            "owner",
            "repo",
            version_file,
        )

        assert has_updates is True
        assert release_data["tag_name"] == "v2026.01.22-2"

    @patch("scripts.utils.github_release.get_latest_release")
    def test_api_error_assumes_update_needed(self, mock_get_release, tmp_path):
        """Test that API errors assume update is needed."""
        version_file = tmp_path / ".github_release"

        mock_get_release.side_effect = requests.RequestException("API error")

        has_updates, release_data = check_for_updates(
            "owner",
            "repo",
            version_file,
        )

        assert has_updates is True  # Assume update needed on error
        assert release_data is None
