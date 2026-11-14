resource "aws_sns_topic" "notifications" {
  name = "${local.name}-notifications"
}

# AWS sends a confirmation email; nothing is delivered until you click it.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notifications.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
