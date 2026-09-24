# Home-server catalog refresh (B16)

Delivered September 24, 2026 as disabled source, not an installed scheduler.
No watched ArgoCD Application references this chart. Default enabled=false renders
nothing; every source defaults false, suspend defaults true and image is empty.
No maintained application image pin or existing watched chart is changed.

The registry's 19 fixed source IDs each have a switch. Enabling a source creates
one CronJob, with sorted-source index × 3 as the minute and */6 as the UTC hour.
Thus sources spread across minutes 0–54 at 00/06/12/18 UTC, independent of DST.
Enabling/disabling one source does not shift others' schedules. B4 import contracts
determine structured versus reviewed handling; no URL/command is configurable here.

Each job: Forbid overlap, 600-second starting window, 300-second active deadline,
zero Kubernetes retries, one successful/two failed histories, one-day finished-job
TTL, 10-second termination grace. The backend CLI has a 240-second alarm and bounded
fetch retries. PostgreSQL source locking also covers manual/duplicate jobs:
Forbid alone only serializes jobs belonging to the same CronJob.
See [Kubernetes CronJob semantics](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/).

The non-root UID 10001 pod uses a read-only filesystem, RuntimeDefault seccomp,
no capabilities or privilege escalation, no mounted API token and 25m/96Mi requests,
250m/256Mi limits. It reuses the existing backend service account and references
only DATABASE_URL from its ConfigMap and the database password from existing custody.
It needs existing DNS/HTTPS/PostgreSQL connectivity, no new AWS grants or RBAC.
The required image is the verified backend B16-or-newer GHCR digest, not a mutable tag.
No JWT, provider key, blob credentials or startup migration is needed.

## Render and verify locally

```sh
helm lint charts/home-server-catalog-refresh
helm template catalog charts/home-server-catalog-refresh -n app
# The default output is empty.
python tests/test_catalog_refresh.py
```

An explicit test render may set enabled=true, sources.testgeneval=true and
image=ghcr.io/steve-droid/driftplain-backend@sha256:<verified-digest>. It still
renders suspend=true. Do not install or add a watched Application as part of B16.

## Separately authorized operational rollout

1. Verify backup/restore, apply the additive backend migrations through the existing
   reviewed migration path, and deploy a compatible backend. Keep schedules suspended.
2. Set backend CATALOG_REFRESH_METRICS_ENABLED=true and verify the existing backend
   ServiceMonitor exposes durable catalog gauges. No new scrape infrastructure is needed.
3. Supply the verified importer digest and selected source switches. Review the
   rendered pod's existing DB references, namespace app, resources and network access.
4. Add a separately reviewed home child Application only under deployment authorization.
   Keep suspend=true initially. Run one bounded manual Job after explicit import approval.
5. Inspect health, source coverage/provenance, pending-review state and last-good data.
   Enable monitoring.enabled and remove suspension only after those checks.
   PrometheusRule resources use the existing monitoring stack; they are omitted while
   suspended. Rules cover missing/stale successful checks, failures and pending review.
6. Record actual first-check time and recovery evidence. Publication/render tests do
   not prove live scheduling, import health or complete source/model coverage.

To disable future executions, set suspend=true globally or sources.SOURCE=false and
review the GitOps change. Suspension does not terminate an already started Job.
Wait for its bounded completion; cancelling a running production Job is an explicit
operator action. Review pending report bytes and snapshot reversal using the backend
[operator contract](https://github.com/Steve-droid/driftplain-backend/blob/main/app/catalog/imports/OPERATIONS.md).
The [home runbook](https://github.com/Steve-droid/driftplain-infra/blob/main/home-server/CATALOG-REFRESH.md)
covers recovery, storage and operational gates. Do not delete data to resolve an alert.
