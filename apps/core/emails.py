"""
Transactional email service.

- Renders `templates/emails/<name>.txt` + `.html` and `<name>_subject.txt`.
- Sends synchronously (PythonAnywhere has no background workers on web apps);
  failures are logged and retried by `python manage.py retry_emails`, which you
  schedule as a PythonAnywhere scheduled task.
- Sensitive emails (OTP, password reset) are never stored or retried; the user
  simply requests a new code.
"""
import logging
import smtplib
import socket

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import EmailLog, SiteSettings

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
TEMPORARY_ERRORS = (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                    smtplib.SMTPHeloError, smtplib.SMTPDataError, socket.timeout,
                    ConnectionError, TimeoutError, OSError)


def _render(template, context):
    ctx = {"store": SiteSettings.load(), "SITE_URL": settings.SITE_URL, **context}
    subject = render_to_string(f"emails/{template}_subject.txt", ctx).strip().replace("\n", " ")
    text = render_to_string(f"emails/{template}.txt", ctx)
    html = render_to_string(f"emails/{template}.html", ctx)
    return subject[:200], text, html


def _resolve_attachments(ref):
    """Attachments are rebuilt from a reference so files never live in the log."""
    if not ref:
        return []
    kind, _, pk = ref.partition(":")
    if kind == "invoice":
        from apps.invoicing.models import Invoice
        from apps.invoicing.pdf import render_invoice_pdf

        invoice = Invoice.objects.get(pk=pk)
        return [(f"{invoice.number.replace('/', '-')}.pdf", render_invoice_pdf(invoice), "application/pdf")]
    if kind == "creditnote":
        from apps.invoicing.models import CreditNote
        from apps.invoicing.pdf import render_credit_note_pdf

        note = CreditNote.objects.get(pk=pk)
        return [(f"{note.number.replace('/', '-')}.pdf", render_credit_note_pdf(note), "application/pdf")]
    return []


def _deliver(log, subject, text, html, attachments_ref):
    """Send using the given content (not the log's), so sensitive bodies never touch the database."""
    log.attempts += 1
    try:
        msg = EmailMultiAlternatives(subject=subject, body=text, from_email=settings.DEFAULT_FROM_EMAIL,
                                     to=[log.to_email])
        msg.attach_alternative(html, "text/html")
        for name, content, mime in _resolve_attachments(attachments_ref):
            msg.attach(name, content, mime)
        msg.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 — logged, never raised to the customer
        log.status = EmailLog.STATUS_FAILED
        # Only the exception type and a short message; never credentials or bodies.
        log.last_error = f"{type(exc).__name__}: {str(exc)[:250]}"
        logger.warning("Email %s to %s failed (attempt %s): %s",
                       log.template, log.to_email, log.attempts, type(exc).__name__)
    else:
        log.status = EmailLog.STATUS_SENT
        log.sent_at = timezone.now()
        log.last_error = ""
    log.save()
    return log


def send_email(template, to_email, context=None, *, sensitive=False, attachment_ref="", related=""):
    """Render and send one email. Returns the EmailLog (check `.status`)."""
    subject, text, html = _render(template, context or {})
    log = EmailLog.objects.create(
        # Sensitive emails (OTP in the subject, reset links) keep no content at all in the log.
        to_email=to_email, template=template, subject="(sensitive — not stored)" if sensitive else subject,
        body_text="" if sensitive else text, body_html="" if sensitive else html,
        is_sensitive=sensitive, attachment_ref=attachment_ref, related_object=related,
    )
    return _deliver(log, subject, text, html, attachment_ref)


def retry_failed(limit=50):
    """Retry non-sensitive failed emails. Returns (retried, now_sent)."""
    qs = (EmailLog.objects.filter(status=EmailLog.STATUS_FAILED, is_sensitive=False,
                                  attempts__lt=MAX_ATTEMPTS).order_by("created_at")[:limit])
    retried = sent = 0
    for log in qs:
        retried += 1
        _deliver(log, log.subject, log.body_text, log.body_html, log.attachment_ref)
        sent += log.status == EmailLog.STATUS_SENT
    return retried, sent


def notify_admins(template, context=None, related=""):
    for address in settings.ADMIN_ALERT_EMAILS:
        send_email(template, address, context or {}, related=related)
