# Phase 1 — Local Backend, Crypto, FUSE, CLI

**Status: COMPLETE**

This document describes what was built in Phase 1 for reference by agents working on later phases. All code is in `skref/src/skref/`.

---

## What Was Built

### 1. Package Structure

| File | Purpose |
|------|---------|
| `pyproject.toml` | Package metadata, optional deps `[fuse]`, `[s3]`, `[nextcloud]`, `[dev]` |
| `src/skref/__init__.py` | Package root, `__version__` |
| `src/skref/models.py` | Pydantic models: `BackendType`, `VaultConfig`, `SkrefConfig` |
| `src/skref/config.py` | YAML config load/save at `~/.skcapstone/vaults.yaml` |
| `src/skref/crypto.py` | GPG encrypt/decrypt bytes and files, key auto-detection |
| `src/skref/vault.py` | `Vault` class combining backend + crypto |
| `src/skref/fuse_mount.py` | pyfuse3 FUSE filesystem with decrypt-on-read / encrypt-on-write |
| `src/skref/cli.py` | Click CLI: `init`, `ls`, `put`, `open`, `mount` |
| `src/skref/backends/base.py` | Abstract `Backend` interface + `FileEntry` dataclass |
| `src/skref/backends/local.py` | Local filesystem backend with path traversal protection |

### 2. Backend Interface

All backends must implement `Backend` from `backends/base.py`:

```python
class Backend(ABC):
    def put(self, rel_path: str, data: bytes) -> None: ...
    def get(self, rel_path: str) -> bytes: ...
    def delete(self, rel_path: str) -> None: ...
    def list_dir(self, rel_path: str = "") -> list[FileEntry]: ...
    def exists(self, rel_path: str) -> bool: ...
    def mkdir(self, rel_path: str) -> None: ...
    def file_size(self, rel_path: str) -> int: ...
```

New backends (Phase 2, 3) must implement this exact interface. The `Vault` class in `vault.py` wraps any backend transparently.

### 3. Crypto

- Uses system `gpg` binary via `subprocess`
- Auto-detects key from `~/.skcapstone/identity/identity.json` or GPG keyring
- Multi-recipient encryption for shared vaults (`peers` in config)
- Files stored as binary GPG (not armored) with `.gpg` suffix

### 4. FUSE

- pyfuse3-based (Linux/macOS)
- Inode table maps inode -> vault-relative path
- File content buffered in memory per `open()` call
- Dirty buffers flushed (encrypted + written) on `release()`
- Supports: `getattr`, `lookup`, `readdir`, `open`, `read`, `write`, `release`, `create`, `mkdir`, `unlink`, `setattr`

### 5. Tests

28 tests covering:
- `test_backend_local.py` — put/get/list/delete, path traversal, hidden files
- `test_config.py` — YAML round-trip, corrupt file recovery, defaults
- `test_crypto.py` — filename suffix helpers
- `test_vault.py` — unencrypted vault operations, encrypted vault key requirement

---

## Conventions for Later Phases

1. **One backend = one file** in `src/skref/backends/`. Name it `<backend_type>.py`.
2. **Register new backends** in `backends/__init__.py` and in `cli.py`'s `_resolve_vault()`.
3. **Add the backend type** to `BackendType` enum in `models.py`.
4. **Tests mirror source**: `test_backend_<type>.py` in `tests/`.
5. **Optional deps** go in `pyproject.toml` under `[project.optional-dependencies]`.
6. **Docstrings**: Google style, type hints on all functions.
7. **Line length**: 99 chars (black + ruff config in pyproject.toml).
