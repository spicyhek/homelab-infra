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
        self.fail_metric = None

    def put_metric_data(self, Namespace, MetricData):
        metric = MetricData[0]
        if metric["MetricName"] == self.fail_metric:
            raise RuntimeError("simulated publication failure")
        self.calls.append((Namespace, metric["MetricName"], metric["Value"]))


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

    def test_metric_publication_failure_marks_probe_execution_unhealthy(self):
        checks = tuple((key, lambda: True) for key in probe.METRICS)
        FAKE_CLOUDWATCH.fail_metric = "NidsHealthy"

        with mock.patch.object(probe, "CHECKS", checks):
            with redirect_stdout(io.StringIO()):
                result = probe.lambda_handler({}, None)

        published = {name: value for _namespace, name, value in FAKE_CLOUDWATCH.calls}
        self.assertEqual(published["ProbeExecutionHealthy"], 0)
        self.assertFalse(result["probe_execution"])


if __name__ == "__main__":
    unittest.main()
