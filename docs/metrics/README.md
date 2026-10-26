# Reliability measurement

The rolling seven-day scorecard measures service availability, monitoring
coverage, application delivery, and incident response. AWS cost and backup
measurements are intentionally excluded.

## Definitions

- **Availability:** healthy one-minute samples divided by every expected minute
  in the last seven complete Pacific-time calendar days. Missing samples count
  as unavailable.
- **Probe coverage:** reported samples divided by expected samples. This keeps a
  monitoring failure from being mistaken for confirmed service downtime.
- **Production promotion:** a merged `Deploy <application> sha-...` commit in
  `homelab-infra`.
- **Release workflow success:** successful completed release workflows divided
  by all completed release workflows.
- **MTTD:** incident detection time minus incident start time.
- **MTTA:** investigation start time minus detection time.
- **MTTR:** service recovery time minus incident start time.

The scorecard does not yet claim deployment lead time to an Argo CD `Healthy`
state. Its workflow duration ends when the application release workflow
finishes and the infrastructure promotion has been merged.

## Local collection

Run the collector with an AWS profile that can read CloudWatch:

```powershell
python scripts/reliability_scorecard.py `
  --end-date 2026-10-19 `
  --aws-profile homelab-admin `
  --output scorecard/scorecard.md `
  --json-output scorecard/scorecard.json
```

The collector automatically selects a CloudWatch period compatible with metric
retention and the 1,440-datapoint API limit. It saves only aggregates; raw logs,
endpoint responses, and secrets are never written to the report.

## Incident records

Create postmortems from `docs/postmortems/TEMPLATE.md`. Complete all five
timestamps when possible. Historical incidents may use `unknown`; the
scorecard reports missing timing data as `Not recorded` instead of estimating
it.

The first reports after introducing `ProbeExecutionHealthy` will show partial
coverage because that metric did not exist for the entire window. Wait for its
first complete seven-day window before using it as a resume statistic.
