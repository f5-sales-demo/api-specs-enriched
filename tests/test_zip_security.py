# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Security tests for ZIP extraction."""

import zipfile

import pytest

from scripts.download import extract_zip, validate_zip_member_path, validate_zip_member_size

_TEST_LIMITS: dict = {
    "max_file_size": 10 * 1024 * 1024,
    "max_compression_ratio": 100,
}

_TEST_CONFIG: dict = {
    "extraction": {
        "include_patterns": ["*.json"],
        "exclude_patterns": [],
        "max_file_size": 10 * 1024 * 1024,
        "max_total_size": 500 * 1024 * 1024,
        "max_compression_ratio": 100,
        "max_file_count": 1000,
    },
}


class TestPathTraversalProtection:
    """Test path traversal attack prevention."""

    def test_reject_absolute_paths(self):
        """Reject absolute paths."""
        assert not validate_zip_member_path("/etc/passwd")
        assert not validate_zip_member_path("/tmp/evil.json")

    def test_reject_parent_directory_traversal(self):
        """Reject ../ traversal attempts."""
        assert not validate_zip_member_path("../../../etc/passwd")
        assert not validate_zip_member_path("foo/../../evil.json")

    def test_accept_safe_paths(self):
        """Accept safe relative paths."""
        assert validate_zip_member_path("api.json")
        assert validate_zip_member_path("foo/bar/api.json")

    def test_malicious_zip_extraction_fails(self, tmp_path):
        """Test extraction of malicious ZIP fails safely."""
        # Create malicious ZIP with path traversal
        evil_zip = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil_zip, "w") as zf:
            zf.writestr("../../../etc/passwd.json", "malicious content")

        output = tmp_path / "output"
        with pytest.raises(ValueError, match="Unsafe included archive member"):
            extract_zip(evil_zip, output, _TEST_CONFIG)

        assert not (tmp_path / ".." / ".." / ".." / "etc" / "passwd.json").exists()


class TestZipBombProtection:
    """Test zip bomb attack prevention."""

    def test_reject_large_files(self):
        """Reject files exceeding size limit."""
        info = zipfile.ZipInfo("huge.json")
        info.file_size = 100 * 1024 * 1024  # 100 MB
        info.compress_size = 1024  # 1 KB compressed

        is_valid, msg = validate_zip_member_size(info, _TEST_LIMITS)
        assert not is_valid
        assert "too large" in msg.lower()

    def test_reject_suspicious_compression(self):
        """Reject files with suspicious compression ratios."""
        info = zipfile.ZipInfo("bomb.json")
        info.file_size = 5 * 1024 * 1024  # 5 MB uncompressed (under 10 MB limit)
        info.compress_size = 10 * 1024  # 10 KB compressed (500:1 ratio - suspicious!)

        is_valid, msg = validate_zip_member_size(info, _TEST_LIMITS)
        assert not is_valid
        assert "compression ratio" in msg.lower()

    def test_accept_normal_compression(self):
        """Accept files with normal compression."""
        info = zipfile.ZipInfo("normal.json")
        info.file_size = 100 * 1024  # 100 KB
        info.compress_size = 10 * 1024  # 10 KB (10:1 ratio)

        is_valid, msg = validate_zip_member_size(info, _TEST_LIMITS)
        assert is_valid

    def test_reject_nonempty_file_with_zero_compressed_size(self):
        info = zipfile.ZipInfo("invalid.json")
        info.file_size = 1
        info.compress_size = 0

        is_valid, msg = validate_zip_member_size(info, _TEST_LIMITS)

        assert not is_valid
        assert "compressed size" in msg.lower()

    def test_accept_empty_file_with_zero_sizes(self):
        info = zipfile.ZipInfo("empty.json")
        info.file_size = 0
        info.compress_size = 0

        assert validate_zip_member_size(info, _TEST_LIMITS) == (True, "")

    @pytest.mark.parametrize(("file_size", "compress_size"), [(-1, 0), (0, -1), (-1, -1)])
    def test_reject_negative_archive_sizes(self, file_size, compress_size):
        info = zipfile.ZipInfo("invalid.json")
        info.file_size = file_size
        info.compress_size = compress_size

        is_valid, msg = validate_zip_member_size(info, _TEST_LIMITS)

        assert not is_valid
        assert "invalid archive size" in msg.lower()


class TestSafeExtractionIntegration:
    """Test complete extraction flow with security."""

    def test_safe_zip_extraction_succeeds(self, tmp_path):
        """Test normal ZIP extraction works with security checks."""
        # Create a safe ZIP file
        safe_zip = tmp_path / "safe.zip"
        with zipfile.ZipFile(safe_zip, "w") as zf:
            zf.writestr("api1.json", '{"test": "data1"}')
            zf.writestr("api2.json", '{"test": "data2"}')
            zf.writestr("subdir/api3.json", '{"test": "data3"}')  # Should flatten

        output = tmp_path / "output"
        files = extract_zip(safe_zip, output, _TEST_CONFIG)

        # Should extract all 3 JSON files
        assert len(files) == 3
        assert "api1.json" in files
        assert "api2.json" in files
        assert "api3.json" in files  # Flattened from subdir

        # Verify files exist
        assert (output / "api1.json").exists()
        assert (output / "api2.json").exists()
        assert (output / "api3.json").exists()

    def test_mixed_safe_and_unsafe_extraction(self, tmp_path):
        """One unsafe included member rejects the complete candidate."""
        # Create ZIP with mix of safe and unsafe files
        mixed_zip = tmp_path / "mixed.zip"
        with zipfile.ZipFile(mixed_zip, "w") as zf:
            zf.writestr("api1.json", '{"test": "safe"}')
            zf.writestr("../evil.json", '{"test": "malicious"}')  # Should skip
            zf.writestr("api2.json", '{"test": "safe2"}')

        output = tmp_path / "output"
        with pytest.raises(ValueError, match="Unsafe included archive member"):
            extract_zip(mixed_zip, output, _TEST_CONFIG)

        assert not (tmp_path / ".." / "evil.json").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
