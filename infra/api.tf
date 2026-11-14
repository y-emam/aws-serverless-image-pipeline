# HTTP API (not REST API): about 70% cheaper, lower latency, and we need none of
# the REST-only features (usage plans, API keys, request validation, WAF on the API).
# It sits behind CloudFront at /api/*, so the browser sees one origin.

resource "aws_apigatewayv2_api" "http" {
  name          = "${local.name}-api"
  protocol_type = "HTTP"

  dynamic "cors_configuration" {
    for_each = length(var.extra_cors_origins) > 0 ? [1] : []
    content {
      allow_origins = var.extra_cors_origins
      allow_methods = ["GET", "POST"]
      allow_headers = ["content-type"]
      max_age       = 3000
    }
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.name}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true

  # The upload endpoint is public. Throttling caps how fast anyone can mint
  # upload URLs (and so how much processing they can trigger).
  default_route_settings {
    throttling_rate_limit  = var.api_throttle_rate
    throttling_burst_limit = var.api_throttle_burst
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId = "$context.requestId"
      ip        = "$context.identity.sourceIp"
      route     = "$context.routeKey"
      status    = "$context.status"
      latency   = "$context.responseLatency"
    })
  }
}

locals {
  api_routes = {
    "POST /api/uploads" = "presign"
    "GET /api/images"   = "list_images"
  }
}

resource "aws_apigatewayv2_integration" "lambda" {
  for_each = toset(values(local.api_routes))

  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.this[each.key].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "this" {
  for_each = local.api_routes

  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.lambda[each.value].id}"
}

resource "aws_lambda_permission" "api" {
  for_each = toset(values(local.api_routes))

  statement_id  = "AllowApiGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
