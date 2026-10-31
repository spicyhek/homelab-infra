#!/usr/bin/env bash
# Managed by homelab-infra: host/control-plane
set -euo pipefail

if [[ "${EUID}" != 0 ]]; then
  echo "Run this installer with sudo on homelab-control-plane." >&2
  exit 1
fi
if [[ "$(hostname -s)" != homelab-control-plane ]]; then
  echo "This configuration is only for homelab-control-plane." >&2
  exit 1
fi
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != --restart-k3s ) ]]; then
  echo "Usage: sudo bash install.sh [--restart-k3s]" >&2
  exit 1
fi

source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for tool in python3 ip ping networkctl systemctl systemd-analyze install; do
  command -v "${tool}" >/dev/null
done
systemctl is-active --quiet systemd-networkd
systemctl is-active --quiet k3s
python3 "${source_dir}/network_guard.py" check

sources=(
  network_guard.py
  homelab-network-guard.service
  homelab-network-guard.timer
  k3s-network.conf
  91-network.yaml
  92-leader-election.yaml
  sysstat-collect-1min.conf
)
targets=(
  /usr/local/libexec/homelab-network-guard.py
  /etc/systemd/system/homelab-network-guard.service
  /etc/systemd/system/homelab-network-guard.timer
  /etc/systemd/system/k3s.service.d/20-network-guard.conf
  /etc/rancher/k3s/config.yaml.d/91-network.yaml
  /etc/rancher/k3s/config.yaml.d/92-leader-election.yaml
  /etc/systemd/system/sysstat-collect.timer.d/10-every-minute.conf
)

# Refuse to overwrite an unrelated administrator-owned file at a managed path.
for target in "${targets[@]}"; do
  if [[ -e "${target}" ]] && ! grep -q 'Managed by homelab-infra: host/control-plane' "${target}"; then
    echo "Refusing to overwrite unmanaged file: ${target}" >&2
    exit 1
  fi
done

backup_dir="$(mktemp -d /var/tmp/homelab-network-guard-backup.XXXXXXXX)"
chmod 0700 "${backup_dir}"
printf 'Backing up existing managed files to %s\n' "${backup_dir}"
trap 'echo "Installation failed; review changes and backups at ${backup_dir}" >&2' ERR
for index in "${!targets[@]}"; do
  target="${targets[index]}"
  if [[ -e "${target}" ]]; then
    cp -a --parents "${target}" "${backup_dir}/"
  fi
  install -D -o root -g root -m 0644 "${source_dir}/${sources[index]}" "${target}"
done

systemd-analyze verify /etc/systemd/system/homelab-network-guard.service \
  /etc/systemd/system/homelab-network-guard.timer /etc/systemd/system/k3s.service \
  /usr/lib/systemd/system/sysstat-collect.timer
systemctl daemon-reload
systemctl restart sysstat-collect.timer
systemctl enable --now homelab-network-guard.timer
systemctl start homelab-network-guard.service
printf 'Installed network guard. Previous managed files, if any: %s\n' "${backup_dir}"

if [[ "${1:-}" == --restart-k3s ]]; then
  # Use the Wi-Fi SSH address during installation. A K3s restart briefly
  # interrupts the API; it is required to activate the pinned addresses now.
  systemctl restart k3s
  systemctl is-active --quiet k3s
  echo 'K3s restarted with its wired address pinned and startup check enabled.'
else
  echo 'K3s startup check is installed; address pinning activates at its next restart.'
  echo 'To activate now: sudo systemctl restart k3s'
fi
