# Driftplain gitops

[driftplain.dev](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [Infra](https://github.com/Steve-droid/driftplain-infra) · **GitOps**

Driftplain picks a cheaper LLM for code review from benchmark data and runs it in the user's CI
on the user's own API key. There are two agents. The review agent makes one API call with the PR
diff and the user's review preferences. The security agent runs an agentic loop with OpenCode
over the checkout and reports vulnerabilities. The dashboard shows the money saved while review
quality holds.

This repo is the desired state of everything inside the Kubernetes cluster: a Helm umbrella for
the app, in-repo charts for the platform pieces, and an ArgoCD app-of-apps that reconciles them.
Since September 22, 2026 the live cluster is a single-node K3s on a home Ubuntu server
([how it got there](https://github.com/Steve-droid/driftplain-infra/blob/main/home-server/HM7-CUTOVER.md)).
ArgoCD is the only thing that applies to it.

## How a change reaches the cluster

1. An app repo pushes a tag. GitHub Actions builds the image and pushes it to
   `ghcr.io/steve-droid/modelmatch-<image>:X.Y.Z`. The job summary prints the digest.
2. A PR here pins that digest in `charts/modelmatch/values-home-server.yaml`.
3. On merge, the ArgoCD home root (prune and selfHeal) rolls it out.

Humans author chart structure. Image changes are digest bumps in a reviewed PR. Nobody runs
`helm install` or `kubectl apply` for app resources by hand. A release with a schema change runs
the Alembic migration as a Job of the Postgres chart before the backend pin moves.

## Layout

```
argocd/home-server/     the live profile: root.yaml (app-of-apps) and apps/ for cnpg-operator, modelmatch-postgres,
                        sealed-secrets, cert-manager, nginx-ingress (ClusterIP), cluster-issuers (private CA),
                        app-secrets (sealed), modelmatch, monitoring and dashboards, backup, heartbeat, cloudflared
argocd/apps/            the retired AWS EKS profile, kept as a reference render
charts/modelmatch/      the product umbrella: backend and frontend subcharts, host-based Ingress templates,
                        values.yaml (contract) and values-home-server.yaml (GHCR digests, fake LLM and blob, no IRSA)
charts/modelmatch-postgres/   CloudNativePG Cluster, storage class, migrate and seed Jobs
charts/{cluster-issuers, app-secrets, monitoring, logging}     platform children
charts/home-server-{backup, heartbeat, cloudflared}            home only: hourly encrypted export to S3, edge heartbeat, tunnel connector
tests/                  offline render and contract tests (pytest, no cluster)
scripts/recompute-host.sh   AWS-era sslip.io host recompute, unused on the home server
docs/diagrams/          CNPG operator and Cluster CR (draw.io)
```

## Check a change offline

```bash
helm lint charts/modelmatch
helm template modelmatch charts/modelmatch -f charts/modelmatch/values.yaml -f charts/modelmatch/values-home-server.yaml
uv run --with pyyaml --with pytest pytest tests
```

The tests assert that the home profile renders what is documented (digests, hosts, fake seams,
limits on every container, probes on `/healthz` and `/readyz`) and that the retired AWS profile
still renders unchanged.

## What the home profile changes

- Images come from public GHCR by digest, so no registry token can expire.
- `LLM_CLIENT=fake` and `BLOB_STORE=fake`, no IRSA. The pod holds no AWS identity. The chat
  answers that the assistant is offline; everything else is live.
- Private hosts `app.home-server.driftplain.dev` and `api.home-server.driftplain.dev` use the
  `home-server-ca` issuer. The public `driftplain.dev` and `staging.driftplain.dev` pairs arrive
  through the Cloudflare tunnel.
- App credentials are Sealed Secrets. The sealing keys are backed up off the machine.

## Conventions

- `feature/<slice>-<description>`, then a PR to `main`. Conventional Commits. A SemVer tag per merged slice.
- One reviewed change per PR to the live profile. Digest pins only, never `latest`.
- Every container has requests and limits. Migrations never run on backend startup.

Steve Levit, stevelevit230@gmail.com
