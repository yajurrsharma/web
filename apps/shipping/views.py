import json
import logging
from hmac import compare_digest

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import handle_webhook

logger = logging.getLogger(__name__)


# Shiprocket posts tracking updates here. CSRF-exempt because it is a server-to-server call; it is
# protected by the secret token Shiprocket sends in the x-api-key header (set the same token in both places).
@csrf_exempt
@require_POST
def tracking_webhook(request):
    expected = settings.SHIPROCKET_WEBHOOK_TOKEN
    given = request.headers.get("x-api-key", "")
    if not expected or not compare_digest(given.encode(), expected.encode()):
        logger.warning("Courier webhook rejected: wrong or missing token")
        return HttpResponse(status=401)
    try:
        payload = json.loads(request.body.decode() or "{}")
    except (ValueError, UnicodeDecodeError):
        return HttpResponse(status=400)
    if not isinstance(payload, dict):
        return HttpResponse(status=400)
    matched = handle_webhook(payload)
    # Always 200 for a valid call, even for an AWB we don't know, so Shiprocket doesn't retry forever.
    return JsonResponse({"ok": True, "matched": matched})
