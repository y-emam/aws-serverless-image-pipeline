# Three private buckets:
#   source    - raw uploads (browser -> pre-signed POST), short-lived
#   processed - thumbnails and watermarked images, read only by CloudFront (OAC)
#   frontend  - static site, read only by CloudFront (OAC)

locals {
  buckets = {
    source    = "${local.name}-uploads-${local.account_id}"
    processed = "${local.name}-processed-${local.account_id}"
    frontend  = "${local.name}-web-${local.account_id}"
  }
  upload_prefix  = "uploads/"
  image_prefix   = "images/"
  staging_prefix = "staging/"
}

resource "aws_s3_bucket" "this" {
  for_each = local.buckets

  bucket        = each.value
  force_destroy = true # lets `terraform destroy` empty the buckets: one-command teardown
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each = aws_s3_bucket.this

  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced" # ACLs disabled; bucket policy is the only access path
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this

  bucket = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256" # SSE-S3: free; SSE-KMS would add per-request KMS cost
    }
  }
}

# ---------------------------------------------------------------- lifecycle

resource "aws_s3_bucket_lifecycle_configuration" "source" {
  bucket = aws_s3_bucket.this["source"].id

  rule {
    id     = "expire-raw-uploads"
    status = "Enabled"
    filter {}

    expiration {
      days = var.raw_retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "processed" {
  bucket = aws_s3_bucket.this["processed"].id

  # Standard-IA bills every object as at least 128 KB, so tiny thumbnails would
  # cost MORE there. Only objects larger than 128 KB are transitioned.
  rule {
    id     = "processed-to-ia"
    status = "Enabled"
    filter {
      and {
        prefix                   = local.image_prefix
        object_size_greater_than = 131072
      }
    }
    transition {
      days          = var.processed_ia_after_days
      storage_class = "STANDARD_IA"
    }
  }

  # Safety net: the watermark step deletes its staging object, but if a run
  # dies in between, the leftover is cleaned up here.
  rule {
    id     = "expire-staging"
    status = "Enabled"
    filter {
      prefix = local.staging_prefix
    }
    expiration {
      days = 1
    }
  }
}

# ---------------------------------------------------------------- CORS (browser uploads straight to S3)

resource "aws_s3_bucket_cors_configuration" "source" {
  bucket = aws_s3_bucket.this["source"].id

  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = concat(["https://${aws_cloudfront_distribution.site.domain_name}"], var.extra_cors_origins)
    allowed_headers = ["*"]
    max_age_seconds = 3000
  }
}

# ---------------------------------------------------------------- bucket policies

data "aws_iam_policy_document" "cloudfront_read" {
  for_each = { processed = local.image_prefix, frontend = "" }

  statement {
    sid       = "AllowCloudFrontOAC"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this[each.key].arn}/${each.value}*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.site.arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this[each.key].arn, "${aws_s3_bucket.this[each.key].arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

data "aws_iam_policy_document" "source_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this["source"].arn, "${aws_s3_bucket.this["source"].arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "processed" {
  bucket     = aws_s3_bucket.this["processed"].id
  policy     = data.aws_iam_policy_document.cloudfront_read["processed"].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket     = aws_s3_bucket.this["frontend"].id
  policy     = data.aws_iam_policy_document.cloudfront_read["frontend"].json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

resource "aws_s3_bucket_policy" "source" {
  bucket     = aws_s3_bucket.this["source"].id
  policy     = data.aws_iam_policy_document.source_tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.this]
}

# ---------------------------------------------------------------- S3 -> SQS

resource "aws_s3_bucket_notification" "source" {
  bucket = aws_s3_bucket.this["source"].id

  queue {
    queue_arn     = aws_sqs_queue.uploads.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = local.upload_prefix
  }

  depends_on = [aws_sqs_queue_policy.uploads]
}
