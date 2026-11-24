from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from .gst import STATE_CHOICES, STATE_NAMES, validate_gstin
from .themes import THEME_CHOICES, contrast_with_white
from .validators import safe_upload_path, shrink_image, validate_image_upload

ZERO = Decimal("0.00")
DEFAULT_TRUST_ITEMS = ("💵 | Cash on Delivery | Pay when it arrives\n"
                       "🔒 | Secure payment | UPI, cards, net banking\n"
                       "🧾 | GST invoice | With every order\n"
                       "📦 | Track your order | Updates at every step")
hex_color_validator = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Enter a colour like #3a0a13.")


class SiteSettings(models.Model):
    """
    Singleton holding all business-configurable values. Edit in the admin under
    "Store settings". Nothing here is invented: blank values show placeholders.
    """

    # --- Store identity ---
    store_name = models.CharField(max_length=120, default="Life Line Dynamics")
    tagline = models.CharField(max_length=200, blank=True, default="Devotional & puja essentials")
    logo = models.ImageField(upload_to=safe_upload_path("branding"), blank=True,
                             validators=[validate_image_upload])
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=20, blank=True)
    whatsapp_number = models.CharField(max_length=20, blank=True)
    footer_text = models.CharField(max_length=300, blank=True)

    # --- Legal / GST profile (must be supplied by the business) ---
    gst_registered = models.BooleanField(
        default=True, help_text="Tax invoices are issued only when this is on and GSTIN is set.")
    legal_name = models.CharField(max_length=200, blank=True)
    trade_name = models.CharField(max_length=200, blank=True)
    gstin = models.CharField("GSTIN", max_length=15, blank=True)
    pan = models.CharField("PAN", max_length=10, blank=True)
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state_code = models.CharField(max_length=2, choices=STATE_CHOICES, blank=True,
                                  help_text="Registered state — decides CGST+SGST vs IGST.")
    pincode = models.CharField(max_length=6, blank=True)

    # --- Pricing & tax behaviour (confirm with your CA) ---
    prices_include_gst = models.BooleanField(
        default=True, help_text="Selling prices entered in admin already include GST (usual for retail).")
    SHIPPING_TAX_PRINCIPAL = "principal"
    SHIPPING_TAX_FIXED = "fixed"
    SHIPPING_TAX_EXEMPT = "none"
    shipping_tax_mode = models.CharField(
        max_length=10, default=SHIPPING_TAX_PRINCIPAL,
        choices=[(SHIPPING_TAX_PRINCIPAL, "Highest GST rate among items in the order"),
                 (SHIPPING_TAX_FIXED, "Fixed GST rate below"),
                 (SHIPPING_TAX_EXEMPT, "No GST on shipping")],
    )
    shipping_tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("18.00"))
    round_off_invoice_total = models.BooleanField(
        default=False, help_text="Round invoice grand total to the nearest rupee with a round-off line.")

    # --- Invoicing ---
    INVOICE_ON_DISPATCH = "dispatch"
    INVOICE_ON_PAYMENT = "payment"
    INVOICE_MANUAL = "manual"
    invoice_trigger = models.CharField(
        max_length=10, default=INVOICE_ON_DISPATCH,
        choices=[(INVOICE_ON_DISPATCH, "When order is shipped"),
                 (INVOICE_ON_PAYMENT, "When online payment is confirmed (COD: on shipping)"),
                 (INVOICE_MANUAL, "Only when staff issues it")],
    )
    invoice_prefix = models.CharField(max_length=10, default="LLD",
                                      help_text="1–5 letters/digits. Numbers look like LLD/2627/00001 (GST limit: 16 characters).")
    credit_note_prefix = models.CharField(max_length=10, default="LLDCN")
    invoice_terms = models.TextField(blank=True, default="Goods once sold are subject to the return policy on our website.")
    email_invoice_to_customer = models.BooleanField(default=True)

    # --- Shipping ---
    shipping_flat_charge = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO,
                                               validators=[MinValueValidator(ZERO)])
    free_shipping_threshold = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Order value (after discount) at or above which shipping is free. Blank = never free.")
    serviceable_pincodes = models.TextField(
        blank=True, help_text="Optional. Comma/space separated PIN codes or prefixes (e.g. 1600, 134109). Blank = all India.")
    estimated_delivery_text = models.CharField(max_length=200, blank=True, default="Usually delivered in 4–7 working days.")
    # Used by "Check delivery" on product pages. Working days (Sundays are skipped).
    delivery_days_local_min = models.PositiveSmallIntegerField("Within your state: from (days)", default=2)
    delivery_days_local_max = models.PositiveSmallIntegerField("Within your state: to (days)", default=4)
    delivery_days_india_min = models.PositiveSmallIntegerField("Rest of India: from (days)", default=4)
    delivery_days_india_max = models.PositiveSmallIntegerField("Rest of India: to (days)", default=7)

    # --- COD ---
    cod_enabled = models.BooleanField(default=True)
    cod_min_order = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    cod_max_order = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cod_fee = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO,
                                  help_text="Added to shipping for COD orders.")

    # --- Prepaid benefit (fewer risky COD orders) ---
    prepaid_discount_percent = models.DecimalField(
        "Extra discount for prepaid orders (%)", max_digits=4, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("20"))],
        help_text="For online, direct UPI and bank-transfer payments. 0 = off.")
    prepaid_free_shipping = models.BooleanField(
        "Free delivery on prepaid orders", default=False,
        help_text="Delivery charge is waived when the customer pays in advance.")

    # --- Offer bar at the top of every page ---
    announcement_text = models.CharField(
        "Offer bar text", max_length=300, blank=True,
        help_text="Short messages separated by | e.g. 'Free delivery above ₹499 | Use code DIWALI10'. "
                  "Active offers marked 'show in offer bar' are added automatically.")

    # --- Online payments ---
    online_payments_enabled = models.BooleanField(default=True,
                                                  help_text="Also requires Razorpay keys in the environment.")
    payment_timeout_minutes = models.PositiveIntegerField(
        default=30, help_text="Unpaid online orders are cancelled and stock restored after this.")
    razorpay_show_upi = models.BooleanField("Razorpay: show UPI", default=True)
    razorpay_show_cards = models.BooleanField("Razorpay: show cards", default=True)
    razorpay_show_netbanking = models.BooleanField("Razorpay: show net banking", default=True)
    razorpay_show_wallets = models.BooleanField(
        "Razorpay: show wallets (Amazon Pay, Paytm, etc.)", default=True,
        help_text="Which wallets appear also depends on what Razorpay has activated for your account.")

    # --- Direct payments, confirmed by staff (no gateway fee) ---
    upi_enabled = models.BooleanField(
        "Direct UPI payment", default=False,
        help_text="Customer pays your UPI ID (QR code shown), enters the UTR number, and you confirm the "
                  "money arrived before shipping.")
    upi_id = models.CharField("Your UPI ID", max_length=60, blank=True,
                              validators=[RegexValidator(r"^[\w.\-]{2,}@[A-Za-z][\w.\-]*$",
                                                         "Enter a UPI ID like name@okhdfcbank.")])
    upi_payee_name = models.CharField("Name shown in the UPI app", max_length=60, blank=True)
    bank_transfer_enabled = models.BooleanField(
        "Bank transfer (NEFT / IMPS / RTGS)", default=False,
        help_text="Customer transfers to your account, enters the reference number, and you confirm.")
    bank_account_name = models.CharField("Account holder name", max_length=100, blank=True)
    bank_account_number = models.CharField(max_length=20, blank=True,
                                           validators=[RegexValidator(r"^\d{6,20}$", "Digits only.")])
    bank_ifsc = models.CharField("IFSC", max_length=11, blank=True,
                                 validators=[RegexValidator(r"^[A-Z]{4}0[A-Z0-9]{6}$",
                                                            "Enter an IFSC like HDFC0001234.")])
    bank_name = models.CharField(max_length=100, blank=True)
    bank_branch = models.CharField(max_length=100, blank=True)
    manual_payment_hours = models.PositiveIntegerField(
        "Time to pay (hours)", default=24,
        help_text="Direct UPI / bank orders with no payment reference after this are cancelled and stock "
                  "restored. Orders where the customer entered a reference wait for you to check.")

    # --- Home page look ---
    trust_items = models.TextField(
        "Trust points on the home page", blank=True, default=DEFAULT_TRUST_ITEMS,
        help_text="One per line: icon | title | short text. Only promises you keep (e.g. dispatch time).")
    shloka_text = models.CharField(
        "Blessing / shloka line", max_length=300, blank=True,
        help_text="Shown in a decorated band on the home page. Leave blank to hide the band.")
    shloka_meaning = models.CharField("Its meaning (small text under it)", max_length=200, blank=True)

    # --- Shiprocket ---
    shiprocket_pickup_location = models.CharField(
        "Shiprocket pickup location name", max_length=60, default="Primary",
        help_text="Exactly as written in Shiprocket → Settings → Pickup Addresses (e.g. 'Primary' or 'Home').")
    shiprocket_pickup_pincode = models.CharField(
        "Pickup PIN code", max_length=6, blank=True, validators=[RegexValidator(r"^\d{6}$", "6 digits.")],
        help_text="PIN code of that pickup address — used to get courier prices. Blank = your GST address PIN.")
    parcel_length_cm = models.DecimalField("Usual box length (cm)", max_digits=5, decimal_places=1, default=20,
                                           validators=[MinValueValidator(Decimal("0.5"))])
    parcel_breadth_cm = models.DecimalField("Usual box width (cm)", max_digits=5, decimal_places=1, default=15,
                                            validators=[MinValueValidator(Decimal("0.5"))])
    parcel_height_cm = models.DecimalField("Usual box height (cm)", max_digits=5, decimal_places=1, default=10,
                                           validators=[MinValueValidator(Decimal("0.5"))])
    parcel_default_weight_g = models.PositiveIntegerField(
        "Weight if products have none (grams)", default=300, validators=[MinValueValidator(10)],
        help_text="Used only when the products in an order have no shipping weight. Courier price depends on it.")

    # --- Look of the shop ---
    card_text_limit = models.PositiveSmallIntegerField(
        "Product text length on listing cards", default=150,
        validators=[MinValueValidator(40), MaxValueValidator(400)],
        help_text="Maximum characters of product description shown on the home page and shop listings "
                  "(40–400). Keeps every card the same height so the Add to Cart buttons line up.")
    hero_interval_seconds = models.PositiveSmallIntegerField(
        "Home slider speed (seconds per banner)", default=4,
        validators=[MinValueValidator(2), MaxValueValidator(15)])
    theme = models.CharField("Colour theme", max_length=20, choices=THEME_CHOICES, default="default",
                             help_text="Switch for festivals. Takes effect immediately on the whole shop.")
    theme_header_color = models.CharField(
        "Custom top bar colour", max_length=7, blank=True, validators=[hex_color_validator],
        help_text="Optional, e.g. #3a0a13. Leave blank to use the theme. Must be dark (white text sits on it).")
    theme_strip_color = models.CharField(
        "Custom menu strip colour", max_length=7, blank=True, validators=[hex_color_validator],
        help_text="Optional. Must be dark (white text sits on it).")
    theme_accent_color = models.CharField(
        "Custom accent colour", max_length=7, blank=True, validators=[hex_color_validator],
        help_text="Optional. Used for the search button and highlights. Should be bright.")

    # --- Orders / returns ---
    cancel_allowed_until = models.CharField(
        max_length=12, default="processing",
        choices=[("confirmed", "Until confirmed"), ("processing", "Until processing starts"),
                 ("shipped", "Until shipped")])
    return_window_days = models.PositiveIntegerField(
        "Days to report a problem after delivery", default=7,
        help_text="Customers can report a damaged, wrong or missing item within these days. 0 = turned off.")

    # --- Grievance officer (Consumer Protection (E-Commerce) Rules, 2020) ---
    grievance_officer_name = models.CharField(max_length=100, blank=True)
    grievance_officer_designation = models.CharField(max_length=100, blank=True, default="Grievance Officer")
    grievance_email = models.EmailField(blank=True, help_text="Leave blank to use the support email.")
    grievance_phone = models.CharField(max_length=20, blank=True, help_text="Leave blank to use the support phone.")
    grievance_hours = models.CharField(max_length=100, blank=True, default="Monday to Saturday, 10 am to 6 pm")
    low_stock_email_alerts = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    CACHE_KEY = "site_settings_v1"

    class Meta:
        verbose_name = "store settings"
        verbose_name_plural = "store settings"

    def __str__(self):
        return "Store settings"

    def clean(self):
        if self.gstin:
            self.gstin = self.gstin.strip().upper()
            validate_gstin(self.gstin)
            if self.state_code and self.gstin[:2] != self.state_code:
                raise ValidationError({"state_code": "State must match the first two digits of the GSTIN."})
        if self.cod_max_order is not None and self.cod_max_order < self.cod_min_order:
            raise ValidationError({"cod_max_order": "Must be greater than the COD minimum."})
        for field in ("invoice_prefix", "credit_note_prefix"):
            value = getattr(self, field) or ""
            if not value.isalnum() or len(value) > 5:
                raise ValidationError({field: "Use 1–5 letters/digits so numbers like LLD/2627/00001 "
                                              "stay within the 16-character GST limit."})
        errors = {}
        if self.upi_enabled and not (self.upi_id and self.upi_payee_name):
            errors["upi_id"] = "Enter your UPI ID and the name shown in the UPI app before turning on direct UPI."
        if self.bank_transfer_enabled and not (self.bank_account_name and self.bank_account_number
                                               and self.bank_ifsc and self.bank_name):
            errors["bank_account_number"] = ("Enter account holder name, account number, IFSC and bank name "
                                             "before turning on bank transfer.")
        if self.bank_ifsc:
            self.bank_ifsc = self.bank_ifsc.strip().upper()
        for field in ("theme_header_color", "theme_strip_color"):
            value = getattr(self, field)
            if value and len(value) == 7 and contrast_with_white(value) < 4.5:
                errors[field] = "Too light — white text on it would be hard to read. Choose a darker colour."
        if errors:
            raise ValidationError(errors)

    @property
    def manual_payments_available(self):
        return self.upi_enabled or self.bank_transfer_enabled

    @property
    def trust_list(self):
        """Home page trust points: [{'icon', 'title', 'text'}] from 'icon | title | text' lines."""
        rows = []
        for line in (self.trust_items or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2 and parts[1]:
                rows.append({"icon": parts[0], "title": parts[1], "text": parts[2] if len(parts) > 2 else ""})
        return rows[:6]

    @property
    def colors(self):
        from .themes import theme_colors

        return theme_colors(self)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    def delete(self, *args, **kwargs):
        raise ValidationError("Store settings cannot be deleted.")

    @classmethod
    def load(cls):
        from django.conf import settings

        ttl = getattr(settings, "SITE_SETTINGS_CACHE_SECONDS", 300)
        obj = cache.get(cls.CACHE_KEY) if ttl else None
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            if ttl:
                cache.set(cls.CACHE_KEY, obj, ttl)
        return obj

    # --- helpers ---
    @property
    def state_name(self):
        return STATE_NAMES.get(self.state_code, "")

    @property
    def can_issue_tax_invoice(self):
        return bool(self.gst_registered and self.gstin and self.legal_name
                    and self.address_line1 and self.state_code)

    @property
    def missing_invoice_fields(self):
        missing = []
        for field, label in [("gstin", "GSTIN"), ("legal_name", "Legal name"),
                             ("address_line1", "Registered address"), ("state_code", "State")]:
            if not getattr(self, field):
                missing.append(label)
        if not self.gst_registered:
            missing.append("GST registration switched off")
        return missing

    @property
    def grievance_contact(self):
        """Email and phone for complaints; falls back to the support contacts."""
        return {"email": self.grievance_email or self.support_email,
                "phone": self.grievance_phone or self.support_phone}

    @property
    def full_address(self):
        parts = [self.address_line1, self.address_line2, self.city,
                 f"{self.state_name} {self.pincode}".strip()]
        return ", ".join(p for p in parts if p)

    def is_pincode_serviceable(self, pincode):
        rules = [r for r in self.serviceable_pincodes.replace(",", " ").split() if r]
        return not rules or any(pincode.startswith(r) for r in rules)


class Page(models.Model):
    """Editable content pages: About, FAQ, policies. Plain text with blank-line paragraphs."""

    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=150)
    body = models.TextField(help_text="Plain text. Lines starting with '## ' become headings. "
                                      "Blank lines separate paragraphs.")
    meta_description = models.CharField(max_length=160, blank=True)
    is_published = models.BooleanField(default=True)
    show_in_footer = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "title"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("core:page", args=[self.slug])

    def blocks(self):
        """Split body into ('h', text) / ('p', text) blocks for safe rendering."""
        out = []
        for chunk in self.body.replace("\r\n", "\n").split("\n\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if chunk.startswith("## "):
                head, _, rest = chunk.partition("\n")
                out.append(("h", head[3:].strip()))
                if rest.strip():
                    out.append(("p", rest.strip()))
            else:
                out.append(("p", chunk))
        return out


class Banner(models.Model):
    heading = models.CharField(max_length=120)
    subheading = models.CharField(max_length=200, blank=True)
    image = models.ImageField(upload_to=safe_upload_path("banners"), blank=True,
                              validators=[validate_image_upload],
                              help_text="Recommended 1600×600. Leave blank for a text banner.")
    link_url = models.CharField(max_length=200, blank=True, help_text="Site path like /shop/ or a full URL.")
    link_text = models.CharField(max_length=40, blank=True, default="Shop now")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.heading

    def save(self, *args, **kwargs):
        shrink_image(self.image)
        super().save(*args, **kwargs)


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True)
    subject = models.CharField(max_length=150)
    message = models.TextField(max_length=3000)
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} — {self.email}"


class AuditLog(models.Model):
    """Append-only record of sensitive staff/system actions."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="audit_entries")
    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=60, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=300)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit entries cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit entries cannot be deleted.")


class EmailLog(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(STATUS_PENDING, "Pending"), (STATUS_SENT, "Sent"), (STATUS_FAILED, "Failed")]

    to_email = models.EmailField(db_index=True)
    template = models.CharField(max_length=60)
    subject = models.CharField(max_length=200)
    # Bodies of sensitive emails (OTP, password reset) are never stored.
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    is_sensitive = models.BooleanField(default=False)
    attachment_ref = models.CharField(max_length=120, blank=True,
                                      help_text="e.g. invoice:42 — regenerated on retry, never stored here.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    related_object = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template} → {self.to_email} ({self.status})"
