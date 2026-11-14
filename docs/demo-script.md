# Demo video script (about 5 minutes)

Record at 1080p. Hide the AWS account id (top-right menu) and your email before recording.
Before you start, open these tabs: the site, Step Functions, DynamoDB (Explore items),
CloudWatch alarms, S3, and your email inbox. Have two images ready: a phone photo (portrait,
with EXIF rotation) and a text file renamed to `fake.jpg`.

## 1. Intro (30 s) - diagram on screen

"This is a serverless image processing pipeline on AWS, built for the Manara Solutions
Architect course. An upload goes to S3, an event goes through SQS to a Lambda that starts a
Step Functions workflow. The workflow validates, resizes, watermarks, tags the image with
Rekognition, captions it with Claude on Bedrock, stores metadata in DynamoDB and emails me.
CloudFront serves the result. Everything is Terraform and pay-per-use."

## 2. Upload (45 s) - the site

- Upload the phone photo. Point out the status line: upload URL, upload, processing.
- "The browser uploads straight to a private S3 bucket using a pre-signed POST. The policy
  limits it to one key, image types and 10 MB."
- When it appears in the gallery: read the caption and tags. Click it to open the watermarked
  full-size image. Point at the watermark and at the URL (`/images/...` on CloudFront).

## 3. Under the hood (90 s)

- **Step Functions:** open the latest execution, show the graph. "Validate, then three branches
  in parallel: thumbnail, full size plus watermark, and the AI step. Then metadata and a
  notification." Click the ai_tag step output to show labels and caption.
- **DynamoDB:** show the item: status COMPLETED, dimensions, tags, caption, output keys.
- **Email:** show the success email with the links.
- **S3:** show the processed bucket `images/<id>/thumb.jpg` and `full.jpg`. Show that
  `staging/` is empty. Open the direct S3 object URL in a new tab -> Access Denied. "The bucket
  is private; only CloudFront can read it, through Origin Access Control."

## 4. Failure path (45 s)

- Upload `fake.jpg`. It appears as FAILED.
- Step Functions: show the failed execution taking the MarkFailed path.
- DynamoDB: status FAILED, error_type InvalidImage. Show the failure email.
- "Bad input is recorded, not lost. Transient errors are retried with backoff; messages that
  can't even start a workflow go to a dead-letter queue, and an alarm emails me."

## 5. Ops and cost (45 s)

- CloudWatch alarms list: DLQ depth, Lambda errors, failed executions.
- S3 lifecycle rules on the source and processed buckets: expire raw uploads after 30 days,
  Standard-IA after 30 days for images over 128 KB.
- "Nothing here bills by the hour. Idle cost is close to zero, and a budget alert is set at
  five dollars."

## 6. Close (20 s) - terminal

- Show `terraform -chdir=infra plan` (no changes) and say: "The whole stack is one Terraform
  apply, and one `terraform destroy` removes it."
- "Code, diagram and write-up are in the GitHub repo."
