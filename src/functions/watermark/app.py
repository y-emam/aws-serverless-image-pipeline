"""Workflow step: stamp a text watermark on the staged full-size image,
publish it under images/<id>/full.jpg, and delete the staging copy.
"""

import io
import logging
import os
from functools import cache

import boto3
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@cache
def _s3():
    return boto3.client("s3")


def apply_watermark(data, text):
    """Return JPEG bytes with semi-transparent text in the bottom-right corner."""
    with Image.open(io.BytesIO(data)) as img:
        base = img.convert("RGBA")

    # Font size scales with the image so the mark looks the same on any size.
    font_size = max(12, base.width // 40)
    font = ImageFont.load_default(size=font_size)

    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = right - left, bottom - top
    margin = max(8, font_size // 2)
    x = base.width - text_w - margin
    y = base.height - text_h - margin - top

    # Dark shadow then light text: readable on both bright and dark photos.
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 110))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 170))

    out = io.BytesIO()
    Image.alpha_composite(base, overlay).convert("RGB").save(
        out, "JPEG", quality=85, optimize=True, progressive=True
    )
    return out.getvalue()


def handler(event, context):
    image_id = event["image_id"]
    staging_bucket, staging_key = event["bucket"], event["key"]
    final_key = f"{os.environ['IMAGE_PREFIX']}{image_id}/full.jpg"

    try:
        data = _s3().get_object(Bucket=staging_bucket, Key=staging_key)["Body"].read()
    except _s3().exceptions.NoSuchKey:
        # Idempotency: a retry after a run that already published and deleted
        # the staging copy. If the final image exists, the work is done.
        head = _s3().head_object(Bucket=os.environ["DEST_BUCKET"], Key=final_key)
        logger.info("Staging copy gone, %s already published", final_key)
        return {
            "image_id": image_id,
            "variant": "full",
            "bucket": os.environ["DEST_BUCKET"],
            "key": final_key,
            "width": event.get("width"),
            "height": event.get("height"),
            "bytes": head["ContentLength"],
        }

    body = apply_watermark(data, os.environ.get("WATERMARK_TEXT", ""))
    _s3().put_object(
        Bucket=os.environ["DEST_BUCKET"],
        Key=final_key,
        Body=body,
        ContentType="image/jpeg",
        CacheControl="public, max-age=31536000, immutable",
    )
    _s3().delete_object(Bucket=staging_bucket, Key=staging_key)
    logger.info("Published %s (%s bytes)", final_key, len(body))

    return {
        "image_id": image_id,
        "variant": "full",
        "bucket": os.environ["DEST_BUCKET"],
        "key": final_key,
        "width": event.get("width"),
        "height": event.get("height"),
        "bytes": len(body),
    }
