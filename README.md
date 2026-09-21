# Driftplain — GitOps

[driftplain.dev](https://driftplain.dev) · [Frontend](https://github.com/Steve-droid/driftplain-frontend) · [Backend](https://github.com/Steve-droid/driftplain-backend) · [Infra](https://github.com/Steve-droid/driftplain-infra) · **GitOps**

The desired state of everything that runs inside the **Driftplain** Kubernetes cluster — a Helm
umbrella for the app, in-repo charts for the platform pieces, and an ArgoCD app-of-apps that
reconciles them. Since **September 22, 2026** the live cluster is a single-node K3s at home
([how it got there](https://github.com/Steve-droid/driftplain-infra/blob/main/home-server/HM7-CUTOVER.md));
ArgoCD is the only thing that ever applies to it.

> Driftplain was previously Modicum / ModelMatch. Chart, image and database names keep
> `modelmatch` for compatibility.

## How a change reaches the cluster

```
app repo tag ──GitHub Actions──► ghcr.io/steve-droid/modelmatch-<image>:X.Y.Z (digest in the job summary)
                                        │
reviewed PR here: pin that digest in charts/modelmatch/values-home-server.yaml
                                        │
merge to main ──► ArgoCD home root (prune + selfHeal) ──► rollout
```

Humans author chart structure; image changes are digest bumps in a reviewed PR; nobody runs
`helm install` or `kubectl apply` for app resources by hand. Schema-changing releases run the
Alembic migration as a Job of the Postgres chart **before** the backend pin moves.

## Layout

```
argocd/home-server/     the live profile: root.yaml (app-of-apps) + apps/ — cnpg-operator, modelmatch-postgres,
                        sealed-secrets, cert-manager, nginx-ingress (ClusterIP), cluster-issuers (private CA),
                        app-secrets (sealed/), modelmatch, monitoring (+ dashboards), backup, heartbeat, cloudflared
argocd/apps/            the retired AWS EKS profile — reference render only, nothing reconciles it
charts/modelmatch/      the product umbrella: backend + frontend subcharts, host-based F5 Ingress templates,
                        values.yaml (contract) · values-home-server.yaml (GHCR digests, fake LLM/blob, no IRSA)
charts/modelmatch-postgres/   CloudNativePG Cluster + storage class + migrate/seed Jobs
charts/{cluster-issuers, app-secrets, monitoring, logging}     platform children (values-home-server.yaml where home differs)
charts/home-server-{backup, heartbeat, cloudflared}            home-only: hourly encrypted export to S3, edge heartbeat, tunnel connector
tests/                  offline render/contract tests (pytest, no cluster): home profile, public hosts, migration-only releases
scripts/recompute-host.sh   AWS-era sslip.io host recompute (unused at home)
docs/diagrams/          CNPG operator ↔ Cluster CR (draw.io)
```

## Check a change offline

```bash
helm lint charts/modelmatch
helm template modelmatch charts/modelmatch -f charts/modelmatch/values.yaml -f charts/modelmatch/values-home-server.yaml
uv run --with pyyaml --with pytest pytest tests
```

The tests assert the home profile renders exactly what is documented (digests, hosts, fake seams,
resource limits on every container, probes on `/healthz` + `/readyz`) and that the retired AWS
profile still renders unchanged.

## What the home profile fixes

- Images from **public GHCR by digest** — no registry token to expire.
- `LLM_CLIENT=fake`, `BLOB_STORE=fake`, no IRSA: the pod holds no AWS identity; the grounded chat
  answers with an honest offline note, everything else is live.
- Private hosts `app`/`api.home-server.driftplain.dev` on the `home-server-ca` issuer; the public
  pairs `driftplain.dev` (runtime) and `staging.driftplain.dev` arrive through the Cloudflare tunnel.
- The app credentials are Sealed Secrets whose sealing keys are backed up off-machine.

## Conventions

- `feature/<slice>-<description>` → PR → `main`; Conventional Commits; a SemVer tag per merged slice.
- One reviewed change per PR to the live profile; digest pins only, never `latest`.
- Every container has requests/limits; migrations never run on backend startup.

Steve Levit — stevelevit230@gmail.com
