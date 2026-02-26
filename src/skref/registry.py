"""
Vault registry — sync vault metadata across devices via Tier 1.

The registry is a small JSON file (~2-10 KB) that lives in the Tier 1
auth sync folder (~/.skcapstone/sync/). Syncthing (Sovereign Singularity)
replicates it to all devices. Each device reads the registry to discover
vaults on other machines, then uses the resolver to pick the best path.

Registry flow:
    1. Device A creates a vault and runs `skref publish`
    2. Registry entry is written to ~/.skcapstone/sync/vault-registry.json
    3. Syncthing replicates to Device B
    4. Device B sees the vault in `skref ls --all-devices`
    5. Device B resolves the endpoint: local → tailnet → funnel → cloud

Dedup: vaults are keyed by (name, origin_device). If two devices have
a vault with the same name, they're treated as separate vaults. Use
`skref link` to create a client-role reference to a remote vault.
"""

from __future__ import annotations

import json
import logging
import socket
import time
from pathlib import Path
from typing import Optional

from .models import (
    DeviceRole,
    SkrefConfig,
    VaultConfig,
    VaultEndpoint,
)

logger = logging.getLogger("skref.registry")

REGISTRY_FILENAME = "vault-registry.json"


def _registry_path(sync_dir: Optional[Path] = None) -> Path:
    """Path to the vault registry in the Tier 1 sync folder."""
    base = sync_dir or Path("~/.skcapstone/sync").expanduser()
    return base / REGISTRY_FILENAME


def _this_hostname() -> str:
    return socket.gethostname()


def load_registry(sync_dir: Optional[Path] = None) -> dict:
    """Load the vault registry from the sync folder.

    Args:
        sync_dir: Override sync folder path.

    Returns:
        Registry dict with 'devices' and 'vaults' keys.
    """
    path = _registry_path(sync_dir)
    if not path.exists():
        return {"devices": {}, "vaults": {}}

    try:
        data = json.loads(path.read_text())
        if "devices" not in data:
            data["devices"] = {}
        if "vaults" not in data:
            data["vaults"] = {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read registry %s: %s", path, exc)
        return {"devices": {}, "vaults": {}}


def save_registry(registry: dict, sync_dir: Optional[Path] = None) -> Path:
    """Save the vault registry to the sync folder.

    Args:
        registry: Registry dict.
        sync_dir: Override sync folder path.

    Returns:
        Path written.
    """
    path = _registry_path(sync_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=False))
    return path


def publish_device(config: SkrefConfig, sync_dir: Optional[Path] = None) -> None:
    """Register this device and its published vaults in the registry.

    Writes device info (hostname, Tailscale FQDN, IP, funnel status)
    and vault endpoints so other devices can discover and reach them.

    Args:
        config: Full skref config for this device.
        sync_dir: Override sync folder.
    """
    registry = load_registry(sync_dir)
    hostname = config.device.hostname or _this_hostname()

    registry["devices"][hostname] = {
        "hostname": hostname,
        "device_id": config.device.device_id,
        "is_datastore": config.device.is_datastore,
        "tailscale_fqdn": config.device.tailscale_fqdn,
        "tailscale_ip": config.device.tailscale_ip,
        "funnel_enabled": config.device.funnel_enabled,
        "funnel_port": config.device.funnel_port,
        "updated_at": int(time.time()),
    }

    for name, vault in config.vaults.items():
        if vault.role != DeviceRole.ORIGIN or not vault.published:
            continue

        vault_key = f"{hostname}:{name}"
        endpoints = []

        endpoints.append({
            "kind": "local",
            "url": vault.path,
            "device": hostname,
            "priority": 10,
        })

        if config.device.tailscale_ip:
            endpoints.append({
                "kind": "tailnet",
                "url": f"http://{config.device.tailscale_ip}:{config.serve.port}/{name}/",
                "device": hostname,
                "priority": 20,
            })

        if config.device.funnel_enabled and config.device.tailscale_fqdn:
            endpoints.append({
                "kind": "funnel",
                "url": f"https://{config.device.tailscale_fqdn}/{name}/",
                "device": hostname,
                "priority": 30,
            })

        registry["vaults"][vault_key] = {
            "name": name,
            "origin_device": hostname,
            "backend": vault.backend.value,
            "encrypted": vault.encrypted,
            "endpoints": endpoints,
            "updated_at": int(time.time()),
        }

    save_registry(registry, sync_dir)
    logger.info("Published device '%s' with %d vault(s) to registry",
                hostname, sum(1 for v in config.vaults.values() if v.published))


def discover_remote_vaults(
    config: SkrefConfig,
    sync_dir: Optional[Path] = None,
) -> list[VaultConfig]:
    """Discover vaults from other devices via the registry.

    Reads the registry (synced via Tier 1) and returns VaultConfig
    objects for vaults NOT on this device.

    Args:
        config: This device's config.
        sync_dir: Override sync folder.

    Returns:
        List of VaultConfig for remote vaults, with endpoints populated.
    """
    registry = load_registry(sync_dir)
    hostname = config.device.hostname or _this_hostname()
    remote_vaults: list[VaultConfig] = []

    for vault_key, vdata in registry.get("vaults", {}).items():
        if vdata.get("origin_device") == hostname:
            continue

        endpoints = [
            VaultEndpoint(
                kind=ep["kind"],
                url=ep["url"],
                device=ep.get("device", ""),
                priority=ep.get("priority", 50),
            )
            for ep in vdata.get("endpoints", [])
            if ep.get("kind") != "local"
        ]

        vcfg = VaultConfig(
            name=vdata["name"],
            backend=BackendType(vdata.get("backend", "local")),
            encrypted=vdata.get("encrypted", True),
            origin_device=vdata["origin_device"],
            role=DeviceRole.CLIENT,
            endpoints=endpoints,
            published=False,
        )
        remote_vaults.append(vcfg)

    return remote_vaults


def list_all_vaults(
    config: SkrefConfig,
    sync_dir: Optional[Path] = None,
) -> list[dict]:
    """List all vaults across all devices (local + remote).

    Args:
        config: This device's config.
        sync_dir: Override sync folder.

    Returns:
        List of dicts with vault info and access method for display.
    """
    hostname = config.device.hostname or _this_hostname()
    results = []

    for name, vault in config.vaults.items():
        if vault.role == DeviceRole.ORIGIN:
            results.append({
                "name": name,
                "device": hostname,
                "access": "local",
                "published": vault.published,
                "encrypted": vault.encrypted,
                "backend": vault.backend.value,
            })

    for remote in discover_remote_vaults(config, sync_dir):
        best_ep = remote.endpoints[0] if remote.endpoints else None
        results.append({
            "name": remote.name,
            "device": remote.origin_device,
            "access": best_ep.kind if best_ep else "unreachable",
            "published": True,
            "encrypted": remote.encrypted,
            "backend": remote.origin_device,
        })

    return results


def deregister_device(
    hostname: Optional[str] = None,
    sync_dir: Optional[Path] = None,
) -> dict:
    """Remove a device and all its vaults from the registry.

    Called during uninstall. After saving, Syncthing propagates the
    updated registry to other devices so they stop trying to reach
    this node.

    Args:
        hostname: Device hostname to remove. Defaults to this machine.
        sync_dir: Override sync folder.

    Returns:
        Dict with 'device_removed' (bool) and 'vaults_removed' (int).
    """
    host = hostname or _this_hostname()
    registry = load_registry(sync_dir)
    result = {"device_removed": False, "vaults_removed": 0}

    if host in registry.get("devices", {}):
        del registry["devices"][host]
        result["device_removed"] = True

    vault_keys_to_remove = [
        key for key, vdata in registry.get("vaults", {}).items()
        if vdata.get("origin_device") == host
    ]
    for key in vault_keys_to_remove:
        del registry["vaults"][key]
        result["vaults_removed"] += 1

    save_registry(registry, sync_dir)
    logger.info(
        "Deregistered device '%s': removed %d vault(s)",
        host, result["vaults_removed"],
    )
    return result


# Avoid circular import — only needed for type in discover_remote_vaults
from .models import BackendType  # noqa: E402
