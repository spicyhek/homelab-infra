variable "aws_region" {
  type    = string
  default = "us-west-1"
}

variable "aws_account_id" {
  type    = string
  default = "062700375181"
}

variable "expected_cluster_nodes" {
  description = "Kubernetes node names that must exist and report Ready"
  type        = set(string)
  default = [
    "homelab-control-plane",
    "homelab-worker1",
    "homelab-worker2",
  ]
}

variable "site_url" {
  type    = string
  default = "https://brendanmanley.com"
}
variable "site_marker" {
  description = "Stable text required in the homepage response"
  type        = string
  sensitive   = true
}
variable "origin_status_url" {
  description = "Origin freshness snapshot endpoint"
  type        = string
  default     = null
  nullable    = true
}
variable "nids_health_url" {
  description = "NIDS JSON health endpoint"
  type        = string
}
variable "max_snapshot_age_seconds" {
  type    = number
  default = 300
}
variable "backup_bucket" {
  description = "S3 bucket containing SQLite backups; null disables validation."
  type        = string
  default     = null
  nullable    = true
}
variable "backup_prefix" {
  type    = string
  default = ""
}
variable "backup_max_age_seconds" {
  type    = number
  default = 7200
}
variable "backup_ephemeral_storage_mb" {
  description = "Temporary storage for downloading and validating a backup in Lambda"
  type        = number
  default     = 2048
}
variable "alert_email_addresses" {
  type    = set(string)
  default = []
}
variable "lambda_log_retention_days" {
  type    = number
  default = 14
}
