import importlib.util
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "reliability_scorecard.py"
SPEC = importlib.util.spec_from_file_location("reliability_scorecard", MODULE_PATH)
scorecard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scorecard
SPEC.loader.exec_module(scorecard)


class ReliabilityScorecardTests(unittest.TestCase):
    def test_rolling_window_uses_five_complete_local_days(self):
        window = scorecard.rolling_window("2026-10-19", "America/Los_Angeles")

        self.assertEqual(window.start, datetime(2026, 10, 14, 7, tzinfo=UTC))
        self.assertEqual(window.end, datetime(2026, 10, 19, 7, tzinfo=UTC))
        self.assertEqual(window.expected_minutes, 7_200)

    def test_rolling_window_accounts_for_daylight_saving_transition(self):
        window = scorecard.rolling_window("2026-11-04", "America/Los_Angeles")

        self.assertEqual(window.start, datetime(2026, 10, 30, 7, tzinfo=UTC))
        self.assertEqual(window.end, datetime(2026, 11, 4, 8, tzinfo=UTC))
        self.assertEqual(window.expected_minutes, 7_260)

    def test_rolling_window_defaults_to_today_as_exclusive_local_end(self):
        window = scorecard.rolling_window(
            None,
            "America/Los_Angeles",
            now=datetime(2026, 10, 19, 10, tzinfo=UTC),
        )

        self.assertEqual(window.start, datetime(2026, 10, 14, 7, tzinfo=UTC))
        self.assertEqual(window.end, datetime(2026, 10, 19, 7, tzinfo=UTC))

    def test_cloudwatch_availability_counts_missing_samples_as_unavailable(self):
        window = scorecard.Window(
            label="Test",
            start=datetime(2026, 10, 1, tzinfo=UTC),
            end=datetime(2026, 10, 1, 1, tzinfo=UTC),
            timezone="UTC",
        )

        def runner(_command):
            return {"Datapoints": [{"Sum": 54, "SampleCount": 57}]}

        results = scorecard.collect_availability(
            window,
            "Homelab/Reliability",
            "us-west-1",
            runner=runner,
        )

        self.assertEqual(len(results), 5)
        self.assertEqual(results[0].healthy_samples, 54)
        self.assertEqual(results[0].reported_samples, 57)
        self.assertEqual(results[0].expected_samples, 60)
        self.assertEqual(results[0].availability_pct, 90)
        self.assertEqual(results[0].coverage_pct, 95)

    def test_incident_parser_calculates_mttd_mtta_and_mttr(self):
        window = scorecard.rolling_window("2026-10-20", "America/Los_Angeles")
        text = """# Example incident

- **Start:** 2026-10-15T15:00:00-07:00
- **Detected:** 2026-10-15T15:03:00-07:00
- **Investigation started:** 2026-10-15T15:08:00-07:00
- **Recovered:** 2026-10-15T15:30:00-07:00
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incident.md"
            path.write_text(text, encoding="utf-8")
            incidents = scorecard.collect_incidents(window, Path(directory))

        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].mttd_minutes, 3)
        self.assertEqual(incidents[0].mtta_minutes, 5)
        self.assertEqual(incidents[0].mttr_minutes, 30)

    def test_repository_postmortems_have_parseable_timestamps(self):
        window = scorecard.rolling_window("2026-10-20", "America/Los_Angeles")
        postmortems = Path(__file__).parents[1] / "docs" / "postmortems"

        incidents = scorecard.collect_incidents(window, postmortems)

        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].mttr_minutes, 1_526)

    def test_delivery_collection_counts_promotions_and_workflow_results(self):
        window = scorecard.rolling_window("2026-10-20", "America/Los_Angeles")

        def requester(url, _token):
            if "/commits?" in url:
                return [
                    {"commit": {"message": "Deploy mynids sha-abc123 (#8)"}},
                    {"commit": {"message": "Deploy personal-site sha-def456 (#7)"}},
                ]
            return {
                "workflow_runs": [
                    {
                        "status": "completed",
                        "conclusion": "success",
                        "run_started_at": "2026-10-01T00:00:00Z",
                        "updated_at": "2026-10-01T00:10:00Z",
                    }
                ]
            }

        results = scorecard.collect_delivery(
            window,
            "spicyhek",
            "homelab-infra",
            None,
            requester=requester,
        )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result.promotions == 1 for result in results))
        self.assertTrue(all(result.workflow_success_pct == 100 for result in results))
        self.assertTrue(all(result.median_workflow_minutes == 10 for result in results))


if __name__ == "__main__":
    unittest.main()
