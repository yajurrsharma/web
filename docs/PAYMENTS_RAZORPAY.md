# Razorpay setup

The integration is built and tested with mocked responses. It is **not live** until you finish
Razorpay's merchant onboarding (KYC) and verify a real test payment on your server.

## How it works

1. The customer chooses *Pay online* → the order is created as **Pending** and its stock is reserved.
2. The server creates a Razorpay order for the exact total from the database, in paise.
3. Razorpay Checkout opens with only the public **Key ID**. The secret never reaches the browser.
4. After payment, the browser posts `payment_id`, `order_id` and `signature` back. The server checks
   the HMAC signature, then fetches the payment from Razorpay and confirms the amount, currency and
   status before marking the order **Paid**.
5. Razorpay also sends **webhooks**. Their signature is checked against the raw request body with
   your webhook secret. Each event is processed once, so duplicates are ignored.
6. If payment never completes, the hourly job checks Razorpay one last time, then cancels the order
   and restores stock after *payment timeout* (Store settings, default 30 minutes).
7. A browser redirect alone never marks an order paid. Card numbers and CVVs never touch this server.

## 1. Create the account

Sign up at https://razorpay.com → dashboard. You can use **Test Mode** right away. Live mode needs
business KYC (PAN, GST, bank account, website with policy pages). Your policy pages must be live
and complete before you apply.

## 2. Test keys

Dashboard → switch to **Test Mode** → *Account & Settings → API Keys* → *Generate Key*. Put them in
`.env`:

```ini
RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxx
```

Reload the web app. "Pay online" now appears at checkout, with a yellow test-mode bar.

## 3. Test webhook

Dashboard (Test Mode) → *Account & Settings → Webhooks* → *Add new webhook*:

- **URL:** `https://www.yourdomain.in/payments/webhooks/razorpay/`. It must be public HTTPS, so
  test webhooks on your live server or use a tunnel locally.
- **Secret:** a long random string. Copy it to `RAZORPAY_WEBHOOK_SECRET`.
- **Events:** `payment.captured`, `payment.failed`, `payment.authorized`, `order.paid`,
  `refund.processed`, `refund.failed`.

## 4. Auto-capture

*Account & Settings → Payment Capture* → **Automatic**. The code also captures authorised payments
itself as a fallback.

## 5. Test payments

Use the test card numbers and UPI test IDs listed in Razorpay's documentation ("Test Card Details"
and "Test UPI"); they change from time to time, so take them from the docs, not from here.

Check each of these:
- [ ] Successful card payment → order Confirmed, Payment Paid, email received, appears in the
      dashboard *Payment reconciliation* report with no issue.
- [ ] Failed payment (Razorpay's failure test card/UPI) → payment-failed page with *Try again*;
      retry succeeds on the same order.
- [ ] Close the payment window → order stays Pending; after the timeout and the hourly job it is
      cancelled and stock is restored.
- [ ] Webhooks: Dashboard → Webhooks shows deliveries with HTTP 200. Admin → Webhook events lists
      them.
- [ ] Refund from the dashboard order page → Razorpay shows the refund; after `refund.processed`
      the order shows *Partially refunded* or *Refunded*.

## 6. Going live (after KYC approval)

1. Razorpay dashboard → **Live Mode** → generate **live** API keys.
2. Create the same webhook again in Live Mode with a **new** secret.
3. Replace all three values in the server `.env`, then reload.
4. Place one small real order, then refund it from the dashboard.
5. Check that the settlement and fees appear in Razorpay's settlement report. Settlements are net of
   Razorpay fees and GST on fees, so they won't equal your gross sales. Record fees separately in
   your accounts.

Never commit keys to GitHub. If a key leaks, regenerate it in the Razorpay dashboard immediately.

## Adding another gateway later

All gateway calls go through `apps/payments/gateways.py`. Write a new `PaymentGateway` subclass
(create order, verify signatures, fetch, capture, refund) and return it from `get_gateway()`.
