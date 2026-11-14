output "site_url" {
  description = "Frontend, images and API all live behind this URL."
  value       = "https://${aws_cloudfront_distribution.site.domain_name}"
}

output "cloudfront_domain" {
  value = aws_cloudfront_distribution.site.domain_name
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.site.id
}

output "api_endpoint" {
  description = "Direct API Gateway endpoint (the site uses /api on CloudFront instead)."
  value       = aws_apigatewayv2_api.http.api_endpoint
}

output "source_bucket" {
  value = aws_s3_bucket.this["source"].id
}

output "processed_bucket" {
  value = aws_s3_bucket.this["processed"].id
}

output "frontend_bucket" {
  value = aws_s3_bucket.this["frontend"].id
}

output "table_name" {
  value = aws_dynamodb_table.images.name
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.pipeline.arn
}

output "queue_url" {
  value = aws_sqs_queue.uploads.id
}

output "dlq_url" {
  value = aws_sqs_queue.uploads_dlq.id
}

output "region" {
  value = var.aws_region
}
