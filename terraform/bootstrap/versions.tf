terraform {
  required_version = ">= 1.16.0, < 2.0.0"

  backend "s3" {
    bucket       = "homelab-reliability-tfstate-062700375181-us-west-1"
    key          = "bootstrap/terraform.tfstate"
    region       = "us-west-1"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
