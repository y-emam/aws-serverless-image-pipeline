"""End-to-end test against the deployed stack.

    python tests/e2e/e2e_test.py              # uses AWS_PROFILE and terraform outputs

What it checks:
  1. A real JPEG goes through the whole pipeline -> COMPLETED in DynamoDB,
     with tags, and both images served by CloudFront.
  2. A text file disguised as a JPEG -> FAILED / InvalidImage in DynamoDB.
  3. The processed bucket is private: a direct S3 URL returns 403.
  4. The gallery API lists the new image.

Costs a few cents at most (Rekognition + Bedrock for one image).
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import boto3
import requests

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "tests" / "images"
TIMEOUT_S = 180


def terraform_outputs():
    tf = os.environ.get("TERRAFORM") or shutil.which("terraform") or str(Path.home() / "tools/terraform/terraform.exe")
    raw = subprocess.run([tf, f"-chdir={ROOT / 'infra'}", "output", "-json"], check=True, capture_output=True, text=True)
    return {k: v["value"] for k, v in json.loads(raw.stdout).items()}


def upload(site, path, content_type):
    res = requests.post(f"{site}/api/uploads", json={"filename": path.name, "content_type": content_type}, timeout=15)
    res.raise_for_status()
    target = res.json()
    with path.open("rb") as fh:
        s3 = requests.post(target["url"], data=target["fields"], files={"file": (path.name, fh, content_type)}, timeout=30)
    if s3.status_code not in (200, 204):
        raise AssertionError(f"S3 upload failed {s3.status_code}: {s3.text[:300]}")
    return target["image_id"]


def wait_for_record(table, image_id):
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        item = table.get_item(Key={"image_id": image_id}).get("Item")
        if item and item.get("status") in ("COMPLETED", "FAILED"):
            return item
        time.sleep(3)
    raise AssertionError(f"{image_id}: no final status after {TIMEOUT_S}s")


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, name, condition, detail=""):
        print(f"  [{'PASS' if condition else 'FAIL'}] {name}{'  -- ' + str(detail) if detail and not condition else ''}")
        self.failed += 0 if condition else 1


def main():
    out = terraform_outputs()
    site = out["site_url"]
    session = boto3.Session(region_name=out["region"])
    table = session.resource("dynamodb").Table(out["table_name"])
    c = Checks()

    print(f"Site: {site}")

    print("1. Valid image")
    started = time.time()
    image_id = upload(site, IMAGES / "photo_rotated.jpg", "image/jpeg")
    item = wait_for_record(table, image_id)
    c.check("status COMPLETED", item["status"] == "COMPLETED", item.get("error_cause"))
    if item["status"] == "COMPLETED":
        print(f"     processed in {time.time() - started:.0f}s; tags={item.get('tags')}; caption={item.get('caption')!r}")
        c.check("has Rekognition tags", len(item.get("tags", [])) > 0)
        c.check("EXIF rotation applied (portrait)", int(item["full_height"]) > int(item["full_width"]))
        for key in ("thumb_key", "full_key"):
            res = requests.get(f"{site}/{item[key]}", timeout=15)
            c.check(f"CloudFront serves {key}", res.status_code == 200 and res.headers.get("content-type") == "image/jpeg", res.status_code)
        direct = requests.get(f"https://{out['processed_bucket']}.s3.{out['region']}.amazonaws.com/{item['full_key']}", timeout=15)
        c.check("processed bucket is private (direct S3 = 403)", direct.status_code == 403, direct.status_code)

    print("2. Invalid image")
    bad_id = upload(site, IMAGES / "not_an_image.jpg", "image/jpeg")
    bad = wait_for_record(table, bad_id)
    c.check("status FAILED", bad["status"] == "FAILED")
    c.check("error_type InvalidImage", bad.get("error_type") == "InvalidImage", bad.get("error_type"))

    print("3. Gallery API")
    listed = requests.get(f"{site}/api/images", timeout=15).json()["images"]
    c.check("gallery lists the new image", any(i["image_id"] == image_id for i in listed))

    print("4. Upload policy")
    res = requests.post(f"{site}/api/uploads", json={"content_type": "text/html"}, timeout=15)
    c.check("API rejects non-image content type", res.status_code == 400, res.status_code)

    print("FAILED checks: %d" % c.failed if c.failed else "All checks passed.")
    sys.exit(1 if c.failed else 0)


if __name__ == "__main__":
    main()
