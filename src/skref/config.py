"""
Load and save vault configuration from ~/.skcapstone/vaults.yaml.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import yaml

from .models import BackendType, SkrefConfig, VaultConfig

logger = logging.getLogger("skref.config")

DEFAULT_CONFIG_PATH = Path("~/.skcapstone/vaults.yaml")


def load_config(path: Optional[Path] = None) -> SkrefConfig:
    """Load vault config from YAML.

    Args:
        path: Config file path. Defaults to ~/.skcapstone/vaults.yaml.

    Returns:
        Parsed SkrefConfig.
    """
    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()

    if not config_path.exists():
        logger.info("No vault config at %s — using defaults", config_path)
        return _default_config()

    try:
        data = yaml.safe_load(config_path.read_text()) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Could not parse %s: %s — using defaults", config_path, exc)
        return _default_config()

    vaults = {}
    for name, vdata in data.get("vaults", {}).items():
        vaults[name] = VaultConfig(name=name, **vdata)

    return SkrefConfig(
        default_vault=data.get("default_vault", "personal"),
        identity_home=Path(data.get("identity_home", "~/.skcapstone/identity")),
        vaults=vaults,
    )


def save_config(cfg: SkrefConfig, path: Optional[Path] = None) -> Path:
    """Write vault config to YAML.

    Args:
        cfg: Config to persist.
        path: Where to write. Defaults to ~/.skcapstone/vaults.yaml.

    Returns:
        Path written.
    """
    config_path = (path or DEFAULT_CONFIG_PATH).expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "default_vault": cfg.default_vault,
        "identity_home": str(cfg.identity_home),
        "vaults": {},
    }
    for name, v in cfg.vaults.items():
        vdata = v.model_dump(mode="json", exclude={"name"})
        vdata["backend"] = v.backend.value
        data["vaults"][name] = vdata

    config_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    return config_path


def _default_config() -> SkrefConfig:
    """Sensible defaults — one encrypted local vault called 'personal'."""
    personal = VaultConfig(
        name="personal",
        backend=BackendType.LOCAL,
        encrypted=True,
        path="~/.skcapstone/vaults/personal",
    )
    return SkrefConfig(vaults={"personal": personal})
