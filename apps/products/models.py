from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Min, Prefetch, Q
from django.urls import reverse
from django.utils.text import slugify

from apps.core.validators import safe_upload_path, shrink_image, validate_image_upload, validate_video_upload

ZERO = Decimal("0.00")

hsn_validator = RegexValidator(r"^\d{4}(\d{2})?(\d{2})?$", "HSN must be 4, 6 or 8 digits.")


class TaxClass(models.Model):
    """
    A GST classification: HSN code + rate. Products point to one of these, so
    your CA can confirm a short list once instead of checking every product.
    """

    name = models.CharField(max_length=100, unique=True, help_text="e.g. 'Agarbatti', 'Dhoop / sambrani cups'")
    hsn_code = models.CharField("HSN code", max_length=8, validators=[hsn_validator])
    gst_rate = models.DecimalField(
        "GST rate (%)", max_digits=5, decimal_places=2,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("40"))],
        help_text="Total GST rate (CGST+SGST or IGST). Enter the rate your CA confirms for this HSN.")
    confirmed_by_ca = models.BooleanField(
        "Confirmed by CA", default=False,
        help_text="Tick only after your accountant has confirmed this HSN and rate.")
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "tax class (HSN & GST)"
        verbose_name_plural = "tax classes (HSN & GST)"

    def __str__(self):
        return f"{self.name} — HSN {self.hsn_code} @ {self.gst_rate.normalize()}%"


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="children", help_text="Leave blank for a top-level category.")
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to=safe_upload_path("categories"), blank=True,
                              validators=[validate_image_upload])
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    tagline = models.CharField(max_length=80, blank=True,
                               help_text="One short line shown in the menu, e.g. 'Mini havan in a cup'.")
    seo_title = models.CharField(max_length=70, blank=True)
    meta_description = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return f"{self.parent.name} › {self.name}" if self.parent_id else self.name

    def clean(self):
        node = self.parent
        while node is not None:
            if node.pk == self.pk:
                raise ValidationError({"parent": "A category cannot be its own parent."})
            node = node.parent

    def save(self, *args, **kwargs):
        shrink_image(self.image)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("products:category", args=[self.slug])

    def descendant_ids(self):
        ids, frontier = [self.pk], [self.pk]
        while frontier:
            frontier = list(Category.objects.filter(parent_id__in=frontier).values_list("pk", flat=True))
            ids.extend(frontier)
        return ids


class ProductQuerySet(models.QuerySet):
    def storefront(self):
        """Published products in active categories that have at least one active variant."""
        active_variants = ProductVariant.objects.filter(is_active=True).order_by("sort_order", "price")
        return (self.filter(status=Product.STATUS_PUBLISHED, category__is_active=True,
                            variants__is_active=True)
                .annotate(min_price=Min("variants__price", filter=Q(variants__is_active=True)))
                .select_related("category")
                .prefetch_related(Prefetch("variants", queryset=active_variants, to_attr="active_variants"))
                .distinct())


class Product(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [(STATUS_DRAFT, "Draft (hidden)"), (STATUS_PUBLISHED, "Published"),
                      (STATUS_ARCHIVED, "Archived")]

    UNIT_CHOICES = [("PCS", "Pieces"), ("NOS", "Numbers"), ("BOX", "Box"), ("PAC", "Packs"),
                    ("SET", "Sets"), ("GMS", "Grams"), ("KGS", "Kilograms")]
    BADGE_CHOICES = [("bestseller", "Bestseller"), ("top_pick", "Top pick"), ("new", "New launch"),
                     ("festive", "Festive special"), ("founders_pick", "Founder's pick"),
                     ("value_combo", "Value combo"), ("limited", "Limited edition")]

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    tax_class = models.ForeignKey(TaxClass, on_delete=models.PROTECT, related_name="products",
                                  help_text="Sets the HSN code and GST rate used on invoices.")
    unit = models.CharField("Invoice unit (UQC)", max_length=3, choices=UNIT_CHOICES, default="PCS")
    short_description = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True, help_text="Plain text; blank lines separate paragraphs.")
    main_image = models.ImageField(upload_to=safe_upload_path("products"), blank=True,
                                   validators=[validate_image_upload])
    main_image_alt = models.CharField(max_length=150, blank=True)
    video_url = models.URLField(blank=True, help_text="Optional YouTube link for longer videos.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    is_featured = models.BooleanField(default=False, db_index=True)
    badge = models.CharField(
        "Badge on product card", max_length=20, choices=BADGE_CHOICES, blank=True,
        help_text="Only choose a badge that is true (e.g. 'Bestseller' only if it really sells the most).")

    # --- Details shown on the product page (what customers ask before buying) ---
    what_you_get = models.TextField("What you get", blank=True,
                                    help_text="One item per line, e.g. '24 incense sticks' / 'Ceramic stand'.")
    key_features = models.TextField(blank=True, help_text="One feature per line. Only claims you can prove.")
    best_for = models.TextField("Best for", blank=True, help_text="One use per line, e.g. 'Daily puja'.")
    fragrance = models.CharField(max_length=100, blank=True, help_text="e.g. Sandalwood, Rose")
    burn_time = models.CharField(max_length=60, blank=True, help_text="e.g. 35–40 minutes per stick")
    ingredients = models.CharField(max_length=300, blank=True)

    # --- Legal Metrology declarations for products sold online ---
    manufacturer_name = models.CharField("Manufactured by", max_length=150, blank=True)
    manufacturer_address = models.CharField("Manufacturer address", max_length=300, blank=True)
    packer_details = models.CharField("Packed / marketed by (name & address)", max_length=300, blank=True,
                                      help_text="Leave blank if the same as the manufacturer.")
    country_of_origin = models.CharField(max_length=60, default="India")
    consumer_care = models.CharField("Consumer care", max_length=200, blank=True,
                                     help_text="Phone / email for complaints. Blank = your store support details.")

    # Kept up to date from approved reviews, so product cards need no extra queries.
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=0, editable=False)
    rating_count = models.PositiveIntegerField(default=0, editable=False)
    seo_title = models.CharField(max_length=70, blank=True)
    meta_description = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "category"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        shrink_image(self.main_image)
        if not self.slug:
            base = slugify(self.name)[:150] or "product"
            slug, n = base, 2
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("products:detail", args=[self.slug])

    @property
    def is_published(self):
        return self.status == self.STATUS_PUBLISHED

    @property
    def youtube_id(self):
        """The 11-character video id from any usual YouTube link (watch, youtu.be, shorts, embed), else ''."""
        import re
        from urllib.parse import parse_qs, urlparse

        url = urlparse(self.video_url or "")
        host = (url.hostname or "").lower().removeprefix("www.").removeprefix("m.")
        vid = ""
        if host == "youtu.be":
            vid = url.path.lstrip("/").split("/")[0]
        elif host in ("youtube.com", "youtube-nocookie.com"):
            if url.path == "/watch":
                vid = parse_qs(url.query).get("v", [""])[0]
            elif url.path.startswith(("/shorts/", "/embed/", "/live/")):
                vid = url.path.split("/")[2] if len(url.path.split("/")) > 2 else ""
        return vid if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or "") else ""

    @property
    def gst_rate(self):
        return self.tax_class.gst_rate

    @property
    def hsn_code(self):
        return self.tax_class.hsn_code

    @property
    def has_details(self):
        return any([self.what_you_get, self.key_features, self.best_for])

    @staticmethod
    def _lines(text):
        return [line.strip(" •-✔\t") for line in (text or "").splitlines() if line.strip(" •-✔\t")]

    def what_you_get_list(self):
        return self._lines(self.what_you_get)

    def key_features_list(self):
        return self._lines(self.key_features)

    def best_for_list(self):
        return self._lines(self.best_for)

    def refresh_rating(self):
        """Recalculate the stars from approved reviews only."""
        from django.db.models import Avg, Count

        agg = self.reviews.filter(status=Review.APPROVED).aggregate(avg=Avg("rating"), n=Count("id"))
        self.rating_count = agg["n"] or 0
        self.rating_avg = Decimal(str(round(agg["avg"] or 0, 2)))
        Product.objects.filter(pk=self.pk).update(rating_avg=self.rating_avg, rating_count=self.rating_count)

    def default_variant(self):
        variants = getattr(self, "active_variants", None)
        if variants is None:
            variants = list(self.variants.filter(is_active=True).order_by("sort_order", "price"))
        in_stock = [v for v in variants if v.stock > 0]
        return (in_stock or variants or [None])[0]

    def gallery(self):
        return self.media.order_by("sort_order", "id")


class ProductVariant(models.Model):
    """The sellable unit. Every product has at least one (e.g. 'Standard' or 'Pack of 12')."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=80, default="Standard", help_text="e.g. 'Pack of 12', '100 g'")
    net_quantity = models.CharField("Net quantity", max_length=60, blank=True,
                                    help_text="As printed on the pack, e.g. '24 sticks (60 g)' or '140 g, ~50 batti'.")
    sku = models.CharField("SKU", max_length=40, unique=True)
    mrp = models.DecimalField("MRP (₹)", max_digits=10, decimal_places=2, validators=[MinValueValidator(ZERO)])
    price = models.DecimalField("Selling price (₹)", max_digits=10, decimal_places=2,
                                validators=[MinValueValidator(Decimal("0.01"))])
    cost_price = models.DecimalField("Cost price (₹, staff only)", max_digits=10, decimal_places=2,
                                     null=True, blank=True, validators=[MinValueValidator(ZERO)])
    stock = models.PositiveIntegerField(default=0, help_text="Change via stock adjustments after creation.")
    low_stock_threshold = models.PositiveIntegerField(default=5)
    weight_grams = models.PositiveIntegerField("Shipping weight (g)", default=0)
    length_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    width_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "price"]
        permissions = [("view_cost_price", "Can see cost prices")]
        constraints = [
            models.CheckConstraint(condition=Q(price__gt=0), name="variant_price_positive"),
        ]

    def __str__(self):
        return f"{self.product.name} — {self.name}" if self.name != "Standard" else self.product.name

    def clean(self):
        if self.price is not None and self.mrp is not None and self.price > self.mrp:
            raise ValidationError({"price": "Selling price cannot be more than MRP."})
        if self.sku:
            self.sku = self.sku.strip().upper()

    @property
    def display_name(self):
        return str(self)

    @property
    def discount_percent(self):
        if self.mrp and self.price < self.mrp:
            return int(((self.mrp - self.price) / self.mrp * 100).to_integral_value())
        return 0

    @property
    def savings(self):
        """Rupees saved against MRP (0 if no discount)."""
        return self.mrp - self.price if self.mrp and self.price < self.mrp else Decimal("0")

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def is_low_stock(self):
        return 0 < self.stock <= self.low_stock_threshold

    @property
    def is_purchasable(self):
        return self.is_active and self.product.status == Product.STATUS_PUBLISHED and self.stock > 0


class ProductMedia(models.Model):
    KIND_IMAGE = "image"
    KIND_VIDEO = "video"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="media")
    kind = models.CharField(max_length=5, choices=[(KIND_IMAGE, "Image"), (KIND_VIDEO, "Short video (MP4/WebM)")],
                            default=KIND_IMAGE)
    file = models.FileField(upload_to=safe_upload_path("products/gallery"))
    alt_text = models.CharField(max_length=150, blank=True, help_text="Describe the image for screen readers.")
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "gallery item"

    def __str__(self):
        return f"{self.product.name} {self.kind} #{self.pk}"

    def save(self, *args, **kwargs):
        if self.kind == self.KIND_IMAGE:
            shrink_image(self.file)
        super().save(*args, **kwargs)

    def clean(self):
        if not self.file:
            return
        if getattr(self.file, "_committed", True):
            return  # unchanged existing file
        if self.kind == self.KIND_IMAGE:
            validate_image_upload(self.file)
        else:
            validate_video_upload(self.file)


class Review(models.Model):
    """A rating from a customer whose order with this product was delivered. Shown only after staff approve it."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STATUS_CHOICES = [(PENDING, "Waiting for check"), (APPROVED, "Published"), (REJECTED, "Not published")]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="reviews")
    order_item = models.ForeignKey("orders.OrderItem", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", help_text="The delivered purchase that makes this a verified review.")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=100, blank=True)
    body = models.TextField("Review", max_length=2000, blank=True)
    photo = models.ImageField(upload_to=safe_upload_path("reviews"), blank=True, validators=[validate_image_upload])
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    moderated_at = models.DateTimeField(null=True, blank=True)
    moderated_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "product"], name="one_review_per_product")]

    def __str__(self):
        return f"{self.product.name} — {self.rating}★ by {self.user}"

    @property
    def author_name(self):
        """First name and last initial only (privacy), e.g. 'Asha D.'"""
        parts = (self.user.full_name or "Customer").split()
        return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")

    def save(self, *args, **kwargs):
        shrink_image(self.photo)
        super().save(*args, **kwargs)
