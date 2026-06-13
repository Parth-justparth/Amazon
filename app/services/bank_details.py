"""Bank_Details_Capture_Service — validate + encrypt-at-rest NEFT details (R18).

Implements the design "Bank_Details_Capture_Service" section and Requirements
18.1-18.5 (and supports the Pay-on-Delivery refund gate R18.6 / R17.10 via the
``accepted`` flag the :mod:`app.services.refund` service checks).

Validation (R18.1, R18.3, R18.4)
--------------------------------
* **IFSC** must be **exactly 11 characters, letters and digits only** (ASCII
  alphanumeric). Anything else is rejected with ``IFSC_INVALID`` naming the
  ``ifsc`` field and stating the expected 11-character format; **nothing is
  stored** (R18.3).
* **Account number** must be **9 to 18 digits, digits only**. Anything else is
  rejected with ``ACCOUNT_INVALID`` naming the ``accountNumber`` field and
  stating the expected 9-to-18-digit format; **nothing is stored** (R18.4).

The IFSC is validated first (matching the design's stated check order); the
first failing field is reported.

Capture (R18.2, R18.5)
----------------------
On a valid pair both values are **encrypted at rest** via
:mod:`app.domain.crypto` and persisted (well within the 5-second bound, R18.2),
the row is marked ``accepted=True``, and an acceptance result is returned that
references the stored details only by the non-sensitive ``bankDetailsId`` token
minted by :func:`app.domain.crypto.mint_bank_details_id` (R18.5).

Security
--------
Plaintext IFSC / account number are **never** logged, echoed, or returned. Only
the opaque ciphertext is persisted, and responses / audit records reference the
capture by ``bankDetailsId`` only (design "Security considerations").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.crypto import encrypt, mint_bank_details_id
from app.domain.models import BankDetails, ReturnRequest
from app.services.return_initiation import get_db

__all__ = [
    "router",
    "IFSC_PATTERN",
    "ACCOUNT_NUMBER_PATTERN",
    "IFSC_FORMAT_MESSAGE",
    "ACCOUNT_FORMAT_MESSAGE",
    "CaptureResult",
    "validate_ifsc",
    "validate_account_number",
    "capture_bank_details",
]


# ---------------------------------------------------------------------------
# Validation patterns + messages (R18.1, R18.3, R18.4)
# ---------------------------------------------------------------------------

#: IFSC: exactly 11 ASCII letters/digits (R18.1, R18.3).
IFSC_PATTERN = re.compile(r"^[A-Za-z0-9]{11}$")

#: Account number: 9 to 18 ASCII digits (R18.1, R18.4).
ACCOUNT_NUMBER_PATTERN = re.compile(r"^[0-9]{9,18}$")

#: Field-specific rejection messages naming the failing field + expected format.
IFSC_FORMAT_MESSAGE = "ifsc must be exactly 11 alphanumeric characters"
ACCOUNT_FORMAT_MESSAGE = "accountNumber must be 9-18 digits"


def validate_ifsc(ifsc: object) -> bool:
    """Return whether ``ifsc`` is exactly 11 ASCII letters/digits (R18.1, R18.3)."""

    return isinstance(ifsc, str) and IFSC_PATTERN.fullmatch(ifsc) is not None


def validate_account_number(accountNumber: object) -> bool:
    """Return whether ``accountNumber`` is 9-18 ASCII digits (R18.1, R18.4)."""

    return (
        isinstance(accountNumber, str)
        and ACCOUNT_NUMBER_PATTERN.fullmatch(accountNumber) is not None
    )


# ---------------------------------------------------------------------------
# Result carrier
# ---------------------------------------------------------------------------


@dataclass
class CaptureResult:
    """Outcome of a bank-details capture attempt.

    ``accepted`` is ``True`` only when both values passed validation and the
    encrypted row was persisted. On rejection, ``field`` names the failing input
    (``"ifsc"`` or ``"accountNumber"``), ``error_code`` carries the
    machine-readable code, and ``message`` states the expected format. The
    plaintext IFSC / account number are intentionally absent from this carrier
    so they can never be echoed or logged (R18 security note).
    """

    accepted: bool
    bankDetailsId: str | None = None
    error_code: str | None = None
    field: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Core capture (R18.2, R18.3, R18.4, R18.5)
# ---------------------------------------------------------------------------


def capture_bank_details(
    session: Session,
    returnRequestId: str,
    ifsc: object,
    accountNumber: object,
) -> CaptureResult:
    """Validate and (on success) encrypt + persist bank details for a return.

    Args:
        session: Open session; the caller controls the transaction boundary.
        returnRequestId: The Pay-on-Delivery return the details belong to.
        ifsc: Submitted IFSC code; validated as exactly 11 ASCII alnum chars.
        accountNumber: Submitted account number; validated as 9-18 ASCII digits.

    Returns:
        A :class:`CaptureResult`. On invalid input nothing is stored and the
        failing field/format is named (R18.3, R18.4). On a valid pair both
        values are encrypted at rest and the row is marked accepted; the result
        references the capture only by ``bankDetailsId`` (R18.2, R18.5).
    """

    # --- Validation first; reject WITHOUT storing (R18.3, R18.4) ---
    if not validate_ifsc(ifsc):
        return CaptureResult(
            accepted=False,
            error_code="IFSC_INVALID",
            field="ifsc",
            message=IFSC_FORMAT_MESSAGE,
        )
    if not validate_account_number(accountNumber):
        return CaptureResult(
            accepted=False,
            error_code="ACCOUNT_INVALID",
            field="accountNumber",
            message=ACCOUNT_FORMAT_MESSAGE,
        )

    # --- Encrypt at rest; never persist or surface plaintext (R18.2) ---
    ifsc_encrypted = encrypt(ifsc)
    account_encrypted = encrypt(accountNumber)

    # One capture per return request (BankDetails.returnRequestId is unique).
    # An existing capture is updated in place so a re-submission of corrected
    # details is accepted while preserving the one-row invariant.
    existing = session.scalar(
        select(BankDetails).where(BankDetails.returnRequestId == returnRequestId)
    )
    if existing is not None:
        existing.ifscEncrypted = ifsc_encrypted
        existing.accountNumberEncrypted = account_encrypted
        existing.accepted = True
        session.flush()
        return CaptureResult(accepted=True, bankDetailsId=existing.bankDetailsId)

    record = BankDetails(
        bankDetailsId=mint_bank_details_id(),
        returnRequestId=returnRequestId,
        ifscEncrypted=ifsc_encrypted,
        accountNumberEncrypted=account_encrypted,
        accepted=True,
    )
    session.add(record)
    session.flush()
    return CaptureResult(accepted=True, bankDetailsId=record.bankDetailsId)


# ---------------------------------------------------------------------------
# FastAPI router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["bank-details"])


@router.post("/returns/{returnRequestId}/bank-details")
def post_bank_details(
    returnRequestId: str, body: dict, session: Session = Depends(get_db)
) -> dict:
    """Capture Pay-on-Delivery NEFT bank details for a return (R18.1-18.5).

    Accepts ``{"ifsc": "HDFC0001234", "accountNumber": "123456789012"}``.
    Returns ``{"accepted": true, "bankDetailsId": ...}`` on success (R18.5);
    a ``400`` naming the failing field and its expected format on invalid input
    (R18.3, R18.4). The submitted plaintext is never echoed back.
    """

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "RETURN_NOT_FOUND",
                "message": "Return request not found.",
            },
        )

    ifsc = body.get("ifsc") if isinstance(body, dict) else None
    accountNumber = body.get("accountNumber") if isinstance(body, dict) else None

    result = capture_bank_details(session, returnRequestId, ifsc, accountNumber)
    if not result.accepted:
        raise HTTPException(
            status_code=400,
            detail={
                "error": result.error_code,
                "field": result.field,
                "message": result.message,
            },
        )

    return {"accepted": True, "bankDetailsId": result.bankDetailsId}
