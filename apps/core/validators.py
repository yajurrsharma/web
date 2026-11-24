"""
Upload validation and safe storage paths.

Every uploaded file is checked for extension, size and real content (Pillow
decode for images, container signature for videos) and saved under a random
name so uploads can never overwrite files or be executed as code.
"""
import os
import struct
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
VIDEO_EXTENSIONS = {".mp4", ".webm"}
MAX_IMAGE_PIXELS = 40_000_000


@deconstructible
class safe_upload_path:
    """upload_to callable: <folder>/<yyyy>/<mm>/<uuid><ext> with a lower-case, whitelisted ext."""

    def __init__(self, folder):
        self.folder = folder

    def __call__(self, instance, filename):
        from django.utils import timezone

        ext = os.path.splitext(filename)[1].lower()
        if ext == ".jpeg":
            ext = ".jpg"
        if ext not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
            ext = ".bin"
        now = timezone.now()
        return f"{self.folder}/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{ext}"

    def __eq__(self, other):
        return isinstance(other, safe_upload_path) and other.folder == self.folder


def _already_stored(f):
    """A file saved earlier (FieldFile) was checked when uploaded. Re-checking it on every form save
    would open it from storage and leave the handle open (on Windows that blocks deleting the file)."""
    return getattr(f, "_committed", False) is True


def validate_image_upload(f):
    if _already_stored(f):
        return
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValidationError("Upload a JPG, PNG or WebP image.")
    if f.size > settings.MAX_IMAGE_UPLOAD_SIZE:
        raise ValidationError(
            f"Image is too large. Maximum size is {settings.MAX_IMAGE_UPLOAD_SIZE // (1024 * 1024)} MB.")
    from PIL import Image, UnidentifiedImageError

    try:
        pos = f.tell() if hasattr(f, "tell") else 0
        f.seek(0)
        with Image.open(f) as img:
            if img.format not in IMAGE_FORMATS:
                raise ValidationError("File content is not a JPG, PNG or WebP image.")
            if img.width * img.height > MAX_IMAGE_PIXELS:
                raise ValidationError("Image dimensions are too large.")
            img.verify()
        f.seek(pos)
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValidationError("This file is not a valid image.")


def _video_signature_ok(head: bytes, ext: str) -> bool:
    if ext == ".mp4":
        return len(head) >= 12 and head[4:8] == b"ftyp"
    if ext == ".webm":
        return head[:4] == b"\x1a\x45\xdf\xa3"
    return False


def validate_video_upload(f):
    if _already_stored(f):
        return
    ext = os.path.splitext(f.name)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise ValidationError("Upload an MP4 or WebM video.")
    if f.size > settings.MAX_VIDEO_UPLOAD_SIZE:
        raise ValidationError(
            f"Video is too large. Maximum size is {settings.MAX_VIDEO_UPLOAD_SIZE // (1024 * 1024)} MB. "
            "For longer videos, upload to YouTube and paste the link instead.")
    f.seek(0)
    head = f.read(16)
    f.seek(0)
    if not _video_signature_ok(head, ext):
        raise ValidationError("File content does not match an MP4/WebM video.")
    limit = settings.MAX_VIDEO_SECONDS
    if limit:
        try:
            seconds = video_duration_seconds(f, ext)
        finally:
            f.seek(0)
        if seconds is None:
            raise ValidationError("Could not read the length of this video. Save or export it again as MP4 "
                                  "from your phone or editing app, then upload.")
        if seconds > limit + 0.5:  # small allowance: phone clips "10 s" are often 10.2 s
            raise ValidationError(f"Video is {seconds:.0f} seconds long. Maximum is {limit} seconds. "
                                  "Trim it, or upload it to YouTube and paste the link instead.")


# ---------------------------------------------------------------- video length
# Reads only the container header (no video decoding, no external tools).

def video_duration_seconds(f, ext):
    """Length of an MP4/WebM in seconds, or None if it cannot be read."""
    try:
        f.seek(0)
        return _mp4_duration(f) if ext == ".mp4" else _webm_duration(f)
    except (OSError, ValueError, IndexError, struct.error):
        return None


def _mp4_boxes(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            return
        yield kind, pos + header, pos + size
        pos += size


def _mp4_duration(f):
    f.seek(0, os.SEEK_END)
    end = f.tell()
    for kind, body, box_end in _mp4_boxes(f, 0, end):
        if kind != b"moov":
            continue
        for sub, sbody, _ in _mp4_boxes(f, body, box_end):
            if sub != b"mvhd":
                continue
            f.seek(sbody)
            version = f.read(1)[0]
            if version == 1:
                f.seek(sbody + 4 + 16)
                timescale, duration = struct.unpack(">IQ", f.read(12))
            else:
                f.seek(sbody + 4 + 8)
                timescale, duration = struct.unpack(">II", f.read(8))
            return duration / timescale if timescale else None
    return None


def _ebml_vint(data, pos, keep_marker):
    first = data[pos]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8:
        raise ValueError("bad EBML number")
    value = first if keep_marker else first & (0xFF >> length)
    for b in data[pos + 1:pos + length]:
        value = (value << 8) | b
    unknown = not keep_marker and value == (1 << (7 * length)) - 1
    return value, pos + length, unknown


def _webm_duration(f):
    data = f.read(2 * 1024 * 1024)  # Segment Info sits near the start of the file
    pos, scale, duration = 0, 1_000_000, None
    containers = {0x18538067, 0x1549A966}  # Segment, Info: step inside
    while pos < len(data):
        eid, pos, _ = _ebml_vint(data, pos, keep_marker=True)
        size, pos, unknown = _ebml_vint(data, pos, keep_marker=False)
        if eid in containers:
            continue
        if eid == 0x2AD7B1:  # TimecodeScale (ns per tick)
            scale = int.from_bytes(data[pos:pos + size], "big")
        elif eid == 0x4489:  # Duration (ticks, float)
            duration = struct.unpack(">f" if size == 4 else ">d", data[pos:pos + size])[0]
        elif eid == 0x1F43B675:  # first Cluster: header is over
            break
        if unknown:
            break
        pos += size
    return duration * scale / 1e9 if duration is not None else None


# ---------------------------------------------------------------- image shrinking
MAX_IMAGE_EDGE = 1600  # px; enough for zoom on product pages


def shrink_image(fieldfile):
    """
    Replace a freshly uploaded image with a resized, compressed copy before it is stored.

    Phone photos (4–8 MB, 4000 px) become ~150–300 KB, which saves hosting space and
    makes pages load fast. Already-stored files and small images are left alone.
    """
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps

    if not fieldfile or getattr(fieldfile, "_committed", True):
        return
    upload = fieldfile.file
    upload.seek(0)
    with Image.open(upload) as img:
        img = ImageOps.exif_transpose(img)  # keep phone photos the right way up
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.LANCZOS)
        out = BytesIO()
        if has_alpha:
            img.convert("RGBA").save(out, "WEBP", quality=82, method=4)
            ext = ".webp"
        else:
            img.convert("RGB").save(out, "JPEG", quality=82, optimize=True, progressive=True)
            ext = ".jpg"
    if out.tell() >= getattr(upload, "size", out.tell() + 1) and \
            os.path.splitext(fieldfile.name)[1].lower() in (".jpg", ".jpeg", ext):
        upload.seek(0)
        return  # already small and in a good format: keep the original
    name = os.path.splitext(os.path.basename(fieldfile.name))[0] + ext
    fieldfile.save(name, ContentFile(out.getvalue()), save=False)
