# Worker unavailability and control-plane Ethernet loss

- **Start:** 2026-10-20T11:41:35-07:00
- **Detected:** 2026-10-20T11:44:01-07:00
- **Investigation started:** 2026-10-20T16:37:16-07:00
- **Recovered:** 2026-10-20T18:44:01-07:00
- **Closed:** unknown
- **Status:** Resolved; preventive work remains

## Summary

Both workers stopped reporting to Kubernetes within minutes of each other. Their
retained journals end abruptly, consistent with the reported physical disturbance
and possible loss of power, but the exact initiating event could not be verified.
The control plane stayed up, as did its site, status API, NIDS, and Cloudflare
Tunnel Pods. An external probe observed one brief HTTP 502 across the public
site, status endpoint, and NIDS endpoint at about 11:42; subsequent probes
succeeded.

Recovery was delayed by a separate control-plane network failure at 16:43. Its
Ethernet link flapped and lost the DHCP lease for `192.168.1.122`. Wi-Fi was
already connected; when Ethernet's route disappeared, the host used Wi-Fi's
fallback route and remained reachable at `192.168.1.104`. Both workers' K3s
agents were configured to reach the API at `192.168.1.122:6443`.
The Ethernet carrier returned without an IPv4 lease. After the interface was
cycled at 18:41, DHCP restored `192.168.1.122` and the workers rejoined.

## Impact

- Both workers were `NotReady` for roughly seven hours. Their site and status
  API replicas were unavailable, and replacements remained `Pending` because
  the deployments require one replica per eligible node. A working replica of
  each service, plus NIDS and a Cloudflare Tunnel connector, remained on the
  control plane.
- The AWS reliability probe received HTTP 502 for the public site, status
  endpoint, and NIDS endpoint in one invocation at about 11:42. The adjacent
  one-minute probes succeeded; the corresponding service alarms did not enter
  `ALARM`. This sampling cannot rule out shorter failures between probes.
- The `ClusterHealthy` alarm was in `ALARM` from 11:44:01 to 18:44:01. The
  control-plane tunnel connector briefly reconnected when Ethernet failed at
  16:43 and again during recovery at 18:42, without another failed external
  service probe in the available samples.

## Timeline

- **11:41:** The control-plane K3s log recorded an abnormal closure of a worker
  connection at 11:41:35. The last retained worker2 journal entry was at
  11:41:04.
- **11:42:** Worker2 became `NotReady` at 11:42:17. The external probe received
  HTTP 502 from all three public endpoints. Worker1's last recorded node
  heartbeat was at 11:42:22.
- **11:43–11:44:** Worker1's retained journal ended at 11:43:14. The
  `ClusterHealthy` alarm entered `ALARM` at 11:44:01, and worker1 became
  `NotReady` at 11:44:17.
- **16:37:** Investigation began. Worker1 did not answer ARP or ping; worker2
  answered ping but refused SSH and kubelet connections.
- **16:43–16:45:** The control-plane Ethernet link lost carrier and its DHCP
  lease at 16:43:40, then flapped twice more. It regained carrier at 16:45:32
  without regaining `192.168.1.122`. The host continued using Wi-Fi.
- **By 17:03:** Both workers were reachable over SSH after rebooting (their
  uptimes were about 17 minutes). Their K3s agents remained in `activating`,
  repeatedly failing to connect to `192.168.1.122:6443`. Neither worker could
  ping that address.
- **18:41–18:44:** The control-plane Ethernet interface cycled at 18:41:49 and
  reacquired `192.168.1.122` at 18:41:58. Worker workloads resumed around
  18:42; the cluster-health alarm cleared at 18:44:01. All three nodes and
  three Cloudflare Tunnel Pods were subsequently `Ready`.

## Causes

1. **Initial worker loss, cause unconfirmed.** Both workers' journals stopped
   abruptly, without a recorded clean shutdown. No kernel OOM event was found
   in the retained logs around the failure. The reported physical disturbance
   and possible power loss fit the evidence, but there is no direct record of
   the power or cable state at that time. The control plane recorded no Ethernet
   link event during the initial worker failure.
2. **Confirmed recovery blocker.** The later control-plane Ethernet carrier
   loss removed its DHCP-assigned IPv4 address. Although carrier returned, the
   address did not return until the interface was cycled. The underlying reason
   for this failure to reacquire a lease remains unknown. The NIC reported many
   receive-resource errors, including after recovery; these counters do not
   establish a cable, switch, or driver cause by themselves.
3. **Fixed K3s endpoint.** Both agents used `192.168.1.122:6443`. Wi-Fi preserved
   control-plane connectivity through `192.168.1.104`, but could not satisfy
   that fixed endpoint. The [October 16 postmortem](2026-10-16-control-plane-network-and-probe-flapping.md)
   had already identified the need to pin K3s to the intended wired address and
   wait for it during startup.

## Resolution

The workers were restarted and became reachable, but remained outside the
cluster until the control-plane Ethernet interface was cycled and DHCP restored
`192.168.1.122`. Their agents then reconnected without a Kubernetes manifest
change. Kubernetes reported all three nodes `Ready`, and CloudWatch reported
`ClusterHealthy=OK`.

## Follow-up actions

| Priority | Action | Owner | Status |
| --- | --- | --- | --- |
| P0 | Secure the nodes, power supplies, and cables against movement; verify the affected power and network connections. | Unassigned | Open |
| P0 | Diagnose the control-plane Ethernet carrier flaps and failure to reacquire IPv4; test the cable and switch port, and track NIC receive-resource errors over time. | Unassigned | Open |
| P0 | Make `192.168.1.122` a reliable wired address with conflict-free host static configuration, and test recovery after a planned link interruption. A DHCP reservation alone still depends on lease acquisition. | Operator | Open; operator plans the addressing change |
| P0 | Complete the October 16 actions to pin K3s node/advertise addresses and gate server startup on the required wired IPv4 address. | Operator | Deployed October 22; Flannel pinned to `eth0`, and startup check passed |
| P1 | Recover a missing wired address independently of Kubernetes with diagnostics and a cooldown. | Operator | Host watchdog deployed October 22; healthy scheduled checks verified; fault recovery test remains open |
| P1 | Alert on control-plane Ethernet carrier/address loss and worker readiness; add network-interface health to the Grafana Cloud metrics allowlist. | Unassigned | Partial: local watchdog fault logging and external `ClusterHealthy` are present; interface metrics and dedicated external network notifications remain open |
| P1 | Make the control-plane Cloudflare Tunnel placement explicit in manifests and documentation so this surviving connector is retained intentionally. | Unassigned | Open |
