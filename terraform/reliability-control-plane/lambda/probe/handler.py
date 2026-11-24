import concurrent.futures
import datetime
import http.client
import json
import os
import time
import urllib.error
import urllib.request

import boto3

cloudwatch = boto3.client("cloudwatch")
METRICS = {
    "site": "SiteHealthy",
    "origin": "OriginHealthy",
    "nids": "NidsHealthy",
    "cluster": "ClusterHealthy",
    "mybuilds": "MybuildsHealthy",
}
PROBE_METRIC = "ProbeExecutionHealthy"

# Two attempts of at most 8 seconds each, 2 seconds apart, keep a failing check
# near 18 seconds, inside the 30-second Lambda timeout. One retry absorbs a single
# dropped TLS handshake or response on the path from AWS to Cloudflare
# (docs/postmortems/2026-11-23-probe-path-timeouts-alarm-flapping.md); a real
# outage still fails both attempts.
FETCH_ATTEMPTS = 2
FETCH_TIMEOUT_SECONDS = 8
RETRY_DELAY_SECONDS = 2


def fetch_once(url):
    request = urllib.request.Request(url, headers={"User-Agent": "homelab-reliability-probe/1.0"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return response.status, response.read()


def fetch(url):
    """Fetch url, retrying once after a network error or a 5xx response.

    A 4xx response is returned to the caller's check by raising at once: it is
    an answer from the service, not a dropped request.
    """
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            return fetch_once(url)
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == FETCH_ATTEMPTS:
                raise
            reason = f"HTTP {error.code}"
        except (OSError, http.client.HTTPException) as error:
            if attempt == FETCH_ATTEMPTS:
                raise
            reason = str(error)
        print(json.dumps({"url": url, "attempt": attempt, "retrying_after": reason}))
        time.sleep(RETRY_DELAY_SECONDS)


def check_cluster():
    status, body = fetch(os.environ["ORIGIN_STATUS_URL"])
    if not 200 <= status < 300:
        return False

    payload = json.loads(body)
    expected_nodes = set(json.loads(os.environ["EXPECTED_CLUSTER_NODES_JSON"]))
    nodes = payload.get("nodes")

    if not isinstance(nodes, list) or not expected_nodes:
        return False

    node_readiness = {
        node.get("name"): node.get("ready") is True
        for node in nodes
        if isinstance(node, dict) and node.get("name")
    }

    return all(node_readiness.get(name, False) for name in expected_nodes)


def check_site():
    status, body = fetch(os.environ["SITE_URL"])
    return 200 <= status < 300 and os.environ["SITE_MARKER"].encode() in body


def check_origin():
    status, body = fetch(os.environ["ORIGIN_STATUS_URL"])
    payload = json.loads(body)
    snapshot = (payload.get("snapshot_at") or payload.get("timestamp") or
                payload.get("updated_at") or payload.get("generatedAt"))
    if not snapshot or not 200 <= status < 300:
        return False
    if isinstance(snapshot, (int, float)):
        timestamp = float(snapshot)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
    else:
        timestamp = datetime.datetime.fromisoformat(snapshot.replace("Z", "+00:00")).timestamp()
    return time.time() - timestamp <= float(os.environ["MAX_SNAPSHOT_AGE_SECONDS"])


def check_nids():
    status, body = fetch(os.environ["NIDS_HEALTH_URL"])
    payload = json.loads(body)
    return (200 <= status < 300 and payload.get("status") == "ok"
            and payload.get("model_loaded") is True and payload.get("ingest_enabled") is True)


def check_mybuilds():
    # mybuilds cannot record its own failed deploy (its docs/self-tracking.md),
    # so this is the independent check of it. /healthz answers from the
    # process, through Cloudflare and the tunnel.
    status, _body = fetch(os.environ["MYBUILDS_HEALTH_URL"])
    return 200 <= status < 300


CHECKS = (
    ("site", check_site),
    ("origin", check_origin),
    ("nids", check_nids),
    ("cluster", check_cluster),
    ("mybuilds", check_mybuilds),
)


def run_minute(event):
    """Return the minute this run belongs to, as an aware UTC datetime.

    Every datapoint of a run is stamped with this minute. Without an explicit
    timestamp CloudWatch uses the time each datapoint arrives; runs start at
    about :58, so a check that waits out its timeout lands in the next minute.
    That gives one minute two datapoints (the 1-minute alarms take the Minimum)
    and the next none (missing data is treated as breaching), and two
    non-consecutive failures could fire a 2-of-2 alarm. The scheduled time from
    the EventBridge event is used because it does not drift; the handler's
    start time is the fallback for a manual invocation.
    """
    scheduled = event.get("time") if isinstance(event, dict) else None
    moment = None
    if isinstance(scheduled, str):
        try:
            moment = datetime.datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is None or moment.tzinfo is None:
        moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).replace(second=0, microsecond=0)


def publish_metric(name, healthy, timestamp):
    cloudwatch.put_metric_data(
        Namespace=os.environ["METRIC_NAMESPACE"],
        MetricData=[{
            "MetricName": name,
            "Value": int(healthy),
            "Unit": "Count",
            "Timestamp": timestamp,
        }],
    )


def lambda_handler(event, context):
    timestamp = run_minute(event)
    results = {}
    metric_publication_succeeded = True

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CHECKS)) as executor:
        futures = {executor.submit(checker): key for key, checker in CHECKS}
        for future in concurrent.futures.as_completed(futures):
            key = futures[future]
            try:
                results[key] = bool(future.result())
            except Exception as error:
                results[key] = False
                print(json.dumps({"check": key, "error": str(error)}))

            try:
                publish_metric(METRICS[key], results[key], timestamp)
            except Exception as error:
                metric_publication_succeeded = False
                print(json.dumps({"metric": METRICS[key], "error": str(error)}))

    execution_healthy = metric_publication_succeeded and len(results) == len(CHECKS)

    try:
        publish_metric(PROBE_METRIC, execution_healthy, timestamp)
    except Exception as error:
        execution_healthy = False
        print(json.dumps({"metric": PROBE_METRIC, "error": str(error)}))

    return {**results, "probe_execution": execution_healthy}
