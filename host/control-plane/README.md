# Control-plane network protection

These host services address the October 16, 20, and 22 Ethernet/address-loss
incidents. They run independently of Kubernetes and Argo CD. They target only
`homelab-control-plane`, `eth0`, `192.168.1.122`, and gateway `192.168.1.254`.
They work with today's DHCP configuration and a later static configuration.

## Behavior

`homelab-network-guard.timer` checks 30 seconds after boot and 30 seconds after
each completed check. A healthy check runs only `ip` and a wired gateway ping.

- A missing IPv4 address with carrier present must persist for 60 seconds before
  recovery. The guard records interface, route, NIC-counter, and networkd-log
  diagnostics, then requests `networkctl reconfigure eth0`.
- If the fault persists at the next check, it cycles only `eth0`, reconfigures it,
  and requests DHCP renewal. A failed DHCP renewal on a static-only setup is
  logged; subsequent checks still use the configured address and connectivity.
- A present address with failed wired gateway probes is recoverable only if a
  probe to the same gateway over `wlan0` succeeds. This distinguishes a stalled
  wired path from a gateway outage and works after moving to static addressing.
- At most one link cycle is permitted in ten minutes, even if the network briefly
  recovers between failures. The cooldown is saved before lowering the link.
- Missing physical carrier, an unexpected IPv4 address, or failure to reach the
  gateway over both interfaces is reported without cycling the interface.
- Inspection errors stop that check without attempting recovery. Each external
  command has a timeout, and systemd prevents concurrent service invocations.

Unresolved faults mark the oneshot service failed and appear in the journal;
the timer continues checking. Existing AWS reliability alarms provide external
outage notifications. This package does not add an email/SNS integration.
Watchdog state lives under `/run` and resets on reboot. No network configuration
files, Wi-Fi interfaces, worker services, or K3s services are changed by recovery.

The K3s configuration drop-in pins `node-ip` and `advertise-address` to the wired
IPv4 address and `flannel-iface` to `eth0`. A service drop-in waits up to 120 seconds
for that exact address, carrier, and gateway connectivity before each K3s start.
If the check fails, the existing `Restart=always` policy retries the start.
The guard timer can recover networking while K3s is waiting. A later link flap
does not stop or restart the running K3s service.

The cluster currently uses IPv4 Pod/Service networks; the pinned node address is
also IPv4. Revisit these settings before introducing a dual-stack cluster.

## K3s stall tolerance and disk sampling

etcd fsync stalls on the shared USB NVMe have outlasted the default 10-second
leader-election renew deadline; the embedded controller manager then loses its
lease and k3s exits (12 restarts by October 30). `92-leader-election.yaml` raises
the lease duration, renew deadline and retry period to 60/40/5 seconds for the
controller manager, scheduler and cloud controller manager, so stalls up to about
40 seconds no longer stop k3s. With one server, slower failover costs nothing.
This tolerates the stalls; it does not remove their cause (shared disk I/O). It
activates at the next k3s restart (`--restart-k3s`).

`sysstat-collect-1min.conf` overrides the packaged 10-minute sysstat timer to
sample every minute, so `sar -d` can show whether a disk stall preceded a k3s exit.
Remove the override to return to 10-minute sampling.

## Install

Review and merge changes through the normal repository PR workflow. Argo CD does
not install host files; run this installer on the control plane from a checkout
or a reviewed copy of this directory. Use Wi-Fi for the SSH session:

```powershell
ssh brendanmanley@192.168.1.104
```

From the copied directory or repository checkout:

```bash
sudo bash host/control-plane/install.sh --restart-k3s
```

For a standalone directory copy, use `sudo bash /path/to/control-plane/install.sh
--restart-k3s`. Sudo prompts for the host password. The installer checks hostname,
dependencies, current wired connectivity, and active K3s/networkd services. It
refuses to overwrite unrelated files at its managed paths. Existing managed
files are copied to a root-only `/var/tmp/homelab-network-guard-backup.*` directory
whose path is printed before installation. Re-running upgrades the managed files.

Installation enables the timer and runs one check, then `--restart-k3s` briefly
interrupts the Kubernetes API to activate address pinning and the startup gate.
Omit that flag to defer activation until the next K3s restart. A failed installer
is not automatically rolled back; it prints the backup path for inspection.
The installer never changes Netplan or the router's address allocation.

## Verify

```bash
systemctl status homelab-network-guard.timer --no-pager
sudo python3 /usr/local/libexec/homelab-network-guard.py check
systemctl show k3s -p ExecStartPre
sudo journalctl -u homelab-network-guard.service -n 50 --no-pager
KUBECONFIG=/home/brendanmanley/.kube/codex-readonly.yaml kubectl get nodes -o wide
```

All nodes should return to `Ready`, and AWS `ClusterHealthy` should return to `OK`.
In a planned maintenance window, disconnect/reconnect the cable and confirm the
address and workers recover without intervention. A short successful reconnect
need not invoke the watchdog. To exercise the 60-second recovery path, use an
isolated network namespace/test fixture rather than deleting the live address.
Do not simulate a fault on the production cluster during routine validation.

## Disable or roll back

Disable automatic recovery first:

```bash
sudo systemctl disable --now homelab-network-guard.timer
sudo systemctl stop homelab-network-guard.service
```

To remove the K3s startup gate and pinning installed for the first time, move
the exact managed files aside (retain them for recovery):

```bash
sudo mv /etc/systemd/system/k3s.service.d/20-network-guard.conf /etc/systemd/system/k3s.service.d/20-network-guard.conf.disabled
sudo mv /etc/rancher/k3s/config.yaml.d/91-network.yaml /etc/rancher/k3s/config.yaml.d/91-network.yaml.disabled
sudo systemctl daemon-reload
sudo systemctl restart k3s
```

For an upgrade rollback, restore the previous versions from the printed backup
directory to their matching absolute paths instead, then reload systemd and
restart K3s. Perform rollback while the wired address is present.

## Development

```bash
python3 -m unittest discover -s tests -p test_network_guard.py -v
bash -n host/control-plane/install.sh
```

Tests cover transient outages, recovery ordering, persistent failures, cooldowns
across brief recoveries, link-up attempts after exceptions, static-address NIC
failures, upstream outages, wrong addresses, and startup gating. A live `check`
is read-only; `recover` is reserved for the root systemd service.

References: [K3s configuration drop-ins](https://docs.k3s.io/installation/configuration),
[K3s network options](https://docs.k3s.io/cli/server),
[systemd boot-time network ordering](https://systemd.io/NETWORK_ONLINE/).
