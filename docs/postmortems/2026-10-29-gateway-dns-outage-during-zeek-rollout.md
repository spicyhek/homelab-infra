# Gateway DNS outage blocked the Zeek rollout

- **Start:** 2026-10-29T20:01:38-07:00
- **Detected:** 2026-10-29T20:04:25-07:00
- **Investigation started:** 2026-10-29T20:08:54-07:00
- **Recovered:** 2026-10-29T20:12:04-07:00
- **Closed:** unknown
- **Status:** Resolved; gateway cause and DNS resilience remain open

All timeline times are PDT. The start is when the old Zeek Pod was killed, which
began the capture gap. The DNS outage itself began at an unknown time after the
last image pull known to succeed (about 19:43:20). Recovery uses the time the new Zeek
container started running. Investigation start is the first recorded
diagnostic. Detection uses the first failed image pull, the first observable
symptom; the operator then saw `ImagePullBackOff` in `kubectl get pods -A -o wide`.
Times marked "about" come from Kubernetes event ages, which are rounded.

## Summary

Argo CD rolled out the Zeek VXLAN 8472 change
([#40](https://github.com/spicyhek/homelab-infra/pull/40)). The DaemonSet deleted
the running Zeek Pod before starting its replacement (`maxUnavailable: 1`,
`maxSurge: 0`). The new Pod could not pull either image. Both pulls failed to
resolve the registry name: `lookup ghcr.io: Try again` and
`lookup registry-1.docker.io: Try again`.

The images were valid; the new converter tag had been confirmed on GHCR before
the rollout. The gateway, `192.168.1.254`, still answered ping, but its DNS
service had stopped responding. UDP queries timed out and TCP was refused on its
IPv4 address and both of its IPv6 DNS addresses. A workstation on the LAN saw the
same timeout from the gateway, so the outage was not specific to the cluster.
Public resolvers such as `1.1.1.1` still answered.

The control-plane node's resolver forwards only to the gateway's DNS servers, so
it had no fallback. The Zeek container also uses `imagePullPolicy: Always`, which
requires a registry lookup even though its digest-pinned image was already on the
node. The gateway's DNS recovered about ten minutes after the failure was
confirmed. The kubelet's next pull retry succeeded, and the Pod started without
any change to the cluster. The operator confirmed the gateway was not restarted;
its DNS recovered on its own.

## Impact

- Zeek captured nothing from the deletion of the old Pod at 20:01:38 until the
  new Zeek container started at 20:12:04: 10 minutes 26 seconds. Flows from that
  window are permanently missing from the NIDS. The converter checkpoint is on a
  host path, so already-logged records were not lost.
- The rollout of the VXLAN 8472 policy and the new converter was delayed by the
  same interval.
- Any other DNS lookup that depended on the gateway failed during the outage.
  The operator confirmed no effect on other public services, such as the site
  served through cloudflared.
- The `nids` Deployment was not affected. It had pulled its new image at about
  19:43, before the outage, and `/healthz` reported `ok` at 19:49:32.

## Timeline

- **About 19:43:20:** The `nids` Pod for
  [#39](https://github.com/spicyhek/homelab-infra/pull/39) started after pulling
  `ghcr.io/spicyhek/mynids:sha-bad602b…`. DNS was working.
- **19:57:23:** [#40](https://github.com/spicyhek/homelab-infra/pull/40) merged.
  The Zeek policy and the converter image changed together.
- **20:01:38:** Argo CD had applied DaemonSet generation 14. The old Zeek Pod,
  `zeek-xjghd`, was killed.
- **20:02:31:** The replacement Pod, `zeek-b4xft`, was created. At 20:02:37, `systemd-resolved` on the
  node switched to a degraded feature set for DNS server `2600:1700:38c0:a2e0::1`.
  Similar transitions appear earlier in the log, so this alone does not mark the
  start of the outage.
- **About 20:04:25:** The first recorded `ErrImagePull` for the Zeek image. Both
  images then entered `ImagePullBackOff` with DNS lookup errors.
- **20:08:54:** Read-only diagnostics on the control-plane node confirmed that
  `192.168.1.254` responded to ping but not to DNS, on all three DNS addresses.
  A direct query to `1.1.1.1` succeeded. A LAN workstation also timed out against
  the gateway.
- **20:11:45:** The converter container started from image digest
  `sha256:c4d5dfe0…`.
- **20:12:04:** The Zeek container started from the pinned digest. Zeek began
  capturing again.
- **20:12:56:** The gateway answered DNS queries again, and the node resolved
  `ghcr.io`.
- **About 20:15:** The converter acknowledged exactly the 245 non-carrier
  records Zeek had logged, confirming the rollout itself was working.

## Causes

1. The gateway's DNS service stopped responding for the whole LAN while the
   gateway itself stayed reachable. It recovered without a restart. Its cause is
   unknown; gateway logs were not collected.
2. The control-plane node's DNS configuration lists only the gateway's three
   addresses, so it had no independent resolver to fall back on.
3. `imagePullPolicy: Always` on the Zeek container required a registry lookup
   for an image already on the node. The converter used a new tag that had not
   been pulled yet, so it needed the registry either way.
4. The DaemonSet update strategy removes the old Pod before the new Pod has its
   images. On a single-node DaemonSet, any pull failure becomes a capture outage.

## Resolution

The gateway's DNS service recovered on its own; the gateway was not restarted.
The kubelet's next scheduled pull retry
succeeded for both images, and the Pod started with no change to Kubernetes
or the node.

## Follow-up actions

| Priority | Action | Owner | Status |
| --- | --- | --- | --- |
| P1 | Add an independent fallback resolver on the cluster nodes, so a gateway DNS failure does not block name resolution | Operator | Open |
| P1 | Collect the gateway's logs, if it keeps them, to find out why DNS stopped and recovered | Operator | Open |
| P2 | Use `imagePullPolicy: IfNotPresent` for digest-pinned and immutable `sha-` tagged images, so a cached image can start without a registry | Operator | Deferred |
| P2 | Pre-pull a new Zeek image on the node before rolling out, or otherwise prevent the old Pod being removed until the new images are present | Operator | Open |
| P2 | Alert on DNS resolution from the nodes and on Zeek not being ready, so a capture gap is detected without manual inspection | Operator | Open |
