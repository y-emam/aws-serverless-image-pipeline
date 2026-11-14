"""Workflow step: write the COMPLETED record to DynamoDB and build the
success notification text.

Input: {"execution": <workflow state>, "cdn_domain": "dxxxx.cloudfront.net"}
The workflow state holds the validate output plus "outputs", the list of
results from the three parallel branches (thumbnail, full size, AI).
"""

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from functools import cache

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


@cache
def _table():
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def _pick_outputs(outputs):
    thumb = next(o for o in outputs if o.get("variant") == "thumb")
    full = next(o for o in outputs if o.get("variant") == "full")
    ai = next(o for o in outputs if "tags" in o)
    return thumb, full, ai


def build_item(state):
    thumb, full, ai = _pick_outputs(state["outputs"])
    item = {
        "image_id": state["image_id"],
        "status": "COMPLETED",
        "uploaded_at": state["uploaded_at"],
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "source_key": state["key"],
        "format": state["format"],
        "size_bytes": state["size_bytes"],
        "width": state["width"],
        "height": state["height"],
        "thumb_key": thumb["key"],
        "thumb_width": thumb["width"],
        "thumb_height": thumb["height"],
        "full_key": full["key"],
        "full_width": full["width"],
        "full_height": full["height"],
        "tags": ai["tags"],
        # DynamoDB numbers must be Decimal, not float.
        "labels": [{"name": l["name"], "confidence": Decimal(str(l["confidence"]))} for l in ai["labels"]],
        "caption_source": ai["caption_source"],
    }
    if ai.get("caption"):
        item["caption"] = ai["caption"]
    return item


def build_message(item, cdn_domain):
    lines = [
        f"Image {item['image_id']} processed.",
        f"Original: {item['width']}x{item['height']} {item['format']}",
        f"Tags: {', '.join(item['tags']) or '(none)'}",
        f"Caption: {item.get('caption') or '(none)'}",
        "",
        f"Thumbnail: https://{cdn_domain}/{item['thumb_key']}",
        f"Full size: https://{cdn_domain}/{item['full_key']}",
    ]
    return "\n".join(lines)


def handler(event, context):
    item = build_item(event["execution"])
    # put_item overwrites: a duplicate S3 event re-processes the same image id
    # and simply rewrites the same record (idempotent).
    _table().put_item(Item=item)
    logger.info("Wrote COMPLETED record %s", item["image_id"])
    return {
        "image_id": item["image_id"],
        "thumb_key": item["thumb_key"],
        "full_key": item["full_key"],
        "message": build_message(item, event["cdn_domain"]),
    }
