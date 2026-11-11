# mybuilds

Delivery-tracking app for the cluster. The manifests and Argo CD Application
arrive with the first mybuilds image. This directory currently holds the AWS
setup that has to exist first.

## Litestream replica

A Litestream sidecar streams the SQLite database to
`s3://homelab-backups-062700375181-us-west-1-an/mybuilds/`. An init container
restores from there when the volume is empty.

- **IAM user:** `homelab-mybuilds-litestream`, with only
  [`litestream-iam-policy.json`](litestream-iam-policy.json): list, read, write
  and delete under `mybuilds/`, plus `s3:GetBucketLocation`. It has no access to
  `sqlite-backups/` or any other prefix.
- **Bucket:** private, AES256 default encryption, versioning enabled.
  Litestream deletes old WAL segments and snapshots continuously, so the
  `mybuilds/` lifecycle rule in
  [`../../backups/bucket-lifecycle.json`](../../backups/bucket-lifecycle.json)
  expires replaced versions after 7 days and aborts multipart uploads left
  incomplete for a day. Without it, every deleted object would be kept forever.
- **Kubernetes Secret:** `mybuilds-litestream-aws` in `default`, with
  `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. It is created by hand and is
  not stored in Git.

The CI Terraform apply role cannot manage IAM users or bucket lifecycle, so
these are applied by hand, like the NIDS backup user. They move into Terraform
in M4 together with the SQS users.

### Setup

Run with an admin AWS profile. `put-bucket-lifecycle-configuration` replaces
the bucket's whole lifecycle configuration, so the file also carries the
existing `backups/` rule. Compare it with
`aws s3api get-bucket-lifecycle-configuration` first.

```bash
aws iam create-user --user-name homelab-mybuilds-litestream \
  --tags Key=Project,Value=mybuilds Key=ManagedBy,Value=manual

aws iam put-user-policy --user-name homelab-mybuilds-litestream \
  --policy-name MybuildsLitestreamReplica \
  --policy-document file://k8s/apps/mybuilds/litestream-iam-policy.json

aws s3api put-bucket-lifecycle-configuration \
  --bucket homelab-backups-062700375181-us-west-1-an \
  --lifecycle-configuration file://k8s/backups/bucket-lifecycle.json
```

Create the access key and the Secret in one step, so the key is never printed.
This needs `kubectl` with write access:

```bash
aws iam create-access-key --user-name homelab-mybuilds-litestream \
  --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text |
  { read -r id secret
    kubectl -n default create secret generic mybuilds-litestream-aws \
      --from-literal=AWS_ACCESS_KEY_ID="$id" \
      --from-literal=AWS_SECRET_ACCESS_KEY="$secret"; }
```

## Placement

Pin the pod and its local-path volume to `homelab-worker2`, which boots from a
USB SSD. `homelab-worker1` runs on an SD card, and the control plane's disk
already holds the k3s datastore, NIDS and Zeek. On 2026-11-10 each worker had
about 380 MiB available, with cloudflared, a personal-site replica and a
status-api replica running. The planned budget is 96 MiB requested and 256 MiB
limit across the app and Litestream containers.

Record a week of disk I/O before deploying; see the queries in
[`../../observability/README.md`](../../observability/README.md#disk-io).
