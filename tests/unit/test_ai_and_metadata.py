import boto3
import pytest
from botocore.stub import ANY, Stubber

from conftest import REGION, SOURCE, TABLE, image_bytes, load_function

MODEL = "us.anthropic.claude-haiku-5-5"


@pytest.fixture
def ai(monkeypatch, aws):
    monkeypatch.setenv("BEDROCK_MODEL_ID", MODEL)
    monkeypatch.setenv("MAX_LABELS", "10")
    monkeypatch.setenv("MIN_CONFIDENCE", "80")
    module = load_function("ai_tag")

    rek = boto3.client("rekognition", region_name=REGION)
    bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    module._rekognition = lambda: rek
    module._bedrock = lambda: bedrock
    aws["s3"].put_object(Bucket=SOURCE, Key="uploads/a.jpg", Body=image_bytes("photo_rotated.jpg"))

    with Stubber(rek) as rek_stub, Stubber(bedrock) as br_stub:
        yield module, rek_stub, br_stub


LABELS = {"Labels": [{"Name": "Sun", "Confidence": 97.123}, {"Name": "Art", "Confidence": 88.04}]}
EVENT = {"image_id": "a", "bucket": SOURCE, "key": "uploads/a.jpg"}
REK_PARAMS = {"Image": {"Bytes": ANY}, "MaxLabels": 10, "MinConfidence": 80.0}


def converse_response(text, stop="end_turn"):
    content = [{"text": text}] if text else []
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        "metrics": {"latencyMs": 100},
    }


def test_labels_and_caption(ai, monkeypatch):
    module, rek, br = ai
    monkeypatch.setenv("ENABLE_BEDROCK", "true")
    rek.add_response("detect_labels", LABELS, REK_PARAMS)
    br.add_response("converse", converse_response('"A yellow circle on a gradient."'))

    out = module.handler(EVENT, None)

    assert out["tags"] == ["Sun", "Art"]
    assert out["labels"][0] == {"name": "Sun", "confidence": 97.1}
    assert out["caption"] == "A yellow circle on a gradient."
    assert out["caption_source"] == MODEL


def test_bedrock_disabled_skips_call(ai, monkeypatch):
    module, rek, br = ai
    monkeypatch.setenv("ENABLE_BEDROCK", "false")
    rek.add_response("detect_labels", LABELS, REK_PARAMS)

    out = module.handler(EVENT, None)

    assert out["caption"] is None and out["caption_source"] == "disabled"
    br.assert_no_pending_responses()


def test_bedrock_access_denied_does_not_fail_the_image(ai, monkeypatch):
    module, rek, br = ai
    monkeypatch.setenv("ENABLE_BEDROCK", "true")
    rek.add_response("detect_labels", LABELS, REK_PARAMS)
    br.add_client_error("converse", "AccessDeniedException", "no model access")

    out = module.handler(EVENT, None)

    assert out["tags"] == ["Sun", "Art"]
    assert out["caption"] is None
    assert out["caption_source"] == "error:AccessDeniedException"


def test_bedrock_retries_without_thinking_field_on_validation_error(ai, monkeypatch):
    module, rek, br = ai
    monkeypatch.setenv("ENABLE_BEDROCK", "true")
    rek.add_response("detect_labels", LABELS, REK_PARAMS)
    br.add_client_error("converse", "ValidationException", "unknown field thinking")
    br.add_response("converse", converse_response("A sun."))

    out = module.handler(EVENT, None)

    assert out["caption"] == "A sun."


def test_empty_bedrock_reply_gives_no_caption(ai, monkeypatch):
    module, rek, br = ai
    monkeypatch.setenv("ENABLE_BEDROCK", "true")
    rek.add_response("detect_labels", LABELS, REK_PARAMS)
    br.add_response("converse", converse_response("", stop="max_tokens"))

    out = module.handler(EVENT, None)

    assert out["caption"] is None and out["caption_source"] == "error:empty:max_tokens"


def test_rekognition_failure_propagates(ai, monkeypatch):
    module, rek, _ = ai
    monkeypatch.setenv("ENABLE_BEDROCK", "false")
    rek.add_client_error("detect_labels", "ThrottlingException", "slow down")
    with pytest.raises(Exception) as exc:
        module.handler(EVENT, None)
    assert type(exc.value).__name__ == "ThrottlingException"  # matches the ASL Retry


# ---------------------------------------------------------------- write_metadata


@pytest.fixture
def write_metadata(monkeypatch, aws):
    monkeypatch.setenv("TABLE_NAME", TABLE)
    return load_function("write_metadata")


def workflow_state(caption="A sun."):
    return {
        "valid": True, "image_id": "img1", "bucket": SOURCE, "key": "uploads/img1.jpg",
        "uploaded_at": "2026-11-15T10:00:00.000Z", "size_bytes": 1234, "format": "JPEG",
        "content_type": "image/jpeg", "width": 800, "height": 600,
        "outputs": [
            {"image_id": "img1", "variant": "thumb", "key": "images/img1/thumb.jpg", "width": 320, "height": 240, "bytes": 1},
            {"image_id": "img1", "variant": "full", "key": "images/img1/full.jpg", "width": 800, "height": 600, "bytes": 2},
            {"image_id": "img1", "tags": ["Sun"], "labels": [{"name": "Sun", "confidence": 97.1}],
             "caption": caption, "caption_source": MODEL},
        ],
    }


def test_write_metadata_puts_completed_record(write_metadata, aws):
    out = write_metadata.handler({"execution": workflow_state(), "cdn_domain": "d123.cloudfront.net"}, None)

    item = aws["table"].get_item(Key={"image_id": "img1"})["Item"]
    assert item["status"] == "COMPLETED"
    assert item["tags"] == ["Sun"]
    assert item["caption"] == "A sun."
    assert item["thumb_key"] == "images/img1/thumb.jpg"
    assert item["full_key"] == "images/img1/full.jpg"
    assert item["width"] == 800 and item["height"] == 600
    assert "https://d123.cloudfront.net/images/img1/thumb.jpg" in out["message"]
    assert "Tags: Sun" in out["message"]


def test_write_metadata_without_caption_and_any_branch_order(write_metadata, aws):
    state = workflow_state(caption=None)
    state["outputs"].reverse()
    out = write_metadata.handler({"execution": state, "cdn_domain": "d"}, None)
    item = aws["table"].get_item(Key={"image_id": "img1"})["Item"]
    assert "caption" not in item
    assert "Caption: (none)" in out["message"]


def test_write_metadata_is_idempotent(write_metadata, aws):
    for _ in range(2):
        write_metadata.handler({"execution": workflow_state(), "cdn_domain": "d"}, None)
    assert aws["table"].scan()["Count"] == 1
