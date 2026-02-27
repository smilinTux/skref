"""Tests for the Nextcloud WebDAV backend.

All HTTP calls are mocked — no real Nextcloud instance required.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from skref.backends.nextcloud import (
    NextcloudBackend,
    NextcloudError,
    _META_SUFFIX,
    _WEBDAV_PREFIX,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NC_URL = "https://cloud.example.com"
_USER = "alice"
_PASS = "s3cret"
_VAULT = "/skref/"
_DAV_ROOT = f"{_NC_URL}{_WEBDAV_PREFIX}/{_USER}"


def _make_backend(**kwargs: Any) -> NextcloudBackend:
    """Create a NextcloudBackend with test credentials."""
    defaults = dict(url=_NC_URL, username=_USER, password=_PASS, vault_path=_VAULT)
    defaults.update(kwargs)
    return NextcloudBackend(**defaults)


def _propfind_xml(entries: list[dict]) -> bytes:
    """Build a minimal PROPFIND multistatus response.

    Args:
        entries: List of dicts with keys ``href``, ``name``, ``is_dir``, ``size``.

    Returns:
        UTF-8 encoded XML bytes.
    """
    items = []
    for e in entries:
        rt = "<d:collection/>" if e.get("is_dir") else ""
        size_el = (
            f"<d:getcontentlength>{e.get('size', 0)}</d:getcontentlength>"
            if not e.get("is_dir")
            else ""
        )
        items.append(
            f"""
            <d:response>
              <d:href>{e['href']}</d:href>
              <d:propstat>
                <d:prop>
                  <d:displayname>{e.get('name', '')}</d:displayname>
                  <d:resourcetype>{rt}</d:resourcetype>
                  {size_el}
                  <d:getlastmodified>Thu, 01 Jan 2026 00:00:00 GMT</d:getlastmodified>
                </d:prop>
                <d:status>HTTP/1.1 200 OK</d:status>
              </d:propstat>
            </d:response>"""
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<d:multistatus xmlns:d="DAV:">'
        + "".join(items)
        + "</d:multistatus>"
    )
    return xml.encode()


def _mock_response(
    status_code: int = 200,
    content: bytes = b"",
    text: str = "",
) -> MagicMock:
    """Build a mock requests.Response.

    Args:
        status_code: HTTP status code.
        content: Response body bytes.
        text: Response body text (used for error messages).

    Returns:
        Configured MagicMock that looks like a requests.Response.
    """
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.content = content
    resp.text = text or content.decode(errors="replace")
    return resp


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> NextcloudBackend:
    """NextcloudBackend with mocked session."""
    nc = _make_backend()
    nc._session = MagicMock()
    return nc


# ---------------------------------------------------------------------------
# Test: construction & validation
# ---------------------------------------------------------------------------


class TestNextcloudBackendInit:
    """Constructor validation and environment-variable fallback."""

    def test_missing_url_raises(self) -> None:
        """ValueError is raised when URL is absent."""
        with pytest.raises(ValueError, match="URL"):
            NextcloudBackend(url="", username=_USER, password=_PASS)

    def test_missing_username_raises(self) -> None:
        """ValueError is raised when username is absent."""
        with pytest.raises(ValueError, match="username"):
            NextcloudBackend(url=_NC_URL, username="", password=_PASS)

    def test_missing_password_raises(self) -> None:
        """ValueError is raised when password is absent."""
        with pytest.raises(ValueError, match="password"):
            NextcloudBackend(url=_NC_URL, username=_USER, password="")

    def test_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Constructor reads credentials from environment variables."""
        monkeypatch.setenv("SKREF_NEXTCLOUD_URL", _NC_URL)
        monkeypatch.setenv("SKREF_NEXTCLOUD_USER", _USER)
        monkeypatch.setenv("SKREF_NEXTCLOUD_PASS", _PASS)
        monkeypatch.setenv("SKREF_NEXTCLOUD_PATH", "/myvault/")
        nc = NextcloudBackend()
        assert nc._url == _NC_URL
        assert nc._username == _USER
        assert nc._vault_path == "/myvault/"

    def test_vault_path_normalised(self) -> None:
        """vault_path always ends with a single slash."""
        nc = _make_backend(vault_path="/skref")
        assert nc._vault_path == "/skref/"


# ---------------------------------------------------------------------------
# Test: health
# ---------------------------------------------------------------------------


class TestHealth:
    """health() connection check."""

    def test_health_ok(self, backend: NextcloudBackend) -> None:
        """health() returns ok=True on HTTP 207."""
        backend._session.request.return_value = _mock_response(207)
        result = backend.health()
        assert result["ok"] is True
        assert result["url"] == _NC_URL
        assert result["user"] == _USER

    def test_health_auth_failure(self, backend: NextcloudBackend) -> None:
        """health() returns ok=False on HTTP 401."""
        backend._session.request.return_value = _mock_response(401)
        result = backend.health()
        assert result["ok"] is False

    def test_health_network_error(self, backend: NextcloudBackend) -> None:
        """health() catches connection errors and returns ok=False."""
        backend._session.request.side_effect = requests.ConnectionError("refused")
        result = backend.health()
        assert result["ok"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# Test: put / get round-trip
# ---------------------------------------------------------------------------


class TestPutGet:
    """put() uploads; get() downloads."""

    def _setup_put(self, backend: NextcloudBackend) -> None:
        """Wire the mock session so put() succeeds."""
        # PROPFIND for ensure_vault_dir -> 207 (exists)
        # PUT -> 201
        backend._session.request.return_value = _mock_response(207)

    def test_put_issues_http_put(self, backend: NextcloudBackend) -> None:
        """put() calls PUT on the correct URL."""
        # Return 207 for all PROPFIND checks and 201 for the MKCOL/PUT.
        # Using a callable side_effect avoids StopIteration on deep paths.
        def _smart_response(method: str, url: str, **kwargs) -> MagicMock:
            if method == "PUT":
                return _mock_response(201)
            return _mock_response(207)

        backend._session.request.side_effect = _smart_response
        backend.put("docs/note.txt.gpg", b"encrypted")
        called_methods = [c.args[0] for c in backend._session.request.call_args_list]
        assert "PUT" in called_methods

    def test_get_returns_content(self, backend: NextcloudBackend) -> None:
        """get() returns response body bytes."""
        payload = b"encrypted-payload"
        backend._session.get.return_value = _mock_response(200, payload)
        result = backend.get("docs/note.txt.gpg")
        assert result == payload

    def test_get_missing_raises_file_not_found(self, backend: NextcloudBackend) -> None:
        """get() raises FileNotFoundError on HTTP 404."""
        backend._session.get.return_value = _mock_response(404)
        with pytest.raises(FileNotFoundError):
            backend.get("ghost.txt.gpg")

    def test_get_server_error_raises_nextcloud_error(
        self, backend: NextcloudBackend
    ) -> None:
        """get() raises NextcloudError on HTTP 500."""
        backend._session.get.return_value = _mock_response(500, text="internal error")
        with pytest.raises(NextcloudError):
            backend.get("broken.txt")


# ---------------------------------------------------------------------------
# Test: delete
# ---------------------------------------------------------------------------


class TestDelete:
    """delete() removes the file and its sidecar."""

    def test_delete_calls_http_delete(self, backend: NextcloudBackend) -> None:
        """delete() sends DELETE request for the primary file."""
        backend._session.request.return_value = _mock_response(204)
        backend._session.delete.return_value = _mock_response(204)
        backend.delete("ref.gpg")
        delete_calls = [
            c for c in backend._session.request.call_args_list
            if c.args[0] == "DELETE"
        ]
        assert delete_calls, "Expected at least one DELETE request"

    def test_delete_also_removes_sidecar(self, backend: NextcloudBackend) -> None:
        """delete() attempts to remove the .meta.json sidecar."""
        backend._session.request.return_value = _mock_response(204)
        backend._session.delete.return_value = _mock_response(204)
        backend.delete("ref.gpg")
        # The sidecar cleanup uses session.delete directly
        backend._session.delete.assert_called_once()
        sidecar_url = backend._session.delete.call_args.args[0]
        assert sidecar_url.endswith("ref.gpg" + _META_SUFFIX)

    def test_delete_tolerates_missing_file(self, backend: NextcloudBackend) -> None:
        """delete() does not raise if the remote file is already gone (404)."""
        backend._session.request.return_value = _mock_response(404)
        backend._session.delete.return_value = _mock_response(404)
        # Should complete without raising
        backend.delete("already-gone.gpg")


# ---------------------------------------------------------------------------
# Test: list_dir
# ---------------------------------------------------------------------------


class TestListDir:
    """list_dir() returns FileEntry objects, filtering sidecars."""

    def test_list_dir_returns_entries(self, backend: NextcloudBackend) -> None:
        """list_dir() parses PROPFIND and returns one entry per real file."""
        collection_href = f"{_DAV_ROOT}{_VAULT}"
        xml = _propfind_xml(
            [
                {"href": collection_href, "name": "", "is_dir": True},
                {
                    "href": collection_href + "doc.txt.gpg",
                    "name": "doc.txt.gpg",
                    "is_dir": False,
                    "size": 512,
                },
                {
                    "href": collection_href + "doc.txt.gpg" + _META_SUFFIX,
                    "name": "doc.txt.gpg" + _META_SUFFIX,
                    "is_dir": False,
                    "size": 128,
                },
            ]
        )
        backend._session.request.return_value = _mock_response(207, xml)
        entries = backend.list_dir()
        names = [e.name for e in entries]
        # Primary file present, sidecar filtered out
        assert "doc.txt.gpg" in names
        assert "doc.txt.gpg" + _META_SUFFIX not in names

    def test_list_dir_empty_vault(self, backend: NextcloudBackend) -> None:
        """list_dir() returns an empty list when the vault is empty."""
        collection_href = f"{_DAV_ROOT}{_VAULT}"
        xml = _propfind_xml(
            [{"href": collection_href, "name": "", "is_dir": True}]
        )
        backend._session.request.return_value = _mock_response(207, xml)
        entries = backend.list_dir()
        assert entries == []

    def test_list_dir_missing_vault_returns_empty(
        self, backend: NextcloudBackend
    ) -> None:
        """list_dir() returns empty list when the vault path is missing (404)."""
        backend._session.request.return_value = _mock_response(404)
        entries = backend.list_dir()
        assert entries == []


# ---------------------------------------------------------------------------
# Test: save / get_ref round-trip (metadata sidecar)
# ---------------------------------------------------------------------------


class TestSaveGetRef:
    """save() stores content + sidecar; get_ref() retrieves both."""

    def test_save_stores_content_and_sidecar(self, backend: NextcloudBackend) -> None:
        """save() calls put() twice: once for content, once for the sidecar."""
        put_paths: list[str] = []

        def fake_put(rel_path: str, data: bytes) -> None:
            put_paths.append(rel_path)

        backend.put = fake_put  # type: ignore[method-assign]
        backend.save("ref001.gpg", b"encrypted", {"title": "My Note", "tags": ["work"]})

        assert "ref001.gpg" in put_paths
        assert "ref001.gpg" + _META_SUFFIX in put_paths

    def test_get_ref_returns_content_and_metadata(
        self, backend: NextcloudBackend
    ) -> None:
        """get_ref() returns (content, metadata) tuple."""
        meta = {"title": "Contract", "tags": ["legal"]}
        store: dict[str, bytes] = {
            "ref002.gpg": b"cipher",
            "ref002.gpg" + _META_SUFFIX: json.dumps(meta).encode(),
        }

        def fake_get(rel_path: str) -> bytes:
            if rel_path in store:
                return store[rel_path]
            raise FileNotFoundError(rel_path)

        backend.get = fake_get  # type: ignore[method-assign]
        content, metadata = backend.get_ref("ref002.gpg")
        assert content == b"cipher"
        assert metadata["title"] == "Contract"
        assert metadata["tags"] == ["legal"]

    def test_get_ref_missing_sidecar_returns_empty_metadata(
        self, backend: NextcloudBackend
    ) -> None:
        """get_ref() returns empty metadata dict when the sidecar is absent."""

        def fake_get(rel_path: str) -> bytes:
            if rel_path == "ref003.gpg":
                return b"cipher"
            raise FileNotFoundError(rel_path)

        backend.get = fake_get  # type: ignore[method-assign]
        content, metadata = backend.get_ref("ref003.gpg")
        assert content == b"cipher"
        assert metadata == {}


# ---------------------------------------------------------------------------
# Test: list_refs
# ---------------------------------------------------------------------------


class TestListRefs:
    """list_refs() returns ref dicts with merged metadata."""

    def test_list_refs_merges_metadata(self, backend: NextcloudBackend) -> None:
        """list_refs() includes sidecar metadata in each ref dict."""
        meta = {"title": "Invoice", "tags": ["finance"]}
        entries_from_list_dir = [
            MagicMock(path="invoice.gpg", is_dir=False, size=1024),
        ]
        backend.list_dir = MagicMock(return_value=entries_from_list_dir)  # type: ignore

        def fake_get(rel_path: str) -> bytes:
            if rel_path.endswith(_META_SUFFIX):
                return json.dumps(meta).encode()
            raise FileNotFoundError(rel_path)

        backend.get = fake_get  # type: ignore[method-assign]

        refs = backend.list_refs()
        assert len(refs) == 1
        assert refs[0]["ref_id"] == "invoice.gpg"
        assert refs[0]["title"] == "Invoice"
        assert refs[0]["tags"] == ["finance"]

    def test_list_refs_skips_dirs(self, backend: NextcloudBackend) -> None:
        """list_refs() does not include directory entries."""
        entries = [
            MagicMock(path="subdir/", is_dir=True, size=0),
            MagicMock(path="note.gpg", is_dir=False, size=256),
        ]
        backend.list_dir = MagicMock(return_value=entries)  # type: ignore
        backend.get = MagicMock(side_effect=FileNotFoundError)  # type: ignore

        refs = backend.list_refs()
        ref_ids = [r["ref_id"] for r in refs]
        assert "note.gpg" in ref_ids
        assert "subdir/" not in ref_ids


# ---------------------------------------------------------------------------
# Test: search
# ---------------------------------------------------------------------------


class TestSearch:
    """search() filters refs by title/tags."""

    def _make_refs(self) -> list[dict]:
        return [
            {"ref_id": "a.gpg", "title": "Budget 2026", "tags": ["finance"], "size": 100},
            {"ref_id": "b.gpg", "title": "Meeting notes", "tags": ["work"], "size": 200},
            {"ref_id": "c.gpg", "title": "Tax return", "tags": ["finance", "legal"], "size": 300},
        ]

    def test_search_by_title(self, backend: NextcloudBackend) -> None:
        """search() matches on title substring (case-insensitive)."""
        backend.list_refs = MagicMock(return_value=self._make_refs())  # type: ignore
        results = backend.search("budget")
        assert len(results) == 1
        assert results[0]["ref_id"] == "a.gpg"

    def test_search_by_tag(self, backend: NextcloudBackend) -> None:
        """search() matches on tags (case-insensitive)."""
        backend.list_refs = MagicMock(return_value=self._make_refs())  # type: ignore
        results = backend.search("finance")
        ids = [r["ref_id"] for r in results]
        assert "a.gpg" in ids
        assert "c.gpg" in ids
        assert "b.gpg" not in ids

    def test_search_no_match(self, backend: NextcloudBackend) -> None:
        """search() returns empty list when nothing matches."""
        backend.list_refs = MagicMock(return_value=self._make_refs())  # type: ignore
        results = backend.search("nonexistent-xyz")
        assert results == []

    def test_search_case_insensitive(self, backend: NextcloudBackend) -> None:
        """search() is case-insensitive."""
        backend.list_refs = MagicMock(return_value=self._make_refs())  # type: ignore
        results = backend.search("MEETING")
        assert len(results) == 1
        assert results[0]["ref_id"] == "b.gpg"


# ---------------------------------------------------------------------------
# Test: sync_status
# ---------------------------------------------------------------------------


class TestSyncStatus:
    """sync_status() reports reachability and remote ref count."""

    def test_sync_status_ok(self, backend: NextcloudBackend) -> None:
        """sync_status() returns ok status when listing succeeds."""
        backend.list_refs = MagicMock(return_value=[  # type: ignore
            {"ref_id": "x.gpg"}, {"ref_id": "y.gpg"}
        ])
        status = backend.sync_status()
        assert status["reachable"] is True
        assert status["remote_ref_count"] == 2
        assert status["status"] == "ok"

    def test_sync_status_unreachable(self, backend: NextcloudBackend) -> None:
        """sync_status() returns unreachable when the request fails."""
        backend.list_refs = MagicMock(  # type: ignore
            side_effect=requests.ConnectionError("timeout")
        )
        status = backend.sync_status()
        assert status["reachable"] is False
        assert status["status"] == "unreachable"
        assert "error" in status


# ---------------------------------------------------------------------------
# Test: exists / mkdir
# ---------------------------------------------------------------------------


class TestExistsMkdir:
    """exists() and mkdir() thin wrappers."""

    def test_exists_true(self, backend: NextcloudBackend) -> None:
        """exists() returns True when PROPFIND succeeds."""
        backend._session.request.return_value = _mock_response(207)
        assert backend.exists("myfile.gpg") is True

    def test_exists_false_on_404(self, backend: NextcloudBackend) -> None:
        """exists() returns False on HTTP 404."""
        backend._session.request.return_value = _mock_response(404)
        assert backend.exists("missing.gpg") is False

    def test_mkdir_creates_collection(self, backend: NextcloudBackend) -> None:
        """mkdir() issues MKCOL for the directory."""
        # PROPFIND for ensure_vault_dir -> 207 (vault exists)
        # PROPFIND for intermediate dir -> 404 (doesn't exist) -> MKCOL 201
        responses = [
            _mock_response(207),   # ensure_vault_dir PROPFIND
            _mock_response(404),   # dir PROPFIND -> missing
            _mock_response(201),   # MKCOL -> created
        ]
        backend._session.request.side_effect = responses
        backend.mkdir("newdir")
        methods = [c.args[0] for c in backend._session.request.call_args_list]
        assert "MKCOL" in methods


# ---------------------------------------------------------------------------
# Test: NextcloudError
# ---------------------------------------------------------------------------


class TestNextcloudError:
    """NextcloudError carries method, url, and status."""

    def test_error_attributes(self) -> None:
        """NextcloudError stores method, url, and status."""
        err = NextcloudError("PUT", "https://example.com/file", 409, "conflict")
        assert err.method == "PUT"
        assert err.url == "https://example.com/file"
        assert err.status == 409
        assert "409" in str(err)

    def test_get_raises_nextcloud_error_on_unexpected_status(
        self, backend: NextcloudBackend
    ) -> None:
        """_request() raises NextcloudError when status is unexpected."""
        backend._session.get.return_value = _mock_response(503, text="unavailable")
        with pytest.raises(NextcloudError) as exc_info:
            backend.get("file.gpg")
        assert exc_info.value.status == 503
