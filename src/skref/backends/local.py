"""
Local filesystem backend — files stored on disk.

Used for local encrypted vaults, USB drives, NAS mounts, etc.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .base import Backend, FileEntry

logger = logging.getLogger("skref.backends.local")


class LocalBackend(Backend):
    """Stores vault files on the local filesystem.

    Args:
        root: Absolute path to the vault's backing directory.
    """

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        """The resolved root directory."""
        return self._root

    def _resolve(self, rel_path: str) -> Path:
        """Resolve a relative path and guard against path traversal."""
        target = (self._root / rel_path).resolve()
        if not str(target).startswith(str(self._root)):
            raise PermissionError(f"Path traversal blocked: {rel_path}")
        return target

    def put(self, rel_path: str, data: bytes) -> None:
        """Write bytes to a file relative to vault root.

        Args:
            rel_path: Relative path.
            data: Raw bytes.
        """
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get(self, rel_path: str) -> bytes:
        """Read bytes from a file.

        Args:
            rel_path: Relative path.

        Returns:
            Raw bytes.

        Raises:
            FileNotFoundError: If path doesn't exist.
        """
        target = self._resolve(rel_path)
        if not target.exists():
            raise FileNotFoundError(f"Not found: {rel_path}")
        return target.read_bytes()

    def delete(self, rel_path: str) -> None:
        """Delete a file.

        Args:
            rel_path: Relative path.
        """
        target = self._resolve(rel_path)
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            import shutil
            shutil.rmtree(target)

    def list_dir(self, rel_path: str = "") -> list[FileEntry]:
        """List directory contents.

        Args:
            rel_path: Relative dir path.

        Returns:
            List of FileEntry objects.
        """
        target = self._resolve(rel_path) if rel_path else self._root
        if not target.is_dir():
            return []

        entries = []
        for child in sorted(target.iterdir()):
            if child.name.startswith("."):
                continue
            entries.append(FileEntry(
                name=child.name,
                path=str(child.relative_to(self._root)),
                is_dir=child.is_dir(),
                size=child.stat().st_size if child.is_file() else 0,
            ))
        return entries

    def exists(self, rel_path: str) -> bool:
        """Check if a path exists.

        Args:
            rel_path: Relative path.
        """
        return self._resolve(rel_path).exists()

    def mkdir(self, rel_path: str) -> None:
        """Create a directory.

        Args:
            rel_path: Relative dir path.
        """
        self._resolve(rel_path).mkdir(parents=True, exist_ok=True)

    def file_size(self, rel_path: str) -> int:
        """Size of a file in bytes.

        Args:
            rel_path: Relative path.
        """
        target = self._resolve(rel_path)
        return target.stat().st_size if target.is_file() else 0
