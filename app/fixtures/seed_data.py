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


# ===========================================================================
# BULK DEMO CATALOG (generated) — a large, valid dataset for hands-on testing.
# ---------------------------------------------------------------------------
# Everything below is appended to the base datasets above so all the hand-built
# demo entries (and their exact-value tests) stay intact. IDs are deterministic
# (seeded RNG) so repeated loads are stable and idempotent. All catalog items
# use the three decision-capable demo categories (ELECTRONICS / HOME_APPLIANCES
# / FOOTWEAR) so the full return -> assessment -> decision flow works for every
# item; a handful of non-returnable items exercise the R15 rejection path.
# ===========================================================================

import random as _random

from app.domain.models import ReturnAction as _ReturnAction

_rng = _random.Random(20240614)

# --- More served cities (marketplace + donation coverage) ------------------
CITIES.extend([
    {"cityId": "city_mum", "name": "Mumbai", "served": True,
     "centroidLat": 19.0760, "centroidLng": 72.8777},
    {"cityId": "city_hyd", "name": "Hyderabad", "served": True,
     "centroidLat": 17.3850, "centroidLng": 78.4867},
    {"cityId": "city_che", "name": "Chennai", "served": True,
     "centroidLat": 13.0827, "centroidLng": 80.2707},
    {"cityId": "city_pun", "name": "Pune", "served": True,
     "centroidLat": 18.5204, "centroidLng": 73.8567},
    {"cityId": "city_kol", "name": "Kolkata", "served": True,
     "centroidLat": 22.5726, "centroidLng": 88.3639},
])

_SERVED_CITY_NAMES = [
    "Bengaluru", "Delhi", "Mumbai", "Hyderabad", "Chennai", "Pune", "Kolkata",
]

# --- More charities + donation bins across cities --------------------------
CHARITIES.extend([
    {"charityId": "char_03", "name": "CareCircle India",
     "verified": True, "supportsWorkerPickup": True},
    {"charityId": "char_04", "name": "ReNew Trust",
     "verified": True, "supportsWorkerPickup": False},
])

for _city, (_la, _ln) in {
    "Mumbai": (19.07, 72.87), "Hyderabad": (17.38, 78.48),
    "Chennai": (13.08, 80.27), "Pune": (18.52, 73.85), "Kolkata": (22.57, 88.36),
}.items():
    CHARITY_BINS.append({
        "binId": f"bin_{_city[:3].lower()}_01", "charityId": "char_03",
        "city": _city, "latitude": _la, "longitude": _ln, "verified": True,
    })

# --- Bulk customers --------------------------------------------------------
_BULK_NAMES = [
    "Aanya", "Vivaan", "Ananya", "Aditya", "Ishaan", "Saanvi", "Kabir", "Myra",
    "Arjun", "Anika", "Reyansh", "Aadhya", "Vihaan", "Kiara", "Sai", "Pari",
    "Krishna", "Riya", "Dhruv", "Navya", "Ayaan", "Mira", "Rohan", "Tara",
    "Kunal", "Meera", "Yash", "Sara", "Nikhil", "Ira",
]
_BULK_CUSTOMERS = []
for _i, _nm in enumerate(_BULK_NAMES):
    _BULK_CUSTOMERS.append({
        "customerId": f"cust_{100 + _i}", "name": _nm,
        "city": _SERVED_CITY_NAMES[_i % len(_SERVED_CITY_NAMES)],
        "greenPoints": _rng.choice([0, 0, 250, 500, 1000, 1500]),
        "amazonPayMinor": _rng.choice([0, 10_000, 25_000, 50_000, 100_000]),
    })
CUSTOMERS.extend(_BULK_CUSTOMERS)

# --- Product pools per demo category ---------------------------------------
# (title, productClassification, price_min_minor, price_max_minor, g_min, g_max)
_CATALOG = {
    ItemCategory.ELECTRONICS: [
        ("Wireless Earbuds", "EARBUDS", 199_900, 1_499_900, 60, 250),
        ("Smartphone", "SMARTPHONE", 899_900, 9_999_900, 150, 250),
        ('13" Laptop', "LAPTOP", 3_999_900, 9_999_900, 1_100, 2_200),
        ("Bluetooth Speaker", "SPEAKER", 149_900, 799_900, 300, 1_800),
        ("Smartwatch", "SMARTWATCH", 129_900, 3_999_900, 40, 120),
        ("Power Bank", "POWER_BANK", 99_900, 499_900, 180, 600),
        ("Action Camera", "CAMERA", 1_499_900, 4_999_900, 120, 400),
        ("Gaming Mouse", "MOUSE", 99_900, 899_900, 80, 180),
    ],
    ItemCategory.HOME_APPLIANCES: [
        ("Microwave Oven", "MICROWAVE", 699_900, 2_499_900, 9_000, 18_000),
        ("Air Fryer", "AIR_FRYER", 399_900, 1_499_900, 3_500, 7_000),
        ("Mixer Grinder", "MIXER_GRINDER", 249_900, 999_900, 2_500, 5_000),
        ("Vacuum Cleaner", "VACUUM_CLEANER", 499_900, 2_999_900, 4_000, 9_000),
        ("Electric Kettle", "KETTLE", 99_900, 399_900, 900, 1_800),
        ("Induction Cooktop", "INDUCTION", 199_900, 799_900, 2_000, 3_500),
        ("Front-Load Washer", "WASHING_MACHINE", 2_499_900, 5_999_900, 55_000, 75_000),
        ("Double-Door Fridge", "REFRIGERATOR", 2_999_900, 7_999_900, 45_000, 70_000),
    ],
    ItemCategory.FOOTWEAR: [
        ("Running Shoes", "FOOTWEAR", 99_900, 1_299_900, 600, 1_100),
        ("Casual Sneakers", "FOOTWEAR", 149_900, 999_900, 650, 1_200),
        ("Leather Boots", "FOOTWEAR", 299_900, 1_499_900, 900, 1_500),
        ("Sports Sandals", "FOOTWEAR", 79_900, 499_900, 400, 900),
        ("Formal Loafers", "FOOTWEAR", 199_900, 1_199_900, 700, 1_300),
        ("Trail Shoes", "FOOTWEAR", 249_900, 1_399_900, 750, 1_300),
    ],
}

_PHOTO_BY_CAT = {
    ItemCategory.ELECTRONICS: "photos_elec_pristine",
    ItemCategory.HOME_APPLIANCES: "photos_appl_likenew",
    ItemCategory.FOOTWEAR: "photos_foot_worn",
}

_CATS = list(_CATALOG.keys())
_PM_CYCLE = list(PaymentMethod)
# Mostly FBA so flows are smooth; ~1 in 4 is FBM (exercises seller-auth/A-to-z).
_SELLER_CYCLE = [SellerType.FBA, SellerType.FBA, SellerType.FBA, SellerType.FBM]

_BULK_ORDERS = []
_BULK_ITEMS = []
for _n in range(150):
    _cat = _CATS[_n % len(_CATS)]
    _title, _cls, _pmin, _pmax, _gmin, _gmax = _rng.choice(_CATALOG[_cat])
    _price = _rng.randint(_pmin // 100, _pmax // 100) * 100
    _grams = _rng.randint(_gmin, _gmax)
    _cust = _rng.choice(_BULK_CUSTOMERS)["customerId"]
    _oid = f"ord_5{_n:03d}"
    _iid = f"item_cat_{_n:04d}"
    _BULK_ORDERS.append({
        "orderId": _oid, "customerId": _cust,
        "deliveryDate": date(2025, (_n % 12) + 1, (_n % 27) + 1),
        "currency": INR, "paymentMethod": _PM_CYCLE[_n % len(_PM_CYCLE)],
        "sellerType": _SELLER_CYCLE[_n % len(_SELLER_CYCLE)],
    })
    _BULK_ITEMS.append({
        "itemId": _iid, "orderId": _oid, "category": _cat, "title": _title,
        "purchasePriceMinor": _price, "currency": INR, "weightGrams": _grams,
        "photoRefs": [_PHOTO_BY_CAT[_cat]], "productClassification": _cls,
        "isReturnable": True,
    })

# A few non-returnable items for the R15 rejection path.
for _j, (_title, _cls) in enumerate([
    ("Cotton Briefs (3-pack)", "INNERWEAR"),
    ("Personalized Photo Mug", "PERSONALIZED"),
    ("₹500 Gift Card", "GIFT_CARD"),
    ("Designer Swimsuit", "SWIMWEAR"),
]):
    _oid = f"ord_5{150 + _j:03d}"
    _iid = f"item_nr_{10 + _j:02d}"
    _BULK_ORDERS.append({
        "orderId": _oid, "customerId": _BULK_CUSTOMERS[_j]["customerId"],
        "deliveryDate": date(2025, 2, 10 + _j), "currency": INR,
        "paymentMethod": PaymentMethod.UPI, "sellerType": SellerType.FBA,
    })
    _BULK_ITEMS.append({
        "itemId": _iid, "orderId": _oid, "category": ItemCategory.CLOTHING_FOOTWEAR,
        "title": _title, "purchasePriceMinor": _rng.randint(2_000, 9_000) * 100,
        "currency": INR, "weightGrams": _rng.randint(150, 500),
        "photoRefs": ["photos_innerwear"], "productClassification": _cls,
        "isReturnable": False,
    })

ORDERS.extend(_BULK_ORDERS)
ITEMS.extend(_BULK_ITEMS)


# ---------------------------------------------------------------------------
# Pre-seeded marketplace inventory (loaded only in live/demo mode, not tests).
# Each listing is backed by a dedicated RESALE return request + item/order so
# the buy flow (atomic compare-and-set + seller refund) works out of the box.
# ---------------------------------------------------------------------------
LISTING_ORDERS: list[dict] = []
LISTING_ITEMS: list[dict] = []
RESALE_RETURN_REQUESTS: list[dict] = []
MARKETPLACE_LISTINGS: list[dict] = []

for _n in range(30):
    _cat = _CATS[_n % len(_CATS)]
    _title, _cls, _pmin, _pmax, _gmin, _gmax = _rng.choice(_CATALOG[_cat])
    _price = _rng.randint(_pmin // 100, _pmax // 100) * 100
    _grams = _rng.randint(_gmin, _gmax)
    _city = _SERVED_CITY_NAMES[_n % 5]
    _sellers = [c for c in _BULK_CUSTOMERS if c["city"] == _city] or _BULK_CUSTOMERS
    _seller = _sellers[_n % len(_sellers)]
    _oid = f"ord_mk_{_n:03d}"
    _iid = f"item_mk_{_n:03d}"
    _rid = f"rr_mk_{_n:03d}"
    _lid = f"list_mk_{_n:03d}"
    _score = _rng.randint(78, 97)
    _disc = int(_price * _rng.choice([0.60, 0.65, 0.70, 0.75, 0.80]))
    LISTING_ORDERS.append({
        "orderId": _oid, "customerId": _seller["customerId"],
        "deliveryDate": date(2025, 3, 1), "currency": INR,
        "paymentMethod": PaymentMethod.UPI, "sellerType": SellerType.FBA,
    })
    LISTING_ITEMS.append({
        "itemId": _iid, "orderId": _oid, "category": _cat, "title": _title,
        "purchasePriceMinor": _price, "currency": INR, "weightGrams": _grams,
        "photoRefs": [_PHOTO_BY_CAT[_cat]], "productClassification": _cls,
        "isReturnable": True,
    })
    RESALE_RETURN_REQUESTS.append({
        "returnRequestId": _rid, "orderId": _oid, "itemId": _iid,
        "customerId": _seller["customerId"], "reason": ReturnReason.DEFECTIVE,
        "returnAction": _ReturnAction.REPLACEMENT, "itemCategory": _cat,
        "purchasePriceMinor": _price, "currency": INR, "weightGrams": _grams,
        "paymentMethod": PaymentMethod.UPI, "sellerType": SellerType.FBA,
        "returnWindowStart": date(2025, 3, 1),
    })
    MARKETPLACE_LISTINGS.append({
        "listingId": _lid, "returnRequestId": _rid, "city": _city,
        "discountedPriceMinor": _disc, "currency": INR, "secondLifeScore": _score,
        "photoRefs": [_PHOTO_BY_CAT[_cat]], "title": _title,
        "pickupLocation": f"Near {_city} city centre", "pickupContact": _seller["name"],
    })
