# Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImproperlyConfigured: Missing required environment variable: DJANGO_SECRET_KEY` | No `.env`, or it's in the wrong folder | `.env` must sit next to `manage.py`. Copy `.env.example` and fill it in. |
| `Set a strong, unique DJANGO_SECRET_KEY in production` | Placeholder key on the server | Generate one with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| Site shows "Bad Request (400)" | Domain missing from `DJANGO_ALLOWED_HOSTS` | Add it, then reload. |
| "CSRF verification failed" on forms over HTTPS | `DJANGO_CSRF_TRUSTED_ORIGINS` missing | Add `https://www.yourdomain.in` (with `https://`), then reload. |
| Pages have no styling on the server | `collectstatic` not run, or `/static/` mapping wrong | Run `collectstatic`; check the Web tab mapping points to `staticfiles`. |
| Product images 404 on the server | `/media/` mapping missing | Web tab → Static files → `/media/` → `/home/yourusername/lifeline/media`. |
| `mysqlclient` fails to install on Windows | Not needed locally | Use `requirements-dev.txt` on your PC; `requirements.txt` on the server. |
| `Access denied for user` (MySQL) | Wrong DB password, user or host | Use the Databases-tab values; DB name is `yourusername$lifeline`. |
| `Table 'django_cache' doesn't exist` | Database cache not created | `python manage.py createcachetable`. |
| No emails arrive | SMTP settings, or still the console backend | Check Admin → Email logs for the error. Remove `EMAIL_BACKEND` from the server `.env`. Free plan: Gmail SMTP only. |
| Emails land in spam | No SPF/DKIM for your domain | Add the DNS records your email provider gives you. |
| OTP "incorrect or expired" | Code older than 5 minutes, used already, or 5 wrong tries | Request a new code (after the 60-second cooldown). |
| "Too many attempts" | Rate limit hit | Wait 15–60 minutes. In production, rate limits need `CACHE_BACKEND=db`. |
| "Pay online" missing at checkout | Razorpay keys not set, or turned off in Store settings | Set both keys and reload; check *Online payments enabled*. |
| Payment taken but order still Pending | Callback lost (browser closed) | Webhook or the hourly task fixes it. Or press *Check payment status with Razorpay* on the dashboard order page. |
| Webhook deliveries fail (400) | Wrong `RAZORPAY_WEBHOOK_SECRET` | Copy the exact secret from the Razorpay webhook settings. Test and live webhooks have different secrets. |
| Webhook deliveries fail (403/404) | Wrong URL, or HTTP instead of HTTPS | Use `https://www.yourdomain.in/payments/webhooks/razorpay/` exactly. |
| "Tax invoices are blocked" | GSTIN, legal name, address or state empty | Fill them in under Store settings. |
| Invoice not issued after shipping | Invoice trigger is set to "manual" | Change Store settings → Invoice trigger, or press *Issue invoice now*. |
| Can't edit stock in the admin | By design: stock is changed through the ledger | Dashboard → Inventory → adjust with a reason. |
| Can't delete a product | It has sales/stock history | Archive it (status Archived). History must remain for tax records. |
| Staff user gets "403 Forbidden" | Not in a group with that permission | Add them to the right group (`setup_roles` creates them). |
| 500 error after an update | Missing migration or package | `pip install -r requirements.txt`, `migrate`, reload; read the Web tab error log. |
| PowerShell won't activate venv | Script execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
