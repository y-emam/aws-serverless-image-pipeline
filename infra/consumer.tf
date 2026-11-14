# SQS -> start_workflow Lambda -> Step Functions (the brief: "a Lambda function polls the queue").
#
# Kept outside the for_each in lambda.tf on purpose: the state machine depends
# on the workflow functions, and this function depends on the state machine.
# Inside the same for_each that would be a dependency cycle.

locals {
  consumer_name    = "${local.name}-start-workflow"
  consumer_timeout = 30
}

data "archive_file" "start_workflow" {
  type        = "zip"
  source_dir  = "${local.src_dir}/start_workflow"
  output_path = "${local.build_dir}/functions/start_workflow.zip"
  excludes    = ["__pycache__", "**/__pycache__/**"]
}

resource "aws_cloudwatch_log_group" "start_workflow" {
  name              = "/aws/lambda/${local.consumer_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "start_workflow" {
  name               = local.consumer_name
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "start_workflow" {
  statement {
    sid       = "OwnLogGroupOnly"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.start_workflow.arn}:*"]
  }
  statement {
    sid       = "XRay"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
  # The event source mapping polls with the FUNCTION's role, so these three
  # SQS actions belong here, scoped to the one queue.
  statement {
    sid       = "PollQueue"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.uploads.arn]
  }
  statement {
    sid       = "StartPipeline"
    actions   = ["states:StartExecution"]
    resources = [aws_sfn_state_machine.pipeline.arn]
  }
  statement {
    sid       = "WriteReceivedRow"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.images.arn]
  }
}

resource "aws_iam_role_policy" "start_workflow" {
  role   = aws_iam_role.start_workflow.id
  policy = data.aws_iam_policy_document.start_workflow.json
}

resource "aws_lambda_function" "start_workflow" {
  function_name    = local.consumer_name
  description      = "SQS consumer: starts one Step Functions execution per uploaded image"
  role             = aws_iam_role.start_workflow.arn
  runtime          = local.runtime
  architectures    = [var.lambda_architecture]
  handler          = "app.handler"
  filename         = data.archive_file.start_workflow.output_path
  source_code_hash = data.archive_file.start_workflow.output_base64sha256
  timeout          = local.consumer_timeout
  memory_size      = 256

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.pipeline.arn
      TABLE_NAME        = aws_dynamodb_table.images.name
      UPLOAD_PREFIX     = local.upload_prefix
      LOG_LEVEL         = "INFO"
    }
  }

  logging_config {
    log_format = "Text" # the handler already writes one JSON object per line
    log_group  = aws_cloudwatch_log_group.start_workflow.name
  }

  tracing_config {
    mode = "Active"
  }

  depends_on = [aws_iam_role_policy.start_workflow]
}

resource "aws_lambda_event_source_mapping" "uploads" {
  event_source_arn = aws_sqs_queue.uploads.arn
  function_name    = aws_lambda_function.start_workflow.arn
  batch_size       = 10 # up to 10 messages per invocation

  # Lets the handler return only the failed message ids, so one bad message
  # does not make SQS redeliver the other nine.
  function_response_types = ["ReportBatchItemFailures"]

  # Cap on concurrent consumer invocations (2 is the minimum). Protects small
  # accounts' Lambda concurrency during an upload burst.
  scaling_config {
    maximum_concurrency = 2
  }
}

resource "aws_cloudwatch_metric_alarm" "start_workflow_errors" {
  alarm_name          = "${local.consumer_name}-errors"
  alarm_description   = "The SQS consumer failed (whole invocation, not a single message)."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.start_workflow.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.notifications.arn]
}
