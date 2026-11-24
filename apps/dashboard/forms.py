"""Simple staff forms for the dashboard (products, stock, categories, HSN & GST)."""
import os
import secrets

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from apps.campaigns.models import Campaign
from apps.cart.models import Promotion
from apps.core.models import Banner, SiteSettings
from apps.core.validators import VIDEO_EXTENSIONS, validate_image_upload, validate_video_upload
from apps.inventory.models import StockMovement
from apps.products.models import Category, Product, ProductVariant, TaxClass


class Styled:
    """Adds the dashboard CSS classes to every widget, so templates can render fields with one include."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                css = "form-check-input"
            elif isinstance(w, forms.Select):
                css = "form-select"
            else:
                css = "form-control"
            w.attrs["class"] = (w.attrs.get("class", "") + " " + css).strip()


def unique_sku():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        sku = "LLD-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not ProductVariant.objects.filter(sku=sku).exists():
            return sku


def unique_category_slug(name, exclude_pk=None):
    base = slugify(name)[:110] or "category"
    slug, n = base, 2
    while Category.objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


class ProductForm(Styled, forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "category", "tax_class", "unit", "status", "is_featured", "badge", "short_description",
                  "description", "key_features", "what_you_get", "best_for", "fragrance", "burn_time",
                  "ingredients", "manufacturer_name", "manufacturer_address", "packer_details",
                  "country_of_origin", "consumer_care",
                  "main_image", "main_image_alt", "video_url", "seo_title", "meta_description"]
        labels = {
            "name": "Product name", "tax_class": "HSN & GST rate", "unit": "Unit on invoice",
            "is_featured": "Show on home page (featured)", "main_image": "Main photo",
            "main_image_alt": "Photo description (for blind users & Google)", "video_url": "YouTube video link",
            "seo_title": "Google title", "meta_description": "Google description",
        }
        help_texts = {
            "status": "Draft = hidden from customers. Published = visible in the shop. Archived = old product.",
            "tax_class": "Add a new HSN / GST rate under Products → HSN & GST.",
            "short_description": "One or two lines shown near the price.",
            "description": "Full details. Leave a blank line between paragraphs.",
            "main_image": "JPG, PNG or WebP. A square photo looks best. Phone photos are fine — they are "
                          "resized automatically to save space.",
            "seo_title": "Optional. Leave blank to use the product name.",
            "meta_description": "Optional. Up to 160 characters.",
        }
        widgets = {
            "short_description": forms.Textarea(attrs={"rows": 2}),
            "description": forms.Textarea(attrs={"rows": 6}),
            "key_features": forms.Textarea(attrs={"rows": 4, "placeholder": "Low smoke\nLong lasting fragrance"}),
            "what_you_get": forms.Textarea(attrs={"rows": 3, "placeholder": "24 incense sticks\n1 ceramic stand"}),
            "best_for": forms.Textarea(attrs={"rows": 3, "placeholder": "Daily puja\nMeditation\nGifting"}),
            "main_image": forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.order_by("sort_order", "name")
        self.fields["category"].empty_label = "-- Select category --"
        self.fields["tax_class"].empty_label = "-- Select HSN & GST --"
        self.fields["country_of_origin"].required = False

    def clean_country_of_origin(self):
        return (self.cleaned_data.get("country_of_origin") or "").strip() or "India"


class VariantForm(Styled, forms.ModelForm):
    """Price, SKU and shipping details for one pack size. Stock is never edited here."""

    class Meta:
        model = ProductVariant
        fields = ["name", "net_quantity", "sku", "mrp", "price", "cost_price", "low_stock_threshold", "weight_grams",
                  "length_cm", "width_cm", "height_cm", "is_active", "sort_order"]
        labels = {
            "name": "Pack size / variant name", "sku": "SKU (item code)", "mrp": "MRP (₹)",
            "price": "Selling price (₹)", "cost_price": "Purchase / cost price (₹)",
            "low_stock_threshold": "Low-stock alert at", "weight_grams": "Weight with packing (grams)",
            "length_cm": "Length (cm)", "width_cm": "Width (cm)", "height_cm": "Height (cm)",
            "is_active": "Available for sale", "sort_order": "Display order",
        }
        help_texts = {
            "name": "e.g. Standard, Pack of 12, 100 g",
            "sku": "Leave blank to create one automatically.",
            "cost_price": "Only staff can see this. Never shown to customers.",
            "low_stock_threshold": "You get a warning when stock falls to this number.",
            "sort_order": "Smaller numbers are shown first.",
        }

    def __init__(self, *args, show_cost=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sku"].required = False
        if not show_cost:
            del self.fields["cost_price"]

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip().upper()
        return sku or (self.instance.sku if self.instance.pk else unique_sku())


class NewVariantForm(VariantForm):
    opening_stock = forms.IntegerField(
        label="Opening stock", min_value=0, initial=0,
        help_text="How many you have right now. Later changes go through Add Stock.")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if not files and self.required:
            raise ValidationError("Choose at least one file.")
        cleaned, errors = [], []
        for f in files:
            try:
                f = super().clean(f, initial)
                if is_video(f):
                    validate_video_upload(f)
                else:
                    validate_image_upload(f)
            except ValidationError as exc:
                errors.extend(f"{getattr(f, 'name', 'File')}: {m}" for m in exc.messages)
            else:
                cleaned.append(f)
        if errors:
            raise ValidationError(errors)
        return cleaned


def is_video(f):
    return os.path.splitext(f.name)[1].lower() in VIDEO_EXTENSIONS


class GalleryUploadForm(Styled, forms.Form):
    files = MultipleFileField(
        label="More photos & short video",
        widget=MultipleFileInput(attrs={"accept": "image/jpeg,image/png,image/webp,video/mp4,video/webm",
                                        "data-max-video-seconds": settings.MAX_VIDEO_SECONDS}))

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        self.fields["files"].help_text = (
            f"You can select many photos at once. Photos: JPG, PNG or WebP — they are resized automatically. "
            f"Video: MP4 or WebM, max {settings.MAX_VIDEO_SECONDS} seconds, "
            f"max {settings.MAX_VIDEO_UPLOAD_SIZE // (1024 * 1024)} MB.")

    def clean_files(self):
        files = self.cleaned_data.get("files") or []
        new_videos = sum(1 for f in files if is_video(f))
        new_photos = len(files) - new_videos
        have = {"image": 0, "video": 0}
        if self.product is not None and self.product.pk:
            for kind in self.product.media.values_list("kind", flat=True):
                have[kind] = have.get(kind, 0) + 1
        if have["video"] + new_videos > settings.MAX_VIDEOS_PER_PRODUCT:
            if settings.MAX_VIDEOS_PER_PRODUCT == 0:
                raise ValidationError("Video uploads are switched off to save hosting space. Upload the video to "
                                      "YouTube and paste its link in “YouTube video link” instead.")
            raise ValidationError(f"A product can have at most {settings.MAX_VIDEOS_PER_PRODUCT} uploaded "
                                  f"video(s) (it has {have['video']}). Remove one first, or put the video on "
                                  "YouTube and paste its link in “YouTube video link” — that uses no hosting space.")
        if have["image"] + new_photos > settings.MAX_PHOTOS_PER_PRODUCT:
            raise ValidationError(f"A product can have at most {settings.MAX_PHOTOS_PER_PRODUCT} extra photos "
                                  f"(it has {have['image']}). Remove some first.")
        return files


class CategoryForm(Styled, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "tagline", "parent", "description", "image", "is_active", "sort_order", "seo_title",
                  "meta_description"]
        labels = {"parent": "Main category", "is_active": "Show in shop", "sort_order": "Display order",
                  "seo_title": "Google title", "meta_description": "Google description"}
        help_texts = {"parent": "Leave blank if this is a main category.",
                      "sort_order": "Smaller numbers are shown first."}
        widgets = {"description": forms.Textarea(attrs={"rows": 3}),
                   "image": forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Category.objects.order_by("sort_order", "name")
        if self.instance.pk:
            qs = qs.exclude(pk__in=self.instance.descendant_ids())
        self.fields["parent"].queryset = qs
        self.fields["parent"].empty_label = "-- None (main category) --"

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.slug:
            obj.slug = unique_category_slug(obj.name, exclude_pk=obj.pk)
        if commit:
            obj.save()
        return obj


class TaxClassForm(Styled, forms.ModelForm):
    class Meta:
        model = TaxClass
        fields = ["name", "hsn_code", "gst_rate", "confirmed_by_ca", "notes"]
        labels = {"name": "Name", "gst_rate": "GST rate (%)", "confirmed_by_ca": "Confirmed by my CA / accountant"}
        help_texts = {"name": "e.g. Agarbatti, Dhoop / sambrani cups",
                      "hsn_code": "4, 6 or 8 digits, as given by your CA.",
                      "gst_rate": "Total GST (e.g. 5, 12 or 18). Invoices split it into CGST+SGST or IGST."}


class StockInForm(Styled, forms.Form):
    REASONS = [(StockMovement.PURCHASE, "New stock received (purchase)"),
               (StockMovement.ADJUSTMENT, "Correction after stock count (+ or −)"),
               (StockMovement.DAMAGE, "Damaged / expired / lost (removes stock)")]

    variant = forms.ModelChoiceField(
        label="Product", queryset=ProductVariant.objects.none(), empty_label="-- Select product --")
    change = forms.IntegerField(label="Quantity",
                                help_text="For a correction you can enter a minus number, e.g. −3.")
    reason = forms.ChoiceField(label="Reason", choices=REASONS)
    note = forms.CharField(label="Note (optional)", max_length=200, required=False,
                           widget=forms.TextInput(attrs={"placeholder": "e.g. Supplier bill no. 245"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["variant"].queryset = (ProductVariant.objects.select_related("product")
                                           .exclude(product__status=Product.STATUS_ARCHIVED)
                                           .order_by("product__name", "sort_order"))
        self.fields["variant"].label_from_instance = lambda v: f"{v} ({v.sku}) — in stock: {v.stock}"

    def clean(self):
        data = super().clean()
        change, reason = data.get("change"), data.get("reason")
        if change == 0:
            self.add_error("change", "Enter a quantity other than zero.")
        elif change is not None and reason == StockMovement.PURCHASE and change < 0:
            self.add_error("change", "New stock must be a positive number. Use 'Correction' to reduce stock.")
        elif change is not None and reason == StockMovement.DAMAGE:
            data["change"] = -abs(change)
        return data


class BannerForm(Styled, forms.ModelForm):
    class Meta:
        model = Banner
        fields = ["image", "heading", "subheading", "link_url", "link_text", "is_active", "sort_order"]
        labels = {"image": "Banner picture", "heading": "Title", "subheading": "Small text (optional)",
                  "link_url": "Opens when clicked", "link_text": "Button text", "is_active": "Show on home page",
                  "sort_order": "Display order"}
        help_texts = {
            "image": "JPG, PNG or WebP. Wide pictures work best (about 2:1, e.g. 1600 × 800). The whole picture is "
                     "always shown — text on it will not be cut off. Leave empty for a coloured text banner.",
            "heading": "Also read out to blind users, so describe the offer, e.g. 'Divine Combo — now ₹250'.",
            "link_url": "A page of this website, e.g. /shop/ or /category/diya-batti/ or a product page link.",
            "sort_order": "Smaller numbers are shown first.",
        }
        widgets = {"image": forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"})}

    def clean_link_url(self):
        url = (self.cleaned_data.get("link_url") or "").strip()
        if not url:
            return url
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if url.startswith("/") and not url.startswith("//"):
            return url  # a page of this site
        if parsed.scheme in ("http", "https") and parsed.netloc == urlparse(settings.SITE_URL).netloc:
            # A pasted link to this site: keep only the path so it still works on the real domain later.
            return (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        if parsed.scheme == "https" and parsed.netloc:
            return url  # another website, https only
        raise ValidationError("Enter a page of this site starting with / (e.g. /shop/) or a full https:// link.")


class StoreSettingsForm(Styled, forms.ModelForm):
    """The everyday shop switches. Legal/GST/invoice settings stay in the admin panel."""

    SECTIONS = [
        ("Contact details", "Shown in the footer, on the Contact page and on invoices.",
         ["support_email", "support_phone", "whatsapp_number"]),
        ("Grievance officer (required by the E-Commerce Rules, 2020)", "Shown on the Grievance page linked in the "
         "footer. Complaints must be acknowledged within 48 hours and resolved within 1 month.",
         ["grievance_officer_name", "grievance_officer_designation", "grievance_email", "grievance_phone",
          "grievance_hours"]),
        ("Cancellations & problem reports", "", ["cancel_allowed_until", "return_window_days"]),
        ("Offer bar (top of every page)", "Write short messages separated by |. Offers you mark "
         "'show in offer bar' (Marketing → Offers) are added automatically.", ["announcement_text"]),
        ("Delivery estimate (\"Check delivery\" on product pages)", "Working days after the order; Sundays are "
         "skipped. Also set which PIN codes you deliver to in the admin (Store & GST details → Shipping).",
         ["delivery_days_local_min", "delivery_days_local_max", "delivery_days_india_min",
          "delivery_days_india_max"]),
        ("Prepaid benefit", "Reward customers who pay in advance — fewer risky COD orders and returns.",
         ["prepaid_discount_percent", "prepaid_free_shipping"]),
        ("Payment methods", "Turn each way to pay on or off. Customers only see the ones that are on.",
         ["cod_enabled", "cod_min_order", "cod_max_order", "cod_fee",
          "online_payments_enabled", "razorpay_show_upi", "razorpay_show_cards", "razorpay_show_netbanking",
          "razorpay_show_wallets", "payment_timeout_minutes"]),
        ("Direct UPI (paid straight to you, no gateway fee)", "Customer scans your QR code and enters the UTR. "
         "You confirm on the order page after the money arrives.",
         ["upi_enabled", "upi_id", "upi_payee_name"]),
        ("Bank transfer (NEFT / IMPS / RTGS)", "Customer transfers to your account and enters the reference. "
         "You confirm on the order page after the money arrives.",
         ["bank_transfer_enabled", "bank_account_name", "bank_account_number", "bank_ifsc", "bank_name",
          "bank_branch", "manual_payment_hours"]),
        ("Shiprocket (courier booking)", "Box size and weight are sent to Shiprocket to get courier prices.",
         ["shiprocket_pickup_location", "shiprocket_pickup_pincode", "parcel_length_cm", "parcel_breadth_cm", "parcel_height_cm",
          "parcel_default_weight_g"]),
        ("Home page look", "Trust points: one per line as  icon | title | short text.",
         ["trust_items", "shloka_text", "shloka_meaning"]),
        ("Product cards", "", ["card_text_limit"]),
        ("Home page slider", "", ["hero_interval_seconds"]),
        ("Colour theme", "Change for festivals — the whole shop updates immediately.",
         ["theme", "theme_header_color", "theme_strip_color", "theme_accent_color"]),
    ]

    class Meta:
        model = SiteSettings
        fields = ["support_email", "support_phone", "whatsapp_number", "grievance_officer_name",
                  "grievance_officer_designation", "grievance_email", "grievance_phone", "grievance_hours",
                  "cancel_allowed_until", "return_window_days",
                  "announcement_text", "prepaid_discount_percent", "prepaid_free_shipping",
                  "delivery_days_local_min", "delivery_days_local_max", "delivery_days_india_min",
                  "delivery_days_india_max",
                  "cod_enabled", "cod_min_order", "cod_max_order", "cod_fee", "online_payments_enabled",
                  "razorpay_show_upi", "razorpay_show_cards", "razorpay_show_netbanking", "razorpay_show_wallets",
                  "payment_timeout_minutes", "upi_enabled", "upi_id", "upi_payee_name", "bank_transfer_enabled",
                  "bank_account_name", "bank_account_number", "bank_ifsc", "bank_name", "bank_branch",
                  "manual_payment_hours", "shiprocket_pickup_location", "shiprocket_pickup_pincode", "parcel_length_cm", "parcel_breadth_cm",
                  "parcel_height_cm", "parcel_default_weight_g", "trust_items", "shloka_text", "shloka_meaning",
                  "card_text_limit", "hero_interval_seconds", "theme",
                  "theme_header_color", "theme_strip_color", "theme_accent_color"]
        labels = {"whatsapp_number": "WhatsApp number", "cancel_allowed_until": "Customers can cancel",
                  "cod_enabled": "Cash on Delivery", "cod_min_order": "COD minimum order (₹)",
                  "cod_max_order": "COD maximum order (₹, blank = no limit)", "cod_fee": "COD fee (₹)",
                  "online_payments_enabled": "Online payment (Razorpay)",
                  "payment_timeout_minutes": "Time to pay online (minutes)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("theme_header_color", "theme_strip_color", "theme_accent_color"):
            self.fields[name].widget.attrs.update({"placeholder": "#rrggbb", "data-color-input": ""})
        self.fields["prepaid_discount_percent"].required = False

    def clean_prepaid_discount_percent(self):
        return self.cleaned_data.get("prepaid_discount_percent") or 0

    def clean_bank_ifsc(self):
        return (self.cleaned_data.get("bank_ifsc") or "").strip().upper()

    def clean_upi_id(self):
        return (self.cleaned_data.get("upi_id") or "").strip()

    def sections(self):
        for title, note, names in self.SECTIONS:
            yield title, note, [self[n] for n in names]


class CampaignForm(Styled, forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["subject", "heading", "message", "products", "button_text", "button_link"]
        widgets = {"message": forms.Textarea(attrs={"rows": 8}),
                   "products": forms.SelectMultiple(attrs={"size": 8})}
        help_texts = {"products": "Optional. Hold Ctrl to pick up to 6 products. Only published products are shown "
                                  "in the email."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["products"].queryset = Product.objects.filter(status=Product.STATUS_PUBLISHED).order_by("name")

    def clean_products(self):
        products = self.cleaned_data.get("products")
        if products is not None and len(products) > 6:
            raise ValidationError("Choose at most 6 products.")
        return products

    def clean_button_link(self):
        link = (self.cleaned_data.get("button_link") or "").strip()
        if link and (not link.startswith("/") or link.startswith("//")):
            raise ValidationError("Enter a page of this website starting with /, e.g. /shop/.")
        return link


class PromotionForm(Styled, forms.ModelForm):
    class Meta:
        model = Promotion
        fields = ["name", "kind", "products", "buy_qty", "free_qty", "bundle_qty", "bundle_price", "min_amount",
                  "gift_variant", "starts_at", "ends_at", "show_in_bar", "is_active", "sort_order"]
        labels = {"name": "Offer name (customers see this)", "kind": "Type of offer",
                  "products": "Applies to products", "is_active": "Offer is ON", "starts_at": "Starts",
                  "ends_at": "Ends", "gift_variant": "Free gift"}
        help_texts = {"products": "Hold Ctrl to pick several. Leave none selected = all products.",
                      "starts_at": "Optional. Blank = starts now."}
        widgets = {"products": forms.SelectMultiple(attrs={"size": 8}),
                   "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                   "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["products"].queryset = Product.objects.exclude(status=Product.STATUS_ARCHIVED).order_by("name")
        self.fields["gift_variant"].queryset = (ProductVariant.objects.filter(is_active=True)
                                                .select_related("product").order_by("product__name"))
        self.fields["gift_variant"].label_from_instance = lambda v: f"{v} ({v.sku}) — in stock: {v.stock}"
        for name in ("starts_at", "ends_at"):
            self.fields[name].input_formats = ["%Y-%m-%dT%H:%M"]
