"""A4 PDF rendering for tax invoices and credit notes (ReportLab, bundled DejaVu font for ₹)."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT_DIR = Path(__file__).resolve().parent / "fonts"
BURGUNDY = colors.HexColor("#6E1423")
GOLD = colors.HexColor("#B8892B")
LIGHT = colors.HexColor("#F7F1E5")
_registered = False


def _fonts():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(TTFont("DejaVu", str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")
        _registered = True


def _styles():
    _fonts()
    return {
        "body": ParagraphStyle("body", fontName="DejaVu", fontSize=8.5, leading=11),
        "small": ParagraphStyle("small", fontName="DejaVu", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444")),
        "bold": ParagraphStyle("bold", fontName="DejaVu-Bold", fontSize=8.5, leading=11),
        "title": ParagraphStyle("title", fontName="DejaVu-Bold", fontSize=15, leading=18, textColor=BURGUNDY),
        "seller": ParagraphStyle("seller", fontName="DejaVu-Bold", fontSize=12, leading=15),
        "right": ParagraphStyle("right", fontName="DejaVu", fontSize=8.5, leading=11, alignment=TA_RIGHT),
        "rbold": ParagraphStyle("rbold", fontName="DejaVu-Bold", fontSize=9.5, leading=12, alignment=TA_RIGHT),
        "cell": ParagraphStyle("cell", fontName="DejaVu", fontSize=7.5, leading=9.5),
        "cellr": ParagraphStyle("cellr", fontName="DejaVu", fontSize=7.5, leading=9.5, alignment=TA_RIGHT),
        "head": ParagraphStyle("head", fontName="DejaVu-Bold", fontSize=7.5, leading=9.5, textColor=colors.white),
        "headr": ParagraphStyle("headr", fontName="DejaVu-Bold", fontSize=7.5, leading=9.5, textColor=colors.white,
                                alignment=TA_RIGHT),
    }


def _r(value):
    value = Decimal(str(value))
    return f"₹{value:,.2f}"


def _esc(text):
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _addr(a):
    lines = [a.get("full_name"), a.get("line1"), a.get("line2"),
             f"{a.get('city', '')} – {a.get('pincode', '')}", f"{a.get('state', '')} (State code {a.get('state_code', '')})",
             f"Phone: {a.get('phone')}" if a.get("phone") else ""]
    return "<br/>".join(_esc(x) for x in lines if x)


def _header(st, seller, title, meta_rows):
    name = seller.get("trade_name") or seller.get("legal_name")
    left = [Paragraph(_esc(name), st["seller"])]
    if seller.get("trade_name") and seller.get("legal_name") and seller["trade_name"] != seller["legal_name"]:
        left.append(Paragraph(_esc(seller["legal_name"]), st["body"]))
    left += [Paragraph(_esc(seller.get("address")), st["body"]),
             Paragraph(f"<b>GSTIN:</b> {_esc(seller.get('gstin'))} &nbsp; <b>State:</b> {_esc(seller.get('state'))} "
                       f"({_esc(seller.get('state_code'))})", st["body"])]
    contact = " · ".join(x for x in [seller.get("email"), seller.get("phone")] if x)
    if contact:
        left.append(Paragraph(_esc(contact), st["small"]))
    right = [Paragraph(title, st["title"])] + [Paragraph(f"<b>{k}:</b> {_esc(v)}", st["right"]) for k, v in meta_rows]
    t = Table([[left, right]], colWidths=[110 * mm, 70 * mm])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, GOLD),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def _footer_canvas(text):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("DejaVu", 7)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(15 * mm, 10 * mm, text)
        canvas.drawRightString(195 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def render_invoice_pdf(invoice) -> bytes:
    st = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=f"Invoice {invoice.number}", author=invoice.seller.get("legal_name", ""))
    story = [_header(st, invoice.seller, _esc(invoice.document_title), [
        ("Invoice No.", invoice.number), ("Invoice date", invoice.invoice_date.strftime("%d %b %Y")),
        ("Order No.", invoice.order_number), ("Order date", invoice.order_date.strftime("%d %b %Y")),
        ("Payment", invoice.payment_method),
        ("Reverse charge", "Yes" if invoice.reverse_charge else "No"),
    ]), Spacer(1, 5 * mm)]

    bill = f"<b>Bill to</b><br/>{_addr(invoice.billing_address)}"
    if invoice.buyer_gstin:
        bill += f"<br/><b>GSTIN:</b> {_esc(invoice.buyer_gstin)}"
    ship = f"<b>Ship to</b><br/>{_addr(invoice.shipping_address)}"
    pos = (f"<b>Place of supply</b><br/>{_esc(invoice.place_of_supply_name)} ({_esc(invoice.place_of_supply)})<br/>"
           f"{'Intra-state: CGST + SGST' if invoice.is_intra_state else 'Inter-state: IGST'}")
    parties = Table([[Paragraph(bill, st["body"]), Paragraph(ship, st["body"]), Paragraph(pos, st["body"])]],
                    colWidths=[65 * mm, 65 * mm, 50 * mm])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                                 ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                                 ("BACKGROUND", (0, 0), (-1, -1), LIGHT)]))
    story += [parties, Spacer(1, 5 * mm)]

    intra = invoice.is_intra_state
    rate_label = "Rate incl. GST" if invoice.order.prices_include_gst else "Rate"
    head = ["#", "Description", "HSN", "Qty", rate_label, "Disc.", "Taxable"]
    head += ["CGST", "SGST"] if intra else ["IGST"]
    head += ["Total"]
    rows = [[Paragraph(h, st["head"] if i in (0, 1, 2) else st["headr"]) for i, h in enumerate(head)]]
    for item in invoice.items.all():
        qty = f"{item.quantity.normalize():f} {item.unit}"
        row = [Paragraph(str(item.line_no), st["cell"]),
               Paragraph(_esc(item.description) + (f"<br/><font size=6.5 color='#666666'>SKU {_esc(item.sku)}</font>"
                                                   if item.sku else ""), st["cell"]),
               Paragraph(_esc(item.hsn_code), st["cell"]), Paragraph(qty, st["cellr"]),
               Paragraph(_r(item.unit_price), st["cellr"]), Paragraph(_r(item.discount), st["cellr"]),
               Paragraph(_r(item.taxable_value), st["cellr"])]
        if intra:
            row += [Paragraph(f"{item.half_rate.normalize():f}%<br/>{_r(item.cgst)}", st["cellr"]),
                    Paragraph(f"{item.half_rate.normalize():f}%<br/>{_r(item.sgst)}", st["cellr"])]
        else:
            row += [Paragraph(f"{item.gst_rate.normalize():f}%<br/>{_r(item.igst)}", st["cellr"])]
        row += [Paragraph(_r(item.total), st["cellr"])]
        rows.append(row)
    widths = ([7, 52, 15, 15, 18, 15, 19, 17, 17, 18] if intra else [7, 58, 15, 15, 19, 16, 20, 19, 24])
    table = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BURGUNDY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, GOLD), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [table, Spacer(1, 4 * mm)]

    totals = [("Taxable value", invoice.taxable_total)]
    if intra:
        totals += [("CGST", invoice.cgst_total), ("SGST", invoice.sgst_total)]
    else:
        totals += [("IGST", invoice.igst_total)]
    if invoice.round_off:
        totals += [("Round off", invoice.round_off)]
    trows = [[Paragraph(k, st["right"]), Paragraph(_r(v), st["right"])] for k, v in totals]
    trows.append([Paragraph("<b>Invoice total</b>", st["rbold"]), Paragraph(f"<b>{_r(invoice.grand_total)}</b>", st["rbold"])])
    tt = Table(trows, colWidths=[40 * mm, 35 * mm])
    tt.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 1, BURGUNDY)]))
    words = Paragraph(f"<b>Amount in words:</b><br/>{_esc(invoice.amount_in_words)}", st["body"])
    if invoice.discount_total:
        words = Paragraph(f"<b>Amount in words:</b><br/>{_esc(invoice.amount_in_words)}<br/><br/>"
                          f"Includes discounts (offers / coupon / prepaid) of {_r(invoice.discount_total)}, shown per line.", st["body"])
    summary = Table([[words, tt]], colWidths=[105 * mm, 75 * mm])
    summary.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [summary, Spacer(1, 6 * mm)]

    if invoice.terms:
        story += [Paragraph("<b>Terms</b>", st["body"]), Paragraph(_esc(invoice.terms).replace("\n", "<br/>"), st["small"]),
                  Spacer(1, 4 * mm)]
    sig = Table([["", Paragraph(f"For {_esc(invoice.seller.get('legal_name'))}<br/><br/><br/>Authorised signatory",
                                st["right"])]], colWidths=[110 * mm, 70 * mm])
    story += [sig, Spacer(1, 3 * mm),
              Paragraph("This is a computer-generated invoice.", st["small"])]
    footer = _footer_canvas(f"{invoice.number} · {invoice.seller.get('legal_name', '')}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def render_credit_note_pdf(note) -> bytes:
    st = _styles()
    inv = note.invoice
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=f"Credit note {note.number}")
    story = [_header(st, inv.seller, "Credit Note", [
        ("Credit note No.", note.number), ("Date", note.note_date.strftime("%d %b %Y")),
        ("Against invoice", inv.number), ("Invoice date", inv.invoice_date.strftime("%d %b %Y")),
        ("Order No.", inv.order_number),
    ]), Spacer(1, 5 * mm)]
    bill = f"<b>Customer</b><br/>{_addr(inv.billing_address)}"
    if inv.buyer_gstin:
        bill += f"<br/><b>GSTIN:</b> {_esc(inv.buyer_gstin)}"
    story += [Paragraph(bill, st["body"]), Spacer(1, 3 * mm),
              Paragraph(f"<b>Reason:</b> {_esc(note.reason)}", st["body"]), Spacer(1, 4 * mm)]
    intra = inv.is_intra_state
    head = ["#", "Description", "HSN", "Qty", "Taxable"] + (["CGST", "SGST"] if intra else ["IGST"]) + ["Total"]
    rows = [[Paragraph(h, st["head"] if i < 3 else st["headr"]) for i, h in enumerate(head)]]
    for line in note.lines:
        row = [Paragraph(str(line["line_no"]), st["cell"]), Paragraph(_esc(line["description"]), st["cell"]),
               Paragraph(_esc(line["hsn_code"]), st["cell"]),
               Paragraph(f"{Decimal(line['quantity']).normalize():f} {line['unit']}", st["cellr"]),
               Paragraph(_r(line["taxable_value"]), st["cellr"])]
        row += ([Paragraph(_r(line["cgst"]), st["cellr"]), Paragraph(_r(line["sgst"]), st["cellr"])] if intra
                else [Paragraph(_r(line["igst"]), st["cellr"])])
        row += [Paragraph(_r(line["total"]), st["cellr"])]
        rows.append(row)
    widths = [8, 62, 16, 18, 22, 18, 18, 18] if intra else [8, 74, 16, 20, 24, 18, 20]
    t = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BURGUNDY), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT])]))
    story += [t, Spacer(1, 4 * mm)]
    totals = [("Taxable value", note.taxable_total)] + (
        [("CGST", note.cgst_total), ("SGST", note.sgst_total)] if intra else [("IGST", note.igst_total)])
    trows = [[Paragraph(k, st["right"]), Paragraph(_r(v), st["right"])] for k, v in totals]
    trows.append([Paragraph("<b>Total credit</b>", st["rbold"]), Paragraph(f"<b>{_r(note.grand_total)}</b>", st["rbold"])])
    tt = Table(trows, colWidths=[40 * mm, 35 * mm])
    summary = Table([[Paragraph(f"<b>Amount in words:</b><br/>{_esc(note.amount_in_words)}", st["body"]), tt]],
                    colWidths=[105 * mm, 75 * mm])
    summary.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [summary, Spacer(1, 8 * mm),
              Paragraph(f"For {_esc(inv.seller.get('legal_name'))} — Authorised signatory", st["right"])]
    footer = _footer_canvas(f"{note.number} · against {inv.number}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
