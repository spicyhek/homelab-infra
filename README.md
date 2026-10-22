# Homelab infrastructure

This repo hosts my site, network intrusion detection service, and some supporting edge services for now. External reliability services exist in AWS and resource monitoring exists in Grafana Cloud. ArgoCD reconciles using this repo, so the head of this repo reflects the current state of the infrastructure.

## Current topology

The cluster currently has three nodes:

| Node                    | Role in the current manifests                                          |
| ----------------------- | ---------------------------------------------------------------------- |
| `homelab-control-plane` | K3s control plane. pinned host for NIDS and the privileged Zeek sensor |
| `homelab-worker1`       | Worker. runs ordinary replicated workloads and a Cloudflare Tunnel pod |
| `homelab-worker2`       | Worker. runs ordinary replicated workloads and a Cloudflare Tunnel pod |

- `mynids`: single-replica NIDS service with a persistent SQLite volume.
- `zeek`: privileged, host-network sensor on the control-plane node; forwards
  network-flow data to NIDS.
- `nids-backup`: hourly SQLite backup job using the NIDS persistent volume.
- `personal-site`: three-replica web workload spread across available nodes.
- `status-api`: three-replica cluster and endpoint status service.
- `cloudflared`: tunnel DaemonSet on nodes labeled `node-type=worker-lowmem`.

`main` is the production desired-state branch. Production changes are
made in Git and merged via pull request. Direct use of `kubectl apply` is
reserved for bootstrap, recovery, or troubleshooting.

## How changes are made to the infrastructure

1. Create a branch from `main` and make the change.
2. Open a pull request and review the rendered Kubernetes or Terraform change.
3. Merge the pull request after its required checks pass.
4. Argo CD detects Kubernetes changes on `main` and reconciles them.
5. For the Terraform reliability control plane, GitHub Actions creates a plan
   after merge and applies that saved plan through the `production` environment once approved.

## Design decisions and tradeoffs

This infrastructure is both a home for real services and a practical DevOps
and SRE lab. Many decisions were made to gain experience with Kubernetes,
GitOps, CI/CD, Terraform, cloud monitoring, and incident response while keeping
the services as available and healthy as the hardware and budget allow.

- **Resource-conscious observability:** Memory is one of the cluster's hardest
  limits: the two workers have only 1 GB each, and the control-plane node has
  8 GB while also running the NIDS and Zeek workloads. Memory pressure was a
  major reason for retiring the original in-cluster Prometheus, Grafana, Loki,
  and related monitoring stack. The replacement keeps only the lightweight
  Alloy and node-exporter collectors in the cluster and sends metrics to
  Grafana Cloud for storage and visualization.
- **GitOps instead of interactive administration:** Git is the operational
  interface because it is familiar, reviewable, and easier to audit than
  repeatedly SSHing into a node to make changes. Argo CD reconciles Kubernetes
  state from `main`, while GitHub Actions validates changes before they are
  merged. Direct changes on a node or routine use of `kubectl apply` are
  reserved for bootstrap, recovery, or troubleshooting.
- **Configuration safety:** Reliability of the original node was not very good,
  and early in the project an incorrect configuration could bring down the
  entire system. Pull-request checks now lint and render Kubernetes manifests,
  validate resources, and review Terraform plans before deployment. GitOps and
  CI have greatly reduced configuration-caused outages, although they cannot
  eliminate hardware and network failures.
- **Strict cloud-cost target:** The AWS reliability plane was designed to cost
  less than $1 per month. As of October 2026, it costs approximately $0.05 per
  month.
- **Known single-site failure domain:** All three cluster nodes connect to one
  switch, which in turn connects to one router. Failure of either network device
  can take the entire cluster offline regardless of workload replication. A
  second cluster in another geographic area would be the best way to remove
  this shared failure domain, but the additional hardware, networking, and
  operating costs currently exceed the project's budget.

## Infrastructure history

The Git history begins in March 2026. The original single-node phase predates
most of that history, so its hardware details come from operator recollection
and the retained [Docker Compose configuration](legacy/compose/docker-compose.yml).

| Date                                | Architecture and operational change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Before K3s (recorded March 7, 2026) | **Single-node Docker.** One Raspberry Pi with 8 GB of memory ran the site and its supporting services as Docker Compose containers. The retained configuration includes Nginx, Cloudflare Tunnel, Uptime Kuma, node-exporter, Prometheus, Grafana, Alertmanager, Blackbox Exporter, Loki, and Promtail.                                                                                                                                                                                                                                                                                                                                    |
| March 7-9, 2026                     | **Migration to K3s.** Kubernetes manifests were introduced for the site, tunnel, and monitoring stack. Two 1 GB worker nodes were added to the original Pi, producing the three-node cluster still used today. ([K3s setup](https://github.com/spicyhek/homelab-infra/commit/3435702), [three-node cluster](https://github.com/spicyhek/homelab-infra/commit/50e08f0))                                                                                                                                                                                                                                                                     |
| March 10-13, 2026                   | **Core cluster services came online.** The status API was added, while the NIDS service was expanded and paired with a host-networked Zeek sensor. The heavier stateful and packet-capture workloads were placed on the 8 GB control-plane node, leaving the low-memory workers for ordinary replicated workloads. ([status API](https://github.com/spicyhek/homelab-infra/commit/1c47f6c), [NIDS and Zeek](https://github.com/spicyhek/homelab-infra/commit/19d115a))                                                                                                                                                                     |
| March 15-17, 2026                   | **First deployment automation and GitOps.** Kustomize image overrides enabled application pipelines to commit immutable image tags, producing the first bot-authored deployment commits. Argo CD was then introduced to reconcile the NIDS manifests from `main` with automated pruning and self-healing; the personal-site deployment pipeline followed at the same time. ([CI/CD preparation](https://github.com/spicyhek/homelab-infra/commit/38ab47e), [initial Argo CD application](https://github.com/spicyhek/homelab-infra/commit/37ad30d), [automated site deployment](https://github.com/spicyhek/homelab-infra/commit/f0fb138)) |
| April 1, 2026                       | **Off-cluster NIDS backups.** A scheduled job began copying the NIDS SQLite database to Amazon S3, adding recovery outside the cluster. ([backup job](https://github.com/spicyhek/homelab-infra/commit/d61dee2))                                                                                                                                                                                                                                                                                                                                                                                                                           |
| September 2-8, 2026                 | **GitOps expanded to the whole cluster.** The personal site, Cloudflare Tunnel, status API, Zeek, and NIDS backup received Argo CD applications. Cloudflared became a DaemonSet on the low-memory workers, and an app-of-apps definition made Argo CD manage its own application catalog. ([personal site](https://github.com/spicyhek/homelab-infra/commit/30c2111), [Cloudflare Tunnel](https://github.com/spicyhek/homelab-infra/commit/d81f6c1), [app-of-apps](https://github.com/spicyhek/homelab-infra/commit/742ea60))                                                                                                              |
| September 9, 2026                   | **The original in-cluster monitoring stack was retired.** Prometheus, Grafana, Loki, Promtail, Blackbox Exporter, Alertmanager, and Uptime Kuma manifests were moved under `legacy/`. ([monitoring retirement](https://github.com/spicyhek/homelab-infra/commit/651bcd7))                                                                                                                                                                                                                                                                                                                                                                  |
| September 16-21, 2026               | **AWS became an independent reliability plane.** Backup IAM policy was added, Terraform was introduced with an S3 state bootstrap, and a reliability control plane was created with Lambda probes, CloudWatch alarms, and SNS email notifications. This allowed the cluster and its backups to be monitored from outside the cluster itself. ([Terraform bootstrap](https://github.com/spicyhek/homelab-infra/commit/f4b62be), [reliability control plane](https://github.com/spicyhek/homelab-infra/commit/430b689))                                                                                                                      |
| September 29-30, 2026               | **Repository-local CI/CD began for Terraform.** GitHub Actions used OIDC instead of long-lived AWS credentials, generated plans, stored them in S3, and placed production applies behind an approval gate. ([initial Terraform CI](https://github.com/spicyhek/homelab-infra/commit/4244786), [approval and saved plans](https://github.com/spicyhek/homelab-infra/commit/13e6305))                                                                                                                                                                                                                                                        |
| October 16-17, 2026                 | **Pull-request validation matured.** Kubernetes changes gained YAML linting plus Kustomize and Kubeconform validation. Terraform CI began publishing safe plan summaries and applying the exact reviewed plan from `main`; Renovate was also added for dependency updates. ([Kubernetes CI](https://github.com/spicyhek/homelab-infra/commit/29496e5), [CI hardening](https://github.com/spicyhek/homelab-infra/commit/f8ae599), [reviewed-plan apply](https://github.com/spicyhek/homelab-infra/commit/f9d3363))                                                                                                                          |
| October 18, 2026                    | **Observability returned in a lighter form.** Grafana Alloy and node-exporter were added through Argo CD, with telemetry sent to Grafana Cloud instead of running the full metrics and log storage stack on the small cluster. ([Grafana Cloud observability](https://github.com/spicyhek/homelab-infra/commit/b5f6c28))                                                                                                                                                                                                                                                                                                                   |

## Operational records

- [Control-plane Ethernet recovery and K3s startup protection](host/control-plane/README.md)
- [Postmortem: control-plane network failure and health-probe flapping](docs/postmortems/2026-10-16-control-plane-network-and-probe-flapping.md)
- [Postmortem: worker unavailability and control-plane Ethernet loss](docs/postmortems/2026-10-20-worker-unavailability-and-control-plane-ethernet-loss.md)
- [Reliability measurement and rolling five-day scorecards](docs/metrics/README.md)
- [Postmortem template](docs/postmortems/TEMPLATE.md)
