# Timeouts between the AWS probe and Cloudflare caused alarm flapping

- **Start:** 2026-11-23T09:20:08-08:00
- **Detected:** 2026-11-23T09:23:48-08:00
- **Investigation started:** 2026-11-23T23:26:02-08:00
- **Recovered:** 2026-11-23T10:10:06-08:00
- **Closed:** unknown
- **Status:** Resolved; probe fix awaiting deployment

All timeline times are PST. The start is the first failed check in the probe
Lambda's log for this episode, a TLS handshake timeout logged at 09:20:08 by the
run that began at 09:19:58. Detection is the first alarm transition,
`ClusterHealthy` to `ALARM` at 09:23:48, from the CloudWatch alarm history.
Investigation start is the first diagnostic command run against the cluster.
Recovery is the last alarm returning to `OK`, `NidsHealthy` at 10:10:06; the
probe passed every check from the 10:09:58 run onward. The failures came in two
bursts, 09:20–09:22 and 10:02–10:09, with every check passing in between. They
are recorded as one incident because they had the same cause on the same
morning.

## Summary

The external health probe, a Lambda in AWS `us-west-1` that runs every minute,
intermittently failed to reach Cloudflare's edge. It logged 15 TLS handshake
timeouts (`_ssl.c:994: The handshake operation timed out`) and several read
timeouts across all five checked endpoints. The homelab was healthy throughout.
Nodes, Pods, the Cloudflare Tunnel and the origin services showed no fault, and
the requests that did reach the origin were served immediately.

The failures caused four one-minute alarm flaps: `ClusterHealthy` at 09:23, and
`SiteHealthy`, `ClusterHealthy` and `NidsHealthy` between 10:07 and 10:10.
Three of the four were artifacts of how the probe timestamps its metrics. A
replay of the logged results shows that only `NidsHealthy` met its alarm
condition. The probe path recovered on its own and no change was made at the
time. The fix to the probe's timestamps and retries is in the same change as
this postmortem.

## Impact

- **Users:** no outage was observed. Another monitor that requests the site and
  the NIDS endpoint every minute from the home internet connection lost one
  request each, at about 10:03:58, and none at other times in the window.
  Cloudflare's request analytics for real visitors were not checked.
- **Alerting:** four alarm flaps, each lasting one minute, sent ALARM and OK
  notifications through SNS. `OriginHealthy`, `MybuildsHealthy` and
  `ProbeExecutionHealthy` stayed `OK`.
- **External health metrics:** 22 checks failed in 10 of the 11 probe runs that
  started between 09:19:58 and 09:21:58 or between 10:01:58 and 10:08:58. Each
  failure was a 10-second timeout, so those runs took 10 to 18 seconds instead
  of the usual 0.4 to 1 second.

## Timeline

- **09:19:58–09:21:58:** Three consecutive probe runs had failures. `mybuilds`
  timed out in the TLS handshake in the first two; `cluster` and `origin` timed
  out reading at 09:20:08–09:20:13 and in the handshake at 09:22:08. `cluster`
  passed in the 09:20:58 run.
- **09:23:48:** `ClusterHealthy` went to `ALARM` on datapoints for 09:20 and
  09:22. The 09:20 minute held both the 09:19:58 run's failure and the
  09:20:58 run's success, and the alarm uses `Minimum`. The 09:21 minute had no
  datapoint.
- **09:24:48:** `ClusterHealthy` returned to `OK`.
- **09:22:58–10:00:58:** All checks passed.
- **10:01:58:** `nids` timed out in the TLS handshake and `mybuilds` timed out
  reading.
- **10:02:58:** `site`, `origin`, `nids` and `mybuilds` timed out in the TLS
  handshake. No probe request reached a `personal-site` Pod for this run or the
  next one.
- **10:03:58:** `site`, `origin` and `cluster` timed out in the TLS handshake.
  The home-side monitor also lost its request at about this time.
- **10:04:58:** All checks passed.
- **10:05:58:** `nids`, `site` and `cluster` timed out reading; the run took
  17.6 seconds. The site request reached `personal-site` at 10:06:05, seven
  seconds after the run started, and was served `200` at once. The Lambda still
  timed out reading at 10:06:15.
- **10:06:58:** `nids` and `origin` timed out in the TLS handshake.
- **10:07:22:** `SiteHealthy` went to `ALARM` on datapoints for 10:03, 10:04 and
  10:06. The 10:05 minute had no datapoint.
- **10:07:48:** `ClusterHealthy` went to `ALARM` on datapoints for 10:04 and
  10:06.
- **10:07:58:** `nids` timed out in the TLS handshake.
- **10:08:22–10:08:48:** `SiteHealthy` and `ClusterHealthy` returned to `OK`.
- **10:08:58:** `site` timed out reading.
- **10:09:06:** `NidsHealthy` went to `ALARM` on datapoints for 10:06, 10:07 and
  10:08.
- **10:09:58:** All checks passed, as did every later run in the window.
- **10:10:06:** `NidsHealthy` returned to `OK`.
- **23:26:02:** Investigation began with read-only diagnostics on the control
  plane. The probe Lambda's logs were read after AWS SSO was renewed.

## Causes

1. **Triggering cause: the probe could not reliably connect to Cloudflare.**
   For these hostnames the TLS handshake is with Cloudflare's edge, so handshake
   timeouts place the fault between the Lambda and the edge, before the tunnel
   or the homelab. DNS resolution and TCP connection both succeeded. The read
   timeouts on requests that the origin had answered, such as the 10:05:58 site
   check, mean responses were also lost on that path. Where the fault was, in
   AWS, in transit or at Cloudflare, was not determined. Cloudflare's status
   page reported nothing for San Jose, North America or Tunnel in the window.
   The AWS Health Dashboard was not checked. Handshake timeouts are rare: 18 in
   the 14 days of retained logs, 3 on November 17 and 15 on this morning.
2. **Contributing condition: metric timestamps made isolated failures look
   consecutive.** The probe did not set `Timestamp`, so CloudWatch stamped each
   datapoint when it arrived. Runs start at about :58, so a check that waits out
   its 10-second timeout lands in the next minute. That minute then also holds
   the next run's result, and the alarms take the `Minimum`. The minute after is
   left empty, and the alarms treat missing data as breaching. Replaying the
   logged results by run:
   - `SiteHealthy` failed in 4 of 8 runs but never 3 in a row, so it would not
     have alarmed.
   - `ClusterHealthy` failed in two runs that were not consecutive, at both
     09:20 and 10:04, so neither alarm would have fired.
   - `NidsHealthy` did fail three runs in a row, 10:05:58 to 10:07:58, so its
     alarm was a genuine result.

   The [October 16 postmortem](2026-10-16-control-plane-network-and-probe-flapping.md)
   found the same mechanism of missing metrics counting as failures, for a
   different reason.
3. **Contributing condition: the probe did not retry.** A single dropped
   handshake or response failed the check for that minute. Other checks in the
   same runs succeeded, so the failures were per connection, and one retry
   would probably have succeeded.

The homelab was ruled out as follows:
- All three nodes have been `Ready` since October or earlier, no Pod restarted,
  and nothing was deployed.
- The control-plane journal has no network, Wi-Fi, network-guard or Tailscale
  events, and `sar` shows a steady load with no interface errors.
- None of the three cloudflared replicas logged anything between 09:50 and
  10:29. There were no tunnel disconnects and no failed origin requests.

This is not a repeat of the
[November 3 gateway outage](2026-11-03-gateway-outage-external-probe-alarms.md),
despite the same time of day. On November 16 at 10:03–10:04, `origin` and `nids`
had read timeouts in two consecutive runs, with no alarm. The error type is
different, so no weekly pattern is established.

## Resolution

The probe path recovered on its own; every check passed from the 10:09:58 run
onward. No change was made at the time.

The corrective change to `terraform/reliability-control-plane/lambda/probe/handler.py`
does two things:
- It stamps every datapoint of a run with the run's scheduled minute, taken from
  the EventBridge event's `time`.
- It retries a failed request once after 2 seconds, on a network error or a
  5xx response, with an 8-second timeout per attempt.

It takes effect when the Terraform change is applied.

## Follow-up actions

| Priority | Action | Owner | Status |
| --- | --- | --- | --- |
| P1 | Apply the probe change (run-minute timestamps and one retry) through Terraform | Operator | Open |
| P1 | After deployment, confirm each metric has exactly one datapoint per minute (`SampleCount` of 1), and check the EventBridge `time` value with a test invocation | Operator | Open |
| P2 | Investigate slow `/api/status` responses: the `origin` and `cluster` checks account for 58 of the 80 probe read timeouts from November 10 to the afternoon of November 23 | Operator | Open |
| P2 | Add a control check against Cloudflare itself, so that probe-path failures mark `ProbeExecutionHealthy` instead of service alarms | Operator | Deferred |
| P2 | Watch for probe failures near 10:00 PST on Mondays (November 16 and 23) | Operator | Open |

## Evidence and limits

The timeline comes from these records:
- the probe Lambda's CloudWatch Logs (per-check errors and run durations)
- the CloudWatch alarm history and the SNS alarm emails
- the raw one-minute `Homelab/Reliability` datapoints (`Minimum` and
  `SampleCount`)
- access logs of the `personal-site` and `nids` Pods (probe user agent
  `homelab-reliability-probe/1.0`)
- the cloudflared logs, the control-plane journal and `sar` data
- current node and Pod status

The status API writes no access logs, so its side of the `origin` and `cluster`
checks was not observed. The worker journals, Cloudflare's request analytics and
the AWS Health Dashboard were not examined. The incident-closure time is
unrecorded.
