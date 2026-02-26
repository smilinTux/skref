"""
Vault resolver — pick the fastest path to reach a vault.

Resolution order:
    1. Local disk   (this device is the origin — zero latency)
    2. Tailnet IP   (same tailnet — private, fast, ~1ms)
    3. Funnel URL   (public HTTPS via Tailscale — works from anywhere)
    4. Cloud URL    (Nextcloud/S3 direct — depends on internet speed)

The resolver reads the vault's endpoints and returns the best one
that's reachable right now. If the vault is local, it returns a
LocalBackend directly. If it's remote, it returns a WebDAV or
cloud backend pointed at the right URL.
"""

from __future__ import annotations

import logging
import platform
import socket
from pathlib import Path
from typing import Optional

from .backends.base import Backend
from .backends.local import LocalBackend
from .models import (
    BackendType,
    DeviceRole,
    SkrefConfig,
    VaultConfig,
    VaultEndpoint,
)

logger = logging.getLogger("skref.resolver")

ENDPOINT_PRIORITY = {
    "local": 10,
    "tailnet": 20,
    "funnel": 30,
    "nextcloud": 40,
    "s3": 40,
    "gdrive": 50,
}


def _this_hostname() -> str:
    """Get this machine's hostname for matching against vault origins."""
    return socket.gethostname()


def resolve_backend(vault: VaultConfig, config: SkrefConfig) -> Backend:
    """Resolve the best backend for a vault.

    Tries endpoints in priority order: local > tailnet > funnel > cloud.
    Falls back to the vault's configured backend if no endpoints match.

    Args:
        vault: Vault configuration.
        config: Full skref config (for device info).

    Returns:
        A Backend instance pointed at the best available endpoint.
    """
    hostname = config.device.hostname or _this_hostname()

    if _is_local(vault, hostname):
        logger.debug("Vault '%s' is local on this device", vault.name)
        return LocalBackend(Path(vault.path))

    endpoints = sorted(vault.endpoints, key=lambda e: e.priority)
    for ep in endpoints:
        if not ep.available:
            continue

        if ep.kind == "local" and ep.device == hostname:
            logger.debug("Vault '%s' resolved to local path via endpoint", vault.name)
            return LocalBackend(Path(ep.url))

        if ep.kind == "tailnet":
            logger.info(
                "Vault '%s' resolved to tailnet: %s (on %s)",
                vault.name, ep.url, ep.device,
            )
            return _make_remote_backend(ep.url, vault)

        if ep.kind == "funnel":
            logger.info(
                "Vault '%s' resolved to funnel: %s (on %s)",
                vault.name, ep.url, ep.device,
            )
            return _make_remote_backend(ep.url, vault)

    logger.debug("Vault '%s' using configured backend: %s", vault.name, vault.backend.value)
    return _make_configured_backend(vault)


def _is_local(vault: VaultConfig, hostname: str) -> bool:
    """Check if this device is the vault's origin."""
    if vault.role == DeviceRole.ORIGIN:
        if not vault.origin_device or vault.origin_device == hostname:
            return True
    if vault.backend == BackendType.LOCAL and not vault.origin_device:
        return True
    return False


def _make_remote_backend(webdav_url: str, vault: VaultConfig) -> Backend:
    """Create a WebDAV backend for a remote vault.

    Falls back to local backend if the WebDAV backend isn't implemented yet.

    Args:
        webdav_url: URL of the remote WebDAV proxy.
        vault: Vault configuration.

    Returns:
        Backend instance.
    """
    try:
        from .backends.nextcloud import NextcloudBackend
        return NextcloudBackend(url=webdav_url)
    except ImportError:
        pass

    logger.warning(
        "Remote backend not yet available (Phase 2). "
        "Vault '%s' at %s requires WebDAV backend. "
        "Falling back to configured backend.",
        vault.name, webdav_url,
    )
    return _make_configured_backend(vault)


def _make_configured_backend(vault: VaultConfig) -> Backend:
    """Build the backend from the vault's static config."""
    if vault.backend == BackendType.LOCAL:
        return LocalBackend(Path(vault.path))
    raise RuntimeError(
        f"Backend '{vault.backend.value}' not yet implemented for vault '{vault.name}'"
    )


def build_endpoints_for_device(config: SkrefConfig) -> list[VaultEndpoint]:
    """Generate endpoints that this device can offer for its local vaults.

    Called during setup / publish to populate the registry with
    reachable endpoints for vaults hosted on this machine.

    Args:
        config: Full skref config.

    Returns:
        List of VaultEndpoint objects for vaults on this device.
    """
    hostname = config.device.hostname or _this_hostname()
    endpoints: list[VaultEndpoint] = []

    for name, vault in config.vaults.items():
        if vault.role != DeviceRole.ORIGIN:
            continue
        if not vault.published:
            continue

        endpoints.append(VaultEndpoint(
            kind="local",
            url=vault.path,
            device=hostname,
            priority=ENDPOINT_PRIORITY["local"],
        ))

        if config.device.tailscale_ip:
            port = config.serve.port
            endpoints.append(VaultEndpoint(
                kind="tailnet",
                url=f"http://{config.device.tailscale_ip}:{port}/{name}/",
                device=hostname,
                priority=ENDPOINT_PRIORITY["tailnet"],
            ))

        if config.device.funnel_enabled and config.device.tailscale_fqdn:
            endpoints.append(VaultEndpoint(
                kind="funnel",
                url=f"https://{config.device.tailscale_fqdn}/{name}/",
                device=hostname,
                priority=ENDPOINT_PRIORITY["funnel"],
            ))

    return endpoints


def describe_resolution(vault: VaultConfig, config: SkrefConfig) -> str:
    """Human-readable description of how a vault will be accessed.

    Args:
        vault: Vault configuration.
        config: Full skref config.

    Returns:
        Multi-line string describing resolution path.
    """
    hostname = config.device.hostname or _this_hostname()
    lines = [f"Vault: {vault.name}"]

    if _is_local(vault, hostname):
        lines.append(f"  Access: LOCAL (this device is the origin)")
        lines.append(f"  Path:   {vault.path}")
        return "\n".join(lines)

    lines.append(f"  Origin: {vault.origin_device}")
    endpoints = sorted(vault.endpoints, key=lambda e: e.priority)
    for i, ep in enumerate(endpoints):
        marker = ">>>" if i == 0 and ep.available else "   "
        status = "OK" if ep.available else "DOWN"
        lines.append(f"  {marker} [{ep.kind}] {ep.url} ({ep.device}) [{status}]")

    if not endpoints:
        lines.append("  No endpoints configured — vault unreachable from this device")

    return "\n".join(lines)
