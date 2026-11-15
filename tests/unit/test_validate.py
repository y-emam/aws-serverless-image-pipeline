import json

import pytest

from conftest import SOURCE, image_bytes, load_function, s3_event


@pytest.fixture
def validate(monkeypatch, aws):
    monkeypatch.setenv("MAX_BYTES", str(5 * 1024 * 1024))
    monkeypatch.setenv("MAX_PIXELS", "40000000")
    monkeypatch.setenv("UPLOAD_PREFIX", "uploads/")
    return load_function("validate")


def put(aws, key, data):
    aws["s3"].put_object(Bucket=SOURCE, Key=key, Body=data)


def sqs_batch(bucket, key):
    """A list of SQS messages, as in a raw SQS batch (manual runs)."""
    return [{"messageId": "m-1", "body": json.dumps(s3_event(bucket, key))}]


@pytest.mark.parametrize(
    "event_factory",
    [
        lambda k: sqs_batch(SOURCE, k),
        lambda k: sqs_batch(SOURCE, k)[0],
        lambda k: {"body": s3_event(SOURCE, k)},
        lambda k: s3_event(SOURCE, k),
        lambda k: {"bucket": SOURCE, "key": k},
    ],
    ids=["sqs-batch", "sqs-record", "sqs-parsed-body", "s3-event", "manual"],
)
def test_accepts_every_event_shape(validate, aws, event_factory):
    put(aws, "uploads/abc123.jpg", image_bytes("photo_rotated.jpg"))
    out = validate.handler(event_factory("uploads/abc123.jpg"), None)
    assert out["valid"] is True
    assert out["image_id"] == "abc123"
    assert (out["width"], out["height"], out["format"]) == (600, 800, "JPEG")  # EXIF rotation applied


def test_url_encoded_key_is_decoded(validate, aws):
    put(aws, "uploads/my photo.jpg", image_bytes("photo_rotated.jpg"))
    out = validate.handler(s3_event(SOURCE, "uploads/my+photo.jpg"), None)
    assert out["valid"] is True
    assert out["key"] == "uploads/my photo.jpg"
    assert out["image_id"] == "my-photo"


@pytest.mark.parametrize("name,fmt", [("transparent.png", "PNG"), ("small.webp", "WEBP")])
def test_other_allowed_formats(validate, aws, name, fmt):
    put(aws, f"uploads/x.{name.split('.')[-1]}", image_bytes(name))
    out = validate.handler({"bucket": SOURCE, "key": f"uploads/x.{name.split('.')[-1]}"}, None)
    assert out["valid"] is True and out["format"] == fmt


def test_rejects_text_disguised_as_jpeg(validate, aws):
    put(aws, "uploads/fake.jpg", image_bytes("not_an_image.jpg"))
    out = validate.handler({"bucket": SOURCE, "key": "uploads/fake.jpg"}, None)
    assert out["valid"] is False
    assert out["error"]["Error"] == "InvalidImage"
    assert out["image_id"] == "fake" and out["uploaded_at"]  # MarkFailed needs these


def test_rejects_disallowed_format(validate, aws):
    put(aws, "uploads/a.gif", image_bytes("anim.gif"))
    out = validate.handler({"bucket": SOURCE, "key": "uploads/a.gif"}, None)
    assert out["valid"] is False and "GIF" in out["error"]["Cause"]


def test_rejects_too_large_file(validate, aws, monkeypatch):
    monkeypatch.setenv("MAX_BYTES", "100")
    put(aws, "uploads/big.jpg", image_bytes("photo_rotated.jpg"))
    out = validate.handler({"bucket": SOURCE, "key": "uploads/big.jpg"}, None)
    assert out["valid"] is False and "limit" in out["error"]["Cause"]


def test_rejects_empty_file(validate, aws):
    put(aws, "uploads/empty.jpg", b"")
    out = validate.handler({"bucket": SOURCE, "key": "uploads/empty.jpg"}, None)
    assert out["valid"] is False


def test_rejects_decompression_bomb(validate, aws, monkeypatch):
    monkeypatch.setenv("MAX_PIXELS", "10000")  # 800x600 is far above this
    put(aws, "uploads/bomb.jpg", image_bytes("photo_rotated.jpg"))
    out = validate.handler({"bucket": SOURCE, "key": "uploads/bomb.jpg"}, None)
    assert out["valid"] is False


def test_missing_object_is_invalid_not_an_exception(validate):
    out = validate.handler({"bucket": SOURCE, "key": "uploads/gone.jpg"}, None)
    assert out["valid"] is False and "no longer exists" in out["error"]["Cause"]


def test_key_outside_upload_prefix_is_invalid(validate, aws):
    put(aws, "other/x.jpg", image_bytes("photo_rotated.jpg"))
    out = validate.handler({"bucket": SOURCE, "key": "other/x.jpg"}, None)
    assert out["valid"] is False


@pytest.mark.parametrize("event", [{}, [], {"body": "not json"}, {"Records": [{"s3": {}}]}, "string"])
def test_unparseable_event_raises(validate, event):
    with pytest.raises(validate.BadEventError):
        validate.handler(event, None)


def _jpeg(size=(800, 600), **save_kwargs):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buf, "JPEG", **save_kwargs)
    return buf.getvalue()


def test_accepts_mpo_phone_photo_as_jpeg(validate, aws):
    """Samsung / iPhone portrait shots are MPO: a JPEG plus extra frames."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    first, second = Image.new("RGB", (640, 480), "red"), Image.new("RGB", (640, 480), "blue")
    first.save(buf, "MPO", save_all=True, append_images=[second])
    put(aws, "uploads/mpo.jpg", buf.getvalue())
    out = validate.handler({"bucket": SOURCE, "key": "uploads/mpo.jpg"}, None)
    assert out["valid"] is True
    assert out["format"] == "JPEG" and out["content_type"] == "image/jpeg"


def test_rejects_truncated_jpeg(validate, aws):
    data = image_bytes("photo_rotated.jpg")
    put(aws, "uploads/cut.jpg", data[: len(data) // 2])
    out = validate.handler({"bucket": SOURCE, "key": "uploads/cut.jpg"}, None)
    assert out["valid"] is False and out["error"]["Error"] == "InvalidImage"


def test_reports_displayed_size_for_rotated_photo(validate, aws):
    put(aws, "uploads/rot.jpg", image_bytes("photo_rotated.jpg"))  # stored 800x600, EXIF "rotate 90"
    out = validate.handler({"bucket": SOURCE, "key": "uploads/rot.jpg"}, None)
    assert (out["width"], out["height"]) == (600, 800)


def test_decompression_bomb_warning_band_is_rejected(validate, aws, monkeypatch):
    # Pillow warns between MAX_IMAGE_PIXELS and 2x that; the step treats it as an error too.
    monkeypatch.setenv("MAX_PIXELS", str(800 * 600 - 1))
    put(aws, "uploads/band.jpg", _jpeg((800, 600)))
    out = validate.handler({"bucket": SOURCE, "key": "uploads/band.jpg"}, None)
    assert out["valid"] is False
