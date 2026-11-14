"""POST /api/uploads -> pre-signed S3 POST for a single image upload.

Request body: {"filename": "cat.jpg", "content_type": "image/jpeg"}
Response:     {"image_id": "...", "key": "uploads/<id>.jpg", "url": "...", "fields": {...}}

A pre-signed POST (not PUT) is used because its policy can enforce the exact
key, the content type and a maximum size on the S3 side, before any byte of a
bad upload is stored.
"""

import json
import logging
import os
import uuid
from functools import cache

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


@cache
def _s3():
    # SigV4 + regional endpoint: required for pre-signed POSTs to new buckets.
    region = os.environ.get("AWS_REGION", "us-east-1")
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://s3.{region}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body),
    }


def handler(event, context):
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            import base64

            raw = base64.b64decode(raw).decode("utf-8")
        body = json.loads(raw)
    except (ValueError, TypeError):
        return _response(400, {"error": "Body must be JSON."})

    content_type = str(body.get("content_type", "")).lower().strip()
    if content_type not in ALLOWED_TYPES:
        return _response(400, {"error": f"content_type must be one of {sorted(ALLOWED_TYPES)}."})

    image_id = uuid.uuid4().hex
    key = f"{os.environ['UPLOAD_PREFIX']}{image_id}.{ALLOWED_TYPES[content_type]}"
    max_bytes = int(os.environ["MAX_BYTES"])

    post = _s3().generate_presigned_post(
        Bucket=os.environ["SOURCE_BUCKET"],
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],
        ],
        ExpiresIn=int(os.environ.get("URL_EXPIRES_IN", "300")),
    )

    logger.info("Issued upload URL image_id=%s type=%s", image_id, content_type)
    return _response(
        200,
        {
            "image_id": image_id,
            "key": key,
            "url": post["url"],
            "fields": post["fields"],
            "max_bytes": max_bytes,
        },
    )
