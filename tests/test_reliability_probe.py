import datetime
import importlib.util
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


MODULE_PATH = (
    Path(__file__).parents[1]
    / "terraform"
    / "reliability-control-plane"
    / "lambda"
    / "probe"
    / "handler.py"
)


class FakeCloudWatch:
    def __init__(self):
        self.calls = []
        self.timestamps = []
        self.fail_metric = None

    def put_metric_data(self, Namespace, MetricData):
        metric = MetricData[0]
        if metric["MetricName"] == self.fail_metric:
            raise RuntimeError("simulated publication failure")
        self.calls.append((Namespace, metric["MetricName"], metric["Value"]))
        self.timestamps.append(metric["Timestamp"])


FAKE_CLOUDWATCH = FakeCloudWatch()
FAKE_BOTO3 = types.ModuleType("boto3")
FAKE_BOTO3.client = lambda _service: FAKE_CLOUDWATCH
sys.modules["boto3"] = FAKE_BOTO3

SPEC = importlib.util.spec_from_file_location("reliability_probe", MODULE_PATH)
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


class ReliabilityProbeTests(unittest.TestCase):
    def setUp(self):
        FAKE_CLOUDWATCH.calls.clear()
        FAKE_CLOUDWATCH.timestamps.clear()
        FAKE_CLOUDWATCH.fail_metric = None
        self.environment = mock.patch.dict(os.environ, {"METRIC_NAMESPACE": "Homelab/Reliability"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_checks_publish_independent_metrics_and_execution_health(self):
        checks = (
            ("site", lambda: True),
            ("origin", lambda: True),
            ("nids", lambda: False),
            ("cluster", lambda: True),
        )

        with mock.patch.object(probe, "CHECKS", checks):
            result = probe.lambda_handler({}, None)

        published = {name: value for _namespace, name, value in FAKE_CLOUDWATCH.calls}
        self.assertEqual(published["SiteHealthy"], 1)
        self.assertEqual(published["OriginHealthy"], 1)
        self.assertEqual(published["NidsHealthy"], 0)
        self.assertEqual(published["ClusterHealthy"], 1)
        self.assertEqual(published["ProbeExecutionHealthy"], 1)
        self.assertTrue(result["probe_execution"])

    def test_mybuilds_check_needs_a_2xx_from_healthz(self):
        with mock.patch.dict(os.environ, {"MYBUILDS_HEALTH_URL": "https://mybuilds.example/healthz"}):
            with mock.patch.object(probe, "fetch", return_value=(200, b"ok\n")) as fetch:
                self.assertTrue(probe.check_mybuilds())
                fetch.assert_called_once_with("https://mybuilds.example/healthz")
            with mock.patch.object(probe, "fetch", return_value=(503, b"database unavailable\n")):
                self.assertFalse(probe.check_mybuilds())

    def test_metric_publication_failure_marks_probe_execution_unhealthy(self):
        checks = tuple((key, lambda: True) for key in probe.METRICS)
        FAKE_CLOUDWATCH.fail_metric = "NidsHealthy"

        with mock.patch.object(probe, "CHECKS", checks):
            with redirect_stdout(io.StringIO()):
                result = probe.lambda_handler({}, None)

        published = {name: value for _namespace, name, value in FAKE_CLOUDWATCH.calls}
        self.assertEqual(published["ProbeExecutionHealthy"], 0)
        self.assertFalse(result["probe_execution"])

    def test_every_datapoint_is_stamped_with_the_scheduled_minute(self):
        checks = tuple((key, lambda: True) for key in probe.METRICS)

        with mock.patch.object(probe, "CHECKS", checks):
            probe.lambda_handler({"time": "2026-11-23T18:05:58Z"}, None)

        expected = datetime.datetime(2026, 11, 23, 18, 5, tzinfo=datetime.timezone.utc)
        self.assertEqual(len(FAKE_CLOUDWATCH.timestamps), len(probe.METRICS) + 1)
        self.assertEqual(set(FAKE_CLOUDWATCH.timestamps), {expected})

    def test_run_minute_falls_back_to_now_without_a_scheduled_time(self):
        before = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
        for event in ({}, {"time": "not a time"}, {"time": "2026-11-23T18:05:00"}, None):
            minute = probe.run_minute(event)
            self.assertEqual((minute.second, minute.microsecond), (0, 0))
            self.assertGreaterEqual(minute, before)
            self.assertEqual(minute.tzinfo, datetime.timezone.utc)

    def test_run_minute_converts_offsets_to_utc(self):
        self.assertEqual(
            probe.run_minute({"time": "2026-11-23T10:05:30-08:00"}),
            datetime.datetime(2026, 11, 23, 18, 5, tzinfo=datetime.timezone.utc),
        )

    def fetch_with(self, outcomes):
        """Run probe.fetch where each attempt returns or raises the next outcome."""
        attempts = iter(outcomes)

        def fetch_once(_url):
            outcome = next(attempts)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with mock.patch.object(probe, "fetch_once", side_effect=fetch_once) as fetch_once_mock:
            with mock.patch.object(probe.time, "sleep") as sleep:
                with redirect_stdout(io.StringIO()):
                    try:
                        return probe.fetch("https://example.test/"), fetch_once_mock.call_count, sleep
                    except Exception as error:
                        return error, fetch_once_mock.call_count, sleep

    def http_error(self, code):
        return probe.urllib.error.HTTPError("https://example.test/", code, "error", {}, None)

    def test_fetch_retries_once_after_a_timeout(self):
        handshake = probe.urllib.error.URLError(TimeoutError("_ssl.c:994: The handshake operation timed out"))
        for first_failure in (handshake, TimeoutError("The read operation timed out"),
                              probe.http.client.RemoteDisconnected("closed")):
            result, attempts, sleep = self.fetch_with([first_failure, (200, b"ok")])
            self.assertEqual(result, (200, b"ok"))
            self.assertEqual(attempts, 2)
            sleep.assert_called_once_with(probe.RETRY_DELAY_SECONDS)

    def test_fetch_retries_a_5xx_but_not_a_4xx(self):
        result, attempts, _sleep = self.fetch_with([self.http_error(502), (200, b"ok")])
        self.assertEqual((result, attempts), ((200, b"ok"), 2))

        result, attempts, sleep = self.fetch_with([self.http_error(404), (200, b"ok")])
        self.assertIsInstance(result, probe.urllib.error.HTTPError)
        self.assertEqual(attempts, 1)
        sleep.assert_not_called()

    def test_fetch_gives_up_after_two_attempts(self):
        result, attempts, _sleep = self.fetch_with([TimeoutError("first"), TimeoutError("second")])
        self.assertIsInstance(result, TimeoutError)
        self.assertEqual(str(result), "second")
        self.assertEqual(attempts, probe.FETCH_ATTEMPTS)

    def test_worst_case_check_fits_inside_the_lambda_timeout(self):
        worst_case = (probe.FETCH_ATTEMPTS * probe.FETCH_TIMEOUT_SECONDS
                      + (probe.FETCH_ATTEMPTS - 1) * probe.RETRY_DELAY_SECONDS)
        self.assertLess(worst_case, 30)


if __name__ == "__main__":
    unittest.main()
