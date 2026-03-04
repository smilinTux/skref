"""
Vault — the high-level interface that combines backend + crypto.

A Vault wraps a Backend and optionally encrypts/decrypts through it.
The FUSE layer and CLI both talk to Vault, not to Backend directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .backends.base import Backend, FileEntry
from .crypto import (
    decrypt_bytes,
    detect_key,
    encrypt_bytes,
    encrypted_name,
    is_encrypted_name,
    plaintext_name,
)
from .models import VaultConfig

logger = logging.getLogger("skref.vault")


class Vault:
    """Encrypted (or plaintext) vault over any backend.

    Args:
        config: Vault configuration.
        backend: Storage backend instance.
        gpg_fingerprint: GPG key fingerprint. Auto-detected if None.
    """

    def __init__(
        self,
        config: VaultConfig,
        backend: Backend,
        gpg_fingerprint: Optional[str] = None,
    ) -> None:
        self.config = config
        self.backend = backend
        self.encrypted = config.encrypted

        if self.encrypted:
            self._fingerprint = gpg_fingerprint or detect_key()
            if not self._fingerprint:
                raise RuntimeError(
                    "Encrypted vault requires a GPG key. "
                    "Run 'capauth init' or set key in vaults.yaml."
                )
            self._peers = config.peers
        else:
            self._fingerprint = None
            self._peers = []

    def read(self, rel_path: str) -> bytes:
        """Read a file, decrypting if the vault is encrypted.

        Args:
            rel_path: Path relative to vault root (plaintext name).

        Returns:
            Plaintext bytes.
        """
        if self.encrypted:
            backend_path = encrypted_name(rel_path)
            ciphertext = self.backend.get(backend_path)
            return decrypt_bytes(ciphertext)
        return self.backend.get(rel_path)

    def write(self, rel_path: str, data: bytes) -> None:
        """Write a file, encrypting if the vault is encrypted.

        Args:
            rel_path: Path relative to vault root (plaintext name).
            data: Plaintext bytes.
        """
        if self.encrypted:
            backend_path = encrypted_name(rel_path)
            ciphertext = encrypt_bytes(data, self._fingerprint, self._peers or None)
            self.backend.put(backend_path, ciphertext)
        else:
            self.backend.put(rel_path, data)

    def delete(self, rel_path: str) -> None:
        """Delete a file.

        Args:
            rel_path: Plaintext relative path.
        """
        if self.encrypted:
            self.backend.delete(encrypted_name(rel_path))
        else:
            self.backend.delete(rel_path)

    def list_dir(self, rel_path: str = "") -> list[FileEntry]:
        """List directory contents, stripping .gpg suffixes for the caller.

        Args:
            rel_path: Relative dir path.

        Returns:
            List of FileEntry with plaintext names.
        """
        raw = self.backend.list_dir(rel_path)
        if not self.encrypted:
            return raw

        entries = []
        for e in raw:
            if e.is_dir:
                entries.append(e)
            elif is_encrypted_name(e.name):
                entries.append(FileEntry(
                    name=plaintext_name(e.name),
                    path=plaintext_name(e.path),
                    is_dir=False,
                    size=e.size,
                ))
            else:
                entries.append(e)
        return entries

    def exists(self, rel_path: str) -> bool:
        """Check if a file exists.

        Args:
            rel_path: Plaintext relative path.
        """
        if self.encrypted:
            return self.backend.exists(encrypted_name(rel_path))
        return self.backend.exists(rel_path)

    def mkdir(self, rel_path: str) -> None:
        """Create a directory.

        Args:
            rel_path: Relative dir path.
        """
        self.backend.mkdir(rel_path)

    def file_size(self, rel_path: str) -> int:
        """Get the on-disk size (encrypted size for encrypted vaults).

        Args:
            rel_path: Plaintext relative path.
        """
        if self.encrypted:
            return self.backend.file_size(encrypted_name(rel_path))
        return self.backend.file_size(rel_path)

    def is_dir(self, rel_path: str) -> bool:
        """Check if a path is a directory.

        Args:
            rel_path: Relative path (directories are never encrypted).
        """
        return self.backend.is_dir(rel_path)
