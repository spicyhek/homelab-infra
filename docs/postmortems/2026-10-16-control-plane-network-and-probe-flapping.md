# Control-plane network failure and health-probe flapping

- **Start:** 2026-10-15T15:05:00-07:00
- **Detected:** unknown
- **Investigation started:** unknown
- **Recovered:** 2026-10-16T16:31:00-07:00
- **Closed:** unknown
- **Status:** Resolved; preventive work remains

## Summary

The control-plane host lost its Ethernet carrier and DHCP address
`192.168.1.122`. Both workers lost access to the Kubernetes API, became
`NotReady`, and cluster DNS and workloads degraded.

After the address was restored, K3s started before DHCPv4 completed and chose
IPv6. Its existing etcd membership expected the IPv4 address, so the API did
not recover until K3s was restarted with `192.168.1.122` present.

The monitoring Lambda made the incident noisier: it runs four checks
sequentially with 10-second request timeouts but has a 30-second total timeout.
Slow checks prevented metrics from being emitted, and CloudWatch treated the
missing data as failures.

## Impact

- Both workers and cluster DNS lost control-plane connectivity.
- Site, status, and NIDS workloads were unavailable or lost redundancy.
- Kubernetes API access remained unavailable during the failed K3s restart.
- Probe timeouts, retries, and missing metrics caused alarm flapping.

## Timeline

- **Oct 15, 15:05:** Ethernet carrier and the control-plane DHCP lease were
  lost; both workers became `NotReady`.
- **Oct 16:** Probe invocations repeatedly timed out, causing missing metrics
  and noisy alarms.
- **About 16:21:** The host rebooted, but K3s selected IPv6 before DHCPv4
  restored `192.168.1.122`.
- **About 16:29:** K3s was restarted after IPv4 was available. The API, nodes,
  DNS, and workloads recovered.
- **16:31:** All four CloudWatch health alarms were `OK`.

## Causes

1. Loss of the control-plane Ethernet carrier and DHCP lease initiated the
   outage.
2. Automatic K3s address selection delayed recovery by choosing IPv6 instead
   of the IPv4 address stored in etcd membership.
3. Sequential checks, an insufficient Lambda timeout budget, and breaching
   treatment for missing metrics amplified the alarm noise.

## Resolution

The IPv4 address was restored and K3s was restarted after it was present. All
three nodes returned to `Ready`, critical workloads recovered, probe execution
returned to normal, and all alarms cleared.

## Follow-up actions

| Priority | Action | Status |
| --- | --- | --- |
| P0 | Pin the K3s node and advertise addresses to `192.168.1.122`. | Deployed October 22; Flannel also pinned to `eth0` |
| P0 | Make K3s wait for the required IPv4 address before starting. | Deployed October 22; pre-start check passed during K3s restart |
| P1 | Investigate the Ethernet carrier and DHCP lease loss. | Open |
| P1 | Run probe checks concurrently and emit each metric independently. | Deployed; verified in the live Lambda package October 22; AWS last-modified timestamp October 19 |
| P1 | Separate probe-execution failures from endpoint-health alarms. | Deployed; live package publishes `ProbeExecutionHealthy` and the dedicated alarm is present |
| P1 | Recover a missing wired address independently of Kubernetes. | Host watchdog deployed October 22; automatic fault recovery validation remains open |
| P2 | Add direct node-readiness and API-reachability alerting. | Partial: `ClusterHealthy` evaluates the external node snapshot; an independent API-reachability alert remains open |
| P2 | Investigate the unrelated Argo CD repo-server crash loop. | Running without restarts in the October 22 observation; original cause and corrective action unverified |
