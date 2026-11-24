"""Barcodes and QR codes as inline SVG, drawn by ReportLab (already a dependency — no extra library)."""
from urllib.parse import quote

from django.utils.safestring import mark_safe


def _svg(drawing):
    from reportlab.graphics import renderSVG

    svg = renderSVG.drawToString(drawing)
    return mark_safe(svg[svg.index("<svg"):])  # drop the XML prolog/doctype for inline use


def barcode_svg(value, height=56):
    """Code 128 barcode (shipping labels)."""
    from reportlab.graphics.barcode import createBarcodeDrawing

    return _svg(createBarcodeDrawing("Code128", value=value, barHeight=height, barWidth=1.3, humanReadable=False))


def qr_svg(text, size=220):
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    widget = QrCodeWidget(text, barLevel="M")
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0])
    drawing.add(widget)
    return _svg(drawing)


def upi_link(upi_id, payee_name, amount, note):
    """Standard UPI deep link: opens GPay / PhonePe / Paytm / BHIM with the amount filled in."""
    return (f"upi://pay?pa={quote(upi_id, safe='@.')}&pn={quote(payee_name)}&am={amount:.2f}&cu=INR"
            f"&tn={quote(note)}")
