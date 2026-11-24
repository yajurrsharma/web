"""Staff pages for automatic offers and customer review approval."""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.cart.models import Promotion
from apps.core.utils import audit
from apps.products.models import Review

from .forms import PromotionForm
from .views import staff_required


# ---------------------------------------------------------------- automatic offers
@staff_required("cart.view_promotion")
@require_http_methods(["GET", "POST"])
def offer_list(request, pk=None):
    obj = get_object_or_404(Promotion, pk=pk) if pk else None
    can_save = request.user.has_perm("cart.change_promotion" if obj else "cart.add_promotion")
    form = PromotionForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if not can_save:
            messages.error(request, "You do not have permission to change offers.")
            return redirect("dashboard:offers")
        if form.is_valid():
            offer = form.save()
            audit("offer.save", f"Offer '{offer.name}' saved ({'on' if offer.is_active else 'off'})",
                  actor=request.user, obj=offer, request=request)
            messages.success(request, f"Offer '{offer.name}' saved.")
            return redirect("dashboard:offers")
    now = timezone.now()
    offers = Promotion.objects.prefetch_related("products").select_related("gift_variant__product")
    live_ids = set(Promotion.objects.live(now).values_list("pk", flat=True))
    return render(request, "dashboard/offers.html", {"form": form, "obj": obj, "offers": offers,
                                                     "live_ids": live_ids, "can_save": can_save})


@staff_required("cart.change_promotion")
@require_POST
def offer_toggle(request, pk):
    offer = get_object_or_404(Promotion, pk=pk)
    offer.is_active = not offer.is_active
    offer.save(update_fields=["is_active"])
    audit("offer.toggle", f"Offer '{offer.name}' switched {'on' if offer.is_active else 'off'}",
          actor=request.user, obj=offer, request=request)
    messages.success(request, f"'{offer.name}' is now {'ON' if offer.is_active else 'OFF'}.")
    return redirect("dashboard:offers")


# ---------------------------------------------------------------- reviews
@staff_required("products.view_review")
@require_http_methods(["GET"])
def review_list(request):
    status = request.GET.get("status", Review.PENDING)
    if status not in dict(Review.STATUS_CHOICES):
        status = Review.PENDING
    counts = {s: Review.objects.filter(status=s).count() for s, _ in Review.STATUS_CHOICES}
    reviews = Review.objects.filter(status=status).select_related("product", "user", "order_item__order")[:200]
    return render(request, "dashboard/reviews.html", {"reviews": reviews, "status": status, "counts": counts,
                                                      "statuses": Review.STATUS_CHOICES})


@staff_required("products.change_review")
@require_POST
def review_action(request, pk):
    review = get_object_or_404(Review, pk=pk)
    action = request.POST.get("action")
    if action not in ("approve", "reject"):
        messages.error(request, "Unknown action.")
        return redirect("dashboard:reviews")
    review.status = Review.APPROVED if action == "approve" else Review.REJECTED
    review.moderated_at, review.moderated_by = timezone.now(), request.user
    review.save(update_fields=["status", "moderated_at", "moderated_by"])
    review.product.refresh_rating()
    audit("review." + action, f"{review.rating}★ review of '{review.product.name}' {action}d",
          actor=request.user, obj=review, request=request)
    messages.success(request, "Review published." if action == "approve" else "Review hidden.")
    back = request.POST.get("back")
    back = back if back in dict(Review.STATUS_CHOICES) else Review.PENDING
    return redirect(reverse("dashboard:reviews") + f"?status={back}")
