# Gateway outage took the site offline and triggered the external alarms

- **Start:** 2026-11-03T10:05:45-08:00
- **Detected:** 2026-11-03T10:07:24-08:00
- **Investigation started:** 2026-11-03T15:50:10-08:00
- **Recovered:** 2026-11-03T10:11:36-08:00
- **Closed:** unknown
- **Status:** Resolved; gateway cause remains open

All timeline times are PST. The start is the control-plane journal entry where
Wi-Fi lost its connection and the routes through the gateway were removed. It is
the first recorded impact; the exact moment of the gateway's failure is not
recorded. Detection is the first CloudWatch alarm transition, `ClusterHealthy`
to `ALARM`. Investigation start is the first diagnostic command run for this
incident. Recovery is the last of the four alarms returning to `OK`; probes were
already succeeding in the 10:10 minute.

## Summary

The LAN gateway and Wi-Fi access point, `192.168.1.254`, stopped responding for
about three and a half minutes. Its internet connection was out for about five
minutes. Cluster nodes remained connected to the switch, and the cluster was not
affected internally. The public site, status API and NIDS health endpoint were
unreachable from the internet because Cloudflare Tunnel needs the gateway's
internet connection.

The AWS probes failed from the 10:05 minute through the 10:09 minute.
`ClusterHealthy`, `SiteHealthy`, `OriginHealthy` and `NidsHealthy` went to
`ALARM` between 10:07:24 and 10:08:36 and sent SNS emails. The gateway returned
on its own and every alarm was back to `OK` by 10:11:36. No change was made to
the cluster or the hosts.

This is the second gateway fault in five days. On
[October 29](2026-10-29-gateway-dns-outage-during-zeek-rollout.md) the gateway's
DNS failed while it still answered ping. This time the gateway stopped answering
entirely on both its wired and wireless interfaces.

## Impact

- The personal site, the origin status API and the NIDS health endpoint were
  unreachable from the internet. Each external probe failed in five consecutive
  one-minute datapoints, 10:05 through 10:09. Measured from the first recorded
  impact to the last alarm clearing, the outage lasted 5 minutes 51 seconds.
- `ClusterHealthy` reported unhealthy only because its probe reads node status
  through the public status API. No node changed its `Ready` condition: the
  control plane has been `Ready` since March 7 and both workers since October 22.
  No Pod restarted.
- The control-plane host lost DNS and Tailscale connectivity. Administration
  over Tailscale was unavailable from about 10:05 until 10:10.
- `ProbeExecutionHealthy` stayed `OK`. The Lambda probes ran normally and
  reported the failures.
- Not checked: how cloudflared reported its tunnel connections and whether the
  workers' journals recorded the outage.

## Timeline

- **10:05:00-10:05:59:** All four external health metrics recorded their first
  failed datapoint in this minute. The Lambda's exact run time within the minute
  is not recorded.
- **10:05:45:** On the control plane, `wpa_supplicant` disconnected from the
  gateway's access point `e0:22:03:50:d3:f6` (`reason=0 locally_generated=1`).
  systemd-networkd recorded `wlan0: Lost carrier` and the loss of its DHCP
  leases. Routes through `192.168.1.254` and the IPv6 default route were removed.
  `eth0` kept its carrier and `192.168.1.122`.
- **10:05:48:** Tailscale reported UDP blocked. DNS lookups through
  `127.0.0.53` then timed out.
- **10:06:04:** `homelab-network-guard` reported
  `wired network unhealthy: gateway-unreachable`: the gateway did not answer
  ping on either `eth0` or `wlan0`. As designed, it took no recovery action,
  because this does not indicate a local NIC fault. Each run from 10:06 to 10:08
  ended as a failed unit.
- **10:07:07:** Tailscale reported no network map from its coordination server
  for 2 minutes 9 seconds.
- **10:07:24:** `homelab-ClusterHealthy` went to `ALARM` after two failed
  datapoints. An SNS email was sent.
- **10:08:19-10:08:36:** `NidsHealthy`, `SiteHealthy` and `OriginHealthy` went
  to `ALARM` after three failed datapoints. SNS emails were sent.
- **10:09:11:** The gateway answered on `eth0` again; the guard reported
  `wired network recovered`.
- **10:09:31-10:09:32:** Wi-Fi reconnected to `e0:22:03:50:d3:ed` and received
  `192.168.1.104` from the gateway's DHCP server.
- **10:10:00-10:10:59:** All four external health metrics recorded healthy
  datapoints again.
- **10:10:26:** Tailscale reached its coordination server again.
- **10:11:19-10:11:36:** All four alarms returned to `OK`.
- **10:10-10:13:** Wi-Fi switched several times between the gateway's two radio
  addresses, `e0:22:03:50:d3:ed` and `e0:22:03:50:d3:f6`, and settled by 10:13.
  The wired path was unaffected.
- **15:50:10:** Investigation began with read-only diagnostics on the control
  plane, after the incident had resolved itself.

## Causes

1. **Triggering cause: the gateway stopped responding.** It stopped answering on
   both the wired LAN and Wi-Fi at the same time, while `eth0` kept its carrier.
   This places the fault in the gateway, not in the switch or the Pi. Its
   link-local address, `fe80::e222:3ff:fe50:d3ed`, matches the access point's
   address `e0:22:03:50:d3:ed`, which confirms that the router and the access
   point are the same device. *Inference:* the pattern fits a gateway reboot or
   restart, since the LAN, DHCP and Wi-Fi came back first, the internet about a
   minute later, and then the radios settled. The gateway's logs were not
   collected, so a reboot, a firmware update, a power interruption and an ISP
   fault have not been told apart.
2. **Contributing condition: a known single point of failure.** All public
   ingress goes through one gateway, as documented in the
   [README](../../README.md). Workload replication across three nodes cannot
   protect against its loss.

## Resolution

The gateway recovered on its own, and no action was needed. The cluster had
stayed healthy throughout, so cloudflared and the external probes recovered as
soon as the internet connection returned. The network guard correctly classed
the fault as `gateway-unreachable` and did not cycle `eth0`.

## Follow-up actions

| Priority | Action | Owner | Status |
| --- | --- | --- | --- |
| P1 | Check the gateway's event log and uptime for a reboot, firmware update, power loss or WAN drop at about 10:05 PST, and record the cause here | Operator | Open |
| P1 | Investigate `homelab-BackupHealthy`, which has been in `ALARM` since 2026-10-10. Found during this investigation; unrelated to this incident | Operator | Open |
| P2 | Based on the gateway's cause, either put the gateway and switch on a UPS, schedule firmware updates for a maintenance window, or report the fault to the ISP | Operator | Open |
| P2 | Track gateway faults (October 29 and November 3). If they keep happening, consider replacing the ISP gateway or running it in bridge mode behind a router you control | Operator | Deferred |

## Evidence and limits

The timeline is built from the control-plane journal (systemd-networkd,
wpa_supplicant, tailscaled and the `homelab-network-guard` service), CloudWatch
alarm history, the one-minute `Homelab/Reliability` metrics, the SNS alarm
emails, and current node and Pod status. Kubernetes events for the window had
expired before investigation, so node stability comes from each node's `Ready`
`lastTransitionTime` and from Pod restart counts. The gateway's logs, the
cloudflared logs and the worker journals were not examined. The incident-closure
time is unrecorded.
