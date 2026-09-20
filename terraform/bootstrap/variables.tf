variable "aws_region" {
  description = "AWS region that stores the Terraform state bucket."
  type        = string
  default     = "us-west-1"
}

variable "state_bucket_prefix" {
  description = "Globally unique S3 bucket names are generated from this prefix."
  type        = string
  default     = "homelab-reliability-tfstate-"
}

variable "aws_account_id" {
  type    = string
  default = "062700375181"
}
