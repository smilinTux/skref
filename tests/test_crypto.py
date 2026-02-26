"""Tests for crypto helpers (name manipulation, key detection)."""

from __future__ import annotations

from skref.crypto import encrypted_name, is_encrypted_name, plaintext_name


class TestNameHelpers:
    """Encrypted filename suffix helpers."""

    def test_encrypted_name_adds_suffix(self) -> None:
        """encrypted_name adds .gpg."""
        assert encrypted_name("file.pdf") == "file.pdf.gpg"

    def test_encrypted_name_idempotent(self) -> None:
        """encrypted_name doesn't double-suffix."""
        assert encrypted_name("file.pdf.gpg") == "file.pdf.gpg"

    def test_plaintext_name_strips_suffix(self) -> None:
        """plaintext_name removes .gpg."""
        assert plaintext_name("file.pdf.gpg") == "file.pdf"

    def test_plaintext_name_no_suffix(self) -> None:
        """plaintext_name is a no-op if no .gpg suffix."""
        assert plaintext_name("file.pdf") == "file.pdf"

    def test_is_encrypted_name(self) -> None:
        """is_encrypted_name detects .gpg suffix."""
        assert is_encrypted_name("data.gpg")
        assert is_encrypted_name("file.pdf.gpg")
        assert not is_encrypted_name("file.pdf")
        assert not is_encrypted_name("gpg")
