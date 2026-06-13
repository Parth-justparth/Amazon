# Requirements Document

## Introduction

Amazon SecondLife AI is a return-interception system that determines the optimal disposition of a returned e-commerce item before a shipping label is generated. Reverse logistics (shipping returns back to a warehouse, inspecting, and repackaging) frequently costs more than a returned item's depreciated value, producing a net financial loss. SecondLife AI intercepts the return at initiation, assesses the item's condition from customer-uploaded photos to produce a comparable SecondLife Score (0-100), runs a real-time unit-economics check comparing reverse-logistics cost against depreciated item value, and routes the item to one of three platform dispositions: Return to Warehouse, Hyperlocal Resale, or Green Donation.

The system additionally provides a hyperlocal second-hand marketplace where buyers in the same city can browse and purchase intercepted returns, a charity donation flow with verified drop-off bins and worker pickup, and a Green Points loyalty system that rewards customers for choosing resale or donation outcomes with points convertible to Amazon Pay.

This release extends the platform in three areas. First, a "Keep It" partial-refund offer lets a customer keep an item affected by a minor issue (a minor defect or color/appearance not as expected) in exchange for a bounded partial refund, avoiding reverse logistics entirely while keeping the company in net profit. Second, a carbon-savings impact display computes and shows the CO2 emissions avoided for every non-warehouse resolution. Third, the platform is aligned to the Amazon.in return and refund policy: customer-selectable return actions (Refund, Replacement, Exchange), category-specific return windows and allowable actions, a non-returnable item blacklist, valid-return condition and DOA verification rules, payment-method-specific refund timelines with secure bank-detail capture for Pay-on-Delivery, FBA versus FBM seller handling with A-to-z Guarantee protection, and a standard return user flow. The SecondLife dispositions are the mechanism through which the platform fulfills or intercepts these customer-facing return actions.

The reference implementation targets a FastAPI backend with OpenAI-based image assessment and a web frontend demonstrating the dispositions across the Electronics, Home Appliances, and Footwear categories.

## Glossary

- **SecondLife_System**: The complete return-interception platform that orchestrates condition assessment, disposition routing, marketplace, donation, refunds, and rewards.
- **Return_Initiation_Service**: The component that starts a return request for a previously purchased item and collects the return reason, return action, and proof.
- **Condition_Assessment_Service**: The AI component that analyzes customer-uploaded photos and produces a SecondLife Score.
- **SecondLife_Score**: An integer from 0 to 100 representing assessed item condition, where higher values indicate better condition. Comparable across items.
- **Decision_Engine**: The component that selects one platform Disposition outcome using unit economics, item weight, condition score, and item category.
- **Reverse_Logistics_Cost**: The estimated total cost to ship, inspect, and repackage a returned item at the warehouse, expressed in the order currency.
- **Depreciated_Item_Value**: The current estimated resale value of the returned item after depreciation, expressed in the order currency.
- **Disposition**: The selected platform return outcome; one of Return to Warehouse, Hyperlocal Resale, Green Donation, or Keep It.
- **Warehouse_Return_Flow**: The disposition flow that ships an item back to an Amazon warehouse for official Refurbished resale.
- **Hyperlocal_Resale_Flow**: The disposition flow that lists an item on the hyperlocal marketplace for local buyers while the customer keeps the item at home.
- **Green_Donation_Flow**: The disposition flow that routes an item to a verified charity via drop-off bin or worker pickup.
- **Keep_It_Offer**: An alternative disposition offered for returns driven by a minor issue, in which the Returning_Customer keeps the item and receives a bounded Partial_Refund instead of shipping the item back.
- **Partial_Refund**: A refund that is strictly less than the recorded purchase price, issued when a Returning_Customer accepts a Keep_It_Offer.
- **Partial_Refund_Amount**: The monetary value of a Partial_Refund, computed from configurable factors and bounded so the company remains in net profit.
- **Minor_Issue_Reason**: A return reason indicating either a minor defect or color/appearance not as expected, for which a Keep_It_Offer may be presented.
- **Carbon_Savings_Service**: The component that computes and records the CO2 emissions avoided by a non-warehouse resolution.
- **Carbon_Savings**: The estimated mass of CO2 emissions avoided by resolving a return without reverse logistics or a replacement shipment, expressed in kilograms of CO2.
- **CO2_Factor**: A configurable factor used to compute Carbon_Savings, defined per disposition, per distance, and per item weight.
- **Impact_Message**: A customer-facing message stating the money saved and the Carbon_Savings for a resolution.
- **Return_Action**: The customer-selected resolution type for a return request; one of Refund, Replacement, or Exchange.
- **Hyperlocal_Marketplace**: The second-hand marketplace feed where buyers in the same city browse and purchase intercepted returns.
- **Marketplace_Listing**: A purchasable entry for an intercepted return on the Hyperlocal_Marketplace, including item details, photos, SecondLife Score, discounted price, and city.
- **Local_Buyer**: A registered user in the same city as the returning customer who can browse and purchase a Marketplace_Listing.
- **Returning_Customer**: The customer who initiated the return.
- **Charity_Bin**: A verified physical donation drop-off location associated with a charity.
- **Green_Points**: Loyalty reward points awarded for choosing Keep It, resale, or donation dispositions, redeemable for Amazon Pay balance.
- **Green_Points_Service**: The component that awards, tracks, and redeems Green Points.
- **Amazon_Pay**: The payment balance system into which Green Points can be converted and into which certain refunds may be issued.
- **Refund_Service**: The component that issues monetary refunds to the Returning_Customer.
- **Item_Category**: The product classification used by the Decision_Engine and return-policy rules; supported demonstration values include Electronics, Home Appliances, and Footwear, and policy values include Mobiles Laptops & Electronics, Clothing & Footwear, Books, Home & Kitchen Appliances, Grocery & Perishables, Beauty & Personal Care, and Software Video Games & Music.
- **Category_Return_Window**: The category-specific number of calendar days, measured from the delivery date, within which a return request for an item of that Item_Category is eligible.
- **Return_Window_Start**: The delivery date of the order, from which a Category_Return_Window is measured.
- **Item_Returnability**: A boolean is_returnable flag indicating whether an item may be returned at all, independent of the Category_Return_Window.
- **Allowable_Return_Action_Set**: The set of Return_Actions permitted for an Item_Category by policy.
- **Valid_Return_Condition**: The required physical condition for an approved return, including original packaging, tags, warranty cards, manuals, and accessories intact.
- **DOA_Verification**: A Dead-on-Arrival verification, satisfied by a certificate from a brand-authorized service center or a scheduled technician visit, required for certain Item_Categories before a return or replacement is approved.
- **Seller_Type**: The fulfillment classification of an order; one of FBA (Fulfilled by Amazon) or FBM (Fulfilled by Merchant).
- **A_to_z_Guarantee**: The buyer-protection mechanism that forces a platform-mandated refund when an FBM seller fails to authorize or resolve a valid return within the seller authorization window.
- **Payment_Method**: The original payment instrument used for the order; one of Amazon Pay Balance, UPI, Credit/Debit Card, Net Banking, or Pay on Delivery.
- **Bank_Details_Capture_Service**: The component that securely collects and stores the IFSC code and bank account number required to refund Pay-on-Delivery orders.
- **Proof_Submission**: The customer-provided photos and damage details attached to a return request and used by the Condition_Assessment_Service.
- **Pickup_Address**: The address selected by the Returning_Customer from which an item is collected for return, replacement, exchange, or donation.

## Requirements

### Requirement 1: Return Initiation

**User Story:** As a returning customer, I want to start a return for a purchased item and state my reason, so that the system can assess and route the item before a shipping label is created.

#### Acceptance Criteria

1. WHEN a customer requests a return for a purchased item whose Item_Returnability is true AND that is within the Category_Return_Window for the item's Item_Category measured from the Return_Window_Start AND that has no existing active return request, THE Return_Initiation_Service SHALL create a return request associated with that item and order within 5 seconds.
2. WHEN a customer creates a return request, THE Return_Initiation_Service SHALL require the customer to select exactly one return reason from a defined list of return reasons.
3. IF a customer attempts to create a return request without selecting exactly one return reason from the defined list of return reasons, THEN THE Return_Initiation_Service SHALL reject the request, SHALL NOT create the return request, and SHALL return a message indicating that a return reason is required.
4. THE Return_Initiation_Service SHALL prevent generation of a shipping label for a return request until the Decision_Engine has selected a Disposition for that return request.
5. IF a customer requests a return for an item whose Item_Returnability is false OR that is outside the Category_Return_Window for the item's Item_Category OR that already has an active return request, THEN THE Return_Initiation_Service SHALL reject the request, SHALL NOT create a return request, and SHALL return a descriptive reason identifying why the item is ineligible.
6. WHEN a return request is created, THE Return_Initiation_Service SHALL record the Item_Category, purchase price, order currency, Payment_Method, Seller_Type, and Return_Window_Start for use by downstream components.

### Requirement 2: Photo Upload and AI Condition Scoring

**User Story:** As a returning customer, I want to upload photos of the item so that the system can assess its condition and produce a comparable quality score.

#### Acceptance Criteria

1. WHEN a customer submits between 1 and 10 photos inclusive for a return request, THE Condition_Assessment_Service SHALL analyze the photos and produce an integer SecondLife_Score between 0 and 100 inclusive within 30 seconds.
2. THE Condition_Assessment_Service SHALL produce SecondLife_Scores using a consistent scoring method such that two items in equivalent condition receive SecondLife_Scores within 5 points of each other, so that scores are comparable across items and categories.
3. WHEN the Condition_Assessment_Service produces a SecondLife_Score, THE Condition_Assessment_Service SHALL record a textual condition summary of between 1 and 500 characters describing observed defects or wear, and WHERE no defects or wear are observed THE Condition_Assessment_Service SHALL record a condition summary stating that no defects were observed.
4. IF a customer submits no photos for a return request, THEN THE Condition_Assessment_Service SHALL reject the assessment request, SHALL NOT produce a SecondLife_Score, and SHALL return a message requesting at least one photo.
5. IF a submitted file is not a supported image format, THEN THE Condition_Assessment_Service SHALL reject that file, SHALL NOT produce a SecondLife_Score, and SHALL return a descriptive error identifying the unsupported format.
6. IF a submitted file exceeds 10 megabytes, THEN THE Condition_Assessment_Service SHALL reject that file, SHALL NOT produce a SecondLife_Score, and SHALL return a descriptive error indicating that the file exceeds the maximum allowed size.
7. IF the Condition_Assessment_Service cannot determine a SecondLife_Score from the submitted photos, THEN THE Condition_Assessment_Service SHALL return an assessment-failure status, SHALL NOT produce a SecondLife_Score, and SHALL request re-upload of clearer photos.

### Requirement 3: Financial and Logistics Decision Engine

**User Story:** As an operations stakeholder, I want the system to choose the most economical disposition based on item economics and condition, so that the platform avoids losses on reverse logistics.

#### Acceptance Criteria

1. WHEN a SecondLife_Score is available for a return request, THE Decision_Engine SHALL compute the Reverse_Logistics_Cost and the Depreciated_Item_Value for the item, each expressed in the order currency.
2. THE Decision_Engine SHALL select exactly one Disposition for each return request using the SecondLife_Score, Reverse_Logistics_Cost, Depreciated_Item_Value, item weight, and Item_Category.
3. WHERE the SecondLife_Score is greater than or equal to 80 AND the Depreciated_Item_Value exceeds the Reverse_Logistics_Cost, THE Decision_Engine SHALL select the Warehouse_Return_Flow.
4. WHERE the SecondLife_Score is greater than or equal to 80 AND the item weight is greater than or equal to 10 kilograms AND the Reverse_Logistics_Cost exceeds the Depreciated_Item_Value, THE Decision_Engine SHALL select the Hyperlocal_Resale_Flow.
5. WHERE the SecondLife_Score is between 0 and 79 inclusive AND the Reverse_Logistics_Cost is greater than or equal to 50 percent of the Depreciated_Item_Value, THE Decision_Engine SHALL select the Green_Donation_Flow.
6. WHEN the Decision_Engine selects a Disposition, THE Decision_Engine SHALL record the selected Disposition, the SecondLife_Score, the Reverse_Logistics_Cost, the Depreciated_Item_Value, the item weight, and the Item_Category used to make the decision.
7. IF any of the SecondLife_Score, Reverse_Logistics_Cost, Depreciated_Item_Value, item weight, or Item_Category required for a return request is unavailable, THEN THE Decision_Engine SHALL return a decision-failure status identifying the missing input AND SHALL NOT select a Disposition.

### Requirement 4: Return to Warehouse Disposition

**User Story:** As a returning customer with a high-value pristine item, I want a standard return to the warehouse, so that the item can be refurbished and resold officially.

#### Acceptance Criteria

1. WHEN the Decision_Engine selects the Warehouse_Return_Flow, THE SecondLife_System SHALL display the message "Standard Return Approved. Please pack the item."
2. WHEN the Warehouse_Return_Flow is selected, THE Warehouse_Return_Flow SHALL generate a shipping label for return of the item to an Amazon warehouse within 30 seconds.
3. WHEN an item processed through the Warehouse_Return_Flow is received at the warehouse, THE Warehouse_Return_Flow SHALL route the item to official Amazon Refurbished resale.
4. WHEN the Warehouse_Return_Flow confirms warehouse receipt of the item AND the returned item passes quality check, THE Refund_Service SHALL issue a refund equal to the recorded purchase price to the Returning_Customer.
5. IF generation of the shipping label fails, THEN THE Warehouse_Return_Flow SHALL retain the return request in a state eligible for a subsequent label-generation attempt AND SHALL return a descriptive error indicating that the label could not be generated.
6. IF the Warehouse_Return_Flow does not confirm warehouse receipt of the item within 30 calendar days of shipping-label generation, THEN THE Warehouse_Return_Flow SHALL flag the return request for manual resolution.

### Requirement 5: Hyperlocal Resale Disposition

**User Story:** As a returning customer with a bulky like-new item, I want the item resold to a local neighbor while I keep it at home, so that costly return shipping is avoided and I still get refunded.

#### Acceptance Criteria

1. WHEN the Decision_Engine selects the Hyperlocal_Resale_Flow, THE Hyperlocal_Resale_Flow SHALL create a Marketplace_Listing on the Hyperlocal_Marketplace at a discounted price strictly below the item's original purchase price for buyers in the Returning_Customer's city.
2. WHEN the Hyperlocal_Resale_Flow is selected, THE Hyperlocal_Resale_Flow SHALL instruct the Returning_Customer to keep the item at home for a 48-hour resale window beginning at the time the Marketplace_Listing is created.
3. WHILE the 48-hour resale window is active, THE Hyperlocal_Marketplace SHALL allow Local_Buyers in the same city to view and purchase the Marketplace_Listing.
4. WHEN a Local_Buyer purchases the Marketplace_Listing, THE Hyperlocal_Resale_Flow SHALL arrange local pickup of the item by the Local_Buyer from the Returning_Customer.
5. WHEN a Local_Buyer completes purchase of the Marketplace_Listing, THE Refund_Service SHALL issue a full refund equal to the original purchase price in the order currency to the Returning_Customer.
6. WHEN a Local_Buyer completes purchase of the Marketplace_Listing, THE Green_Points_Service SHALL award Green_Points to the Returning_Customer.
7. IF the 48-hour resale window expires without a purchase, THEN THE SecondLife_System SHALL re-evaluate the return request through the Decision_Engine for an alternative Disposition other than the Hyperlocal_Resale_Flow.
8. IF the Returning_Customer's city is not served by the Hyperlocal_Marketplace, THEN THE SecondLife_System SHALL re-evaluate the return request through the Decision_Engine for an alternative Disposition other than the Hyperlocal_Resale_Flow.

### Requirement 6: Hyperlocal Resale Marketplace

**User Story:** As a local buyer, I want to browse and purchase discounted intercepted returns in my city, so that I can buy quality second-hand items nearby.

#### Acceptance Criteria

1. WHEN a Local_Buyer requests the marketplace feed, THE Hyperlocal_Marketplace SHALL display Marketplace_Listings located in the Local_Buyer's city within 3 seconds.
2. THE Hyperlocal_Marketplace SHALL display for each Marketplace_Listing the item details, item photos, SecondLife_Score, discounted price, and city.
3. WHEN a Local_Buyer selects a Marketplace_Listing to purchase, THE Hyperlocal_Marketplace SHALL process payment for the discounted price within 30 seconds.
4. WHEN payment for a Marketplace_Listing succeeds, THE Hyperlocal_Marketplace SHALL mark the Marketplace_Listing as sold AND SHALL remove the Marketplace_Listing from the active feed.
5. IF two Local_Buyers attempt to purchase the same Marketplace_Listing, THEN THE Hyperlocal_Marketplace SHALL complete the purchase for only one Local_Buyer, SHALL decline the other purchase with a descriptive unavailability message, and SHALL NOT charge the declined Local_Buyer.
6. IF payment for a Marketplace_Listing fails, THEN THE Hyperlocal_Marketplace SHALL retain the Marketplace_Listing as available in the active feed AND SHALL return a descriptive payment-failure message to the Local_Buyer.
7. WHEN a Marketplace_Listing is sold, THE Hyperlocal_Marketplace SHALL provide local pickup details, including the pickup location and pickup contact information, to the purchasing Local_Buyer.

### Requirement 7: Green Donation Disposition

**User Story:** As a returning customer with a low-value worn item, I want to donate it to a nearby charity, so that I get refunded and rewarded without unprofitable return shipping.

#### Acceptance Criteria

1. WHEN the Decision_Engine selects the Green_Donation_Flow, THE Green_Donation_Flow SHALL present the nearest verified Charity_Bin located within a 25-kilometer radius of the Returning_Customer AND SHALL present the option of charity worker pickup.
2. WHEN the Green_Donation_Flow presents donation options, THE Green_Donation_Flow SHALL display the distance to the nearest verified Charity_Bin expressed in kilometers.
3. WHEN a customer selects charity worker pickup, THE Green_Donation_Flow SHALL schedule a charity worker pickup of the item from the Returning_Customer within 5 business days AND SHALL display the scheduled pickup date to the Returning_Customer.
4. WHEN the Green_Donation_Flow confirms drop-off of the item at a verified Charity_Bin or confirms charity worker collection, THE Refund_Service SHALL issue a refund to the Returning_Customer AND THE Green_Points_Service SHALL award Green_Points to the Returning_Customer.
5. IF no verified Charity_Bin is available within a 25-kilometer radius of the Returning_Customer, THEN THE Green_Donation_Flow SHALL offer charity worker pickup as the donation method.
6. IF charity worker pickup scheduling fails, THEN THE Green_Donation_Flow SHALL return a descriptive scheduling-failure message AND SHALL retain the return request in a state eligible for a subsequent pickup-scheduling attempt.
7. IF neither a verified Charity_Bin within a 25-kilometer radius nor charity worker pickup is available, THEN THE Green_Donation_Flow SHALL re-evaluate the return request through the Decision_Engine for an alternative Disposition.

### Requirement 8: Green Points Earning

**User Story:** As a returning customer, I want to earn Green Points when I choose Keep It, resale, or donation outcomes, so that I am rewarded for sustainable choices.

#### Acceptance Criteria

1. WHEN the Green_Donation_Flow confirms drop-off of the item at a verified Charity_Bin or confirms charity worker collection for a return request, THE Green_Points_Service SHALL credit the configured donation Green_Points amount, which SHALL be an integer of at least 1, to the Returning_Customer's balance.
2. WHEN a Local_Buyer completes purchase of the Marketplace_Listing for a return request routed through the Hyperlocal_Resale_Flow, THE Green_Points_Service SHALL credit the configured resale Green_Points amount, which SHALL be an integer of at least 1, to the Returning_Customer's balance.
3. WHEN a Returning_Customer accepts a Keep_It_Offer for a return request, THE Green_Points_Service SHALL credit the configured Keep It Green_Points amount, which SHALL be an integer of at least 1, to the Returning_Customer's balance.
4. WHEN Green_Points are credited, THE Green_Points_Service SHALL record the disposition and return request that generated the Green_Points.
5. THE Green_Points_Service SHALL maintain a current Green_Points balance for each customer as an integer greater than or equal to 0, initialized to 0 before any Green_Points are credited.
6. WHERE a disposition is the Warehouse_Return_Flow, THE Green_Points_Service SHALL credit zero Green_Points for that return request.
7. THE Green_Points_Service SHALL credit Green_Points at most once per return request.
8. IF a Green_Points credit attempt fails, THEN THE Green_Points_Service SHALL leave the Returning_Customer's balance unchanged AND SHALL retain the return request in a state eligible for a subsequent credit attempt.

### Requirement 9: Green Points Redemption to Amazon Pay

**User Story:** As a customer, I want to convert my Green Points into Amazon Pay balance, so that I can use my rewards on purchases.

#### Acceptance Criteria

1. WHEN a customer requests redemption of a Green_Points amount, THE Green_Points_Service SHALL verify within 3 seconds that the requested amount is a whole number of at least 1 Green_Point and does not exceed the customer's current Green_Points balance.
2. WHEN a redemption request is valid, THE Green_Points_Service SHALL convert the redeemed Green_Points into Amazon_Pay balance using the configured conversion rate AND SHALL deduct the redeemed Green_Points from the customer's balance as a single atomic operation such that both the conversion and the deduction either complete together or neither is applied.
3. IF a customer requests redemption of a Green_Points amount that exceeds the current Green_Points balance, THEN THE Green_Points_Service SHALL reject the redemption, retain the customer's Green_Points balance unchanged, and return a message stating the available balance.
4. IF a customer requests redemption of a Green_Points amount that is zero, negative, or not a whole number, THEN THE Green_Points_Service SHALL reject the redemption, retain the customer's Green_Points balance unchanged, and return an error message indicating the requested amount is invalid.
5. IF crediting the Amazon_Pay balance fails during a redemption, THEN THE Green_Points_Service SHALL retain the customer's Green_Points balance unchanged AND SHALL return an error message indicating the redemption could not be completed.
6. WHEN a redemption completes, THE Green_Points_Service SHALL record the redeemed Green_Points amount, the credited Amazon_Pay amount, and the timestamp of completion.

### Requirement 10: Refund Handling

**User Story:** As a returning customer, I want my refund issued at the correct point in each disposition flow, so that I am paid accurately and only once per return.

#### Acceptance Criteria

1. THE Refund_Service SHALL issue no more than one successful refund per return request.
2. WHEN the Refund_Service issues a refund, THE Refund_Service SHALL issue the refund in the order currency of the original purchase.
3. WHEN the Refund_Service issues a refund, THE Refund_Service SHALL record the refunded amount, the return request, and the triggering Disposition.
4. IF a refund attempt fails, THEN THE Refund_Service SHALL record the failure, SHALL retain the return request in a state eligible for a subsequent refund attempt, and SHALL retry the refund up to a maximum of 3 attempts per return request.
5. IF a refund has already been successfully issued for a return request, THEN THE Refund_Service SHALL reject any subsequent refund request for that return request, SHALL return a descriptive message indicating that a refund was already issued, and SHALL leave the previously refunded amount unchanged.
6. IF refund attempts for a return request fail 3 consecutive times, THEN THE Refund_Service SHALL flag the return request for manual resolution AND SHALL notify the Returning_Customer that the refund could not be completed.

### Requirement 11: Keep It Partial-Refund Offer

**User Story:** As a returning customer with a minor issue, I want the option to keep the item for a partial refund, so that I avoid the hassle of shipping it back while the company avoids a costly return.

#### Acceptance Criteria

1. WHEN the recorded return reason for a return request is a Minor_Issue_Reason AND the SecondLife_Score is greater than or equal to the configured Keep It minimum score threshold, which SHALL be an integer between 0 and 100 inclusive, THE SecondLife_System SHALL present a Keep_It_Offer to the Returning_Customer within 5 seconds.
2. THE SecondLife_System SHALL compute the Partial_Refund_Amount such that the Partial_Refund_Amount is greater than 0, strictly less than the recorded purchase price, AND strictly less than the Reverse_Logistics_Cost that a standard return would otherwise incur.
3. THE SecondLife_System SHALL compute the Partial_Refund_Amount from configurable factors such that the sum of the Partial_Refund_Amount and the retained Depreciated_Item_Value of the item not returned is less than or equal to the Reverse_Logistics_Cost that a standard return would otherwise incur, so that the company remains in net profit.
4. WHEN the SecondLife_System presents a Keep_It_Offer, THE SecondLife_System SHALL display the Partial_Refund_Amount in the order currency within 3 seconds.
5. WHEN the Returning_Customer accepts the Keep_It_Offer, THE Refund_Service SHALL issue a Partial_Refund equal to the Partial_Refund_Amount to the Returning_Customer, THE Green_Points_Service SHALL credit the configured Keep It Green_Points amount, and THE SecondLife_System SHALL NOT generate a shipping label and SHALL NOT initiate return logistics for that return request.
6. WHEN the Returning_Customer declines the Keep_It_Offer, THE SecondLife_System SHALL route the return request to the Decision_Engine for selection of a Disposition among the Warehouse_Return_Flow, Hyperlocal_Resale_Flow, or Green_Donation_Flow.
7. IF the Returning_Customer neither accepts nor declines the Keep_It_Offer within a configured response window of at least 1 hour, THEN THE SecondLife_System SHALL treat the Keep_It_Offer as declined AND SHALL route the return request to the Decision_Engine for selection of a Disposition among the Warehouse_Return_Flow, Hyperlocal_Resale_Flow, or Green_Donation_Flow.
8. WHEN a Returning_Customer accepts a Keep_It_Offer, THE SecondLife_System SHALL record the Keep It outcome, the Partial_Refund_Amount, and the accepting Returning_Customer in the audit trail for the return request.
9. THE SecondLife_System SHALL ensure that acceptance of a Keep_It_Offer results in at most one successful Partial_Refund and at most one Green_Points credit per return request, consistent with Requirement 10 and Requirement 8.

### Requirement 12: Carbon-Savings Impact Display

**User Story:** As a returning customer, I want to see the environmental impact of my resolution, so that I understand the carbon emissions my choice avoided.

#### Acceptance Criteria

1. WHEN a return request reaches a confirmed resolution through the Keep_It_Offer, the Hyperlocal_Resale_Flow, or the Green_Donation_Flow, THE Carbon_Savings_Service SHALL compute the Carbon_Savings expressed in kilograms of CO2 as a value greater than or equal to 0 within 5 seconds.
2. THE Carbon_Savings_Service SHALL compute the Carbon_Savings from the configurable CO2_Factor values defined per disposition, per distance, and per item weight.
3. WHEN the Carbon_Savings_Service computes the Carbon_Savings for a resolution, THE SecondLife_System SHALL display an Impact_Message to the Returning_Customer within 3 seconds stating both the money saved expressed in the order currency and the Carbon_Savings expressed in kilograms of CO2.
4. WHEN the Carbon_Savings_Service computes the Carbon_Savings for a return request, THE SecondLife_System SHALL record the Carbon_Savings with the return request for reporting.
5. WHERE a return request is resolved through the Warehouse_Return_Flow, THE Carbon_Savings_Service SHALL record a Carbon_Savings of 0 kilograms of CO2 for that return request.
6. IF any CO2_Factor value required to compute the Carbon_Savings for a return request is unavailable, THEN THE Carbon_Savings_Service SHALL return a computation-failure status identifying the missing CO2_Factor, SHALL NOT record a Carbon_Savings value, AND THE SecondLife_System SHALL display the money saved expressed in the order currency without a Carbon_Savings value.

### Requirement 13: Customer-Selectable Return Actions

**User Story:** As a returning customer, I want to choose how my return is resolved, so that I receive the outcome that best fits my situation.

#### Acceptance Criteria

1. WHEN a customer creates a return request for an item whose Item_Category permits a return, THE Return_Initiation_Service SHALL require the customer to select exactly one Return_Action from the Allowable_Return_Action_Set for that Item_Category.
2. THE Return_Initiation_Service SHALL restrict the selectable Return_Action values to Refund, Replacement, and Exchange.
3. IF a customer attempts to create a return request without selecting exactly one Return_Action, THEN THE Return_Initiation_Service SHALL reject the request, SHALL NOT create the return request, and SHALL return a message indicating that exactly one Return_Action is required.
4. IF a customer selects a Return_Action that is not in the Allowable_Return_Action_Set for the item's Item_Category, THEN THE Return_Initiation_Service SHALL reject the request, SHALL NOT create the return request, and SHALL return a message identifying the allowable Return_Actions for that Item_Category.
5. WHEN a return request records a selected Return_Action, THE SecondLife_System SHALL fulfill that Return_Action through the platform Disposition selected by the Decision_Engine or accepted by the Returning_Customer, consistent with the recorded Return_Action.
6. WHEN a return request is created, THE Return_Initiation_Service SHALL record the selected Return_Action with the return request within 5 seconds.

### Requirement 14: Category-Specific Return Windows and Allowable Actions

**User Story:** As an operations stakeholder, I want category-specific return rules, so that the platform conforms to the Amazon.in return policy per product category.

#### Acceptance Criteria

1. THE Return_Initiation_Service SHALL measure every Category_Return_Window from the Return_Window_Start, which is the delivery date of the order, counting the delivery date as day 1, such that a return request submitted on or before 23:59:59 of the final calendar day of the Category_Return_Window is within the window.
2. WHERE the Item_Category is Mobiles Laptops & Electronics, THE Return_Initiation_Service SHALL apply a Category_Return_Window of 7 calendar days, SHALL restrict the Allowable_Return_Action_Set to Replacement, and SHALL permit a return only when the return reason indicates the item is defective or damaged.
3. WHERE the Item_Category is Clothing & Footwear, THE Return_Initiation_Service SHALL apply a Category_Return_Window of 30 calendar days, SHALL set the Allowable_Return_Action_Set to Refund and Exchange, and SHALL permit a return only when the item is recorded as unworn and unwashed with tags intact.
4. WHERE the Item_Category is Books, THE Return_Initiation_Service SHALL apply a Category_Return_Window of 7 calendar days, SHALL restrict the Allowable_Return_Action_Set to Replacement, and SHALL permit a return only when the item is recorded as unused and undamaged.
5. WHERE the Item_Category is Home & Kitchen Appliances, THE Return_Initiation_Service SHALL apply a Category_Return_Window of 10 calendar days, SHALL restrict the Allowable_Return_Action_Set to Replacement, and SHALL require an unboxing video or technician verification for a damage claim before approving the return.
6. WHERE the Item_Category is Grocery & Perishables, THE Return_Initiation_Service SHALL set the Item_Returnability to false AND SHALL permit a Refund only when the item is recorded as spoiled or damaged on arrival.
7. WHERE the Item_Category is Beauty & Personal Care, THE Return_Initiation_Service SHALL set the Item_Returnability to false AND SHALL permit a Refund or Replacement only when the item is recorded as the wrong item or an expired item delivered.
8. WHERE the Item_Category is Software Video Games & Music, THE Return_Initiation_Service SHALL set the Item_Returnability to false AND SHALL reject any return request for that Item_Category with a message indicating that digital or open media items are non-returnable.
9. IF a return request is submitted after the Category_Return_Window for the item's Item_Category has elapsed, THEN THE Return_Initiation_Service SHALL reject the request, SHALL NOT create a return request, and SHALL return a message indicating that the return window has elapsed.
10. IF a return request does not meet the category eligibility condition for the item's Item_Category, THEN THE Return_Initiation_Service SHALL reject the request, SHALL NOT create a return request, and SHALL return a message identifying the unmet eligibility condition.
11. IF the Item_Category is Home & Kitchen Appliances AND a damage claim is made without an unboxing video or technician verification, THEN THE Return_Initiation_Service SHALL reject the damage claim AND SHALL return a message indicating that an unboxing video or technician verification is required.

### Requirement 15: Non-Returnable Item Handling

**User Story:** As an operations stakeholder, I want non-returnable items rejected at initiation, so that the platform does not accept returns prohibited by policy.

#### Acceptance Criteria

1. THE Return_Initiation_Service SHALL set the Item_Returnability to false for any item whose product classification is in the non-returnable blacklist, which comprises innerwear, lingerie, and swimwear; customized or personalized products; gift cards and digital software downloads; and pet food and live plants.
2. IF a customer requests a return for an item whose Item_Returnability is false, THEN THE Return_Initiation_Service SHALL reject the request within 5 seconds, SHALL NOT create a return request, SHALL NOT generate a shipping label, and SHALL return a reason indicating that the item is non-returnable.
3. WHEN evaluating a return request, THE Return_Initiation_Service SHALL evaluate Item_Returnability before evaluating the Category_Return_Window for that return request.
4. IF the Item_Returnability for a requested return is false, THEN THE Return_Initiation_Service SHALL NOT evaluate the Category_Return_Window for that return request.

### Requirement 16: Valid Return Conditions and DOA Verification

**User Story:** As an operations stakeholder, I want returns validated against condition and verification rules, so that only eligible items are approved for return or replacement.

#### Acceptance Criteria

1. WHEN a customer creates a return request, THE Return_Initiation_Service SHALL require confirmation that the item meets the Valid_Return_Condition, comprising original packaging, tags, warranty cards, manuals, and accessories intact.
2. IF a return request does not confirm the Valid_Return_Condition, THEN THE Return_Initiation_Service SHALL reject the request, SHALL NOT create a return request, and SHALL return a message identifying which of the original packaging, tags, warranty cards, manuals, or accessories elements were not confirmed.
3. WHERE the Item_Category is Mobiles Laptops & Electronics OR the item is recorded with a large-appliance attribute OR the item brand is recorded as requiring brand-authorized verification, THE SecondLife_System SHALL require DOA_Verification before approving a return or replacement for that return request.
4. WHEN DOA_Verification is required, THE SecondLife_System SHALL treat the DOA_Verification as satisfied only upon recording either a certificate from a brand-authorized service center that attests the item's dead-on-arrival status OR the recorded outcome of a completed scheduled technician visit that confirms the item's dead-on-arrival status.
5. IF DOA_Verification is required and a submitted certificate or completed technician-visit outcome does not confirm the item's dead-on-arrival status, THEN THE SecondLife_System SHALL treat the DOA_Verification as not satisfied, SHALL withhold approval of the return or replacement, and SHALL return a message indicating that the item did not pass DOA_Verification.
6. IF DOA_Verification is required and no certificate or completed technician-visit outcome has been recorded for the return request, THEN THE SecondLife_System SHALL withhold approval of the return or replacement, retain the return request in its pending state, AND SHALL return a message indicating that DOA_Verification is required.

### Requirement 17: Refund Processing Timelines by Payment Method

**User Story:** As a returning customer, I want my refund processed within the timeline for my payment method, so that I know when to expect my money.

#### Acceptance Criteria

1. WHEN the Refund_Service initiates a refund for a return request resolved through the Warehouse_Return_Flow, THE Refund_Service SHALL begin the refund timeline after the returned item passes the quality check at the fulfillment center.
2. WHEN the Refund_Service initiates a refund for a return request resolved through the Keep_It_Offer, the Hyperlocal_Resale_Flow, or the Green_Donation_Flow, THE Refund_Service SHALL begin the refund timeline at the corresponding confirmation event for that disposition.
3. WHERE the Payment_Method is Amazon Pay Balance, THE Refund_Service SHALL issue the refund to the Amazon_Pay wallet within 2 hours of the start of the refund timeline.
4. WHERE the Payment_Method is UPI, THE Refund_Service SHALL issue the refund to the linked bank account within 2 to 4 business days of the start of the refund timeline, where a business day excludes weekends and public holidays at the fulfillment center location.
5. WHERE the Payment_Method is Credit/Debit Card, THE Refund_Service SHALL issue the refund to the source card within 3 to 5 business days of the start of the refund timeline, where a business day excludes weekends and public holidays at the fulfillment center location.
6. WHERE the Payment_Method is Net Banking, THE Refund_Service SHALL issue the refund to the source bank account within 2 to 10 business days of the start of the refund timeline, where a business day excludes weekends and public holidays at the fulfillment center location.
7. WHERE the Payment_Method is Pay on Delivery, THE Refund_Service SHALL issue the refund by NEFT to the customer-provided bank account or to the Amazon_Pay balance within 2 to 4 business days of the start of the refund timeline, where a business day excludes weekends and public holidays at the fulfillment center location.
8. WHEN the refund timeline begins, THE Refund_Service SHALL notify the customer of the expected refund completion window corresponding to the customer's Payment_Method.
9. IF the Refund_Service fails to issue a refund within the timeline for the customer's Payment_Method, THEN THE Refund_Service SHALL retain the refund amount as owed to the customer, notify the customer with an indication that the refund could not be completed, and retry issuance up to a maximum of 3 attempts.
10. IF the Payment_Method is Pay on Delivery and the customer has not provided valid bank account details, THEN THE Refund_Service SHALL withhold the start of the refund timeline AND SHALL notify the customer with an indication that bank account details are required.

### Requirement 18: Secure Bank-Details Capture for Pay-on-Delivery Refunds

**User Story:** As a returning customer who paid on delivery, I want to securely provide my bank details, so that my cash refund can be transferred to my account.

#### Acceptance Criteria

1. WHERE the Payment_Method is Pay on Delivery AND the customer selects an NEFT refund, THE Bank_Details_Capture_Service SHALL require the customer to input an IFSC code of exactly 11 characters and a bank account number of 9 to 18 digits before the refund is issued.
2. WHEN the Bank_Details_Capture_Service receives an IFSC code and bank account number that pass format validation, THE Bank_Details_Capture_Service SHALL store both values using encrypted storage within 5 seconds of submission.
3. IF the submitted IFSC code is not exactly 11 characters, or contains characters outside letters and digits, THEN THE Bank_Details_Capture_Service SHALL reject the submission, SHALL NOT store the submitted values, and SHALL return an error message indicating that the IFSC code field failed and stating the expected 11-character format.
4. IF the submitted bank account number is fewer than 9 or more than 18 digits, or contains non-digit characters, THEN THE Bank_Details_Capture_Service SHALL reject the submission, SHALL NOT store the submitted values, and SHALL return an error message indicating that the bank account number field failed and stating the expected 9-to-18-digit format.
5. WHEN the Bank_Details_Capture_Service successfully stores the IFSC code and bank account number, THE Bank_Details_Capture_Service SHALL return a confirmation indicating that the bank details were accepted.
6. IF a Pay-on-Delivery refund is requested before valid bank details are captured, THEN THE Refund_Service SHALL withhold the refund AND SHALL return a message indicating that bank details are required.

### Requirement 19: Seller Type Handling and A-to-z Guarantee

**User Story:** As a returning customer, I want returns handled correctly regardless of who fulfills the order, so that I am protected even when a third-party seller is slow to respond.

#### Acceptance Criteria

1. WHEN a return request is created AND the Seller_Type is FBA, THE SecondLife_System SHALL provide customer service, SHALL automatically authorize the return within 5 seconds, AND SHALL arrange return logistics for the return request.
2. WHERE the Seller_Type is FBM, THE SecondLife_System SHALL allow the third-party seller a seller authorization window of 24 to 48 hours, beginning at the time the return request is submitted to the seller, to authorize a manual return for the return request.
3. WHEN the Seller_Type is FBM AND the seller authorizes a valid return within the seller authorization window, THE SecondLife_System SHALL arrange return logistics for the return request.
4. IF the Seller_Type is FBM AND the seller does not authorize or resolve a valid return within the seller authorization window, THEN THE SecondLife_System SHALL apply the A_to_z_Guarantee, SHALL issue a platform-mandated refund equal to the recorded purchase price in the order currency to the Returning_Customer, AND SHALL notify the Returning_Customer.
5. WHEN the A_to_z_Guarantee is applied to a return request, THE SecondLife_System SHALL record that the refund was issued under the A_to_z_Guarantee.

### Requirement 20: Standard Return User Flow

**User Story:** As a returning customer, I want a clear step-by-step return flow, so that I can complete my return without confusion.

#### Acceptance Criteria

1. WHEN a Returning_Customer initiates a return, THE SecondLife_System SHALL guide the Returning_Customer through the ordered steps of return initiation, return-reason selection, Proof_Submission, Return_Action selection, Pickup_Address selection, inspection, and closure, and SHALL NOT advance to a step until the preceding step is completed.
2. WHEN the return reason indicates a damaged item, THE SecondLife_System SHALL require Proof_Submission comprising between 1 and 10 photos inclusive and damage details of between 1 and 1000 characters, AND SHALL route the Proof_Submission to the Condition_Assessment_Service within 5 seconds.
3. WHEN the Returning_Customer reaches the Return_Action selection step, THE SecondLife_System SHALL present only the Return_Actions in the Allowable_Return_Action_Set for the item's Item_Category.
4. WHEN the return is resolved through the Warehouse_Return_Flow, OR the selected Return_Action is Replacement or Exchange, OR the return is resolved through Green_Donation_Flow worker pickup, THE SecondLife_System SHALL require the Returning_Customer to select a Pickup_Address before scheduling pickup.
5. WHEN inspection of a return request is completed at pickup or at the warehouse, THE SecondLife_System SHALL record the inspection outcome as pass or fail AND SHALL advance the return request to closure.
6. WHEN a return request reaches closure, THE SecondLife_System SHALL record the final Disposition, the Return_Action fulfilled, the refund outcome, and the Carbon_Savings for the return request within 5 seconds.
7. IF the return reason indicates a damaged item and the required Proof_Submission is not provided, THEN THE SecondLife_System SHALL reject the return request AND SHALL return a message indicating that photos and damage details are required.
8. IF a return requires collection of the item and no Pickup_Address has been selected, THEN THE SecondLife_System SHALL withhold pickup scheduling AND SHALL return a message indicating that a Pickup_Address is required.
9. IF the inspection outcome of a return request is fail, THEN THE SecondLife_System SHALL withhold approval of the refund or replacement, SHALL flag the return request for manual resolution, AND SHALL notify the Returning_Customer.
