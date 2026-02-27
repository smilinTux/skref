"""
Nextcloud WebDAV backend — sovereign encrypted vaults synced via Nextcloud.

Files are stored on Nextcloud over WebDAV. Each vault file may be encrypted
at rest using the CapAuth PGP key before upload. A JSON sidecar file is stored
alongside every ref (``<name>.meta.json``) to carry searchable metadata without
decrypting the payload.

Environment variables (override constructor arguments):
    SKREF_NEXTCLOUD_URL   — Nextcloud base URL (e.g. https://cloud.example.com)
    SKREF_NEXTCLOUD_USER  — WebDAV username
    SKREF_NEXTCLOUD_PASS  — WebDAV password / app token
    SKREF_NEXTCLOUD_PATH  — Remote vault path (default: /skref/)
"""

from __future__ import annotations

import json
import logging
import os
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import quote, urljoin

import requests
from requests.auth import HTTPBasicAuth

from .base import Backend, FileEntry

logger = logging.getLogger("skref.backends.nextcloud")

# Nextcloud WebDAV endpoint relative to the instance root.
_WEBDAV_PREFIX = "/remote.php/dav/files"

# XML namespace used by WebDAV PROPFIND responses.
_DAV_NS = "DAV:"

# Suffix appended to metadata sidecar files.
_META_SUFFIX = ".meta.json"


class NextcloudError(OSError):
    """Raised when a WebDAV operation fails with an unexpected HTTP status."""

    def __init__(self, method: str, url: str, status: int, body: str = "") -> None:
        self.method = method
        self.url = url
        self.status = status
        self.body = body
        super().__init__(f"WebDAV {method} {url} -> HTTP {status}")


class NextcloudBackend(Backend):
    """Nextcloud WebDAV storage backend for SKRef vaults.

    Stores encrypted vault files on a Nextcloud instance via the standard
    WebDAV API (``/remote.php/dav/files/<user>/``).  Each file is accompanied
    by a ``.meta.json`` sidecar that holds searchable metadata (title, tags,
    timestamps) so callers can list and search without decrypting payloads.

    Args:
        url: Nextcloud instance base URL, e.g. ``https://cloud.example.com``.
            Falls back to ``SKREF_NEXTCLOUD_URL``.
        username: WebDAV username.  Falls back to ``SKREF_NEXTCLOUD_USER``.
        password: WebDAV password or app token.  Falls back to
            ``SKREF_NEXTCLOUD_PASS``.
        vault_path: Remote directory path relative to the WebDAV root, e.g.
            ``/skref/``.  Falls back to ``SKREF_NEXTCLOUD_PATH``.
        timeout: HTTP request timeout in seconds.

    Raises:
        ValueError: If URL or credentials are missing after env-var fallback.

    Example::

        backend = NextcloudBackend(
            url="https://cloud.example.com",
            username="alice",
            password="app-token",
            vault_path="/skref/",
        )
        backend.put("notes/idea.txt.gpg", encrypted_bytes)
    """

    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        vault_path: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self._url = (url or os.environ.get("SKREF_NEXTCLOUD_URL", "")).rstrip("/")
        self._username = username or os.environ.get("SKREF_NEXTCLOUD_USER", "")
        self._password = password or os.environ.get("SKREF_NEXTCLOUD_PASS", "")
        self._vault_path = (
            vault_path or os.environ.get("SKREF_NEXTCLOUD_PATH", "/skref/")
        ).rstrip("/") + "/"
        self._timeout = timeout

        if not self._url:
            raise ValueError(
                "Nextcloud URL is required. Pass url= or set SKREF_NEXTCLOUD_URL."
            )
        if not self._username:
            raise ValueError(
                "Nextcloud username is required. Pass username= or set SKREF_NEXTCLOUD_USER."
            )
        if not self._password:
            raise ValueError(
                "Nextcloud password is required. Pass password= or set SKREF_NEXTCLOUD_PASS."
            )

        self._auth = HTTPBasicAuth(self._username, self._password)
        self._session = requests.Session()
        self._session.auth = self._auth
        self._session.headers["User-Agent"] = "skref/0.1 NextcloudBackend"

        logger.debug(
            "NextcloudBackend initialised: url=%s vault_path=%s user=%s",
            self._url,
            self._vault_path,
            self._username,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _webdav_root(self) -> str:
        """Return the WebDAV base URL for this user."""
        return f"{self._url}{_WEBDAV_PREFIX}/{quote(self._username, safe='')}"

    def _remote_url(self, rel_path: str) -> str:
        """Build the full WebDAV URL for a vault-relative path.

        Args:
            rel_path: Path relative to the vault root (e.g. ``notes/doc.gpg``).

        Returns:
            Fully-qualified WebDAV URL.
        """
        # Strip leading slashes to avoid double-slash in joins.
        clean = rel_path.lstrip("/")
        # vault_path always ends with /
        return self._webdav_root() + self._vault_path + quote(clean, safe="/")

    def _meta_path(self, rel_path: str) -> str:
        """Return the sidecar metadata path for a given file path.

        Args:
            rel_path: Relative path of the primary file.

        Returns:
            Path of the ``.meta.json`` sidecar.
        """
        return rel_path.rstrip("/") + _META_SUFFIX

    def _request(
        self,
        method: str,
        url: str,
        *,
        expected: tuple[int, ...] = (200,),
        **kwargs,
    ) -> requests.Response:
        """Execute an HTTP request and check the status code.

        Args:
            method: HTTP method (GET, PUT, DELETE, PROPFIND, MKCOL).
            url: Full URL.
            expected: Acceptable HTTP status codes.
            **kwargs: Extra arguments forwarded to ``requests.Session.request``.

        Returns:
            The response object.

        Raises:
            NextcloudError: If the response status is not in *expected*.
        """
        resp = self._session.request(
            method, url, timeout=self._timeout, **kwargs
        )
        if resp.status_code not in expected:
            raise NextcloudError(method, url, resp.status_code, resp.text[:256])
        return resp

    def _propfind(self, url: str, depth: int = 1) -> list[dict]:
        """Perform a WebDAV PROPFIND and parse the multistatus response.

        Args:
            url: Collection URL to list.
            depth: PROPFIND Depth header value (0 = self only, 1 = children).

        Returns:
            List of dicts with keys: ``href``, ``name``, ``is_dir``, ``size``,
            ``last_modified``.
        """
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<d:propfind xmlns:d="DAV:">'
            "  <d:prop>"
            "    <d:displayname/>"
            "    <d:getcontentlength/>"
            "    <d:getlastmodified/>"
            "    <d:resourcetype/>"
            "  </d:prop>"
            "</d:propfind>"
        )
        resp = self._request(
            "PROPFIND",
            url,
            expected=(207, 404),
            headers={"Depth": str(depth), "Content-Type": "application/xml"},
            data=body.encode(),
        )
        if resp.status_code == 404:
            return []

        root = ET.fromstring(resp.content)
        entries: list[dict] = []

        for response in root.findall(f"{{{_DAV_NS}}}response"):
            href_el = response.find(f"{{{_DAV_NS}}}href")
            if href_el is None or href_el.text is None:
                continue
            href = href_el.text

            propstat = response.find(f"{{{_DAV_NS}}}propstat")
            if propstat is None:
                continue
            prop = propstat.find(f"{{{_DAV_NS}}}prop")
            if prop is None:
                continue

            rt = prop.find(f"{{{_DAV_NS}}}resourcetype")
            is_dir = rt is not None and rt.find(f"{{{_DAV_NS}}}collection") is not None

            size_el = prop.find(f"{{{_DAV_NS}}}getcontentlength")
            size = int(size_el.text or "0") if size_el is not None and size_el.text else 0

            lm_el = prop.find(f"{{{_DAV_NS}}}getlastmodified")
            last_modified = lm_el.text if lm_el is not None else ""

            name_el = prop.find(f"{{{_DAV_NS}}}displayname")
            name = (name_el.text or "").strip() if name_el is not None else href.split("/")[-1]

            entries.append(
                {
                    "href": href,
                    "name": name,
                    "is_dir": is_dir,
                    "size": size,
                    "last_modified": last_modified,
                }
            )

        return entries

    def _ensure_vault_dir(self) -> None:
        """Create the remote vault directory if it does not already exist."""
        url = self._webdav_root() + self._vault_path
        resp = self._session.request("PROPFIND", url, timeout=self._timeout,
                                     headers={"Depth": "0"})
        if resp.status_code == 404:
            self._request("MKCOL", url, expected=(201, 405))

    # ------------------------------------------------------------------
    # Backend interface implementation
    # ------------------------------------------------------------------

    def put(self, rel_path: str, data: bytes) -> None:
        """Upload bytes to Nextcloud.

        Parent collections are created automatically if they do not exist.
        This is the raw storage method; the ``save`` method adds metadata
        sidecar support on top.

        Args:
            rel_path: Vault-relative path (e.g. ``legal/contract.pdf.gpg``).
            data: Raw bytes to upload.
        """
        self._ensure_vault_dir()
        # Create any intermediate collections.
        parts = rel_path.lstrip("/").split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            parent_url = self._webdav_root() + self._vault_path + quote(parent, safe="/") + "/"
            r = self._session.request("PROPFIND", parent_url, timeout=self._timeout,
                                      headers={"Depth": "0"})
            if r.status_code == 404:
                self._request("MKCOL", parent_url, expected=(201, 405))

        url = self._remote_url(rel_path)
        self._request("PUT", url, expected=(200, 201, 204), data=data)
        logger.debug("PUT %s (%d bytes)", rel_path, len(data))

    def get(self, rel_path: str) -> bytes:
        """Download bytes from Nextcloud.

        Args:
            rel_path: Vault-relative path.

        Returns:
            Raw bytes.

        Raises:
            FileNotFoundError: If the remote path does not exist.
        """
        url = self._remote_url(rel_path)
        resp = self._session.get(url, timeout=self._timeout)
        if resp.status_code == 404:
            raise FileNotFoundError(f"Not found on Nextcloud: {rel_path}")
        if resp.status_code not in (200, 206):
            raise NextcloudError("GET", url, resp.status_code, resp.text[:256])
        logger.debug("GET %s (%d bytes)", rel_path, len(resp.content))
        return resp.content

    def delete(self, rel_path: str) -> None:
        """Delete a file (and its sidecar if present) from Nextcloud.

        Args:
            rel_path: Vault-relative path.
        """
        url = self._remote_url(rel_path)
        self._request("DELETE", url, expected=(200, 204, 404))
        logger.debug("DELETE %s", rel_path)

        # Clean up sidecar silently.
        meta_path = self._meta_path(rel_path)
        meta_url = self._remote_url(meta_path)
        try:
            self._session.delete(meta_url, timeout=self._timeout)
        except Exception:  # noqa: BLE001
            pass

    def list_dir(self, rel_path: str = "") -> list[FileEntry]:
        """List entries in a vault directory on Nextcloud.

        Args:
            rel_path: Vault-relative directory path (empty = vault root).

        Returns:
            List of FileEntry objects.  Sidecar ``.meta.json`` files are
            filtered out so callers see only primary vault files.
        """
        if rel_path:
            url = self._remote_url(rel_path.rstrip("/") + "/")
        else:
            url = self._webdav_root() + self._vault_path

        entries_raw = self._propfind(url, depth=1)
        vault_url_prefix = self._webdav_root() + self._vault_path

        result: list[FileEntry] = []
        for entry in entries_raw:
            name = entry["name"]
            if not name:
                continue
            # Skip the directory itself (self-entry from PROPFIND).
            href = entry["href"].rstrip("/")
            collection_href = (vault_url_prefix + rel_path).rstrip("/")
            if href == collection_href or href == collection_href.rstrip("/"):
                continue
            # Filter out metadata sidecar files.
            if name.endswith(_META_SUFFIX):
                continue
            result.append(
                FileEntry(
                    name=name,
                    path=(rel_path.rstrip("/") + "/" + name).lstrip("/"),
                    is_dir=entry["is_dir"],
                    size=entry["size"],
                )
            )
        return result

    def exists(self, rel_path: str) -> bool:
        """Check if a path exists on Nextcloud.

        Args:
            rel_path: Vault-relative path.

        Returns:
            True if the remote resource exists.
        """
        url = self._remote_url(rel_path)
        resp = self._session.request(
            "PROPFIND", url, timeout=self._timeout, headers={"Depth": "0"}
        )
        return resp.status_code not in (404, 405)

    def mkdir(self, rel_path: str) -> None:
        """Create a remote collection (directory).

        Creates parent collections as needed.

        Args:
            rel_path: Vault-relative directory path.
        """
        self._ensure_vault_dir()
        parts = rel_path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            partial = "/".join(parts[:i])
            url = self._webdav_root() + self._vault_path + quote(partial, safe="/") + "/"
            r = self._session.request("PROPFIND", url, timeout=self._timeout,
                                      headers={"Depth": "0"})
            if r.status_code == 404:
                self._request("MKCOL", url, expected=(201, 405))

    def file_size(self, rel_path: str) -> int:
        """Return the remote file size in bytes.

        Args:
            rel_path: Vault-relative file path.

        Returns:
            File size in bytes, or 0 if not found.
        """
        url = self._remote_url(rel_path)
        entries = self._propfind(url, depth=0)
        if not entries:
            return 0
        return entries[0].get("size", 0)

    # ------------------------------------------------------------------
    # High-level ref API (used by CLI sync commands)
    # ------------------------------------------------------------------

    def save(self, ref_id: str, content: bytes, metadata: dict) -> None:
        """Upload an encrypted ref and its metadata sidecar to Nextcloud.

        Args:
            ref_id: Unique ref identifier (used as the remote filename).
            content: Raw (pre-encrypted) bytes to store.
            metadata: Dict of searchable metadata (title, tags, created, …).
                Stored as a ``.meta.json`` sidecar alongside the content file.
        """
        self.put(ref_id, content)
        meta_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode()
        self.put(self._meta_path(ref_id), meta_bytes)
        logger.info("Saved ref %s with metadata", ref_id)

    def get_ref(self, ref_id: str) -> tuple[bytes, dict]:
        """Download a ref and its metadata from Nextcloud.

        Args:
            ref_id: Unique ref identifier.

        Returns:
            Tuple of ``(content_bytes, metadata_dict)``.  If the sidecar is
            missing the metadata dict will be empty.

        Raises:
            FileNotFoundError: If the primary content file does not exist.
        """
        content = self.get(ref_id)
        metadata: dict = {}
        try:
            meta_bytes = self.get(self._meta_path(ref_id))
            metadata = json.loads(meta_bytes.decode())
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.debug("No/invalid sidecar for %s: %s", ref_id, exc)
        return content, metadata

    def delete_ref(self, ref_id: str) -> None:
        """Remove a ref and its sidecar from Nextcloud.

        Args:
            ref_id: Unique ref identifier.
        """
        self.delete(ref_id)  # delete() already cleans up the sidecar

    def list_refs(self, prefix: str = "") -> list[dict]:
        """List refs stored under the vault path, with metadata.

        Sidecar ``.meta.json`` files are filtered out.  Metadata is loaded
        from the sidecar when available.

        Args:
            prefix: Optional path prefix to filter results (e.g. ``"legal/"``).

        Returns:
            List of dicts, each with at minimum ``ref_id``, ``size``, and any
            sidecar metadata fields (``title``, ``tags``, ``created``, etc.).
        """
        entries = self.list_dir(prefix)
        refs: list[dict] = []
        for entry in entries:
            if entry.is_dir:
                continue
            ref_id = entry.path
            meta: dict = {}
            try:
                meta_bytes = self.get(self._meta_path(ref_id))
                meta = json.loads(meta_bytes.decode())
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            refs.append({"ref_id": ref_id, "size": entry.size, **meta})
        return refs

    def search(self, query: str) -> list[dict]:
        """Search refs by title or tags.

        Performs a full list and filters by checking whether *query* appears
        (case-insensitive) in the ref's ``title`` or ``tags`` metadata fields.
        For large vaults consider maintaining a local index.

        Args:
            query: Search string (matched against title and tags).

        Returns:
            Filtered list of ref dicts (same shape as ``list_refs``).
        """
        q = query.lower()
        results: list[dict] = []
        for ref in self.list_refs():
            title = str(ref.get("title", "")).lower()
            tags = " ".join(str(t) for t in ref.get("tags", [])).lower()
            if q in title or q in tags:
                results.append(ref)
        return results

    def sync_status(self) -> dict:
        """Report basic sync state between local cache and Nextcloud.

        Currently reports the remote ref count and connection reachability.
        A future implementation can compare against a local manifest to report
        added/modified/deleted files.

        Returns:
            Dict with ``reachable``, ``remote_ref_count``, and ``status``
            (``"ok"`` or ``"unreachable"``).
        """
        try:
            refs = self.list_refs()
            return {
                "reachable": True,
                "remote_ref_count": len(refs),
                "status": "ok",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("sync_status failed: %s", exc)
            return {
                "reachable": False,
                "remote_ref_count": 0,
                "status": "unreachable",
                "error": str(exc),
            }

    def health(self) -> dict:
        """Test the Nextcloud connection and report its health.

        Returns:
            Dict with ``ok`` (bool), ``url``, ``user``, and optionally
            ``error`` on failure.
        """
        try:
            url = self._webdav_root() + "/"
            resp = self._session.request(
                "PROPFIND", url, timeout=self._timeout, headers={"Depth": "0"}
            )
            ok = resp.status_code in (200, 207, 301)
            return {
                "ok": ok,
                "url": self._url,
                "user": self._username,
                "http_status": resp.status_code,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "url": self._url,
                "user": self._username,
                "error": str(exc),
            }
