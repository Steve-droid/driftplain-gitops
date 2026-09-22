# Driftplain GitOps

[Project overview](https://github.com/Steve-droid/driftplain) · [Open the app](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [Infrastructure](https://github.com/Steve-droid/driftplain-infra)

This repo defines how Driftplain runs in Kubernetes. Helm charts describe the application
and its supporting services. ArgoCD watches `main` and keeps the cluster in sync with those
definitions.

The live deployment is a single-node K3s cluster on an Ubuntu home server, reached through
Cloudflare Tunnel. The [infrastructure repo](https://github.com/Steve-droid/driftplain-infra)
prepares that server and manages AWS and Cloudflare. This repo manages the workloads inside it.

## How deployment works

1. A release tag in an app repo triggers GitHub Actions to publish an image to GHCR.
2. A PR here updates the image digest in
   [values-home-server.yaml](charts/modelmatch/values-home-server.yaml). The digest identifies
   the exact image to run.
3. After the PR merges, ArgoCD applies the change and Kubernetes rolls out the new image.

Publishing an image does not deploy it. Application and cluster configuration changes go
through this repo. ArgoCD also corrects changes made directly to resources it manages and
removes managed resources deleted from Git.

Database migrations are a separate step. The PostgreSQL chart includes an Alembic migration
Job, but the home-server profile disables automatic migration and seed jobs to protect the
restored production database. A schema-changing release needs an explicit migration before
the new backend is deployed. The backend never runs migrations at startup.

## Main components

| Path | Purpose |
|---|---|
| [argocd/home-server/root.yaml](argocd/home-server/root.yaml) | Root ArgoCD application. It creates and manages the child applications in the home-server profile. |
| [argocd/home-server/apps](argocd/home-server/apps/) | Child applications for the app, database, ingress, certificates, monitoring, backups and tunnel. |
| [charts/modelmatch](charts/modelmatch/) | Parent Helm chart containing frontend and backend subcharts, plus application ingress rules. |
| [charts/modelmatch-postgres](charts/modelmatch-postgres/) | PostgreSQL managed by CloudNativePG, persistent storage, and optional migration and seed jobs. |
| [charts/cluster-issuers](charts/cluster-issuers/) | Certificate issuers used by cert-manager. The home profile uses a private certificate authority. |
| [argocd/home-server/sealed](argocd/home-server/sealed/) | Encrypted application credentials that the Sealed Secrets controller decrypts inside the cluster. |
| [charts/monitoring](charts/monitoring/) | Grafana dashboards. The monitoring ArgoCD application configures Prometheus and alert rules. |
| [charts/home-server-backup](charts/home-server-backup/) | Hourly encrypted PostgreSQL exports to S3. |
| [charts/home-server-heartbeat](charts/home-server-heartbeat/) | Checks cluster alerts and public endpoints, then sends a heartbeat to an external monitor. |
| [charts/home-server-cloudflared](charts/home-server-cloudflared/) | Cloudflare Tunnel connector and its configuration. |
| [tests](tests/) | Local chart-rendering tests for hosts, image pins, storage, resource limits and migration behavior. |

New image releases use `driftplain-*` package names. The current deployment still pins
existing `modelmatch-*` images. For the first deployment of each new package, update both
`image.repository` and `image.digest`; agent references use `AGENT_IMAGE` and
`AGENT_SECURITY_IMAGE`. Verify anonymous pulls before merging a deployment change.
See the [image naming policy](https://github.com/Steve-droid/driftplain/blob/main/IMAGE-NAMING.md).
Chart names and Kubernetes resource names keep their existing `modelmatch` identifiers.

## Home-server configuration

The app moved from EKS to the home server in September 2026. Shared charts still support
the original AWS configuration; `values-home-server.yaml` files supply the home-server settings.

- Frontend and backend images come from public GHCR and are pinned by digest.
- PostgreSQL runs as one CloudNativePG instance with persistent local storage.
- Cloudflare Tunnel routes public traffic to the cluster's private NGINX ingress.
- cert-manager provides certificates for the private ingress, and Sealed Secrets manages app credentials.
- The backend uses fake model and blob clients. The hosted chat assistant reports that it is offline.

The former EKS application definitions remain in [argocd/apps](argocd/apps/) for reference.
The home-server root watches its own directory and does not deploy them. AWS-specific charts,
including the original logging setup, remain alongside the shared charts.

## Check a change locally

Install Helm and `uv`, then run:

```bash
helm lint charts/modelmatch
helm template modelmatch charts/modelmatch \
  -f charts/modelmatch/values.yaml \
  -f charts/modelmatch/values-home-server.yaml
uv run --with pyyaml --with pytest pytest tests
```

These checks render manifests and verify their configuration without connecting to a cluster.
The tests cover both the home-server profile and the retained AWS configuration.

For bootstrap and recovery, follow the infrastructure repo's
[operations guide](https://github.com/Steve-droid/driftplain-infra/blob/main/home-server/HM5-OPERATIONS.md)
and [lost-host recovery guide](https://github.com/Steve-droid/driftplain-infra/blob/main/home-server/HM5-LOST-HOST-RECOVERY.md).
