"""GET /api/images -> newest images for the gallery.

A Scan is fine for a demo-sized table. At real scale you would add a GSI
(e.g. partition "status", sort "uploaded_at") and Query it instead, so each
request reads only the rows it returns.
"""

import json
import logging
import os
from decimal import Decimal
from functools import cache

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

MAX_ITEMS = 60
PUBLIC_FIELDS = (
    "image_id", "status", "uploaded_at", "processed_at", "width", "height", "format",
    "tags", "caption", "thumb_key", "full_key", "full_width", "full_height", "error_type",
)


@cache
def _table():
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def handler(event, context):
    items, kwargs = [], {}
    # Bounded scan: stop after a few pages so a large table cannot run up cost.
    for _ in range(5):
        page = _table().scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    items.sort(key=lambda i: i.get("uploaded_at", ""), reverse=True)
    images = [{k: _plain(i[k]) for k in PUBLIC_FIELDS if k in i} for i in items[:MAX_ITEMS]]

    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps({"images": images}),
    }
