# Node observability

This application runs one Prometheus node-exporter Pod on each Linux node and
one Grafana Alloy replica and one kube-state-metrics replica on the node labeled
`observability=heavy`. Alloy scrapes a small allowlist every 60 seconds and writes
it to Grafana Cloud:

- node-exporter: CPU, memory, root filesystem, identity, and scrape health.
- kube-state-metrics: node Ready, MemoryPressure, DiskPressure, PIDPressure,
  NetworkUnavailable, the unschedulable flag, and scrape health.

kube-state-metrics watches only nodes, with cluster-scoped `list` and `watch`
permissions. Its metric allowlist contains only `kube_node_status_condition` and
`kube_node_spec_unschedulable`. Alloy scrapes the internal ClusterIP service at
`kube-state-metrics.monitoring.svc.cluster.local:8080`, labels the samples with
`cluster="homelab"` and `job="integrations/kube-state-metrics"`, and forwards only
the conditions listed above. `node` identifies the node whose state is reported;
`instance="kube-state-metrics"` identifies the exporter, not that node.

The deployment does not include an in-cluster Prometheus server, Grafana,
Alertmanager, persistent volume, or public endpoint.

## Suggested Grafana Cloud alerts

Node not Ready, pending for five minutes (preserves the `node` label):

```promql
max by (cluster, node) (
  kube_node_status_condition{cluster="homelab",condition="Ready",status="true"}
) == bool 0
```

Fewer than three reporting nodes, pending for five minutes. This catches a node
that disappears entirely from the Kubernetes API; it is separate from NotReady:

```promql
(
  count(max by (node) (
    kube_node_status_condition{cluster="homelab",condition="Ready",status="true"}
  )) or vector(0)
) < bool 3
```

Exporter scrape failure or missing telemetry, pending for two minutes:

```promql
(
  max(last_over_time(
    up{cluster="homelab",job="integrations/kube-state-metrics"}[2m]
  )) or vector(0)
) == bool 0
```

Active pressure or unavailable networking, pending for five minutes:

```promql
max by (cluster, node, condition) (
  kube_node_status_condition{
    cluster="homelab",
    condition=~"MemoryPressure|DiskPressure|PIDPressure|NetworkUnavailable",
    status="true"
  }
) > bool 0
```

Keep AWS `ClusterHealthy`, `OriginHealthy`, and `ProbeExecutionHealthy` as the
external reliability signals. Grafana metrics support node-level diagnosis;
collection depends on the cluster and its control plane. Existing AWS alarms
continue to cover failures of the external status reporting path.
