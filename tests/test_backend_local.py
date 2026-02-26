"""Tests for the local filesystem backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from skref.backends.local import LocalBackend


@pytest.fixture
def backend(tmp_path: Path) -> LocalBackend:
    """Create a LocalBackend in a temp dir."""
    return LocalBackend(tmp_path / "vault")


class TestLocalBackend:
    """Local backend put/get/list/delete."""

    def test_put_and_get(self, backend: LocalBackend) -> None:
        """Store and retrieve bytes."""
        backend.put("hello.txt", b"world")
        assert backend.get("hello.txt") == b"world"

    def test_get_missing_raises(self, backend: LocalBackend) -> None:
        """Reading a missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            backend.get("nope.txt")

    def test_nested_put(self, backend: LocalBackend) -> None:
        """Writing to a nested path creates parent dirs."""
        backend.put("a/b/c.txt", b"deep")
        assert backend.get("a/b/c.txt") == b"deep"

    def test_list_dir_root(self, backend: LocalBackend) -> None:
        """List root directory."""
        backend.put("file1.txt", b"1")
        backend.put("file2.txt", b"2")
        entries = backend.list_dir()
        names = {e.name for e in entries}
        assert "file1.txt" in names
        assert "file2.txt" in names

    def test_list_dir_subdirectory(self, backend: LocalBackend) -> None:
        """List a subdirectory."""
        backend.put("sub/a.txt", b"a")
        backend.put("sub/b.txt", b"b")
        entries = backend.list_dir("sub")
        names = {e.name for e in entries}
        assert names == {"a.txt", "b.txt"}

    def test_list_dir_shows_dirs(self, backend: LocalBackend) -> None:
        """Directories appear with is_dir=True."""
        backend.mkdir("mydir")
        entries = backend.list_dir()
        dirs = [e for e in entries if e.is_dir]
        assert any(e.name == "mydir" for e in dirs)

    def test_exists(self, backend: LocalBackend) -> None:
        """Exists returns True for stored files."""
        backend.put("yes.txt", b"y")
        assert backend.exists("yes.txt")
        assert not backend.exists("no.txt")

    def test_delete(self, backend: LocalBackend) -> None:
        """Deleting removes the file."""
        backend.put("gone.txt", b"bye")
        assert backend.exists("gone.txt")
        backend.delete("gone.txt")
        assert not backend.exists("gone.txt")

    def test_mkdir(self, backend: LocalBackend) -> None:
        """mkdir creates a directory."""
        backend.mkdir("newdir")
        assert (backend.root / "newdir").is_dir()

    def test_file_size(self, backend: LocalBackend) -> None:
        """file_size returns correct byte count."""
        data = b"hello world"
        backend.put("sized.txt", data)
        assert backend.file_size("sized.txt") == len(data)

    def test_path_traversal_blocked(self, backend: LocalBackend) -> None:
        """Path traversal outside vault root is rejected."""
        with pytest.raises(PermissionError):
            backend.put("../../etc/passwd", b"nope")

    def test_hidden_files_excluded(self, backend: LocalBackend) -> None:
        """Dotfiles are excluded from list_dir."""
        backend.put(".hidden", b"secret")
        backend.put("visible.txt", b"hi")
        entries = backend.list_dir()
        names = {e.name for e in entries}
        assert "visible.txt" in names
        assert ".hidden" not in names
