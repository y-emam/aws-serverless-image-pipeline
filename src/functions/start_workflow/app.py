"""SQS consumer: one Step Functions Standard execution per uploaded image.

For each SQS message (an S3 event notification):
  1. Skip noise: the s3:TestEvent S3 sends when the notification is created,
     and keys that are not images under the upload prefix.
  2. Write a RECEIVED row to DynamoDB (only if no row exists yet).
  3. Start an execution with a deterministic name, so a duplicate S3 event
     (S3 delivers at least once) cannot process the same image twice.
  4. Report only the failed messages back to SQS (ReportBatchItemFailures).
"""

import hashlib
import json
import logging
import os
import re
from functools import cache
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


@cache
def _sfn():
    return boto3.client("stepfunctions")


@cache
def _table():
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def log(level, message, **fields):
    """One JSON object per line: easy to query in CloudWatch Logs Insights."""
    logger.log(level, json.dumps({"message": message, **fields}, default=str))


def image_id_from_key(key):
    """Same rule as the validate step: the file name without extension."""
    stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    clean = re.sub(r"[^A-Za-z0-9_-]", "-", stem)[:64]
    return clean or "unnamed"


def execution_name(image_id, record):
    """Deterministic, <= 80 chars, only [A-Za-z0-9-_].

    The S3 sequencer is unique per object version, so a re-upload of the same
    key still gets a new execution, while a duplicate delivery of the same
    event gets the same name and is rejected by Step Functions.
    """
    s3 = record["s3"]
    token = s3["object"].get("sequencer") or hashlib.sha256(
        f"{s3['object']['key']}|{record.get('eventTime')}".encode()
    ).hexdigest()[:20]
    return f"{image_id[:58]}-{token[-20:]}"


def images_in_message(body, prefix):
    """Yield (bucket, key, record) for each image record in one S3 event."""
    for record in body.get("Records", []):
        if record.get("eventSource") != "aws:s3" or not record.get("eventName", "").startswith("ObjectCreated:"):
            continue
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])  # keys arrive URL-encoded
        if not key.startswith(prefix) or not key.lower().endswith(IMAGE_EXTENSIONS):
            log(logging.INFO, "skip non-image key", key=key)
            continue
        yield bucket, key, record


def record_received(image_id, key, uploaded_at):
    try:
        _table().put_item(
            Item={"image_id": image_id, "status": "RECEIVED", "source_key": key, "uploaded_at": uploaded_at},
            # Never overwrite a later status (COMPLETED/FAILED) on a duplicate event.
            ConditionExpression="attribute_not_exists(image_id)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def start(bucket, key, record):
    image_id = image_id_from_key(key)
    uploaded_at = record.get("eventTime")
    name = execution_name(image_id, record)

    record_received(image_id, key, uploaded_at)
    try:
        resp = _sfn().start_execution(
            stateMachineArn=os.environ["STATE_MACHINE_ARN"],
            name=name,
            input=json.dumps({"image_id": image_id, "bucket": bucket, "key": key, "uploaded_at": uploaded_at}),
        )
        log(logging.INFO, "started", image_id=image_id, execution=resp["executionArn"])
    except _sfn().exceptions.ExecutionAlreadyExists:
        log(logging.INFO, "duplicate event, execution already exists", image_id=image_id, execution_name=name)


def handler(event, context):
    prefix = os.environ.get("UPLOAD_PREFIX", "uploads/")
    failures = []

    for message in event.get("Records", []):
        message_id = message["messageId"]
        try:
            body = json.loads(message["body"])
            if body.get("Event") == "s3:TestEvent":
                log(logging.INFO, "skip s3:TestEvent", message_id=message_id)
                continue
            for bucket, key, record in images_in_message(body, prefix):
                start(bucket, key, record)
        except Exception as exc:  # noqa: BLE001 - one bad message must not fail the batch
            log(logging.ERROR, "message failed", message_id=message_id, error=repr(exc))
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
