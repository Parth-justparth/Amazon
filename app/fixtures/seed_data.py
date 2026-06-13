"""Raw seed / demo datasets for SecondLife AI (design "Datasets" section).

This module holds the *data* only as clean Python literals; the
:mod:`app.fixtures.loader` translates them into ORM rows. Keeping the datasets
declarative makes them easy to inspect, diff, and reuse from tests and the
integrations layer (e.g. the STUB_MODE OpenAI client imports
:data:`PHOTO_SCORE_FIXTURES`).

Monetary convention
-------------------
Every monetary value is stored as **integer minor units** (₹ amount × 100),
consistent with :mod:`app.domain.money`. For example ₹12,999.00 is ``1_299_900``.

Important nuance: the Keep It demo item ``item_keepit_01`` follows the design
worked example exactly — its purchase price is ``38_990`` *minor units*
(₹389.90), which is deliberately different from the AC unit ``item_appl_01``
whose price is ₹38,990 → ``3_899_000`` minor units.
"""

from __future__ import annotations

from datetime import date

from app.domain.models import ItemCategory, PaymentMethod, ReturnReason, SellerType

INR = "INR"


# ---------------------------------------------------------------------------
# Demo category -> policy category mapping (documented per task 3.1)
# ---------------------------------------------------------------------------
#
# The three demo catalog categories (ItemCategory.ELECTRONICS / HOME_APPLIANCES
# / FOOTWEAR) drive the three reference scenarios. The category-policy gate
# (R14/R15) is expressed against the seven *policy* categories. This map records
# how a demo item's category resolves to the policy row that governs it.
DEMO_TO_POLICY_CATEGORY: dict[ItemCategory, ItemCategory] = {
    ItemCategory.ELECTRONICS: ItemCategory.MOBILES_LAPTOPS_ELECTRONICS,
    ItemCategory.HOME_APPLIANCES: ItemCategory.HOME_KITCHEN_APPLIANCES,
    ItemCategory.FOOTWEAR: ItemCategory.CLOTHING_FOOTWEAR,
}

#: Human-readable display names for the policy categories (design table 7).
POLICY_CATEGORY_DISPLAY_NAMES: dict[ItemCategory, str] = {
    ItemCategory.MOBILES_LAPTOPS_ELECTRONICS: "Mobiles Laptops & Electronics",
    ItemCategory.CLOTHING_FOOTWEAR: "Clothing & Footwear",
    ItemCategory.BOOKS: "Books",
    ItemCategory.HOME_KITCHEN_APPLIANCES: "Home & Kitchen Appliances",
    ItemCategory.GROCERY_PERISHABLES: "Grocery & Perishables",
    ItemCategory.BEAUTY_PERSONAL_CARE: "Beauty & Personal Care",
    ItemCategory.SOFTWARE_VIDEO_GAMES_MUSIC: "Software Video Games & Music",
}


# ---------------------------------------------------------------------------
# 4. Sample customer accounts (+ balances)
# ---------------------------------------------------------------------------
# greenPoints is a whole-number Green Points balance; amazonPayMinor is the
# Amazon Pay wallet balance in minor units (₹ × 100).
CUSTOMERS: list[dict] = [
    {"customerId": "cust_01", "name": "Aarav", "city": "Bengaluru",
     "greenPoints": 0, "amazonPayMinor": 0},
    {"customerId": "cust_02", "name": "Diya", "city": "Bengaluru",
     "greenPoints": 1500, "amazonPayMinor": 25_000},  # ₹250.00
    {"customerId": "buyer_22", "name": "Rahul", "city": "Bengaluru",
     "greenPoints": 0, "amazonPayMinor": 500_000},  # ₹5,000.00
]


# ---------------------------------------------------------------------------
# 1 + 10 + 11. Orders (with payment method + seller type) and their items
# ---------------------------------------------------------------------------
# Every Item.orderId references a real Order below (FK consistency). The four
# expanded demo orders ord_1001..ord_1004 carry the payment-method / seller-type
# scenarios; ord_2001..ord_2004 host the remaining catalog/demo items.
ORDERS: list[dict] = [
    # --- Expanded demo orders (design dataset 10) ---
    {"orderId": "ord_1001", "customerId": "cust_01", "deliveryDate": date(2025, 1, 12),
     "currency": INR, "paymentMethod": PaymentMethod.UPI, "sellerType": SellerType.FBA},
    {"orderId": "ord_1002", "customerId": "cust_01", "deliveryDate": date(2025, 1, 10),
     "currency": INR, "paymentMethod": PaymentMethod.CARD, "sellerType": SellerType.FBM},
    {"orderId": "ord_1003", "customerId": "cust_01", "deliveryDate": date(2025, 1, 5),
     "currency": INR, "paymentMethod": PaymentMethod.PAY_ON_DELIVERY, "sellerType": SellerType.FBA},
    {"orderId": "ord_1004", "customerId": "cust_01", "deliveryDate": date(2025, 1, 14),
     "currency": INR, "paymentMethod": PaymentMethod.AMAZON_PAY_BALANCE, "sellerType": SellerType.FBA},
    # --- Supporting orders for the remaining catalog / non-returnable items ---
    {"orderId": "ord_2001", "customerId": "cust_02", "deliveryDate": date(2025, 1, 12),
     "currency": INR, "paymentMethod": PaymentMethod.UPI, "sellerType": SellerType.FBA},
    {"orderId": "ord_2002", "customerId": "cust_02", "deliveryDate": date(2025, 1, 10),
     "currency": INR, "paymentMethod": PaymentMethod.CARD, "sellerType": SellerType.FBA},
    {"orderId": "ord_2003", "customerId": "cust_02", "deliveryDate": date(2025, 1, 5),
     "currency": INR, "paymentMethod": PaymentMethod.UPI, "sellerType": SellerType.FBA},
    {"orderId": "ord_2004", "customerId": "cust_01", "deliveryDate": date(2025, 1, 8),
     "currency": INR, "paymentMethod": PaymentMethod.UPI, "sellerType": SellerType.FBA},
]


# Shape: itemId, orderId, category, title, purchasePriceMinor, currency,
# weightGrams, photoRefs, productClassification, isReturnable.
ITEMS: list[dict] = [
    # --- Electronics (warehouse scenario) ---
    {"itemId": "item_elec_01", "orderId": "ord_1001", "category": ItemCategory.ELECTRONICS,
     "title": "Noise-Cancelling Headphones", "purchasePriceMinor": 1_299_900,
     "currency": INR, "weightGrams": 280, "photoRefs": ["photos_elec_pristine"],
     "productClassification": "HEADPHONES", "isReturnable": True},
    {"itemId": "item_elec_02", "orderId": "ord_2001", "category": ItemCategory.ELECTRONICS,
     "title": "4K Action Camera", "purchasePriceMinor": 2_849_900,
     "currency": INR, "weightGrams": 320, "photoRefs": ["photos_elec_pristine"],
     "productClassification": "CAMERA", "isReturnable": True},
    # --- Home appliances (resale scenario) ---
    {"itemId": "item_appl_01", "orderId": "ord_1002", "category": ItemCategory.HOME_APPLIANCES,
     "title": "1.5T Inverter AC Indoor Unit", "purchasePriceMinor": 3_899_000,
     "currency": INR, "weightGrams": 12_500, "photoRefs": ["photos_appl_likenew"],
     "productClassification": "AIR_CONDITIONER", "isReturnable": True},
    {"itemId": "item_appl_02", "orderId": "ord_2002", "category": ItemCategory.HOME_APPLIANCES,
     "title": "Front-Load Washing Machine", "purchasePriceMinor": 3_250_000,
     "currency": INR, "weightGrams": 65_000, "photoRefs": ["photos_appl_likenew"],
     "productClassification": "WASHING_MACHINE", "isReturnable": True},
    # --- Footwear (donation scenario) ---
    {"itemId": "item_foot_01", "orderId": "ord_1003", "category": ItemCategory.FOOTWEAR,
     "title": "Running Shoes (used)", "purchasePriceMinor": 349_900,
     "currency": INR, "weightGrams": 850, "photoRefs": ["photos_foot_worn"],
     "productClassification": "FOOTWEAR", "isReturnable": True},
    {"itemId": "item_foot_02", "orderId": "ord_2003", "category": ItemCategory.FOOTWEAR,
     "title": "Leather Boots (worn)", "purchasePriceMinor": 499_900,
     "currency": INR, "weightGrams": 1_200, "photoRefs": ["photos_foot_worn"],
     "productClassification": "FOOTWEAR", "isReturnable": True},
    # --- Keep It demo item (design worked example: P = 38,990 MINOR units = ₹389.90) ---
    {"itemId": "item_keepit_01", "orderId": "ord_1004", "category": ItemCategory.HOME_APPLIANCES,
     "title": "Countertop Blender (cosmetic dent)", "purchasePriceMinor": 38_990,
     "currency": INR, "weightGrams": 11_500, "photoRefs": ["photos_appl_likenew"],
     "productClassification": "BLENDER", "isReturnable": True},
    # --- Non-returnable sample item (R15.2 rejection demo) ---
    {"itemId": "item_nr_01", "orderId": "ord_2004", "category": ItemCategory.CLOTHING_FOOTWEAR,
     "title": "Cotton Innerwear Pack", "purchasePriceMinor": 49_900,
     "currency": INR, "weightGrams": 200, "photoRefs": ["photos_innerwear"],
     "productClassification": "INNERWEAR", "isReturnable": False},
]


# ---------------------------------------------------------------------------
# 2. Charities + charity bins
# ---------------------------------------------------------------------------
CHARITIES: list[dict] = [
    {"charityId": "char_01", "name": "GreenEarth Foundation",
     "verified": True, "supportsWorkerPickup": True},
    {"charityId": "char_02", "name": "Helping Hands Trust",
     "verified": True, "supportsWorkerPickup": True},
]

CHARITY_BINS: list[dict] = [
    {"binId": "bin_blr_01", "charityId": "char_01", "city": "Bengaluru",
     "latitude": 12.9716, "longitude": 77.5946, "verified": True},
    {"binId": "bin_blr_02", "charityId": "char_02", "city": "Bengaluru",
     "latitude": 12.9352, "longitude": 77.6245, "verified": True},
    {"binId": "bin_del_01", "charityId": "char_01", "city": "Delhi",
     "latitude": 28.6139, "longitude": 77.2090, "verified": True},
]


# ---------------------------------------------------------------------------
# 3. Cities (served / unserved) with centroids where given
# ---------------------------------------------------------------------------
CITIES: list[dict] = [
    {"cityId": "city_blr", "name": "Bengaluru", "served": True,
     "centroidLat": 12.9716, "centroidLng": 77.5946},
    {"cityId": "city_del", "name": "Delhi", "served": True,
     "centroidLat": 28.6139, "centroidLng": 77.2090},
    {"cityId": "city_xyz", "name": "Tier-3 Town", "served": False,
     "centroidLat": None, "centroidLng": None},
]


# ---------------------------------------------------------------------------
# 5. Demo photo set -> expected SecondLife_Score fixtures (STUB_MODE)
# ---------------------------------------------------------------------------
# Imported by the STUB_MODE OpenAI client to return a deterministic score per
# photo set. ``expected_score`` sits inside ``band`` (inclusive [min, max]).
PHOTO_SCORE_FIXTURES: dict[str, dict] = {
    "photos_elec_pristine": {
        "itemId": "item_elec_01", "band": [90, 100], "expected_score": 95,
        "summary": "Flawless headphones, all angles pristine.", "drives": "WAREHOUSE_RETURN"},
    "photos_appl_likenew": {
        "itemId": "item_appl_01", "band": [80, 89], "expected_score": 85,
        "summary": "Like-new unit, minor box wear only.", "drives": "HYPERLOCAL_RESALE"},
    "photos_foot_worn": {
        "itemId": "item_foot_01", "band": [20, 45], "expected_score": 32,
        "summary": "Scuffed soles and creased leather.", "drives": "GREEN_DONATION"},
}


# ---------------------------------------------------------------------------
# 6. Decision_Engine config constants (importable; not DB rows)
# ---------------------------------------------------------------------------
# Per-category economics constants. Fees are in minor units; per_kg_freight_rate
# is minor units per kilogram; category_base_retention is a fraction in [0, 1].
DECISION_ENGINE_CATEGORY_CONFIG: dict[ItemCategory, dict] = {
    ItemCategory.ELECTRONICS: {
        "base_handling_fee": 5_000, "inspection_fee": 4_000,
        "per_kg_freight_rate": 600, "category_base_retention": 0.55},
    ItemCategory.HOME_APPLIANCES: {
        "base_handling_fee": 8_000, "inspection_fee": 6_000,
        "per_kg_freight_rate": 900, "category_base_retention": 0.50},
    ItemCategory.FOOTWEAR: {
        "base_handling_fee": 2_000, "inspection_fee": 1_500,
        "per_kg_freight_rate": 400, "category_base_retention": 0.30},
}

#: Global Decision_Engine / points / Keep It configuration (importable).
GLOBAL_CONFIG: dict = {
    "green_points_resale": 500,
    "green_points_donation": 300,
    "green_points_keep_it": 200,
    "conversion_rate_points_to_minor": 100,  # 100 minor units per point
    "keep_it_min_score": 70,
    "keep_it_refund_factor": 0.30,
    "keep_it_response_window_hours": 1,
}


# ---------------------------------------------------------------------------
# 7. Category policy dataset (R14/R15). Keyed by ItemCategory enum value.
# ---------------------------------------------------------------------------
# allowableActions hold ReturnAction enum values. windowDays is None where the
# category has no return window. eligibilityCondition is a stable token.
CATEGORY_POLICIES: list[dict] = [
    {"category": ItemCategory.MOBILES_LAPTOPS_ELECTRONICS.value, "windowDays": 7,
     "allowableActions": ["REPLACEMENT"], "eligibilityCondition": "DEFECTIVE_OR_DAMAGED",
     "returnable": True, "requiresDamageProof": False},
    {"category": ItemCategory.CLOTHING_FOOTWEAR.value, "windowDays": 30,
     "allowableActions": ["REFUND", "EXCHANGE"], "eligibilityCondition": "UNWORN_UNWASHED_TAGS",
     "returnable": True, "requiresDamageProof": False},
    {"category": ItemCategory.BOOKS.value, "windowDays": 7,
     "allowableActions": ["REPLACEMENT"], "eligibilityCondition": "UNUSED_UNDAMAGED",
     "returnable": True, "requiresDamageProof": False},
    {"category": ItemCategory.HOME_KITCHEN_APPLIANCES.value, "windowDays": 10,
     "allowableActions": ["REPLACEMENT"], "eligibilityCondition": "DAMAGE_REQUIRES_VIDEO_OR_TECHNICIAN",
     "returnable": True, "requiresDamageProof": True},
    {"category": ItemCategory.GROCERY_PERISHABLES.value, "windowDays": None,
     "allowableActions": ["REFUND"], "eligibilityCondition": "SPOILED_OR_DAMAGED_ON_ARRIVAL",
     "returnable": False, "requiresDamageProof": False},
    {"category": ItemCategory.BEAUTY_PERSONAL_CARE.value, "windowDays": None,
     "allowableActions": ["REFUND", "REPLACEMENT"], "eligibilityCondition": "WRONG_OR_EXPIRED",
     "returnable": False, "requiresDamageProof": False},
    {"category": ItemCategory.SOFTWARE_VIDEO_GAMES_MUSIC.value, "windowDays": None,
     "allowableActions": [], "eligibilityCondition": "NON_RETURNABLE",
     "returnable": False, "requiresDamageProof": False},
]


# ---------------------------------------------------------------------------
# 8. Non-returnable blacklist (productClassification tokens force isReturnable=false)
# ---------------------------------------------------------------------------
# Human-readable groups from the design plus the normalized token set the policy
# gate (task 4) matches productClassification against.
NON_RETURNABLE_BLACKLIST_GROUPS: list[str] = [
    "innerwear/lingerie/swimwear",
    "customized/personalized products",
    "gift cards & digital downloads",
    "pet food & live plants",
]

NON_RETURNABLE_CLASSIFICATIONS: frozenset[str] = frozenset({
    "INNERWEAR", "LINGERIE", "SWIMWEAR",
    "CUSTOMIZED", "PERSONALIZED",
    "GIFT_CARD", "DIGITAL_DOWNLOAD",
    "PET_FOOD", "LIVE_PLANT",
})


# ---------------------------------------------------------------------------
# 9. CO2_Factor config rows (R12)
# ---------------------------------------------------------------------------
# Values are kg CO2 (stored as Decimal in the DB). The key ``per_km`` is the
# factor omitted when the loader's "missing factor" toggle is enabled (R12.6).
CO2_FACTORS: list[dict] = [
    {"factorKey": "disposition:KEEP_IT", "value": "3.0"},
    {"factorKey": "disposition:HYPERLOCAL_RESALE", "value": "2.5"},
    {"factorKey": "disposition:GREEN_DONATION", "value": "2.0"},
    {"factorKey": "disposition:WAREHOUSE_RETURN", "value": "0.0"},
    {"factorKey": "per_km", "value": "0.12"},
    {"factorKey": "per_kg", "value": "0.5"},
]

#: The factor key removed by the "missing factor" toggle to exercise R12.6.
MISSING_CO2_FACTOR_KEY: str = "per_km"


# ---------------------------------------------------------------------------
# 11. Keep It demo return reason (Minor_Issue_Reason) for item_keepit_01
# ---------------------------------------------------------------------------
KEEP_IT_DEMO_REASON: ReturnReason = ReturnReason.MINOR_DEFECT
KEEP_IT_DEMO_ITEM_ID: str = "item_keepit_01"


# ---------------------------------------------------------------------------
# 12. Sample Pay-on-Delivery bank details (demo INPUT constants, not seeded rows)
# ---------------------------------------------------------------------------
# Used to exercise R18 format validation + encryption-at-rest on the ord_1003
# PoD return. Only the encrypted form is ever persisted; nothing here is seeded.
POD_BANK_DETAILS_DEMO: dict = {
    "returnRequestId": "rr_pod_demo",
    "orderId": "ord_1003",
    "itemId": "item_foot_01",
    "valid": {"ifsc": "HDFC0001234", "accountNumber": "123456789012"},
    "invalid": {
        "ifsc": "HDFC00012",        # 9 chars -> IFSC_INVALID (R18.3)
        "accountNumber": "12345",   # 5 digits -> ACCOUNT_INVALID (R18.4)
    },
}


__all__ = [
    "INR",
    "DEMO_TO_POLICY_CATEGORY",
    "POLICY_CATEGORY_DISPLAY_NAMES",
    "CUSTOMERS",
    "ORDERS",
    "ITEMS",
    "CHARITIES",
    "CHARITY_BINS",
    "CITIES",
    "PHOTO_SCORE_FIXTURES",
    "DECISION_ENGINE_CATEGORY_CONFIG",
    "GLOBAL_CONFIG",
    "CATEGORY_POLICIES",
    "NON_RETURNABLE_BLACKLIST_GROUPS",
    "NON_RETURNABLE_CLASSIFICATIONS",
    "CO2_FACTORS",
    "MISSING_CO2_FACTOR_KEY",
    "KEEP_IT_DEMO_REASON",
    "KEEP_IT_DEMO_ITEM_ID",
    "POD_BANK_DETAILS_DEMO",
]
