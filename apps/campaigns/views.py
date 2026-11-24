from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .services import unsubscribe as do_unsubscribe
from .services import user_from_token


# CSRF-exempt on purpose: Gmail/Yahoo "one-click unsubscribe" POSTs here directly (RFC 8058). The signed
# token is the protection — it identifies one customer and can only switch offer emails OFF.
@csrf_exempt
@require_http_methods(["GET", "POST"])
def unsubscribe(request, token):
    user = user_from_token(token)
    if user is None:
        raise Http404
    done = False
    if request.method == "POST":
        do_unsubscribe(user)
        done = True
    return render(request, "campaigns/unsubscribe.html", {"done": done or not user.marketing_opt_in,
                                                           "email": user.email})
