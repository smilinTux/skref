"""
Data models for vaults, backends, and config.

The vault registry model: every device in the sovereign network knows
about every vault. Each vault has one "origin" (the device that hosts
the backing store) and one or more "endpoints" (ways to reach it).
When you access a vault, the resolver picks the fastest path:

    1. Local disk  (vault origin is this machine)
    2. Tailnet IP  (same tailnet, private, fast)
    3. Funnel URL  (public HTTPS, works from anywhere)
    4. Cloud URL   (Nextcloud/S3 direct, if configured)
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class BackendType(str, Enum):
    """Supported storage backends."""

    LOCAL = "local"
    NEXTCLOUD = "nextcloud"
    S3 = "s3"
    GDRIVE = "gdrive"


class DeviceRole(str, Enum):
    """What role this device plays for a vault."""

    ORIGIN = "origin"
    REPLICA = "replica"
    CLIENT = "client"


class VaultEndpoint(BaseModel):
    """One way to reach a vault from a device.

    A vault may have multiple endpoints — local path on the origin
    machine, a Tailscale tailnet URL, a Funnel public URL, or a
    cloud backend URL. The resolver tries them in priority order.

    Attributes:
        kind: Endpoint type for prioritization.
        url: How to reach it (file path, tailnet URL, funnel URL, etc).
        device: Hostname of the device that serves this endpoint.
        priority: Lower is better. Resolver tries in order.
        available: Whether this endpoint is currently reachable.
    """

    kind: str
    url: str
    device: str = ""
    priority: int = 50
    available: bool = True


class VaultConfig(BaseModel):
    """Configuration for a single vault."""

    name: str
    backend: BackendType = BackendType.LOCAL
    encrypted: bool = True
    path: str = ""
    url: Optional[str] = None
    bucket: Optional[str] = None
    region: Optional[str] = None
    key: str = "auto"
    peers: list[str] = Field(default_factory=list)

    origin_device: str = ""
    role: DeviceRole = DeviceRole.ORIGIN
    endpoints: list[VaultEndpoint] = Field(default_factory=list)
    published: bool = False

    def storage_root(self) -> Path:
        """Resolve the local storage path for this vault.

        Returns:
            Absolute path to the vault's backing directory.
        """
        if self.backend == BackendType.LOCAL:
            return Path(self.path).expanduser()
        return Path(f"~/.skcapstone/vaults/{self.name}").expanduser()


class DeviceConfig(BaseModel):
    """This device's identity in the vault network.

    Attributes:
        hostname: Machine hostname (matches Tailscale HostName).
        device_id: Unique ID for this device (generated at install).
        is_datastore: Whether this device hosts vault backing stores.
        tailscale_fqdn: Tailscale FQDN if available.
        tailscale_ip: Tailnet IP if available.
        funnel_enabled: Whether Tailscale Funnel is enabled.
        funnel_port: Port exposed via Funnel.
    """

    hostname: str = ""
    device_id: str = ""
    is_datastore: bool = False
    tailscale_fqdn: str = ""
    tailscale_ip: str = ""
    funnel_enabled: bool = False
    funnel_port: int = 8443


class ServeConfig(BaseModel):
    """Configuration for the WebDAV proxy / serve layer."""

    host: str = "127.0.0.1"
    port: int = 8443
    tailscale_funnel: bool = False
    tailscale_serve: bool = True
    auth_user: Optional[str] = None
    auth_pass: Optional[str] = None


class SkrefConfig(BaseModel):
    """Top-level config — multiple vaults, default settings."""

    default_vault: str = "personal"
    identity_home: Path = Path("~/.skcapstone/identity")
    device: DeviceConfig = Field(default_factory=DeviceConfig)
    vaults: dict[str, VaultConfig] = Field(default_factory=dict)
    serve: ServeConfig = Field(default_factory=ServeConfig)
