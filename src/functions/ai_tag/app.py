"""Workflow step: AI tags (Rekognition) and an optional caption (Bedrock).

Rekognition is required: if it fails, the error propagates and Step Functions
retries / marks the image FAILED.

Bedrock is optional: it can be switched off with ENABLE_BEDROCK=false, and any
Bedrock error (model access not granted, throttling, region without the model)
is logged and the image is still processed, just without a caption.
"""

import io
import logging
import os
from functools import cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageOps

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

AI_MAX_PX = 1024  # plenty for labels/captions; keeps requests small and cheap

CAPTION_SYSTEM = (
    "You write alt text for images on a website. Reply with one plain sentence of at "
    "most 25 words that describes what the image shows. No preamble, no quotes."
)


@cache
def _s3():
    return boto3.client("s3")


@cache
def _rekognition():
    return boto3.client("rekognition")


@cache
def _bedrock():
    return boto3.client("bedrock-runtime")


def to_rgb(img):
    """Same flattening as the resize step (transparency onto white), so the AI
    services see the same pixels as the users do."""
    if img.mode.startswith("I"):
        img = img.point(lambda v: v / 256).convert("L")
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")


def downscale_jpeg(data):
    with Image.open(io.BytesIO(data)) as img:
        img.draft("RGB", (AI_MAX_PX * 2, AI_MAX_PX * 2))  # cheap JPEG downscale while decoding
        img = to_rgb(ImageOps.exif_transpose(img))
        img.thumbnail((AI_MAX_PX, AI_MAX_PX))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85)
        return out.getvalue()


def detect_labels(jpeg):
    resp = _rekognition().detect_labels(
        Image={"Bytes": jpeg},
        MaxLabels=int(os.environ.get("MAX_LABELS", "10")),
        MinConfidence=float(os.environ.get("MIN_CONFIDENCE", "80")),
    )
    return [
        {"name": label["Name"], "confidence": round(label["Confidence"], 1)}
        for label in resp.get("Labels", [])
    ]


def _converse(model_id, jpeg, extra_fields):
    kwargs = {
        "modelId": model_id,
        "system": [{"text": CAPTION_SYSTEM}],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": jpeg}}},
                    {"text": "Write the alt text for this image."},
                ],
            }
        ],
        "inferenceConfig": {"maxTokens": 300},
    }
    if extra_fields:
        kwargs["additionalModelRequestFields"] = extra_fields
    return _bedrock().converse(**kwargs)


def generate_caption(jpeg):
    """Return (caption or None, error code or None). Never raises."""
    model_id = os.environ["BEDROCK_MODEL_ID"]
    # A one-sentence caption does not need extended reasoning; turning it off
    # keeps latency and output tokens down on Claude models that think by default.
    extra = {"thinking": {"type": "disabled"}} if "anthropic" in model_id else None
    try:
        try:
            resp = _converse(model_id, jpeg, extra)
        except ClientError as exc:
            if extra and exc.response["Error"]["Code"] == "ValidationException":
                resp = _converse(model_id, jpeg, None)  # model rejected the extra field
            else:
                raise
    except (ClientError, BotoCoreError) as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        logger.warning("Bedrock caption skipped (%s): %s", code, exc)
        return None, code

    parts = resp.get("output", {}).get("message", {}).get("content", [])
    text = " ".join(p["text"] for p in parts if "text" in p).strip().strip('"')
    if not text:
        logger.warning("Bedrock returned no text (stopReason=%s)", resp.get("stopReason"))
        return None, f"empty:{resp.get('stopReason')}"
    return text[:300], None


def handler(event, context):
    data = _s3().get_object(Bucket=event["bucket"], Key=event["key"])["Body"].read()
    jpeg = downscale_jpeg(data)

    labels = detect_labels(jpeg)
    result = {
        "image_id": event["image_id"],
        "labels": labels,
        "tags": [label["name"] for label in labels],
        "caption": None,
        "caption_source": "disabled",
    }

    if os.environ.get("ENABLE_BEDROCK", "false").lower() == "true":
        caption, error = generate_caption(jpeg)
        result["caption"] = caption
        result["caption_source"] = os.environ["BEDROCK_MODEL_ID"] if caption else f"error:{error}"

    logger.info("AI step %s: %d tags, caption=%s", event["image_id"], len(labels), bool(result["caption"]))
    return result
