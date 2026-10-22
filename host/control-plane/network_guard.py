#!/usr/bin/env python3
# Managed by homelab-infra: host/control-plane
"""Recover a missing wired address without depending on Kubernetes or Wi-Fi."""

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import time


LOG = logging.getLogger("homelab-network-guard")
INTERFACE = "eth0"
ADDRESS = "192.168.1.122"
GATEWAY = "192.168.1.254"
FAILURE_SECONDS = 60
COOLDOWN_SECONDS = 600
STATE_PATH = Path("/run/homelab-network-guard/state.json")


def run(*command, timeout=5):
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False
    )


def inspect_network():
    """Fail closed on inspection errors: never cycle an interface we cannot inspect."""
    carrier = Path(f"/sys/class/net/{INTERFACE}/carrier").read_text().strip() == "1"
    result = run("ip", "-j", "-4", "address", "show", "dev", INTERFACE)
    if result.returncode:
        raise RuntimeError(f"cannot inspect {INTERFACE}: {result.stderr.strip()}")
    interfaces = json.loads(result.stdout)
    if len(interfaces) != 1 or interfaces[0].get("ifname") != INTERFACE:
        raise RuntimeError("unexpected interface inspection result")
    addresses = [entry["local"] for entry in interfaces[0].get("addr_info", [])]
    # A different IPv4 address may indicate a DHCP/configuration change. Alert,
    # but do not repeatedly bounce a working interface to force a preferred IP.
    if not carrier:
        return "no-carrier"
    if ADDRESS not in addresses:
        return "wrong-address" if addresses else "missing-address"
    gateway = run("ping", "-n", "-I", INTERFACE, "-c", "1", "-W", "2", GATEWAY)
    if gateway.returncode == 0:
        return "healthy"
    # A successful Wi-Fi probe to the same gateway isolates the wired path.
    # This also catches a stalled receive path after switching to a static IP.
    fallback = run("ping", "-n", "-I", "wlan0", "-c", "1", "-W", "2", GATEWAY)
    return "wired-unreachable" if fallback.returncode == 0 else "gateway-unreachable"


def diagnose():
    for command in (
        ("ip", "-4", "address", "show", "dev", INTERFACE),
        ("ip", "-4", "route", "show"),
        ("ethtool", "-S", INTERFACE),
        ("journalctl", "-u", "systemd-networkd", "--since", "5 minutes ago",
         "--no-pager", "-n", "30"),
    ):
        try:
            result = run(*command)
            LOG.warning("diagnostic %s: %s", " ".join(command),
                        (result.stdout + result.stderr).strip()[:5000])
        except (OSError, subprocess.TimeoutExpired) as error:
            LOG.warning("diagnostic unavailable: %s", error)


def action(*command):
    LOG.warning("recovery action: %s", " ".join(command))
    try:
        result = run(*command, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        LOG.error("recovery command failed: %s", error)
        return False
    if result.returncode:
        LOG.error("recovery command failed: %s", result.stderr.strip())
    return result.returncode == 0


def recover(state, now, status):
    """Advance one recovery stage per timer tick; return updated volatile state."""
    if status == "healthy":
        if "failed_since" in state:
            LOG.warning("wired network recovered")
        return {key: value for key, value in state.items() if key == "last_cycle"}

    if state.get("status") != status:
        LOG.warning("wired network unhealthy: %s", status)
        state = {key: value for key, value in state.items() if key == "last_cycle"}
        state.update(status=status, failed_since=now)

    # Carrier loss needs a physical link; a changed address needs operator review.
    # A router that stops answering on both interfaces is not a local NIC fault.
    if status not in ("missing-address", "wired-unreachable"):
        return state
    if now - state["failed_since"] < FAILURE_SECONDS:
        return state
    last_cycle = state.get("last_cycle")
    if last_cycle is not None and now - last_cycle < COOLDOWN_SECONDS:
        return state

    if not state.get("reconfigured"):
        diagnose()
        action("networkctl", "reconfigure", INTERFACE)
        state["reconfigured"] = True
        return state

    # Persist the cooldown BEFORE lowering the link, including if this process is
    # interrupted. Always attempt to raise it again, even if the down command fails.
    state["last_cycle"] = now
    state.pop("reconfigured", None)
    save_state(state)
    diagnose()
    try:
        action("ip", "link", "set", "dev", INTERFACE, "down")
    finally:
        action("ip", "link", "set", "dev", INTERFACE, "up")
    action("networkctl", "reconfigure", INTERFACE)
    action("networkctl", "renew", INTERFACE)
    return state


def load_state():
    try:
        state = json.loads(STATE_PATH.read_text())
    except FileNotFoundError:
        return {}
    # /run is cleared at boot, so these monotonic timestamps cannot outlive a boot.
    if not isinstance(state, dict):
        raise ValueError("invalid watchdog state")
    for key in ("failed_since", "last_cycle"):
        if key in state and not isinstance(state[key], (int, float)):
            raise ValueError(f"invalid watchdog timestamp: {key}")
    return state


def save_state(state):
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state) + "\n")
    temporary.replace(STATE_PATH)


def wait_online(timeout):
    deadline = time.monotonic() + timeout
    LOG.info("waiting for %s on %s and gateway reachability", ADDRESS, INTERFACE)
    while True:
        if inspect_network() == "healthy":
            LOG.info("wired network is ready")
            return 0
        if time.monotonic() >= deadline:
            LOG.error("wired network did not become ready within %ss", timeout)
            return 1
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "recover", "wait-online"))
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.mode == "check":
            status = inspect_network()
            print(json.dumps({"interface": INTERFACE, "address": ADDRESS, "status": status}))
            return 0 if status == "healthy" else 1
        if args.mode == "wait-online":
            return wait_online(args.timeout)
        if os.geteuid() != 0:
            raise PermissionError("recovery must run as root")
        # Only this systemd oneshot service writes state; systemd serializes runs.
        state = recover(load_state(), time.monotonic(), inspect_network())
        save_state(state)
        # An unresolved outage is visible as a failed unit, in addition to journal
        # warnings. The timer continues trying, subject to the recovery cooldown.
        return 0 if inspect_network() == "healthy" else 1
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        LOG.error("network guard failed without further recovery: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
