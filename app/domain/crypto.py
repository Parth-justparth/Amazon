"""Application-layer encryption-at-rest for sensitive bank details (R18.2).

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the ``cryptography`` library with
a urlsafe-base64 32-byte key sourced from :data:`Settings.encryption_key`. In
production this key would be KMS-managed; for the demo it lives in config.

Security rules enforced here:

* Plaintext IFSC / account numbers are **never** logged, echoed, or returned.
* Ciphertext is returned as opaque ``bytes`` and never contains the plaintext.
* Audit/response code references captured details by a non-sensitive
  :func:`mint_bank_details_id` token, never the account number.
"""

from __future__ import annotations

import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


def _fernet() -> Fernet:
    """Build a :class:`Fernet` from the configured urlsafe-base64 32-byte key.

    The key is read fresh each call so test overrides of settings take effect.
    """

    key = get_settings().encryption_key
    # Fernet accepts the key as ``str`` or ``bytes``; normalize to bytes.
    if isinstance(key, str):
        key = key.encode("ascii")
    return Fernet(key)


def encrypt(plaintext: str) -> bytes:
    """Encrypt ``plaintext`` and return an opaque ciphertext token.

    Args:
        plaintext: The sensitive string (e.g. IFSC or account number).

    Returns:
        The Fernet token as ``bytes``. The token never contains the plaintext.

    Raises:
        TypeError: If ``plaintext`` is not a ``str``.
    """

    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be a str")
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    """Decrypt a token produced by :func:`encrypt` back to the plaintext.

    Args:
        token: The ciphertext bytes (``str`` is accepted and encoded as ASCII).

    Returns:
        The original plaintext string.

    Raises:
        InvalidToken: If the token is malformed or was not produced with this key.
    """

    if isinstance(token, str):
        token = token.encode("ascii")
    return _fernet().decrypt(token).decode("utf-8")


def mint_bank_details_id() -> str:
    """Return a fresh, non-sensitive ``bankDetailsId`` reference token.

    The token is random and carries no information derived from the bank
    details, so it is safe to log, store in audit records, and return to
    clients (R18: reference by ``bankDetailsId``, never plaintext).
    """

    return f"bd_{secrets.token_urlsafe(16)}"


__all__ = ["encrypt", "decrypt", "mint_bank_details_id", "InvalidToken"]
