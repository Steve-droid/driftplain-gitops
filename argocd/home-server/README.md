# `argocd/home-server/` — the home-server GitOps profile (E21)

The **home K3s cluster** (`driftplain-home`) GitOps profile, kept separate from
`argocd/apps/` (AWS EKS production). Nothing here is reconciled by the AWS root app: its
source path is `argocd/apps`, not this directory.

| Path | Holds | Slice |
|---|---|---|
| `argocd-values.yaml` | Values for the home argo-cd chart install (**9.5.21**, the AWS pin): no dex/notifications, ClusterIP server, limits on every component, the Application Lua health check. | HM3 |
| `root.yaml` | The home **root App-of-Apps** (`home-server-root`): the one operator-applied seed. Watches this directory's `apps/` on `main`, non-recursive, prune + selfHeal. The home counterpart of Terraform's `argocd-apps` release on AWS. | HM3 |
| `apps/cnpg-operator.yaml` | CloudNativePG operator, chart 0.29.0 / operator 1.30.0 — the series that lists Kubernetes 1.36 as supported (1.29 only *tests* 1.36 and reaches EOL September 29, 2026). AWS stays on its own pin; it is not upgraded by this profile. | HM3 |
| `apps/modelmatch-postgres.yaml` | The same in-repo Postgres chart as AWS, rendered with `values-home-server.yaml`: one instance, `home-server-retain` local-path storage, no ESO, no migrate/seed hooks. | HM3 |
| `apps/sealed-secrets.yaml` | Sealed Secrets controller (chart 2.20.0 / 0.40.0, ns `sealed-secrets`, wave -2): unseals the committed SealedSecret manifests; its sealing keys are backed up age-encrypted off-machine (infra `home-server/HM4-HOME-APP.md`). Home has no ESO. | HM4 |
| `apps/cert-manager.yaml` | cert-manager v1.20.2 (the AWS pin), wave -1. Signs the Ingress leaf certs from the private CA below; no ACME at home. | HM4 |
| `apps/nginx-ingress.yaml` | F5 NGINX Ingress Controller 2.6.0 (the AWS pin) with a **ClusterIP** Service, wave 0: no LoadBalancer/NLB/NodePort — reached by SSH port-forward (HM4) and the tunnel connector (HM5). | HM4 |
| `apps/cluster-issuers.yaml` | The in-repo cluster-issuers chart with `values-home-server.yaml`, wave 1: Let's Encrypt off, a self-signed bootstrap → CA Certificate → `home-server-ca` ClusterIssuer chain on. | HM4 |

**HM3 was DB-only; HM4 adds the platform children above first, then the sealed app
Secrets (`apps/app-secrets.yaml` → `sealed/`) and the `modelmatch` umbrella with
`charts/modelmatch/values-home-server.yaml` (GHCR images by digest, no IRSA, fake LLM,
private `*.home-server.driftplain.dev` hosts) once the images are published and the
sealing keys are backed up.** The root syncs from `main`, so a child change only takes
effect after its PR is merged. Contract tests: `tests/test_home_server_profile.py`.

## Bootstrap (once per home cluster; runbook: infra `home-server/HM3-RESTORE.md`)

Home has no `helm` binary: render on the Mac, apply over `ssh home-server` with the
explicit K3s kubeconfig/context. Every wait is bounded.

```sh
helm template argocd argo/argo-cd --version 9.5.21 -n argocd --include-crds \
  -f argocd/home-server/argocd-values.yaml > /tmp/argocd-home.yaml   # Mac
kubectl create namespace argocd                                        # home
kubectl apply --server-side -n argocd -f /tmp/argocd-home.yaml         # CRDs are large: server-side
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
# operator-owned owner Secret app/modelmatch-db-app FIRST (install-owner-secret), then:
kubectl apply -n argocd -f argocd/home-server/root.yaml
```

The ArgoCD server is reachable only via `kubectl -n argocd port-forward svc/argocd-server`
over SSH; the initial admin password is the `argocd-initial-admin-secret` Secret on home.

The owner basic-auth Secret `app/modelmatch-db-app` is **not** in Git: home has no
ESO. It is created by the operator from the age-encrypted HM3 credential bundle
(same original value) and moves to Sealed Secrets in HM4.
