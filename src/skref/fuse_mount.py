"""
FUSE filesystem — mount an encrypted vault as a normal directory.

Files on the backend are GPG-encrypted. When you open a file in the
mounted directory, it decrypts on the fly. When you save, it encrypts
and writes back. The mount point never has plaintext on real disk.

Requires: pyfuse3 (pip install skref[fuse])
          Linux: libfuse3-dev / fuse3
          macOS: macfuse (macFUSE)

Usage:
    skref mount ~/vault --vault personal
    # Now ~/vault/ shows decrypted files
    # Open a PDF — decrypts transparently
    # Save a file — encrypts and writes to backend
    # Ctrl-C or umount ~/vault → done
"""

from __future__ import annotations

import errno
import logging
import os
import stat
import time
from typing import Optional

logger = logging.getLogger("skref.fuse")

try:
    import pyfuse3
    FUSE_AVAILABLE = True
except ImportError:
    FUSE_AVAILABLE = False


def check_fuse_available() -> bool:
    """Check if FUSE dependencies are installed.

    Returns:
        True if pyfuse3 is importable.
    """
    return FUSE_AVAILABLE


if FUSE_AVAILABLE:
    import pyfuse3
    import trio

    class _SkrefFS(pyfuse3.Operations):
        """FUSE operations that proxy through a Vault.

        Inodes:
            1 = root directory
            2+ = dynamically assigned to files/dirs as they're accessed

        The inode table maps inode -> relative vault path.
        File content is cached in memory during open/read/write cycles
        and flushed on release.
        """

        def __init__(self, vault) -> None:
            super().__init__()
            from .vault import Vault

            self._vault: Vault = vault
            self._inode_to_path: dict[int, str] = {pyfuse3.ROOT_INODE: ""}
            self._path_to_inode: dict[str, int] = {"": pyfuse3.ROOT_INODE}
            self._next_inode = pyfuse3.ROOT_INODE + 1
            # fd -> (inode, data_bytes, dirty_flag)
            self._open_files: dict[int, tuple[int, bytearray, bool]] = {}
            self._next_fd = 100

        def _get_inode(self, rel_path: str) -> int:
            """Get or create an inode for a relative path."""
            if rel_path in self._path_to_inode:
                return self._path_to_inode[rel_path]
            inode = self._next_inode
            self._next_inode += 1
            self._inode_to_path[inode] = rel_path
            self._path_to_inode[rel_path] = inode
            return inode

        def _get_path(self, inode: int) -> str:
            """Resolve inode to vault-relative path."""
            if inode not in self._inode_to_path:
                raise pyfuse3.FUSEError(errno.ENOENT)
            return self._inode_to_path[inode]

        def _make_attr(self, inode: int, is_dir: bool, size: int = 0) -> pyfuse3.EntryAttributes:
            """Build a FUSE EntryAttributes struct."""
            attr = pyfuse3.EntryAttributes()
            attr.st_ino = inode
            attr.st_size = size
            now_ns = int(time.time() * 1e9)
            attr.st_atime_ns = now_ns
            attr.st_mtime_ns = now_ns
            attr.st_ctime_ns = now_ns
            attr.st_uid = os.getuid()
            attr.st_gid = os.getgid()
            if is_dir:
                attr.st_mode = stat.S_IFDIR | 0o755
                attr.st_nlink = 2
            else:
                attr.st_mode = stat.S_IFREG | 0o644
                attr.st_nlink = 1
            return attr

        async def getattr(self, inode: int, ctx=None):
            """Get file/dir attributes."""
            rel = self._get_path(inode)
            if rel == "" or self._vault.backend.exists(rel) and self._vault.backend.list_dir(rel):
                return self._make_attr(inode, is_dir=True)
            if self._vault.exists(rel):
                sz = self._vault.file_size(rel)
                return self._make_attr(inode, is_dir=False, size=sz)
            raise pyfuse3.FUSEError(errno.ENOENT)

        async def lookup(self, parent_inode: int, name: bytes, ctx=None):
            """Look up a directory entry by name."""
            parent_path = self._get_path(parent_inode)
            child_name = name.decode("utf-8")
            child_path = f"{parent_path}/{child_name}".lstrip("/")

            is_dir = False
            entries = self._vault.list_dir(parent_path)
            for e in entries:
                if e.name == child_name:
                    is_dir = e.is_dir
                    break
            else:
                raise pyfuse3.FUSEError(errno.ENOENT)

            inode = self._get_inode(child_path)
            if is_dir:
                return self._make_attr(inode, is_dir=True)
            sz = self._vault.file_size(child_path)
            return self._make_attr(inode, is_dir=False, size=sz)

        async def opendir(self, inode: int, ctx=None):
            """Open a directory for reading."""
            return inode

        async def readdir(self, inode: int, start_id: int, token):
            """Read directory entries."""
            rel = self._get_path(inode)
            entries = self._vault.list_dir(rel)

            for idx, entry in enumerate(entries):
                if idx <= start_id:
                    continue
                child_path = f"{rel}/{entry.name}".lstrip("/")
                child_inode = self._get_inode(child_path)
                if entry.is_dir:
                    attr = self._make_attr(child_inode, is_dir=True)
                else:
                    attr = self._make_attr(child_inode, is_dir=False, size=entry.size)
                if not pyfuse3.readdir_reply(token, entry.name.encode("utf-8"), attr, idx + 1):
                    break

        async def open(self, inode: int, flags: int, ctx=None):
            """Open a file — decrypt content into memory."""
            rel = self._get_path(inode)
            try:
                data = bytearray(self._vault.read(rel))
            except FileNotFoundError:
                raise pyfuse3.FUSEError(errno.ENOENT)

            fd = self._next_fd
            self._next_fd += 1
            self._open_files[fd] = (inode, data, False)
            return pyfuse3.FileInfo(fh=fd)

        async def read(self, fd: int, offset: int, length: int):
            """Read bytes from an open file."""
            if fd not in self._open_files:
                raise pyfuse3.FUSEError(errno.EBADF)
            _, data, _ = self._open_files[fd]
            return bytes(data[offset:offset + length])

        async def write(self, fd: int, offset: int, buf: bytes):
            """Write bytes to an open file (buffered, flushed on release)."""
            if fd not in self._open_files:
                raise pyfuse3.FUSEError(errno.EBADF)
            inode, data, _ = self._open_files[fd]
            end = offset + len(buf)
            if end > len(data):
                data.extend(b"\x00" * (end - len(data)))
            data[offset:end] = buf
            self._open_files[fd] = (inode, data, True)
            return len(buf)

        async def release(self, fd: int):
            """Close a file — encrypt and flush if dirty."""
            if fd not in self._open_files:
                return
            inode, data, dirty = self._open_files.pop(fd)
            if dirty:
                rel = self._get_path(inode)
                self._vault.write(rel, bytes(data))

        async def create(self, parent_inode: int, name: bytes, mode: int, flags: int, ctx=None):
            """Create a new file."""
            parent_path = self._get_path(parent_inode)
            child_name = name.decode("utf-8")
            child_path = f"{parent_path}/{child_name}".lstrip("/")

            self._vault.write(child_path, b"")
            inode = self._get_inode(child_path)

            fd = self._next_fd
            self._next_fd += 1
            self._open_files[fd] = (inode, bytearray(), True)

            attr = self._make_attr(inode, is_dir=False, size=0)
            return pyfuse3.FileInfo(fh=fd), attr

        async def mkdir(self, parent_inode: int, name: bytes, mode: int, ctx=None):
            """Create a directory."""
            parent_path = self._get_path(parent_inode)
            child_name = name.decode("utf-8")
            child_path = f"{parent_path}/{child_name}".lstrip("/")
            self._vault.mkdir(child_path)
            inode = self._get_inode(child_path)
            return self._make_attr(inode, is_dir=True)

        async def unlink(self, parent_inode: int, name: bytes, ctx=None):
            """Delete a file."""
            parent_path = self._get_path(parent_inode)
            child_name = name.decode("utf-8")
            child_path = f"{parent_path}/{child_name}".lstrip("/")
            self._vault.delete(child_path)
            if child_path in self._path_to_inode:
                inode = self._path_to_inode.pop(child_path)
                self._inode_to_path.pop(inode, None)

        async def setattr(self, inode, attr, fields, fh, ctx):
            """Handle setattr (truncate, chmod, etc.)."""
            if fields.update_size and fh is not None and fh in self._open_files:
                ino, data, _ = self._open_files[fh]
                new_size = attr.st_size
                if new_size < len(data):
                    del data[new_size:]
                else:
                    data.extend(b"\x00" * (new_size - len(data)))
                self._open_files[fh] = (ino, data, True)
            return await self.getattr(inode)


    def mount_vault(vault, mountpoint: str, foreground: bool = True) -> None:
        """Mount a vault as a FUSE filesystem.

        Args:
            vault: A Vault instance.
            mountpoint: Directory to mount on (must exist and be empty).
            foreground: Run in foreground (True) or daemonize (False).
        """
        mp = os.path.expanduser(mountpoint)
        os.makedirs(mp, exist_ok=True)

        fs = _SkrefFS(vault)
        fuse_options = set(pyfuse3.default_options)
        fuse_options.add("fsname=skref")
        if foreground:
            fuse_options.discard("default_permissions")

        pyfuse3.init(fs, mp, fuse_options)
        logger.info("Mounted vault '%s' at %s", vault.config.name, mp)

        try:
            trio.run(pyfuse3.main)
        finally:
            pyfuse3.close(unmount=True)
            logger.info("Unmounted %s", mp)

else:
    def mount_vault(vault, mountpoint: str, foreground: bool = True) -> None:
        """Stub when pyfuse3 is not installed."""
        raise RuntimeError(
            "FUSE support requires pyfuse3. Install with: pip install skref[fuse]\n"
            "On Linux you also need: sudo apt install fuse3 libfuse3-dev\n"
            "On macOS: install macFUSE from https://osxfuse.github.io/"
        )
