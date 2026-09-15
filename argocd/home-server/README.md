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

**HM3 scope is DB-only.** The root and these two children are the complete set the
restore rehearsal needs; the application/ingress/secrets children join `apps/` under the
same root in HM4. The root syncs from `main`, so a child change only takes effect after
its PR is merged.

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
