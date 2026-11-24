"""Test fixtures shared by every app's tests. Never imported by production code."""
import struct
from decimal import Decimal

from apps.accounts.models import Address, User
from apps.core.models import SiteSettings
from apps.inventory.models import StockMovement
from apps.inventory.services import adjust_stock
from apps.products.models import Category, Product, ProductVariant, TaxClass

# A syntactically valid GSTIN with a correct check digit (state 03 = Punjab). Test data only.
TEST_GSTIN = "03AAAAA0000A1ZC"


def _box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4_bytes(seconds, timescale=1000, version=0):
    """A minimal MP4 container (no real frames) whose header says it lasts `seconds`.
    The moov box is placed after mdat, as many phone cameras do."""
    if version == 1:
        mvhd = b"\x01\x00\x00\x00" + b"\x00" * 16 + struct.pack(">IQ", timescale, int(seconds * timescale))
    else:
        mvhd = b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", timescale, int(seconds * timescale))
    mvhd += b"\x00" * 80
    return (_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2mp41") + _box(b"mdat", b"\x00" * 64)
            + _box(b"moov", _box(b"mvhd", mvhd)))


def webm_bytes(seconds):
    """A minimal WebM header (EBML + Segment of unknown size + Info) lasting `seconds`."""
    def el(eid, payload):
        return eid + bytes([0x80 | len(payload)]) + payload

    header = el(b"\x1a\x45\xdf\xa3", el(b"\x42\x82", b"webm"))
    info = el(b"\x2a\xd7\xb1", (1_000_000).to_bytes(3, "big")) + el(b"\x44\x89", struct.pack(">d", seconds * 1000))
    segment = b"\x18\x53\x80\x67" + b"\x01\xff\xff\xff\xff\xff\xff\xff" + el(b"\x15\x49\xa9\x66", info)
    return header + segment + b"\x1f\x43\xb6\x75\x80"


def configure_store(**overrides):
    store = SiteSettings.load()
    values = dict(legal_name="Test Traders", trade_name="Life Line Dynamics", gstin=TEST_GSTIN,
                  address_line1="1 Test Road", city="Testpur", state_code="03", pincode="140001",
                  shipping_flat_charge=Decimal("50.00"), free_shipping_threshold=Decimal("500.00"),
                  cod_enabled=True, cod_fee=Decimal("0.00"), prices_include_gst=True,
                  shipping_tax_mode=SiteSettings.SHIPPING_TAX_PRINCIPAL, round_off_invoice_total=False,
                  invoice_trigger=SiteSettings.INVOICE_ON_DISPATCH, support_email="help@example.com")
    values.update(overrides)
    for k, v in values.items():
        setattr(store, k, v)
    store.save()
    return store


def make_product(name="Test Agarbatti", price="105.00", mrp="120.00", rate="5.00", stock=10, sku=None,
                 hsn="3307"):
    tax, _ = TaxClass.objects.get_or_create(name=f"Rate {rate}", defaults={"hsn_code": hsn, "gst_rate": Decimal(rate)})
    cat, _ = Category.objects.get_or_create(slug="test-cat", defaults={"name": "Test category"})
    product = Product.objects.create(name=name, category=cat, tax_class=tax, status=Product.STATUS_PUBLISHED)
    variant = ProductVariant.objects.create(product=product, sku=sku or f"SKU-{product.pk}", mrp=Decimal(mrp),
                                            price=Decimal(price))
    if stock:
        adjust_stock(variant.pk, stock, StockMovement.OPENING)
        variant.refresh_from_db()
    return variant


def make_user(email="buyer@example.com", password="Str0ng-pass!", verified=True, **extra):
    return User.objects.create_user(email=email, password=password, full_name=extra.pop("full_name", "Asha Devi"),
                                    is_verified=verified, **extra)


def make_address(user, state_code="03", pincode="140001"):
    return Address.objects.create(user=user, full_name=user.full_name, phone="9876543210", line1="12 Temple Street",
                                  city="Testpur", state_code=state_code, pincode=pincode, is_default=True)


def place_test_order(user, variant, qty=1, method="cod", state_code="03", token=None, coupon=None):
    from apps.cart.models import Cart, CartItem
    from apps.orders.services import place_order

    cart, _ = Cart.objects.get_or_create(user=user)
    cart.items.all().delete()
    CartItem.objects.create(cart=cart, variant=variant, quantity=qty)
    cart.coupon = coupon
    cart.save()
    address = make_address(user, state_code=state_code)
    snap = address.as_snapshot()
    return place_order(user=user, cart=cart, billing=snap, shipping=snap, payment_method=method,
                       checkout_token=token or f"tok-{user.pk}-{variant.pk}-{qty}-{method}-{state_code}")
