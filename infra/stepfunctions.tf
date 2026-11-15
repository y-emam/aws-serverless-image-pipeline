data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    # Built as a string (not the resource ARN) to avoid a dependency cycle.
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:states:${local.region}:${local.account_id}:stateMachine:${local.name}-pipeline"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name}-workflow"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

data "aws_iam_policy_document" "sfn" {
  statement {
    sid     = "InvokeWorkflowFunctions"
    actions = ["lambda:InvokeFunction"]
    resources = flatten([
      for fn in ["validate", "resize", "watermark", "ai_tag", "write_metadata"] : [
        aws_lambda_function.this[fn].arn,
        "${aws_lambda_function.this[fn].arn}:*",
      ]
    ])
  }

  statement {
    sid       = "MarkFailed"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.images.arn]
  }

  statement {
    sid       = "Notify"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.notifications.arn]
  }

  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
    resources = ["*"]
  }

  # Step Functions log delivery uses these account-level CloudWatch Logs APIs,
  # which do not support resource-level permissions.
  statement {
    sid = "LogDelivery"
    actions = [
      "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn" {
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/vendedlogs/states/${local.name}-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.name}-pipeline"
  type     = "STANDARD"
  role_arn = aws_iam_role.sfn.arn

  definition = templatefile("${path.module}/statemachine/pipeline.asl.json", {
    partition          = local.partition
    validate_arn       = aws_lambda_function.this["validate"].arn
    resize_arn         = aws_lambda_function.this["resize"].arn
    watermark_arn      = aws_lambda_function.this["watermark"].arn
    ai_tag_arn         = aws_lambda_function.this["ai_tag"].arn
    write_metadata_arn = aws_lambda_function.this["write_metadata"].arn
    table_name         = aws_dynamodb_table.images.name
    topic_arn          = aws_sns_topic.notifications.arn
    cdn_domain         = aws_cloudfront_distribution.site.domain_name
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = false # keep image metadata out of the logs
    level                  = "ERROR"
  }

  tracing_configuration {
    enabled = true
  }

  depends_on = [aws_iam_role_policy.sfn]
}
