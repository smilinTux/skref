"""Tests for the SKRef WebDAV proxy server.

Uses aiohttp's TestClient / TestServer so no real network is required.
Each test builds a fresh in-memory vault (unencrypted, backed by a
tmp_path directory) and a fresh WebDAVProxy instance.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from skref.backends.local import LocalBackend
from skref.models import VaultConfig
from skref.vault import Vault
from skref.webdav.proxy import WebDAVProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vault(tmp_path: Path) -> Vault:
    cfg = VaultConfig(name="test", encrypted=False, path=str(tmp_path / "vault"))
    return Vault(config=cfg, backend=LocalBackend(Path(cfg.path)))


def _auth_headers(user: str = "user", password: str = "secret") -> dict:
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def _run(coro):
    """Run a coroutine in a fresh event loop (synchronous wrapper)."""
    return asyncio.run(coro)


def _make_client(proxy: WebDAVProxy):
    """Return the aiohttp TestClient / TestServer for a proxy."""
    from aiohttp.test_utils import TestClient, TestServer
    return TestClient(TestServer(proxy._build_app()))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_auth_required(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get("/")
                assert resp.status == 401

        _run(_go())

    def test_auth_wrong_password(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get("/", headers=_auth_headers(password="wrong"))
                assert resp.status == 401

        _run(_go())

    def test_auth_success(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get("/", headers=_auth_headers())
                assert resp.status == 200

        _run(_go())

    def test_bearer_token_auth(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, token="capauth-secret-token")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get(
                    "/", headers={"Authorization": "Bearer capauth-secret-token"}
                )
                assert resp.status == 200

        _run(_go())

    def test_bearer_token_wrong(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, token="capauth-secret-token")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get(
                    "/", headers={"Authorization": "Bearer wrong-token"}
                )
                assert resp.status == 401

        _run(_go())


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


class TestGet:
    def test_get_decrypts_and_returns(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("hello.txt", b"hello world")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get("/hello.txt", headers=_auth_headers())
                assert resp.status == 200
                assert await resp.read() == b"hello world"

        _run(_go())

    def test_get_404(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get("/missing.txt", headers=_auth_headers())
                assert resp.status == 404

        _run(_go())

    def test_get_nested(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("notes/deep.md", b"nested content")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.get("/notes/deep.md", headers=_auth_headers())
                assert resp.status == 200
                assert await resp.read() == b"nested content"

        _run(_go())


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


class TestPut:
    def test_put_encrypts_and_stores(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.put(
                    "/notes/todo.md", data=b"buy milk", headers=_auth_headers()
                )
                assert resp.status == 201
            assert vault.read("notes/todo.md") == b"buy milk"

        _run(_go())

    def test_put_overwrite_returns_204(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("existing.txt", b"old")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.put(
                    "/existing.txt", data=b"new", headers=_auth_headers()
                )
                assert resp.status == 204
            assert vault.read("existing.txt") == b"new"

        _run(_go())


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_removes(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("gone.txt", b"bye")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.delete("/gone.txt", headers=_auth_headers())
                assert resp.status == 204
            assert not vault.exists("gone.txt")

        _run(_go())

    def test_delete_404(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.delete("/nope.txt", headers=_auth_headers())
                assert resp.status == 404

        _run(_go())


# ---------------------------------------------------------------------------
# MKCOL
# ---------------------------------------------------------------------------


class TestMkcol:
    def test_mkcol_creates_dir(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request("MKCOL", "/newdir", headers=_auth_headers())
                assert resp.status == 201
            assert vault.is_dir("newdir")

        _run(_go())


# ---------------------------------------------------------------------------
# PROPFIND
# ---------------------------------------------------------------------------


class TestPropfind:
    def test_propfind_root(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("a.txt", b"a")
        vault.write("b.txt", b"b")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "PROPFIND", "/",
                    headers={**_auth_headers(), "Depth": "1"},
                )
                assert resp.status == 207
                text = await resp.text()
                assert "D:multistatus" in text
                assert "a.txt" in text
                assert "b.txt" in text

        _run(_go())

    def test_propfind_subdir(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.mkdir("legal")
        vault.write("legal/contract.pdf", b"pdf content")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "PROPFIND", "/legal/",
                    headers={**_auth_headers(), "Depth": "1"},
                )
                assert resp.status == 207
                text = await resp.text()
                assert "contract.pdf" in text

        _run(_go())

    def test_propfind_depth_0(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("only-me.txt", b"solo")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "PROPFIND", "/only-me.txt",
                    headers={**_auth_headers(), "Depth": "0"},
                )
                assert resp.status == 207
                text = await resp.text()
                assert "only-me.txt" in text

        _run(_go())

    def test_propfind_collection_resourcetype(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.mkdir("mydir")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "PROPFIND", "/",
                    headers={**_auth_headers(), "Depth": "1"},
                )
                text = await resp.text()
                assert "D:collection" in text

        _run(_go())

    def test_propfind_404(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "PROPFIND", "/ghost/",
                    headers={**_auth_headers(), "Depth": "1"},
                )
                assert resp.status == 404

        _run(_go())


# ---------------------------------------------------------------------------
# HEAD
# ---------------------------------------------------------------------------


class TestHead:
    def test_head_exists(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("file.txt", b"twelve bytes")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.head("/file.txt", headers=_auth_headers())
                assert resp.status == 200
                assert resp.headers.get("Content-Length") == "12"

        _run(_go())

    def test_head_missing(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.head("/nope.txt", headers=_auth_headers())
                assert resp.status == 404

        _run(_go())


# ---------------------------------------------------------------------------
# OPTIONS
# ---------------------------------------------------------------------------


class TestOptions:
    def test_options(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.options("/", headers=_auth_headers())
                assert resp.status == 200
                allow = resp.headers.get("Allow", "")
                assert "PROPFIND" in allow
                assert "GET" in allow
                assert "PUT" in allow
                assert "DELETE" in allow
                assert "MKCOL" in allow

        _run(_go())


# ---------------------------------------------------------------------------
# MOVE & COPY
# ---------------------------------------------------------------------------


class TestMove:
    def test_move_renames_file(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("original.txt", b"data")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "MOVE", "/original.txt",
                    headers={**_auth_headers(), "Destination": "/renamed.txt"},
                )
                assert resp.status == 201
            assert vault.read("renamed.txt") == b"data"
            assert not vault.exists("original.txt")

        _run(_go())

    def test_move_404(self, tmp_path):
        vault = _make_vault(tmp_path)
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "MOVE", "/ghost.txt",
                    headers={**_auth_headers(), "Destination": "/dest.txt"},
                )
                assert resp.status == 404

        _run(_go())


class TestCopy:
    def test_copy_duplicates_file(self, tmp_path):
        vault = _make_vault(tmp_path)
        vault.write("source.txt", b"original")
        proxy = WebDAVProxy(vault=vault, username="user", password="secret")

        async def _go():
            async with _make_client(proxy) as client:
                resp = await client.request(
                    "COPY", "/source.txt",
                    headers={**_auth_headers(), "Destination": "/copy.txt"},
                )
                assert resp.status == 201
            assert vault.read("source.txt") == b"original"
            assert vault.read("copy.txt") == b"original"

        _run(_go())


# ---------------------------------------------------------------------------
# TLS enforcement
# ---------------------------------------------------------------------------


class TestTlsEnforcement:
    def test_tls_required_on_non_localhost(self, tmp_path):
        vault = _make_vault(tmp_path)
        with pytest.raises(ValueError, match="TLS"):
            WebDAVProxy(vault=vault, host="0.0.0.0")

    def test_tls_required_on_public_ip(self, tmp_path):
        vault = _make_vault(tmp_path)
        with pytest.raises(ValueError, match="TLS"):
            WebDAVProxy(vault=vault, host="192.168.1.10")

    def test_localhost_allows_no_tls(self, tmp_path):
        vault = _make_vault(tmp_path)
        # Should not raise
        proxy = WebDAVProxy(vault=vault, host="127.0.0.1")
        assert proxy.host == "127.0.0.1"

    def test_tls_cert_and_key_allows_public_bind(self, tmp_path):
        vault = _make_vault(tmp_path)
        # cert/key don't need to be valid files for construction — only for startup
        proxy = WebDAVProxy(
            vault=vault,
            host="0.0.0.0",
            tls_cert="/tmp/cert.pem",
            tls_key="/tmp/key.pem",
        )
        assert proxy.host == "0.0.0.0"
