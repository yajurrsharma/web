from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.core.exceptions import ValidationError

from .models import Address, User, normalize_mobile

MOBILE_TAKEN = ("This mobile number is already linked to another account. Log in with it, or leave this "
                "blank and add a different number later.")


def clean_mobile_value(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    mobile = normalize_mobile(raw)
    if not mobile:
        raise ValidationError("Enter a valid 10-digit Indian mobile number.")
    return mobile


def login_identifier_to_email(value):
    """Login accepts an email or a mobile number. A mobile is swapped for its account's email; an unknown
    number is returned unchanged so it simply fails like a wrong password (no hint whether it exists)."""
    value = (value or "").strip()
    if "@" in value:
        return value.lower()
    mobile = normalize_mobile(value)
    if mobile:
        email = User.objects.filter(mobile=mobile).values_list("email", flat=True).first()
        if email:
            return email
    return value


class BootstrapMixin:
    def _bootstrapify(self):
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, forms.Select):
                widget.attrs.setdefault("class", "form-select")
            else:
                widget.attrs.setdefault("class", "form-control")


class RegisterForm(BootstrapMixin, forms.Form):
    full_name = forms.CharField(max_length=150, label="Full name",
                                widget=forms.TextInput(attrs={"autocomplete": "name"}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    mobile = forms.CharField(max_length=16, required=False, label="Mobile (optional)",
                             help_text="Add it to log in with your mobile number as well as your email.",
                             widget=forms.TextInput(attrs={"autocomplete": "tel-national", "inputmode": "tel"}))
    password1 = forms.CharField(label="Password", strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                                help_text="At least 8 characters; not entirely numbers or too common.")
    password2 = forms.CharField(label="Confirm password", strip=False,
                                widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    accept_terms = forms.BooleanField(label="I agree to the Terms and Privacy Policy")
    # Separate and unticked on purpose: DPDP Act consent must be specific and not a condition of signing up.
    marketing_opt_in = forms.BooleanField(
        required=False, label="🎁 Yes, send me festival offers, discounts and new products by email",
        help_text="Optional. Unsubscribe any time with one click. Order and delivery emails come either way.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrapify()

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_mobile(self):
        return clean_mobile_value(self.cleaned_data.get("mobile"))

    def clean(self):
        cleaned = super().clean()
        mobile, email = cleaned.get("mobile"), cleaned.get("email")
        if mobile and User.objects.filter(mobile=mobile).exclude(email__iexact=email or "").exists():
            self.add_error("mobile", MOBILE_TAKEN)
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        if p1:
            temp_user = User(email=cleaned.get("email", ""), full_name=cleaned.get("full_name", ""))
            try:
                password_validation.validate_password(p1, temp_user)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class OTPForm(BootstrapMixin, forms.Form):
    code = forms.CharField(label="6-digit code", min_length=6, max_length=6,
                           widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric",
                                                         "pattern": "[0-9]{6}"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrapify()

    def clean_code(self):
        code = self.cleaned_data["code"].strip()
        if not code.isdigit():
            raise ValidationError("Enter the 6 digits from the email.")
        return code


class EmailOnlyForm(BootstrapMixin, forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrapify()

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class LoginForm(BootstrapMixin, AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Email / mobile number or password is incorrect.",
        "unverified": "Please verify your email address first.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Email or mobile number"
        self.fields["username"].widget = forms.TextInput(attrs={"autocomplete": "username", "autofocus": True,
                                                                "autocapitalize": "none", "spellcheck": "false"})
        self._bootstrapify()

    def clean_username(self):
        return login_identifier_to_email(self.cleaned_data["username"])

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_verified and not user.is_staff:
            raise ValidationError(self.error_messages["unverified"], code="unverified")


class StyledPasswordResetForm(BootstrapMixin, PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrapify()

    def send_mail(self, subject_template_name, email_template_name, context, from_email, to_email,
                  html_email_template_name=None):
        """Route through the logged email service; the reset link is treated as sensitive."""
        from apps.core.emails import send_email

        send_email("password_reset", to_email, {
            "reset_url": f"{context['protocol']}://{context['domain']}"
                         f"/accounts/reset/{context['uid']}/{context['token']}/",
            "user": context["user"],
        }, sensitive=True, related="password_reset")


class StyledSetPasswordForm(BootstrapMixin, SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrapify()


class StyledPasswordChangeForm(BootstrapMixin, PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrapify()


class ProfileForm(BootstrapMixin, forms.ModelForm):
    mobile = forms.CharField(max_length=16, required=False, label="Mobile",
                             help_text="You can log in with this number as well as your email.",
                             widget=forms.TextInput(attrs={"autocomplete": "tel-national", "inputmode": "tel"}))

    marketing_opt_in = forms.BooleanField(
        required=False, label="🎁 Send me festival offers, discounts and new products by email",
        help_text="Untick any time to stop. Order and delivery emails come either way.")

    class Meta:
        model = User
        fields = ["full_name", "mobile"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["marketing_opt_in"].initial = self.instance.marketing_opt_in
        self._bootstrapify()

    def clean_mobile(self):
        mobile = clean_mobile_value(self.cleaned_data.get("mobile"))
        if mobile and User.objects.filter(mobile=mobile).exclude(pk=self.instance.pk).exists():
            raise ValidationError(MOBILE_TAKEN)
        return mobile

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_marketing_consent(self.cleaned_data.get("marketing_opt_in"), save=False)
        if commit:
            user.save()
        return user


class AddressForm(BootstrapMixin, forms.ModelForm):
    LABEL_CHOICES = [("Home", "🏠 Home"), ("Office", "🏢 Office"), ("Other", "📍 Other")]

    class Meta:
        model = Address
        fields = ["label", "full_name", "phone", "line1", "line2", "city", "state_code", "pincode", "is_default"]
        widgets = {
            "phone": forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "tel-national"}),
            "pincode": forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "postal-code"}),
            "line1": forms.TextInput(attrs={"autocomplete": "address-line1"}),
            "line2": forms.TextInput(attrs={"autocomplete": "address-line2"}),
            "city": forms.TextInput(attrs={"autocomplete": "address-level2"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrapify()
        # A drop-down instead of free typing. An older typed label is kept as an extra choice when editing.
        choices = list(self.LABEL_CHOICES)
        current = self.instance.label if self.instance.pk else ""
        if current and current not in dict(choices):
            choices.append((current, current))
        self.fields["label"] = forms.ChoiceField(
            label="Address type", choices=choices, initial=current or "Home",
            widget=forms.Select(attrs={"class": "form-select"}))
