# CLAUDE.md — driftplain-gitops

## Claude Code continuation — HM3 private database restore — September 15, 2026

Read [the HM3 handoff](../docs/session-handoffs/E21-home-hosting/2026-09-15-hm3-backup-restore.md)
first and [umbrella instructions](../CLAUDE.md). HM2 is complete. HM3 creates an isolated
one-instance CNPG PostgreSQL 16 restore target; AWS production remains unchanged.

**Do not apply the current AWS root/chart unchanged to home.**
`charts/modelmatch-postgres/values.yaml` currently declares `app/modelmatch-postgres`,
owner/database `modelmatch`, chat role `modelmatch_chat_ro`, PG image
`16.10-system-trixie`, two 5Gi PVCs and `modelmatch-gp3`/Delete storage. The storage template
hardcodes `ebs.csi.aws.com`. Home requires a real separate local-storage profile with
Retain and node affinity on `/var/lib/rancher/k3s/storage`; size requests are not quotas.

The current `migrate-job.yaml` is an unconditional PostSync hook, still using image tag
1.0.22. Runtime FE/BE is 1.0.24. Both seed flags are false. Make restore/migration policy
explicit before syncing: no automatic upgrade, seed or tag bump against restored data.
Preserve owner credentials from `modelmatch/app`, basic-auth Secret `modelmatch-db-app`
and app Secret `modelmatch-app-secrets`; inspect role/ACL/default-privilege restoration.
Existing operator chart pin is 0.28.3/app 1.29.1; verify compatibility, don't assume latest.

Resolve the critical boundary between **minimal DB-only home GitOps ownership in HM3**
and HM4's full home root/app deployment. Preserve the GitOps invariant below; prepare the
smallest proper profile or surface a narrowly justified rehearsal exception. Do not
silently bypass ArgoCD, reuse the AWS destination or deploy the full application early.
Namespaces `app` and `home-server-backups` already exist with operator-owned identity
Secrets; preserve those. No home DB/operator/app deployment is yet present.

**Home root (decided September 15):** `argocd/home-server/root.yaml` is the minimal DB-only
home App-of-Apps; `argocd/home-server/README.md` holds the bootstrap steps. Its children
sync from `main`, so home profile changes land only after merge. Tests:
`../driftplain-backend/.venv/bin/python tests/test_home_server_profile.py`.

GHCR, Sealed Secrets, durable S3 ingestion and Cloudflare full DNS are selected; their
full runtime integration is HM4/HM5. No public DNS/cutover or scheduled backup/renewal work
belongs in this slice. See [HM2 acceptance](../driftplain-infra/home-server/HM2-ACCEPTANCE.md).

**Current working preference (Steve, September 15):** keep progressing and pause only
for critical architectural decisions. Plan, use focused tests for new behavior, verify and
self-review before routine commits/PRs; do not reintroduce the generic approval loops or
full-suite repetition below for unchanged work. This supersedes those older instructions
for this continuation. No paid LLM calls, public cutover, production teardown or destructive
source changes without explicit scope. No subagents/review agents, unsolicited diagrams
or additional tasks. Keep answers concise.

> **P38r (September 12, 2026):** Driftplain DNS and trusted app/API HTTPS are verified; the existing Google client has the new origin, verified ownership and published branding. `runtimeHostSet=driftplain` selects api.driftplain.dev while retaining Modicum and sslip.io.

> Driftplain was previously Modicum / ModelMatch. The four public repositories use `driftplain-*`; existing infrastructure, images, database names, metrics and CI credential/environment identifiers retain `modelmatch` for compatibility.

**Status: ACTIVE.** The GitOps repo for Driftplain: the Helm umbrella + (later) ArgoCD app-of-apps that
deploy the cluster. Activated at **P9 (2026-06-14)** when the Helm umbrella was authored.

> Polyrepo: this is its **own git repo** — branches/commits/tags happen **here**, not in
> `driftplain-infra`. See the umbrella `../CLAUDE.md` (working style, git strategy, stack) and
> `../docs/planning/01-devops-backlog.md` E12 rows for the slice plan.

## What this repo is

The **GitOps source of truth** for everything that runs *inside* the EKS cluster. Two layers:

1. **The Driftplain product chart** — `charts/modelmatch/`, a Helm **umbrella** with **frontend** +
   **backend** local subcharts (Postgres subchart lands in **P13**). One release boundary for the app.
2. **ArgoCD app-of-apps** (from **P10**) — a root Application that points at this repo and fans out to
   platform child-apps (Nginx ingress, cert-manager, ESO, monitoring, logging) + the product chart.

**The deploy boundary:** CI (Jenkins, in `driftplain-backend`/`-frontend`) builds images, pushes to ECR,
and **commits an image-tag bump here**; **ArgoCD** is the only thing that ever applies to the cluster.
Humans author chart structure; the CI Deploy stage edits image tags; ArgoCD syncs. **Never
`helm install` / `kubectl apply` app resources by hand** — that breaks the GitOps invariant.

## Chart layout

```
charts/modelmatch/                 # umbrella = the Driftplain product chart (release boundary)
├── Chart.yaml                     # dependencies: backend, frontend (local, condition <name>.enabled)
├── values.yaml                    # global.awsAccountId/awsRegion + global.sslipIp + backend:/frontend: blocks
├── templates/NOTES.txt            # render summary (no workload templates at umbrella level)
└── charts/
    ├── backend/                   # FastAPI: Deployment+Service+ConfigMap, probes /healthz /readyz
    └── frontend/                  # nginx:   Deployment+Service+ConfigMap, probe /
```

- **Subcharts are real charts** (own `Chart.yaml`/`values.yaml`/`_helpers.tpl`), vendored under
  `charts/`. Helper `define` names are **namespaced** (`backend.*`, `frontend.*`) so they don't collide
  in one umbrella render.
- **Values flow:** umbrella `values.yaml` has a `backend:`/`frontend:` block per subchart + a shared
  `global:` block. Env-specific overrides layer on later as `values-<env>.yaml` **without restructuring**.
- **Image wiring:** the ECR registry host is **derived** from `global.awsAccountId` + `global.awsRegion`
  (subchart `<name>.registry` helpers, P33b) + per-subchart `image.repository`/`image.tag`. The CI Deploy
  stage (P17/P18) bumps **one `tag` field** per repo. The account id is never a template literal —
  see README "Account switch".

## Hard rules (don't re-derive)

- **Resource requests/limits on EVERY container — no exceptions.** Backend: **request 256Mi/200m, limit
  1Gi/1CPU** — the 1Gi cap is the documented **ingestion-OOM gotcha** (the in-cluster Nova ingestion path
  must stay under it). Frontend (nginx): request 64Mi/50m, limit 128Mi/250m.
- **Probes:** backend liveness `/healthz` + readiness `/readyz` (FastAPI). Frontend liveness/readiness on
  `/`. Probe `port:` references the named container port (`http`), not a hardcoded number.
- **Container ports:** backend **8000** (gunicorn, non-root), frontend **8080** (nginx-unprivileged,
  uid 101). Services: backend `8000`, frontend `80` → targetPort `http`.
- **NO secrets in values/ConfigMap — ever.** `JWT_SECRET`, the DB password (→ `DATABASE_URL`, composed
  at P14), and the chat read-only DB password (`CHAT_READONLY_DB_PASSWORD`) arrive via **ESO → Secrets
  Manager (P12)**. ConfigMaps hold non-secret knobs only (`BASELINE_MODEL_ID`, `QUALITY_THRESHOLD`,
  region/model knobs, **`LLM_HOURLY_TOKEN_CAP` — a tuning knob, not a secret**, `API_BASE_URL`).
  `.gitignore` blocks `*-secret.yaml`/`secrets.yaml` as a backstop.
- **`API_BASE_URL` is browser-facing** — it's the **public ingress host** (P15 sslip.io), *not* the
  in-cluster backend Service DNS, because the user's browser (not nginx) calls the backend.
- **In-cluster Postgres uses an EBS-CSI-backed PVC** (no hostPath/container disk) — **P13**.
- **The proactive advisor is a separable subchart** toggled by a `FEATURE_PROACTIVE_ADVISOR` values flag
  (not a code change) — droppable by demo time with no core impact.
- **The CI-agent image is NOT a cluster workload** — it runs in the *user's* Jenkins (BYOK). It is built
  and published by the backend repo's `Jenkinsfile.agent`; it never becomes an umbrella subchart.

## Git / verification

- **Branch → PR (self-review) → merge `--no-ff` → SemVer tag** (per-slice cadence, `v0.X.0`). The
  shell-init commit was the one allowed direct-to-`main`; everything since is feature-branch-only.
- **Verification for chart work = `helm lint charts/modelmatch` + `helm template modelmatch
  charts/modelmatch`** render clean; **no `helm install`**. Conventional Commits, **no Claude co-author
  trailer**, **SSH remote**.

> See `../docs/planning/architecture.md` §12–§13 and `../docs/instructions/lesson-03` (Infrastructure/Helm).

## P38m public hostname rollout

`global.appHost`/`apiHost` stage new ingress and cert-manager TLS alongside the legacy hosts.
`useCustomHosts` switches FE runtime API URL + BE public URL only after DNS and certificate checks.
Keep `retainSslipHosts=true` for existing browser origins/Jenkins snippets. Rollback only the switch;
retain both ingress sets. `recompute-host.sh` refuses custom-host configurations. See the README
and infra `dns/README.md`; both Modicum and Driftplain domains are retained. Revert only the runtime selector for rollback.
