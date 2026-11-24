from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.emails import send_email
from apps.core.utils import client_ip, rate_limited

from .forms import (AddressForm, EmailOnlyForm, LoginForm, OTPForm, ProfileForm, RegisterForm,
                    StyledPasswordChangeForm, StyledPasswordResetForm, StyledSetPasswordForm,
                    login_identifier_to_email)
from .models import Address, EmailOTP, User
from .otp import OTPCooldown, cooldown_remaining, send_otp_email, verify_otp

SESSION_VERIFY_EMAIL = "pending_verify_email"
SESSION_LOGIN_EMAIL = "pending_otp_login_email"
SESSION_NEXT = "post_auth_next"
BACKEND = "django.contrib.auth.backends.ModelBackend"


def _safe_next(request, fallback="core:home"):
    nxt = request.session.pop(SESSION_NEXT, None) or request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}, require_https=request.is_secure()):
        return nxt
    return fallback


def _remember_next(request):
    nxt = request.GET.get("next") or request.POST.get("next")
    if nxt:
        request.session[SESSION_NEXT] = nxt


def _try_send_otp(request, email, purpose, name=""):
    """Send an OTP, respecting cooldown. Never reveals whether the account exists."""
    try:
        log = send_otp_email(email, purpose, name)
    except OTPCooldown as exc:
        messages.info(request, f"A code was sent recently. You can request a new one in {exc.seconds} seconds.")
        return
    if log.status != log.STATUS_SENT:
        messages.error(request, "We couldn't send the email right now. Please try again in a few minutes.")


# ---------------------------------------------------------------- registration
@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
    _remember_next(request)
    form = RegisterForm(request.POST or None)
    if request.method == "POST":
        if rate_limited(f"register:{client_ip(request)}", limit=8, window_seconds=3600):
            messages.error(request, "Too many attempts. Please try again later.")
            return render(request, "accounts/register.html", {"form": form}, status=429)
        if form.is_valid():
            data = form.cleaned_data
            email = data["email"]
            existing = User.objects.filter(email__iexact=email).first()
            if existing is None:
                with transaction.atomic():
                    user = User(email=email, full_name=data["full_name"], mobile=data.get("mobile", ""))
                    user.set_password(data["password1"])
                    user.set_marketing_consent(data.get("marketing_opt_in"), save=False)
                    user.save()
                _try_send_otp(request, email, EmailOTP.PURPOSE_VERIFY, data["full_name"])
            elif not existing.is_verified:
                # Unverified leftover: let the owner of the inbox finish verification.
                existing.full_name = data["full_name"]
                existing.mobile = data.get("mobile", "")
                existing.set_password(data["password1"])
                existing.set_marketing_consent(data.get("marketing_opt_in"), save=False)
                existing.save()
                _try_send_otp(request, email, EmailOTP.PURPOSE_VERIFY, data["full_name"])
            else:
                # Already registered: tell the inbox owner, not the browser.
                send_email("account_exists", email, {"user": existing}, related=f"user:{existing.pk}")
            request.session[SESSION_VERIFY_EMAIL] = email
            messages.success(request, f"We've sent a 6-digit code to {email}. It expires in "
                                      f"{settings.OTP_EXPIRY_SECONDS // 60} minutes.")
            return redirect("accounts:verify_email")
    return render(request, "accounts/register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def verify_email(request):
    email = request.session.get(SESSION_VERIFY_EMAIL)
    if not email:
        messages.info(request, "Enter your email to get a verification code.")
        return redirect("accounts:resend_verification")
    form = OTPForm(request.POST or None)
    if request.method == "POST":
        if rate_limited(f"otpverify:{client_ip(request)}", limit=30, window_seconds=3600):
            messages.error(request, "Too many attempts. Please try again later.")
        elif form.is_valid():
            if verify_otp(email, EmailOTP.PURPOSE_VERIFY, form.cleaned_data["code"]):
                user = User.objects.filter(email__iexact=email, is_active=True).first()
                if user:
                    first_time = not user.is_verified
                    user.is_verified = True
                    user.save(update_fields=["is_verified", "updated_at"])
                    request.session.pop(SESSION_VERIFY_EMAIL, None)
                    login(request, user, backend=BACKEND)
                    if first_time:
                        send_email("welcome", user.email, {"user": user}, related=f"user:{user.pk}")
                    messages.success(request, "Your email is verified. Welcome!")
                    return redirect(_safe_next(request, "accounts:dashboard"))
            form.add_error("code", "That code is incorrect or has expired. Request a new code if needed.")
    return render(request, "accounts/verify_email.html", {
        "form": form, "email": email,
        "cooldown": cooldown_remaining(email, EmailOTP.PURPOSE_VERIFY),
    })


@require_http_methods(["GET", "POST"])
def resend_verification(request):
    form = EmailOnlyForm(request.POST or None,
                         initial={"email": request.session.get(SESSION_VERIFY_EMAIL, "")})
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if rate_limited(f"otpsend:{email}", limit=5, window_seconds=3600) or \
                rate_limited(f"otpsend-ip:{client_ip(request)}", limit=20, window_seconds=3600):
            messages.error(request, "Too many codes requested. Please try again later.")
            return redirect("accounts:resend_verification")
        user = User.objects.filter(email__iexact=email, is_active=True, is_verified=False).first()
        if user:
            _try_send_otp(request, email, EmailOTP.PURPOSE_VERIFY, user.full_name)
        request.session[SESSION_VERIFY_EMAIL] = email
        messages.success(request, "If that email needs verification, a new code is on its way.")
        return redirect("accounts:verify_email")
    return render(request, "accounts/resend_verification.html", {"form": form})


# ---------------------------------------------------------------- login
class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        # Same account however it is typed (email, "98765 43210", "+91…") shares one attempt counter.
        email = login_identifier_to_email(request.POST.get("username")).lower()
        if rate_limited(f"login:{client_ip(request)}:{email}", limit=10, window_seconds=900) or \
                rate_limited(f"login-ip:{client_ip(request)}", limit=50, window_seconds=900):
            form = self.get_form()
            form.errors.clear()
            messages.error(request, "Too many login attempts. Please wait 15 minutes and try again.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        if any(e.code == "unverified" for e in form.non_field_errors().as_data()):
            email = form.cleaned_data.get("username", "")
            request = self.request
            _try_send_otp(request, email, EmailOTP.PURPOSE_VERIFY)
            request.session[SESSION_VERIFY_EMAIL] = email
            _remember_next(request)
            messages.info(request, "Please verify your email — we've sent you a 6-digit code.")
            return redirect("accounts:verify_email")
        return super().form_invalid(form)


@require_http_methods(["GET", "POST"])
def otp_login_request(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")
    _remember_next(request)
    form = EmailOnlyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        if rate_limited(f"otpsend:{email}", limit=5, window_seconds=3600) or \
                rate_limited(f"otpsend-ip:{client_ip(request)}", limit=20, window_seconds=3600):
            messages.error(request, "Too many codes requested. Please try again later.")
            return redirect("accounts:otp_login")
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            _try_send_otp(request, email, EmailOTP.PURPOSE_LOGIN, user.full_name)
        request.session[SESSION_LOGIN_EMAIL] = email
        messages.success(request, "If an account exists for that email, a login code is on its way.")
        return redirect("accounts:otp_login_verify")
    return render(request, "accounts/otp_login.html", {"form": form})


@require_http_methods(["GET", "POST"])
def otp_login_verify(request):
    email = request.session.get(SESSION_LOGIN_EMAIL)
    if not email:
        return redirect("accounts:otp_login")
    form = OTPForm(request.POST or None)
    if request.method == "POST":
        if rate_limited(f"otpverify:{client_ip(request)}", limit=30, window_seconds=3600):
            messages.error(request, "Too many attempts. Please try again later.")
        elif form.is_valid():
            if verify_otp(email, EmailOTP.PURPOSE_LOGIN, form.cleaned_data["code"]):
                user = User.objects.filter(email__iexact=email, is_active=True).first()
                if user:
                    if not user.is_verified:
                        user.is_verified = True  # they just proved inbox ownership
                        user.save(update_fields=["is_verified", "updated_at"])
                    request.session.pop(SESSION_LOGIN_EMAIL, None)
                    login(request, user, backend=BACKEND)
                    messages.success(request, f"Welcome back, {user.get_short_name()}!")
                    return redirect(_safe_next(request, "accounts:dashboard"))
            form.add_error("code", "That code is incorrect or has expired.")
    return render(request, "accounts/otp_login_verify.html", {
        "form": form, "email": email, "cooldown": cooldown_remaining(email, EmailOTP.PURPOSE_LOGIN)})


# ---------------------------------------------------------------- password reset / change
class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    form_class = StyledPasswordResetForm
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        email = form.cleaned_data["email"].lower()
        if rate_limited(f"pwreset:{email}", limit=3, window_seconds=3600) or \
                rate_limited(f"pwreset-ip:{client_ip(self.request)}", limit=10, window_seconds=3600):
            return redirect(self.success_url)  # same response either way
        return super().form_valid(form)


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("accounts:dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)  # keeps this session, logs out all others
        messages.success(self.request, "Password changed. Other devices have been signed out.")
        return response


# ---------------------------------------------------------------- account area
@login_required
def dashboard(request):
    recent = request.user.orders.order_by("-created_at")[:5]
    return render(request, "accounts/dashboard.html", {"recent_orders": recent})


@login_required
@require_http_methods(["GET", "POST"])
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form})


@login_required
def address_list(request):
    return render(request, "accounts/addresses.html", {"addresses": request.user.addresses.all()})


def _save_address(request, form):
    with transaction.atomic():
        address = form.save(commit=False)
        address.user = request.user
        if address.is_default or not request.user.addresses.exclude(pk=address.pk).exists():
            request.user.addresses.exclude(pk=address.pk).update(is_default=False)
            address.is_default = True
        address.save()
    return address


@login_required
@require_http_methods(["GET", "POST"])
def address_create(request):
    form = AddressForm(request.POST or None, initial={"full_name": request.user.full_name,
                                                       "phone": request.user.mobile})
    if request.method == "POST" and form.is_valid():
        _save_address(request, form)
        messages.success(request, "Address saved.")
        return redirect(request.GET.get("next") if request.GET.get("next") == "/checkout/" else "accounts:addresses")
    return render(request, "accounts/address_form.html", {"form": form, "title": "Add address"})


@login_required
@require_http_methods(["GET", "POST"])
def address_edit(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)  # ownership enforced here
    form = AddressForm(request.POST or None, instance=address)
    if request.method == "POST" and form.is_valid():
        _save_address(request, form)
        messages.success(request, "Address updated.")
        return redirect("accounts:addresses")
    return render(request, "accounts/address_form.html", {"form": form, "title": "Edit address"})


@login_required
@require_POST
def address_delete(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    was_default = address.is_default
    address.delete()
    if was_default:
        nxt = request.user.addresses.first()
        if nxt:
            nxt.is_default = True
            nxt.save(update_fields=["is_default"])
    messages.success(request, "Address removed.")
    return redirect("accounts:addresses")
