"""Workflow step 1: parse the S3 event and validate the upload.

Normal input is {"bucket","key","uploaded_at"} from the start_workflow consumer.
It also accepts a list of SQS records, a single SQS record or a raw S3 event
(handy for manual runs from the console) and returns one flat image description.

A bad image is NOT an exception: it returns {"valid": false, "error": {...}}
so the workflow can record the reason in DynamoDB. Exceptions are reserved for
unexpected problems (permissions, outages) that should trigger retries.
"""

import io
import json
import logging
import os
import re
import struct
import warnings
from datetime import datetime, timezone
from functools import cache
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# MPO is a JPEG with extra frames (multi-picture format). Samsung phones and
# iPhone portrait/HDR photos are often saved this way; the first frame is a normal JPEG.
ALLOWED_FORMATS = {"JPEG": "image/jpeg", "MPO": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

# EXIF orientations that rotate the image by 90 or 270 degrees (width and height swap).
SWAPPED_ORIENTATIONS = {5, 6, 7, 8}


class BadEventError(Exception):
    """The workflow input does not contain an S3 object reference."""


@cache
def _s3():
    return boto3.client("s3")


def _first_record(event):
    """Return (bucket, key, event_time) from any supported event shape."""
    if isinstance(event, list):
        if not event:
            raise BadEventError("Empty batch")
        return _first_record(event[0])

    if not isinstance(event, dict):
        raise BadEventError(f"Unsupported event type {type(event).__name__}")

    if "bucket" in event and "key" in event:
        return event["bucket"], event["key"], event.get("uploaded_at")

    if "body" in event:  # SQS message
        body = event["body"]
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError as exc:
                raise BadEventError("SQS body is not JSON") from exc
        return _first_record(body)

    records = event.get("Records")
    if records:
        rec = records[0]
        s3 = rec.get("s3", {})
        try:
            bucket = s3["bucket"]["name"]
            # Keys in S3 events are URL-encoded ("my photo.jpg" -> "my+photo.jpg").
            key = unquote_plus(s3["object"]["key"])
        except KeyError as exc:
            raise BadEventError("Record has no s3.bucket.name / s3.object.key") from exc
        return bucket, key, rec.get("eventTime")

    raise BadEventError("No S3 record found in event")


def image_id_from_key(key):
    stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    clean = re.sub(r"[^A-Za-z0-9_-]", "-", stem)[:64]
    return clean or "unnamed"


def handler(event, context):
    bucket, key, event_time = _first_record(event)
    uploaded_at = event_time or datetime.now(timezone.utc).isoformat()
    result = {
        "image_id": image_id_from_key(key),
        "bucket": bucket,
        "key": key,
        "uploaded_at": uploaded_at,
    }
    max_bytes = int(os.environ.get("MAX_BYTES", str(10 * 1024 * 1024)))
    max_pixels = int(os.environ.get("MAX_PIXELS", "40000000"))
    prefix = os.environ.get("UPLOAD_PREFIX", "uploads/")

    def invalid(reason):
        logger.warning("Invalid upload %s: %s", key, reason)
        return {**result, "valid": False, "error": {"Error": "InvalidImage", "Cause": reason}}

    if not key.startswith(prefix):
        return invalid(f"Key is outside {prefix}")

    try:
        head = _s3().head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return invalid("Object no longer exists")
        raise

    size = head["ContentLength"]
    result["size_bytes"] = size
    if size == 0:
        return invalid("File is empty")
    if size > max_bytes:
        return invalid(f"File is {size} bytes; limit is {max_bytes}")

    data = _s3().get_object(Bucket=bucket, Key=key)["Body"].read()

    # Pillow refuses images above MAX_IMAGE_PIXELS (decompression bombs);
    # turn its warning band into an error too.
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                img.verify()  # checks the file structure without decoding all pixels
            with Image.open(io.BytesIO(data)) as img:
                fmt, (width, height) = img.format, img.size
                if fmt not in ALLOWED_FORMATS:
                    return invalid(f"Format {fmt} is not allowed")  # before decoding anything
                rotated = img.getexif().get(0x0112) in SWAPPED_ORIENTATIONS
                # verify() does not catch a truncated file. Decoding it (at reduced
                # scale for JPEG, so it stays cheap) does, so a cut-off upload is
                # rejected here instead of crashing the resize step.
                img.draft("RGB", (1024, 1024))
                img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        return invalid(f"Not a readable image: {exc.__class__.__name__}")
    except (OSError, SyntaxError, ValueError, struct.error) as exc:
        return invalid(f"Corrupt image: {exc}")

    if fmt not in ALLOWED_FORMATS:
        return invalid(f"Format {fmt} is not allowed")
    if fmt == "MPO":
        fmt = "JPEG"  # report it as what it is to users: a JPEG photo

    if rotated:
        width, height = height, width  # report the size as displayed, not as stored

    result.update(
        valid=True,
        format=fmt,
        content_type=ALLOWED_FORMATS[fmt],
        width=width,
        height=height,
    )
    logger.info("Valid image %s %sx%s %s", result["image_id"], width, height, fmt)
    return result
