# Seven small functions, one IAM role each (least privilege).
# Image functions share the Pillow layer; API functions do not need it.

locals {
  runtime   = "python3.13"
  src_dir   = "${path.module}/../src/functions"
  build_dir = "${path.module}/../build"

  source_arn    = aws_s3_bucket.this["source"].arn
  processed_arn = aws_s3_bucket.this["processed"].arn

  bedrock_base_model = replace(var.bedrock_model_id, "/^(us|eu|apac|global)\\./", "")

  functions = {
    presign = {
      description = "API: returns a pre-signed S3 POST for one upload"
      timeout     = 10
      memory      = 256
      layer       = false
      env = {
        SOURCE_BUCKET  = aws_s3_bucket.this["source"].id
        UPLOAD_PREFIX  = local.upload_prefix
        MAX_BYTES      = tostring(var.max_upload_mb * 1024 * 1024)
        URL_EXPIRES_IN = "300"
      }
      statements = [
        # Pre-signed URLs carry the signer's permissions, so the role needs PutObject.
        { actions = ["s3:PutObject"], resources = ["${local.source_arn}/${local.upload_prefix}*"] },
      ]
    }

    list_images = {
      description = "API: lists processed images for the gallery"
      timeout     = 10
      memory      = 256
      layer       = false
      env = {
        TABLE_NAME = aws_dynamodb_table.images.name
      }
      statements = [
        { actions = ["dynamodb:Scan"], resources = [aws_dynamodb_table.images.arn] },
      ]
    }

    validate = {
      description = "Workflow: checks type, size and dimensions of an upload"
      timeout     = 30
      memory      = 512
      layer       = true
      env = {
        MAX_BYTES     = tostring(var.max_upload_mb * 1024 * 1024)
        MAX_PIXELS    = "40000000"
        UPLOAD_PREFIX = local.upload_prefix
      }
      statements = [
        { actions = ["s3:GetObject"], resources = ["${local.source_arn}/${local.upload_prefix}*"] },
        # Without ListBucket, S3 answers 403 instead of 404 for a missing key.
        { actions = ["s3:ListBucket"], resources = [local.source_arn], prefixes = ["${local.upload_prefix}*"] },
      ]
    }

    resize = {
      description = "Workflow: writes the thumbnail or the full-size staging image"
      timeout     = 60
      memory      = 1024
      layer       = true
      env = {
        DEST_BUCKET    = aws_s3_bucket.this["processed"].id
        IMAGE_PREFIX   = local.image_prefix
        STAGING_PREFIX = local.staging_prefix
        THUMBNAIL_PX   = tostring(var.thumbnail_px)
        FULL_PX        = tostring(var.full_px)
      }
      statements = [
        { actions = ["s3:GetObject"], resources = ["${local.source_arn}/${local.upload_prefix}*"] },
        { actions = ["s3:PutObject"], resources = ["${local.processed_arn}/${local.image_prefix}*", "${local.processed_arn}/${local.staging_prefix}*"] },
      ]
    }

    watermark = {
      description = "Workflow: stamps the watermark on the full-size image"
      timeout     = 60
      memory      = 1024
      layer       = true
      env = {
        DEST_BUCKET    = aws_s3_bucket.this["processed"].id
        IMAGE_PREFIX   = local.image_prefix
        WATERMARK_TEXT = var.watermark_text
      }
      statements = [
        { actions = ["s3:GetObject", "s3:DeleteObject"], resources = ["${local.processed_arn}/${local.staging_prefix}*"] },
        { actions = ["s3:PutObject", "s3:GetObject"], resources = ["${local.processed_arn}/${local.image_prefix}*"] },
        { actions = ["s3:ListBucket"], resources = [local.processed_arn], prefixes = ["${local.staging_prefix}*", "${local.image_prefix}*"] },
      ]
    }

    ai_tag = {
      description = "Workflow: Rekognition labels + optional Bedrock caption"
      timeout     = 60
      memory      = 1024
      layer       = true
      env = {
        ENABLE_BEDROCK   = tostring(var.enable_bedrock)
        BEDROCK_MODEL_ID = var.bedrock_model_id
        MAX_LABELS       = "10"
        MIN_CONFIDENCE   = "80"
      }
      statements = concat(
        [
          { actions = ["s3:GetObject"], resources = ["${local.source_arn}/${local.upload_prefix}*"] },
          # DetectLabels with image bytes does not support resource-level permissions.
          { actions = ["rekognition:DetectLabels"], resources = ["*"] },
        ],
        var.enable_bedrock ? [
          {
            actions = ["bedrock:InvokeModel"]
            resources = [
              "arn:${local.partition}:bedrock:*::foundation-model/${local.bedrock_base_model}",
              # The profile lives in this region; the model ARN needs "*" because a
              # cross-region profile routes requests to other US regions.
              "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:inference-profile/${var.bedrock_model_id}",
            ]
          },
        ] : []
      )
    }

    write_metadata = {
      description = "Workflow: writes the COMPLETED record to DynamoDB"
      timeout     = 10
      memory      = 256
      layer       = false
      env = {
        TABLE_NAME = aws_dynamodb_table.images.name
      }
      statements = [
        { actions = ["dynamodb:PutItem"], resources = [aws_dynamodb_table.images.arn] },
      ]
    }
  }
}

# ---------------------------------------------------------------- packaging

data "archive_file" "function" {
  for_each = local.functions

  type        = "zip"
  source_dir  = "${local.src_dir}/${each.key}"
  output_path = "${local.build_dir}/functions/${each.key}.zip"
  excludes    = ["__pycache__", "**/__pycache__/**"]
}

# Built by scripts/build_layer.py (manylinux wheels for the Lambda architecture).
data "archive_file" "pillow_layer" {
  type        = "zip"
  source_dir  = "${local.build_dir}/layers/pillow-${var.lambda_architecture}"
  output_path = "${local.build_dir}/layers/pillow-${var.lambda_architecture}.zip"
}

resource "aws_lambda_layer_version" "pillow" {
  layer_name               = "${local.name}-pillow"
  description              = "Pillow for ${local.runtime} on ${var.lambda_architecture}"
  filename                 = data.archive_file.pillow_layer.output_path
  source_code_hash         = data.archive_file.pillow_layer.output_base64sha256
  compatible_runtimes      = [local.runtime]
  compatible_architectures = [var.lambda_architecture]
}

# ---------------------------------------------------------------- IAM

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each = local.functions

  name               = "${local.name}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "lambda" {
  for_each = local.functions

  statement {
    sid       = "OwnLogGroupOnly"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.lambda[each.key].arn}:*"]
  }

  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"] # X-Ray has no resource-level permissions
  }

  dynamic "statement" {
    for_each = each.value.statements
    content {
      actions   = statement.value.actions
      resources = statement.value.resources

      dynamic "condition" {
        for_each = try(statement.value.prefixes, null) == null ? [] : [statement.value.prefixes]
        content {
          test     = "StringLike"
          variable = "s3:prefix"
          values   = condition.value
        }
      }
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  for_each = local.functions

  role   = aws_iam_role.lambda[each.key].id
  policy = data.aws_iam_policy_document.lambda[each.key].json
}

# ---------------------------------------------------------------- functions

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.functions

  name              = "/aws/lambda/${local.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "this" {
  for_each = local.functions

  function_name    = "${local.name}-${each.key}"
  description      = each.value.description
  role             = aws_iam_role.lambda[each.key].arn
  runtime          = local.runtime
  architectures    = [var.lambda_architecture]
  handler          = "app.handler"
  filename         = data.archive_file.function[each.key].output_path
  source_code_hash = data.archive_file.function[each.key].output_base64sha256
  timeout          = each.value.timeout
  memory_size      = each.value.memory
  layers           = each.value.layer ? [aws_lambda_layer_version.pillow.arn] : []

  environment {
    variables = merge(each.value.env, { LOG_LEVEL = "INFO" })
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda[each.key].name
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_iam_role_policy.lambda]
}
