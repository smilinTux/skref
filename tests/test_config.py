"""Tests for vault config load/save."""

from __future__ import annotations

from pathlib import Path

import pytest

from skref.config import load_config, save_config
from skref.models import BackendType, SkrefConfig, VaultConfig


class TestConfig:
    """Config serialization round-trip."""

    def test_default_config(self) -> None:
        """Loading from non-existent file returns defaults."""
        cfg = load_config(Path("/tmp/nonexistent-skref-config-test.yaml"))
        assert "personal" in cfg.vaults
        assert cfg.vaults["personal"].encrypted is True

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Config survives YAML round-trip."""
        config_path = tmp_path / "vaults.yaml"
        cfg = SkrefConfig(
            default_vault="mytest",
            vaults={
                "mytest": VaultConfig(
                    name="mytest",
                    backend=BackendType.LOCAL,
                    encrypted=True,
                    path="/tmp/test-vault",
                    peers=["AAAA", "BBBB"],
                ),
                "shared": VaultConfig(
                    name="shared",
                    backend=BackendType.LOCAL,
                    encrypted=False,
                    path="/tmp/shared",
                ),
            },
        )

        save_config(cfg, config_path)
        loaded = load_config(config_path)

        assert loaded.default_vault == "mytest"
        assert "mytest" in loaded.vaults
        assert "shared" in loaded.vaults
        assert loaded.vaults["mytest"].encrypted is True
        assert loaded.vaults["mytest"].peers == ["AAAA", "BBBB"]
        assert loaded.vaults["shared"].encrypted is False

    def test_corrupt_yaml_returns_defaults(self, tmp_path: Path) -> None:
        """Corrupt YAML gracefully falls back to defaults."""
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{{{invalid yaml")
        cfg = load_config(bad)
        assert "personal" in cfg.vaults
