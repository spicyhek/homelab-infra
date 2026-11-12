# mybuilds

Delivery-tracking app for the cluster
([spicyhek/mybuilds](https://github.com/spicyhek/mybuilds)). One pod on
`homelab-worker2`: the app, a Litestream sidecar that streams its SQLite
database to S3, and an init container that restores from S3 when the volume
is empty.

| File | Purpose |
| --- | --- |
| [`deploy.yml`](deploy.yml) | PVC, Deployment (Recreate, one replica), Service |
| [`litestream.yml`](litestream.yml) | Litestream config, mounted through a generated ConfigMap |
| [`release-patch.yml`](release-patch.yml) | Release annotations and `RELEASE_ID`, rewritten by mybuilds' `bump-gitops` job |
| [`kustomization.yml`](kustomization.yml) | Image digest pin and the pieces above |
| [`../../argocd/applications/mybuilds.yml`](../../argocd/applications/mybuilds.yml) | Argo CD Application; **merging it deploys** |

## Before merging

`homelab-apps` syncs `k8s/argocd/applications` automatically, so merging the
Application is the deploy. Check each of these first:

1. **Disk baseline:** the planned week was waived on 2026-11-12. The "before"
   window is 2026-11-11 05:28 UTC to the deploy, about 27 hours, in which
   worker2's `sda` averaged 0.13% busy with 0.48 ms mean write latency.
2. **The image pin is the newest `main` build.** The first deploy pins
   `mybuilds/c5923c1/production/3` (SSE and the pipeline UI), set by hand
   with the same commands as `bump-gitops`. If `main` has moved on, re-pin
   (the digest is in the `Resolve digest` step of mybuilds' release run) or
   merge and then re-run `bump-gitops` on the latest `main` run.
3. **`INFRA_REPO_TOKEN` exists in the mybuilds repository**, the same kind of
   fine-grained token personal-site uses. Until the manifests are on `main`,
   mybuilds' `bump-gitops` job only warns; after that it needs the token on
   every push.
4. **`mybuilds-litestream-aws` exists** in `default` (below). Without it the
   pod stays in `CreateContainerConfigError`.
5. **The tunnel route exists** (below), or the app runs but is unreachable.

After the merge, every push to mybuilds' `main` deploys: `bump-gitops` opens
and squash-merges a `Deploy mybuilds sha-...` pull request here, and Argo CD
syncs it. The reliability scorecard counts these commits as mybuilds
promotions, next to its release workflow runs.

`mybuilds-ingest` can come later: each ingestion endpoint answers 503 until
its key is present.

## Secrets

Both are created by hand and never stored in Git.

- `mybuilds-litestream-aws`: S3 credentials, see [Litestream replica](#litestream-replica).
- `mybuilds-ingest`: one key per ingestion endpoint. Every key is optional.

| Key | Also configured in |
| --- | --- |
| `MYBUILDS_GITHUB_WEBHOOK_SECRET` | The webhook on each tracked repository (Workflow runs and Workflow jobs events) |
| `MYBUILDS_CI_EVENT_SECRET` | The `MYBUILDS_CI_EVENT_SECRET` Actions secret of each tracked repository |
| `MYBUILDS_ARGOCD_TOKEN` | `argocd-notifications-secret`, referenced by the mybuilds webhook service |

Generate the values into a file outside the repository, create the Secret
from it, then copy each value to the place in the table and delete the file:

```bash
umask 077
for k in MYBUILDS_GITHUB_WEBHOOK_SECRET MYBUILDS_CI_EVENT_SECRET MYBUILDS_ARGOCD_TOKEN; do
  echo "$k=$(openssl rand -hex 32)"
done > ~/mybuilds-ingest.env
kubectl -n default create secret generic mybuilds-ingest --from-env-file="$HOME/mybuilds-ingest.env"
```

Changing a key needs a pod restart (`kubectl -n default rollout restart
deployment/mybuilds`), since the values are read at startup. The payloads and
signatures are in mybuilds'
[`docs/ingestion.md`](https://github.com/spicyhek/mybuilds/blob/main/docs/ingestion.md).

## Tunnel route

The tunnel is remotely managed, so its routes live in the Cloudflare
dashboard, not here. Add a public hostname to the homelab tunnel:

- Hostname: `mybuilds.brendanmanley.com`
- Service: `HTTP` `mybuilds.default.svc.cluster.local:80`

Only Service port 80 is routed. `/metrics` is on port 9090 and stays
cluster-internal. The M4 Cloudflare Worker takes over the ingestion paths on
the same hostname without changing this route.

## After deploying

```bash
kubectl -n default rollout status deployment/mybuilds
kubectl -n default logs deployment/mybuilds -c litestream --tail=20
curl -sI https://mybuilds.brendanmanley.com/healthz | grep -i x-release-id
aws s3 ls s3://homelab-backups-062700375181-us-west-1-an/mybuilds/ --recursive | head
```

The release ID header should match `release-patch.yml`, and Litestream should
have written a snapshot under `mybuilds/` within a minute.

## Restore and rollback

- **Lost volume:** delete the PVC and the pod. The new volume is empty, so the
  init container restores the latest replica before the app starts.
- **Bad image:** revert the `Deploy mybuilds sha-...` commit, or push a fix
  to mybuilds' `main`, which promotes itself. The schema migrates forward only, so a rollback across
  a migration needs a restore from a point in time before it
  (`litestream restore -timestamp`).
- **Remove the app:** delete `../../argocd/applications/mybuilds.yml`. Argo
  CD prunes the workload; the PVC's local-path volume is deleted with it, and
  the S3 replica stays.

The security context is restricted: uid 65532 (the image's `nonroot`), no
capabilities, read-only root filesystem, no service account token. The
runtime watch in M2 adds a ServiceAccount with a read-only Role.

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
The baseline window is 2026-11-11 05:28 to 2026-11-18 05:28 UTC.

The containers request 96 MiB together (app 48 MiB, Litestream 48 MiB) and
are limited to 256 MiB (160 + 96). The restore init container runs before
either and uses the Litestream budget.

mybuilds' own metrics are not scraped yet. Alloy only keeps an allowlist of
series for Grafana Cloud, so adding them is a separate change.
