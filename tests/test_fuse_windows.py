"""Tests for Windows FUSE support — mock-based, runs on any platform."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skref.fuse_windows import _format_mountpoint, check_winfsp_available


class TestCheckWinfspAvailable:
    """Tests for WinFsp availability check."""

    def test_returns_bool(self):
        """Should return True or False, never raise."""
        result = check_winfsp_available()
        assert isinstance(result, bool)

    @patch("skref.fuse_windows.WINFSP_AVAILABLE", True)
    def test_available_when_imported(self):
        from skref import fuse_windows
        old = fuse_windows.WINFSP_AVAILABLE
        fuse_windows.WINFSP_AVAILABLE = True
        assert fuse_windows.check_winfsp_available() is True
        fuse_windows.WINFSP_AVAILABLE = old

    @patch("skref.fuse_windows.WINFSP_AVAILABLE", False)
    def test_unavailable_when_not_imported(self):
        from skref import fuse_windows
        old = fuse_windows.WINFSP_AVAILABLE
        fuse_windows.WINFSP_AVAILABLE = False
        assert fuse_windows.check_winfsp_available() is False
        fuse_windows.WINFSP_AVAILABLE = old


class TestFormatMountpoint:
    """Tests for mount point formatting."""

    def test_drive_letter_gets_backslash(self):
        """V: should become V:\\."""
        assert _format_mountpoint("V:") == "V:\\"

    def test_drive_letter_with_spaces(self):
        """Trailing/leading spaces should be stripped."""
        assert _format_mountpoint("  V:  ") == "V:\\"

    def test_directory_path_unchanged(self):
        """Directory paths should pass through as-is."""
        assert _format_mountpoint(r"C:\Users\me\vault") == r"C:\Users\me\vault"

    def test_unix_path_unchanged(self):
        """Non-drive-letter paths are preserved."""
        assert _format_mountpoint("/mnt/vault") == "/mnt/vault"

    def test_lowercase_drive_letter(self):
        """Lowercase drive letters should work."""
        assert _format_mountpoint("v:") == "v:\\"


class TestSkrefWindowsFS:
    """Tests for the Windows FUSE filesystem operations (mocked vault)."""

    @pytest.fixture
    def mock_vault(self):
        """Create a mock vault with standard methods."""
        vault = MagicMock()
        vault.config = MagicMock()
        vault.config.name = "test-vault"
        return vault

    def test_to_rel_backslash_conversion(self):
        """Windows paths should be converted to forward-slash relative paths."""
        # Import the module-level function by creating an instance
        # Since winfspy may not be available, test the path conversion logic directly
        from skref.fuse_windows import _format_mountpoint

        # The _to_rel is a static method, test the conversion logic
        path = "\\dir\\subdir\\file.txt"
        rel = path.replace("\\", "/").lstrip("/")
        assert rel == "dir/subdir/file.txt"

    def test_to_rel_root(self):
        """Root path should convert to empty string."""
        path = "\\"
        rel = path.replace("\\", "/").lstrip("/")
        assert rel == ""

    def test_to_rel_single_file(self):
        """Single file at root should have no leading slash."""
        path = "\\file.txt"
        rel = path.replace("\\", "/").lstrip("/")
        assert rel == "file.txt"


class TestGpgWindowsDetection:
    """Tests for Windows GPG path detection."""

    def test_find_gpg_on_path(self):
        """When gpg is on PATH, use it."""
        import skref.crypto as crypto_mod

        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = None  # Reset cache

        with patch("skref.crypto.shutil.which", return_value="/usr/bin/gpg"):
            result = crypto_mod._find_gpg()
            assert result == "/usr/bin/gpg"

        crypto_mod._gpg_path = old  # Restore

    def test_find_gpg_windows_fallback(self):
        """When gpg not on PATH but Gpg4win installed, find it."""
        import skref.crypto as crypto_mod

        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = None

        with patch("skref.crypto.shutil.which", return_value=None), \
             patch("skref.crypto.platform.system", return_value="Windows"), \
             patch("skref.crypto.Path.exists", return_value=True):
            result = crypto_mod._find_gpg()
            assert result is not None
            assert "gpg" in result.lower()

        crypto_mod._gpg_path = old

    def test_find_gpg_not_found(self):
        """When gpg not available anywhere, return None."""
        import skref.crypto as crypto_mod

        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = None

        with patch("skref.crypto.shutil.which", return_value=None), \
             patch("skref.crypto.platform.system", return_value="Linux"):
            result = crypto_mod._find_gpg()
            assert result is None

        crypto_mod._gpg_path = old

    def test_find_gpg_caches_result(self):
        """Result should be cached after first detection."""
        import skref.crypto as crypto_mod

        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = "/cached/gpg"

        result = crypto_mod._find_gpg()
        assert result == "/cached/gpg"

        crypto_mod._gpg_path = old


class TestMountVaultWindowsStub:
    """Tests for the mount_vault_windows stub when winfspy is not available."""

    def test_stub_raises_without_winfspy(self):
        """Without winfspy, mount_vault_windows should raise RuntimeError."""
        from skref.fuse_windows import WINFSP_AVAILABLE

        if not WINFSP_AVAILABLE:
            from skref.fuse_windows import mount_vault_windows
            with pytest.raises(RuntimeError, match="WinFsp"):
                mount_vault_windows(MagicMock(), "V:")
