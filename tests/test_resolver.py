"""Tests for vault resolution (local → tailnet → funnel fallback)."""

from __future__ import annotations

from pathlib import Path

import pytest

from skref.backends.local import LocalBackend
from skref.models import (
    BackendType,
    DeviceConfig,
    DeviceRole,
    SkrefConfig,
    VaultConfig,
    VaultEndpoint,
)
from skref.resolver import (
    build_endpoints_for_device,
    describe_resolution,
    resolve_backend,
)


def _make_config(
    hostname: str = "my-desktop",
    tailscale_ip: str = "",
    fqdn: str = "",
    funnel: bool = False,
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
    )


class TestResolveBackend:
    """Tests for resolve_backend()."""

    def test_local_vault_returns_local_backend(self, tmp_path: Path) -> None:
        """Origin vault on this device resolves to LocalBackend."""
        cfg = _make_config(hostname="my-desktop")
        vault = VaultConfig(
            name="personal",
            backend=BackendType.LOCAL,
            path=str(tmp_path / "vault"),
            origin_device="my-desktop",
            role=DeviceRole.ORIGIN,
        )
        backend = resolve_backend(vault, cfg)
        assert isinstance(backend, LocalBackend)

    def test_local_vault_no_origin_device(self, tmp_path: Path) -> None:
        """Local vault with no origin_device assumed to be this device."""
        cfg = _make_config(hostname="my-desktop")
        vault = VaultConfig(
            name="personal",
            backend=BackendType.LOCAL,
            path=str(tmp_path / "vault"),
        )
        backend = resolve_backend(vault, cfg)
        assert isinstance(backend, LocalBackend)

    def test_remote_vault_falls_back_to_configured(self, tmp_path: Path) -> None:
        """Remote vault with no reachable endpoints falls back."""
        cfg = _make_config(hostname="my-laptop")
        vault = VaultConfig(
            name="personal",
            backend=BackendType.LOCAL,
            path=str(tmp_path / "vault"),
            origin_device="my-desktop",
            role=DeviceRole.CLIENT,
            endpoints=[],
        )
        backend = resolve_backend(vault, cfg)
        assert isinstance(backend, LocalBackend)


class TestBuildEndpoints:
    """Tests for build_endpoints_for_device()."""

    def test_no_published_vaults_returns_empty(self) -> None:
        """No published vaults → no endpoints."""
        cfg = _make_config()
        cfg.vaults["test"] = VaultConfig(
            name="test", published=False, role=DeviceRole.ORIGIN,
        )
        eps = build_endpoints_for_device(cfg)
        assert eps == []

    def test_published_vault_gets_local_endpoint(self) -> None:
        """Published vault gets a local endpoint."""
        cfg = _make_config()
        cfg.vaults["personal"] = VaultConfig(
            name="personal",
            path="~/.skcapstone/vaults/personal",
            published=True,
            role=DeviceRole.ORIGIN,
        )
        eps = build_endpoints_for_device(cfg)
        local_eps = [e for e in eps if e.kind == "local"]
        assert len(local_eps) == 1
        assert local_eps[0].device == "my-desktop"

    def test_published_with_tailscale_gets_tailnet_endpoint(self) -> None:
        """Published vault with Tailscale gets tailnet endpoint."""
        cfg = _make_config(tailscale_ip="100.64.0.42")
        cfg.vaults["personal"] = VaultConfig(
            name="personal",
            path="/tmp/vault",
            published=True,
            role=DeviceRole.ORIGIN,
        )
        eps = build_endpoints_for_device(cfg)
        tailnet_eps = [e for e in eps if e.kind == "tailnet"]
        assert len(tailnet_eps) == 1
        assert "100.64.0.42" in tailnet_eps[0].url

    def test_published_with_funnel_gets_funnel_endpoint(self) -> None:
        """Published vault with Funnel gets funnel endpoint."""
        cfg = _make_config(
            tailscale_ip="100.64.0.42",
            fqdn="desktop.tail1234.ts.net",
            funnel=True,
        )
        cfg.vaults["personal"] = VaultConfig(
            name="personal",
            path="/tmp/vault",
            published=True,
            role=DeviceRole.ORIGIN,
        )
        eps = build_endpoints_for_device(cfg)
        funnel_eps = [e for e in eps if e.kind == "funnel"]
        assert len(funnel_eps) == 1
        assert "desktop.tail1234.ts.net" in funnel_eps[0].url


class TestDescribeResolution:
    """Tests for describe_resolution()."""

    def test_local_vault_description(self, tmp_path: Path) -> None:
        """Local vault shows LOCAL access."""
        cfg = _make_config(hostname="my-desktop")
        vault = VaultConfig(
            name="personal",
            path=str(tmp_path),
            origin_device="my-desktop",
            role=DeviceRole.ORIGIN,
        )
        desc = describe_resolution(vault, cfg)
        assert "LOCAL" in desc
        assert "personal" in desc

    def test_remote_vault_shows_endpoints(self) -> None:
        """Remote vault lists endpoints with priority."""
        cfg = _make_config(hostname="my-laptop")
        vault = VaultConfig(
            name="personal",
            origin_device="my-desktop",
            role=DeviceRole.CLIENT,
            endpoints=[
                VaultEndpoint(kind="tailnet", url="http://100.64.0.42:8443/", device="my-desktop", priority=20),
                VaultEndpoint(kind="funnel", url="https://desktop.ts.net/", device="my-desktop", priority=30),
            ],
        )
        desc = describe_resolution(vault, cfg)
        assert "my-desktop" in desc
        assert "tailnet" in desc
        assert "funnel" in desc
