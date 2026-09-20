# Terraform state bootstrap

This root module manages the private S3 bucket used for remote Terraform state.

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
