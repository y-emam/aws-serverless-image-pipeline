import importlib.util
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parent.parent
FUNCTIONS = ROOT / "src" / "functions"
IMAGES = Path(__file__).resolve().parent / "images"

REGION = "us-east-1"
SOURCE = "test-source"
DEST = "test-processed"
TABLE = "test-images"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    """Fake credentials so nothing can ever reach a real AWS account."""
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def load_function(name):
    """Import src/functions/<name>/app.py under a unique module name.

    Every handler file is called app.py, so a plain import would collide.
    """
    module_name = f"fn_{name}"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, FUNCTIONS / name / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def aws():
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=SOURCE)
        s3.create_bucket(Bucket=DEST)
        ddb = boto3.client("dynamodb", region_name=REGION)
        ddb.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "image_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "image_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield {"s3": s3, "table": boto3.resource("dynamodb", region_name=REGION).Table(TABLE)}


def image_bytes(name):
    return (IMAGES / name).read_bytes()


def s3_event(bucket, key, event_name="ObjectCreated:Post"):
    return {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventName": event_name,
                "eventTime": "2026-11-15T10:00:00.000Z",
                "s3": {"bucket": {"name": bucket}, "object": {"key": key}},
            }
        ]
    }
