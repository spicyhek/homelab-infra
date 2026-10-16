# Homelab infrastructure

It hosts my site and my network intrusion detection service.

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
made in Git and merged through via pull request. No using `kubectl apply` normally.

## How changes are made to the infrastructure

1. Create a branch from `main` and make the change.
2. Open a pull request and review the rendered Kubernetes or Terraform change.
3. Merge the pull request after its required checks pass.
4. Argo CD detects Kubernetes changes on `main` and reconciles them.
5. For the Terraform reliability control plane, GitHub Actions creates a plan
   after merge and applies that saved plan through the `production` environment once approved.
