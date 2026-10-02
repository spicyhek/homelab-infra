data "aws_caller_identity" "current" {}

locals {
  metric_namespace = "Homelab/Reliability"
  backup_enabled   = var.backup_bucket != null && var.backup_bucket != ""
  metrics = merge(
    {
      cluster = {
        name                = "ClusterHealthy"
        period              = 60
        evaluation_periods  = 3
        datapoints_to_alarm = 3
      }
    },
    {
      site = {
        name                = "SiteHealthy"
        period              = 60
        evaluation_periods  = 2
        datapoints_to_alarm = 2
      }
      origin = {
        name                = "OriginHealthy"
        period              = 60
        evaluation_periods  = 2
        datapoints_to_alarm = 2
      }
      nids = {
        name                = "NidsHealthy"
        period              = 60
        evaluation_periods  = 2
        datapoints_to_alarm = 2
      }
    },
    local.backup_enabled ? {
      backup = {
        name                = "BackupHealthy"
        period              = 3600
        evaluation_periods  = 8
        datapoints_to_alarm = 2
      }
    } : {}
  )
  origin_status_url = coalesce(var.origin_status_url, "${trimsuffix(var.site_url, "/")}/api/status")
}

data "archive_file" "probe" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/probe"
  output_path = "${path.module}/.terraform/probe.zip"
}

data "archive_file" "backup" {
  count       = local.backup_enabled ? 1 : 0
  type        = "zip"
  source_dir  = "${path.module}/lambda/backup"
  output_path = "${path.module}/.terraform/backup.zip"
}

resource "aws_iam_role" "probe" {
  name = "homelab-reliability-probe"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}

resource "aws_cloudwatch_log_group" "probe" {
  name              = "/aws/lambda/homelab-reliability-probe"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_iam_role_policy" "probe" {
  role = aws_iam_role.probe.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.probe.arn}:*" },
    { Effect = "Allow", Action = "cloudwatch:PutMetricData", Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = local.metric_namespace } } }
  ] })
}

resource "aws_lambda_function" "probe" {
  function_name    = "homelab-reliability-probe"
  role             = aws_iam_role.probe.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = data.archive_file.probe.output_path
  source_code_hash = data.archive_file.probe.output_base64sha256
  environment { variables = {
    SITE_URL                    = var.site_url,
    SITE_MARKER                 = var.site_marker,
    ORIGIN_STATUS_URL           = local.origin_status_url,
    NIDS_HEALTH_URL             = var.nids_health_url,
    MAX_SNAPSHOT_AGE_SECONDS    = tostring(var.max_snapshot_age_seconds),
    METRIC_NAMESPACE            = local.metric_namespace
    EXPECTED_CLUSTER_NODES_JSON = jsonencode(var.expected_cluster_nodes)

  } }
  depends_on = [aws_cloudwatch_log_group.probe]
}

resource "aws_iam_role" "backup" {
  count = local.backup_enabled ? 1 : 0
  name  = "homelab-reliability-backup"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}

resource "aws_cloudwatch_log_group" "backup" {
  count             = local.backup_enabled ? 1 : 0
  name              = "/aws/lambda/homelab-reliability-backup"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_iam_role_policy" "backup" {
  count = local.backup_enabled ? 1 : 0
  role  = aws_iam_role.backup[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.backup[0].arn}:*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${var.backup_bucket}", Condition = { StringLike = { "s3:prefix" = [var.backup_prefix, "${var.backup_prefix}*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${var.backup_bucket}/${var.backup_prefix}*" },
    { Effect = "Allow", Action = "cloudwatch:PutMetricData", Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = local.metric_namespace } } }
  ] })
}

resource "aws_lambda_function" "backup" {
  count         = local.backup_enabled ? 1 : 0
  function_name = "homelab-reliability-backup"
  role          = aws_iam_role.backup[0].arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"
  timeout       = 60
  memory_size   = 256
  ephemeral_storage {
    size = var.backup_ephemeral_storage_mb
  }
  filename         = data.archive_file.backup[0].output_path
  source_code_hash = data.archive_file.backup[0].output_base64sha256
  environment { variables = {
    BACKUP_BUCKET          = coalesce(var.backup_bucket, ""), BACKUP_PREFIX = var.backup_prefix,
    BACKUP_MAX_AGE_SECONDS = tostring(var.backup_max_age_seconds), METRIC_NAMESPACE = local.metric_namespace
  } }
  depends_on = [aws_cloudwatch_log_group.backup]
}

resource "aws_sns_topic" "alerts" { name = "homelab-reliability-alerts" }

resource "aws_sns_topic_subscription" "email" {
  for_each  = var.alert_email_addresses
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

resource "aws_cloudwatch_event_rule" "probe" {
  name                = "homelab-reliability-probe-every-minute"
  schedule_expression = "rate(1 minute)"
}

resource "aws_cloudwatch_event_target" "probe" {
  rule      = aws_cloudwatch_event_rule.probe.name
  target_id = "reliability-probe"
  arn       = aws_lambda_function.probe.arn
}

resource "aws_lambda_permission" "probe_events" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.probe.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.probe.arn
}

resource "aws_cloudwatch_event_rule" "backup" {
  count               = local.backup_enabled ? 1 : 0
  name                = "homelab-reliability-backup-hourly"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "backup" {
  count     = local.backup_enabled ? 1 : 0
  rule      = aws_cloudwatch_event_rule.backup[0].name
  target_id = "reliability-backup"
  arn       = aws_lambda_function.backup[0].arn
}

resource "aws_lambda_permission" "backup_events" {
  count         = local.backup_enabled ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.backup[0].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.backup[0].arn
}

resource "aws_cloudwatch_metric_alarm" "health" {
  for_each            = local.metrics
  alarm_name          = "homelab-${each.value.name}"
  alarm_description   = "${each.value.name} has failed its health check for ${each.value.evaluation_periods} consecutive periods of ${each.value.period} seconds."
  namespace           = local.metric_namespace
  metric_name         = each.value.name
  statistic           = "Minimum"
  period              = each.value.period
  evaluation_periods  = each.value.evaluation_periods
  datapoints_to_alarm = each.value.datapoints_to_alarm
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
