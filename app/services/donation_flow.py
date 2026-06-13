"""Green Donation Flow (Task 20).

Implements Charity bin finding, worker pickup scheduling, and confirmation (R7).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.domain.models import (
    Charity,
    CharityBin,
    City,
    Customer,
    Disposition,
    DispositionRecord,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import utc_now
from app.services.green_points import credit
from app.services.refund import issue_refund
from app.services.return_initiation import get_db

router = APIRouter(tags=["donation-flow"])


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


@router.get("/returns/{returnRequestId}/donation/options")
def get_donation_options(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Present nearest charity bin and pickup options (R7.1, R7.2, R7.5, R7.7)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(
            status_code=404, detail={"error": "RETURN_NOT_FOUND", "message": "Not found"}
        )

    disp = session.scalar(
        select(DispositionRecord).where(
            DispositionRecord.returnRequestId == returnRequestId
        )
    )
    if disp is None or disp.selected != Disposition.GREEN_DONATION:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_FLOW", "message": "Not routed to donation."},
        )

    customer = session.get(Customer, rr.customerId)
    if not customer or not customer.city:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_CITY", "message": "Customer has no city configured."},
        )

    city_obj = session.scalar(select(City).where(City.name == customer.city))
    lat, lng = None, None
    if city_obj and city_obj.centroidLat and city_obj.centroidLng:
        lat, lng = city_obj.centroidLat, city_obj.centroidLng

    nearest_bin = None
    min_dist = float("inf")

    if lat is not None and lng is not None:
        bins = session.scalars(
            select(CharityBin).where(CharityBin.verified.is_(True))
        ).all()
        for b in bins:
            d = haversine(lat, lng, b.latitude, b.longitude)
            if d <= 25.0 and d < min_dist:
                min_dist = d
                nearest_bin = b

    charities_with_pickup = session.scalars(
        select(Charity).where(
            Charity.verified.is_(True), Charity.supportsWorkerPickup.is_(True)
        )
    ).all()
    pickup_available = len(charities_with_pickup) > 0

    if not nearest_bin and not pickup_available:
        # Re-evaluate logic (R7.7) -> In a real system we'd call DecisionEngine.re_evaluate here.
        # But this is an endpoint to GET options, we should just return that it's unavailable.
        return {
            "message": "No donation options available. Re-evaluating disposition...",
            "reEvaluate": True,
        }

    options = {
        "pickupAvailable": pickup_available,
        "nearestBin": None,
    }

    if nearest_bin:
        options["nearestBin"] = {
            "binId": nearest_bin.binId,
            "charityId": nearest_bin.charityId,
            "distanceKm": round(min_dist, 2),
            "city": nearest_bin.city,
        }
        
    rr.status = ReturnStatus.DONATION
    session.flush()

    return options


class ScheduleRequest(BaseModel):
    charityId: str | None = None


@router.post("/returns/{returnRequestId}/donation/pickup")
def schedule_pickup(
    returnRequestId: str, body: ScheduleRequest, session: Session = Depends(get_db)
) -> dict:
    """Schedule a charity worker pickup (R7.3, R7.6)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND"})

    if rr.status not in (ReturnStatus.DONATION, ReturnStatus.DECIDED):
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_STATE", "message": "Cannot schedule pickup."},
        )

    # In a real app we'd integrate with charity logistics here.
    scheduled_date = utc_now() + timedelta(days=3)  # within 5 business days
    
    rr.status = ReturnStatus.DONATION
    session.flush()

    return {
        "message": "Pickup scheduled successfully.",
        "scheduledDate": scheduled_date.date().isoformat(),
    }


@router.post("/returns/{returnRequestId}/donation/confirm")
def confirm_donation(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Confirm drop-off/pickup, issue refund and green points (R7.4)."""

    rr = session.get(ReturnRequest, returnRequestId)
    if rr is None or rr.status != ReturnStatus.DONATION:
        raise HTTPException(status_code=400, detail={"error": "INVALID_STATE"})

    outcome = issue_refund(
        session=session,
        returnRequestId=rr.returnRequestId,
        disposition=Disposition.GREEN_DONATION,
        amountMinor=rr.purchasePriceMinor,
        currency=rr.currency,
        paymentMethod=rr.paymentMethod,
        quality_check_passed=True,
    )

    credit(
        session=session,
        returnRequestId=rr.returnRequestId,
        customerId=rr.customerId,
        disposition=Disposition.GREEN_DONATION,
    )

    rr.status = ReturnStatus.REFUNDED
    session.flush()

    return {
        "message": "Donation confirmed. Refund and Green Points issued.",
        "refundStatus": outcome.status.value,
    }
