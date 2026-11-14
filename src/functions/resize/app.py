"""Workflow step: resize the original into one variant.

variant "thumb": writes images/<id>/thumb.jpg (final, served by CloudFront)
variant "full":  writes staging/<id>/full.jpg (the watermark step publishes it)

The full-size image goes to a staging prefix that CloudFront does not serve,
so an un-watermarked copy is never publicly reachable.
"""

import io
import logging
import os
from functools import cache

import boto3
from PIL import Image, ImageOps

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

JPEG_QUALITY = {"thumb": 80, "full": 85}


@cache
def _s3():
    return boto3.client("s3")


def to_rgb(img):
    """JPEG has no alpha channel: flatten transparency onto white."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")


def resize_bytes(data, max_px, quality):
    """Return (jpeg_bytes, width, height). Never upscales."""
    with Image.open(io.BytesIO(data)) as img:
        # JPEG only: let the decoder downscale by 1/2, 1/4 or 1/8 while decoding.
        # A 40 MP photo then never has to be fully decoded into memory.
        img.draft("RGB", (max_px * 2, max_px * 2))
        # Phones store rotation in EXIF; apply it, because EXIF is dropped below.
        img = ImageOps.exif_transpose(img)
        img = to_rgb(img)
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        # No exif= argument: metadata such as GPS location is stripped.
        img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        return out.getvalue(), img.width, img.height


def handler(event, context):
    variant = event["variant"]
    if variant not in JPEG_QUALITY:
        raise ValueError(f"Unknown variant {variant}")

    image_id = event["image_id"]
    max_px = int(os.environ["THUMBNAIL_PX"] if variant == "thumb" else os.environ["FULL_PX"])
    data = _s3().get_object(Bucket=event["bucket"], Key=event["key"])["Body"].read()

    body, width, height = resize_bytes(data, max_px, JPEG_QUALITY[variant])

    if variant == "thumb":
        key = f"{os.environ['IMAGE_PREFIX']}{image_id}/thumb.jpg"
    else:
        key = f"{os.environ['STAGING_PREFIX']}{image_id}/full.jpg"

    _s3().put_object(
        Bucket=os.environ["DEST_BUCKET"],
        Key=key,
        Body=body,
        ContentType="image/jpeg",
        # Keys contain the unique image id and never change, so they can be
        # cached for a year by browsers and CloudFront.
        CacheControl="public, max-age=31536000, immutable",
    )
    logger.info("Wrote %s %sx%s (%s bytes)", key, width, height, len(body))

    return {
        "image_id": image_id,
        "variant": variant,
        "bucket": os.environ["DEST_BUCKET"],
        "key": key,
        "width": width,
        "height": height,
        "bytes": len(body),
    }
