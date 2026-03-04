"""
Windows FUSE filesystem — mount an encrypted vault as a drive letter.

Uses WinFsp via the winfspy Python binding to provide FUSE-compatible
semantics on Windows. Files are decrypted on open and encrypted on close,
identical to the Linux/macOS pyfuse3 implementation.

Requires: winfspy (pip install skref[windows])
          WinFsp: https://winfsp.dev/rel/

Usage:
    skref mount V: --vault personal
    # V:\\ appears in Explorer with decrypted files
"""

from __future__ import annotations

import logging
import platform
import time
from typing import Optional

logger = logging.getLogger("skref.fuse_windows")

try:
    import winfspy
    from winfspy import (
        FILE_ATTRIBUTE,
        CREATE_FILE_CREATE_OPTIONS,
        FileSystemHost,
        BaseFileSystemOperations,
        NTStatusError,
    )
    WINFSP_AVAILABLE = True
except ImportError:
    WINFSP_AVAILABLE = False

    # ---------------------------------------------------------------------------
    # Stubs so that _SkrefWindowsFS can be defined and unit-tested on
    # non-Windows platforms without WinFsp / winfspy installed.
    # ---------------------------------------------------------------------------

    class _FakeFileAttribute:  # noqa: N801
        FILE_ATTRIBUTE_DIRECTORY = 0x10
        FILE_ATTRIBUTE_NORMAL = 0x80

    class _FakeCreateOptions:  # noqa: N801
        FILE_DIRECTORY_FILE = 0x1

    FILE_ATTRIBUTE = _FakeFileAttribute()  # type: ignore[assignment]
    CREATE_FILE_CREATE_OPTIONS = _FakeCreateOptions()  # type: ignore[assignment]

    class BaseFileSystemOperations:  # type: ignore[no-redef]
        """Minimal stub for testing without winfspy."""

        def __init__(self) -> None:
            pass

    class NTStatusError(OSError):  # type: ignore[no-redef]
        """Minimal stub for testing without winfspy."""

        def __init__(self, code: int) -> None:
            super().__init__(f"NT status 0x{code:08X}")
            self.code = code

    class FileSystemHost:  # type: ignore[no-redef]
        """Minimal stub for testing without winfspy."""

        def __init__(self, fs: object) -> None:
            self.fs = fs
            self.prefix = ""
            self.file_system_name = ""
            self.volume_label = ""

        def mount(self, mountpoint: str) -> None:  # noqa: ARG002
            raise RuntimeError("WinFsp not available — install winfspy")

        def unmount(self) -> None:
            pass

        def wait(self) -> None:
            pass


def check_winfsp_available() -> bool:
    """Check if WinFsp and winfspy are installed.

    Returns:
        True if winfspy is importable and WinFsp driver is present.
    """
    return WINFSP_AVAILABLE


def _format_mountpoint(mountpoint: str) -> str:
    """Normalize a mount point for WinFsp.

    Drive letters (e.g., "V:") get a trailing backslash.
    Directory paths are returned as-is.

    Args:
        mountpoint: Drive letter or directory path.

    Returns:
        Normalized mount point string.
    """
    mp = mountpoint.strip()
    if len(mp) == 2 and mp[1] == ":":
        return mp + "\\"
    return mp


class _SkrefWindowsFS(BaseFileSystemOperations):
    """WinFsp operations that proxy through a Vault.

    Maps WinFsp's Windows-style file system operations to the
    same Vault read/write/list_dir/delete interface used by the
    Linux FUSE implementation.
    """

    def __init__(self, vault) -> None:
        super().__init__()
        from .vault import Vault

        self._vault: Vault = vault
        # fd -> (rel_path, data_bytes, dirty_flag)
        self._open_files: dict[int, tuple[str, bytearray, bool]] = {}
        self._next_fd = 100

    @staticmethod
    def _to_rel(file_name: str) -> str:
        """Convert a WinFsp file name to a vault-relative path.

        WinFsp passes paths like ``\\dir\\file.txt``. We convert
        to forward-slash relative paths for the vault.
        """
        rel = file_name.replace("\\", "/").lstrip("/")
        return rel

    def get_volume_info(self) -> dict:
        """Return volume metadata."""
        return {
            "total_size": 1024 * 1024 * 1024,  # 1 GB virtual
            "free_size": 512 * 1024 * 1024,
            "volume_label": "SKRef",
            "volume_serial": 0x5B5EF,
        }

    def get_security_by_name(self, file_name: str) -> dict:
        """Get file attributes and security descriptor for a path.

        This is called by Windows before any open/read/create to
        check if the path exists and what kind of object it is.
        """
        rel = self._to_rel(file_name)

        if rel == "":
            return {
                "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY,
                "allocation_size": 0,
                "file_size": 0,
            }

        # Check if it's a directory
        try:
            entries = self._vault.list_dir(rel)
            if entries is not None:
                return {
                    "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY,
                    "allocation_size": 0,
                    "file_size": 0,
                }
        except (FileNotFoundError, OSError):
            pass

        # Check if it's a file
        if self._vault.exists(rel):
            size = self._vault.file_size(rel)
            return {
                "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL,
                "allocation_size": size,
                "file_size": size,
            }

        raise NTStatusError(0xC0000034)  # STATUS_OBJECT_NAME_NOT_FOUND

    def open(self, file_name: str, create_options: int, granted_access: int) -> int:
        """Open a file — decrypt content into memory buffer."""
        rel = self._to_rel(file_name)

        if not rel or (create_options & CREATE_FILE_CREATE_OPTIONS.FILE_DIRECTORY_FILE):
            fd = self._next_fd
            self._next_fd += 1
            self._open_files[fd] = (rel, bytearray(), False)
            return fd

        try:
            data = bytearray(self._vault.read(rel))
        except FileNotFoundError:
            raise NTStatusError(0xC0000034)  # STATUS_OBJECT_NAME_NOT_FOUND

        fd = self._next_fd
        self._next_fd += 1
        self._open_files[fd] = (rel, data, False)
        return fd

    def read(self, file_context: int, offset: int, length: int) -> bytes:
        """Read bytes from an open file's memory buffer."""
        if file_context not in self._open_files:
            raise NTStatusError(0xC0000008)  # STATUS_INVALID_HANDLE
        _, data, _ = self._open_files[file_context]
        return bytes(data[offset:offset + length])

    def write(
        self,
        file_context: int,
        buffer: bytes,
        offset: int,
        write_to_end_of_file: bool,
        constrained_io: bool,
    ) -> int:
        """Write bytes to an open file's memory buffer."""
        if file_context not in self._open_files:
            raise NTStatusError(0xC0000008)
        rel, data, _ = self._open_files[file_context]

        if write_to_end_of_file:
            offset = len(data)

        end = offset + len(buffer)
        if end > len(data):
            data.extend(b"\x00" * (end - len(data)))
        data[offset:end] = buffer
        self._open_files[file_context] = (rel, data, True)
        return len(buffer)

    def cleanup(self, file_context: int, file_name: str, flags: int) -> None:
        """Pre-close cleanup — handle delete-on-close."""
        if flags & 1:  # FspCleanupDelete
            rel = self._to_rel(file_name)
            try:
                self._vault.delete(rel)
            except (FileNotFoundError, OSError):
                pass

    def close(self, file_context: int) -> None:
        """Close a file — encrypt and flush if dirty."""
        if file_context not in self._open_files:
            return
        rel, data, dirty = self._open_files.pop(file_context)
        if dirty and rel:
            self._vault.write(rel, bytes(data))

    def create(
        self,
        file_name: str,
        create_options: int,
        granted_access: int,
        file_attributes: int,
        security_descriptor: bytes,
        allocation_size: int,
    ) -> tuple[int, dict]:
        """Create a new file or directory."""
        rel = self._to_rel(file_name)

        if create_options & CREATE_FILE_CREATE_OPTIONS.FILE_DIRECTORY_FILE:
            self._vault.mkdir(rel)
            fd = self._next_fd
            self._next_fd += 1
            self._open_files[fd] = (rel, bytearray(), False)
            return fd, {
                "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY,
                "allocation_size": 0,
                "file_size": 0,
            }

        self._vault.write(rel, b"")
        fd = self._next_fd
        self._next_fd += 1
        self._open_files[fd] = (rel, bytearray(), True)
        return fd, {
            "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL,
            "allocation_size": 0,
            "file_size": 0,
        }

    def read_directory(self, file_context: int, marker: Optional[str]) -> list[dict]:
        """List directory entries."""
        if file_context not in self._open_files:
            raise NTStatusError(0xC0000008)
        rel, _, _ = self._open_files[file_context]

        entries = self._vault.list_dir(rel)
        result = []

        # Add . and .. entries
        now_ft = int((time.time() + 11644473600) * 10_000_000)
        for special in [".", ".."]:
            result.append({
                "file_name": special,
                "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY,
                "file_size": 0,
                "allocation_size": 0,
                "creation_time": now_ft,
                "last_access_time": now_ft,
                "last_write_time": now_ft,
            })

        for entry in entries:
            if entry.is_dir:
                attrs = FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
                size = 0
            else:
                attrs = FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL
                size = entry.size
            result.append({
                "file_name": entry.name,
                "file_attributes": attrs,
                "file_size": size,
                "allocation_size": size,
                "creation_time": now_ft,
                "last_access_time": now_ft,
                "last_write_time": now_ft,
            })

        # Apply marker — skip entries up to and including the marker
        if marker:
            for i, e in enumerate(result):
                if e["file_name"] == marker:
                    return result[i + 1:]

        return result

    def get_file_info(self, file_context: int) -> dict:
        """Get file info for an open file handle."""
        if file_context not in self._open_files:
            raise NTStatusError(0xC0000008)
        rel, data, _ = self._open_files[file_context]

        if not rel:
            return {
                "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY,
                "file_size": 0,
                "allocation_size": 0,
            }

        return {
            "file_attributes": FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL,
            "file_size": len(data),
            "allocation_size": len(data),
        }

    def set_file_size(self, file_context: int, new_size: int, set_allocation_size: bool) -> None:
        """Resize a file (truncate or extend)."""
        if file_context not in self._open_files:
            raise NTStatusError(0xC0000008)
        rel, data, _ = self._open_files[file_context]

        if new_size < len(data):
            del data[new_size:]
        else:
            data.extend(b"\x00" * (new_size - len(data)))
        self._open_files[file_context] = (rel, data, True)


def mount_vault_windows(vault, mountpoint: str, foreground: bool = True) -> None:
    """Mount a vault as a Windows drive letter or directory.

    Args:
        vault: A Vault instance.
        mountpoint: Drive letter (e.g., "V:") or directory path.
        foreground: Run in foreground (True) or as service.

    Raises:
        RuntimeError: If winfspy / WinFsp is not installed.
    """
    if not WINFSP_AVAILABLE:
        raise RuntimeError(
            "Windows vault mounting requires WinFsp and winfspy.\n"
            "1. Install WinFsp: https://winfsp.dev/rel/\n"
            "2. Install winfspy: pip install skref[windows]"
        )

    mp = _format_mountpoint(mountpoint)

    fs = _SkrefWindowsFS(vault)

    host = FileSystemHost(fs)
    host.prefix = ""
    host.file_system_name = "SKRef"
    host.volume_label = f"SKRef - {vault.config.name}"

    logger.info("Mounting vault '%s' at %s", vault.config.name, mp)

    try:
        host.mount(mp)
        if foreground:
            import signal
            signal.signal(signal.SIGINT, lambda *_: host.unmount())
            host.wait()
    finally:
        try:
            host.unmount()
        except Exception:
            pass
        logger.info("Unmounted %s", mp)
