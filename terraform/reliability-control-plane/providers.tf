provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "homelab-reliability-control-plane"
      ManagedBy = "terraform"
    }
  }
}
