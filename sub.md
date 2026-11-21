# StoreFlow Pricing, Subscription & Transaction Billing Implementation

## Objective

Implement a complete, production-ready subscription and transaction-based billing system for StoreFlow.

StoreFlow will operate on a hybrid monetisation model consisting of:

1. Monthly subscriptions.
2. Fixed transaction fees based on completed sale amounts.
3. Different transaction fee schedules for Free, Basic and Pro subscription plans.
4. Monthly consolidated billing for subscriptions and accumulated transaction fees.

Before implementation, inspect the existing codebase, database schema, authentication, tenant/business relationships, sales lifecycle, roles and permissions, and existing billing functionality.

Do not introduce duplicate functionality or unnecessarily replace existing architecture. Extend the existing implementation using its established patterns.

---

# 1. Subscription Plans

Implement three subscription plans.

### Free

- Monthly subscription: ₦0
- Intended for small businesses and new retailers.
- Essential POS and inventory functionality.
- Basic sales and expense management.
- Basic customer management.
- Standard AI assistant allowance.
- Standard transaction fees.
- Limited operational capacity.

### Basic

- Monthly subscription: ₦7,500
- Intended for established small and medium-sized businesses.
- Everything in Free.
- Higher product and staff limits.
- Advanced inventory management.
- Credit customer management.
- Receivables and invoicing.
- More detailed business reports.
- Increased AI assistant allowance.
- Reduced transaction fees.

### Pro

- Monthly subscription: ₦19,500
- Intended for wholesalers, distributors and multi-branch businesses.
- Everything in Basic.
- Multiple branch management.
- Advanced staff permissions.
- Advanced business analytics.
- Consolidated business reporting.
- Highest AI assistant allowance.
- Higher operational limits.
- Lowest transaction fees.

These prices must be configurable by authorised administrators. Do not hardcode subscription prices or feature limits throughout the application.

Support subscription status management, upgrades, downgrades, renewals, cancellation, expiry and grace periods.

Initial billing currency: NGN.

Initial subscription billing interval: Monthly.

Keep the architecture extensible for annual billing in the future, but do not expose annual billing until it is enabled.

---

# 2. Transaction Fee Tiers

Each subscription plan must have its own transaction fee configuration.

Transaction fees are fixed amounts in NGN, determined by the total completed sale amount.

## Free Plan

| Transaction Amount | Fee |
|---|---:|
| ₦0 – ₦5,000 | ₦25 |
| ₦6,000 – ₦10,000 | ₦35 |
| ₦11,000 – ₦20,000 | ₦40 |
| ₦21,000 – ₦50,000 | ₦50 |
| ₦51,000 – ₦100,000 | ₦65 |
| ₦101,000 – ₦300,000 | ₦100 |
| ₦301,000 – ₦500,000 | ₦200 |
| ₦501,000 – ₦1,000,000 | ₦300 |
| Above ₦1,000,000 | ₦500 |

## Basic Plan

| Transaction Amount | Fee |
|---|---:|
| ₦0 – ₦5,000 | ₦20 |
| ₦6,000 – ₦10,000 | ₦30 |
| ₦11,000 – ₦20,000 | ₦35 |
| ₦21,000 – ₦50,000 | ₦40 |
| ₦51,000 – ₦100,000 | ₦50 |
| ₦101,000 – ₦300,000 | ₦80 |
| ₦301,000 – ₦500,000 | ₦160 |
| ₦501,000 – ₦1,000,000 | ₦240 |
| Above ₦1,000,000 | ₦400 |

## Pro Plan

| Transaction Amount | Fee |
|---|---:|
| ₦0 – ₦5,000 | ₦15 |
| ₦6,000 – ₦10,000 | ₦20 |
| ₦11,000 – ₦20,000 | ₦25 |
| ₦21,000 – ₦50,000 | ₦30 |
| ₦51,000 – ₦100,000 | ₦40 |
| ₦101,000 – ₦300,000 | ₦60 |
| ₦301,000 – ₦500,000 | ₦120 |
| ₦501,000 – ₦1,000,000 | ₦180 |
| Above ₦1,000,000 | ₦300 |

Important:

- Every amount range must be mutually exclusive.
- No overlapping boundaries.
- No gaps between transaction bands.
- The final band must support unlimited upper values.
- All amount comparisons must use the currency's smallest unit or an appropriate exact decimal representation. Avoid floating-point financial calculations.
- All fees must be configurable through the admin dashboard.

---

# 3. Transaction Charging Rules

StoreFlow is a retail management and business operations platform, NOT a payment processor.

StoreFlow does not process or collect customers' sales payments.

Merchants receive money directly through their existing payment channels:

- Cash.
- Bank transfer.
- External POS terminals operated by tenants.
- Split payments combining supported payment methods.

StoreFlow only records sales and payment information.

## Core billing rule

Every eligible completed sale attracts exactly one StoreFlow transaction fee, regardless of the payment method.

The fee is determined by the total sale amount, not the individual payment amount.

Examples:

A ₦100,000 sale paid through:
- ₦20,000 cash.
- ₦50,000 bank transfer.
- ₦30,000 external POS.

This must generate ONE transaction fee based on ₦100,000.

It must not generate three separate fees.

## Eligible transactions

Charge transaction fees for:

- Completed cash sales.
- Completed bank transfer sales.
- Completed external POS/card sales.
- Completed split-payment sales.
- Completed credit sales.
- Completed partially paid sales where the sale itself has been completed.

## Non-billable activities

Do not charge transaction fees for:

- Quotations.
- Unpaid draft invoices.
- Failed sales.
- Voided sales.
- Stock adjustments.
- Internal stock transfers.
- Expense records.
- Customer repayments against an existing credit sale.
- Additional instalments against an already-billed sale.
- Duplicate sale events.

A credit sale attracts its transaction fee when the sale is completed, not when the customer eventually pays.

Subsequent credit repayments must not generate additional transaction fees.

---

# 4. Refunds, Voids and Reversals

Implement proper transaction fee reversal handling.

### Full refunds

- Reverse the original transaction fee.
- Create a billing credit referencing the original fee entry.
- Do not delete the original ledger entry.

### Partial refunds

- Recalculate the applicable fee based on the retained sale value.
- Credit the difference between the original fee and the revised fee.
- Prevent duplicate refund processing.

### Voided sales

- Sales voided before completion must not attract transaction fees.
- If a completed sale is subsequently reversed, create an appropriate fee reversal entry.

### Audit requirements

Every reversal must record:

- Original sale ID.
- Original fee ledger entry.
- Refund/reversal ID.
- Amount refunded.
- Original fee.
- Reversed fee amount.
- User performing the action.
- Timestamp.
- Reason, where applicable.

Use immutable ledger entries. Never silently modify historical financial records.

---

# 5. Subscription and Pricing Architecture

Implement a configurable pricing hierarchy.

Recommended relationship:

SubscriptionPlan
- id
- name
- slug
- description
- monthly_price
- currency
- is_active
- sort_order
- created_at
- updated_at

PlanFeature
- id
- plan_id
- feature_key
- feature_value
- is_enabled
- created_at
- updated_at

TransactionFeeTier
- id
- plan_id
- pricing_version_id
- minimum_amount
- maximum_amount (nullable for unlimited)
- fee_amount
- currency
- is_active
- created_at
- updated_at

PricingVersion
- id
- name
- version_number
- effective_from
- effective_until
- status
- created_by
- created_at

MerchantSubscription
- id
- tenant_id
- plan_id
- status
- billing_interval
- current_period_start
- current_period_end
- next_billing_date
- grace_period_end
- created_at
- updated_at

BillableTransaction
- id
- tenant_id
- branch_id
- sale_id
- subscription_id
- pricing_version_id
- transaction_amount
- transaction_fee
- currency
- transaction_status
- completed_at
- created_at

TransactionFeeLedger
- id
- tenant_id
- sale_id
- billable_transaction_id
- entry_type
- amount
- currency
- billing_period_id
- reference_id
- description
- created_at

BillingPeriod
- id
- tenant_id
- period_start
- period_end
- status
- created_at

BillingInvoice
- id
- tenant_id
- billing_period_id
- invoice_number
- subscription_amount
- transaction_fee_total
- adjustment_total
- credit_total
- total_amount
- amount_paid
- amount_due
- status
- issued_at
- due_at
- paid_at
- created_at

BillingPayment
- id
- invoice_id
- tenant_id
- amount
- payment_method
- reference
- status
- paid_at
- created_at

Treat these as proposed entities. Adapt names, types and relationships to the existing database architecture.

Use appropriate indexes, foreign keys, unique constraints and database transactions.

If the existing application uses Supabase/PostgreSQL, follow its existing conventions, including migrations, RLS and RPC patterns.

---

# 6. Pricing Versioning

Implement versioned pricing configuration.

This is essential.

When an administrator changes transaction fees:

- Create a new pricing version.
- Do not overwrite historical pricing configurations.
- New completed sales use the applicable active pricing version.
- Previously completed sales retain their original transaction fees.
- Existing billing invoices must never be recalculated using new pricing.
- Support scheduled pricing changes through effective dates.

Every billable transaction must retain a snapshot of:

- Subscription plan.
- Pricing version.
- Transaction amount.
- Applicable fee band.
- Applied transaction fee.
- Currency.
- Sale completion timestamp.

---

# 7. Monthly Billing Engine

Transaction fees are accumulated and invoiced monthly.

Do not deduct transaction fees from individual customer sales.

## Billing process

1. Merchant records completed sales.
2. StoreFlow determines the applicable subscription plan.
3. StoreFlow loads the relevant pricing version.
4. StoreFlow determines the correct transaction fee band.
5. StoreFlow creates a billable transaction.
6. StoreFlow records the fee in the transaction fee ledger.
7. All eligible fees accumulate throughout the billing period.
8. At the end of the calendar month, generate a consolidated billing invoice.
9. Include the applicable subscription charge.
10. Include accumulated transaction fees.
11. Apply eligible refunds and billing credits.
12. Generate the final amount payable.

## Billing schedule

- Billing period: Calendar month.
- Invoice generation: First day of the following month.
- Payment deadline: Seven days after invoice generation.
- Grace period: Seven additional days.
- Subscription and transaction fees must be itemised separately.

The billing system must support:

- Invoice generation.
- Invoice numbering.
- Payment recording.
- Payment reconciliation.
- Partial payments, if supported by the existing billing architecture.
- Outstanding balances.
- Failed payments.
- Overdue invoices.
- Billing reminders.
- Invoice history.
- Downloadable invoices and receipts.

If a tenant changes plans during a billing period, define and implement a consistent proration policy for subscription charges. Transaction fees must follow the plan effective when each sale was completed.

Do not silently charge tenants through a gateway. Use the existing supported tenant billing/payment arrangement, or provide an auditable manual payment and reconciliation workflow if automated collection is not configured.

---

# 8. Multi-Branch Billing

A tenant account may own multiple branches.

Implement billing at the tenant/business account level.

- Each branch records its own sales.
- Each eligible sale generates its own transaction fee.
- All branch fees accumulate under the tenant's billing account.
- Generate one consolidated monthly invoice for the tenant.
- Preserve branch-level transaction references.
- Allow administrators and authorised tenants to view fees by branch.

Do not create separate subscriptions for each branch unless the existing business model explicitly requires it.

---

# 9. Admin Dashboard

Add a Billing & Pricing management section to the existing admin dashboard.

Only authorised SUPER_ADMIN users should be able to modify global pricing.

## Pricing management

Administrators should be able to:

- View Free, Basic and Pro plans.
- Create and edit subscription plans.
- Configure monthly subscription prices.
- Configure plan descriptions.
- Enable or disable plans.
- Configure plan features and limits.
- Add, edit and remove transaction fee bands.
- Configure separate transaction fees for each plan.
- Configure effective dates.
- Create new pricing versions.
- Schedule pricing changes.
- Preview the effects of pricing changes.
- View historical pricing configurations.

## Transaction fee editor

Build a clear interface displaying:

Plan name | Minimum amount | Maximum amount | Transaction fee

Allow administrators to:

- Add new bands.
- Edit existing bands.
- Change fees.
- Validate ranges.
- Prevent overlapping ranges.
- Detect gaps.
- Prevent invalid negative amounts.
- Ensure the last band is unlimited.
- Save a new pricing version.

Validate the entire pricing configuration before activation.

## Billing management

Administrators should be able to:

- View all tenant subscriptions.
- View tenants by subscription plan.
- View monthly transaction fee revenue.
- View outstanding invoices.
- View overdue invoices.
- View paid invoices.
- View transaction fee breakdowns.
- View billing histories.
- View refunds and adjustments.
- Record verified manual payments.
- Apply authorised billing adjustments.
- Export billing reports.

Every sensitive administrative action must be auditable.

---

# 10. Merchant Dashboard

Add a Billing & Subscription section to the tenant application.

Merchants should be able to see:

### Current plan

- Plan name.
- Monthly subscription price.
- Subscription status.
- Renewal date.
- Available features.
- Plan usage and limits.

### Transaction fee usage

- Number of billable transactions this month.
- Total transaction value.
- Total transaction fees.
- Breakdown by fee band.
- Breakdown by branch.
- Breakdown by payment method.
- Refund credits.
- Estimated monthly bill.

### Billing history

- Previous invoices.
- Invoice status.
- Amount paid.
- Outstanding amount.
- Due date.
- Payment history.
- Downloadable invoices and receipts.

### Upgrade experience

Allow tenants to compare Free, Basic and Pro.

Show the transaction fee differences between their current plan and other plans.

Clearly explain the potential savings from upgrading, based on actual usage where possible.

Do not display misleading projected savings.

---

# 11. Billing Notifications

Implement notifications for:

- Subscription renewal approaching.
- Monthly invoice generated.
- Invoice approaching its due date.
- Invoice overdue.
- Payment successfully recorded.
- Payment reconciliation completed.
- Subscription upgraded.
- Subscription downgraded.
- Subscription expired.
- Grace period ending.

Use the existing notification infrastructure.

Support in-app notifications and existing configured communication channels.

Avoid duplicate notifications.

---

# 12. Security and Reliability

This module handles financial records and must be treated as a critical financial subsystem.

Implement:

- Server-side fee calculation.
- Strict role-based access control.
- Merchant-level data isolation.
- Database-level constraints.
- Idempotent transaction fee creation.
- Idempotent invoice generation.
- Immutable financial ledger.
- Auditable billing adjustments.
- Atomic database transactions.
- Safe concurrency handling.
- Proper handling of failed background jobs.
- Retryable invoice generation.
- Consistent monetary precision.
- Comprehensive logging.

Never trust transaction fee amounts supplied by the client application.

The backend must calculate the fee independently.

Do not allow tenants to modify their own transaction fee records, invoice totals or subscription prices.

If Supabase is used, ensure RLS policies protect all tenant financial records and that privileged operations are performed through secure backend services or appropriately protected database functions.

---

# 13. Testing Requirements

Write automated tests covering:

### Fee calculation

- Every transaction band.
- Every subscription plan.
- Exact band boundaries.
- Values immediately above and below boundaries.
- Transactions above ₦1,000,000.
- Invalid negative amounts.
- Zero-value sales.

### Sales lifecycle

- Completed cash sale.
- Completed transfer sale.
- Completed external POS sale.
- Split payment sale.
- Credit sale.
- Partial payment.
- Customer instalment repayment.
- Voided sale.
- Full refund.
- Partial refund.
- Duplicate sale events.

### Subscription lifecycle

- Free subscription.
- Basic upgrade.
- Pro upgrade.
- Downgrade.
- Subscription expiry.
- Renewal.
- Grace period.
- Pricing change during a billing period.

### Billing

- Monthly invoice generation.
- Subscription charge calculation.
- Transaction fee aggregation.
- Refund credits.
- Partial invoice payment.
- Duplicate invoice prevention.
- Payment reconciliation.
- Outstanding balances.
- Multi-branch aggregation.

### Security

- Merchant cannot view another tenant's billing.
- Merchant cannot alter transaction fees.
- Cashier cannot change subscription pricing.
- Only authorised administrators can configure global pricing.
- Financial ledger records cannot be silently deleted or modified.

---

# 14. Implementation Approach

Work in stages:

1. Audit the existing application and identify relevant models, services, sales events, roles and database structures.
2. Propose the database changes and relationships.
3. Implement subscription plans and pricing configuration.
4. Implement the transaction fee calculation service.
5. Integrate fee creation with the existing completed-sale lifecycle.
6. Implement the immutable transaction fee ledger.
7. Implement refund and reversal handling.
8. Implement monthly billing and invoice generation.
9. Implement the admin pricing and billing interface.
10. Implement the tenant subscription and billing interface.
11. Implement notifications.
12. Add automated tests.
13. Run migrations and verify existing functionality.
14. Provide an implementation summary.

Do not build everything in one oversized service.

Use clean domain boundaries and separate responsibilities for:

- Subscription management.
- Pricing management.
- Transaction fee calculation.
- Transaction fee ledger.
- Billing period management.
- Invoice generation.
- Payment reconciliation.
- Notifications.

Reuse existing application services where appropriate.

Do not break the existing POS, inventory, sales, reporting, authentication or tenant workflows.

---

# 15. Expected Deliverables

At completion, provide:

1. Summary of existing architecture discovered.
2. Database schema and migrations.
3. Subscription and pricing services.
4. Transaction fee calculation service.
5. Billing ledger implementation.
6. Monthly invoicing implementation.
7. Admin dashboard changes.
8. Merchant dashboard changes.
9. API endpoints or server functions introduced.
10. RLS and security policies.
11. Automated test results.
12. Any remaining integration requirements.
13. A list of assumptions and decisions requiring product-owner approval.

## Final Product Principle

StoreFlow is a retail management platform, not a payment gateway.

Merchants receive their money directly through cash, bank transfers and their own external POS terminals.

StoreFlow records sales and payment methods, manages business operations and charges tenants through:

- Monthly subscription fees.
- Fixed transaction recording fees based on completed sale amounts.

The transaction fee is charged once per eligible sale, regardless of how many payment methods were used.

All transaction fees are accumulated and billed monthly.

The pricing system must remain configurable, versioned, auditable and scalable without requiring code changes whenever an administrator adjusts commercial pricing.

# 16. Wallet system
- Create a Wallet system for tenants. So wallet should be created for all tenants when they register. Wallet can be funded(flutterwave) so we have the option of removing settlement from the wallet or tenants clear manually as it currently is
- all refund should be created and fee add to wallet