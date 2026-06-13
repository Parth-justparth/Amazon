"""Hyperlocal Resale Flow (Task 19).

Implements the creation of the marketplace listing and the 48-hour
resale window instructions (R5.1, R5.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Customer,
    Disposition,
    DispositionRecord,
    Item,
    ListingStatus,
    MarketplaceListing,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import utc_now
from app.services.return_initiation import get_db

router = APIRouter(tags=["resale-flow"])


@router.post("/returns/{returnRequestId}/resale/list")
def list_for_resale(returnRequestId: str, session: Session = Depends(get_db)) -> dict:
    """Start the 48-hour resale window and create a listing (R5.1, R5.2)."""

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
    if disp is None or disp.selected != Disposition.HYPERLOCAL_RESALE:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "INVALID_FLOW",
                "message": "This return is not routed to hyperlocal resale.",
            },
        )

    # Note: R20 flow expects PICKUP_ADDRESS to be captured before we can list it.
    if not rr.pickupAddress:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "MISSING_PICKUP",
                "message": "Pickup address must be set before listing.",
            },
        )

    customer = session.get(Customer, rr.customerId)
    if not customer or not customer.city:
        raise HTTPException(
            status_code=400,
            detail={"error": "NO_CITY", "message": "Customer has no city configured."},
        )

    # Check if a listing already exists to make it idempotent
    existing = session.scalar(
        select(MarketplaceListing).where(
            MarketplaceListing.returnRequestId == returnRequestId
        )
    )
    if existing is not None:
        return {
            "message": "Item already listed on hyperlocal marketplace. Please keep it at home for the 48-hour window.",
            "listingId": existing.listingId,
            "discountedPriceMinor": existing.discountedPriceMinor,
            "windowExpiresAt": existing.windowExpiresAt.isoformat() if existing.windowExpiresAt else None,
        }

    # Discount logic: 20% off for 90+ score, else 40% off.
    discount = 0.8 if disp.secondLifeScore >= 90 else 0.6
    discountedPrice = int(rr.purchasePriceMinor * discount)
    if discountedPrice >= rr.purchasePriceMinor:
        discountedPrice = rr.purchasePriceMinor - 1

    item = session.get(Item, rr.itemId)
    photos = item.photoRefs if item else []

    now = utc_now()
    expires = now + timedelta(hours=48)

    listingId = f"list_{uuid.uuid4().hex[:12]}"
    ml = MarketplaceListing(
        listingId=listingId,
        returnRequestId=returnRequestId,
        city=customer.city,
        discountedPriceMinor=discountedPrice,
        currency=rr.currency,
        secondLifeScore=disp.secondLifeScore,
        photoRefs=photos,
        status=ListingStatus.ACTIVE,
        windowStartAt=now,
        windowExpiresAt=expires,
        pickupLocation=rr.pickupAddress.get("addressLine1", "Customer Address"),
        pickupContact=customer.name,
    )
    session.add(ml)

    rr.status = ReturnStatus.RESALE
    session.flush()

    return {
        "message": "Item listed on hyperlocal marketplace. Please keep it at home for the 48-hour window.",
        "listingId": ml.listingId,
        "discountedPriceMinor": ml.discountedPriceMinor,
        "windowExpiresAt": ml.windowExpiresAt.isoformat(),
    }
