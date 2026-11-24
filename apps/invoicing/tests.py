from datetime import date
from decimal import Decimal

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.core.models import SiteSettings
from apps.core.testing import configure_store, make_product, make_user, place_test_order
from apps.orders.models import Order
from apps.orders.services import cancel_order, change_status

from .models import CreditNote, Invoice, InvoiceSequence
from .pdf import render_credit_note_pdf, render_invoice_pdf
from .services import InvoiceError, financial_year_for, issue_credit_note, issue_invoice

D = Decimal


class InvoiceNumberingTests(TestCase):
    def setUp(self):
        configure_store()
        self.user = make_user()
        self.variant = make_product(price="105.00", stock=20)

    def test_financial_year(self):
        self.assertEqual(financial_year_for(date(2026, 3, 31)), "2025-26")
        self.assertEqual(financial_year_for(date(2026, 4, 1)), "2026-27")

    def test_consecutive_unique_numbers_within_16_chars(self):
        numbers = []
        for i in range(3):
            order = place_test_order(self.user, self.variant, token=f"n{i}")
            numbers.append(issue_invoice(order).number)
        fy = financial_year_for(date.today())
        short = fy[2:4] + fy[-2:]
        self.assertEqual(numbers, [f"LLD/{short}/00001", f"LLD/{short}/00002", f"LLD/{short}/00003"])
        self.assertTrue(all(len(n) <= 16 for n in numbers))
        self.assertEqual(InvoiceSequence.objects.get(series="INV").next_number, 4)

    def test_one_invoice_per_order(self):
        order = place_test_order(self.user, self.variant)
        first = issue_invoice(order)
        second = issue_invoice(order)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_blocked_without_gst_details(self):
        configure_store(gstin="")
        order = place_test_order(self.user, self.variant)
        with self.assertRaises(InvoiceError):
            issue_invoice(order)

    def test_not_for_unpaid_online_or_cancelled(self):
        online = place_test_order(self.user, self.variant, method="online", token="on")
        with self.assertRaises(InvoiceError):
            issue_invoice(online)
        cod = place_test_order(self.user, self.variant, token="cx")
        cancel_order(cod, reason="x")
        with self.assertRaises(InvoiceError):
            issue_invoice(cod)

    def test_issued_on_dispatch_automatically_and_emailed(self):
        order = place_test_order(self.user, self.variant)
        with self.captureOnCommitCallbacks(execute=True):
            change_status(order, Order.SHIPPED)
        self.assertTrue(Invoice.objects.filter(order=order).exists())
        invoice_mail = [m for m in mail.outbox if "GST invoice" in m.subject][0]
        self.assertEqual(invoice_mail.attachments[0][2], "application/pdf")

    def test_manual_trigger_does_not_auto_issue(self):
        configure_store(invoice_trigger=SiteSettings.INVOICE_MANUAL)
        order = place_test_order(self.user, self.variant)
        change_status(order, Order.SHIPPED)
        self.assertFalse(Invoice.objects.exists())


class GSTInvoiceContentTests(TestCase):
    def setUp(self):
        configure_store()
        self.user = make_user()

    def test_intra_state_cgst_sgst(self):
        order = place_test_order(self.user, make_product(price="105.00", rate="5.00", stock=5), state_code="03")
        inv = issue_invoice(order)
        self.assertTrue(inv.is_intra_state)
        item = inv.items.get(is_shipping=False)
        self.assertEqual((item.taxable_value, item.cgst, item.sgst, item.igst),
                         (D("100.00"), D("2.50"), D("2.50"), D("0.00")))
        self.assertEqual(inv.igst_total, D("0.00"))
        self.assertEqual(inv.taxable_total + inv.tax_total + inv.round_off, inv.grand_total)

    def test_inter_state_igst_and_place_of_supply(self):
        order = place_test_order(self.user, make_product(price="118.00", mrp="118.00", rate="18.00", stock=5),
                                 state_code="07")
        inv = issue_invoice(order)
        self.assertFalse(inv.is_intra_state)
        self.assertEqual(inv.place_of_supply, "07")
        self.assertEqual(inv.place_of_supply_name, "Delhi")
        self.assertEqual(inv.items.get(is_shipping=False).igst, D("18.00"))
        self.assertEqual(inv.cgst_total + inv.sgst_total, D("0.00"))

    def test_rule46_particulars_snapshotted(self):
        order = place_test_order(self.user, make_product(price="105.00", stock=5))
        inv = issue_invoice(order)
        self.assertEqual(inv.seller["gstin"], "03AAAAA0000A1ZC")
        self.assertEqual(inv.seller["legal_name"], "Test Traders")
        item = inv.items.first()
        self.assertTrue(item.hsn_code and item.unit and item.quantity)
        self.assertIn("Rupees", inv.amount_in_words)
        # Changing settings later doesn't alter the issued invoice.
        configure_store(legal_name="New Name Pvt Ltd")
        inv.refresh_from_db()
        self.assertEqual(inv.seller["legal_name"], "Test Traders")

    def test_issued_invoice_is_immutable(self):
        inv = issue_invoice(place_test_order(self.user, make_product(stock=5)))
        inv.buyer_name = "Someone else"
        with self.assertRaises(ValidationError):
            inv.save()
        with self.assertRaises(ValidationError):
            inv.delete()
        with self.assertRaises(ValidationError):
            inv.items.first().delete()

    def test_pdf_renders(self):
        order = place_test_order(self.user, make_product(price="105.00", stock=5), state_code="03")
        pdf = render_invoice_pdf(issue_invoice(order))
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 3000)
        order2 = place_test_order(self.user, make_product(name="Other", sku="OT", stock=5), state_code="27",
                                  token="pdf2")
        self.assertTrue(render_invoice_pdf(issue_invoice(order2)).startswith(b"%PDF"))


class CreditNoteTests(TestCase):
    def setUp(self):
        configure_store()
        self.user = make_user()
        self.order = place_test_order(self.user, make_product(price="105.00", stock=10), qty=4)
        self.invoice = issue_invoice(self.order)

    def test_partial_then_full_credit(self):
        line = self.invoice.items.get(is_shipping=False)
        note = issue_credit_note(self.invoice, quantities={line.line_no: 1}, reason="1 pack damaged")
        self.assertEqual(note.taxable_total, D("100.00"))
        self.assertEqual(note.cgst_total + note.sgst_total, D("5.00"))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_PARTLY_CREDITED)
        with self.assertRaises(InvoiceError):
            issue_credit_note(self.invoice, quantities={line.line_no: 4}, reason="too many")
        self.assertTrue(render_credit_note_pdf(note).startswith(b"%PDF"))

    def test_cancellation_after_invoice_issues_credit_note(self):
        cancel_order(self.order, reason="customer request")
        note = CreditNote.objects.get()
        self.assertEqual(note.grand_total, self.invoice.grand_total)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.STATUS_CREDITED)
        self.assertTrue(note.number.startswith("LLDCN/"))


class InvoiceAccessTests(TestCase):
    def setUp(self):
        configure_store()
        self.owner = make_user(email="owner@example.com")
        self.invoice = issue_invoice(place_test_order(self.owner, make_product(stock=5)))
        self.url = self.invoice.get_pdf_url()

    def test_owner_can_download(self):
        self.client.force_login(self.owner)
        resp = self.client.get(self.url)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn("no-store", resp["Cache-Control"])

    def test_other_customer_gets_404(self):
        self.client.force_login(make_user(email="nosy@example.com"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_anonymous_redirected(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_staff_with_permission_can_view(self):
        from django.contrib.auth.models import Permission

        staff = make_user(email="acc@example.com", is_staff=True)
        staff.user_permissions.add(Permission.objects.get(codename="view_invoice"))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url + "?view=1").status_code, 200)
