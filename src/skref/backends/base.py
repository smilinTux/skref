"""
Abstract backend interface.

Every storage backend (local, Nextcloud, S3, etc.) implements this.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class FileEntry:
    """Metadata for a file or directory in the vault.

    Attributes:
        name: Filename (may include .gpg suffix for encrypted vaults).
        path: Relative path within the vault.
        is_dir: Whether this entry is a directory.
        size: Size in bytes (0 for dirs or unknown).
    """

    name: str
    path: str
    is_dir: bool = False
    size: int = 0


class Backend(ABC):
    """Abstract storage backend."""

    @abstractmethod
    def put(self, rel_path: str, data: bytes) -> None:
        """Write bytes to a path relative to the vault root.

        Args:
            rel_path: Relative path (e.g. "legal/contract.pdf.gpg").
            data: Raw bytes to store.
        """

    @abstractmethod
    def get(self, rel_path: str) -> bytes:
        """Read bytes from a relative path.

        Args:
            rel_path: Relative path.

        Returns:
            Raw bytes.

        Raises:
            FileNotFoundError: If the path does not exist.
        """

    @abstractmethod
    def delete(self, rel_path: str) -> None:
        """Delete a file at a relative path.

        Args:
            rel_path: Relative path.
        """

    @abstractmethod
    def list_dir(self, rel_path: str = "") -> list[FileEntry]:
        """List entries in a directory.

        Args:
            rel_path: Relative dir path (empty string = vault root).

        Returns:
            List of FileEntry objects.
        """

    @abstractmethod
    def exists(self, rel_path: str) -> bool:
        """Check if a path exists.

        Args:
            rel_path: Relative path.

        Returns:
            True if the file or directory exists.
        """

    @abstractmethod
    def mkdir(self, rel_path: str) -> None:
        """Create a directory (and parents).

        Args:
            rel_path: Relative dir path.
        """

    def file_size(self, rel_path: str) -> int:
        """Get size of a file in bytes.

        Args:
            rel_path: Relative path.

        Returns:
            Size in bytes, or 0 if unknown/missing.
        """
        return 0
