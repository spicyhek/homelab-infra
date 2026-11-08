# nids models: v2 tree operational, Keras in shadow

The nids API runs two classifiers. `NIDS_OPERATIONAL_MODEL` decides whose labels are operational:

- **`v2` (current):** the `zeek-conn-window-v2` tree labels every flow with a warm, undegraded window (it reads the
  v2 features the Zeek converter sends alongside the normal flows). The built-in Keras model labels the rest: the
  ~60 s of cold windows after a converter restart, and flows without v2 features. Keras also runs in **shadow** on
  the flows v2 labeled, stored in `shadow_predictions`.
- **`keras` (the app default, and the rollback):** Keras labels everything; v2 runs in shadow.

If the v2 bundle cannot load or fails on a batch, every flow in it gets the Keras label, `/healthz` reports
`degraded` with "The operational v2 model is unavailable; flows are labeled by the Keras fallback model", and
`/readyz` stays ready, so ingestion never stops because of v2. Every stored flow records `model_id`, the model
whose label it carries.

Operational v2 model: `nids-v6-tree-baseline-v2`, `tree_model.joblib` sha256
`e0c5b9fd21a3c19880e53eb9694d6f475e6df5f1731318fd96c2b010b6ac6ba8`. It passed the mynids reserved final test on
2026-11-05 (all six gates; `mynids/data/evaluation/final-test-nids-v6/FINAL-REPORT.md`, a private local file), ran
in shadow from 2026-11-05, and passed the live parity re-check of 2026-11-07 apart from inherent window ties (see
below). It adds a `BRUTE_FORCE` class; Keras counts brute force under `OTHER_ATTACK`.

## How it is wired

- `k8s/apps/nids/deploy.yml`: `NIDS_OPERATIONAL_MODEL`, `NIDS_V2_MODEL_DIR` and `NIDS_V2_MODEL_SHA256` (the app
  still accepts the older `NIDS_SHADOW_MODEL_*` names), plus a **read-only** hostPath mount of
  `/var/lib/mynids-models` at `/models`. The app unpickles `tree_model.joblib` only if it matches the pinned hash.
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
  - The re-check on a 1 h capture after that change
    (`mynids/data/evaluation/analyses/20261107T075956Z-81fd7a5b/parity-v2/PARITY.md`, private) found **0 live-only
    content gaps** (v1: 581), 0 shadow label flips, and identical connection records apart from 2 that were open
    when the capture stopped. Its frozen verdict failed only the all-features criterion (98.7 % vs 99 %), from ±1–2
    window counts caused by live write order and sparse multicast timing, which are inherent to live processing.
- Shadow alert rate vs the final test's 0/h on homelab benign, and disagreements with the production model.
- Database growth and disk I/O on the shared USB NVMe (see the control-plane disk notes).

## Promotion to operational

The mynids promotion gates proposed at least a week of live shadow. **On 2026-11-08 the operator waived the rest of
that week** and promoted after about 3 days (from 2026-11-05 ~02:00Z). The shadow evidence at that point:
- 114,827 flows a day, with no shadow errors or restarts.
- 22 shadow alerts in 387,150 flows (16 BRUTE_FORCE, 6 DOS_DDOS), all explained and benign:
  - 20 came from three bursts of short SSH sessions from the operator's desktop to the control plane: 1 on 11-06,
    9 from a per-file `scp` copy on 11-07, and 10 from SSH copy tests on 11-08.
  - 2 were single aborted HTTP connections, one confirmed to a Tailscale DERP relay.
- Over the same period, Keras raised about 2,000 benign false positives a day.

The image must be `sha-4160e63…` or later (mynids #12), which already runs with Keras operational and records
`model_id`.

1. **Database backup: optional, and skipped for this promotion.** Switching modes rewrites no existing rows: only new
   flows get v2 labels, the schema change already happened in #12, and rolling back is one variable. A full copy, by
   contrast, reads and writes about 3 GB on the control-plane disk, which has stalled etcd under heavy reads. The
   hourly backup CronJob stays suspended (#42) for the same reason. If a copy is wanted anyway, the host has no
   `sqlite3` CLI, so use the pod's Python, whose online backup copies a consistent snapshot while the app keeps
   writing. `pages`/`sleep` throttle it to roughly 10 MB/s (about 5 min for 3 GB); `kubectl cp` is not throttled.
   Run it at a quiet time from the desktop (a kubeconfig with `exec` rights), PowerShell:

   ```powershell
   kubectl exec deploy/nids -- python -c "import sqlite3; s=sqlite3.connect('/data/nids.sqlite3'); d=sqlite3.connect('/tmp/nids-pre-v2.sqlite3'); s.backup(d, pages=256, sleep=0.1); d.close(); s.close(); print('backup ok')"
   $pod = kubectl get pod -l app=nids -o jsonpath='{.items[0].metadata.name}'
   kubectl cp "default/${pod}:/tmp/nids-pre-v2.sqlite3" .\nids-pre-v2.sqlite3
   kubectl exec deploy/nids -- rm /tmp/nids-pre-v2.sqlite3
   python -c "import sqlite3; print(sqlite3.connect('nids-pre-v2.sqlite3').execute('PRAGMA quick_check').fetchone())"
   ```

   Expect `('ok',)`. Keep the copy off the host; it holds 28 days of flow metadata.
2. **Merge.** Only the nids pod restarts (Zeek is untouched). The bundle is already staged; nothing to copy.
3. **Verify** (same kubeconfig as above):

   ```bash
   kubectl rollout status deploy/nids --timeout=5m
   kubectl logs deploy/nids --since=10m | grep -iE 'v2 model|shadow|traceback'   # nothing expected
   ```

   - `/healthz`: `status` `ok`, no "Keras fallback" warning.
   - The public dashboard shows `nids-v6-tree-baseline-v2` as the model and has a "Suspected brute force" card.
   - `GET /api/internal/shadow/summary?hours=1` (ingest token): `operational_model: v2`, `configured_model_id`
     `cicids2018_dense_model:…` (Keras is the shadow now), `shadow_available: true`, and
     `operational_label_sources` with `nids-v6-tree-baseline-v2:e0c5b9fd21a3` taking most flows.
4. **Watch for several days.**
   - Operational alerts should fall from Keras's ~90–105/h of benign false positives to near 0.
   - The Keras share of `operational_label_sources` is the fallback rate: it should be small and mostly right after
     restarts. A large or growing share means v2 is not judging flows; check `/healthz`.
   - In the shadow agreement matrix, rows are now v2 (production) and columns Keras (shadow). Review every flow
     that v2 labels as an attack, whatever Keras says.
   - **Known false positives in shadow** (2026-11-05 to 11-08, all benign):
     - BRUTE_FORCE on bursts of short SSH sessions from the operator's desktop, and DOS_DDOS when those sessions
       end with a TCP reset, as Windows `scp` often does. Per-file `scp` copies and quick test logins to the control
       plane did this: a handful of sessions within a minute was enough, and one burst raised 9 alerts in 6 s. The
       model sees connection counts, not login results, so a burst of real logins looks like a burst of guesses. `scp` opens one login per
       `host:` argument, so fetch several files with one login instead: `scp -r host:/dir .`, a quoted remote
       wildcard (`scp "host:/var/lib/zeek-logs/conn.2026-11-07-0*.log" .`), or
       `ssh host "tar cf - -C /dir a b" | tar xf -` from Git Bash. Windows OpenSSH cannot multiplex connections.
       (Tested 2026-11-08; the table is in mynids `training/CAPTURE_GUIDE.md`.)
     - DOS_DDOS on a single aborted HTTP connection to a Tailscale DERP relay (`derp*.tailscale.com`).

## Roll back

- **From operational v2 to shadow:** set `NIDS_OPERATIONAL_MODEL` to `keras` (or revert the promotion PR). Stored
  rows keep the `model_id` that labeled them, and v2 goes back to shadow.
- **Remove v2 entirely:** also remove `NIDS_V2_MODEL_DIR` / `NIDS_V2_MODEL_SHA256`, the mount and the converter
  flag. The staged bundle can stay; it is inert without the variables. Shadow rows age out with normal retention.
