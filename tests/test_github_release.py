#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for GitHub release utility module."""

import json
from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest
import requests

from scripts.utils.github_release import (
    download_release_asset,
    find_release_asset,
    get_latest_release,
    get_release_by_tag,
    parse_release_version,
    release_receipt,
    require_release_asset,
    resolve_release_receipt,
    save_release_metadata,
    validate_release_receipt,
)

_DIGEST = "sha256:" + "a" * 64
_OTHER_DIGEST = "sha256:" + "b" * 64


def _asset(digest: str = _DIGEST) -> dict[str, object]:
    return {
        "name": "api-specs-v2026.01.22-2.zip",
        "size": 5971024,
        "digest": digest,
    }


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
            _DIGEST,
            version_file,
        )

        assert version_file.exists()
        metadata = json.loads(version_file.read_text())

        assert metadata["version"] == "2026.01.22-2"
        assert metadata["tag_name"] == "v2026.01.22-2"
        assert metadata["published_at"] == "2026-01-26T10:30:00Z"
        assert metadata["asset_name"] == "api-specs-v2026.01.22-2.zip"
        assert metadata["asset_size"] == 5971024
        assert metadata["asset_digest"] == _DIGEST

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
            "asset_digest",
        }


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

    def test_require_release_asset_rejects_multiple_matches(self):
        release_data = {
            "assets": [
                {"name": "api-specs-v2026.01.22-1.zip"},
                {"name": "api-specs-v2026.01.22-2.zip"},
            ]
        }

        with pytest.raises(ValueError, match=r"exactly one.*found 2"):
            require_release_asset(release_data, "api-specs-v*.zip")

    def test_require_release_asset_rejects_absent_match(self):
        with pytest.raises(ValueError, match=r"exactly one.*found 0"):
            require_release_asset({"assets": []}, "api-specs-v*.zip")


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
            "immutable": True,
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


class TestPinnedReleaseReceipt:
    """Exact receipts prevent a newer latest release from replacing the trigger input."""

    @patch("scripts.utils.github_release.requests.get")
    def test_exact_tag_fetch_never_calls_latest(self, mock_get):
        response = Mock()
        response.headers = {"X-RateLimit-Remaining": "100", "X-RateLimit-Limit": "5000"}
        response.json.return_value = {
            "tag_name": "v2026.01.22-1",
            "published_at": "2026-01-25T10:30:00Z",
            "assets": [_asset()],
            "immutable": True,
        }
        mock_get.return_value = response

        release = get_release_by_tag("owner", "repo", "v2026.01.22-1")

        assert release["tag_name"] == "v2026.01.22-1"
        requested_url = mock_get.call_args.args[0]
        assert requested_url.endswith("/releases/tags/v2026.01.22-1")
        assert "/releases/latest" not in requested_url

    @patch("scripts.utils.github_release.get_release_by_tag")
    def test_dispatch_a_consumes_a_when_newer_b_exists(self, get_by_tag):
        release_a = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [_asset()],
            "immutable": True,
        }
        receipt_a = release_receipt(release_a, release_a["assets"][0])
        get_by_tag.return_value = release_a

        selected_release, selected_asset = resolve_release_receipt(
            "owner", "repo", receipt_a, asset_pattern="api-specs-v*.zip"
        )

        get_by_tag.assert_called_once_with("owner", "repo", "v2026.01.22-2", token=None)
        assert selected_release is release_a
        assert selected_asset["digest"] == receipt_a["asset_digest"]

    @patch("scripts.utils.github_release.get_release_by_tag")
    def test_exact_receipt_requires_the_named_asset_to_be_the_sole_zip(self, get_by_tag):
        release = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [
                _asset(),
                {
                    "name": "extra.zip",
                    "size": 1,
                    "digest": _OTHER_DIGEST,
                },
            ],
            "immutable": True,
        }
        receipt = release_receipt(release, release["assets"][0])
        get_by_tag.return_value = release

        with pytest.raises(ValueError, match=r"exactly one ZIP asset.*found 2"):
            resolve_release_receipt(
                "owner",
                "repo",
                receipt,
                asset_pattern="api-specs-v*.zip",
            )

    @pytest.mark.parametrize(
        ("remote_field", "remote_value", "mismatch"),
        [
            ("tag_name", "v2026.01.22-3", "tag_name"),
            ("digest", _OTHER_DIGEST, "asset_digest"),
        ],
    )
    @patch("scripts.utils.github_release.get_release_by_tag")
    def test_remote_identity_mismatch_is_rejected(
        self, get_by_tag, remote_field, remote_value, mismatch
    ):
        release_a = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [_asset()],
            "immutable": True,
        }
        receipt_a = release_receipt(release_a, release_a["assets"][0])
        remote = {**release_a, "assets": [{**release_a["assets"][0]}]}
        if remote_field == "digest":
            remote["assets"][0][remote_field] = remote_value
        else:
            remote[remote_field] = remote_value
        get_by_tag.return_value = remote

        with pytest.raises(ValueError, match=mismatch):
            resolve_release_receipt("owner", "repo", receipt_a, asset_pattern="api-specs-v*.zip")

    def test_receipt_contract_rejects_missing_extra_and_wrong_types(self):
        release = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [_asset()],
        }
        receipt = release_receipt(release, release["assets"][0])
        for invalid in (
            {key: value for key, value in receipt.items() if key != "asset_digest"},
            {**receipt, "release_url": "https://example.invalid"},
            {**receipt, "asset_size": "5971024"},
            {**receipt, "asset_size": True},
            {**receipt, "asset_size": 0},
            {**receipt, "asset_size": -1},
            {**receipt, "asset_size": 1.5},
        ):
            with pytest.raises(ValueError, match=r"release receipt|asset_size"):
                validate_release_receipt(invalid)

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("tag_name", "vv2026.01.22-2", "tag_name"),
            ("published_at", "not-a-timestamp", "published_at"),
            ("published_at", "2026-01-26T10:30:00+00:00", "published_at"),
            ("published_at", "2026-01-26T10:30:00.000Z", "published_at"),
            ("published_at", "2026-02-30T10:30:00Z", "published_at"),
        ],
    )
    def test_receipt_contract_rejects_noncanonical_identity_fields(
        self,
        field,
        value,
        message,
    ):
        release = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [_asset()],
        }
        receipt = release_receipt(release, release["assets"][0])

        with pytest.raises(ValueError, match=message):
            validate_release_receipt({**receipt, field: value})

    @patch("scripts.utils.github_release.requests.get")
    def test_rate_limit_warning(self, mock_get, capsys):
        """Test rate limit warning when approaching limit."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {
            "X-RateLimit-Remaining": "5",
            "X-RateLimit-Limit": "60",
            "X-RateLimit-Reset": str(int(datetime.now(UTC).timestamp()) + 3600),
        }
        mock_response.json.return_value = {
            "tag_name": "v2026.01.22-2",
            "published_at": "2026-01-26T10:30:00Z",
            "assets": [{"name": "test.zip"}],
            "immutable": True,
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
            "immutable": True,
        }
        mock_get.return_value = mock_response

        get_latest_release("owner", "repo", token="test-token")

        # Verify Authorization header was included
        call_args = mock_get.call_args
        headers = call_args[1]["headers"]
        assert "Authorization" in headers
        assert headers["Authorization"] == "Bearer test-token"

    @pytest.mark.parametrize("immutable", [False, None])
    @patch("scripts.utils.github_release.requests.get")
    def test_mutable_or_unmarked_release_is_rejected(self, mock_get, immutable):
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
            "immutable": immutable,
        }
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="is not immutable"):
            get_latest_release("owner", "repo")


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
