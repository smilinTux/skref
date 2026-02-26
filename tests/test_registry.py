"""Tests for vault registry (publish, discover, sync)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skref.models import (
    BackendType,
    DeviceConfig,
    DeviceRole,
    SkrefConfig,
    VaultConfig,
)
from skref.registry import (
    discover_remote_vaults,
    list_all_vaults,
    load_registry,
    publish_device,
    save_registry,
)


def _make_config(
    hostname: str = "desktop",
    tailscale_ip: str = "100.64.0.42",
    fqdn: str = "desktop.tail1234.ts.net",
    funnel: bool = True,
) -> SkrefConfig:
    return SkrefConfig(
        device=DeviceConfig(
            hostname=hostname,
            device_id="test-001",
            is_datastore=True,
            tailscale_ip=tailscale_ip,
            tailscale_fqdn=fqdn,
            funnel_enabled=funnel,
        ),
        vaults={
            "personal": VaultConfig(
                name="personal",
                backend=BackendType.LOCAL,
                path="~/.skcapstone/vaults/personal",
                origin_device=hostname,
                role=DeviceRole.ORIGIN,
                published=True,
                encrypted=True,
            ),
        },
    )


class TestRegistryLoadSave:
    """Tests for registry persistence."""

    def test_load_missing_returns_empty(self, tmp_path: Path) -> None:
        """Loading from nonexistent file returns empty registry."""
        reg = load_registry(tmp_path / "nonexistent")
        assert reg["devices"] == {}
        assert reg["vaults"] == {}

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Registry survives JSON round-trip."""
        reg = {"devices": {"host1": {"hostname": "host1"}}, "vaults": {}}
        save_registry(reg, tmp_path)
        loaded = load_registry(tmp_path)
        assert loaded["devices"]["host1"]["hostname"] == "host1"

    def test_corrupt_json_returns_empty(self, tmp_path: Path) -> None:
        """Corrupt registry gracefully returns empty."""
        bad_file = tmp_path / "vault-registry.json"
        bad_file.write_text("{{{invalid json")
        reg = load_registry(tmp_path)
        assert reg["devices"] == {}


class TestPublishDevice:
    """Tests for publish_device()."""

    def test_publishes_device_info(self, tmp_path: Path) -> None:
        """Device info written to registry."""
        cfg = _make_config()
        publish_device(cfg, tmp_path)

        reg = load_registry(tmp_path)
        assert "desktop" in reg["devices"]
        assert reg["devices"]["desktop"]["tailscale_fqdn"] == "desktop.tail1234.ts.net"

    def test_publishes_vault_endpoints(self, tmp_path: Path) -> None:
        """Published vault gets endpoints in registry."""
        cfg = _make_config()
        publish_device(cfg, tmp_path)

        reg = load_registry(tmp_path)
        assert "desktop:personal" in reg["vaults"]
        vault = reg["vaults"]["desktop:personal"]
        kinds = {ep["kind"] for ep in vault["endpoints"]}
        assert "local" in kinds
        assert "tailnet" in kinds
        assert "funnel" in kinds

    def test_unpublished_vault_not_in_registry(self, tmp_path: Path) -> None:
        """Unpublished vault excluded from registry."""
        cfg = _make_config()
        cfg.vaults["private"] = VaultConfig(
            name="private",
            role=DeviceRole.ORIGIN,
            published=False,
        )
        publish_device(cfg, tmp_path)

        reg = load_registry(tmp_path)
        assert "desktop:private" not in reg["vaults"]


class TestDiscoverRemoteVaults:
    """Tests for discover_remote_vaults()."""

    def test_discovers_vaults_from_other_devices(self, tmp_path: Path) -> None:
        """Vaults from another device are discovered."""
        remote_cfg = _make_config(hostname="remote-server")
        publish_device(remote_cfg, tmp_path)

        local_cfg = _make_config(hostname="my-laptop")
        remote_vaults = discover_remote_vaults(local_cfg, tmp_path)

        assert len(remote_vaults) == 1
        assert remote_vaults[0].name == "personal"
        assert remote_vaults[0].origin_device == "remote-server"
        assert remote_vaults[0].role == DeviceRole.CLIENT

    def test_excludes_local_endpoints_from_remote(self, tmp_path: Path) -> None:
        """Remote vaults don't include 'local' endpoints (can't access remote disk)."""
        remote_cfg = _make_config(hostname="remote-server")
        publish_device(remote_cfg, tmp_path)

        local_cfg = _make_config(hostname="my-laptop")
        remote_vaults = discover_remote_vaults(local_cfg, tmp_path)

        kinds = {ep.kind for ep in remote_vaults[0].endpoints}
        assert "local" not in kinds
        assert "tailnet" in kinds
        assert "funnel" in kinds

    def test_own_vaults_not_discovered(self, tmp_path: Path) -> None:
        """Own vaults are excluded from discovery."""
        cfg = _make_config(hostname="desktop")
        publish_device(cfg, tmp_path)

        remote_vaults = discover_remote_vaults(cfg, tmp_path)
        assert len(remote_vaults) == 0


class TestListAllVaults:
    """Tests for list_all_vaults()."""

    def test_includes_local_and_remote(self, tmp_path: Path) -> None:
        """Lists both local and remote vaults."""
        remote_cfg = _make_config(hostname="remote-server")
        publish_device(remote_cfg, tmp_path)

        local_cfg = _make_config(hostname="my-laptop")
        all_vaults = list_all_vaults(local_cfg, tmp_path)

        names = {v["name"] for v in all_vaults}
        assert "personal" in names
        assert len(all_vaults) >= 2

    def test_local_vault_shows_local_access(self, tmp_path: Path) -> None:
        """Local vault shows access='local'."""
        cfg = _make_config(hostname="desktop")
        all_vaults = list_all_vaults(cfg, tmp_path)

        local_ones = [v for v in all_vaults if v["device"] == "desktop"]
        assert len(local_ones) == 1
        assert local_ones[0]["access"] == "local"
