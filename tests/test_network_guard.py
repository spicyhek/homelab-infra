import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch


MODULE_PATH = Path(__file__).parents[1] / "host/control-plane/network_guard.py"
SPEC = importlib.util.spec_from_file_location("network_guard", MODULE_PATH)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


class NetworkInspectionTests(unittest.TestCase):
    def inspect(self, addresses, carrier="1", wired_ping=0, wifi_ping=0):
        response = Mock(returncode=0, stdout=json.dumps([
            {"ifname": "eth0", "addr_info": [{"local": ip} for ip in addresses]}
        ]))
        runner = Mock(side_effect=[response, Mock(returncode=wired_ping),
                                   Mock(returncode=wifi_ping)])
        with patch.object(Path, "read_text", return_value=carrier), \
                patch.object(guard, "run", runner):
            status = guard.inspect_network()
        return status, runner

    def test_healthy_host_uses_only_wired_gateway_probe(self):
        status, runner = self.inspect([guard.ADDRESS])
        self.assertEqual(status, "healthy")
        self.assertEqual(runner.call_count, 2)

    def test_missing_address_and_changed_address_are_distinct(self):
        self.assertEqual(self.inspect([])[0], "missing-address")
        self.assertEqual(self.inspect(["192.168.1.200"])[0], "wrong-address")

    def test_link_loss_does_not_send_gateway_probes(self):
        status, runner = self.inspect([guard.ADDRESS], carrier="0")
        self.assertEqual(status, "no-carrier")
        self.assertEqual(runner.call_count, 1)

    def test_wifi_distinguishes_stalled_wired_path_from_router_outage(self):
        self.assertEqual(self.inspect([guard.ADDRESS], wired_ping=1)[0],
                         "wired-unreachable")
        self.assertEqual(self.inspect([guard.ADDRESS], wired_ping=1, wifi_ping=1)[0],
                         "gateway-unreachable")

    def test_inspection_errors_fail_closed(self):
        with patch.object(Path, "read_text", return_value="1"), \
                patch.object(guard, "run", return_value=Mock(returncode=1, stderr="error")):
            with self.assertRaises(RuntimeError):
                guard.inspect_network()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_patch = patch.object(guard, "STATE_PATH", Path(self.temp.name) / "state.json")
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.action_patch = patch.object(guard, "action", return_value=True)
        self.actions = self.action_patch.start()
        self.addCleanup(self.action_patch.stop)
        diagnostics_patch = patch.object(guard, "diagnose")
        diagnostics_patch.start()
        self.addCleanup(diagnostics_patch.stop)

    def test_brief_failure_does_not_change_network(self):
        state = guard.recover({}, 100, "missing-address")
        guard.recover(state, 159, "missing-address")
        self.actions.assert_not_called()

    def test_reconfigure_precedes_cycle_by_one_tick(self):
        state = guard.recover({}, 100, "missing-address")
        state = guard.recover(state, 160, "missing-address")
        self.actions.assert_called_once_with("networkctl", "reconfigure", "eth0")
        self.actions.reset_mock()
        guard.recover(state, 190, "missing-address")
        self.assertEqual(self.actions.call_args_list, [
            call("ip", "link", "set", "dev", "eth0", "down"),
            call("ip", "link", "set", "dev", "eth0", "up"),
            call("networkctl", "reconfigure", "eth0"),
            call("networkctl", "renew", "eth0"),
        ])
        self.assertEqual(guard.load_state()["last_cycle"], 190)

    def test_reconfiguration_success_avoids_cycle(self):
        state = guard.recover({"status": "missing-address", "failed_since": 0},
                              60, "missing-address")
        state = guard.recover(state, 90, "healthy")
        self.assertEqual(state, {})
        self.assertEqual(self.actions.call_count, 1)

    def test_repeated_failure_is_limited_by_cooldown(self):
        state = {"status": "missing-address", "failed_since": 100, "last_cycle": 190}
        for now in (220, 400, 789):
            state = guard.recover(state, now, "missing-address")
        self.actions.assert_not_called()
        guard.recover(state, 790, "missing-address")
        self.actions.assert_called_once_with("networkctl", "reconfigure", "eth0")

    def test_short_recovery_does_not_reset_cycle_cooldown(self):
        state = guard.recover({"failed_since": 100, "last_cycle": 190}, 200, "healthy")
        state = guard.recover(state, 210, "missing-address")
        guard.recover(state, 500, "missing-address")
        self.actions.assert_not_called()

    def test_carrier_router_and_wrong_address_faults_do_not_cycle(self):
        for status in ("no-carrier", "gateway-unreachable", "wrong-address"):
            with self.subTest(status=status):
                state = guard.recover({}, 100, status)
                guard.recover(state, 1000, status)
        self.actions.assert_not_called()

    def test_isolated_wired_failure_uses_recovery_even_with_static_address(self):
        state = guard.recover({}, 100, "wired-unreachable")
        guard.recover(state, 160, "wired-unreachable")
        self.actions.assert_called_once_with("networkctl", "reconfigure", "eth0")

    def test_up_is_attempted_when_down_raises(self):
        self.actions.side_effect = [RuntimeError("interrupted"), True]
        state = {"status": "missing-address", "failed_since": 0, "reconfigured": True}
        with self.assertRaises(RuntimeError):
            guard.recover(state, 100, "missing-address")
        self.assertEqual(self.actions.call_args_list[-1],
                         call("ip", "link", "set", "dev", "eth0", "up"))
        self.assertEqual(guard.load_state()["last_cycle"], 100)

    def test_command_timeout_is_reported_as_failure(self):
        # Exercise the real action helper rather than the stub used by other tests.
        self.action_patch.stop()
        with patch.object(guard, "run", side_effect=subprocess.TimeoutExpired("ip", 10)):
            self.assertFalse(guard.action("ip", "link"))


class StartupTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "linux" and shutil.which("systemd-analyze"),
                         "requires Linux systemd validation tools")
    def test_systemd_accepts_timer_service_and_k3s_startup_dropin(self):
        directory = MODULE_PATH.parent
        with tempfile.TemporaryDirectory() as temporary:
            service = Path(temporary) / "k3s.service"
            service.write_text("[Service]\nType=simple\nExecStart=/usr/bin/true\nRestart=always\n")
            dropins = Path(temporary) / "k3s.service.d"
            dropins.mkdir()
            shutil.copyfile(directory / "k3s-network.conf", dropins / "20-network-guard.conf")
            result = subprocess.run([
                "systemd-analyze", "verify", "--man=no", str(service),
                str(directory / "homelab-network-guard.service"),
                str(directory / "homelab-network-guard.timer"),
            ], capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_startup_waits_for_wired_address(self):
        with patch.object(guard, "inspect_network", side_effect=["missing-address", "healthy"]), \
                patch.object(guard.time, "sleep") as sleep, \
                patch.object(guard.time, "monotonic", return_value=0):
            self.assertEqual(guard.wait_online(120), 0)
            sleep.assert_called_once_with(2)

    def test_startup_fails_if_required_network_does_not_arrive(self):
        with patch.object(guard, "inspect_network", return_value="missing-address"), \
                patch.object(guard.time, "monotonic", side_effect=[0, 120]):
            self.assertEqual(guard.wait_online(120), 1)


if __name__ == "__main__":
    unittest.main()
