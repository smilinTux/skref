"""
Tailscale integration — install, authenticate, detect, manage Funnel.

Full lifecycle:
  1. Install Tailscale (platform-specific, automated)
  2. Authenticate (browser OAuth or reusable auth key from sync)
  3. Detect FQDN and tailnet IP
  4. Enable Serve + Funnel for WebDAV proxy
  5. Save auth key to Tier 1 sync so next device auto-joins

Auth key flow for second+ devices:
  - First device: user logs in via browser → Tailscale generates auth key
  - Auth key is GPG-encrypted and saved to ~/.skcapstone/sync/tailscale.key.gpg
  - Syncthing replicates to new device
  - New device decrypts auth key with CapAuth PGP key
  - `tailscale up --auth-key=<key>` joins the tailnet automatically
  - Zero browser, zero copy-paste, zero manual anything
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("skref.tailscale")

AUTH_KEY_FILENAME = "tailscale.key.gpg"
AUTH_KEY_PLAIN = "tailscale.key"


def _tailscale_bin() -> Optional[str]:
    """Find the tailscale CLI binary.

    Returns:
        Path to tailscale binary, or None.
    """
    if shutil.which("tailscale"):
        return "tailscale"

    if platform.system() == "Windows":
        from pathlib import Path

        candidates = [
            Path(r"C:\Program Files\Tailscale\tailscale.exe"),
            Path(r"C:\Program Files (x86)\Tailscale\tailscale.exe"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
    return None


def is_installed() -> bool:
    """Check if Tailscale CLI is available.

    Returns:
        True if tailscale binary found on PATH or default install locations.
    """
    return _tailscale_bin() is not None


@dataclass
class TailscaleStatus:
    """Parsed output from `tailscale status --json`."""

    running: bool
    hostname: str
    dns_name: str
    tailnet: str
    ip4: str
    ip6: str
    funnel_available: bool

    @property
    def fqdn(self) -> str:
        """Fully qualified domain name on the tailnet.

        Returns:
            FQDN like 'your-machine.tail1234.ts.net'.
        """
        return self.dns_name.rstrip(".")

    @property
    def webdav_url(self) -> str:
        """HTTPS URL for WebDAV proxy via Funnel.

        Returns:
            URL like 'https://your-machine.tail1234.ts.net:8443/'.
        """
        return f"https://{self.fqdn}:8443/"


def get_status() -> Optional[TailscaleStatus]:
    """Get current Tailscale status.

    Returns:
        TailscaleStatus if Tailscale is running, None otherwise.
    """
    ts = _tailscale_bin()
    if not ts:
        return None

    try:
        result = subprocess.run(
            [ts, "status", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            logger.debug("tailscale status failed: %s", result.stderr)
            return None

        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.debug("tailscale status error: %s", exc)
        return None

    self_node = data.get("Self", {})
    hostname = self_node.get("HostName", "")
    dns_name = self_node.get("DNSName", "")
    tailnet = data.get("MagicDNSSuffix", "")

    addrs = self_node.get("TailscaleIPs", [])
    ip4 = next((a for a in addrs if "." in a), "")
    ip6 = next((a for a in addrs if ":" in a), "")

    # Funnel requires HTTPS on tailnet — check if the node has it enabled
    caps = self_node.get("Capabilities", []) or []
    funnel_available = any("funnel" in c.lower() for c in caps)

    # If we can't determine funnel from caps, assume available if tailnet has MagicDNS
    if not funnel_available and dns_name:
        funnel_available = True

    return TailscaleStatus(
        running=True,
        hostname=hostname,
        dns_name=dns_name,
        tailnet=tailnet,
        ip4=ip4,
        ip6=ip6,
        funnel_available=funnel_available,
    )


def enable_funnel(port: int = 8443) -> bool:
    """Enable Tailscale Funnel for a port.

    This makes the port publicly accessible via your Tailscale FQDN
    with a valid Let's Encrypt TLS certificate.

    Args:
        port: Local port to expose (default 8443).

    Returns:
        True if funnel was successfully enabled.
    """
    ts = _tailscale_bin()
    if not ts:
        return False

    try:
        result = subprocess.run(
            [ts, "funnel", str(port)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Tailscale Funnel enabled on port %d", port)
            return True
        logger.warning("Funnel enable failed: %s", result.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Funnel enable error: %s", exc)
    return False


def disable_funnel(port: int = 8443) -> bool:
    """Disable Tailscale Funnel for a port.

    Args:
        port: Port to stop exposing.

    Returns:
        True if funnel was successfully disabled.
    """
    ts = _tailscale_bin()
    if not ts:
        return False

    try:
        result = subprocess.run(
            [ts, "funnel", "off", str(port)],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def serve_background(port: int = 8443) -> bool:
    """Set up Tailscale serve to proxy HTTPS traffic to a local port.

    `tailscale serve https / http://127.0.0.1:<port>` makes the local
    service accessible at https://<fqdn>/ with automatic TLS.

    Args:
        port: Local HTTP port to proxy to.

    Returns:
        True on success.
    """
    ts = _tailscale_bin()
    if not ts:
        return False

    try:
        result = subprocess.run(
            [ts, "serve", "https", "/", f"http://127.0.0.1:{port}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            logger.info("Tailscale serve configured: https → 127.0.0.1:%d", port)
            return True
        logger.warning("Tailscale serve failed: %s", result.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Tailscale serve error: %s", exc)
    return False


def get_install_hint() -> str:
    """Platform-specific Tailscale install instructions.

    Returns:
        Human-readable install instructions.
    """
    system = platform.system()
    if system == "Linux":
        return (
            "Install Tailscale:\n"
            "  curl -fsSL https://tailscale.com/install.sh | sh\n"
            "  sudo tailscale up\n"
            "  tailscale funnel 8443  # to expose WebDAV proxy"
        )
    elif system == "Darwin":
        return (
            "Install Tailscale:\n"
            "  Download from https://tailscale.com/download/mac\n"
            "  Or: brew install tailscale\n"
            "  Then: tailscale funnel 8443"
        )
    elif system == "Windows":
        return (
            "Install Tailscale:\n"
            "  Download from https://tailscale.com/download/windows\n"
            "  Or: winget install Tailscale.Tailscale\n"
            "  Then: tailscale funnel 8443"
        )
    return "Install Tailscale: https://tailscale.com/download"


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

def auto_install() -> bool:
    """Install Tailscale automatically for the current platform.

    Linux:   curl -fsSL https://tailscale.com/install.sh | sh
    macOS:   brew install tailscale (if brew available)
    Windows: winget install Tailscale.Tailscale

    Returns:
        True if install succeeded and tailscale binary is now available.
    """
    if is_installed():
        return True

    system = platform.system()
    try:
        if system == "Linux":
            result = subprocess.run(
                ["bash", "-c", "curl -fsSL https://tailscale.com/install.sh | sh"],
                capture_output=True, text=True, timeout=120,
            )
            return result.returncode == 0 and is_installed()

        elif system == "Darwin":
            if shutil.which("brew"):
                result = subprocess.run(
                    ["brew", "install", "tailscale"],
                    capture_output=True, text=True, timeout=120,
                )
                return result.returncode == 0 and is_installed()

        elif system == "Windows":
            if shutil.which("winget"):
                result = subprocess.run(
                    ["winget", "install", "--id", "Tailscale.Tailscale",
                     "--accept-source-agreements", "--accept-package-agreements"],
                    capture_output=True, text=True, timeout=180,
                )
                return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as exc:
        logger.warning("Tailscale auto-install failed: %s", exc)

    return False


# ---------------------------------------------------------------------------
# Authenticate
# ---------------------------------------------------------------------------

def authenticate(auth_key: Optional[str] = None) -> bool:
    """Authenticate this device to a Tailscale tailnet.

    If auth_key is provided, uses `tailscale up --auth-key` for
    headless join (no browser needed — used for second+ devices).

    If auth_key is None, uses `tailscale up` which opens a browser
    for OAuth login (used for the first device).

    Args:
        auth_key: Reusable auth key from Tailscale admin or synced file.

    Returns:
        True if authentication succeeded.
    """
    ts = _tailscale_bin()
    if not ts:
        return False

    cmd = [ts, "up"]
    if auth_key:
        cmd.extend(["--auth-key", auth_key])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("Tailscale authentication succeeded")
            return True
        logger.warning("Tailscale auth failed: %s", result.stderr)
    except subprocess.TimeoutExpired:
        logger.warning("Tailscale auth timed out (browser login may still be pending)")
    except OSError as exc:
        logger.warning("Tailscale auth error: %s", exc)

    return False


def is_authenticated() -> bool:
    """Check if Tailscale is authenticated and connected.

    Returns:
        True if tailscale status shows a connected state.
    """
    status = get_status()
    return status is not None and status.running


# ---------------------------------------------------------------------------
# Auth key management — save/load from Tier 1 sync (GPG-encrypted)
# ---------------------------------------------------------------------------

def _sync_dir(override: Optional[Path] = None) -> Path:
    """Resolve the Tier 1 sync directory."""
    return override or Path("~/.skcapstone/sync").expanduser()


def save_auth_key(
    auth_key: str,
    gpg_fingerprint: Optional[str] = None,
    sync_dir: Optional[Path] = None,
) -> Optional[Path]:
    """GPG-encrypt and save an auth key to the Tier 1 sync folder.

    The encrypted file syncs to all devices via Syncthing. Each device
    can decrypt it with the CapAuth PGP key to join the same tailnet.

    Args:
        auth_key: Tailscale reusable auth key.
        gpg_fingerprint: GPG key to encrypt to. Auto-detected if None.
        sync_dir: Override sync folder.

    Returns:
        Path to the encrypted file, or None on failure.
    """
    from .crypto import detect_key, encrypt_bytes

    fp = gpg_fingerprint or detect_key()
    if not fp:
        logger.warning("Cannot save auth key: no GPG key found")
        return None

    dest = _sync_dir(sync_dir) / AUTH_KEY_FILENAME
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        ciphertext = encrypt_bytes(auth_key.encode("utf-8"), fp)
        dest.write_bytes(ciphertext)
        logger.info("Auth key saved to %s", dest)
        return dest
    except RuntimeError as exc:
        logger.warning("Failed to encrypt auth key: %s", exc)
        return None


def load_auth_key(sync_dir: Optional[Path] = None) -> Optional[str]:
    """Load and decrypt a Tailscale auth key from the Tier 1 sync folder.

    Used on second+ devices to auto-join the same tailnet without
    requiring a browser login.

    Args:
        sync_dir: Override sync folder.

    Returns:
        Decrypted auth key string, or None if not found / decrypt failed.
    """
    from .crypto import decrypt_bytes

    key_path = _sync_dir(sync_dir) / AUTH_KEY_FILENAME
    if not key_path.exists():
        return None

    try:
        ciphertext = key_path.read_bytes()
        plaintext = decrypt_bytes(ciphertext)
        key = plaintext.decode("utf-8").strip()
        if key:
            logger.info("Auth key loaded from %s", key_path)
            return key
    except RuntimeError as exc:
        logger.warning("Failed to decrypt auth key: %s", exc)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read auth key: %s", exc)

    return None


def has_synced_auth_key(sync_dir: Optional[Path] = None) -> bool:
    """Check if a synced auth key exists (without decrypting).

    Args:
        sync_dir: Override sync folder.

    Returns:
        True if the encrypted auth key file exists.
    """
    return (_sync_dir(sync_dir) / AUTH_KEY_FILENAME).exists()


# ---------------------------------------------------------------------------
# Generate reusable auth key via Tailscale API
# ---------------------------------------------------------------------------

def generate_auth_key() -> Optional[str]:
    """Attempt to generate a reusable auth key via Tailscale API.

    Uses `tailscale api` or the local API socket if available.
    Falls back to prompting the user to create one at the admin console.

    Returns:
        Auth key string, or None if generation failed.
    """
    ts = _tailscale_bin()
    if not ts:
        return None

    # Tailscale v1.56+ supports `tailscale api` for key generation.
    # Older versions require the admin console.
    try:
        result = subprocess.run(
            [ts, "api", "POST", "/api/v2/tailnet/-/keys", "--data",
             '{"capabilities":{"devices":{"create":{"reusable":true,"ephemeral":false,"preauthorized":true,"tags":[]}}}, "expirySeconds": 7776000}'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            key = data.get("key")
            if key:
                logger.info("Generated reusable auth key via API")
                return key
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass

    return None


def get_admin_console_url() -> str:
    """URL to the Tailscale admin console for manual key generation.

    Returns:
        URL string to the auth keys page.
    """
    return "https://login.tailscale.com/admin/settings/keys"


# ---------------------------------------------------------------------------
# Teardown — used by uninstaller
# ---------------------------------------------------------------------------

def logout() -> bool:
    """Log out of Tailscale, removing this device from the tailnet.

    The device will no longer be reachable by other nodes. The admin
    can also remove the device from the Tailscale admin console.

    Returns:
        True if logout succeeded.
    """
    ts = _tailscale_bin()
    if not ts:
        return False

    # Disable funnel first to clean up serve config
    disable_funnel()

    try:
        result = subprocess.run(
            [ts, "logout"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            logger.info("Tailscale logout succeeded")
            return True
        logger.warning("Tailscale logout failed: %s", result.stderr)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Tailscale logout error: %s", exc)
    return False


def remove_auth_key(sync_dir: Optional[Path] = None) -> bool:
    """Delete the synced auth key file from the Tier 1 sync folder.

    Other devices will still have their copy (and their own Tailscale
    sessions), but no new devices can auto-join using this key.

    Args:
        sync_dir: Override sync folder.

    Returns:
        True if the file was removed.
    """
    key_path = _sync_dir(sync_dir) / AUTH_KEY_FILENAME
    if key_path.exists():
        try:
            key_path.unlink()
            logger.info("Removed auth key: %s", key_path)
            return True
        except OSError as exc:
            logger.warning("Could not remove auth key: %s", exc)
    return False
