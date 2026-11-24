from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class InvoiceSequence(models.Model):
    """Gap-free counter per document series and financial year. Locked with select_for_update."""

    SERIES_INVOICE = "INV"
    SERIES_CREDIT = "CN"

    series = models.CharField(max_length=3, choices=[(SERIES_INVOICE, "Tax invoice"), (SERIES_CREDIT, "Credit note")])
    financial_year = models.CharField(max_length=7, help_text="e.g. 2026-27")
    prefix = models.CharField(max_length=10)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["series", "financial_year", "prefix"], name="unique_series_fy")]

    def __str__(self):
        return f"{self.prefix} {self.financial_year} (next {self.next_number})"


class ImmutableMixin(models.Model):
    """Issued tax documents are never edited or deleted — corrections go through credit notes."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("allow_update", False):
            raise ValidationError(f"Issued {self._meta.verbose_name}s cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(f"Issued {self._meta.verbose_name}s cannot be deleted.")


class Invoice(ImmutableMixin):
    STATUS_ISSUED = "issued"
    STATUS_PARTLY_CREDITED = "partly_credited"
    STATUS_CREDITED = "credited"

    order = models.OneToOneField("orders.Order", on_delete=models.PROTECT, related_name="invoice")
    number = models.CharField(max_length=16, unique=True)
    financial_year = models.CharField(max_length=7, db_index=True)
    sequence_no = models.PositiveIntegerField()
    invoice_date = models.DateField(db_index=True)
    document_title = models.CharField(max_length=30, default="Tax Invoice")
    channel = models.CharField(max_length=10, default="website")

    seller = models.JSONField(help_text="Snapshot of seller name, address, GSTIN at issue time.")
    buyer_name = models.CharField(max_length=150)
    buyer_gstin = models.CharField(max_length=15, blank=True)
    billing_address = models.JSONField()
    shipping_address = models.JSONField()
    place_of_supply = models.CharField(max_length=2)
    place_of_supply_name = models.CharField(max_length=60)
    is_intra_state = models.BooleanField()
    reverse_charge = models.BooleanField(default=False)

    taxable_total = models.DecimalField(max_digits=12, decimal_places=2)
    cgst_total = models.DecimalField(max_digits=12, decimal_places=2)
    sgst_total = models.DecimalField(max_digits=12, decimal_places=2)
    igst_total = models.DecimalField(max_digits=12, decimal_places=2)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    round_off = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_in_words = models.CharField(max_length=300)
    terms = models.TextField(blank=True)
    order_number = models.CharField(max_length=20)
    order_date = models.DateField()
    payment_method = models.CharField(max_length=20)

    status = models.CharField(max_length=16, default=STATUS_ISSUED,
                              choices=[(STATUS_ISSUED, "Issued"), (STATUS_PARTLY_CREDITED, "Partly credited"),
                                       (STATUS_CREDITED, "Fully credited")])
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-sequence_no"]
        constraints = [models.UniqueConstraint(fields=["financial_year", "sequence_no"], name="unique_invoice_seq_fy")]

    def __str__(self):
        return self.number

    def get_pdf_url(self):
        from django.urls import reverse

        return reverse("invoicing:invoice_pdf", args=[self.number.replace("/", "-")])


class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="items")
    line_no = models.PositiveSmallIntegerField()
    description = models.CharField(max_length=250)
    sku = models.CharField(max_length=40, blank=True)
    hsn_code = models.CharField(max_length=8)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=3)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2,
                                     help_text="As charged (GST-inclusive if prices include GST).")
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    taxable_value = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    cgst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    is_shipping = models.BooleanField(default=False)

    class Meta:
        ordering = ["line_no"]
        constraints = [models.UniqueConstraint(fields=["invoice", "line_no"], name="unique_invoice_line")]

    def __str__(self):
        return f"{self.invoice.number} #{self.line_no}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Invoice lines cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Invoice lines cannot be deleted.")

    @property
    def half_rate(self):
        return self.gst_rate / 2


class CreditNote(ImmutableMixin):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="credit_notes")
    number = models.CharField(max_length=16, unique=True)
    financial_year = models.CharField(max_length=7)
    sequence_no = models.PositiveIntegerField()
    note_date = models.DateField()
    reason = models.CharField(max_length=200)
    lines = models.JSONField(help_text="Snapshot of credited lines.")
    taxable_total = models.DecimalField(max_digits=12, decimal_places=2)
    cgst_total = models.DecimalField(max_digits=12, decimal_places=2)
    sgst_total = models.DecimalField(max_digits=12, decimal_places=2)
    igst_total = models.DecimalField(max_digits=12, decimal_places=2)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2)
    grand_total = models.DecimalField(max_digits=12, decimal_places=2)
    amount_in_words = models.CharField(max_length=300)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-note_date", "-sequence_no"]
        constraints = [models.UniqueConstraint(fields=["financial_year", "sequence_no"], name="unique_cn_seq_fy")]

    def __str__(self):
        return self.number

    def get_pdf_url(self):
        from django.urls import reverse

        return reverse("invoicing:credit_note_pdf", args=[self.number.replace("/", "-")])
