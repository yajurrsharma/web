"""
Small copies of uploaded photos for product cards, menus and thumbnails.

A copy is made the first time a size is needed and saved next to the uploads under `thumbs/<size>/…`,
so later pages just link to it. Originals are never changed. If anything fails, the original is used.
"""
import os
from io import BytesIO

from django.core.files.base import ContentFile

ALLOWED_SIZES = (120, 160, 240, 480, 800)


def thumb_url(fieldfile, size=480):
    if not fieldfile:
        return ""
    size = min(ALLOWED_SIZES, key=lambda s: abs(s - int(size)))  # only a few sizes → bounded disk use
    storage = fieldfile.storage
    base, _ = os.path.splitext(fieldfile.name)
    name = f"thumbs/{size}/{base}.jpg"
    if not storage.exists(name):
        from PIL import Image, ImageOps

        with storage.open(fieldfile.name, "rb") as src, Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                img = Image.new("RGB", rgba.size, (255, 255, 255))  # product photos sit on white
                img.paste(rgba, mask=rgba.split()[-1])
            else:
                img = img.convert("RGB")
            img.thumbnail((size, size), Image.LANCZOS)
            out = BytesIO()
            img.save(out, "JPEG", quality=80, optimize=True, progressive=True)
        if not storage.exists(name):  # another request may have made it meanwhile
            storage.save(name, ContentFile(out.getvalue()))
    return storage.url(name)
