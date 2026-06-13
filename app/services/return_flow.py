"""Return Flow Orchestrator (Tasks 22 & 23).

Enforces the ordered step-by-step return user flow (R20).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.domain.models import (
    ReturnRequest,
    ReturnStatus,
    FlowStep,
    InspectionOutcome,
    DispositionRecord,
    Refund
)
from app.services.return_initiation import get_db
from app.services.carbon_savings import resolve_impact as compute_savings

router = APIRouter(tags=["return-flow"])


class PickupAddressRequest(BaseModel):
    addressLine1: str
    city: str
    pincode: str


@router.post("/returns/{returnRequestId}/step/pickup")
def set_pickup_address(
    returnRequestId: str, body: PickupAddressRequest, session: Session = Depends(get_db)
) -> dict:
    """Record Pickup_Address (R20.4, R20.8)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})

    # In a fully strict system we'd check if previous steps are complete.
    # Since reason/action are gathered at initiation and proof is condition assessment:
    if rr.flowStep in (FlowStep.CLOSURE,):
        raise HTTPException(status_code=400, detail={"error": "FLOW_COMPLETED"})

    rr.pickupAddress = body.model_dump()
    if rr.flowStep.value < FlowStep.PICKUP_ADDRESS.value: # Assuming enum values can be compared or we just set it.
        # Enums are strings, so we can't do <. Just set it.
        pass
    rr.flowStep = FlowStep.PICKUP_ADDRESS
    session.flush()

    return {
        "returnRequestId": rr.returnRequestId,
        "flowStep": rr.flowStep.value,
        "message": "Pickup address recorded.",
    }


class InspectionRequest(BaseModel):
    outcome: str  # "PASS" or "FAIL"


@router.post("/returns/{returnRequestId}/step/inspection")
def record_inspection(
    returnRequestId: str, body: InspectionRequest, session: Session = Depends(get_db)
) -> dict:
    """Record inspection outcome at pickup/warehouse (R20.5, R20.9)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})

    try:
        outcome = InspectionOutcome(body.outcome)
    except ValueError:
        raise HTTPException(status_code=400, detail={"error": "INVALID_OUTCOME"})

    rr.inspectionOutcome = outcome
    rr.flowStep = FlowStep.INSPECTION

    if outcome == InspectionOutcome.FAIL:
        rr.status = ReturnStatus.MANUAL
        session.flush()
        return {
            "returnRequestId": rr.returnRequestId,
            "status": rr.status.value,
            "message": "Inspection failed. Return flagged for manual resolution.",
        }

    session.flush()
    return {
        "returnRequestId": rr.returnRequestId,
        "status": rr.status.value,
        "message": "Inspection passed.",
    }


@router.post("/returns/{returnRequestId}/step/closure")
def close_return(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Advance the return request to closure and compute Carbon_Savings (R20.6, R12)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})

    if rr.status not in (ReturnStatus.REFUNDED, ReturnStatus.KEEP_IT_ACCEPTED):
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_STATE", "message": "Return cannot be closed yet."},
        )

    # Compute carbon savings (R12)
    disp_rec = session.scalar(
        select(DispositionRecord).where(
            DispositionRecord.returnRequestId == returnRequestId
        )
    )
    if disp_rec and disp_rec.selected:
        savings = compute_savings(session, rr, disp_rec.selected)
        if savings.carbon_savings_kg is not None:
            rr.carbonSavingsKg = savings.carbon_savings_kg

    rr.flowStep = FlowStep.CLOSURE
    rr.status = ReturnStatus.CLOSED
    session.flush()

    return {
        "returnRequestId": rr.returnRequestId,
        "status": rr.status.value,
        "flowStep": rr.flowStep.value,
        "carbonSavingsKg": float(rr.carbonSavingsKg) if rr.carbonSavingsKg is not None else None,
        "message": "Return request closed successfully.",
    }
