import secrets

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.forms import AddressForm, EmailOnlyForm, OTPForm
from apps.accounts.models import EmailOTP, User
from apps.accounts.otp import OTPCooldown, cooldown_remaining, send_otp_email, verify_otp
from apps.cart.services import cart_items, get_cart, quote_for_cart, validate_cart
from apps.core.models import SiteSettings
from apps.core.utils import client_ip, rate_limited

from .forms import CheckoutForm, ProblemForm, ReasonForm
from .models import Order, OrderRequest
from .pincode import delivery_check, lookup
from .services import (CheckoutError, OrderActionError, cod_available, customer_cancel,
                       customer_request_return, payments_available, place_order, submit_payment_reference)

SESSION_TOKENS = "checkout_tokens"


def _payment_choices(store):
    choices = []
    if payments_available(store):
        shown = [name for name, on in (("UPI", store.razorpay_show_upi), ("cards", store.razorpay_show_cards),
                                       ("net banking", store.razorpay_show_netbanking),
                                       ("wallets incl. Amazon Pay", store.razorpay_show_wallets)) if on]
        choices.append((Order.METHOD_ONLINE, "Pay online — " + (", ".join(shown) or "Razorpay")))
    if store.upi_enabled:
        choices.append((Order.METHOD_UPI, f"Pay by UPI to our UPI ID ({store.upi_id}) — scan QR, then enter the "
                                          "UTR number"))
    if store.bank_transfer_enabled:
        choices.append((Order.METHOD_BANK, "Bank transfer (NEFT / IMPS) — account details shown after you "
                                           "place the order"))
    if store.cod_enabled:
        label = "Cash on Delivery"
        if store.cod_fee:
            label += f" (+ ₹{store.cod_fee} COD fee)"
        choices.append((Order.METHOD_COD, label))
    return choices


def _issue_token(request):
    token = secrets.token_urlsafe(32)
    tokens = request.session.get(SESSION_TOKENS, [])[-4:]
    tokens.append(token)
    request.session[SESSION_TOKENS] = tokens
    return token


SESSION_CHECKOUT_EMAIL = "checkout_email"


@require_http_methods(["GET", "POST"])
def checkout_start(request):
    """
    Step 1 for customers who are not logged in: email → 6-digit code on the same page → logged in.
    A new account is created on first verification (the code proves they own the inbox), so nobody has
    to register before buying. The guest cart is kept (merged on login).
    """
    if request.user.is_authenticated:
        return redirect("orders:checkout")
    cart = get_cart(request)
    if not cart or not cart.items.exists():
        messages.info(request, "Your cart is empty.")
        return redirect("cart:detail")
    email = request.session.get(SESSION_CHECKOUT_EMAIL, "")
    email_form = EmailOnlyForm(request.POST if request.POST.get("action") == "send" else None)
    code_form = OTPForm(request.POST if request.POST.get("action") == "verify" else None)
    action = request.POST.get("action")
    if action == "change":
        request.session.pop(SESSION_CHECKOUT_EMAIL, None)
        return redirect("orders:checkout_start")
    if action == "send" and email_form.is_valid():
        email = email_form.cleaned_data["email"]
        if rate_limited(f"otpsend:{email}", limit=5, window_seconds=3600) or \
                rate_limited(f"otpsend-ip:{client_ip(request)}", limit=20, window_seconds=3600):
            messages.error(request, "Too many codes requested. Please try again later.")
            return redirect("orders:checkout_start")
        existing = User.objects.filter(email__iexact=email).first()
        if existing is not None and not existing.is_active:
            pass  # closed account: say nothing different, send nothing
        else:
            try:
                log = send_otp_email(email, EmailOTP.PURPOSE_LOGIN, existing.full_name if existing else "")
                if log.status != log.STATUS_SENT:
                    messages.error(request, "We couldn't send the email right now. Please try again in a minute.")
            except OTPCooldown as exc:
                messages.info(request, f"A code was sent recently. You can request a new one in {exc.seconds} s.")
        request.session[SESSION_CHECKOUT_EMAIL] = email
        return redirect("orders:checkout_start")
    if action == "verify" and email:
        if rate_limited(f"otpverify:{client_ip(request)}", limit=30, window_seconds=3600):
            messages.error(request, "Too many attempts. Please try again later.")
        elif code_form.is_valid() and verify_otp(email, EmailOTP.PURPOSE_LOGIN, code_form.cleaned_data["code"]):
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                user = User.objects.create_user(email=email, full_name="", is_verified=True)
            elif not user.is_active:
                user = None
            elif not user.is_verified:
                user.is_verified = True
                user.save(update_fields=["is_verified", "updated_at"])
            if user is not None:
                request.session.pop(SESSION_CHECKOUT_EMAIL, None)
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                return redirect("orders:checkout")
            code_form.add_error("code", "That code is incorrect or has expired.")
        elif code_form.is_bound and not code_form.errors:
            code_form.add_error("code", "That code is incorrect or has expired.")
    quote, _ = quote_for_cart(cart)
    return render(request, "orders/checkout_start.html", {
        "email": email, "email_form": email_form, "code_form": code_form, "quote": quote,
        "items": list(zip(cart_items(cart), quote.item_lines, strict=True)),
        "cooldown": cooldown_remaining(email, EmailOTP.PURPOSE_LOGIN) if email else 0,
    })


@require_GET
def pincode_lookup(request, pin):
    """City/state for a PIN (used to fill the address form). Rate-limited; never required."""
    if rate_limited(f"pin:{client_ip(request)}", limit=60, window_seconds=600):
        return JsonResponse({"found": False}, status=429)
    data = lookup(pin)
    return JsonResponse({"found": True, **data} if data else {"found": False})


@require_GET
def delivery_check_view(request, pin):
    """For the "Check delivery" box on product pages."""
    if rate_limited(f"pin:{client_ip(request)}", limit=60, window_seconds=600):
        return JsonResponse({"ok": False, "message": "Too many checks — please try again in a few minutes."},
                            status=429)
    return JsonResponse(delivery_check(SiteSettings.load(), pin))


@require_http_methods(["GET", "POST"])
def checkout(request):
    if not request.user.is_authenticated:
        return redirect("orders:checkout_start")
    if not request.user.is_verified:
        messages.info(request, "Please verify your email before placing an order.")
        request.session["pending_verify_email"] = request.user.email
        return redirect("accounts:resend_verification")
    store = SiteSettings.load()
    cart = get_cart(request)
    if cart:
        for notice in validate_cart(cart):
            messages.warning(request, notice)
    if not cart or not cart.items.exists():
        messages.info(request, "Your cart is empty.")
        return redirect("cart:detail")
    if not request.user.addresses.exists():
        return _checkout_address_step(request, cart)

    choices = _payment_choices(store)
    form = CheckoutForm(request.POST or None, user=request.user, payment_choices=choices,
                        initial={"checkout_token": _issue_token(request) if request.method == "GET" else ""})
    if request.method == "GET":  # ?pm= / ?addr= keep the customer's choice when the summary refreshes
        if request.GET.get("pm") in dict(choices):
            form.initial["payment_method"] = request.GET["pm"]
        if request.GET.get("addr", "").isdigit() and request.user.addresses.filter(pk=request.GET["addr"]).exists():
            form.initial["shipping_address"] = int(request.GET["addr"])

    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        token = data["checkout_token"]
        if token not in request.session.get(SESSION_TOKENS, []):
            existing = Order.objects.filter(checkout_token=token, user=request.user).first()
            if existing:
                return _after_place(existing)
            messages.error(request, "Your checkout session expired. Please review and place the order again.")
            return redirect("orders:checkout")
        shipping = data["shipping_address"].as_snapshot()
        billing = shipping if data["billing_same"] else data["billing_address"].as_snapshot()
        try:
            order = place_order(user=request.user, cart=cart, billing=billing, shipping=shipping,
                                payment_method=data["payment_method"], checkout_token=token,
                                customer_gstin=data["customer_gstin"],
                                delivery_instructions=data["delivery_instructions"])
        except CheckoutError as exc:
            messages.error(request, str(exc))
            return redirect("orders:checkout")
        request.session[SESSION_TOKENS] = [t for t in request.session.get(SESSION_TOKENS, []) if t != token]
        return _after_place(order)

    selected = form["shipping_address"].value() or form.initial.get("shipping_address")
    address = request.user.addresses.filter(pk=selected).first() if selected else None
    state = address.state_code if address else None
    method = form["payment_method"].value() or form.initial.get("payment_method")
    quote, coupon_error = quote_for_cart(cart, user=request.user, ship_state_code=state, payment_method=method)
    if coupon_error:
        messages.warning(request, f"Coupon removed: {coupon_error}")
        cart.coupon = None
        cart.save(update_fields=["coupon"])
    # What each payment method would cost, so the prepaid saving is visible next to each choice.
    method_totals = {key: quote_for_cart(cart, user=request.user, ship_state_code=state,
                                         payment_method=key)[0].grand_total for key, _ in choices}
    cod_total = method_totals.get(Order.METHOD_COD)
    cod_ok, cod_reason = cod_available(store, cod_total if cod_total is not None else quote.grand_total)
    return render(request, "orders/checkout.html", {
        "form": form, "quote": quote, "cart": cart, "addresses": request.user.addresses.all(),
        "payment_choices": choices, "cod_ok": cod_ok, "cod_reason": cod_reason, "method_totals": method_totals,
        "items": list(zip(cart_items(cart), quote.item_lines, strict=True)),
    })


def _checkout_address_step(request, cart):
    """Step 2 inside checkout: the first delivery address (city/state fill in from the PIN code)."""
    user = request.user
    form = AddressForm(request.POST or None, initial={"full_name": user.full_name, "label": "Home"})
    if request.method == "POST" and form.is_valid():
        address = form.save(commit=False)
        address.user = user
        address.is_default = True
        address.save()
        if not user.full_name:
            user.full_name = address.full_name
            user.save(update_fields=["full_name", "updated_at"])
        return redirect("orders:checkout")
    quote, _ = quote_for_cart(cart, user=user)
    return render(request, "orders/checkout_address.html", {
        "form": form, "quote": quote, "items": list(zip(cart_items(cart), quote.item_lines, strict=True))})


def _url(name):
    from django.urls import reverse

    return reverse(name)


def _after_place(order):
    if order.payment_method == Order.METHOD_ONLINE and not order.is_paid:
        return redirect("payments:pay", order_number=order.order_number)
    return redirect("orders:confirmation", order_number=order.order_number)


def _manual_pay_context(order, store):
    """UPI QR / bank details shown while a direct-payment order waits for money."""
    if not order.awaiting_manual_payment:
        return {}
    ctx = {"manual_pay": True}
    if order.payment_method == Order.METHOD_UPI and store.upi_id:
        from apps.core.codes import qr_svg, upi_link

        link = upi_link(store.upi_id, store.upi_payee_name or store.store_name, order.grand_total,
                        order.order_number)
        ctx.update(upi_link=link, upi_qr=qr_svg(link))
    return ctx


@login_required
@require_GET
def confirmation(request, order_number):
    order = get_object_or_404(Order.objects.prefetch_related("items"), order_number=order_number,
                              user=request.user)
    return render(request, "orders/confirmation.html",
                  {"order": order, **_manual_pay_context(order, SiteSettings.load())})


@login_required
@require_POST
def payment_reference(request, order_number):
    from apps.core.utils import client_ip, rate_limited

    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    if rate_limited(f"payref:{client_ip(request)}", limit=10, window_seconds=3600):
        messages.error(request, "Too many attempts. Please try again later or contact us.")
        return redirect(order)
    try:
        submit_payment_reference(order, request.user, request.POST.get("reference", ""))
    except OrderActionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Thank you! We'll check our account and confirm your payment soon — "
                                  "usually within a few working hours.")
    return redirect(order)


@login_required
@require_GET
def order_list(request):
    orders = request.user.orders.prefetch_related("items").order_by("-created_at")
    page = Paginator(orders, 10).get_page(request.GET.get("page"))
    return render(request, "orders/list.html", {"page_obj": page, "orders": page.object_list})


@login_required
@require_GET
def order_detail(request, order_number):
    # Filtering by user means another customer's order number returns 404, not 403.
    order = get_object_or_404(Order.objects.prefetch_related("items", "history", "requests"),
                              order_number=order_number, user=request.user)
    store = SiteSettings.load()
    return render(request, "orders/detail.html", {
        **_manual_pay_context(order, store),
        "order": order,
        "history": [h for h in order.history.all() if h.visible_to_customer],
        "can_cancel": order.customer_cancellable(store) and not order.requests.filter(
            kind="cancel", status="requested").exists(),
        "can_return": order.return_window_open(store),
        "reason_form": ReasonForm(),
        "problem_form": ProblemForm(),
        "photo_optional": sorted(OrderRequest.PHOTO_OPTIONAL),
        "invoice": getattr(order, "invoice", None),
    })


@login_required
@require_POST
def cancel(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    form = ReasonForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please tell us why you'd like to cancel.")
        return redirect(order)
    try:
        result = customer_cancel(order, request.user, form.cleaned_data["reason"])
    except OrderActionError as exc:
        messages.error(request, str(exc))
        return redirect(order)
    if isinstance(result, Order):
        messages.success(request, "Your order has been cancelled.")
    else:
        messages.success(request, "Cancellation requested. As this order is paid, our team will confirm "
                                  "and refund it to your original payment method.")
    return redirect(order)


@login_required
@require_POST
def request_return(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    form = ProblemForm(request.POST, request.FILES)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(order.get_absolute_url() + "#report")
    try:
        customer_request_return(order, request.user, form.cleaned_data["reason"],
                                problem=form.cleaned_data["problem"], photos=form.cleaned_data["photos"])
    except OrderActionError as exc:
        messages.error(request, str(exc))
        return redirect(order)
    messages.success(request, "Thank you — we have received your report. Our team will check the photos and "
                              "reply within 48 hours with a replacement or refund.")
    return redirect(order)


@require_http_methods(["GET", "POST"])
def track(request):
    """Public order tracking by order number + email (no login needed)."""
    from apps.core.utils import client_ip, rate_limited

    order = None
    if request.method == "POST":
        if rate_limited(f"track:{client_ip(request)}", limit=20, window_seconds=600):
            messages.error(request, "Too many lookups. Please wait a few minutes.")
        else:
            number = (request.POST.get("order_number") or "").strip().upper()[:20]
            email = (request.POST.get("email") or "").strip().lower()[:254]
            order = Order.objects.filter(order_number=number, email__iexact=email).first()
            if order is None:
                messages.error(request, "We couldn't find an order with those details.")
    return render(request, "orders/track.html", {"order": order})
