"""WebDAV proxy server for SKRef vaults.

Exposes an encrypted vault over WebDAV so any device with a WebDAV
client (phone, tablet, remote desktop) can access files without FUSE
or GPG installed on the client side. Files are decrypted on read and
encrypted on write — all in memory, nothing plaintext on disk.

Authentication:
    - HTTP Basic Auth (username + password via --user/--password or
      SKREF_WEBDAV_USER / SKREF_WEBDAV_PASS env vars)
    - Bearer token (CapAuth API token via --token or SKREF_WEBDAV_TOKEN)

TLS:
    - Binding to a non-localhost address requires tls_cert + tls_key.
    - Localhost binding allows plain HTTP (development / Tailscale mode).
    - Use ``skref serve --tailscale`` for automatic Let's Encrypt TLS via
      Tailscale Funnel.

Supported WebDAV methods:
    OPTIONS, PROPFIND, GET, HEAD, PUT, DELETE, MKCOL, MOVE, COPY
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import mimetypes
import os
import ssl
from typing import Optional
from urllib.parse import unquote, urlparse

from aiohttp import web

from ..vault import Vault

logger = logging.getLogger("skref.webdav")

_WEBDAV_ALLOW = "OPTIONS, GET, HEAD, PUT, DELETE, MKCOL, PROPFIND, MOVE, COPY"
_DAV_HEADER = "1, 2"


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _dav_response_xml(href: str, is_dir: bool, name: str, size: int = 0) -> str:
    """Build a single ``DAV:response`` XML block for a PROPFIND reply."""
    if is_dir:
        resourcetype = "<D:resourcetype><D:collection/></D:resourcetype>"
        extra = ""
    else:
        resourcetype = "<D:resourcetype/>"
        content_type, _ = mimetypes.guess_type(name)
        ct = content_type or "application/octet-stream"
        extra = (
            f"\n        <D:getcontentlength>{size}</D:getcontentlength>"
            f"\n        <D:getcontenttype>{ct}</D:getcontenttype>"
        )

    return (
        "  <D:response>\n"
        f"    <D:href>{href}</D:href>\n"
        "    <D:propstat>\n"
        "      <D:prop>\n"
        f"        {resourcetype}\n"
        f"        <D:displayname>{name}</D:displayname>{extra}\n"
        "      </D:prop>\n"
        "      <D:status>HTTP/1.1 200 OK</D:status>\n"
        "    </D:propstat>\n"
        "  </D:response>"
    )


def _build_multistatus(responses: list[str]) -> str:
    """Wrap ``DAV:response`` elements in a ``DAV:multistatus`` envelope."""
    inner = "\n".join(responses)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<D:multistatus xmlns:D="DAV:">\n'
        f"{inner}\n"
        "</D:multistatus>"
    )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _parse_dest_path(dest_header: str) -> str:
    """Extract a vault-relative path from a WebDAV ``Destination`` header.

    The header is an absolute URL (``http://host/path``) or an absolute
    path (``/path``). We strip the leading slash and URL-decode it.
    """
    parsed = urlparse(dest_header)
    path = parsed.path if parsed.scheme else dest_header
    return unquote(path).strip("/")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _safe_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    return hmac.compare_digest(a.encode(), b.encode())


# ---------------------------------------------------------------------------
# WebDAVProxy
# ---------------------------------------------------------------------------


class WebDAVProxy:
    """Async WebDAV server that exposes a :class:`~skref.vault.Vault` over HTTP/S.

    Args:
        vault: The vault to expose.
        host: Bind address (default ``127.0.0.1``).
        port: Port number (default ``8443``).
        username: Basic-auth username.  Falls back to ``SKREF_WEBDAV_USER``.
        password: Basic-auth password.  Falls back to ``SKREF_WEBDAV_PASS``.
        token: CapAuth Bearer token.  Falls back to ``SKREF_WEBDAV_TOKEN``.
        tls_cert: Path to TLS certificate (required for non-localhost binding).
        tls_key: Path to TLS private key.

    Raises:
        ValueError: If binding to a non-localhost address without TLS cert+key.
    """

    def __init__(
        self,
        vault: Vault,
        host: str = "127.0.0.1",
        port: int = 8443,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
        tls_cert: Optional[str] = None,
        tls_key: Optional[str] = None,
    ) -> None:
        self.vault = vault
        self.host = host
        self.port = port
        self.username = username or os.environ.get("SKREF_WEBDAV_USER")
        self.password = password or os.environ.get("SKREF_WEBDAV_PASS")
        self.token = token or os.environ.get("SKREF_WEBDAV_TOKEN")
        self.tls_cert = tls_cert
        self.tls_key = tls_key

        _localhost = {"127.0.0.1", "::1", "localhost"}
        if host not in _localhost and not (tls_cert and tls_key):
            raise ValueError(
                f"TLS certificate required when binding to '{host}' (non-localhost). "
                "Pass tls_cert= and tls_key=, or use --tailscale for automatic TLS "
                "via Tailscale Funnel."
            )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _check_auth(self, request: web.Request) -> bool:
        """Return ``True`` if the request carries valid credentials."""
        # No credentials configured → allow all (no-auth dev mode)
        if not self.username and not self.password and not self.token:
            return True

        auth = request.headers.get("Authorization", "")
        if not auth:
            return False

        # CapAuth Bearer token
        if auth.startswith("Bearer ") and self.token:
            return _safe_compare(auth[7:].strip(), self.token)

        # HTTP Basic Auth
        if auth.startswith("Basic ") and self.username and self.password:
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
                user, _, pwd = decoded.partition(":")
                return _safe_compare(user, self.username) and _safe_compare(pwd, self.password)
            except Exception:
                return False

        return False

    def _require_auth(self) -> web.Response:
        return web.Response(
            status=401,
            text="Unauthorized",
            headers={
                "WWW-Authenticate": 'Basic realm="SKRef Vault", charset="UTF-8"',
                "DAV": _DAV_HEADER,
            },
        )

    # ------------------------------------------------------------------
    # App builder
    # ------------------------------------------------------------------

    def _build_app(self) -> web.Application:
        """Create the :class:`aiohttp.web.Application` with routes and auth."""
        app = web.Application(middlewares=[self._auth_middleware])
        app.router.add_route("OPTIONS",  "/{path_info:.*}", self._handle_options)
        app.router.add_route("PROPFIND", "/{path_info:.*}", self._handle_propfind)
        app.router.add_route("GET",      "/{path_info:.*}", self._handle_get)
        app.router.add_route("HEAD",     "/{path_info:.*}", self._handle_head)
        app.router.add_route("PUT",      "/{path_info:.*}", self._handle_put)
        app.router.add_route("DELETE",   "/{path_info:.*}", self._handle_delete)
        app.router.add_route("MKCOL",    "/{path_info:.*}", self._handle_mkcol)
        app.router.add_route("MOVE",     "/{path_info:.*}", self._handle_move)
        app.router.add_route("COPY",     "/{path_info:.*}", self._handle_copy)
        return app

    @web.middleware
    async def _auth_middleware(
        self, request: web.Request, handler
    ) -> web.Response:
        if not self._check_auth(request):
            return self._require_auth()
        return await handler(request)

    # ------------------------------------------------------------------
    # Path helper
    # ------------------------------------------------------------------

    def _rel_path(self, request: web.Request) -> str:
        """Extract vault-relative path from request (URL-decoded, no leading /)."""
        return unquote(request.match_info.get("path_info", "")).strip("/")

    # ------------------------------------------------------------------
    # WebDAV method handlers
    # ------------------------------------------------------------------

    async def _handle_options(self, request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={
                "DAV": _DAV_HEADER,
                "Allow": _WEBDAV_ALLOW,
                "MS-Author-Via": "DAV",
            },
        )

    async def _handle_propfind(self, request: web.Request) -> web.Response:
        rel = self._rel_path(request)
        depth = request.headers.get("Depth", "1")

        # Determine whether this is a collection or a file
        if rel == "":
            is_dir = True
        else:
            is_dir = self.vault.is_dir(rel)
            if not is_dir and not self.vault.exists(rel):
                return web.Response(status=404, text="Not Found")

        # Build the self-response entry
        if rel == "":
            href = "/"
            name = ""
        elif is_dir:
            href = "/" + rel + "/"
            name = rel.split("/")[-1]
        else:
            href = "/" + rel
            name = rel.split("/")[-1]

        size = 0 if is_dir else self.vault.file_size(rel)
        responses = [_dav_response_xml(href, is_dir, name, size)]

        # Children for Depth:1 on a collection
        if depth != "0" and is_dir:
            for entry in self.vault.list_dir(rel):
                child_rel = (rel + "/" + entry.name).lstrip("/")
                child_href = "/" + child_rel + ("/" if entry.is_dir else "")
                child_size = 0 if entry.is_dir else entry.size
                responses.append(
                    _dav_response_xml(child_href, entry.is_dir, entry.name, child_size)
                )

        return web.Response(
            status=207,
            text=_build_multistatus(responses),
            content_type="application/xml",
            charset="utf-8",
            headers={"DAV": _DAV_HEADER},
        )

    async def _handle_get(self, request: web.Request) -> web.Response:
        rel = self._rel_path(request)
        if not rel:
            return web.Response(
                status=200,
                text="SKRef WebDAV Vault — use a WebDAV client to browse.",
                headers={"DAV": _DAV_HEADER},
            )
        try:
            data = self.vault.read(rel)
        except FileNotFoundError:
            return web.Response(status=404, text="Not Found")

        content_type, _ = mimetypes.guess_type(rel)
        return web.Response(
            status=200,
            body=data,
            content_type=content_type or "application/octet-stream",
            headers={"Content-Length": str(len(data)), "DAV": _DAV_HEADER},
        )

    async def _handle_head(self, request: web.Request) -> web.Response:
        rel = self._rel_path(request)
        if not rel:
            return web.Response(status=200, headers={"DAV": _DAV_HEADER})
        if not self.vault.exists(rel):
            return web.Response(status=404)
        size = self.vault.file_size(rel)
        content_type, _ = mimetypes.guess_type(rel)
        return web.Response(
            status=200,
            headers={
                "Content-Length": str(size),
                "Content-Type": content_type or "application/octet-stream",
                "DAV": _DAV_HEADER,
            },
        )

    async def _handle_put(self, request: web.Request) -> web.Response:
        rel = self._rel_path(request)
        if not rel:
            return web.Response(status=405, text="Cannot PUT to vault root")
        data = await request.read()
        existed = self.vault.exists(rel)
        try:
            self.vault.write(rel, data)
        except Exception as exc:
            logger.error("PUT %s failed: %s", rel, exc)
            return web.Response(status=500, text=str(exc))
        return web.Response(status=201 if not existed else 204)

    async def _handle_delete(self, request: web.Request) -> web.Response:
        rel = self._rel_path(request)
        if not rel:
            return web.Response(status=405, text="Cannot DELETE vault root")
        if not self.vault.exists(rel):
            return web.Response(status=404, text="Not Found")
        try:
            self.vault.delete(rel)
        except Exception as exc:
            logger.error("DELETE %s failed: %s", rel, exc)
            return web.Response(status=500, text=str(exc))
        return web.Response(status=204)

    async def _handle_mkcol(self, request: web.Request) -> web.Response:
        rel = self._rel_path(request)
        if not rel:
            return web.Response(status=405, text="Cannot MKCOL vault root")
        try:
            self.vault.mkdir(rel)
        except Exception as exc:
            logger.error("MKCOL %s failed: %s", rel, exc)
            return web.Response(status=500, text=str(exc))
        return web.Response(status=201)

    async def _handle_move(self, request: web.Request) -> web.Response:
        src = self._rel_path(request)
        dest_header = request.headers.get("Destination", "")
        if not dest_header:
            return web.Response(status=400, text="Destination header required")
        dest = _parse_dest_path(dest_header)
        if not self.vault.exists(src):
            return web.Response(status=404, text="Source not found")
        try:
            data = self.vault.read(src)
            self.vault.write(dest, data)
            self.vault.delete(src)
        except Exception as exc:
            logger.error("MOVE %s -> %s failed: %s", src, dest, exc)
            return web.Response(status=500, text=str(exc))
        return web.Response(status=201)

    async def _handle_copy(self, request: web.Request) -> web.Response:
        src = self._rel_path(request)
        dest_header = request.headers.get("Destination", "")
        if not dest_header:
            return web.Response(status=400, text="Destination header required")
        dest = _parse_dest_path(dest_header)
        if not self.vault.exists(src):
            return web.Response(status=404, text="Source not found")
        try:
            data = self.vault.read(src)
            self.vault.write(dest, data)
        except Exception as exc:
            logger.error("COPY %s -> %s failed: %s", src, dest, exc)
            return web.Response(status=500, text=str(exc))
        return web.Response(status=201)

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the WebDAV server and run until cancelled."""
        app = self._build_app()
        runner = web.AppRunner(app)
        await runner.setup()

        ssl_ctx: Optional[ssl.SSLContext] = None
        if self.tls_cert and self.tls_key:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(self.tls_cert, self.tls_key)

        site = web.TCPSite(runner, self.host, self.port, ssl_context=ssl_ctx)
        await site.start()

        scheme = "https" if ssl_ctx else "http"
        logger.info("SKRef WebDAV proxy on %s://%s:%d/", scheme, self.host, self.port)

        try:
            await asyncio.Event().wait()  # run forever until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()

    def run(self) -> None:
        """Blocking entry point for CLI use."""
        asyncio.run(self.start())
