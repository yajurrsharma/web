# Life Line Dynamics — E-commerce Website

A complete Django store for devotional products (agarbatti, dhoop, sambrani cups, ghee diya batti):
catalogue, customer accounts with email OTP, cart and coupons, checkout with Cash on Delivery and
Razorpay, stock ledger, GST tax invoices and credit notes (PDF), transactional emails, a staff
dashboard with reports, and an installable web app ready for the Google Play Store.

| | |
|---|---|
| Stack | Python 3.10–3.13, Django 5.2 LTS, Bootstrap 5, ReportLab, Razorpay SDK |
| Database | SQLite on your PC · MySQL on PythonAnywhere |
| Tests | 158 automated tests (`python manage.py test`) |

> **Before taking real orders**, work through [docs/LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md).
> All sample prices, HSN codes and GST rates are placeholders that you and your CA must replace.

---

## 1. What's included

**Customers** — home, shop, categories, search/filter/sort, product pages with image/video gallery and
options, cart, coupons, checkout (saved addresses, separate billing address, optional GSTIN), online
payment or COD, order confirmation, My Account (orders, addresses, profile, password), order
tracking (also without login), invoice download, cancel and return requests, policy pages, contact
form, GST/business details page, custom 403/404/500 pages, offline page.

**Staff** — Django admin at `/manage-lld/` for products, variants, gallery, categories, tax classes,
coupons, banners, pages, users and store settings. The staff dashboard at `/dashboard/` handles orders
(status, tracking, COD collection, invoices, refunds, credit notes, cancellations, return inspection),
inventory adjustments, and reports with CSV export: sales, products, order status, payment
reconciliation, invoice register, GST tax summary, refunds, stock movements.

**Behind the scenes** — server-side pricing and GST, row-locked stock, idempotent webhooks,
gap-free invoice numbering, append-only audit, stock and invoice records, rate limiting, hashed OTPs.

Full design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 2. Run it on your Windows PC

Install **Python 3.12 or 3.13** from python.org (tick *Add python.exe to PATH*) and **Git**. Then, in
PowerShell inside the project folder:

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt

copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
# open .env in Notepad and paste the output after DJANGO_SECRET_KEY=

python manage.py migrate
python manage.py setup_roles          # staff permission groups
python manage.py seed_store           # placeholder catalogue, policy pages, banner
python manage.py createsuperuser      # your admin login (email + name + password)
python manage.py runserver
```

- Store: http://127.0.0.1:8000/
- Staff dashboard: http://127.0.0.1:8000/dashboard/
- Admin: http://127.0.0.1:8000/manage-lld/

In development, **emails (including OTP codes) are printed in the PowerShell window** instead of
being sent. That is the documented development mode, and nothing is actually emailed.

If PowerShell blocks `venv\Scripts\activate`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## 3. Run the tests

```powershell
python manage.py test
```

The tests use an in-memory database, dummy keys and mocked Razorpay responses. They never contact
Razorpay or send email.

## 4. First-time setup in the admin

1. **Store settings** (`/manage-lld/` → Core → Store settings): store name, contact details, legal
   name, GSTIN, registered address and state, invoice prefix, shipping charges and free-shipping
   threshold, COD rules, return window. Tax invoices stay blocked until GSTIN, legal name, address and
   state are filled in.
2. **Tax classes** (Products → Tax classes): one per HSN/rate combination your CA confirms. Tick
   *Confirmed by CA* on each. The dashboard warns you about any that aren't confirmed.
3. **Categories → Products**: add a product, pick its tax class, then add one or more *variants*
   (SKU, MRP, selling price, opening stock, weight) and gallery images or short videos. Set status to
   **Published** to show it. Use **Preview** ("View on site") to check drafts.
4. **Staff users**: create the user, tick *Staff status*, and add them to a group: *Store Manager*,
   *Order Staff*, *Inventory Staff* or *Accounts*.
5. **Pages**: edit About, FAQ and the policies. Every `[PLACEHOLDER]` must be replaced, and the
   privacy and terms pages reviewed by a legal professional.

### Adding products — what each field means

| Field | Notes |
|---|---|
| MRP / Selling price | Selling price ≤ MRP. Prices include GST by default (Store settings → *prices include GST*). |
| Cost price | Visible only to staff with *Can see cost prices*. Never shown on the store. |
| Stock | Set as opening stock when you create the variant. After that, change it only from **Dashboard → Inventory**, so every change is recorded. |
| Images | JPG, PNG or WebP, up to 10 MB on upload (`MAX_IMAGE_UPLOAD_SIZE`). Content is verified, not just the extension. Every photo is automatically resized to max 1600 px and compressed (~150–300 KB stored), so phone photos can be uploaded directly without using much hosting space. Up to 12 gallery photos per product (`MAX_PHOTOS_PER_PRODUCT`). |
| Videos | MP4 or WebM, max **10 seconds** (`MAX_VIDEO_SECONDS`) and 10 MB (`MAX_VIDEO_UPLOAD_SIZE`), up to 2 per product (`MAX_VIDEOS_PER_PRODUCT`). The length is read from the file itself. Videos are not resized on the server, so record at 720p if a clip is too large. For longer videos, paste a YouTube link instead; hosting storage is limited. |

## 5. Email (SMTP)

Set these in `.env` (or on the server):

```
EMAIL_HOST=smtp.your-provider.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=Life Line Dynamics <orders@yourdomain.in>
ADMIN_ALERT_EMAILS=you@yourdomain.in
```

Remove `EMAIL_BACKEND` from `.env` (or set it to `django.core.mail.backends.smtp.EmailBackend`) to
send real emails locally. Every email is logged under Admin → Email logs. Failed ones are retried by
the scheduled task. OTP and password-reset emails are never stored or retried.

Options: Gmail/Google Workspace with an *app password*, or a transactional provider such as Zoho
Mail, Brevo or Amazon SES. See [docs/DEPLOYMENT_PYTHONANYWHERE.md](docs/DEPLOYMENT_PYTHONANYWHERE.md#14-email)
for hosting limits.

## 6. Payments (Razorpay test mode)

Online payment appears at checkout only when `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` are set.
Start with **test keys** (`rzp_test_…`): no real money moves, and a yellow "Test mode" bar shows on
the payment page. Cash on Delivery works with or without Razorpay.

Step-by-step setup, webhooks, testing and going live: [docs/PAYMENTS_RAZORPAY.md](docs/PAYMENTS_RAZORPAY.md).

## 7. Invoices

- When an invoice is issued depends on Store settings → *Invoice trigger*: **when shipped** (default),
  when online payment is confirmed, or only manually.
- Numbers look like `LLD/2627/00001` (prefix / financial year / sequence). They are consecutive and
  unique per April–March year, within the 16-character GST limit.
- Customers download invoices from *My orders*. Staff view them from the order page or the invoice
  register report.
- Issued invoices can't be edited or deleted. Cancellations and returns create **credit notes**
  (`LLDCN/2627/00001`). Partial credit notes can be issued from the order page.
- E-invoicing (IRN/QR) is **not** included. It applies to B2B supplies once your turnover crosses the
  notified limit — ask your CA whether it applies to you.

## 8. Deploying and updating

Full guide (PythonAnywhere, MySQL, domain, HTTPS, scheduled tasks, backups):
[docs/DEPLOYMENT_PYTHONANYWHERE.md](docs/DEPLOYMENT_PYTHONANYWHERE.md).

Updating the live site after you push to GitHub:

```bash
cd ~/lifeline && git pull
workon lifeline-venv
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
# then click Reload on the PythonAnywhere Web tab
```

## 9. More documentation

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Design, data model, decisions |
| [DEPLOYMENT_PYTHONANYWHERE.md](docs/DEPLOYMENT_PYTHONANYWHERE.md) | GitHub → PythonAnywhere, step by step |
| [PAYMENTS_RAZORPAY.md](docs/PAYMENTS_RAZORPAY.md) | Razorpay account, keys, webhooks, testing, going live |
| [SECURITY_CHECKLIST.md](docs/SECURITY_CHECKLIST.md) | Security controls and privacy handling |
| [LAUNCH_CHECKLIST.md](docs/LAUNCH_CHECKLIST.md) | Everything to confirm before real customers |
| [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common problems and fixes |
| [PLAY_STORE.md](docs/PLAY_STORE.md) | Publishing the Android app |
| [AMAZON_PHASE2.md](docs/AMAZON_PHASE2.md) | Amazon SP-API integration plan |

## 10. Management commands

| Command | What it does |
|---|---|
| `seed_store` | Placeholder catalogue, policy pages and banner (safe to re-run) |
| `setup_roles` | Creates or updates the staff permission groups |
| `run_scheduled` | Hourly job: payment reconciliation, release of unpaid orders, email retries, cleanup |
