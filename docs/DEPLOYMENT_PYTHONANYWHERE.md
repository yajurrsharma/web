# Deploying to PythonAnywhere

This guide takes the project from your Windows PC to a live site at `https://www.yourdomain.in`.
Replace `yourusername` with your PythonAnywhere username and `yourdomain.in` with your domain.

## 0. Choose the plan (checked October 2026)

| Need | Beginner (free) | Developer (~$10/month) |
|---|---|---|
| Custom domain (`www.yourdomain.in`) | ✗ — only `yourusername.pythonanywhere.com` | ✓ |
| MySQL | ✗ for accounts created from 2026 | ✓ |
| Outbound internet (Razorpay API) | Allow-list only | Unrestricted |
| Scheduled tasks (hourly maintenance job) | ✗ for new accounts | ✓ (20) |
| Storage | 512 MB | 5 GB |
| Web apps | 1 | 1 |

**Use the Developer plan for the real store.** The store needs MySQL, your own domain, the Razorpay
API (server-side payment checks and refunds), and the hourly scheduled task.

Notes:
- The Developer plan includes **one web app**. To run this store next to another site on the **same
  account**, customise the plan to **2 web apps** (Account → Customize your plan).
- **Sharing an account with other apps:** the store gets its **own web app, its own virtualenv
  (`lifeline-venv`) and its own MySQL database**. Never install this project's requirements into another
  app's virtualenv — it would upgrade Django for that app too. Disk space (5 GB) and scheduled tasks
  (20) are shared by all apps on the account.
- Plans and limits change. Check https://www.pythonanywhere.com/pricing/ before you pay.
- **Free demo option:** SQLite (`DB_ENGINE=sqlite`), Cash on Delivery only (leave Razorpay keys
  empty), Gmail SMTP. Use it to show the site to people, not to take real orders.

## 1. Local virtual environment (Windows)

See README §2. Make sure `python manage.py test` passes before you deploy.

## 2. Put the code on GitHub (private repository)

1. On github.com create a **private** repository, e.g. `lifeline-store`, with no README.
2. In PowerShell, in the project folder:

```powershell
git init
git add .
git status          # .env, db.sqlite3, media/, venv/ must NOT be listed
git commit -m "Life Line Dynamics store"
git branch -M main
git remote add origin https://github.com/YOUR-GITHUB-NAME/lifeline-store.git
git push -u origin main
```

`.gitignore` already excludes `.env`, databases, uploads, logs and virtual environments. Never commit
real keys. If you ever do, rotate them immediately in Razorpay or your email provider.

## 3. Clone onto PythonAnywhere

PythonAnywhere → **Consoles → Bash**:

```bash
git clone https://github.com/YOUR-GITHUB-NAME/lifeline-store.git ~/lifeline
```

For a private repository, GitHub asks for a username and a **personal access token** (GitHub →
Settings → Developer settings → Fine-grained tokens, read-only access to this repository) instead of
a password.

**Without GitHub (zip upload):** upload the deploy zip (it contains no `.env`, database, uploads or
virtualenv) on the **Files** tab to your home folder, then in a Bash console:

```bash
mkdir -p ~/lifeline && cd ~/lifeline && unzip -o ~/lifeline-deploy.zip && rm ~/lifeline-deploy.zip
```

For later updates, back up first (§19), unzip the new zip over the same folder (your `.env`, `media/`
and `private_media/` are not in the zip, so they are kept), then follow §20.

## 4. Virtual environment on the server

```bash
mkvirtualenv --python=/usr/bin/python3.12 lifeline-venv   # use 3.13 if listed; 3.10+ works
cd ~/lifeline
pip install -r requirements.txt
```

`workon lifeline-venv` reactivates it in future consoles.

## 5. Create the MySQL database

**Databases** tab:
1. Set a MySQL password (different from your login password).
2. Create a database named `lifeline`. Its full name is `yourusername$lifeline`.
3. Note the host: `yourusername.mysql.pythonanywhere-services.com`.

Django 5.2 needs MySQL 8.0.11 or newer. The Databases tab shows the version.

## 6. Environment variables (secrets)

Create `~/lifeline/.env` on the server. It is never committed:

```bash
cd ~/lifeline && cp .env.example .env && nano .env
```

Production values:

```ini
DJANGO_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(64))">
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=www.yourdomain.in,yourdomain.in,yourusername.pythonanywhere.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://www.yourdomain.in,https://yourdomain.in
SITE_URL=https://www.yourdomain.in
ADMIN_URL_PATH=pick-something-hard-to-guess

DB_ENGINE=mysql
DB_NAME=yourusername$lifeline
DB_USER=yourusername
DB_PASSWORD=<your MySQL password>
DB_HOST=yourusername.mysql.pythonanywhere-services.com

CACHE_BACKEND=db
MEDIA_ROOT=/home/yourusername/lifeline/media
PRIVATE_MEDIA_ROOT=/home/yourusername/lifeline/private_media   # invoices — never mapped as static
STATIC_ROOT=/home/yourusername/lifeline/staticfiles
LOG_DIR=/home/yourusername/lifeline/logs

# Uploads (photos are resized automatically; long videos belong on YouTube)
MAX_VIDEOS_PER_PRODUCT=1
STORAGE_QUOTA_MB=5000        # your account's disk allowance — shared with your other apps

# Offer emails (raise if your email provider allows more per day)
CAMPAIGN_BATCH_SIZE=40
CAMPAIGN_DAILY_LIMIT=400

# Shiprocket (API user from Shiprocket → Settings → API; not your main login)
SHIPROCKET_API_EMAIL=...
SHIPROCKET_API_PASSWORD=...
SHIPROCKET_WEBHOOK_TOKEN=<run: python -c "import secrets; print(secrets.token_urlsafe(32))">

SECURE_HSTS_SECONDS=3600

EMAIL_HOST=...           # see section 14
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=Life Line Dynamics <orders@yourdomain.in>
ADMIN_ALERT_EMAILS=you@yourdomain.in

RAZORPAY_KEY_ID=rzp_test_...       # test keys first
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
```

Then lock the file: `chmod 600 ~/lifeline/.env`.

`EMAIL_BACKEND` is not set here: production uses SMTP by default. The production settings refuse to
start if the secret key is weak or `ALLOWED_HOSTS` is `*`. That is intentional.

## 7–10. Migrate, admin user, roles, sample pages

```bash
cd ~/lifeline && workon lifeline-venv
export DJANGO_SETTINGS_MODULE=config.settings.prod
python manage.py check --deploy
python manage.py migrate
python manage.py createcachetable          # needed because CACHE_BACKEND=db (rate limiting)
python manage.py setup_roles
python manage.py seed_store --pages-only   # policy pages to edit; skip products if adding your own
python manage.py createsuperuser
python manage.py collectstatic --noinput
```

`check --deploy` should report at most the two HSTS notes. Leave those until HTTPS is stable (§16).

## 11. Web app and WSGI file

**Web** tab → *Add a new web app* → **Manual configuration** (not "Django") → same Python version
as the virtualenv.

- **Source code:** `/home/yourusername/lifeline`
- **Virtualenv:** `/home/yourusername/.virtualenvs/lifeline-venv`

Click the **WSGI configuration file** link, delete everything, and paste:

```python
import os
import sys

path = "/home/yourusername/lifeline"
if path not in sys.path:
    sys.path.insert(0, path)

os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"

from django.core.wsgi import get_wsgi_application  # noqa: E402
application = get_wsgi_application()
```

Secrets come from `~/lifeline/.env`, which the settings load automatically. Don't put keys in the
WSGI file.

## 12. Static files

Web tab → **Static files**:

| URL | Directory |
|---|---|
| `/static/` | `/home/yourusername/lifeline/staticfiles` |
| `/media/` | `/home/yourusername/lifeline/media` |

**Never** add a mapping for `private_media` — invoice PDFs must only be reachable through the
logged-in, permission-checked download pages.

## 13. Media uploads

- Product images and videos go to `~/lifeline/media/` and are served directly by PythonAnywhere's
  static file server, which never executes them.
- Invoice PDFs are **not** stored as public files. They are generated on request behind a login and
  ownership check.
- Storage: 5 GB on the Developer plan covers thousands of optimised photos, but not many videos.
  Keep videos short (≤ 15 MB) or link to YouTube. Check usage on the **Files** tab. If you outgrow it,
  move media to object storage such as Cloudflare R2 or Amazon S3 using `django-storages`.
- Back up `~/lifeline/media/` along with the database (§19).

## 14. Email

- **Developer plan:** any SMTP provider works. Use a domain mailbox (Zoho Mail, Google Workspace)
  or a transactional service (Brevo, Amazon SES, Mailgun). Set up SPF and DKIM DNS records for your
  domain so emails don't land in spam.
- **Free plan:** only Gmail SMTP (`smtp.gmail.com`, port 587, an *app password*) or the HTTP APIs
  of allow-listed providers. Gmail has daily sending limits and isn't suited to a growing store.
- Test it: place a COD test order and check **Admin → Email logs** for `sent`.

## 15. Razorpay

Follow [PAYMENTS_RAZORPAY.md](PAYMENTS_RAZORPAY.md). The webhook URL is
`https://www.yourdomain.in/payments/webhooks/razorpay/`.

Direct UPI and bank transfer need no keys: switch them on in **Dashboard → Website → Payments, Theme &
Cards** and enter your UPI ID / bank details there.

## 15b. Shiprocket

1. Shiprocket → **Settings → API → Configure** → create an **API user** (an email different from your
   main login). Put its email and password in `.env` (§6) and reload the web app.
2. Shiprocket → **Settings → Pickup Addresses**: note the pickup location **name** and **PIN code**, and
   enter both in **Dashboard → Website → Payments, Theme & Cards → Shiprocket**, with your usual box size.
3. Shiprocket → **Settings → API → Webhooks**: URL `https://www.yourdomain.in/hooks/courier-updates/`,
   token = the `SHIPROCKET_WEBHOOK_TOKEN` value from `.env`.
4. Test: book one real order from the order page (**Check couriers & prices → Book shipment**), print the
   label, and check that the order moves to *Shipped* when the courier picks it up.

## 16. Domain and HTTPS

1. Web tab → change the web app's domain to `www.yourdomain.in` (paid plans). Note the CNAME
   target it shows, e.g. `webapp-XXXX.pythonanywhere.com`.
2. At your domain registrar's DNS: add a `CNAME` record `www` → that target. For the bare domain
   (`yourdomain.in`), use the registrar's URL redirect to `https://www.yourdomain.in`. PythonAnywhere
   can't serve a bare domain via CNAME.
3. Once DNS works, Web tab → **HTTPS certificate** → *Auto-renewing Let's Encrypt certificate*, and
   turn on **Force HTTPS**.
4. After a week of stable HTTPS, raise `SECURE_HSTS_SECONDS` to `31536000` in `.env` and reload.

## 17. Production switches

Already set by `config.settings.prod`: `DEBUG=False`, secure cookies, HTTPS redirect, HSTS,
`X-Frame-Options: DENY`, nosniff. You provide `DJANGO_ALLOWED_HOSTS` and
`DJANGO_CSRF_TRUSTED_ORIGINS` (§6).

## 18. Scheduled task (hourly)

**Tasks** tab → add an **hourly** task:

```bash
cd /home/yourusername/lifeline && DJANGO_SETTINGS_MODULE=config.settings.prod /home/yourusername/.virtualenvs/lifeline-venv/bin/python manage.py run_scheduled
```

It reconciles pending Razorpay payments, releases unpaid online / direct-UPI / bank orders (restoring
stock), retries failed emails, sends the next batch of offer emails, checks Shiprocket tracking (backup
for missed webhooks) and clears expired OTPs and old guest carts.

## 19. Backups and restore

Back up **before every update** and at least weekly. In a Bash console:

```bash
mkdir -p ~/backups
mysqldump -u yourusername -h yourusername.mysql.pythonanywhere-services.com \
  --set-gtid-purged=OFF --no-tablespaces 'yourusername$lifeline' | gzip > ~/backups/db-$(date +%F).sql.gz
tar czf ~/backups/media-$(date +%F).tar.gz -C ~/lifeline media
```

You can add these two lines as a **daily** scheduled task. Download copies to your PC regularly
from the Files tab; a backup that lives only on the same server isn't a real backup.

**Restore** (test this once before launch, into a scratch database):

```bash
gunzip < ~/backups/db-2026-10-18.sql.gz | mysql -u yourusername \
  -h yourusername.mysql.pythonanywhere-services.com 'yourusername$lifeline_restore_test'
```

Keep the database dumps private: they contain customer data. Delete old dumps according to the
retention rules in [SECURITY_CHECKLIST.md](SECURITY_CHECKLIST.md).

## 20. Updating the site

```bash
cd ~/lifeline
mysqldump ... (backup as above)
git pull
workon lifeline-venv
pip install -r requirements.txt
DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py migrate
DJANGO_SETTINGS_MODULE=config.settings.prod python manage.py collectstatic --noinput
```

Then **Web tab → Reload**. Check the error log (Web tab) if anything breaks.

## Verify after deploying

Run through [LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md) section B on the live site.
