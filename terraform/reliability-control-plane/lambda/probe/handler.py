import datetime
import json
import os
import time
import urllib.request

import boto3

cloudwatch = boto3.client("cloudwatch")
METRICS = { "site": "SiteHealthy",
            "origin": "OriginHealthy", 
            "nids": "NidsHealthy",
            "cluster": "ClusterHealthy"           
    }

def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "homelab-reliability-probe/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read()

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


def lambda_handler(event, context):
    results = {}
    for key, checker in (("site", check_site), 
                         ("origin", check_origin), 
                         ("nids", check_nids), 
                         ("cluster", check_cluster)
                        ):
        try:
            results[key] = checker()
        except Exception as error:
            results[key] = False
            print(json.dumps({"check": key, "error": str(error)}))
    cloudwatch.put_metric_data(
        Namespace=os.environ["METRIC_NAMESPACE"],
        MetricData=[{"MetricName": name, "Value": int(results[key]), "Unit": "Count"}
                    for key, name in METRICS.items()],
    )
    return results
