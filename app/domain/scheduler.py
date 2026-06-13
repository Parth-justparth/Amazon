"""Background scheduler (Task 21).

Runs periodic checks to enforce window expirations:
- 48-hour Hyperlocal Resale window (R5.7)
- 30-day Warehouse receipt timeout (R4.6)
- 1-hour Keep It offer timeout (R11.7)
- 24-48h FBM Seller Auth timeout (R19.4)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Disposition,
    KeepItOffer,
    KeepItOfferState,
    ListingStatus,
    MarketplaceListing,
    ReturnRequest,
    ReturnStatus,
)
from app.domain.money import utc_now
from app.domain.repository import get_session_factory
from app.services.decision_engine import re_evaluate
from app.services.refund import issue_platform_refund

logger = logging.getLogger(__name__)


def process_resale_expiries(session: Session) -> int:
    """Find expired resale listings and re-evaluate them (R5.7)."""
    now = utc_now()
    expired_listings = session.scalars(
        select(MarketplaceListing).where(
            MarketplaceListing.status == ListingStatus.ACTIVE,
            MarketplaceListing.windowExpiresAt <= now,
        )
    ).all()

    count = 0
    for listing in expired_listings:
        listing.status = ListingStatus.EXPIRED
        rr = session.get(ReturnRequest, listing.returnRequestId)
        if rr and rr.status == ReturnStatus.RESALE:
            # R5.7: Re-evaluate through decision engine
            re_evaluate(session, rr, excluded_disposition=Disposition.HYPERLOCAL_RESALE)
            count += 1
    
    session.flush()
    return count


def process_warehouse_timeouts(session: Session) -> int:
    """Find WAREHOUSE returns older than 30 days and flag MANUAL (R4.6)."""
    # Use the ReturnRequest.createdAt or when it entered WAREHOUSE (approximated)
    # The requirement says "within 30 calendar days of shipping-label generation".
    # For simplicity, we check if it has been in WAREHOUSE for 30 days. We can
    # approximate this using a specific timestamp if we tracked it, else `createdAt` + 30.
    now = utc_now()
    timeout_threshold = now - timedelta(days=30)
    
    timed_out = session.scalars(
        select(ReturnRequest).where(
            ReturnRequest.status == ReturnStatus.WAREHOUSE,
            ReturnRequest.createdAt <= timeout_threshold, # Approximation
        )
    ).all()

    count = 0
    for rr in timed_out:
        rr.status = ReturnStatus.MANUAL
        count += 1
    
    session.flush()
    return count


def process_keep_it_timeouts(session: Session) -> int:
    """Find expired Keep_It_Offers and re-evaluate them (R11.7)."""
    now = utc_now()
    expired_offers = session.scalars(
        select(KeepItOffer).where(
            KeepItOffer.state == KeepItOfferState.PRESENTED,
            KeepItOffer.expiresAt <= now,
        )
    ).all()

    count = 0
    for offer in expired_offers:
        offer.state = KeepItOfferState.EXPIRED
        rr = session.get(ReturnRequest, offer.returnRequestId)
        if rr and rr.status == ReturnStatus.KEEP_IT_OFFERED:
            # Route to DecisionEngine excluding Keep It
            re_evaluate(session, rr, excluded_disposition=Disposition.KEEP_IT)
            count += 1
            
    session.flush()
    return count


def process_fbm_seller_timeouts(session: Session) -> int:
    """Find expired FBM seller authorizations and apply A-to-z (R19.4)."""
    now = utc_now()
    expired_auths = session.scalars(
        select(ReturnRequest).where(
            ReturnRequest.status == ReturnStatus.AWAITING_SELLER_AUTH,
            ReturnRequest.sellerAuthDeadline <= now,
        )
    ).all()

    count = 0
    for rr in expired_auths:
        # Issue platform refund
        issue_platform_refund(
            session=session,
            returnRequestId=rr.returnRequestId,
            purchasePriceMinor=rr.purchasePriceMinor,
            currency=rr.currency,
            paymentMethod=rr.paymentMethod,
            now=now,
        )
        count += 1
        
    session.flush()
    return count


def run_scheduler_cycle(session: Session) -> dict:
    """Run one full cycle of the background scheduler."""
    resale = process_resale_expiries(session)
    warehouse = process_warehouse_timeouts(session)
    keep_it = process_keep_it_timeouts(session)
    fbm = process_fbm_seller_timeouts(session)
    
    session.commit()
    
    return {
        "resale_expired": resale,
        "warehouse_timed_out": warehouse,
        "keep_it_expired": keep_it,
        "fbm_atoz_applied": fbm,
    }


async def scheduler_loop(): # pragma: no cover
    """Async loop to run the scheduler periodically."""
    factory = get_session_factory()
    while True:
        try:
            session = factory()
            try:
                run_scheduler_cycle(session)
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Scheduler cycle failed: {e}")
        await asyncio.sleep(60) # Run every minute
