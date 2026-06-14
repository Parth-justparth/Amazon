"""Hyperlocal Marketplace (Task 18).

Implements the buyer-facing marketplace feed and purchase logic (R5, R6).
"""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func as sa_func, select, update
from sqlalchemy.orm import Session

from app.domain.models import (
    Item,
    ListingStatus,
    MarketplaceListing,
    ReturnRequest,
    ReturnStatus,
)
from app.services.refund import issue_refund
from app.services.green_points import credit, GreenPointsType
from app.services.return_initiation import get_db

router = APIRouter(tags=["marketplace"])


@router.get("/marketplace/cities")
def get_marketplace_cities(session: Session = Depends(get_db)) -> dict:
    """Return the cities that currently have active listings (+ counts)."""

    rows = session.execute(
        select(MarketplaceListing.city, sa_func.count())
        .where(MarketplaceListing.status == ListingStatus.ACTIVE)
        .group_by(MarketplaceListing.city)
        .order_by(MarketplaceListing.city)
    ).all()
    total = sum(n for _, n in rows)
    return {"total": total, "cities": [{"city": c, "count": n} for c, n in rows]}


@router.get("/marketplace")
def get_marketplace_feed(
    city: str | None = None, session: Session = Depends(get_db)
) -> dict:
    """Return active marketplace listings (all cities, or one city) (R6.1, R6.2).

    ``city`` is optional: when omitted (or "ALL") every active listing is
    returned, so an item routed to resale always appears regardless of which
    city its seller is in.
    """

    conditions = [MarketplaceListing.status == ListingStatus.ACTIVE]
    if city and city.upper() != "ALL":
        conditions.append(MarketplaceListing.city == city)

    listings = session.scalars(
        select(MarketplaceListing).where(*conditions)
        .order_by(MarketplaceListing.windowStartAt.desc())
    ).all()

    feed = []
    for listing in listings:
        rr = session.get(ReturnRequest, listing.returnRequestId)
        if rr is None:
            continue
        item = session.get(Item, rr.itemId)
        _reason_txt = rr.reason.value.replace("_", " ").lower()
        why = (
            f"Returned ({_reason_txt}) and graded "
            f"{listing.secondLifeScore}/100 by AI — in great shape but can't be sold "
            "as new, so it's offered locally to give it a second life and cut waste."
        )
        feed.append({
            "listingId": listing.listingId,
            "returnRequestId": listing.returnRequestId,
            "itemCategory": rr.itemCategory.value,
            "itemTitle": item.title if item is not None else None,
            "originalPriceMinor": item.purchasePriceMinor if item is not None else None,
            "discountedPriceMinor": listing.discountedPriceMinor,
            "currency": listing.currency,
            "secondLifeScore": listing.secondLifeScore,
            "reason": rr.reason.value,
            "why": why,
            "photoRefs": listing.photoRefs,
            "city": listing.city,
            "status": listing.status.value,
        })

    return {"city": city, "listings": feed}


class PurchaseRequest(BaseModel):
    buyerId: str


@router.post("/listings/{listingId}/purchase")
def purchase_listing(
    listingId: str, body: PurchaseRequest, session: Session = Depends(get_db)
) -> dict:
    """Purchase a listing with atomic compare-and-set (R6.3, R6.4, R6.5, R5.5)."""

    # Check existence
    listing = session.get(MarketplaceListing, listingId)
    if listing is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "LISTING_NOT_FOUND", "message": "Listing not found."},
        )

    # Idempotency / Concurrency check: must be ACTIVE
    if listing.status != ListingStatus.ACTIVE:
        if listing.status == ListingStatus.SOLD and listing.buyerId == body.buyerId:
            # Idempotent retry by the successful buyer
            pass
        else:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "LISTING_UNAVAILABLE",
                    "message": "This listing is no longer available for purchase.",
                },
            )

    # Compare-and-set to ensure atomicity (R6.5)
    result = session.execute(
        update(MarketplaceListing)
        .where(
            MarketplaceListing.listingId == listingId,
            MarketplaceListing.status == ListingStatus.ACTIVE,
        )
        .values(status=ListingStatus.SOLD, buyerId=body.buyerId)
    )
    if result.rowcount == 0 and listing.buyerId != body.buyerId:
        # R6.5: Concurrency failure
        raise HTTPException(
            status_code=409,
            detail={
                "error": "LISTING_UNAVAILABLE",
                "message": "This listing is no longer available for purchase.",
            },
        )

    # Ensure memory object reflects update
    listing.status = ListingStatus.SOLD
    listing.buyerId = body.buyerId

    rr = session.get(ReturnRequest, listing.returnRequestId)
    if rr is None:
        raise HTTPException(status_code=500, detail={"error": "DATA_INTEGRITY"})

    # Trigger full refund to original seller (R5.5)
    from app.domain.models import Disposition
    refund_outcome = issue_refund(
        session=session,
        returnRequestId=rr.returnRequestId,
        disposition=Disposition.HYPERLOCAL_RESALE,
        amountMinor=rr.purchasePriceMinor,
        currency=rr.currency,
        paymentMethod=rr.paymentMethod,
        quality_check_passed=True, # Resale starts timeline instantly
    )

    # Credit green points (R8.2)
    credit_result = credit(
        session=session,
        customerId=rr.customerId,
        returnRequestId=rr.returnRequestId,
        disposition=Disposition.HYPERLOCAL_RESALE,
    )

    rr.status = ReturnStatus.REFUNDED
    session.flush()

    return {
        "listingId": listing.listingId,
        "status": listing.status.value,
        "message": "Purchase successful.",
        "refundStatus": refund_outcome.status.value,
        "pickupLocation": listing.pickupLocation,
        "pickupContact": listing.pickupContact,
    }
