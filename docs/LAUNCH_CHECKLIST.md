# Launch checklist

Tick everything before you announce the store. Items marked **(you)** need business information or
decisions only you or your advisers can provide.

## A. Business content and tax (before go-live)

- [ ] **(you + CA)** Store settings: legal name, trade name, GSTIN, PAN, registered address, state —
      exactly as on the GST certificate.
- [ ] **(CA)** One tax class per HSN + rate; each one ticked *Confirmed by CA*. Delete the seeded
      placeholder classes.
- [ ] **(CA)** Confirm: prices include GST (Y/N), GST on delivery charges (principal-item rate /
      fixed / none), invoice timing (on dispatch / on payment), invoice prefix, round-off, and whether
      e-invoicing applies.
- [ ] **(you)** Replace every seeded product: real names, descriptions, photos you own, MRP, prices,
      opening stock, weights. Remove or archive the placeholders.
- [ ] **(you)** No unverified claims (e.g. "100% natural", certifications, health benefits) unless you
      can prove them.
- [ ] **(you)** Shipping charge, free-shipping threshold, serviceable PIN codes, delivery estimate.
- [ ] **(you)** COD on/off, COD minimum and maximum, COD fee.
- [ ] **(you)** Cancellation rule and return window.
- [ ] **(you + lawyer)** About, FAQ, Shipping, Cancellation & refund, Privacy and Terms pages: every
      `[PLACEHOLDER]` replaced; Privacy and Terms reviewed.
- [ ] **(you)** Logo uploaded; homepage banner updated; support email and phone set.

## B. Acceptance tests on the live site

- [ ] Site loads on `https://www.yourdomain.in` with a padlock; `http://` redirects to `https://`.
- [ ] Mobile phone and desktop: home, shop, category, search, product, cart, checkout all usable.
- [ ] Register with a real email → OTP email arrives → verified.
- [ ] Log out → log in with password; log in with an email code; forgot-password email works once only.
- [ ] Add to cart as a guest, log in, and the cart is kept.
- [ ] Coupon applies; an expired coupon is refused.
- [ ] **COD order**: confirmation email; stock reduced; dashboard shows it; mark Processing →
      Shipped (tracking entered) → invoice issued and emailed with PDF → COD collected → Delivered.
- [ ] Check the invoice PDF against your CA's sample: numbers, GSTIN, HSN, CGST/SGST for an
      in-state address, IGST for an out-of-state address, place of supply, totals.
- [ ] **Online order (Razorpay test mode)**: success, failure + retry, abandoned payment released by
      the hourly task (PAYMENTS_RAZORPAY.md §5).
- [ ] Customer cancels an unpaid order → stock restored; paid order → request appears for staff.
- [ ] Return: request → approve → complete with and without restock; credit note created.
- [ ] Logged in as customer A, open customer B's order and invoice URLs → "Page not found".
- [ ] Staff account in *Order Staff* can't see reports or refund; *Inventory Staff* can't see orders.
- [ ] Reports load and CSV downloads open in Excel.
- [ ] Upload a product image and a short MP4; a renamed `.php` or fake image is rejected.
- [ ] Contact form message arrives at `ADMIN_ALERT_EMAILS`.
- [ ] Low-stock alert email arrives when a product drops to its threshold.
- [ ] Hourly scheduled task runs (Tasks tab log shows output, no errors).
- [ ] Database backup created **and a restore tested**.
- [ ] `/sitemap.xml` and `/robots.txt` load; submit the sitemap in Google Search Console.

## C. Payments go-live

- [ ] Razorpay KYC approved.
- [ ] Live keys and live webhook secret set; live webhook created.
- [ ] One small real payment made and refunded successfully.

## D. After launch (first weeks)

- [ ] Check the dashboard daily for orders to ship, COD to collect, requests and refunds due.
- [ ] Weekly: Payment reconciliation report vs Razorpay settlements; email log failures; backups.
- [ ] Monthly: invoice register + tax summary CSV to your CA for GST returns.
- [ ] Raise `SECURE_HSTS_SECONDS` to `31536000` once HTTPS has been stable for a week.
- [ ] Then: Google Play app ([PLAY_STORE.md](PLAY_STORE.md)), later Amazon ([AMAZON_PHASE2.md](AMAZON_PHASE2.md)).
