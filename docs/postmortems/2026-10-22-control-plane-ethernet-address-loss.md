# Repeated control-plane Ethernet and IPv4 address loss

- **Start:** 2026-10-22T11:19:04-07:00
- **Detected:** 2026-10-22T11:22:01-07:00
- **Investigation started:** 2026-10-22T12:14:00-07:00
- **Recovered:** 2026-10-22T12:26:01-07:00
- **Closed:** unknown
- **Status:** Resolved; physical cause and recovery validation remain open

All timeline times are PDT. Investigation began at 12:14, when the first message
in this incident's chat was sent, as confirmed by the operator. The recovery
timestamp uses the external alarm's return to `OK`; the wired IPv4 address was
restored earlier, at 12:23:55.

## Summary

The control-plane Ethernet interface lost carrier and its DHCP lease for
`192.168.1.122`. Carrier returned three seconds later, but the address did not.
Both workers lost access to their configured K3s API endpoint,
`192.168.1.122:6443`, stopped reporting node status, and became `NotReady`.
The control-plane host remained running and reachable over Wi-Fi at
`192.168.1.104`; public services on that node continued responding in the
available observations.

Restarting the workers did not restore their API connection. Cycling and
reconfiguring control-plane Ethernet restored its DHCP address, after which
the workers rejoined and `ClusterHealthy` cleared. This repeats the
control-plane network failure described in the
[October 16](2026-10-16-control-plane-network-and-probe-flapping.md) and
[October 20](2026-10-20-worker-unavailability-and-control-plane-ethernet-loss.md)
postmortems. It does not establish that the initial worker loss on
October 20 had the same cause.

After recovery, a host-level network watchdog and explicit K3s address/startup
configuration were installed. They were not present during the outage and did
not perform this incident's recovery. The cause of the carrier drop remains
unconfirmed, and static addressing and physical-layer investigation remain open.

## Impact

- Cluster degradation lasted approximately 67 minutes from the carrier loss
  through external recovery confirmation. `ClusterHealthy` was in `ALARM` for
  64 minutes, from 11:22:01 to 12:26:01.
- Both workers became `NotReady` at 11:19:50. Their site and status API Pods
  were marked for deletion; replacements remained `Pending` because the
  deployment topology-spread constraints could not be satisfied on the sole
  remaining ready node. This reduced service redundancy.
- The surviving status API returned a fresh response with `nodes: []` and a
  warning that the Kubernetes request timed out after 5,000 milliseconds.
  A fresh status response therefore did not mean cluster state was available.
- kube-state-metrics entered a crash loop while failing to reach
  `https://10.43.0.1:443/version`; metrics-server was also unready.
- `SiteHealthy`, `OriginHealthy`, `NidsHealthy`, and `ProbeExecutionHealthy`
  remained `OK`. Public endpoint observations succeeded during investigation.
  These sampled checks cannot exclude shorter interruptions between probes.

## Timeline

- **11:19:04:** Kernel and systemd-networkd logs recorded `eth0` carrier loss;
  networkd recorded loss of its DHCPv4 and DHCPv6 leases.
- **11:19:07:** `eth0` regained carrier at 1 Gbps/full duplex. IPv4 did not return.
- **11:19:50:** Both workers transitioned to `Ready=Unknown`, with
  `NodeStatusUnknown` and the message that the kubelet stopped posting status.
- **11:20-11:22:** The first recorded failed cluster metric was in the 11:20
  minute. Two failed one-minute datapoints triggered `ClusterHealthy` at
  11:22:01. The alarm was responding to reported failures, not only missing data.
- **12:14:** Investigation began with the first message in this incident's chat,
  as confirmed by the operator.
- **12:15-12:17:** Read-only diagnostics confirmed both workers were powered
  on with active agents. Their attempts to reach `192.168.1.122:6443` failed
  with `no route to host`; their ARP entries for that address were incomplete.
  The control plane had no Ethernet IPv4 address and used Wi-Fi instead.
- **By 12:21:56:** Both workers had been restarted and had about one minute of
  uptime. Their agents remained in `activating`, unable to connect to the API.
  The control plane had not rebooted and still lacked the wired address.
- **12:23:45-12:23:55:** Ethernet was cycled and reconfigured. It regained carrier
  at 12:23:48 and acquired `192.168.1.122/24` from `192.168.1.254` at 12:23:55.
- **12:25-12:26:** All three nodes were observed `Ready` at 12:25:44. A healthy
  cluster datapoint in the 12:25 minute cleared `ClusterHealthy` at 12:26:01.
- **13:12:42:** `homelab-network-guard.timer` was enabled and started. Its checks
  subsequently completed successfully without invoking recovery actions.
- **13:12:45-13:13:10:** K3s passed the new wired-network startup check and
  completed its planned restart at 13:13:10 with address/interface pinning.
- **By 13:16:** Read-only verification confirmed the timer, successful watchdog
  runs, loaded K3s drop-in, healthy wired connectivity, and all three nodes ready.

## Causes

1. **Confirmed trigger and recovery failure.** Ethernet carrier loss removed
   the control-plane DHCP address. After carrier returned, IPv4 remained absent
   until manual interface recovery. The reason for the carrier loss and failure
   to reacquire the address is still unknown.
2. **Dependency on the wired API address.** Both agents and the in-cluster API
   path depended on `192.168.1.122`. Wi-Fi preserved host administration and
   outbound connectivity, but did not provide that address to the workers.
3. **No automatic host recovery at incident time.** The earlier address-stability
   and startup follow-ups were still open, and no host-level network watchdog
   was installed. Worker restarts could not restore an absent server IP.
4. **NIC evidence is inconclusive.** The NIC reported 491,791,322 cumulative
   receive-resource errors and 122,061 overruns at 12:25:44, while its FCS error
   counter was zero. The resource/overrun counters were unchanged at 12:26:12
   while received packets increased. These observations warrant NIC/driver and
   receive-path investigation but do not prove a bad cable, switch port, or
   driver. The user reported that all nodes share a switch and the control plane
   uses a different cable; cable shape alone does not establish a fault.

## Resolution

The operator cycled and reconfigured only control-plane Ethernet while
administration remained available over Wi-Fi. DHCP restored `192.168.1.122`,
the agents reconnected, and cluster health recovered. Worker restarts alone had
not resolved the incident. No control-plane reboot was needed for recovery.

The subsequent [host protection](../../host/control-plane/README.md) adds a
network check every 30 seconds after each completed run. After a sustained
recoverable fault, it records diagnostics, attempts reconfiguration, and may
cycle only Ethernet, with a ten-minute cycle cooldown. It also distinguishes
a failed wired gateway path from a gateway outage using the Wi-Fi path.
Missing carrier or an unexpected address is reported without repeated cycling.
The watchdog does not restart K3s or change Netplan.

K3s now pins its node and advertise address to `192.168.1.122` and Flannel to
`eth0`. A pre-start check requires the wired address, carrier, and gateway
connectivity. The check passed during the installation restart. Seventeen
watchdog tests and systemd validation passed on the Pi; automatic recovery from
an injected or actual post-installation fault has not yet been demonstrated.
Warnings and failed checks are recorded locally; existing AWS alarms provide
external outage notifications, with no new dedicated network SNS alert added.

## Follow-up actions

| Priority | Action | Owner | Status |
| --- | --- | --- | --- |
| P0 | Replace only the control-plane Ethernet cable with a known-good spare on the same switch port, inspect connector seating, and observe before changing another variable. | Operator | Open |
| P0 | If the fault recurs, move the replacement cable to a different switch port; continue with Pi NIC, power, driver/kernel, and receive-path investigation if necessary. | Operator | Open |
| P0 | Configure a conflict-free static `192.168.1.122/24` address and coordinate the router's DHCP allocation. A reservation alone still depends on DHCP recovery. | Operator | Open |
| P0 | Pin K3s node/advertise addresses and the Flannel interface; gate startup on the required wired network. | Operator | Deployed October 22; startup check passed |
| P1 | Install host-level network recovery with sustained-failure checks, diagnostics, and a cycle cooldown. | Operator | Deployed October 22; healthy scheduled checks verified |
| P1 | Validate automatic recovery with isolated fault injection, then a planned physical link interruption; verify worker reconnection and external alarm recovery. | Unassigned | Open; unit tests and healthy startup validation are complete |
| P1 | Export Ethernet carrier/link changes, receive-error deltas, and address presence; add dedicated external notification for host-network faults. | Unassigned | Open; local guard logging and existing AWS cluster alarm are present |

## Evidence and limits

The timeline combines systemd-networkd/kernel journals, CloudWatch alarm history
and one-minute metrics, incident-time node conditions and worker agent logs, and
post-installation systemd state. Recovery log times were retrieved after service
was restored. The deployed Lambda package was also inspected: concurrent checks,
independent metric publication, and `ProbeExecutionHealthy` are deployed; AWS
reports its last update as October 19. No packet capture, cable certification,
switch-port error history, or confirmed power fault establishes the physical
cause. Investigation start is confirmed by the operator as 12:14 PDT. The
incident-closure timestamp is unrecorded.
