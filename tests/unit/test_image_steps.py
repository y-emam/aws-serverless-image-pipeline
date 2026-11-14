import io

import pytest
from PIL import Image

from conftest import DEST, SOURCE, image_bytes, load_function


@pytest.fixture
def image_env(monkeypatch):
    monkeypatch.setenv("DEST_BUCKET", DEST)
    monkeypatch.setenv("IMAGE_PREFIX", "images/")
    monkeypatch.setenv("STAGING_PREFIX", "staging/")
    monkeypatch.setenv("THUMBNAIL_PX", "320")
    monkeypatch.setenv("FULL_PX", "1600")
    monkeypatch.setenv("WATERMARK_TEXT", "manara-image-pipeline")


@pytest.fixture
def resize(image_env, aws):
    return load_function("resize")


@pytest.fixture
def watermark(image_env, aws):
    return load_function("watermark")


def get_image(aws, bucket, key):
    obj = aws["s3"].get_object(Bucket=bucket, Key=key)
    return obj, Image.open(io.BytesIO(obj["Body"].read()))


def upload(aws, name, key):
    aws["s3"].put_object(Bucket=SOURCE, Key=key, Body=image_bytes(name))


def test_thumbnail_applies_exif_rotation_and_is_public_path(resize, aws):
    upload(aws, "photo_rotated.jpg", "uploads/p1.jpg")
    out = resize.handler({"image_id": "p1", "bucket": SOURCE, "key": "uploads/p1.jpg", "variant": "thumb"}, None)
    assert out["key"] == "images/p1/thumb.jpg"
    obj, img = get_image(aws, DEST, out["key"])
    # 800x600 stored with "rotate 90" -> displayed as portrait 600x800 -> thumb 240x320
    assert img.size == (240, 320) == (out["width"], out["height"])
    assert img.format == "JPEG"
    assert obj["ContentType"] == "image/jpeg"
    assert "immutable" in obj["CacheControl"]
    assert not img.getexif()  # EXIF (incl. GPS) stripped


def test_full_variant_goes_to_staging_not_public_prefix(resize, aws):
    upload(aws, "photo_rotated.jpg", "uploads/p2.jpg")
    out = resize.handler({"image_id": "p2", "bucket": SOURCE, "key": "uploads/p2.jpg", "variant": "full"}, None)
    assert out["key"] == "staging/p2/full.jpg"
    assert (out["width"], out["height"]) == (600, 800)  # never upscaled past the original


def test_transparent_png_flattened_to_white(resize, aws):
    upload(aws, "transparent.png", "uploads/t.png")
    out = resize.handler({"image_id": "t", "bucket": SOURCE, "key": "uploads/t.png", "variant": "thumb"}, None)
    _, img = get_image(aws, DEST, out["key"])
    assert img.mode == "RGB"
    r, g, b = img.getpixel((2, 2))  # corner was fully transparent
    assert min(r, g, b) > 240


def test_small_image_not_upscaled(resize, aws):
    upload(aws, "small.webp", "uploads/s.webp")
    out = resize.handler({"image_id": "s", "bucket": SOURCE, "key": "uploads/s.webp", "variant": "thumb"}, None)
    assert (out["width"], out["height"]) == (200, 150)


def test_unknown_variant_rejected(resize):
    with pytest.raises(ValueError):
        resize.handler({"image_id": "x", "bucket": SOURCE, "key": "k", "variant": "huge"}, None)


def test_watermark_publishes_and_removes_staging(resize, watermark, aws):
    upload(aws, "photo_rotated.jpg", "uploads/w.jpg")
    staged = resize.handler({"image_id": "w", "bucket": SOURCE, "key": "uploads/w.jpg", "variant": "full"}, None)
    _, before = get_image(aws, DEST, staged["key"])

    out = watermark.handler(staged, None)

    assert out["key"] == "images/w/full.jpg"
    obj, after = get_image(aws, DEST, out["key"])
    assert after.size == before.size
    assert obj["ContentType"] == "image/jpeg"
    # The bottom-right corner changed (watermark), the top-left did not much.
    w, h = after.size
    corner_box = (w - 200, h - 40, w, h)
    diff_corner = sum(abs(a - b) for a, b in zip(after.crop(corner_box).tobytes(), before.crop(corner_box).tobytes()))
    assert diff_corner > 10000
    staging = aws["s3"].list_objects_v2(Bucket=DEST, Prefix="staging/")
    assert staging.get("KeyCount", 0) == 0


def test_watermark_retry_after_success_is_idempotent(resize, watermark, aws):
    upload(aws, "photo_rotated.jpg", "uploads/r.jpg")
    staged = resize.handler({"image_id": "r", "bucket": SOURCE, "key": "uploads/r.jpg", "variant": "full"}, None)
    first = watermark.handler(staged, None)
    second = watermark.handler(staged, None)  # staging copy already deleted
    assert second["key"] == first["key"]
    assert second["bytes"] == first["bytes"]


def test_apply_watermark_handles_tiny_images(watermark):
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, "JPEG")
    out = watermark.apply_watermark(buf.getvalue(), "manara-image-pipeline")
    assert Image.open(io.BytesIO(out)).size == (20, 20)
