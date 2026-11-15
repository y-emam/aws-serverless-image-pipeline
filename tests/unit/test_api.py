import json
from decimal import Decimal

import pytest

from conftest import SOURCE, TABLE, load_function


@pytest.fixture
def presign(monkeypatch, aws):
    monkeypatch.setenv("SOURCE_BUCKET", SOURCE)
    monkeypatch.setenv("UPLOAD_PREFIX", "uploads/")
    monkeypatch.setenv("MAX_BYTES", "1048576")
    return load_function("presign")


@pytest.fixture
def list_images(monkeypatch, aws):
    monkeypatch.setenv("TABLE_NAME", TABLE)
    return load_function("list_images")


def call(fn, body):
    return fn.handler({"body": json.dumps(body)}, None)


def test_presign_returns_post_policy_for_allowed_type(presign):
    res = call(presign, {"filename": "cat.png", "content_type": "image/png"})
    assert res["statusCode"] == 200
    body = json.loads(res["body"])
    assert body["key"] == f"uploads/{body['image_id']}.png"
    assert body["fields"]["key"] == body["key"]
    assert body["fields"]["Content-Type"] == "image/png"
    assert "policy" in body["fields"]
    assert SOURCE in body["url"]
    assert body["max_bytes"] == 1048576


def test_presign_policy_enforces_size_and_type(presign):
    import base64

    body = json.loads(call(presign, {"content_type": "image/jpeg"})["body"])
    policy = json.loads(base64.b64decode(body["fields"]["policy"]))
    conditions = policy["conditions"]
    assert ["content-length-range", 1, 1048576] in conditions
    assert {"Content-Type": "image/jpeg"} in conditions


@pytest.mark.parametrize("content_type", ["image/gif", "text/html", "", None])
def test_presign_rejects_other_types(presign, content_type):
    res = call(presign, {"content_type": content_type})
    assert res["statusCode"] == 400


def test_presign_rejects_non_json(presign):
    res = presign.handler({"body": "not json"}, None)
    assert res["statusCode"] == 400


def test_presign_ids_are_unique(presign):
    ids = {json.loads(call(presign, {"content_type": "image/webp"})["body"])["image_id"] for _ in range(20)}
    assert len(ids) == 20


def test_list_images_sorted_newest_first_and_json_safe(list_images, aws):
    table = aws["table"]
    table.put_item(Item={"image_id": "a", "status": "COMPLETED", "uploaded_at": "2026-11-01T00:00:00Z",
                         "width": Decimal(800), "tags": ["Dog"], "source_key": "uploads/a.jpg"})
    table.put_item(Item={"image_id": "b", "status": "FAILED", "uploaded_at": "2026-11-02T00:00:00Z",
                         "error_type": "InvalidImage"})
    res = list_images.handler({}, None)
    images = json.loads(res["body"])["images"]
    assert [i["image_id"] for i in images] == ["b", "a"]
    assert images[1]["width"] == 800
    assert "source_key" not in images[1]  # internal fields are not exposed


def test_list_images_empty(list_images):
    images = json.loads(list_images.handler({}, None)["body"])["images"]
    assert images == []


@pytest.mark.parametrize("raw", ["null", "[]", "5", '"text"'])
def test_presign_rejects_json_that_is_not_an_object(presign, raw):
    assert presign.handler({"body": raw}, None)["statusCode"] == 400


def test_presign_accepts_base64_body(presign):
    import base64

    raw = base64.b64encode(json.dumps({"content_type": "image/png"}).encode()).decode()
    res = presign.handler({"body": raw, "isBase64Encoded": True}, None)
    assert res["statusCode"] == 200


def test_list_images_caps_results(list_images, aws):
    with aws["table"].batch_writer() as batch:
        for i in range(75):
            batch.put_item(Item={"image_id": f"id{i:03d}", "status": "COMPLETED", "uploaded_at": f"2026-11-01T00:{i // 60:02d}:{i % 60:02d}Z"})
    images = json.loads(list_images.handler({}, None)["body"])["images"]
    assert len(images) == list_images.MAX_ITEMS
    assert images[0]["image_id"] == "id074"  # newest first
