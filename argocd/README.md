# `argocd/` — the App-of-Apps GitOps surface

This directory is what **ArgoCD watches** to deploy everything inside the cluster. It is the GitOps
half of the deploy boundary: humans author Application structure here; **ArgoCD is the only thing that
applies to the cluster** (CI never `kubectl apply`s — it only bumps image tags, P17/P18).

## The root App-of-Apps is Terraform-seeded (not committed here)

The **root `Application`** is created by **Terraform**, in `driftplain-infra/platform/argocd.tf`
(`helm_release "argocd-apps"`), on every day-start `platform/` apply. It is the single bootstrap seed
that makes the rest declarative, so it lives with the installer (infra), **not** in this repo — decided
2026-06-14 (P10).

The root app is configured to watch this repo:

```
source:
  repoURL:        https://github.com/Steve-droid/driftplain-gitops.git
  path:           argocd/apps        # ← the child-Application dir below
  targetRevision: main
syncPolicy:
  automated: { prune: true, selfHeal: true }   # auto-sync
```

So a commit under `argocd/apps/` on `main` auto-syncs into the cluster with no human action.

## What lives where

| Path | Holds | Slice |
|---|---|---|
| `argocd/apps/` | **child** `Application` manifests (one YAML per app) | P11+ |
| *(root app)* | seeded by Terraform in `driftplain-infra/platform/` | P10 |

**Roadmap for `argocd/apps/`** (empty skeleton at P10):
- **P11** — platform child-apps: Nginx ingress controller, cert-manager, External Secrets Operator.
- **P14** — the Driftplain product umbrella (`charts/modelmatch/`: FE + BE + in-cluster Postgres).
- The proactive advisor stays a **separable** child app, toggled in one place.

## Health

ArgoCD **removed** built-in health assessment for `argoproj.io/Application` in v1.8 — without a fix a
parent app-of-apps reads Healthy regardless of its children. We restore it with the documented Lua
health check (`argocd-cm` key `resource.customizations.health.argoproj.io_Application`), set in the
argo-cd Helm values in `driftplain-infra/platform/argocd.tf`. So the root app's health **does** reflect
each child Application's health once children land (P11+).
[Ref](https://argo-cd.readthedocs.io/en/stable/operator-manual/health/#argocd-app-health).

## Access (public repo — a deliberate tradeoff)

This repo is **public**, so ArgoCD reads it **over HTTPS anonymously — no credential, no repo `Secret`,
nothing in Terraform state**. That is safe because the repo holds only Helm charts + ArgoCD `Application`
manifests; all app secrets (`JWT_SECRET`, DB password, …) arrive in-cluster via **ESO → Secrets Manager**
(P12), never Git. `.gitignore` blocks `*-secret.yaml`/`secrets.yaml` as a backstop.

> **Tradeoff (decided 2026-06-14):** this deviates from the build-module default of *four private repos*
> (lesson-01). We accept it for the gitops repo specifically because (a) it is secret-free by design and
> (b) a public repo lets ArgoCD sync with **zero credential bootstrap** (no deploy key in cluster Secrets
> or Terraform state). The other three repos stay private.
