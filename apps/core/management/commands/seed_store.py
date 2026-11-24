"""
Create starter data so the site is usable immediately.

Everything created here is a PLACEHOLDER — prices, stock, HSN codes and GST
rates must be replaced with your real figures (confirmed by your CA) before
launch. Product descriptions avoid any health, purity or certification claims.

    python manage.py seed_store            # categories, tax classes, products, pages, banner
    python manage.py seed_store --pages-only
"""
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.models import Banner, Page, SiteSettings
from apps.inventory.models import StockMovement
from apps.inventory.services import adjust_stock
from apps.products.models import Category, Product, ProductVariant, TaxClass

PLACEHOLDER = "[PLACEHOLDER — replace before launch]"

PAGES = [
    ("about", "About us", 1, f"""## Our story

Life Line Dynamics brings you devotional and puja essentials — agarbatti, dhoop, sambrani cups and ghee diya batti — for your daily prayers and special occasions.

{PLACEHOLDER} Tell your customers who you are, where you are based and what makes your products special. Only include facts you can stand behind.

## Our range

Devleela Premium Agarbatti, PadamGandha Panchagavya Sambrani Cup, Dev Vandana and Dev Sadhana Ghee Diya Batti, with more products added over time."""),
    ("faq", "Frequently asked questions", 2, f"""## How long does delivery take?

{PLACEHOLDER} Orders are usually dispatched within 1–2 working days. Delivery time depends on your location.

## Which payment methods do you accept?

UPI, debit/credit cards and net banking through Razorpay, and Cash on Delivery where available.

## Can I cancel my order?

Yes, before it is processed — open the order in My Orders and choose Cancel. Paid orders are refunded to the original payment method.

## How do I get a GST invoice?

A GST invoice is emailed to you and can be downloaded from My Orders once it has been issued. Enter your GSTIN at checkout for a business purchase.

## How can I contact you?

Use the Contact us page, or write to the support email shown in the footer."""),
    ("shipping-policy", "Shipping policy", 3, f"""## Delivery areas

{PLACEHOLDER} We deliver across India. Some remote PIN codes may not be serviceable; checkout will tell you.

## Charges

Delivery charges, any free-delivery threshold and any Cash on Delivery fee are shown in your cart before you pay.

## Dispatch & tracking

Orders are usually dispatched within {PLACEHOLDER} working days. Once shipped, the courier name and tracking number appear in My Orders and in your shipping email."""),
    ("return-refund-policy", "Cancellation & refund policy", 4, f"""## Cancellation

You can cancel an order online until it starts processing. After that, please contact us.

## Returns

{PLACEHOLDER} Returns are accepted within the number of days stated at checkout for items that arrive damaged, defective or incorrect. Please share photos when you raise the request. Returned items are inspected before a refund is approved.

## Refunds

Approved refunds for online payments go back to your original payment method; banks usually take 5–7 working days. Cash on Delivery refunds are made by bank or UPI transfer.

## Non-returnable items

{PLACEHOLDER} List any items that cannot be returned once opened, for hygiene or safety reasons."""),
    ("privacy-policy", "Privacy policy", 5, f"""{PLACEHOLDER} Have this reviewed by a legal professional.

## What we collect

Your name, email, mobile number and delivery/billing addresses; your GSTIN if you give it; your order history. Payments are processed by Razorpay — we never see or store your card number or CVV.

## Why we use it

To process and deliver orders, issue invoices, send order updates, provide customer support and meet legal and tax record-keeping obligations.

## Sharing

Only with service providers needed to run the store (payment gateway, courier, email provider, hosting) and authorities where the law requires.

## Retention

Account data is kept while your account is active. Order and invoice records are kept for the period required under GST and other laws.

## Your choices

You can update your profile and addresses in My Account, and ask us to close your account by contacting us. One-time codes are stored only in protected (hashed) form and expire within minutes.

## Contact

Write to the support email shown on this site."""),
    ("terms", "Terms and conditions", 6, f"""{PLACEHOLDER} Have this reviewed by a legal professional.

## Orders

All orders are subject to availability and confirmation. Prices are in Indian Rupees and include GST unless stated otherwise.

## Pricing errors

If a product is listed at an incorrect price due to an error, we may cancel the order and refund any amount paid.

## Use of products

Agarbatti, dhoop, sambrani and diyas involve open flame and smoke. Use them on a stable, heat-resistant surface, away from children, pets and anything flammable, in a ventilated space, and never leave them unattended.

## Governing law

These terms are governed by the laws of India. {PLACEHOLDER} Add your jurisdiction."""),
]

CATEGORIES = [
    ("Agarbatti", "agarbatti", "Incense sticks for daily puja."),
    ("Dhoop & Sambrani", "dhoop-sambrani", "Dhoop sticks and sambrani cups."),
    ("Diya Batti", "diya-batti", "Ready-to-use diya wicks."),
]

# (name, category slug, tax class key, sku, mrp, price, stock, short description)
PRODUCTS = [
    ("Devleela Premium Agarbatti", "agarbatti", "agarbatti", "LLD-DL-AGB", "150.00", "129.00", 40,
     "Premium agarbatti for daily prayers."),
    ("PadamGandha Panchagavya Sambrani Cup", "dhoop-sambrani", "dhoop", "LLD-PG-SMB", "199.00", "179.00", 30,
     "Sambrani cups for puja and havan."),
    ("Dev Vandana", "agarbatti", "agarbatti", "LLD-DV-AGB", "99.00", "89.00", 50,
     "Everyday agarbatti for home and temple."),
    ("Dev Sadhana Ghee Diya Batti", "diya-batti", "diya", "LLD-DS-GDB", "120.00", "110.00", 35,
     "Ready-to-light diya batti."),
]

TAX_CLASSES = {
    # key: (name, hsn, rate) — PLACEHOLDERS: your CA must confirm each one.
    "agarbatti": ("Agarbatti (placeholder — confirm with CA)", "3307", Decimal("5.00")),
    "dhoop": ("Dhoop / sambrani (placeholder — confirm with CA)", "3307", Decimal("5.00")),
    "diya": ("Diya batti (placeholder — confirm with CA)", "3406", Decimal("5.00")),
}


def placeholder_image(text, size=(800, 800)):
    """Plain brand-coloured tile with the product initials — no third-party artwork."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", size, "#FBF6EC")
    draw = ImageDraw.Draw(img)
    draw.rectangle([24, 24, size[0] - 24, size[1] - 24], outline="#B8892B", width=6)
    initials = "".join(w[0] for w in text.split()[:3]).upper()
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", size[0] // 5)
    except OSError:
        try:
            from pathlib import Path

            font = ImageFont.truetype(str(Path(__file__).resolve().parents[3] / "invoicing" / "fonts"
                                          / "DejaVuSans-Bold.ttf"), size[0] // 5)
        except OSError:
            font = ImageFont.load_default()
    box = draw.textbbox((0, 0), initials, font=font)
    draw.text(((size[0] - (box[2] - box[0])) / 2, (size[1] - (box[3] - box[1])) / 2 - 20), initials,
              fill="#6E1423", font=font)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85)
    return ContentFile(buf.getvalue(), name="placeholder.jpg")


class Command(BaseCommand):
    help = "Create placeholder catalogue, policy pages and a banner (safe to run more than once)."

    def add_arguments(self, parser):
        parser.add_argument("--pages-only", action="store_true")
        parser.add_argument("--no-images", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        SiteSettings.load()
        for slug, title, order, body in PAGES:
            _, created = Page.objects.get_or_create(slug=slug, defaults={"title": title, "body": body,
                                                                         "sort_order": order})
            self.stdout.write(f"{'Created' if created else 'Kept'} page: {title}")
        if opts["pages_only"]:
            return
        if not Banner.objects.exists():
            Banner.objects.create(heading="Pure devotion, every day",
                                  subheading="Agarbatti, dhoop, sambrani cups and ghee diya batti",
                                  link_url="/shop/", link_text="Shop now")
        cats = {}
        for name, slug, desc in CATEGORIES:
            cats[slug], _ = Category.objects.get_or_create(slug=slug, defaults={"name": name, "description": desc})
        taxes = {}
        for key, (name, hsn, rate) in TAX_CLASSES.items():
            taxes[key], _ = TaxClass.objects.get_or_create(
                name=name, defaults={"hsn_code": hsn, "gst_rate": rate, "confirmed_by_ca": False,
                                     "notes": "Placeholder from seed_store. Replace with CA-confirmed values."})
        for name, cat, tax, sku, mrp, price, stock, short in PRODUCTS:
            if ProductVariant.objects.filter(sku=sku).exists():
                self.stdout.write(f"Kept product: {name}")
                continue
            product = Product.objects.create(
                name=name, category=cats[cat], tax_class=taxes[tax], status=Product.STATUS_PUBLISHED,
                is_featured=True, short_description=short,
                description=f"{short}\n\n{PLACEHOLDER} Add the real description, pack size, fragrance notes, "
                            "burn time and usage instructions.\n\nSafety: keep away from children and flammable "
                            "materials; never leave a lit product unattended.",
                main_image_alt=f"{name} — placeholder image")
            if not opts["no_images"]:
                product.main_image.save(f"{sku}.jpg", placeholder_image(name), save=True)
            variant = ProductVariant.objects.create(product=product, name="Standard", sku=sku, mrp=Decimal(mrp),
                                                    price=Decimal(price), low_stock_threshold=5, weight_grams=250)
            adjust_stock(variant.pk, stock, StockMovement.OPENING, note="Placeholder opening stock (seed_store)")
            self.stdout.write(self.style.SUCCESS(f"Created product: {name} (placeholder price Rs. {price})"))
        self.stdout.write(self.style.WARNING(
            "\nAll prices, stock, HSN codes and GST rates are PLACEHOLDERS. Update them in the admin and have your "
            "CA confirm each tax class before taking real orders."))
