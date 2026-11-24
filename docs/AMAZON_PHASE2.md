# Amazon marketplace — phase 2 design

Not part of the website launch. The groundwork is in place, but the integration stays **disabled**
(`AMAZON_SP_API_ENABLED=False`).

## Already prepared

- `Order.channel` (`website` / `amazon`) and `Order.external_order_id`, with a database constraint
  so each Amazon order can be imported only once.
- `marketplace.SkuMapping`: maps your variant SKU to the Amazon seller SKU / ASIN and marks whether
  to push stock.
- `apps/marketplace/amazon.py`: the client interface with the methods to implement.
- Reports already group sales by channel.

## What you'll need from Amazon

1. An Amazon Seller Central (India) account in good standing, with Professional selling.
2. Registration as a **developer** in Seller Central (Solution Provider Portal / "Develop Apps"),
   with roles for orders, inventory/listings, finances and reports. Amazon reviews this, including
   data-protection questions.
3. A private SP-API application with Login-with-Amazon (LWA) **client ID / client secret** and a
   **refresh token** from self-authorising the app. These go in environment variables, never in code.

## Planned behaviour

| Area | Plan |
|---|---|
| Order import | Scheduled job pulls new orders (Orders API) → creates `Order(channel="amazon", external_order_id=AmazonOrderId)` with items mapped through `SkuMapping`. Duplicate imports are rejected by the constraint. Unmapped SKUs are logged for review. |
| Stock | Amazon orders deduct website stock through the same ledger (`reason=sale`, idempotency key `amazon:<order>:<line>`). Mapped variants' stock is pushed back to Amazon after every change, rate-limited. |
| Invoices | **No website invoice is issued for Amazon orders.** Amazon or your Seller Central invoicing settings decide who issues it. Store Amazon's invoice number and a reference to its PDF on the order. A website order number is never an Amazon invoice number. |
| Accounting | Keep separate entries for gross sales, Amazon fees, GST on fees, TCS deducted, refunds and settlement payouts (Finances API / settlement reports). Settlement ≠ sales. |
| Reconciliation | Report matching imported orders ↔ Amazon invoice numbers ↔ settlements, with unmatched items flagged. |
| Fallback | CSV import of Amazon order reports and CSV export of SKU stock, using the same mapping and duplicate checks. |
| Reliability | Every sync run logged; failed runs retried with back-off; no partial imports (one transaction per order). |

Expect roughly 2–3 weeks of development and testing once credentials are approved.
