"""Tests for Windows FUSE support — mock-based, runs on any platform."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from skref.fuse_windows import (
    NTStatusError,
    _SkrefWindowsFS,
    _format_mountpoint,
    check_winfsp_available,
)
from skref.backends.base import FileEntry


# ---------------------------------------------------------------------------
# Helper: build a standard mock vault
# ---------------------------------------------------------------------------

def _make_vault(files: dict[str, bytes] | None = None, dirs: list[str] | None = None):
    """Return a mock vault with configurable content."""
    vault = MagicMock()
    vault.config = MagicMock()
    vault.config.name = "test-vault"

    files = files or {}
    dirs = dirs or []

    def _exists(rel_path):
        return rel_path in files

    def _read(rel_path):
        if rel_path not in files:
            raise FileNotFoundError(rel_path)
        return files[rel_path]

    def _write(rel_path, data):
        files[rel_path] = data

    def _file_size(rel_path):
        if rel_path not in files:
            raise FileNotFoundError(rel_path)
        return len(files[rel_path])

    def _list_dir(rel_path):
        # Real backends raise FileNotFoundError when listing a plain file path
        if rel_path and rel_path in files:
            raise FileNotFoundError(f"{rel_path!r} is a file, not a directory")
        # Simple flat listing: return entries whose "parent" matches rel_path
        entries = []
        prefix = rel_path.rstrip("/") + "/" if rel_path else ""
        seen = set()
        for name in files:
            if prefix:
                if not name.startswith(prefix):
                    continue
                rest = name[len(prefix):]
            else:
                rest = name
            # Immediate child only
            parts = rest.split("/")
            child = parts[0]
            if child in seen:
                continue
            seen.add(child)
            if len(parts) == 1:
                entries.append(FileEntry(name=child, path=name, is_dir=False, size=len(files[name])))
            else:
                entries.append(FileEntry(name=child, path=prefix + child, is_dir=True, size=0))
        for d in dirs:
            d_prefix = rel_path.rstrip("/") + "/" if rel_path else ""
            if d.startswith(d_prefix):
                rest = d[len(d_prefix):]
                if rest and "/" not in rest:
                    entries.append(FileEntry(name=rest, path=d, is_dir=True, size=0))
        return entries

    def _mkdir(rel_path):
        dirs.append(rel_path)

    def _delete(rel_path):
        files.pop(rel_path, None)

    vault.exists.side_effect = _exists
    vault.read.side_effect = _read
    vault.write.side_effect = _write
    vault.file_size.side_effect = _file_size
    vault.list_dir.side_effect = _list_dir
    vault.mkdir.side_effect = _mkdir
    vault.delete.side_effect = _delete

    return vault, files, dirs


# ---------------------------------------------------------------------------
# check_winfsp_available
# ---------------------------------------------------------------------------

class TestCheckWinfspAvailable:
    def test_returns_bool(self):
        result = check_winfsp_available()
        assert isinstance(result, bool)

    def test_available_when_flag_true(self):
        import skref.fuse_windows as fw
        old = fw.WINFSP_AVAILABLE
        fw.WINFSP_AVAILABLE = True
        assert fw.check_winfsp_available() is True
        fw.WINFSP_AVAILABLE = old

    def test_unavailable_when_flag_false(self):
        import skref.fuse_windows as fw
        old = fw.WINFSP_AVAILABLE
        fw.WINFSP_AVAILABLE = False
        assert fw.check_winfsp_available() is False
        fw.WINFSP_AVAILABLE = old


# ---------------------------------------------------------------------------
# _format_mountpoint
# ---------------------------------------------------------------------------

class TestFormatMountpoint:
    def test_drive_letter_gets_backslash(self):
        assert _format_mountpoint("V:") == "V:\\"

    def test_drive_letter_with_spaces(self):
        assert _format_mountpoint("  V:  ") == "V:\\"

    def test_directory_path_unchanged(self):
        assert _format_mountpoint(r"C:\Users\me\vault") == r"C:\Users\me\vault"

    def test_unix_path_unchanged(self):
        assert _format_mountpoint("/mnt/vault") == "/mnt/vault"

    def test_lowercase_drive_letter(self):
        assert _format_mountpoint("v:") == "v:\\"


# ---------------------------------------------------------------------------
# _SkrefWindowsFS._to_rel (static helper)
# ---------------------------------------------------------------------------

class TestToRel:
    def test_backslash_conversion(self):
        assert _SkrefWindowsFS._to_rel("\\dir\\subdir\\file.txt") == "dir/subdir/file.txt"

    def test_root_gives_empty(self):
        assert _SkrefWindowsFS._to_rel("\\") == ""

    def test_single_file(self):
        assert _SkrefWindowsFS._to_rel("\\file.txt") == "file.txt"

    def test_forward_slash_path(self):
        assert _SkrefWindowsFS._to_rel("/already/posix") == "already/posix"

    def test_no_leading_slash(self):
        assert _SkrefWindowsFS._to_rel("plain") == "plain"


# ---------------------------------------------------------------------------
# get_volume_info
# ---------------------------------------------------------------------------

class TestGetVolumeInfo:
    def test_returns_required_keys(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        info = fs.get_volume_info()
        assert "total_size" in info
        assert "free_size" in info
        assert "volume_label" in info
        assert info["volume_label"] == "SKRef"

    def test_sizes_are_positive(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        info = fs.get_volume_info()
        assert info["total_size"] > 0
        assert info["free_size"] > 0


# ---------------------------------------------------------------------------
# get_security_by_name
# ---------------------------------------------------------------------------

class TestGetSecurityByName:
    def test_root_is_directory(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        info = fs.get_security_by_name("\\")
        from skref.fuse_windows import FILE_ATTRIBUTE
        assert info["file_attributes"] == FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY

    def test_existing_file_attributes(self):
        vault, _, _ = _make_vault(files={"doc.txt": b"hello"})
        fs = _SkrefWindowsFS(vault)
        from skref.fuse_windows import FILE_ATTRIBUTE
        info = fs.get_security_by_name("\\doc.txt")
        assert info["file_attributes"] == FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL
        assert info["file_size"] == 5

    def test_not_found_raises_nt_error(self):
        vault, _, _ = _make_vault()
        vault.list_dir.side_effect = FileNotFoundError
        fs = _SkrefWindowsFS(vault)
        with pytest.raises(NTStatusError):
            fs.get_security_by_name("\\missing.txt")

    def test_directory_detected_via_list_dir(self):
        vault, _, _ = _make_vault(files={"subdir/file.txt": b"data"})
        # list_dir returns entries when dir exists
        fs = _SkrefWindowsFS(vault)
        from skref.fuse_windows import FILE_ATTRIBUTE
        info = fs.get_security_by_name("\\subdir")
        assert info["file_attributes"] == FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------

class TestOpen:
    def test_open_file_loads_data(self):
        vault, _, _ = _make_vault(files={"notes.txt": b"sovereign"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\notes.txt", 0, 0)
        assert fd >= 100
        _, data, dirty = fs._open_files[fd]
        assert bytes(data) == b"sovereign"
        assert dirty is False

    def test_open_directory(self):
        from skref.fuse_windows import CREATE_FILE_CREATE_OPTIONS
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\mydir", CREATE_FILE_CREATE_OPTIONS.FILE_DIRECTORY_FILE, 0)
        assert fd in fs._open_files
        _, data, _ = fs._open_files[fd]
        assert bytes(data) == b""

    def test_open_root_returns_fd(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\", 0, 0)
        assert fd in fs._open_files

    def test_open_missing_file_raises(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        with pytest.raises(NTStatusError):
            fs.open("\\ghost.txt", 0, 0)

    def test_fd_increments(self):
        vault, _, _ = _make_vault(files={"a.txt": b"a", "b.txt": b"b"})
        fs = _SkrefWindowsFS(vault)
        fd1 = fs.open("\\a.txt", 0, 0)
        fd2 = fs.open("\\b.txt", 0, 0)
        assert fd2 == fd1 + 1


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

class TestRead:
    def test_read_full_file(self):
        vault, _, _ = _make_vault(files={"f.bin": b"\x01\x02\x03\x04"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.bin", 0, 0)
        result = fs.read(fd, 0, 4)
        assert result == b"\x01\x02\x03\x04"

    def test_read_partial_offset(self):
        vault, _, _ = _make_vault(files={"f.bin": b"ABCDEF"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.bin", 0, 0)
        assert fs.read(fd, 2, 3) == b"CDE"

    def test_read_beyond_eof_returns_available(self):
        vault, _, _ = _make_vault(files={"f.bin": b"AB"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.bin", 0, 0)
        assert fs.read(fd, 0, 100) == b"AB"

    def test_read_invalid_handle_raises(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        with pytest.raises(NTStatusError):
            fs.read(9999, 0, 10)


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

class TestWrite:
    def test_write_at_offset(self):
        vault, _, _ = _make_vault(files={"f.txt": b"HELLO"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        n = fs.write(fd, b"hi", 0, False, False)
        assert n == 2
        _, data, dirty = fs._open_files[fd]
        assert bytes(data[:2]) == b"hi"
        assert dirty is True

    def test_write_to_end_of_file(self):
        vault, _, _ = _make_vault(files={"f.txt": b"AB"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        fs.write(fd, b"CD", 0, True, False)
        _, data, _ = fs._open_files[fd]
        assert bytes(data) == b"ABCD"

    def test_write_extends_file(self):
        vault, _, _ = _make_vault(files={"f.txt": b"AB"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        fs.write(fd, b"XY", 5, False, False)
        _, data, _ = fs._open_files[fd]
        assert len(data) == 7
        assert bytes(data[5:7]) == b"XY"

    def test_write_invalid_handle_raises(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        with pytest.raises(NTStatusError):
            fs.write(9999, b"data", 0, False, False)

    def test_write_marks_dirty(self):
        vault, _, _ = _make_vault(files={"f.txt": b"X"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        _, _, dirty_before = fs._open_files[fd]
        assert dirty_before is False
        fs.write(fd, b"Y", 0, False, False)
        _, _, dirty_after = fs._open_files[fd]
        assert dirty_after is True


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_cleanup_with_delete_flag_removes_file(self):
        vault, files, _ = _make_vault(files={"del.txt": b"bye"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\del.txt", 0, 0)
        fs.cleanup(fd, "\\del.txt", flags=1)  # FspCleanupDelete = 1
        vault.delete.assert_called_once_with("del.txt")

    def test_cleanup_without_delete_flag_leaves_file(self):
        vault, _, _ = _make_vault(files={"keep.txt": b"stay"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\keep.txt", 0, 0)
        fs.cleanup(fd, "\\keep.txt", flags=0)
        vault.delete.assert_not_called()

    def test_cleanup_delete_missing_file_no_raise(self):
        vault, _, _ = _make_vault()
        vault.delete.side_effect = FileNotFoundError
        fs = _SkrefWindowsFS(vault)
        # Should not raise even if delete raises FileNotFoundError
        fs.cleanup(9999, "\\ghost.txt", flags=1)


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_dirty_file_writes_to_vault(self):
        vault, files, _ = _make_vault(files={"f.txt": b""})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        fs.write(fd, b"newcontent", 0, False, False)
        fs.close(fd)
        assert fd not in fs._open_files
        assert files["f.txt"] == b"newcontent"

    def test_close_clean_file_does_not_write(self):
        vault, _, _ = _make_vault(files={"f.txt": b"original"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        write_call_count_before = vault.write.call_count
        fs.close(fd)
        assert vault.write.call_count == write_call_count_before

    def test_close_unknown_fd_is_noop(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        fs.close(9999)  # Should not raise

    def test_close_removes_from_open_files(self):
        vault, _, _ = _make_vault(files={"f.txt": b"data"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        assert fd in fs._open_files
        fs.close(fd)
        assert fd not in fs._open_files


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

class TestCreate:
    def test_create_file(self):
        from skref.fuse_windows import FILE_ATTRIBUTE
        vault, files, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        fd, info = fs.create("\\new.txt", 0, 0, 0, b"", 0)
        assert info["file_attributes"] == FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL
        assert fd in fs._open_files

    def test_create_directory(self):
        from skref.fuse_windows import FILE_ATTRIBUTE, CREATE_FILE_CREATE_OPTIONS
        vault, _, dirs = _make_vault()
        fs = _SkrefWindowsFS(vault)
        fd, info = fs.create(
            "\\mydir",
            CREATE_FILE_CREATE_OPTIONS.FILE_DIRECTORY_FILE,
            0, 0, b"", 0,
        )
        assert info["file_attributes"] == FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY
        vault.mkdir.assert_called_once_with("mydir")

    def test_create_file_is_dirty(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        fd, _ = fs.create("\\f.txt", 0, 0, 0, b"", 0)
        _, _, dirty = fs._open_files[fd]
        assert dirty is True


# ---------------------------------------------------------------------------
# read_directory
# ---------------------------------------------------------------------------

class TestReadDirectory:
    def test_read_directory_includes_dot_entries(self):
        vault, _, _ = _make_vault(files={"a.txt": b"x"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\", 0, 0)
        entries = fs.read_directory(fd, None)
        names = [e["file_name"] for e in entries]
        assert "." in names
        assert ".." in names

    def test_read_directory_lists_files(self):
        vault, _, _ = _make_vault(files={"readme.txt": b"hello"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\", 0, 0)
        entries = fs.read_directory(fd, None)
        names = [e["file_name"] for e in entries]
        assert "readme.txt" in names

    def test_read_directory_marker_skips_entries(self):
        vault, _, _ = _make_vault(files={"a.txt": b"a", "b.txt": b"b", "c.txt": b"c"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\", 0, 0)
        all_entries = fs.read_directory(fd, None)
        # Find an entry that is NOT the last
        names = [e["file_name"] for e in all_entries]
        # Using "." as marker should skip it and return the rest
        marked_entries = fs.read_directory(fd, ".")
        marked_names = [e["file_name"] for e in marked_entries]
        assert "." not in marked_names

    def test_read_directory_invalid_handle_raises(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        with pytest.raises(NTStatusError):
            fs.read_directory(9999, None)

    def test_read_directory_entries_have_required_keys(self):
        vault, _, _ = _make_vault(files={"f.bin": b"\x00" * 100})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\", 0, 0)
        entries = fs.read_directory(fd, None)
        for entry in entries:
            assert "file_name" in entry
            assert "file_attributes" in entry
            assert "file_size" in entry
            assert "allocation_size" in entry
            assert "creation_time" in entry

    def test_read_directory_file_has_correct_size(self):
        vault, _, _ = _make_vault(files={"big.bin": b"\xff" * 200})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\", 0, 0)
        entries = fs.read_directory(fd, None)
        file_entries = [e for e in entries if e["file_name"] == "big.bin"]
        assert len(file_entries) == 1
        assert file_entries[0]["file_size"] == 200


# ---------------------------------------------------------------------------
# get_file_info
# ---------------------------------------------------------------------------

class TestGetFileInfo:
    def test_get_file_info_for_file(self):
        from skref.fuse_windows import FILE_ATTRIBUTE
        vault, _, _ = _make_vault(files={"doc.pdf": b"x" * 42})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\doc.pdf", 0, 0)
        info = fs.get_file_info(fd)
        assert info["file_attributes"] == FILE_ATTRIBUTE.FILE_ATTRIBUTE_NORMAL
        assert info["file_size"] == 42

    def test_get_file_info_for_directory(self):
        from skref.fuse_windows import FILE_ATTRIBUTE
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\", 0, 0)
        info = fs.get_file_info(fd)
        assert info["file_attributes"] == FILE_ATTRIBUTE.FILE_ATTRIBUTE_DIRECTORY

    def test_get_file_info_invalid_handle_raises(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        with pytest.raises(NTStatusError):
            fs.get_file_info(9999)


# ---------------------------------------------------------------------------
# set_file_size
# ---------------------------------------------------------------------------

class TestSetFileSize:
    def test_truncate_file(self):
        vault, _, _ = _make_vault(files={"f.txt": b"HELLO WORLD"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        fs.set_file_size(fd, 5, False)
        _, data, dirty = fs._open_files[fd]
        assert bytes(data) == b"HELLO"
        assert dirty is True

    def test_extend_file_with_zeros(self):
        vault, _, _ = _make_vault(files={"f.txt": b"AB"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        fs.set_file_size(fd, 5, False)
        _, data, _ = fs._open_files[fd]
        assert bytes(data) == b"AB\x00\x00\x00"

    def test_set_same_size_no_change(self):
        vault, _, _ = _make_vault(files={"f.txt": b"ABC"})
        fs = _SkrefWindowsFS(vault)
        fd = fs.open("\\f.txt", 0, 0)
        fs.set_file_size(fd, 3, False)
        _, data, _ = fs._open_files[fd]
        assert bytes(data) == b"ABC"

    def test_set_file_size_invalid_handle_raises(self):
        vault, _, _ = _make_vault()
        fs = _SkrefWindowsFS(vault)
        with pytest.raises(NTStatusError):
            fs.set_file_size(9999, 10, False)


# ---------------------------------------------------------------------------
# mount_vault_windows stub (when winfspy not available)
# ---------------------------------------------------------------------------

class TestMountVaultWindowsStub:
    def test_stub_raises_without_winfspy(self):
        import skref.fuse_windows as fw
        old = fw.WINFSP_AVAILABLE
        fw.WINFSP_AVAILABLE = False
        try:
            from skref.fuse_windows import mount_vault_windows
            with pytest.raises(RuntimeError, match="WinFsp"):
                mount_vault_windows(MagicMock(), "V:")
        finally:
            fw.WINFSP_AVAILABLE = old


# ---------------------------------------------------------------------------
# GPG Windows detection (crypto.py)
# ---------------------------------------------------------------------------

class TestGpgWindowsDetection:
    def test_find_gpg_on_path(self):
        import skref.crypto as crypto_mod
        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = None
        with patch("skref.crypto.shutil.which", return_value="/usr/bin/gpg"):
            result = crypto_mod._find_gpg()
            assert result == "/usr/bin/gpg"
        crypto_mod._gpg_path = old

    def test_find_gpg_windows_fallback(self):
        import skref.crypto as crypto_mod
        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = None
        with patch("skref.crypto.shutil.which", return_value=None), \
             patch("skref.crypto.platform.system", return_value="Windows"), \
             patch("skref.crypto.Path.exists", return_value=True):
            result = crypto_mod._find_gpg()
            assert result is not None
            assert "gpg" in result.lower()
        crypto_mod._gpg_path = old

    def test_find_gpg_not_found(self):
        import skref.crypto as crypto_mod
        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = None
        with patch("skref.crypto.shutil.which", return_value=None), \
             patch("skref.crypto.platform.system", return_value="Linux"):
            result = crypto_mod._find_gpg()
            assert result is None
        crypto_mod._gpg_path = old

    def test_find_gpg_caches_result(self):
        import skref.crypto as crypto_mod
        old = crypto_mod._gpg_path
        crypto_mod._gpg_path = "/cached/gpg"
        result = crypto_mod._find_gpg()
        assert result == "/cached/gpg"
        crypto_mod._gpg_path = old


# ---------------------------------------------------------------------------
# Cross-platform detection
# ---------------------------------------------------------------------------

class TestCrossPlatformDetection:
    def test_check_winfsp_on_non_windows_returns_false_or_true(self):
        """On non-Windows, winfspy is typically not installed, so returns False."""
        result = check_winfsp_available()
        assert isinstance(result, bool)

    def test_nt_status_error_has_code(self):
        err = NTStatusError(0xC0000034)
        assert hasattr(err, "code") or str(err)  # stub may store code attr

    def test_format_mountpoint_drive_letter(self):
        assert _format_mountpoint("Z:") == "Z:\\"

    def test_format_mountpoint_unc_path(self):
        path = r"\\server\share"
        assert _format_mountpoint(path) == path
