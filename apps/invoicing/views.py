from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from .models import CreditNote, Invoice
from .pdf import render_credit_note_pdf, render_invoice_pdf


def _can_view(user, invoice):
    return invoice.order.user_id == user.pk or user.has_perm("invoicing.view_invoice")


def _pdf_response(content, filename, inline):
    resp = HttpResponse(content, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    resp["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    resp["Cache-Control"] = "private, no-store"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@login_required
@require_GET
def invoice_pdf(request, number):
    invoice = get_object_or_404(Invoice.objects.select_related("order"), number=number.replace("-", "/"))
    if not _can_view(request.user, invoice):
        raise Http404  # don't reveal that the invoice exists
    return _pdf_response(render_invoice_pdf(invoice), f"{invoice.number.replace('/', '-')}.pdf",
                         inline=request.GET.get("view") == "1")


@login_required
@require_GET
def credit_note_pdf(request, number):
    note = get_object_or_404(CreditNote.objects.select_related("invoice__order"), number=number.replace("-", "/"))
    if not _can_view(request.user, note.invoice):
        raise Http404
    return _pdf_response(render_credit_note_pdf(note), f"{note.number.replace('/', '-')}.pdf",
                         inline=request.GET.get("view") == "1")
