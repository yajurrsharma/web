from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import CreditNote, Invoice, InvoiceItem, InvoiceSequence


class ReadOnly(admin.ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0
    fields = ("line_no", "description", "hsn_code", "quantity", "unit", "taxable_value", "gst_rate", "cgst",
              "sgst", "igst", "total")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(ReadOnly):
    list_display = ("number", "invoice_date", "order_number", "buyer_name", "taxable_total", "tax_total",
                    "grand_total", "status", "pdf")
    list_filter = ("financial_year", "status", "is_intra_state", "channel")
    search_fields = ("number", "order_number", "buyer_name", "buyer_gstin")
    date_hierarchy = "invoice_date"
    inlines = [InvoiceItemInline]

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("invoicing.view_invoice")

    @admin.display(description="PDF")
    def pdf(self, obj):
        url = reverse("invoicing:invoice_pdf", args=[obj.number.replace("/", "-")])
        return format_html('<a href="{}?view=1" target="_blank" rel="noopener">View</a>', url)


@admin.register(CreditNote)
class CreditNoteAdmin(ReadOnly):
    list_display = ("number", "note_date", "invoice", "reason", "grand_total")
    search_fields = ("number", "invoice__number")


@admin.register(InvoiceSequence)
class InvoiceSequenceAdmin(ReadOnly):
    list_display = ("series", "prefix", "financial_year", "next_number")
