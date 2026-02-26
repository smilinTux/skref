"""Tests for the Vault layer (backend + crypto integration)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from skref.backends.local import LocalBackend
from skref.models import VaultConfig
from skref.vault import Vault


@pytest.fixture
def unencrypted_vault(tmp_path: Path) -> Vault:
    """An unencrypted vault for testing without GPG."""
    cfg = VaultConfig(
        name="test-plain",
        encrypted=False,
        path=str(tmp_path / "vault"),
    )
    backend = LocalBackend(Path(cfg.path))
    return Vault(config=cfg, backend=backend)


class TestUnencryptedVault:
    """Vault operations without encryption (no GPG needed)."""

    def test_write_and_read(self, unencrypted_vault: Vault) -> None:
        """Write and read a file."""
        unencrypted_vault.write("test.txt", b"hello world")
        assert unencrypted_vault.read("test.txt") == b"hello world"

    def test_exists(self, unencrypted_vault: Vault) -> None:
        """Exists works for written files."""
        unencrypted_vault.write("yes.txt", b"y")
        assert unencrypted_vault.exists("yes.txt")
        assert not unencrypted_vault.exists("no.txt")

    def test_delete(self, unencrypted_vault: Vault) -> None:
        """Delete removes a file."""
        unencrypted_vault.write("gone.txt", b"bye")
        unencrypted_vault.delete("gone.txt")
        assert not unencrypted_vault.exists("gone.txt")

    def test_list_dir(self, unencrypted_vault: Vault) -> None:
        """List directory shows files."""
        unencrypted_vault.write("a.txt", b"a")
        unencrypted_vault.write("b.txt", b"b")
        entries = unencrypted_vault.list_dir()
        names = {e.name for e in entries}
        assert "a.txt" in names
        assert "b.txt" in names

    def test_mkdir(self, unencrypted_vault: Vault) -> None:
        """mkdir creates a directory visible in list_dir."""
        unencrypted_vault.mkdir("subdir")
        entries = unencrypted_vault.list_dir()
        dirs = [e for e in entries if e.is_dir]
        assert any(e.name == "subdir" for e in dirs)

    def test_nested_write(self, unencrypted_vault: Vault) -> None:
        """Writing to nested paths works."""
        unencrypted_vault.write("a/b/deep.txt", b"nested")
        assert unencrypted_vault.read("a/b/deep.txt") == b"nested"

    def test_file_size(self, unencrypted_vault: Vault) -> None:
        """file_size returns byte count."""
        data = b"twelve bytes"
        unencrypted_vault.write("sized.txt", data)
        assert unencrypted_vault.file_size("sized.txt") == len(data)


class TestEncryptedVaultRequiresKey:
    """Encrypted vault rejects init without a GPG key."""

    def test_no_key_raises(self, tmp_path: Path) -> None:
        """Creating an encrypted vault without a key raises RuntimeError."""
        cfg = VaultConfig(name="enc", encrypted=True, path=str(tmp_path / "enc"))
        backend = LocalBackend(Path(cfg.path))

        with patch("skref.vault.detect_key", return_value=None):
            with pytest.raises(RuntimeError, match="GPG key"):
                Vault(config=cfg, backend=backend)
