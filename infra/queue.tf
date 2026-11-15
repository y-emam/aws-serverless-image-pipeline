# S3 event -> SQS (+ DLQ) -> start_workflow Lambda (consumer.tf) -> Step Functions

resource "aws_sqs_queue" "uploads_dlq" {
  name                      = "${local.name}-uploads-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum: time to inspect failures
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "uploads" {
  name                    = "${local.name}-uploads"
  sqs_managed_sse_enabled = true

  # SQS hides a received message for this long. It must outlast the consumer,
  # or a second Lambda gets the same message while the first is still working.
  # AWS guidance for Lambda consumers: at least 6x the function timeout (30 s),
  # to cover batching, throttling and retries.
  visibility_timeout_seconds = local.consumer_timeout * 6
  # Long polling for any manual consumer (CLI, debugging). The Lambda event
  # source mapping does its own long polling and ignores this setting.
  receive_wait_time_seconds = 20
  message_retention_seconds = 345600 # 4 days

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.uploads_dlq.arn
    # AWS recommends >= 5 for Lambda consumers so throttled (not failed)
    # receives do not push healthy messages into the DLQ.
    maxReceiveCount = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "uploads_dlq" {
  queue_url = aws_sqs_queue.uploads_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.uploads.arn]
  })
}

data "aws_iam_policy_document" "uploads_queue" {
  statement {
    sid       = "AllowS3SendFromSourceBucket"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.uploads.arn]

    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.this["source"].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_sqs_queue_policy" "uploads" {
  queue_url = aws_sqs_queue.uploads.id
  policy    = data.aws_iam_policy_document.uploads_queue.json
}
