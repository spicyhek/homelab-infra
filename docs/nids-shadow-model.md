# nids shadow model rollout

The nids API can run a second, **shadow** classifier next to the operational model. It reads the
`zeek-conn-window-v2` features that the Zeek converter sends alongside the normal flows. Shadow predictions go to
their own `shadow_predictions` table and **never** change operational labels, counters, alerts or the dashboard. A
shadow failure never fails ingestion or readiness.

Current shadow model: `nids-v6-tree-baseline-v2`, `tree_model.joblib` sha256
`e0c5b9fd21a3c19880e53eb9694d6f475e6df5f1731318fd96c2b010b6ac6ba8`. It passed the mynids reserved final test on
2026-11-05 (all six gates; `mynids/data/evaluation/final-test-nids-v6/FINAL-REPORT.md`, a private local file).

## How it is wired

- `k8s/apps/nids/deploy.yml`: `NIDS_SHADOW_MODEL_DIR` and `NIDS_SHADOW_MODEL_SHA256`, plus a **read-only** hostPath
  mount of `/var/lib/mynids-models` at `/models`. The app unpickles `tree_model.joblib` only if it matches the
  pinned hash.
- `k8s/daemonsets/zeek/zeek.yml`: `NIDS_EXPORT_FEATURES_V2=true` on `zeek-converter`. If the server ever rejects
  the extra fields (HTTP 422), the batch is resent without them, so v1 flows are never lost.
- The bundle is staged by hand on `homelab-control-plane`, where the nids pod is pinned. It stays out of the public
  `ghcr.io/spicyhek/mynids` image, a ConfigMap (the bundle is ~1.8 MB, over the limit) and the database volume.
  The hostPath uses `type: Directory`: if the stage is missing, the new pod cannot start, and `maxUnavailable: 0`
  keeps the old pod serving.

## 1. Preflight (control plane)

```bash
ssh brendanmanley@192.168.1.104   # Wi-Fi, per host/control-plane/README.md
# Database size: shadow rows roughly double per-flow storage under the same 28-day retention.
# The storage dir is root 0700, so the glob must expand under sudo, not in your shell.
sudo sh -c 'du -sh /var/lib/rancher/k3s/storage/*_default_nids-data'
df -h /
# DNS must work and both images should already be cached (no pull needed); see the 2026-10-29 postmortem.
getent hosts ghcr.io
sudo k3s crictl images | grep -E 'spicyhek/mynids|zeek/zeek'
```

`local-path` does not enforce the PVC's 2Gi request; the node's free space is the real limit. The nids SQLite
backup CronJob is currently suspended (#42), so shadow rows are not backed up either.

## 2. Stage the bundle (before merging)

From the desktop (PowerShell):

```powershell
scp Z:\GithubProjects\mynids\data\models\nids-v6-tree-baseline-v2\tree_model.joblib Z:\GithubProjects\mynids\data\models\nids-v6-tree-baseline-v2\feature_order.json Z:\GithubProjects\mynids\data\models\nids-v6-tree-baseline-v2\label_map.json Z:\GithubProjects\mynids\data\models\nids-v6-tree-baseline-v2\feature_contract.json brendanmanley@192.168.1.104:/tmp/
```

On the control plane:

```bash
sudo install -d -o root -g root -m 0755 /var/lib/mynids-models/nids-v6-tree-baseline-v2
cd /tmp && sudo install -o root -g root -m 0644 tree_model.joblib feature_order.json label_map.json feature_contract.json /var/lib/mynids-models/nids-v6-tree-baseline-v2/
sha256sum /var/lib/mynids-models/nids-v6-tree-baseline-v2/*
rm /tmp/tree_model.joblib /tmp/feature_order.json /tmp/label_map.json /tmp/feature_contract.json
```

Expected hashes:

```
903a23ab41e746a237dc34793ee02de6eb3e138d2db9a551860c7b337c929432  feature_contract.json
a042091f9a60579975e409333b6fd31275eb4c81ef543845c3980b6121a2e8c3  feature_order.json
a592dc474e64d88943112547a030cee7bfa22f41c3bde3523704b49a9a3c2ed5  label_map.json
e0c5b9fd21a3c19880e53eb9694d6f475e6df5f1731318fd96c2b010b6ac6ba8  tree_model.joblib
```

Never overwrite a staged version in place; stage a new version directory and change the env values in Git.

## 3. Merge

Argo CD syncs two applications independently:
- `mynids` rolls the nids pod. The old pod serves until the new one is ready.
- `zeek` restarts the Zeek DaemonSet pod. **This causes a short capture gap.** The converter resumes from its state
  file, and its v2 windows are cold for the first ~60 s.

## 4. Verify

```bash
export KUBECONFIG=/home/brendanmanley/.kube/codex-readonly.yaml
kubectl rollout status deploy/nids --timeout=5m
kubectl rollout status ds/zeek --timeout=5m
kubectl logs deploy/nids --since=10m | grep -i shadow      # no "unavailable" errors expected
kubectl logs ds/zeek -c zeek-converter --since=10m | tail  # no repeated "rejected features_v2"
```

- `/healthz` must not list "Shadow model is configured but unavailable." Operational `status` stays `ok` either way.
- After traffic flows, `GET /api/internal/shadow/summary?hours=1` (ingest token required) should show
  `shadow_available: true`, the `configured_model_id` `nids-v6-tree-baseline-v2:e0c5b9fd21a3`, rising `classified`
  counts, and an agreement matrix against production.

## What to watch during shadow

- **Online vs offline parity.** Training and the final test ran Zeek offline with the image's default `local`
  scripts plus the VXLAN policy. The live sensor loads only `json-logs` plus the same VXLAN redefs.
  - A parity check on 2026-11-07 compared 9.3 h of live conn.log against an offline replay of the same capture
    (`mynids/data/evaluation/analyses/20261103T004336Z-24f3f36a/parity-v1/PARITY.md`, a private local file). It
    found **no difference from the script set** and 0 shadow label flips in 49k flows.
  - It did find that the default 9216-byte `Pcap::snaplen` truncated this host's offloaded apiserver responses
    (up to ~17 KB). That logged false content gaps (`missed_bytes`, history `g`) on ~1.2 % of live connections,
    which offline replays never have. `local.zeek` now sets `Pcap::snaplen = 262144`.
  - Re-run the parity check on a capture taken after that change. Only the inherent live-vs-offline differences
    should remain: sparse multicast UDP split differently, and ±1 window counts from write order.
- Shadow alert rate vs the final test's 0/h on homelab benign, and disagreements with the production model.
- Database growth and disk I/O on the shared USB NVMe (see the control-plane disk notes).

## Roll back

Revert the PR. Argo removes the env vars, the mount and the converter flag. The staged bundle can stay; it is
inert without `NIDS_SHADOW_MODEL_DIR`. Shadow rows age out with normal retention.
