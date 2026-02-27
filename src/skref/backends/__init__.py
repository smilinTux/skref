"""
Storage backends — dumb put/get/list/delete over any medium.

Each backend implements the same interface so the crypto and FUSE
layers don't care where bytes live.
"""

from __future__ import annotations

from .base import Backend, FileEntry
from .local import LocalBackend
from .nextcloud import NextcloudBackend, NextcloudError

__all__ = ["Backend", "FileEntry", "LocalBackend", "NextcloudBackend", "NextcloudError"]
