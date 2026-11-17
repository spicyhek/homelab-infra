# mybuilds' off-cluster ingestion buffer. A
# Cloudflare Worker on the ingestion paths of mybuilds.brendanmanley.com puts
# each GitHub, CI and Argo CD delivery on this queue, and mybuilds drains it,
# so a restart of its one pod loses nothing.
#
# The CI apply role (homelab-terraform-apply, managed by hand) is scoped by
# name: it may manage SQS queues named mybuilds-ingest* and IAM users named
# mybuilds-ingest-*. Keep new names under those prefixes or widen the role
# first.

resource "aws_sqs_queue" "mybuilds_ingest_dlq" {
  name                      = "mybuilds-ingest-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "mybuilds_ingest" {
  name                       = "mybuilds-ingest"
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = 60
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.mybuilds_ingest_dlq.arn
    maxReceiveCount     = 5
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "mybuilds_ingest_dlq" {
  queue_url = aws_sqs_queue.mybuilds_ingest_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.mybuilds_ingest.arn]
  })
}
resource "aws_iam_user" "mybuilds_ingest_worker" {
  name = "mybuilds-ingest-worker"
}

resource "aws_iam_user_policy" "mybuilds_ingest_worker" {
  name = "send"
  user = aws_iam_user.mybuilds_ingest_worker.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.mybuilds_ingest.arn }
  ] })
}

resource "aws_iam_user" "mybuilds_ingest_consumer" {
  name = "mybuilds-ingest-consumer"
}

resource "aws_iam_user_policy" "mybuilds_ingest_consumer" {
  name = "receive"
  user = aws_iam_user.mybuilds_ingest_consumer.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    {
      Effect   = "Allow",
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"],
      Resource = [aws_sqs_queue.mybuilds_ingest.arn, aws_sqs_queue.mybuilds_ingest_dlq.arn]
    }
  ] })
}

output "mybuilds_ingest_queue_url" {
  value = aws_sqs_queue.mybuilds_ingest.url
}

output "mybuilds_ingest_dlq_url" {
  value = aws_sqs_queue.mybuilds_ingest_dlq.url
}
