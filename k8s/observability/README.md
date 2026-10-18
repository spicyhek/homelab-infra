# Node observability

This application runs one Prometheus node-exporter Pod on each Linux node and
one Grafana Alloy replica on the node labeled `observability=heavy`. Alloy
scrapes a small allowlist of CPU, memory, root filesystem, identity, and target
health metrics every 60 seconds and writes them to Grafana Cloud.

The deployment does not include an in-cluster Prometheus server, Grafana,
Alertmanager, persistent volume, or public endpoint.

## Verification

```bash
kubectl -n monitoring get daemonset node-exporter
kubectl -n monitoring get deployment alloy
kubectl -n monitoring get pods -o wide
kubectl -n monitoring logs deployment/alloy -c alloy --tail=100
```
