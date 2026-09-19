# Terraform state bootstrap

This root module manages the private S3 bucket used for remote Terraform state.
The bucket was initially created with local state, then this module's state was
migrated into the bucket under `bootstrap/terraform.tfstate`.

## Normal workflow

Authenticate to AWS and verify the target account before applying:

```powershell
aws sso login --profile homelab-admin
aws sts get-caller-identity --profile homelab-admin
$env:AWS_PROFILE = "homelab-admin"
```

Initialize, review, and apply changes from the repository root:

```powershell
terraform -chdir=terraform/bootstrap init
terraform -chdir=terraform/bootstrap fmt -check
terraform -chdir=terraform/bootstrap validate
terraform -chdir=terraform/bootstrap plan "-out=bootstrap.tfplan"
terraform -chdir=terraform/bootstrap apply bootstrap.tfplan
terraform -chdir=terraform/bootstrap output state_bucket_name
```

The bucket has versioning, server-side encryption, public-access blocking, a
TLS-only bucket policy, and Terraform deletion protection. Do not commit saved
plans or any emergency local state.

## Backend configuration

The S3 `backend` block uses a separate key for each root module:

```hcl
backend "s3" {
  bucket       = "homelab-reliability-tfstate-062700375181-us-west-1"
  key          = "bootstrap/terraform.tfstate"
  region       = "us-west-1"
  encrypt      = true
  use_lockfile = true
}
```

The reliability control plane uses the same bucket with the distinct key
`reliability-control-plane/terraform.tfstate`.

The backend bucket cannot bootstrap itself from an empty AWS account. If it is
ever lost, temporarily remove the backend block, recreate the bucket using
local state, restore the latest state object version if available, and then run
`terraform init -migrate-state` to restore normal operation.
