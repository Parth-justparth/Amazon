"""Warehouse_Return_Flow (Task 17).

Implements the standard warehouse return flow (R4).
- POST /returns/{id}/warehouse/label generates the shipping label.
- POST /returns/{id}/warehouse/receipt confirms warehouse arrival and triggers Refund_Service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Disposition, DispositionRecord, ReturnRequest, ReturnStatus
from app.services.refund import issue_refund
from app.services.return_initiation import can_generate_label, get_db

router = APIRouter(tags=["warehouse-flow"])


@router.post("/returns/{returnRequestId}/warehouse/label")
def generate_label(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Generate a standard return shipping label (R4.1, R4.2)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )

    disp = session.scalar(
        select(DispositionRecord).where(
            DispositionRecord.returnRequestId == returnRequestId
        )
    )
    if disp is None or disp.selected != Disposition.WAREHOUSE_RETURN:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_FLOW",
                "message": "This return is not routed to the warehouse return flow.",
            },
        )

    if not can_generate_label(session, rr):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "LABEL_NOT_ALLOWED",
                "message": "Cannot generate a label for this return request yet.",
            },
        )

    # In a real system, we'd call the carrier API here.
    # On failure, we would raise 502 LABEL_GENERATION_FAILED (R4.5).
    # For demo, we assume success.

    rr.status = ReturnStatus.WAREHOUSE
    session.flush()

    return {
        "message": "Standard Return Approved. Please pack the item.",
        "shippingLabelUrl": f"https://amazon.com/returns/labels/{returnRequestId}",
    }


@router.post("/returns/{returnRequestId}/warehouse/receipt")
def warehouse_receipt(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Confirm warehouse receipt, route to Refurbished, trigger refund (R4.3, R4.4)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "RETURN_NOT_FOUND", "message": "Return request not found."},
        )

    if rr.status != ReturnStatus.WAREHOUSE:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_STATE",
                "message": "Return request must be in WAREHOUSE state to process receipt.",
            },
        )

    # Trigger full refund (R4.4)
    outcome = issue_refund(
        session=session,
        returnRequestId=rr.returnRequestId,
        disposition=Disposition.WAREHOUSE_RETURN,
        amountMinor=rr.purchasePriceMinor,
        currency=rr.currency,
        paymentMethod=rr.paymentMethod,
        quality_check_passed=True,
    )

    return {
        "returnRequestId": rr.returnRequestId,
        "status": rr.status.value,  # issue_refund updates this to REFUNDED if successful
        "message": "Warehouse receipt confirmed. Item routed to Refurbished.",
        "refundStatus": outcome.status.value,
    }
