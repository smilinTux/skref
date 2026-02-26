"""Tests for Tailscale integration (detection, FQDN, funnel, auth key management)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from skref.tailscale import (
    AUTH_KEY_FILENAME,
    TailscaleStatus,
    authenticate,
    auto_install,
    generate_auth_key,
    get_admin_console_url,
    get_install_hint,
    get_status,
    has_synced_auth_key,
    is_authenticated,
    is_installed,
    load_auth_key,
    save_auth_key,
)


MOCK_TS_STATUS_JSON = json.dumps({
    "Self": {
        "HostName": "my-desktop",
        "DNSName": "my-desktop.tail1234.ts.net.",
        "TailscaleIPs": ["100.64.0.42", "fd7a:115c:a1e0::1"],
        "Capabilities": ["funnel"],
    },
    "MagicDNSSuffix": "tail1234.ts.net",
})


class TestIsInstalled:
    """Tests for is_installed()."""

    @patch("skref.tailscale.shutil.which", return_value="/usr/bin/tailscale")
    def test_found_on_path(self, mock_which: MagicMock) -> None:
        """Returns True when tailscale is on PATH."""
        assert is_installed() is True

    @patch("skref.tailscale.shutil.which", return_value=None)
    @patch("skref.tailscale.platform.system", return_value="Linux")
    def test_not_found_linux(self, mock_sys: MagicMock, mock_which: MagicMock) -> None:
        """Returns False on Linux when not on PATH."""
        assert is_installed() is False


class TestGetStatus:
    """Tests for get_status()."""

    @patch("skref.tailscale._tailscale_bin", return_value=None)
    def test_returns_none_when_not_installed(self, mock_bin: MagicMock) -> None:
        """Returns None if tailscale binary not found."""
        assert get_status() is None

    @patch("skref.tailscale._tailscale_bin", return_value="tailscale")
    @patch("skref.tailscale.subprocess.run")
    def test_parses_status_json(self, mock_run: MagicMock, mock_bin: MagicMock) -> None:
        """Parses tailscale status --json into TailscaleStatus."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=MOCK_TS_STATUS_JSON,
        )
        status = get_status()

        assert status is not None
        assert status.running is True
        assert status.hostname == "my-desktop"
        assert status.dns_name == "my-desktop.tail1234.ts.net."
        assert status.tailnet == "tail1234.ts.net"
        assert status.ip4 == "100.64.0.42"
        assert status.ip6 == "fd7a:115c:a1e0::1"
        assert status.funnel_available is True

    @patch("skref.tailscale._tailscale_bin", return_value="tailscale")
    @patch("skref.tailscale.subprocess.run")
    def test_returns_none_on_failure(self, mock_run: MagicMock, mock_bin: MagicMock) -> None:
        """Returns None if tailscale status exits non-zero."""
        mock_run.return_value = MagicMock(returncode=1, stderr="not running")
        assert get_status() is None


class TestTailscaleStatus:
    """Tests for TailscaleStatus properties."""

    def _make_status(self) -> TailscaleStatus:
        return TailscaleStatus(
            running=True,
            hostname="my-desktop",
            dns_name="my-desktop.tail1234.ts.net.",
            tailnet="tail1234.ts.net",
            ip4="100.64.0.42",
            ip6="fd7a:115c:a1e0::1",
            funnel_available=True,
        )

    def test_fqdn_strips_trailing_dot(self) -> None:
        """FQDN property strips trailing dot from DNS name."""
        status = self._make_status()
        assert status.fqdn == "my-desktop.tail1234.ts.net"

    def test_webdav_url(self) -> None:
        """webdav_url returns correct HTTPS URL."""
        status = self._make_status()
        assert status.webdav_url == "https://my-desktop.tail1234.ts.net:8443/"


class TestGetInstallHint:
    """Tests for platform-specific install hints."""

    @patch("skref.tailscale.platform.system", return_value="Linux")
    def test_linux_hint(self, mock_sys: MagicMock) -> None:
        """Linux hint mentions curl install script."""
        hint = get_install_hint()
        assert "curl" in hint
        assert "tailscale.com" in hint

    @patch("skref.tailscale.platform.system", return_value="Darwin")
    def test_macos_hint(self, mock_sys: MagicMock) -> None:
        """macOS hint mentions download or brew."""
        hint = get_install_hint()
        assert "tailscale.com" in hint

    @patch("skref.tailscale.platform.system", return_value="Windows")
    def test_windows_hint(self, mock_sys: MagicMock) -> None:
        """Windows hint mentions winget or download."""
        hint = get_install_hint()
        assert "tailscale.com" in hint
        assert "winget" in hint or "download" in hint.lower()


# ---------------------------------------------------------------------------
# Auto-install
# ---------------------------------------------------------------------------

class TestAutoInstall:
    """Tests for auto_install()."""

    @patch("skref.tailscale.is_installed", return_value=True)
    def test_skips_if_already_installed(self, mock_inst: MagicMock) -> None:
        """Returns True immediately if already installed."""
        assert auto_install() is True

    @patch("skref.tailscale.is_installed", side_effect=[False, True])
    @patch("skref.tailscale.platform.system", return_value="Linux")
    @patch("skref.tailscale.subprocess.run")
    def test_linux_install(self, mock_run: MagicMock, mock_sys: MagicMock,
                           mock_inst: MagicMock) -> None:
        """Linux install calls the curl install script."""
        mock_run.return_value = MagicMock(returncode=0)
        assert auto_install() is True
        args = mock_run.call_args[0][0]
        assert "bash" in args
        assert "tailscale.com/install.sh" in " ".join(args)

    @patch("skref.tailscale.is_installed", side_effect=[False, False])
    @patch("skref.tailscale.platform.system", return_value="Linux")
    @patch("skref.tailscale.subprocess.run")
    def test_linux_install_failure(self, mock_run: MagicMock, mock_sys: MagicMock,
                                   mock_inst: MagicMock) -> None:
        """Returns False if install fails."""
        mock_run.return_value = MagicMock(returncode=1)
        assert auto_install() is False


# ---------------------------------------------------------------------------
# Authenticate
# ---------------------------------------------------------------------------

class TestAuthenticate:
    """Tests for authenticate()."""

    @patch("skref.tailscale._tailscale_bin", return_value=None)
    def test_returns_false_no_binary(self, mock_bin: MagicMock) -> None:
        """Returns False if tailscale not installed."""
        assert authenticate() is False

    @patch("skref.tailscale._tailscale_bin", return_value="tailscale")
    @patch("skref.tailscale.subprocess.run")
    def test_auth_with_key(self, mock_run: MagicMock, mock_bin: MagicMock) -> None:
        """Uses --auth-key flag when key is provided."""
        mock_run.return_value = MagicMock(returncode=0)
        assert authenticate(auth_key="tskey-auth-abc123") is True
        cmd = mock_run.call_args[0][0]
        assert "--auth-key" in cmd
        assert "tskey-auth-abc123" in cmd

    @patch("skref.tailscale._tailscale_bin", return_value="tailscale")
    @patch("skref.tailscale.subprocess.run")
    def test_auth_browser(self, mock_run: MagicMock, mock_bin: MagicMock) -> None:
        """Without key, calls `tailscale up` for browser auth."""
        mock_run.return_value = MagicMock(returncode=0)
        assert authenticate() is True
        cmd = mock_run.call_args[0][0]
        assert cmd == ["tailscale", "up"]


class TestIsAuthenticated:
    """Tests for is_authenticated()."""

    @patch("skref.tailscale.get_status")
    def test_true_when_running(self, mock_get: MagicMock) -> None:
        """Returns True when get_status returns a running status."""
        mock_get.return_value = TailscaleStatus(
            running=True, hostname="h", dns_name="h.ts.net.",
            tailnet="ts.net", ip4="100.1.2.3", ip6="", funnel_available=True,
        )
        assert is_authenticated() is True

    @patch("skref.tailscale.get_status", return_value=None)
    def test_false_when_not_running(self, mock_get: MagicMock) -> None:
        """Returns False when get_status returns None."""
        assert is_authenticated() is False


# ---------------------------------------------------------------------------
# Auth key save/load
# ---------------------------------------------------------------------------

class TestAuthKeyManagement:
    """Tests for save_auth_key, load_auth_key, has_synced_auth_key."""

    def test_has_synced_auth_key_false(self, tmp_path: Path) -> None:
        """Returns False when no key file exists."""
        assert has_synced_auth_key(sync_dir=tmp_path) is False

    def test_has_synced_auth_key_true(self, tmp_path: Path) -> None:
        """Returns True when key file exists."""
        (tmp_path / AUTH_KEY_FILENAME).write_bytes(b"encrypted-data")
        assert has_synced_auth_key(sync_dir=tmp_path) is True

    @patch("skref.crypto.decrypt_bytes", return_value=b"tskey-auth-abc123")
    def test_load_auth_key(self, mock_decrypt: MagicMock, tmp_path: Path) -> None:
        """Loads and decrypts auth key from sync dir."""
        (tmp_path / AUTH_KEY_FILENAME).write_bytes(b"encrypted-data")
        key = load_auth_key(sync_dir=tmp_path)
        assert key == "tskey-auth-abc123"

    def test_load_auth_key_missing(self, tmp_path: Path) -> None:
        """Returns None when no key file exists."""
        assert load_auth_key(sync_dir=tmp_path) is None

    @patch("skref.crypto.encrypt_bytes", return_value=b"encrypted-data")
    @patch("skref.crypto.detect_key", return_value="ABCDEF1234567890")
    def test_save_auth_key(self, mock_detect: MagicMock,
                           mock_encrypt: MagicMock, tmp_path: Path) -> None:
        """Encrypts and saves auth key to sync dir."""
        result = save_auth_key("tskey-auth-abc123", sync_dir=tmp_path)
        assert result is not None
        assert result.name == AUTH_KEY_FILENAME
        assert result.exists()

    @patch("skref.crypto.detect_key", return_value=None)
    def test_save_auth_key_no_gpg(self, mock_detect: MagicMock, tmp_path: Path) -> None:
        """Returns None when no GPG key is available."""
        result = save_auth_key("tskey-auth-abc123", sync_dir=tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# Generate auth key
# ---------------------------------------------------------------------------

class TestGenerateAuthKey:
    """Tests for generate_auth_key()."""

    @patch("skref.tailscale._tailscale_bin", return_value=None)
    def test_returns_none_no_binary(self, mock_bin: MagicMock) -> None:
        """Returns None if tailscale not installed."""
        assert generate_auth_key() is None

    @patch("skref.tailscale._tailscale_bin", return_value="tailscale")
    @patch("skref.tailscale.subprocess.run")
    def test_generates_key_via_api(self, mock_run: MagicMock, mock_bin: MagicMock) -> None:
        """Generates key when tailscale API succeeds."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({"key": "tskey-auth-generated-xyz"}),
        )
        assert generate_auth_key() == "tskey-auth-generated-xyz"

    @patch("skref.tailscale._tailscale_bin", return_value="tailscale")
    @patch("skref.tailscale.subprocess.run")
    def test_returns_none_api_failure(self, mock_run: MagicMock, mock_bin: MagicMock) -> None:
        """Returns None if API call fails."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        assert generate_auth_key() is None


class TestAdminConsoleUrl:
    """Tests for get_admin_console_url()."""

    def test_returns_url(self) -> None:
        """Returns the Tailscale admin keys URL."""
        url = get_admin_console_url()
        assert "login.tailscale.com" in url
        assert "keys" in url
