"""Unit + property tests for the encryption-at-rest module (R18.2).

Verifies that:
* ``encrypt``/``decrypt`` round-trip representative IFSC and account strings.
* Ciphertext bytes never contain the plaintext substring.
* A serialized/audit-style form (e.g. a stored ``BankDetails`` row referenced
  by ``bankDetailsId``) never contains plaintext values.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.domain import crypto
from app.domain.models import BankDetails

# Representative real-world-shaped values.
SAMPLE_IFSCS = ["HDFC0001234", "SBIN0000456", "ICIC0004321", "PUNB0123456"]
SAMPLE_ACCOUNTS = ["123456789", "000111222333", "987654321012345678", "557700119900"]


def test_encrypt_decrypt_round_trip_ifsc() -> None:
    """Each representative IFSC decrypts back to the original plaintext."""

    for ifsc in SAMPLE_IFSCS:
        token = crypto.encrypt(ifsc)
        assert isinstance(token, bytes)
        assert crypto.decrypt(token) == ifsc


def test_encrypt_decrypt_round_trip_account() -> None:
    """Each representative account number decrypts back to the original."""

    for acct in SAMPLE_ACCOUNTS:
        token = crypto.encrypt(acct)
        assert isinstance(token, bytes)
        assert crypto.decrypt(token) == acct


def test_ciphertext_does_not_contain_plaintext() -> None:
    """Ciphertext bytes must not contain the plaintext substring."""

    for value in SAMPLE_IFSCS + SAMPLE_ACCOUNTS:
        token = crypto.encrypt(value)
        assert value.encode("utf-8") not in token
        # And the base64-ish token text form must not leak it either.
        assert value not in token.decode("ascii")


def test_encryption_is_nondeterministic() -> None:
    """Fernet embeds a random IV, so encrypting twice yields distinct tokens."""

    a = crypto.encrypt("HDFC0001234")
    b = crypto.encrypt("HDFC0001234")
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == "HDFC0001234"


def test_mint_bank_details_id_is_non_sensitive_and_unique() -> None:
    """The minted token carries no plaintext and is unique per call."""

    ifsc, acct = "HDFC0001234", "123456789012"
    id1 = crypto.mint_bank_details_id()
    id2 = crypto.mint_bank_details_id()
    assert id1 != id2
    assert id1.startswith("bd_")
    assert ifsc not in id1 and acct not in id1


def test_stored_bank_details_audit_form_has_no_plaintext() -> None:
    """A stored ``BankDetails`` row (audit form) never contains plaintext."""

    ifsc, acct = "ICIC0004321", "987654321012345678"
    row = BankDetails(
        bankDetailsId=crypto.mint_bank_details_id(),
        returnRequestId="rr_5001",
        ifscEncrypted=crypto.encrypt(ifsc),
        accountNumberEncrypted=crypto.encrypt(acct),
        accepted=True,
    )

    # Build a serialized/audit representation of the persisted columns.
    audit_blob = "".join(
        str(getattr(row, col.name)) for col in BankDetails.__table__.columns
    )
    assert ifsc not in audit_blob
    assert acct not in audit_blob
    # Encrypted columns still decrypt correctly.
    assert crypto.decrypt(row.ifscEncrypted) == ifsc
    assert crypto.decrypt(row.accountNumberEncrypted) == acct


@given(
    # Realistic bank-detail-length secrets (IFSC=11, account=9-18). Very short
    # single-char inputs are excluded: a 1-char value trivially coincides with
    # the base64 token alphabet and is not a representative secret.
    plaintext=st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=6, max_size=24
    )
)
def test_round_trip_property(plaintext: str) -> None:
    """For any realistic secret, decrypt(encrypt(x)) == x and ciphertext hides x."""

    token = crypto.encrypt(plaintext)
    assert crypto.decrypt(token) == plaintext
    assert plaintext.encode("utf-8") not in token
