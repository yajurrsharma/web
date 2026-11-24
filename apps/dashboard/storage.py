"""How much hosting space the shop's files use (photos, videos, banners, invoices)."""
import os

from django.conf import settings
from django.core.cache import cache

from apps.core.validators import VIDEO_EXTENSIONS

CACHE_KEY = "storage_usage_v1"
GROUPS = [("product_photos", "Product photos"), ("videos", "Uploaded videos"), ("banners", "Banners & logo"),
          ("invoices", "Invoice PDFs"), ("other", "Other files")]


def _group(rel_path):
    ext = os.path.splitext(rel_path)[1].lower()
    top = rel_path.replace("\\", "/").split("/")[0]
    if ext in VIDEO_EXTENSIONS:
        return "videos"
    if top in ("products", "categories"):
        return "product_photos"
    if top in ("banners", "branding"):
        return "banners"
    return "other"


def _walk(root, fixed_group=None):
    sizes = {}
    if not root or not os.path.isdir(root):
        return sizes
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(folder, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            key = fixed_group or _group(os.path.relpath(path, root))
            sizes[key] = sizes.get(key, 0) + size
    return sizes


def usage(refresh=False):
    data = None if refresh else cache.get(CACHE_KEY)
    if data is None:
        sizes = _walk(str(settings.MEDIA_ROOT))
        for k, v in _walk(str(settings.PRIVATE_MEDIA_ROOT), fixed_group="invoices").items():
            sizes[k] = sizes.get(k, 0) + v
        total = sum(sizes.values())
        quota = settings.STORAGE_QUOTA_MB * 1024 * 1024
        data = {
            "rows": [{"label": label, "mb": round(sizes.get(key, 0) / 1048576, 1)} for key, label in GROUPS
                     if sizes.get(key)],
            "total_mb": round(total / 1048576, 1),
            "quota_mb": settings.STORAGE_QUOTA_MB,
            "percent": round(total * 100 / quota) if quota else None,
        }
        cache.set(CACHE_KEY, data, 30 * 60)
    return data
