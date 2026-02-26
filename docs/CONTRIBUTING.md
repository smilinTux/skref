# Contributing to SKRef

Guide for developers and AI agents implementing new phases.

---

## Project Structure

```
skref/
├── pyproject.toml              ← package config, deps, entry points
├── README.md                   ← user-facing overview
├── docs/
│   ├── ARCHITECTURE.md         ← system design, Mermaid diagrams
│   ├── USAGE.md                ← end-user guide
│   ├── CONTRIBUTING.md         ← you are here
│   └── phases/
│       ├── PHASE-1.md          ← local backend (done)
│       ├── PHASE-2.md          ← Nextcloud/WebDAV backend
│       ├── PHASE-3.md          ← S3/MinIO backend
│       ├── PHASE-4.md          ← WebDAV proxy server
│       ├── PHASE-5.md          ← Windows support
│       └── PHASE-6.md          ← sharing
├── src/skref/
│   ├── __init__.py
│   ├── models.py               ← Pydantic data models
│   ├── config.py               ← YAML config load/save
│   ├── crypto.py               ← GPG encrypt/decrypt
│   ├── vault.py                ← Vault (backend + crypto)
│   ├── fuse_mount.py           ← FUSE filesystem (Linux/macOS)
│   ├── cli.py                  ← Click CLI
│   └── backends/
│       ├── __init__.py
│       ├── base.py             ← Abstract Backend interface
│       └── local.py            ← Local filesystem backend
└── tests/
    ├── __init__.py
    ├── test_backend_local.py
    ├── test_config.py
    ├── test_crypto.py
    └── test_vault.py
```

---

## How to Add a New Backend

This is the most common contribution pattern. Follow these steps exactly.

### Step 1: Create the backend file

Create `src/skref/backends/<type>.py`:

```python
"""
<Type> storage backend for SKRef vaults.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import Backend, FileEntry

logger = logging.getLogger("skref.backends.<type>")


class <Type>Backend(Backend):
    """Stores vault files on <service>.

    Args:
        <constructor args>
    """

    def __init__(self, ...) -> None:
        ...

    def put(self, rel_path: str, data: bytes) -> None:
        """Write bytes to <service>.

        Args:
            rel_path: Relative path within the vault.
            data: Raw bytes (already encrypted by Vault layer).
        """
        ...

    def get(self, rel_path: str) -> bytes:
        """Read bytes from <service>.

        Args:
            rel_path: Relative path.

        Returns:
            Raw bytes.

        Raises:
            FileNotFoundError: If the path does not exist.
        """
        ...

    def delete(self, rel_path: str) -> None:
        ...

    def list_dir(self, rel_path: str = "") -> list[FileEntry]:
        ...

    def exists(self, rel_path: str) -> bool:
        ...

    def mkdir(self, rel_path: str) -> None:
        ...

    def file_size(self, rel_path: str) -> int:
        ...
```

### Step 2: Register the backend

In `backends/__init__.py`:

```python
from .<type> import <Type>Backend
```

### Step 3: Add the backend type (if needed)

Check `models.py` — the `BackendType` enum may already have your type. If not:

```python
class BackendType(str, Enum):
    LOCAL = "local"
    NEXTCLOUD = "nextcloud"
    S3 = "s3"
    GDRIVE = "gdrive"
    NEW_TYPE = "new_type"    # add here
```

### Step 4: Wire into CLI

In `cli.py`, update `_resolve_vault()`:

```python
elif vcfg.backend == BackendType.NEW_TYPE:
    from .backends.new_type import NewTypeBackend
    backend = NewTypeBackend(...)
```

### Step 5: Add optional dependencies

In `pyproject.toml`:

```toml
[project.optional-dependencies]
new_type = ["some-library>=1.0"]
```

Add to `all` group as well.

### Step 6: Write tests

Create `tests/test_backend_<type>.py`. Use mocks — tests must run without the actual service.

**Minimum required tests:**

| Test | Purpose |
|------|---------|
| `test_put` | Correct service call made |
| `test_get_returns_bytes` | Bytes returned correctly |
| `test_get_missing_raises` | `FileNotFoundError` on missing file |
| `test_delete` | Correct service call |
| `test_list_dir` | Returns correct `FileEntry` list |
| `test_exists_true` | Returns `True` for existing file |
| `test_exists_false` | Returns `False` for missing file |
| `test_mkdir` | Directory creation works |
| `test_file_size` | Correct byte count returned |
| `test_error_handling` | Service errors → appropriate Python exceptions |
| `test_credentials` | Auth failure → helpful error message |

---

## Code Standards

### Python

- **Version:** 3.10+
- **Style:** PEP 8, enforced by `black` (line-length 99) and `ruff`
- **Type hints:** All function signatures
- **Docstrings:** Google style on every public function

```python
def example_function(path: str, data: bytes) -> int:
    """Brief summary of what this does.

    Args:
        path: Description of path parameter.
        data: Description of data parameter.

    Returns:
        Number of bytes written.

    Raises:
        FileNotFoundError: If path does not exist.
    """
```

### Imports

```python
# stdlib first
from __future__ import annotations
import logging
from pathlib import Path

# third-party
import click
from pydantic import BaseModel

# local
from .backends.base import Backend, FileEntry
from .crypto import encrypt_bytes
```

### Error Handling

Map service-specific errors to standard Python exceptions:

| Service error | Python exception | When |
|---------------|-----------------|------|
| File/object not found | `FileNotFoundError` | GET/HEAD on missing resource |
| Auth failure | `PermissionError` | 401/403 from service |
| Service unreachable | `ConnectionError` | Network timeout/refused |
| Everything else | `RuntimeError` | Include service error details |

Always include helpful messages:

```python
# Bad
raise RuntimeError("401")

# Good
raise PermissionError(
    "Nextcloud authentication failed. "
    "Create an app password: Settings → Security → 'Create new app password'"
)
```

### File Length

No file should exceed 500 lines. Split into modules if approaching this limit.

---

## Testing

### Run tests

```bash
cd skref/
python3 -m pytest tests/ -v
```

### Test principles

1. **No real services** — mock all external calls (HTTP, S3, etc.)
2. **Use `tmp_path` fixture** for local filesystem tests
3. **Test the contract** — every `Backend` method must have at least one test
4. **Edge cases** — empty dirs, nested paths, large files, unicode filenames
5. **Error paths** — missing files, auth failures, network errors

### Test naming

```python
class TestNextcloudBackend:
    def test_put_sends_put_request(self) -> None: ...
    def test_get_404_raises_file_not_found(self) -> None: ...
    def test_list_dir_parses_xml_response(self) -> None: ...
```

---

## Adding CLI Commands

All CLI commands use [Click](https://click.palletsprojects.com/). Add to `cli.py`:

```python
@main.command()
@click.argument("required_arg")
@click.option("--vault", "vault_name", default=None, help="Vault name.")
@click.option("--flag/--no-flag", default=True, help="Description.")
def new_command(required_arg: str, vault_name: str | None, flag: bool):
    """One-line description shown in --help.

    Longer description for the command.
    """
    vault = _resolve_vault(vault_name)
    # ... implementation
```

Use `rich` for output formatting (panels, tables, progress bars). The `console` object is already available in `cli.py`.

---

## Phase Implementation Checklist

When implementing a phase, follow this checklist:

- [ ] Read the phase spec in `docs/phases/PHASE-N.md` completely
- [ ] Read `docs/ARCHITECTURE.md` for overall system context
- [ ] Read existing code: `backends/base.py`, `vault.py`, `cli.py`, `models.py`
- [ ] Implement the deliverables listed in the phase spec
- [ ] Write all tests listed in the phase spec
- [ ] Run `python3 -m pytest tests/ -v` — all tests must pass (including existing)
- [ ] Run `black src/ tests/` — formatting clean
- [ ] Run `ruff check src/ tests/` — no lint errors
- [ ] Update `backends/__init__.py` with new exports
- [ ] Update `cli.py` `_resolve_vault()` for new backend types
- [ ] Update `pyproject.toml` with new optional dependencies
- [ ] Verify the acceptance criteria in the phase spec
- [ ] Do not break existing Phase 1 functionality (run all tests)

---

## Git Workflow

```bash
# Create a feature branch
git checkout -b phase-2/nextcloud-backend

# Make changes
# ... implement ...

# Run tests
python3 -m pytest tests/ -v

# Format
black src/ tests/
ruff check src/ tests/ --fix

# Commit
git add .
git commit -m "feat(skref): implement Nextcloud/WebDAV backend (Phase 2)"

# Push
git push -u origin phase-2/nextcloud-backend
```

Commit message format: `feat(skref): <description>` for features, `fix(skref): <description>` for fixes, `docs(skref): <description>` for docs.

---

## Questions?

If you're an AI agent and something in the phase spec is ambiguous:
1. Check `docs/ARCHITECTURE.md` for system-level context
2. Check `docs/phases/PHASE-1.md` for conventions established in Phase 1
3. Look at existing code in `src/skref/` for patterns
4. If still unclear, implement the simpler/safer option and document your decision
