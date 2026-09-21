output "aws_account_id" {
  description = "AWS account Terraform is authenticated to"
  value       = data.aws_caller_identity.current.account_id
}

output "alarm_arns" {
  value = { for name, alarm in aws_cloudwatch_metric_alarm.health : name => alarm.arn }
}

output "alert_topic_arn" {
  value = aws_sns_topic.alerts.arn
}
