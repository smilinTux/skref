"""
GPG encryption/decryption for vault files.

Uses the CapAuth PGP key from ~/.skcapstone/identity/ by default.
Supports encrypting to multiple recipients (peer keys) so shared
vaults work without re-encrypting per peer.

All operations work on bytes — the caller handles file I/O.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("skref.crypto")

ENCRYPTED_SUFFIX = ".gpg"


def detect_key(identity_home: Optional[Path] = None) -> Optional[str]:
    """Auto-detect the user's GPG fingerprint from CapAuth identity.

    Args:
        identity_home: Path to identity dir (default ~/.skcapstone/identity).

    Returns:
        40-char GPG fingerprint, or None.
    """
    home = (identity_home or Path("~/.skcapstone/identity")).expanduser()
    identity_file = home / "identity.json"
    if identity_file.exists():
        try:
            data = json.loads(identity_file.read_text())
            fp = data.get("fingerprint")
            if fp and len(fp) >= 16:
                return fp
        except (json.JSONDecodeError, OSError):
            pass

    return _detect_from_keyring()


def _detect_from_keyring() -> Optional[str]:
    """Fall back to gpg keyring for fingerprint detection."""
    if not shutil.which("gpg"):
        return None
    try:
        result = subprocess.run(
            ["gpg", "--list-secret-keys", "--keyid-format", "long", "--with-colons"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.startswith("fpr:"):
                return line.split(":")[9]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return None


def encrypt_bytes(
    plaintext: bytes,
    recipient: str,
    extra_recipients: Optional[list[str]] = None,
) -> bytes:
    """GPG-encrypt bytes to one or more recipients.

    Args:
        plaintext: Data to encrypt.
        recipient: Primary GPG fingerprint.
        extra_recipients: Additional peer fingerprints for shared vaults.

    Returns:
        GPG-encrypted ciphertext (binary, not armored).

    Raises:
        RuntimeError: If GPG is not installed or encryption fails.
    """
    if not shutil.which("gpg"):
        raise RuntimeError("gpg not found on PATH")

    all_recipients = [recipient]
    if extra_recipients:
        all_recipients.extend(r for r in extra_recipients if r and r != recipient)

    recipient_args: list[str] = []
    for r in all_recipients:
        recipient_args += ["--recipient", r]

    result = subprocess.run(
        [
            "gpg", "--batch", "--yes", "--trust-model", "always",
            "--encrypt", *recipient_args,
        ],
        input=plaintext,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"GPG encrypt failed: {result.stderr.decode(errors='replace')}")

    return result.stdout


def decrypt_bytes(ciphertext: bytes) -> bytes:
    """GPG-decrypt bytes using the user's private key.

    Args:
        ciphertext: GPG-encrypted data.

    Returns:
        Decrypted plaintext bytes.

    Raises:
        RuntimeError: If GPG is not installed or decryption fails.
    """
    if not shutil.which("gpg"):
        raise RuntimeError("gpg not found on PATH")

    result = subprocess.run(
        ["gpg", "--batch", "--yes", "--decrypt"],
        input=ciphertext,
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"GPG decrypt failed: {result.stderr.decode(errors='replace')}")

    return result.stdout


def encrypt_file(src: Path, dest: Path, recipient: str, extra_recipients: Optional[list[str]] = None) -> Path:
    """Encrypt a file on disk.

    Args:
        src: Plaintext source file.
        dest: Where to write the encrypted output.
        recipient: Primary GPG fingerprint.
        extra_recipients: Peer fingerprints for shared vaults.

    Returns:
        Path to the encrypted file.
    """
    plaintext = src.read_bytes()
    ciphertext = encrypt_bytes(plaintext, recipient, extra_recipients)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(ciphertext)
    return dest


def decrypt_file(src: Path, dest: Path) -> Path:
    """Decrypt a file on disk.

    Args:
        src: Encrypted (.gpg) source file.
        dest: Where to write the plaintext output.

    Returns:
        Path to the decrypted file.
    """
    ciphertext = src.read_bytes()
    plaintext = decrypt_bytes(ciphertext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plaintext)
    return dest


def is_encrypted_name(name: str) -> bool:
    """Check if a filename has the encrypted suffix."""
    return name.endswith(ENCRYPTED_SUFFIX)


def encrypted_name(name: str) -> str:
    """Add .gpg suffix if not already present."""
    if not name.endswith(ENCRYPTED_SUFFIX):
        return name + ENCRYPTED_SUFFIX
    return name


def plaintext_name(name: str) -> str:
    """Strip .gpg suffix if present."""
    if name.endswith(ENCRYPTED_SUFFIX):
        return name[: -len(ENCRYPTED_SUFFIX)]
    return name
