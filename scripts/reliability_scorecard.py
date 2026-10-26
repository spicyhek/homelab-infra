#!/usr/bin/env python3
"""Generate a rolling homelab reliability and delivery scorecard."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone as fixed_timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


METRICS = (
    ("Site", "SiteHealthy"),
    ("Origin snapshot", "OriginHealthy"),
    ("Cluster", "ClusterHealthy"),
    ("NIDS", "NidsHealthy"),
    ("Probe execution", "ProbeExecutionHealthy"),
)
APPLICATION_WORKFLOWS = {
    "mynids": "release.yml",
    "homelab-hosted-personal-site": "release.yml",
}
DEPLOYMENT_NAMES = {
    "mynids": "mynids",
    "homelab-hosted-personal-site": "personal-site",
}
MAX_CLOUDWATCH_DATAPOINTS = 1_440


@dataclass(frozen=True)
class Window:
    label: str
    start: datetime
    end: datetime
    timezone: str

    @property
    def expected_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class AvailabilityResult:
    label: str
    metric_name: str
    healthy_samples: int
    reported_samples: int
    expected_samples: int
    availability_pct: float
    coverage_pct: float


@dataclass(frozen=True)
class DeliveryResult:
    application: str
    promotions: int
    workflow_runs: int
    successful_runs: int
    workflow_success_pct: float | None
    median_workflow_minutes: float | None


@dataclass(frozen=True)
class IncidentResult:
    title: str
    path: str
    started_at: str
    detected_at: str | None
    investigation_started_at: str | None
    recovered_at: str | None
    mttd_minutes: float | None
    mtta_minutes: float | None
    mttr_minutes: float | None


def pacific_dst_boundaries(year: int) -> tuple[datetime, datetime]:
    """Return the US Pacific DST transition instants in UTC."""
    march_eighth = datetime(year, 3, 8, 2)
    second_sunday = march_eighth + timedelta(days=(6 - march_eighth.weekday()) % 7)
    november_first = datetime(year, 11, 1, 2)
    first_sunday = november_first + timedelta(days=(6 - november_first.weekday()) % 7)
    start_utc = (second_sunday + timedelta(hours=8)).replace(tzinfo=UTC)
    end_utc = (first_sunday + timedelta(hours=7)).replace(tzinfo=UTC)
    return start_utc, end_utc


def pacific_offset_for_utc(value: datetime) -> int:
    value = value.astimezone(UTC)
    start, end = pacific_dst_boundaries(value.year)
    return -7 if start <= value < end else -8


def pacific_offset_for_local(value: datetime) -> int:
    march_eighth = datetime(value.year, 3, 8, 2)
    start = march_eighth + timedelta(days=(6 - march_eighth.weekday()) % 7)
    november_first = datetime(value.year, 11, 1, 2)
    end = november_first + timedelta(days=(6 - november_first.weekday()) % 7)
    return -7 if start <= value < end else -8


def localize(value: datetime, timezone_name: str) -> datetime:
    try:
        return value.replace(tzinfo=ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        if timezone_name != "America/Los_Angeles":
            raise
        offset = pacific_offset_for_local(value)
        return value.replace(tzinfo=fixed_timezone(timedelta(hours=offset)))


def to_local(value: datetime, timezone_name: str) -> datetime:
    try:
        return value.astimezone(ZoneInfo(timezone_name))
    except ZoneInfoNotFoundError:
        if timezone_name != "America/Los_Angeles":
            raise
        offset = pacific_offset_for_utc(value)
        return value.astimezone(fixed_timezone(timedelta(hours=offset)))


def rolling_window(
    end_date: str | None,
    timezone: str,
    days: int = 7,
    now: datetime | None = None,
) -> Window:
    """Return complete local calendar days ending at an exclusive local date."""
    now = now or datetime.now(UTC)
    if days < 1:
        raise ValueError("days must be at least 1")

    if end_date:
        try:
            end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError as error:
            raise ValueError("end date must use YYYY-MM-DD format") from error
        if end_day.isoformat() != end_date:
            raise ValueError("end date must use YYYY-MM-DD format")
    else:
        end_day = to_local(now, timezone).date()

    start_day = end_day - timedelta(days=days)
    start_local = localize(datetime(start_day.year, start_day.month, start_day.day), timezone)
    end_local = localize(datetime(end_day.year, end_day.month, end_day.day), timezone)

    return Window(
        label=f"{days} days ending {end_day.isoformat()}",
        start=start_local.astimezone(UTC),
        end=end_local.astimezone(UTC),
        timezone=timezone,
    )


def cloudwatch_period(window: Window, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    age = now - window.start
    if age > timedelta(days=63):
        resolution = 3_600
    elif age > timedelta(days=15):
        resolution = 300
    else:
        resolution = 60

    minimum_period = math.ceil((window.end - window.start).total_seconds() / MAX_CLOUDWATCH_DATAPOINTS)
    return math.ceil(max(resolution, minimum_period) / resolution) * resolution


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_aws_json(command: list[str]) -> Any:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def collect_availability(
    window: Window,
    namespace: str,
    region: str,
    profile: str | None = None,
    runner: Callable[[list[str]], Any] = run_aws_json,
) -> list[AvailabilityResult]:
    period = cloudwatch_period(window)
    results = []

    for label, metric_name in METRICS:
        command = ["aws"]
        if profile:
            command.extend(["--profile", profile])
        command.extend(
            [
                "cloudwatch",
                "get-metric-statistics",
                "--region",
                region,
                "--namespace",
                namespace,
                "--metric-name",
                metric_name,
                "--start-time",
                iso_z(window.start),
                "--end-time",
                iso_z(window.end),
                "--period",
                str(period),
                "--statistics",
                "Sum",
                "SampleCount",
                "--output",
                "json",
            ]
        )
        response = runner(command)
        datapoints = response.get("Datapoints", [])
        healthy = round(sum(float(point.get("Sum", 0)) for point in datapoints))
        reported = round(sum(float(point.get("SampleCount", 0)) for point in datapoints))
        expected = window.expected_minutes
        results.append(
            AvailabilityResult(
                label=label,
                metric_name=metric_name,
                healthy_samples=healthy,
                reported_samples=reported,
                expected_samples=expected,
                availability_pct=100 * healthy / expected if expected else 0,
                coverage_pct=100 * reported / expected if expected else 0,
            )
        )

    return results


def github_json(url: str, token: str | None) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "homelab-reliability-scorecard/1.0",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if token and error.code in (403, 404):
            return github_json(url, None)
        raise


def paginated_github_items(
    url: str,
    key: str | None,
    token: str | None,
    requester: Callable[[str, str | None], Any] = github_json,
) -> list[dict[str, Any]]:
    items = []
    page = 1
    separator = "&" if "?" in url else "?"
    while True:
        payload = requester(f"{url}{separator}per_page=100&page={page}", token)
        batch = payload.get(key, []) if key else payload
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def parse_github_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect_promotions(
    window: Window,
    owner: str,
    infra_repository: str,
    token: str | None,
    requester: Callable[[str, str | None], Any] = github_json,
) -> dict[str, int]:
    query = urllib.parse.urlencode(
        {"since": iso_z(window.start), "until": iso_z(window.end - timedelta(seconds=1))}
    )
    url = f"https://api.github.com/repos/{owner}/{infra_repository}/commits?{query}"
    commits = paginated_github_items(url, None, token, requester)
    counts = {application: 0 for application in APPLICATION_WORKFLOWS}

    for commit in commits:
        subject = commit.get("commit", {}).get("message", "").splitlines()[0]
        for application, deployment_name in DEPLOYMENT_NAMES.items():
            if re.match(rf"^Deploy {re.escape(deployment_name)} sha-[0-9a-f]+", subject):
                counts[application] += 1
    return counts


def collect_delivery(
    window: Window,
    owner: str,
    infra_repository: str,
    token: str | None,
    requester: Callable[[str, str | None], Any] = github_json,
) -> list[DeliveryResult]:
    promotions = collect_promotions(window, owner, infra_repository, token, requester)
    created_range = f"{iso_z(window.start)}..{iso_z(window.end - timedelta(seconds=1))}"
    results = []

    for application, workflow in APPLICATION_WORKFLOWS.items():
        query = urllib.parse.urlencode({"branch": "main", "event": "push", "created": created_range})
        url = f"https://api.github.com/repos/{owner}/{application}/actions/workflows/{workflow}/runs?{query}"
        runs = paginated_github_items(url, "workflow_runs", token, requester)
        completed = [run for run in runs if run.get("status") == "completed"]
        successful = [run for run in completed if run.get("conclusion") == "success"]
        durations = []
        for run in completed:
            if run.get("run_started_at") and run.get("updated_at"):
                duration = parse_github_time(run["updated_at"]) - parse_github_time(run["run_started_at"])
                durations.append(duration.total_seconds() / 60)

        results.append(
            DeliveryResult(
                application=application,
                promotions=promotions[application],
                workflow_runs=len(completed),
                successful_runs=len(successful),
                workflow_success_pct=100 * len(successful) / len(completed) if completed else None,
                median_workflow_minutes=statistics.median(durations) if durations else None,
            )
        )
    return results


def parse_incident_time(value: str | None, timezone: str) -> datetime | None:
    if not value or value.lower() in {"unknown", "n/a", "none"}:
        return None

    abbreviations = {" PDT": "-07:00", " PST": "-08:00"}
    for suffix, offset in abbreviations.items():
        if value.endswith(suffix):
            value = value[: -len(suffix)] + offset
            break

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = localize(parsed, timezone)
    return parsed.astimezone(UTC)


def minutes_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return (end - start).total_seconds() / 60


def collect_incidents(window: Window, postmortems_dir: Path) -> list[IncidentResult]:
    field_pattern = re.compile(r"^- \*\*(.+?):\*\*\s*(.+?)\s*$", re.MULTILINE)
    incidents = []
    if not postmortems_dir.exists():
        return incidents

    for path in sorted(postmortems_dir.glob("*.md")):
        if path.name.upper() == "TEMPLATE.MD":
            continue
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        fields = {key.lower(): value for key, value in field_pattern.findall(text)}
        started = parse_incident_time(fields.get("start"), window.timezone)
        if not started or not window.start <= started < window.end:
            continue

        detected = parse_incident_time(fields.get("detected"), window.timezone)
        investigation = parse_incident_time(fields.get("investigation started"), window.timezone)
        recovered = parse_incident_time(fields.get("recovered"), window.timezone)
        incidents.append(
            IncidentResult(
                title=title_match.group(1) if title_match else path.stem,
                path=path.as_posix(),
                started_at=started.isoformat(),
                detected_at=detected.isoformat() if detected else None,
                investigation_started_at=investigation.isoformat() if investigation else None,
                recovered_at=recovered.isoformat() if recovered else None,
                mttd_minutes=minutes_between(started, detected),
                mtta_minutes=minutes_between(detected, investigation),
                mttr_minutes=minutes_between(started, recovered),
            )
        )
    return incidents


def format_pct(value: float | None) -> str:
    return "Not available" if value is None else f"{value:.3f}%"


def format_minutes(value: float | None) -> str:
    if value is None:
        return "Not recorded"
    total = round(value)
    hours, minutes = divmod(total, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def median_metric(incidents: Iterable[IncidentResult], field: str) -> float | None:
    values = [getattr(incident, field) for incident in incidents if getattr(incident, field) is not None]
    return statistics.median(values) if values else None


def render_markdown(
    window: Window,
    availability: list[AvailabilityResult],
    delivery: list[DeliveryResult],
    incidents: list[IncidentResult],
) -> str:
    lines = [
        f"# Reliability scorecard — {window.label}",
        "",
        f"Window: `{iso_z(window.start)}` through `{iso_z(window.end)}` "
        f"(complete {window.timezone} calendar days; end is exclusive)",
        "",
        "## Availability",
        "",
        "| Signal | Healthy minutes | Reported samples | Expected minutes | Availability | Probe coverage |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for result in availability:
        lines.append(
            f"| {result.label} | {result.healthy_samples:,} | {result.reported_samples:,} | "
            f"{result.expected_samples:,} | {format_pct(result.availability_pct)} | "
            f"{format_pct(result.coverage_pct)} |"
        )

    lines.extend(
        [
            "",
            "Availability is conservative: an unhealthy or missing one-minute sample counts as unavailable. "
            "Probe coverage is reported separately so monitoring failures are visible.",
            "",
            "## Delivery",
            "",
            "| Application | Production promotions | Completed release workflows | Successful workflows | Workflow success | Median workflow duration |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for result in delivery:
        median_duration = (
            "Not available"
            if result.median_workflow_minutes is None
            else f"{result.median_workflow_minutes:.1f}m"
        )
        lines.append(
            f"| {result.application} | {result.promotions} | {result.workflow_runs} | "
            f"{result.successful_runs} | {format_pct(result.workflow_success_pct)} | {median_duration} |"
        )

    lines.extend(
        [
            "",
            "A production promotion is a merged `Deploy <application> sha-...` change in `homelab-infra`. "
            "Workflow duration ends when the release workflow completes; it does not yet measure Argo CD's time to Healthy.",
            "",
            "## Incidents",
            "",
            f"Incidents started during the window: **{len(incidents)}**",
            "",
            f"- Median MTTD: **{format_minutes(median_metric(incidents, 'mttd_minutes'))}**",
            f"- Median MTTA: **{format_minutes(median_metric(incidents, 'mtta_minutes'))}**",
            f"- Median MTTR: **{format_minutes(median_metric(incidents, 'mttr_minutes'))}**",
        ]
    )
    if incidents:
        lines.extend(
            [
                "",
                "| Incident | Started | MTTD | MTTA | MTTR |",
                "| --- | --- | ---: | ---: | ---: |",
            ]
        )
        for incident in incidents:
            lines.append(
                f"| [{incident.title}]({incident.path}) | `{incident.started_at}` | "
                f"{format_minutes(incident.mttd_minutes)} | {format_minutes(incident.mtta_minutes)} | "
                f"{format_minutes(incident.mttr_minutes)} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation notes",
            "",
            "- Treat availability as resume-ready only when probe coverage is complete and the metric existed for the full reporting window.",
            "- Record every incident using `docs/postmortems/TEMPLATE.md`; missing timestamps remain explicitly unmeasured.",
            "- Backup measurements are intentionally excluded from this scorecard.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--end-date",
        help="Exclusive local end date in YYYY-MM-DD format; defaults to today",
    )
    parser.add_argument("--days", type=int, default=7, help="Number of complete local calendar days (default: 7)")
    parser.add_argument("--timezone", default="America/Los_Angeles")
    parser.add_argument("--aws-region", default=os.environ.get("AWS_REGION", "us-west-1"))
    parser.add_argument("--aws-profile")
    parser.add_argument("--namespace", default="Homelab/Reliability")
    parser.add_argument("--github-owner", default="spicyhek")
    parser.add_argument("--infra-repository", default="homelab-infra")
    parser.add_argument("--github-token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--postmortems-dir", type=Path, default=Path("docs/postmortems"))
    parser.add_argument("--output", type=Path, default=Path("scorecard.md"))
    parser.add_argument("--json-output", type=Path, default=Path("scorecard.json"))
    parser.add_argument("--skip-cloudwatch", action="store_true")
    parser.add_argument("--skip-github", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        window = rolling_window(args.end_date, args.timezone, args.days)
        availability = [] if args.skip_cloudwatch else collect_availability(
            window, args.namespace, args.aws_region, args.aws_profile
        )
        delivery = [] if args.skip_github else collect_delivery(
            window, args.github_owner, args.infra_repository, args.github_token
        )
        incidents = collect_incidents(window, args.postmortems_dir)
    except (OSError, subprocess.CalledProcessError, urllib.error.URLError, ValueError) as error:
        print(f"scorecard collection failed: {error}", file=sys.stderr)
        return 1

    markdown = render_markdown(window, availability, delivery, incidents)
    payload = {
        "window": {
            "label": window.label,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "timezone": window.timezone,
        },
        "availability": [asdict(result) for result in availability],
        "delivery": [asdict(result) for result in delivery],
        "incidents": [asdict(result) for result in incidents],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    args.json_output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} and {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
