output "aws_account_id" {
  description = "AWS account Terraform is authenticated to"
  value       = data.aws_caller_identity.current.account_id
}
