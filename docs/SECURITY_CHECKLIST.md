# Security & privacy checklist

## Controls built into the code

| Area | Control | Where |
|---|---|---|
| Secrets | `SECRET_KEY`, DB, SMTP and Razorpay keys only from env/`.env`; prod refuses weak key or `*` hosts | `config/settings/` |
| Transport | HTTPS redirect, HSTS, secure + HttpOnly session cookie, secure CSRF cookie, proxy SSL header | `settings/prod.py` |
| Passwords | Django PBKDF2 hashing, length/common/numeric/similarity validators | `settings/base.py` |
| OTP | `secrets` RNG, HMAC-SHA256 storage, 5-minute expiry, attempt limit, resend cooldown, single use, older codes invalidated, never logged or stored in the email log | `apps/accounts/otp.py` |
| Enumeration | Register, OTP login and password reset respond identically for unknown emails | `apps/accounts/views.py` |
| Rate limits | Login, register, OTP send/verify, password reset, coupons, contact, tracking, payment callback | `apps/core/utils.rate_limited` |
| Sessions | Key rotated on login; password change/reset signs out other sessions | Django auth |
| CSRF | On every form; webhook exempt but HMAC-verified on the raw body | `apps/payments/views.py` |
| Authorization | Every customer query filtered by `user=request.user` (other users' orders, addresses, invoices return 404); staff pages check `is_staff` + per-action permissions | views |
| Price tampering | Prices, discounts, shipping and GST recomputed on the server; browser totals ignored | `apps/cart/pricing.py` |
| Overselling | Variant rows locked (`select_for_update`) and stock deducted inside the order transaction | `apps/orders/services.py` |
| Duplicates | Unique checkout token, idempotent stock ledger keys, unique webhook event ids, one invoice per order | models |
| Payments | Signature verification (constant time), server fetch of amount/currency/status, no card data stored | `apps/payments/` |
| Uploads | Extension allow-list, size limits, real content check (Pillow / video signature), random filenames, served as static files only, staff-only | `apps/core/validators.py` |
| Files | Invoices generated on demand behind login + ownership check, `Cache-Control: no-store` | `apps/invoicing/views.py` |
| XSS | Django auto-escaping; policy pages rendered as escaped text; JSON-LD escaped | templates |
| SQL injection | ORM only, no raw SQL | — |
| CSV injection | Cells starting with `= + - @` prefixed in exports | `apps/dashboard/views.py` |
| Clickjacking | `X-Frame-Options: DENY` | settings |
| Audit | Append-only `AuditLog` for status changes, cancellations, refunds, invoices, stock adjustments, tax class changes | `apps/core/models.py` |
| Admin | Non-default admin URL (`ADMIN_URL_PATH`), role groups (`setup_roles`) | — |
| Errors | `DEBUG=False`; custom 403/404/500; 500 page has no database dependency | `apps/core/views.py` |

## Before launch

- [ ] `python manage.py check --deploy` with prod settings shows only the HSTS notes.
- [ ] `.env` on the server is `chmod 600`; no secrets in GitHub (`git log -p | grep -i secret` is clean).
- [ ] `ADMIN_URL_PATH` changed from the default.
- [ ] Every staff account uses a strong, unique password; staff are in the narrowest group that fits.
- [ ] Superuser accounts limited to the owner.
- [ ] Razorpay webhook secret set; test webhook delivers HTTP 200.
- [ ] Backup and restore tested (DEPLOYMENT §19).
- [ ] Dependencies current: `pip list --outdated`. Apply Django security releases (5.2.x) promptly.

## Monthly

- [ ] Update dependencies, run tests, deploy.
- [ ] Review Admin → Audit logs and staff accounts; remove people who left.
- [ ] Check Admin → Email logs for failures and the Web tab error log.
- [ ] Download a backup copy off the server.

## Customer data (privacy)

| Data | Why it's collected | Where it's kept | Retention |
|---|---|---|---|
| Name, email, mobile | Account, order updates, delivery | `accounts.User` | While the account is active |
| Addresses | Delivery and billing | `accounts.Address`, copied into each order | Account life; order copies kept with tax records |
| GSTIN (optional) | B2B tax invoice | Order and invoice | Tax-record period |
| Orders, invoices, credit notes | Fulfilment, GST law, accounting | `orders`, `invoicing` | At least as long as the GST Act requires records to be kept (confirm the period with your CA) |
| Payment references | Reconciliation, refunds | `payments` (IDs only, never card data) | Same as orders |
| OTP records | Login and verification | Hashed, deleted ~1 day after expiry | Automatic |
| Email log | Delivery troubleshooting | Non-sensitive bodies only | Clear entries older than 12 months |
| Contact messages | Customer support | `core.ContactMessage` | Delete after resolving and ~12 months |
| Server logs | Security and errors | `logs/app.log` (rotating, 5 × 5 MB) | Automatic rotation |

Practices:
- Only collect what's needed. Mobile is optional at registration; GSTIN is optional at checkout.
- Share data only with processors needed to run the store (Razorpay, courier, email provider,
  PythonAnywhere). Name them in the privacy policy.
- Database dumps contain personal data: store them privately and delete old ones.
- To close a customer's account on request: deactivate the user (*Active* off) and remove saved
  addresses. Keep orders and invoices for the legal retention period.
- Have the privacy policy reviewed against India's Digital Personal Data Protection Act and its rules
  as they apply to your business.
