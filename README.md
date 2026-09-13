# Driftplain — GitOps

[Website](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [Infra](https://github.com/Steve-droid/driftplain-infra) · [GitOps](https://github.com/Steve-droid/driftplain-gitops)

> **P38r — shipped September 12, 2026:** Driftplain is live at **https://driftplain.dev**, with **https://api.driftplain.dev** as its runtime API. Trusted HTTPS, Google domain ownership, published Google branding and real sign-in are verified. Modicum/sslip.io endpoints and operational identifiers remain compatible. FE/BE 1.0.24, agents 1.1.3; runtime cutover GitOps v0.18.22.


> **2026-09-09 DNS follow-up:** Route 53 delegation and trusted HTTPS are verified for modicum.cloud and api.modicum.cloud. These values prepare the reviewed runtime URL cutover; legacy sslip.io routes remain available. Cutover deployment is pending.

> Driftplain was previously Modicum / ModelMatch. The four public repositories use `driftplain-*`; existing infrastructure, images, database names, metrics and CI credential/environment identifiers retain `modelmatch` for compatibility.

> **ACTIVE** (since P9). The GitOps source of truth for everything that runs **inside** the EKS cluster —
> a Helm umbrella + an ArgoCD **App-of-Apps**. Part of the four-repository Driftplain project linked above.
> See [`CLAUDE.md`](CLAUDE.md) for the chart layout + hard rules.

## Table of Contents

- [Overview](#overview)
- [The deploy boundary](#the-deploy-boundary)
- [Technology Stack](#technology-stack)
- [Repository Structure](#repository-structure)
- [ArgoCD App-of-Apps](#argocd-app-of-apps)
- [Getting Started / verification](#getting-started--verification)
- [Ingress host recompute (P15 runbook)](#ingress-host-recompute-p15-runbook)
- [Conventions](#conventions)
- [Contact](#contact)

## Overview

The GitOps repo holds the desired state of the cluster. Two layers:

1. **The Driftplain product chart** — `charts/modelmatch/`, a Helm **umbrella** with **frontend** +
   **backend** local subcharts plus the host-based `Ingress` templates. One release boundary for the app.
2. **Platform charts + ArgoCD App-of-Apps** — a Terraform-seeded **root Application** (in
   `driftplain-infra/platform/argocd.tf`) watches `argocd/apps/` and fans out to one child `Application`
   per platform component (ingress controller, cert-manager, ESO, CNPG, monitoring, logging, cluster
   issuers, app secrets) **and** the product chart.

**Kubernetes hygiene** is enforced everywhere: dedicated namespaces, **resource requests/limits on every
container**, liveness/readiness probes wired to `/healthz` + `/readyz`. The in-cluster Postgres runs on an
**EBS-CSI-backed PVC** (no container-disk state). DB migrations run as a **PostSync Job/hook**, never on
backend startup.

> The proactive advisor was scoped as a **separable, feature-flagged** subchart
> (`FEATURE_PROACTIVE_ADVISOR`) — droppable by demo time with no core impact. It is **not** part of the
> shipped chart.

## The deploy boundary

**CI builds images, pushes to ECR, and commits an image-tag bump here; ArgoCD is the only thing that ever
applies to the cluster.** Humans author chart structure; the Jenkins **Deploy** stage edits the
per-subchart `image.tag` in `charts/modelmatch/values.yaml`; ArgoCD syncs. **Never `helm install` /
`kubectl apply` app resources by hand** — that breaks the GitOps invariant.

```
Jenkins (FE/BE repos)                    this repo (git)                 EKS
  build → ECR → commit "image.tag bump" ───────────►  ArgoCD auto-sync ───────► cluster
```

## Technology Stack

| Category       | Technologies   |
| -------------- | -------------- |
| **Packaging**  | Helm (umbrella `modelmatch` + FE/BE subcharts; in-repo platform charts) |
| **GitOps**     | ArgoCD (App-of-Apps; `prune` + `selfHeal` auto-sync) |
| **Ingress**    | F5 NGINX Ingress Controller (OSS) — one ELB; **host-based** (`app.`/`api.` sslip.io) |
| **TLS**        | cert-manager + Let's Encrypt (HTTP-01); sslip.io host (no domain to register) |
| **Database**   | PostgreSQL 16 — in-cluster via CloudNativePG, EBS-CSI PVC |
| **Secrets**    | External Secrets Operator → AWS Secrets Manager (IRSA role B) |
| **Observability** | kube-prometheus-stack (Grafana dashboards) · ECK Elasticsearch/Kibana + Fluent Bit (EFK) |

## Repository Structure

```
driftplain-gitops/
├── charts/
│   ├── modelmatch/            # umbrella = the product chart (release boundary)
│   │   ├── Chart.yaml         # deps: backend, frontend (local, condition <name>.enabled)
│   │   ├── values.yaml        # global.awsAccountId/awsRegion + global.sslipIp + backend:/frontend: + ingress:
│   │   ├── templates/         # host-based Ingress (F5 master/minion) + NOTES.txt
│   │   └── charts/{backend,frontend}/   # FastAPI + nginx subcharts (Deployment+Service+ConfigMap, probes)
│   ├── modelmatch-postgres/   # CNPG Cluster + gp3 StorageClass + migrate Job (P13)
│   ├── app-secrets/           # ESO SecretStore + ExternalSecret (P12)
│   ├── cluster-issuers/       # Let's Encrypt staging + prod ClusterIssuers, HTTP-01 (P15)
│   ├── monitoring/            # kube-prometheus-stack values + Grafana dashboards (P20–P22)
│   └── logging/               # ECK Elasticsearch/Kibana + Fluent Bit (EFK) (P23)
├── argocd/
│   ├── README.md              # the root App-of-Apps explained
│   └── apps/                  # one ArgoCD Application per child app (see below)
├── scripts/recompute-host.sh  # re-derive the sslip.io host after an ELB IP change
├── docs/diagrams/
├── README.md · CLAUDE.md · .gitignore
```

## ArgoCD App-of-Apps

The Terraform-seeded root Application watches [`argocd/apps/`](argocd/apps/); each `Application` manifest
there points either at a pinned upstream chart (thin app + inline `helm.values`) or at an in-repo `path:`.
All auto-sync (`prune` + `selfHeal`), ordered by **sync-waves** where dependencies require it (e.g. the
CNPG operator before the Postgres cluster; cert-manager before its ClusterIssuers).

| Child app | Source | Namespace | Slice |
|---|---|---|---|
| `nginx-ingress` | F5 NGINX Ingress Controller (OSS) — the single ingress LB | `nginx-ingress` | P11 |
| `cert-manager` | `jetstack/cert-manager` | `cert-manager` | P11 |
| `external-secrets` | `external-secrets` (IRSA role B) | `external-secrets` | P11 |
| `app-secrets` | in-repo `charts/app-secrets` | `app` | P12 |
| `cnpg-operator` | `cloudnative-pg` | `cnpg-system` | P13 |
| `modelmatch-postgres` | in-repo `charts/modelmatch-postgres` | `app` | P13 |
| `modelmatch` | in-repo `charts/modelmatch` (FE + BE + Ingress) | `app` | P14/P15 |
| `cluster-issuers` | in-repo `charts/cluster-issuers` | (cluster-scoped) | P15 |
| `monitoring` + `monitoring-dashboards` | kube-prometheus-stack + Grafana dashboards | `monitoring` | P20–P22 |
| `logging` + `logging-operator` + `logging-fluent-bit` | ECK ES/Kibana + Fluent Bit (EFK) | `logging` | P23 |

See [`argocd/apps/README.md`](argocd/apps/README.md) for the per-app sync-wave reasoning and the
ingress-LB teardown gotcha.

> **Why F5, not community `ingress-nginx`:** the community project was archived 2026-03-24 (end-of-life),
> so the maintained **F5 NGINX Ingress Controller (OSS)** is used instead — same architecture (one NGINX
> controller, one `LoadBalancer` Service, standard `Ingress`).

## Getting Started / verification

This repo is **never** applied by hand — ArgoCD reconciles it. Local work is **author + render-check
only**:

```bash
helm lint charts/modelmatch
helm template modelmatch charts/modelmatch        # renders clean (sslipIp may be the 0.0.0.0 sentinel)
git diff charts/modelmatch/values.yaml
```

Push to `main`; ArgoCD auto-syncs. **No `helm install` / `kubectl apply`.**

## Ingress host recompute (P15 runbook)

The app is reachable over HTTPS at a **sslip.io** host derived from the single ingress ELB's public IP.
That IP has **no static allocation** — every platform rebuild gives a new one — so the host is a
**recompute, not a stable name**. One field drives everything: `global.sslipIp` in
[`charts/modelmatch/values.yaml`](charts/modelmatch/values.yaml). The Ingress, the FE `API_BASE_URL`, and
the BE `PUBLIC_BASE_URL`/`CORS_ALLOW_ORIGINS` all derive from it in-template:

```
global.sslipIp = <ELB public IP>
   ├── app.<ip>.sslip.io  → frontend Service (the SPA)
   └── api.<ip>.sslip.io  → backend  Service (FastAPI, at root)
```

**After each cluster rebuild** (or whenever the ELB IP changes), run the helper — it does **read-only
cluster discovery + a one-field local edit only** (never commits, pushes, or `kubectl apply`s):

```bash
./scripts/recompute-host.sh        # digs the ELB IP, writes global.sslipIp, prints next steps
git diff charts/modelmatch/values.yaml
git commit -am "chore(ingress): recompute sslip.io host -> <ip>"
git push                            # ArgoCD auto-syncs; cert-manager (re)issues the cert
```

**TLS — staging → prod.** `ingress.clusterIssuer` validates HTTP-01 on `letsencrypt-staging` first
(untrusted cert; `curl -k`), then flips to `letsencrypt-prod` (current). On a fresh rebuild, if a stale
staging cert lingers, **delete the `app/modelmatch-app-tls` and `app/modelmatch-api-tls` Secrets** so
cert-manager re-issues against prod, then verify a trusted chain (no `-k`):

```bash
kubectl -n app get ingress,certificate,order,challenge
curl -kI https://app.<ip>.sslip.io/          # FE
curl -k  https://api.<ip>.sslip.io/healthz   # BE
```

**Why the Ingress is split (F5 mergeable master/minion).** F5 NGINX, unlike community ingress-nginx,
won't merge a second Ingress onto a host the app already owns — and cert-manager's HTTP-01 solver *is* a
second Ingress on that host. So per host the umbrella renders a **master** (host + TLS + cert-manager
annotation, no paths) + a **minion** (the route), and the ClusterIssuer annotates the solver as another
minion so F5 merges the challenge path in. `ssl-redirect` is forced **off** (F5 otherwise 301s
HTTP→HTTPS, breaking the plain-HTTP:80 challenge and the 60-day renewals). Each host owns its own
single-SAN cert (`modelmatch-app-tls` / `modelmatch-api-tls`).

## Conventions

- Humans commit chart structure; the CI Deploy stage commits image-tag bumps; ArgoCD syncs.
- **NO secrets in values/ConfigMap — ever.** `JWT_SECRET`, the DB password, and the chat read-only DB
  password arrive via **ESO → AWS Secrets Manager**. ConfigMaps hold non-secret knobs only.
- Resource requests/limits on **every** container; backend capped at **1Gi** (the ingestion-OOM gotcha).
- Branching: `feature/<story-id>-<desc>` → PR (self-review) → merge `--no-ff` → `main`. Conventional
  Commits; SemVer tags. Verification = `helm lint` + `helm template`, never `helm install`.
- **Account switch (P33b, "Option B"):** the AWS account id is never a literal in a template. It lives in
  **three places only** — `charts/modelmatch/values.yaml` `global.awsAccountId`/`awsRegion` (ECR registry
  host + backend IRSA ARN are templated from it), the same `global:` block in
  `charts/modelmatch-postgres/values.yaml` (its own Application, so its own copy), and the hand-maintained
  ESO role ARN in `argocd/apps/external-secrets.yaml` (upstream chart, inline values). Moving accounts =
  edit those three + `S3_BUCKET`; `grep -rn <old-account-id> charts argocd` must return nothing.

## Contact

Steve Levit — stevelevit230@gmail.com
</content>

## Branded hosts: Driftplain DNS cutover (P38m, staged)

Selected names: **modicum.cloud** (app), **api.modicum.cloud** (API). Registration at Porkbun is complete;
Route 53 delegation and both trusted HTTPS endpoints are verified. The live runtime still uses
sslip.io until this cutover commit is reviewed and deployed.

`global.appHost` and `global.apiHost` create additional F5 master/minion ingress pairs and
separate single-host cert-manager certificates. **`global.useCustomHosts: false` is deliberate:**
the runtime API URL remains the working sslip.io API until both new certificates are Ready.
Set it to `true` only in the second reviewed deployment, after DNS and trusted HTTPS checks.
Backend `PUBLIC_BASE_URL` and frontend runtime `API_BASE_URL` switch together. CORS accepts
both app origins while `global.retainSslipHosts: true`; old Jenkins snippets keep their direct
API endpoint and do not need POST redirects. Pod config checksums trigger the required rollouts.

With custom names set, `scripts/recompute-host.sh` fails before editing anything: changing the old
IP would retire existing snippets. Refresh Route 53's alias target through the infra
[DNS runbook](https://github.com/Steve-droid/driftplain-infra/blob/main/dns/README.md). That runbook covers Porkbun delegation to the Terraform-created zone,
Terraform plan, certificate staging, cutover, rollback, rebuilds, and final teardown.
A domain change does not migrate browser storage; sign in at the new origin.

Rollback: keep both ingress sets and switch `useCustomHosts` back to `false`. Remove legacy
hosts/CORS only after clients migrate, by setting `retainSslipHosts: false` while custom hosts
are active. The schema rejects incomplete host pairs, URL/path values, and unsafe transitions.

Offline checks (Python 3 + PyYAML, Helm):

```sh
helm lint charts/modelmatch
helm template modelmatch charts/modelmatch
uv run --with pyyaml python tests/test_public_hosts.py
bash -n scripts/recompute-host.sh
```

The render tests cover the original configuration, staged certificates, runtime cutover,
legacy retirement, unique certificates/F5 routes, config rollout checksums, and rejected inputs.
The values now set `useCustomHosts: true` for the second reviewed rollout, while
`retainSslipHosts: true` preserves compatibility. DNS needs no application rebuild, migration,
seed or postgres-app sync. ArgoCD deploys the product chart after review. Live checks on
2026-09-09 confirmed HTTPS 200 and trusted single-host certificates for app and API, including
both NLB addresses, plus branded API login/dashboard reads. Passwords/tokens stayed in process;
chat was skipped. Re-run runtime config, login, dashboard and CI-setup checks after cutover.


### Google sign-in and migration-only releases (P38n)

`backend.config.GOOGLE_CLIENT_ID` is the existing Driftplain Web public client ID. Its
subchart default is blank (disabled); no Google client secret or ExternalSecret is used.
Google's authorized JavaScript origin is `https://modicum.cloud`. Both legacy sslip.io
routes remain for password sessions and existing API/CI clients. The public privacy page
is `https://modicum.cloud/privacy.html`.

For the existing populated database, `seed.catalog=false` and `seed.demo=false` omit both
seed hooks. A reviewed fresh bootstrap must explicitly enable the seeds it needs. The
migration image is independently pinned in `charts/modelmatch-postgres/values.yaml`.

For a schema-changing release: publish the tested migration image, privately back up and
restore-check the DB, release only the Postgres chart pin, and request an ArgoCD sync at
that exact commit. Hook-only changes can show Synced without executing the hook. Verify
new Job image/creation time/success and schema/data preservation before releasing backend,
then frontend/client configuration. Never use the normal backend Jenkins Deploy stage for
this ordering: it bumps app and migration pins simultaneously. Paid Bedrock tests require
explicit `RUN_LIVE_LLM=true`; P38n uses the Phase 2 local image-publication path.

P38n release: migration PR #27 / v0.18.9 → backend PR #28 / v0.18.10 → frontend/client
PR #29 / v0.18.11. Schema `e2f3a4b5c6d7`, backend 1.0.20, frontend 1.0.18. Actual Google
new-user and returning login, stable account/project, collision refusal and password/legacy
compatibility passed; Google audience is External / In production. The follow-up frontend
image adds Google's public HTML ownership proof for consent-screen branding, without DNS
changes. The proof must remain available for Google's periodic ownership checks.

If Google auth needs disabling, blank its public client ID and roll forward. Once Google-only
users exist, do not blindly restore pre-Google backend code (it assumes non-null passwords)
or run the migration downgrade (it removes Google identity associations).


## Driftplain transition (P38r; September 12, 2026)

Committed values stage the enabled `global.additionalHosts.driftplain` app/API pair, producing
five extra protected F5 ingress objects with distinct TLS secrets. Existing ingress objects,
migration/seed charts and Google client ID are preserved. Backend
CORS accepts the exact new app origin. `global.runtimeHostSet=driftplain` selects the new runtime; an empty selector retains the prior api.modicum.cloud runtime.

Registration, delegation, trusted HTTPS, Google domain ownership and the retained OAuth client origin are verified. Google verified and published Driftplain branding. Both public URL ConfigMaps select
`https://api.driftplain.dev`; all existing hosts continue to serve directly. Runtime rollback
sets the selector back to an empty string and leaves the additional pair enabled. Set a host
set's `enabled=false` only when intentionally retiring its routes and CORS origin; a disabled
set cannot be selected as the runtime. Do not remove domains using a null map override, since
Helm coalesces global maps into subcharts. New keys must not use the reserved `branded` suffix.

The new master/certificate secret names are `modelmatch-app-tls-driftplain` and
`modelmatch-api-tls-driftplain`. Existing `*-branded` secrets belong to modicum.cloud and remain.
The separately published agent images must exist in ECR before AGENT_IMAGE/AGENT_SECURITY_IMAGE
are bumped. No postgres Application sync or seed/migration hook is needed for this rebrand.
