# Implementation Plan: Amazon SecondLife AI

## Overview

This plan converts the SecondLife AI design into incremental, test-driven coding tasks for a FastAPI (Python) + Pydantic v2 + SQLAlchemy/SQLite backend, an OpenAI vision client with `STUB_MODE`, and a lightweight web frontend. Tasks build bottom-up: scaffolding and persistence first, then seed fixtures, the category-policy/returnability gate, core services (assessment, hybrid Decision_Engine, refund, points, carbon, bank details, Keep It), disposition flows and the marketplace, the scheduler, the standard return-flow orchestration and API wiring, the frontend, and finally end-to-end integration/demo tests.

Property-based tests use Hypothesis with a minimum of 100 examples each, against `STUB_MODE` for AI/LLM paths. Each property test is tagged with a comment in the exact format `Feature: secondlife-ai, Property {number}: {property_text}` and references the design property and the requirement clause it validates. Sub-tasks marked with `*` (unit/property/integration tests) are optional and can be skipped for a faster MVP.

## Tasks

- [x] 1. Project scaffolding and core infrastructure
  - [x] 1.1 Create FastAPI project structure and configuration
    - Create the package layout: `app/main.py`, `app/services/`, `app/domain/`, `app/integrations/`, `app/api/`, `app/fixtures/`, `tests/`
    - Add dependencies and pin versions (fastapi, uvicorn, pydantic v2, sqlalchemy, hypothesis, pytest, httpx, cryptography)
    - Implement a settings/config loader exposing `STUB_MODE`, database URL, OpenAI model/version, and the demo encryption key
    - Define monetary convention helpers (integer minor units + ISO-4217 currency) and ISO-8601 UTC time helpers
    - _Requirements: (foundation for all)_
  - [x] 1.2 Set up the test framework and CI entry point
    - Configure pytest + Hypothesis (profile with `max_examples >= 100`), shared conftest fixtures, and a `--run` style single-pass test command
    - Add a CI script that runs unit and property tests with `STUB_MODE` enabled
    - _Requirements: (foundation for all testing)_

- [x] 2. Domain models, persistence, and crypto
  - [x] 2.1 Implement domain models (`app/domain/models.py`)
    - Define SQLAlchemy ORM + Pydantic v2 models for Order, Item, Customer, ReturnRequest (with `status`, `flowStep`, `excludedDispositions`), Assessment, Disposition/audit record, MarketplaceListing, Charity, CharityBin, City, Refund, GreenPointsLedger, CarbonSavings, KeepItOffer, BankDetails, CategoryPolicy
    - Store money as integer minor units with `currency`; store weight in grams (integer) for the exact 10 kg threshold
    - Encode enums: Disposition, Return_Action, Payment_Method, Seller_Type, ReturnRequest status, decisionSource
    - _Requirements: 1.6, 3.6, 8.4, 10.3, 12.4_
  - [x] 2.2 Implement the persistence/repository layer (`app/domain/repository.py`)
    - CRUD/session management over SQLite, plus atomic helpers: compare-and-set listing status, unique-constraint guards for refund-per-return and `(returnRequestId, type)` points credits, transactional redemption
    - _Requirements: 6.5, 8.6, 9.2, 10.1_
  - [x] 2.3 Implement encryption-at-rest module (`app/domain/crypto.py`)
    - Application-layer symmetric encrypt/decrypt for bank details with a local demo key; expose a non-sensitive `bankDetailsId` token; never log or echo plaintext
    - _Requirements: 18.2_
  - [x]* 2.4 Write unit tests for crypto round-trip and no-plaintext-leak
    - Verify encrypt/decrypt round-trips and that serialized/audit forms never contain plaintext values
    - _Requirements: 18.2_

- [x] 3. Seed datasets and fixture loader
  - [x] 3.1 Create all seed fixtures and a startup loader (`app/fixtures/`)
    - Product catalog (orders + items for Electronics/Home Appliances/Footwear), charities/bins, cities (served/unserved), customers (balances), demo photos keyed by photo set, Decision_Engine config constants + global config, category policy table, non-returnable blacklist + sample item, CO2_Factor config (with a "missing factor" toggle), expanded orders (`ord_1001`-`ord_1004` with payment method + seller type), Keep It demo item (`item_keepit_01`), and sample Pay-on-Delivery bank details
    - Implement a loader invoked at app startup to populate the database
    - _Requirements: 1.6, 3.3, 3.4, 3.5, 7.1, 11.1, 12.2, 14.2, 15.1, 17.3, 18.1, 19.1_

- [x] 4. Category policy and returnability module
  - [x] 4.1 Implement the policy module (`app/domain/policy.py`)
    - Encode the category policy table (window days, allowable actions, eligibility condition, returnable flag, requiresDamageProof), the non-returnable blacklist, the returnability-before-window ordering, the window boundary rule (delivery date = day 1, in-window through 23:59:59 of final day), and allowable-action restriction
    - _Requirements: 13.1, 13.2, 13.4, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9, 14.10, 14.11, 15.1, 15.3, 15.4_
  - [x]* 4.2 Write property test for return-action restriction
    - **Property 35: Return action restricted to the category allowable set**
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4**
  - [x]* 4.3 Write property test for category window boundary
    - **Property 36: Category window boundary correctness**
    - **Validates: Requirements 14.1, 14.9**
  - [x]* 4.4 Write property test for category policy enforcement
    - **Property 37: Category policy table enforcement**
    - **Validates: Requirements 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.10, 14.11**
  - [x]* 4.5 Write property test for non-returnable rejection and ordering
    - **Property 38: Non-returnable rejection and returnability-before-window ordering**
    - **Validates: Requirements 15.1, 15.2, 15.3, 15.4**

- [x] 5. Return_Initiation_Service core gates
  - [x] 5.1 Implement return initiation with the ordered gate (`app/services/return_initiation.py`)
    - POST `/returns`: enforce returnability → window+allowable action → reason+action presence → category eligibility → Valid_Return_Condition confirmation → active-return guard, in the strict order; snapshot itemCategory/purchasePrice/currency/weightGrams/paymentMethod/sellerType/returnWindowStart; record the selected Return_Action; block shipping-label generation until a disposition or Keep It acceptance exists
    - Add GET `/returns/{id}`, `GET /return-reasons` (including `MINOR_DEFECT`, `COLOR_APPEARANCE_NOT_AS_EXPECTED`), and `GET /categories/{category}/policy`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 13.5, 13.6, 16.1, 16.2_
  - [x]* 5.2 Write property test for return creation eligibility
    - **Property 1: Return creation eligibility**
    - **Validates: Requirements 1.1, 1.5**
  - [x]* 5.3 Write property test for exactly one valid return reason
    - **Property 2: Exactly one valid return reason**
    - **Validates: Requirements 1.2, 1.3**
  - [x]* 5.4 Write property test for no shipping label before disposition
    - **Property 3: No shipping label before disposition**
    - **Validates: Requirements 1.4**
  - [x]* 5.5 Write property test for item snapshot fidelity
    - **Property 4: Item snapshot fidelity**
    - **Validates: Requirements 1.6**
  - [x]* 5.6 Write property test for valid return condition confirmation
    - **Property 39: Valid return condition confirmation**
    - **Validates: Requirements 16.1, 16.2**

- [x] 6. Condition_Assessment_Service and OpenAI client
  - [x] 6.1 Implement the OpenAI vision client with STUB_MODE (`app/integrations/openai_client.py`)
    - Two call shapes (assessment: score+summary; hybrid decision: score+disposition+reasoning) at temperature 0 with a pinned model; `STUB_MODE` serves both from the photo-set fixture map and configurable decision fixtures (valid/guardrail-violating/malformed/excluded/timeout)
    - _Requirements: 2.1, 2.2_
  - [x] 6.2 Implement condition assessment (`app/services/condition_assessment.py`)
    - POST `/returns/{id}/assessment`: validate count 1-10, format in {jpeg,png,webp}, size <= 10 MB each; produce integer score 0-100 + 1-500 char summary within 30 s; return assessment-failure for unscorable photos
    - _Requirements: 2.1, 2.3, 2.4, 2.5, 2.6, 2.7_
  - [x]* 6.3 Write property test for score range and summary bounds
    - **Property 5: Score output range and summary bounds**
    - **Validates: Requirements 2.1, 2.3**
  - [x]* 6.4 Write property test for score comparability (STUB_MODE golden fixtures)
    - **Property 6: Score comparability**
    - **Validates: Requirements 2.2**
  - [x]* 6.5 Write unit tests for photo rejection edges
    - Zero photos, unsupported format, oversize file, unscorable photos
    - _Requirements: 2.4, 2.5, 2.6, 2.7_

- [x] 7. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Decision_Engine (hybrid LLM-primary with rule fallback/guardrail)
  - [x] 8.1 Implement economics computation (`app/services/decision_engine.py`)
    - Compute `Reverse_Logistics_Cost` (base handling + inspection + per-kg freight) and `Depreciated_Item_Value` (score-driven retention) in the order currency; both non-negative; value non-decreasing in score
    - _Requirements: 3.1_
  - [x]* 8.2 Write property test for economics and depreciation monotonicity
    - **Property 7: Depreciated value monotonicity and non-negative economics**
    - **Validates: Requirements 3.1**
  - [x] 8.3 Implement the deterministic rule-based engine
    - Pure function selecting exactly one disposition from score/cost/value/weight/category thresholds (Warehouse: score>=80 & value>cost; Resale: score>=80 & weight>=10kg & cost>value; Donation: 0-79 & cost>=50% value); deterministic and repeatable
    - _Requirements: 3.2, 3.3, 3.4, 3.5_
  - [x]* 8.4 Write property test for rule determinism and single final disposition
    - **Property 8: Rule-engine determinism and exactly one final disposition**
    - **Validates: Requirements 3.2**
  - [x]* 8.5 Write property test for threshold rule selection
    - **Property 9: Threshold rules select the specified disposition (rule engine)**
    - **Validates: Requirements 3.3, 3.4, 3.5**
  - [x] 8.6 Implement the hybrid decision path with guardrail, fallback, and audit
    - POST `/returns/{id}/decision`: call LLM for primary score+disposition+reasoning; always compute the rule-based shadow; apply the hard economic guardrail; fall back to the rule disposition on LLM failure/timeout/malformed/invalid/excluded output; record exactly one final disposition with `decisionSource` (LLM | RULE_FALLBACK) plus full audit (llmDisposition, ruleDisposition, llmReasoning, score, cost, value, weight, category); return `422 DECISION_FAILED` naming any missing input
    - _Requirements: 3.2, 3.6, 3.7_
  - [x]* 8.7 Write property test for decision audit completeness
    - **Property 10: Decision audit completeness**
    - **Validates: Requirements 3.6**
  - [x]* 8.8 Write property test for decision failure on missing input
    - **Property 11: Decision failure on missing input**
    - **Validates: Requirements 3.7**
  - [x]* 8.9 Write property test for always-valid final disposition
    - **Property 26: Final disposition is always valid**
    - **Validates: Requirements 3.2**
  - [x]* 8.10 Write property test for rule-fallback equivalence
    - **Property 27: Rule-fallback equivalence**
    - **Validates: Requirements 3.2, 3.3, 3.4, 3.5**
  - [x]* 8.11 Write property test for safety guardrail
    - **Property 28: Safety guardrail is never violated**
    - **Validates: Requirements 3.2**
  - [x] 8.12 Implement re-evaluation with excluded dispositions
    - Re-run the engine excluding a prior disposition (resale-window expiry, unserved city, no donation method) and never reselect an excluded disposition
    - _Requirements: 5.7, 5.8, 7.7_
  - [x]* 8.13 Write property test for re-evaluation exclusion
    - **Property 12: Re-evaluation never reselects an excluded disposition**
    - **Validates: Requirements 5.7, 5.8, 7.7**

- [x] 9. Refund_Service
  - [x] 9.1 Implement idempotent refund with retry and manual flag (`app/services/refund.py`)
    - At most one successful refund per return request, in order currency, recording amount/return request/triggering disposition; retry up to 3 attempts; reject subsequent refunds after success; flag MANUAL and notify after 3 consecutive failures
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_
  - [x]* 9.2 Write property test for refund correctness
    - **Property 20: Refund correctness**
    - **Validates: Requirements 4.4, 5.5, 7.4, 10.2, 10.3**
  - [x]* 9.3 Write property test for at-most-one refund per return request
    - **Property 21: At most one successful refund per return request**
    - **Validates: Requirements 10.1, 10.4, 10.5**
  - [x] 9.4 Implement payment-method timeline selection, PoD gate, and A-to-z refund
    - Select `expectedCompletionWindow` by Payment_Method (Amazon Pay <=2h, UPI 2-4d, Card 3-5d, Net Banking 2-10d, PoD 2-4d) using business-day rules; set timeline start per disposition (warehouse after quality check; confirmation event for Keep It/resale/donation); withhold start and enter `AWAITING_BANK_DETAILS` for PoD without valid bank details; support A-to-z platform refund = purchase price with `atozApplied`; expose GET `/returns/{id}/refund`
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8, 17.9, 17.10, 18.6, 19.4_
  - [x]* 9.5 Write property test for refund timeline selection
    - **Property 41: Refund timeline selection by payment method**
    - **Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7, 17.8**
  - [x]* 9.6 Write property test for PoD refund withheld until bank details
    - **Property 42: Pay-on-Delivery refund withheld until valid bank details**
    - **Validates: Requirements 17.10, 18.6**

- [x] 10. Green_Points_Service
  - [x] 10.1 Implement credit and balance (`app/services/green_points.py`)
    - Idempotent `credit(returnRequestId, disposition)` crediting the configured integer amount (>=1) at most once, zero for warehouse, recording disposition + return request; GET `/customers/{id}/green-points` returning an integer balance >= 0 (init 0); leave balance unchanged and retry-eligible on failure
    - _Requirements: 5.6, 7.4, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_
  - [x]* 10.2 Write property test for at-most-once points credit
    - **Property 22: Green Points credited at most once with the configured amount**
    - **Validates: Requirements 5.6, 7.4, 8.1, 8.2, 8.3, 8.5, 8.6**
  - [x]* 10.3 Write property test for non-negative integer balance
    - **Property 23: Green Points balance is always a non-negative integer**
    - **Validates: Requirements 8.4**
  - [x] 10.4 Implement atomic redemption to Amazon Pay
    - POST `/customers/{id}/green-points/redeem`: validate whole number >=1 and <= balance within 3 s; atomically convert at configured rate and deduct; reject over-balance with available balance; leave balance unchanged on Amazon Pay failure; record points, credited amount, timestamp
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  - [x]* 10.5 Write property test for redemption validity
    - **Property 24: Redemption validity**
    - **Validates: Requirements 9.1, 9.3, 9.4**
  - [x]* 10.6 Write property test for redemption atomicity
    - **Property 25: Redemption atomicity**
    - **Validates: Requirements 9.2, 9.6**

- [x] 11. Carbon_Savings_Service
  - [x] 11.1 Implement carbon-savings computation and impact message (`app/services/carbon_savings.py`)
    - Compute kg CO2 from CO2_Factor config (per-disposition + per-km*distance + per-kg*weight) >= 0 within 5 s; record 0 kg for warehouse; build the Impact_Message (money saved + kg CO2); on missing factor return a computation-failure naming the factor, record no carbon value, and display money-only
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_
  - [x] 11.2 Write property test for carbon savings formula
    - **Property 33: Carbon savings non-negative, formula-correct, zero for warehouse**
    - **Validates: Requirements 12.1, 12.2, 12.5**
  - [x] 11.3 Write property test for impact message content
    - **Property 34: Impact message contains money saved and CO2 saved**
    - **Validates: Requirements 12.3**

- [x] 12. Bank_Details_Capture_Service
  - [x] 12.1 Implement bank-details validation and encrypted capture (`app/services/bank_details.py`)
    - Validate IFSC (exactly 11 letters/digits) and account number (9-18 digits); on valid pair store encrypted within 5 s and return acceptance; on invalid reject without storing and name the failing field with expected format; reference by `bankDetailsId`, never echo plaintext
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_
  - [x] 12.2 Write property test for bank-details validation
    - **Property 43: Bank-details validation**
    - **Validates: Requirements 18.1, 18.2, 18.3, 18.4, 18.5**

- [-] 13. Keep_It_Service
  - [x] 13.1 Implement Keep It offer math and lifecycle (`app/services/keep_it.py`)
    - After scoring, before normal routing: present an offer iff reason is a Minor_Issue_Reason AND score >= keepItMinScore AND a bounded positive `Partial_Refund_Amount` exists (A>0, A<P, A<RLC, A+DIV<=RLC); display amount in order currency; on accept issue partial refund + credit Keep It points + compute carbon, with no label/logistics, recording audit (outcome, amount, customer), bounded to one refund and one credit; on decline or >=1h expiry route to Decision_Engine excluding KEEP_IT
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9, 8.3, 12.1_
  - [-] 13.2 Write property test for Keep It offer trigger conditions
  - [x]* 13.2 Write property test for Keep It offer trigger conditions
    - **Property 29: Keep It offer trigger conditions**
    - **Validates: Requirements 11.1**
  - [x]* 13.3 Write property test for partial-refund bounds and net-profit
    - **Property 30: Partial_Refund_Amount bounds and net-profit invariant**
    - **Validates: Requirements 11.2, 11.3**
  - [x]* 13.4 Write property test for bounded Keep It acceptance side-effects
    - **Property 31: Keep It acceptance side-effects (amount matches, idempotency)**
    - **Validates: Requirements 11.5, 11.9**
  - [x]* 13.5 Write property test for Keep It decline/expiry routing
    - **Property 32: Keep It decline or expiry routes to the Decision_Engine**
    - **Validates: Requirements 11.6, 11.7**

- [x] 14. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Seller authorization and A-to-z Guarantee
  - [x] 15.1 Implement FBA/FBM seller authorization (extend `app/services/return_initiation.py`)
    - On creation: FBA auto-authorizes within 5 s and arranges logistics; FBM opens a 24-48h window (status `AWAITING_SELLER_AUTH`); POST `/returns/{id}/seller-auth` authorizes within the window; on timeout apply A-to-z platform refund = purchase price via Refund_Service and record `atozApplied`, notify customer
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5_
  - [ ]* 15.2 Write property test for FBA/FBM A-to-z handling
    - **Property 44: FBA auto-authorization versus FBM A-to-z platform refund**
    - **Validates: Requirements 19.1, 19.2, 19.3, 19.4, 19.5**

- [x] 16. DOA verification gate
  - [x] 16.1 Implement the DOA gate (extend `app/services/return_initiation.py`)
    - For Mobiles Laptops & Electronics, large-appliance, or brand-requires-verification items, enter `AWAITING_DOA` and withhold approval until a brand-authorized certificate or completed technician outcome confirms DOA; POST `/returns/{id}/doa` records the outcome; non-confirming or absent verification withholds approval with a descriptive message
    - _Requirements: 16.3, 16.4, 16.5, 16.6_
  - [x]* 16.2 Write property test for the DOA verification gate
    - **Property 40: DOA verification gate**
    - **Validates: Requirements 16.3, 16.4, 16.5, 16.6**

- [x] 17. Warehouse_Return_Flow
  - [x] 17.1 Implement the warehouse flow (`app/services/warehouse_flow.py`)
    - POST `/returns/{id}/warehouse/label`: return the exact message "Standard Return Approved. Please pack the item." and generate a label within 30 s; on failure return `LABEL_GENERATION_FAILED` and keep retry-eligible; POST `/returns/{id}/warehouse/receipt`: mark received, route to Refurbished, trigger full refund on quality-check pass
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  - [x]* 17.2 Write unit tests for warehouse flow edges
    - Exact-string message, label generation/refurbished routing, label-failure retry, receipt-triggered refund
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 18. Hyperlocal_Marketplace
  - [x] 18.1 Implement the marketplace feed and concurrency-safe purchase (`app/services/marketplace.py`)
    - GET `/marketplace?city=`: city-filtered active listings within 3 s, each with item details, photos, score, discounted price, city; POST `/listings/{id}/purchase`: process payment within 30 s, atomic compare-and-set so only one of concurrent buyers wins (loser `LISTING_UNAVAILABLE`, not charged), mark SOLD and remove from feed, return pickup location + contact; on payment failure keep listing active and return `PAYMENT_FAILED`
    - _Requirements: 5.3, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_
  - [x]* 18.2 Write property test for city-scoped visibility
    - **Property 14: City-scoped marketplace visibility**
    - **Validates: Requirements 5.3, 6.1**
  - [x]* 18.3 Write property test for listing feed fields
    - **Property 15: Listing feed contains required fields**
    - **Validates: Requirements 6.2**
  - [x]* 18.4 Write property test for sold-listing removal and pickup details
    - **Property 16: Sold listing removed and pickup details provided**
    - **Validates: Requirements 6.4, 6.7**
  - [x]* 18.5 Write property test for concurrent single-winner purchase
    - **Property 17: Concurrent purchase yields a single winner**
    - **Validates: Requirements 6.5**

- [x] 19. Hyperlocal_Resale_Flow
  - [x] 19.1 Implement the resale flow (`app/services/resale_flow.py`)
    - On selection create a listing priced strictly below purchase price in the customer's city, instruct keep-at-home for a 48h window starting now, start the scheduler timer; on purchase arrange local pickup, trigger full refund, credit resale points; on window expiry or unserved city re-evaluate excluding HYPERLOCAL_RESALE
    - _Requirements: 5.1, 5.2, 5.4, 5.5, 5.6, 5.7, 5.8_
  - [x]* 19.2 Write property test for listing price and 48h window
    - **Property 13: Listing price below purchase price within a 48-hour window**
    - **Validates: Requirements 5.1, 5.2**

- [x] 20. Green_Donation_Flow
  - [x] 20.1 Implement the donation flow (`app/services/donation_flow.py`)
    - GET `/returns/{id}/donation/options`: nearest verified bin within 25 km with great-circle distance in km, plus worker-pickup; only worker pickup if no bin in range; POST `/returns/{id}/donation/pickup`: schedule within 5 business days (return date), `SCHEDULING_FAILED` keeps retry-eligible; POST `/returns/{id}/donation/confirm`: on bin drop-off or collection trigger refund + donation points; if no method available re-evaluate
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_
  - [x]* 20.2 Write property test for nearest bin within radius and distance
    - **Property 18: Nearest verified bin within radius with correct distance**
    - **Validates: Requirements 7.1, 7.2, 7.5**
  - [x]* 20.3 Write property test for worker-pickup scheduling window
    - **Property 19: Worker pickup scheduled within five business days**
    - **Validates: Requirements 7.3**

- [x] 21. Scheduler
  - [x] 21.1 Implement the background scheduler (`app/domain/scheduler.py`)
    - Async lifespan/APScheduler timers for the 48h resale-window expiry (re-evaluate), warehouse-receipt 30-day timeout (flag MANUAL), Keep It offer expiry (>=1h, route to Decision_Engine), and FBM seller-auth timeout (apply A-to-z)
    - _Requirements: 4.6, 5.7, 11.7, 19.4_
  - [x]* 21.2 Write unit tests for scheduler timers
    - Verify each timer fires the correct action at its deadline using controllable clocks
    - _Requirements: 4.6, 5.7, 11.7, 19.4_

- [x] 22. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 23. Standard return-flow orchestration and API wiring
  - [x] 23.1 Implement the ordered standard return flow (`app/services/return_flow.py`)
    - Enforce the no-skip step order (initiation -> reason -> proof submission -> return action -> pickup address -> inspection -> closure) via `flowStep`, rejecting out-of-order submissions; require routed Proof_Submission (1-10 photos + 1-1000 chars) for damaged reasons within 5 s; present only allowable actions; require Pickup_Address before scheduling for warehouse/replacement/exchange/donation-pickup; record inspection pass/fail (fail -> withhold, flag MANUAL, notify); record closure (final disposition, action fulfilled, refund outcome, carbon) within 5 s
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8, 20.9_
  - [x]* 23.2 Write property test for ordered no-skip flow
    - **Property 45: Ordered no-skip return flow**
    - **Validates: Requirements 20.1**
  - [x]* 23.3 Write property test for damaged-return proof submission
    - **Property 46: Damaged returns require routed proof submission**
    - **Validates: Requirements 20.2, 20.7**
  - [x]* 23.4 Write property test for pickup-address requirement
    - **Property 47: Pickup address required before scheduling**
    - **Validates: Requirements 20.4, 20.8**
  - [x]* 23.5 Write property test for inspection outcome handling
    - **Property 48: Inspection outcome and failure handling**
    - **Validates: Requirements 20.5, 20.9**
  - [x]* 23.6 Write property test for closure record completeness
    - **Property 49: Closure record completeness**
    - **Validates: Requirements 20.6**
  - [x] 23.7 Wire all routers into the FastAPI app (`app/main.py`)
    - Register all service routers, dependency injection (repository, scheduler, OpenAI client), startup seed loading, and the scheduler lifespan task
    - _Requirements: 1.1, 3.2, 20.1_

- [x] 24. Web frontend
  - [x] 24.1 Build the standard ordered return wizard
    - Step-ordered flow consuming the REST API, including the Keep It offer screen (accept/decline) and the carbon-savings impact card
    - _Requirements: 11.4, 12.3, 20.1, 20.2, 20.3, 20.4_
  - [x] 24.2 Build the marketplace feed UI
    - City-filtered listing feed with item details, photos, score, discounted price, city, and purchase action
    - _Requirements: 6.1, 6.2, 6.3, 6.7_
  - [x] 24.3 Build the Green Points / wallet UI
    - Display balance and redeem points to Amazon Pay
    - _Requirements: 8.4, 9.1, 9.2_

- [x] 25. Integration and demo verification
  - [x] 25.1 Wire the demo scenario orchestration and seed selection
    - Pre-wire the three demo scenarios plus Keep It, FBM A-to-z, and PoD runs end-to-end against `STUB_MODE` using the seed datasets
    - _Requirements: 3.3, 3.4, 3.5, 11.1, 17.7, 19.4_
  - [x]* 25.2 Write integration tests for the three demo scenarios
    - Electronics->Warehouse, Home Appliances->Resale, Footwear->Donation full lifecycle (initiation -> assessment -> decision -> disposition -> refund -> points)
    - _Requirements: 3.3, 3.4, 3.5, 4.4, 5.5, 7.4_
  - [x]* 25.3 Write the Keep It demo integration test
    - `item_keepit_01`: score >= threshold -> offer -> accept -> partial refund (11,697 minor) + Keep It points + no label, then carbon impact and closure
    - _Requirements: 11.1, 11.5, 12.1, 20.6_
  - [x]* 25.4 Write the FBM A-to-z integration test
    - `ord_1002`: seller-auth window elapses -> platform refund = purchase price with `atozApplied`
    - _Requirements: 19.4_
  - [x]* 25.5 Write the Pay-on-Delivery bank-details integration test
    - `ord_1003`: refund withheld -> valid IFSC + account captured (encrypted) -> NEFT refund timeline starts
    - _Requirements: 17.7, 18.1, 18.6_
  - [x]* 25.6 Write the marketplace concurrency integration test
    - Simultaneous purchase requests on one listing -> exactly one winner, loser not charged
    - _Requirements: 6.5_

- [x] 26. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional (unit, property, and integration tests) and can be skipped for a faster MVP; core implementation tasks are never optional.
- All property tests use Hypothesis with a minimum of 100 examples and are tagged `Feature: secondlife-ai, Property {number}: {property_text}`; AI/LLM-dependent properties (5, 6, 8-12, 26-28) run against `STUB_MODE` with golden/configurable fixtures.
- Each task references the specific requirement clauses and design correctness properties it implements for full traceability; Properties 1-49 are each covered by exactly one property-based test sub-task.
- Checkpoints provide incremental validation; financial, points, and concurrency invariants are validated by the highest-value PBT targets (Properties 8-12, 17, 20-28, 30-44).
- The Decision_Engine is hybrid LLM-primary; only the deterministic rule fallback/guardrail behavior is asserted by property tests, never the raw LLM choice.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "2.3"] },
    { "id": 2, "tasks": ["2.2", "2.4", "3.1"] },
    { "id": 3, "tasks": ["4.1", "6.1", "8.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "4.4", "4.5", "5.1", "6.2", "8.2", "8.3"] },
    { "id": 5, "tasks": ["5.2", "5.3", "5.4", "5.5", "5.6", "6.3", "6.4", "6.5", "8.4", "8.5", "8.6", "9.1", "10.1", "11.1", "12.1"] },
    { "id": 6, "tasks": ["8.7", "8.8", "8.9", "8.10", "8.11", "8.12", "9.2", "9.3", "9.4", "10.2", "10.3", "10.4", "11.2", "11.3", "12.2", "15.1"] },
    { "id": 7, "tasks": ["8.13", "9.5", "9.6", "10.5", "10.6", "13.1", "15.2", "16.1", "17.1", "18.1", "20.1"] },
    { "id": 8, "tasks": ["13.2", "13.3", "13.4", "13.5", "16.2", "17.2", "18.2", "18.3", "18.4", "18.5", "19.1", "20.2", "20.3", "21.1"] },
    { "id": 9, "tasks": ["19.2", "21.2", "23.1"] },
    { "id": 10, "tasks": ["23.2", "23.3", "23.4", "23.5", "23.6", "23.7"] },
    { "id": 11, "tasks": ["24.1", "24.2", "24.3"] },
    { "id": 12, "tasks": ["25.1"] },
    { "id": 13, "tasks": ["25.2", "25.3", "25.4", "25.5", "25.6"] }
  ]
}
```
