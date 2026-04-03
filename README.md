# AgentOS on Kubernetes

A production-grade reference implementation for deploying [Agno AgentOS](https://docs.agno.com/agent-os) on Kubernetes.

> **Documentation**: [k8s-engineering.github.io/agno-k8s](https://k8s-engineering.github.io/agno-k8s) | **Agno Docs**: [docs.agno.com](https://docs.agno.com)

## What is AgentOS?

AgentOS is a FastAPI-based runtime for managing AI agents built with the [Agno](https://github.com/agno-agi/agno) framework. This repository demonstrates how to deploy it on Kubernetes with:

- **Dynamic agent loading** — agents are plain Python files delivered via git-sync, not baked into the container image
- **Hot-reload** — filesystem watcher detects agent code changes and resyncs routes without pod restarts
- **Dual-export tracing** — OpenTelemetry spans sent to both a local Postgres DB (Agno UI) and an external OTLP collector
- **Feature-flag Helm chart** — toggle Istio, git-sync, DB init, and external secrets via simple `true/false` values
- **Semantic versioning** — both the Docker image and Helm chart are auto-versioned on every code change

## What Changed from the Base AgentOS Template

The upstream [agno-agi/agentos-docker-template](https://github.com/agno-agi/agentos-docker-template) is a single-file starter. This repo extends it for Kubernetes with the following changes:

### Application Architecture

| Change | Upstream Template | This Repo |
|--------|-------------------|-----------|
| **Entry point** | `main.py` (~43 lines), statically imports agents | `main.py` (~120 lines), uses `base_app` pattern with `APIRouter` modules |
| **Agent loading** | Agents baked into the image at build time | Dynamic discovery from `/agents` via `importlib` at runtime |
| **Hot-reload** | None — requires image rebuild + redeploy | `watcher.py` detects git-sync symlink swaps, triggers `resync()` with route snapshot/restore |
| **Lifespan** | Default AgentOS lifespan | Custom lifespan passed to `AgentOS()` constructor — starts filesystem watcher, metrics collector, and daemon threads |
| **Custom routes** | None | `routers/admin.py` (reload endpoint), `routers/observability.py` (metrics, DB size tracking) |
| **Shared state** | Not needed | `shared.py` — cross-cutting references (agent_os instance) accessible to all routers |
| **Route conflict** | Not applicable | `on_route_conflict="preserve_base_app"` protects custom routes during AgentOS initialization |

### Observability

| Change | Upstream Template | This Repo |
|--------|-------------------|-----------|
| **Tracing** | Single DB exporter | Dual-export: DB (Agno UI) + OTLP (external collector) via shared `TracerProvider` |
| **Metrics** | None | `metrics.py` — OTel counters, histograms, gauges for agent runs, webhook throughput, DB table sizes |
| **OTLP toggle** | Not applicable | Enabled when `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set; zero-cost no-ops when unset |

### Kubernetes-Specific

| Change | Upstream Template | This Repo |
|--------|-------------------|-----------|
| **Container image** | `docker-compose` for local dev | Multi-stage Dockerfile optimized for K8s (non-root UID 61000, DB readiness wait in entrypoint) |
| **Agent delivery** | Copied into image via `COPY` | git-sync sidecar pulls from Git repo into shared `emptyDir` volume |
| **Configuration** | `example.env` + Docker Compose env | Helm `values.yaml` with feature flags (`istio`, `initDb`, `gitSync`, `externalSecrets`) |
| **Secrets** | `.env` file | External Secrets Operator (Keeper, AWS SSM, Vault) or manual K8s Secrets |
| **DB initialization** | Manual | Helm pre-install Job creates database, user, schema, pgvector extension |
| **Health probes** | Built-in `/health` endpoint | Configured as K8s liveness/readiness probes in the Deployment |
| **Ingress** | Not applicable | Optional Istio VirtualService with configurable gateway and timeout |
| **Dev tooling** | Docker Compose, build/format scripts | Removed in favor of Helm-only deployment; CI/CD via GitHub Actions |

## Repository Structure

```
agno-k8s/
├── agentos/                    # AgentOS container image
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/
│       ├── app/                # Application code
│       │   ├── main.py         # Bootstrap (~120 lines, base_app pattern)
│       │   ├── shared.py       # Cross-cutting state
│       │   ├── agent_loader.py # Dynamic agent discovery via importlib
│       │   ├── watcher.py      # Filesystem watcher + symlink poller
│       │   ├── metrics.py      # OTel metrics (counters, histograms, gauges)
│       │   └── routers/        # FastAPI APIRouter modules
│       │       ├── admin.py
│       │       └── observability.py
│       ├── db/                 # Database utilities
│       │   ├── session.py      # PostgresDb + Knowledge factories
│       │   └── url.py          # URL builder from env vars
│       └── scripts/
│           └── entrypoint.sh   # Container entrypoint (DB wait, banner)
├── helm/
│   └── agentos/                # Helm chart
│       ├── Chart.yaml
│       ├── values.yaml         # Feature flags + defaults
│       └── templates/          # K8s manifests
├── docs/                       # GitHub Pages documentation
│   ├── agno-architecture.md    # Agno framework patterns
│   └── kubernetes-deployment.md # K8s deployment model
├── .github/workflows/
│   ├── build-agentos.yml       # Docker image CI (semantic versioning)
│   └── build-helm.yml          # Helm chart CI (OCI artifact)
└── AGENTS.md                   # AI agent instructions
```

## Quick Start

### Install the Helm chart

```bash
# Add the OCI registry
helm pull oci://ghcr.io/k8s-engineering/agno-k8s/charts/agentos --version 1.0.0

# Install with your values
helm install agentos ./agentos -n agno --create-namespace \
  -f my-values.yaml
```

### Minimal values.yaml

```yaml
database:
  host: "your-postgres-host.example.com"
  port: 5432
  name: agno
  appUser: agno

gitSync:
  enabled: true
  repo: "https://github.com/your-org/your-agents.git"
  branch: main
  subPath: "agents"
  auth:
    secretName: git-credentials
    secretKey: token
```

## Feature Flags

| Flag | Default | Description |
|------|---------|-------------|
| `istio.enabled` | `false` | Create Istio VirtualService for ingress routing |
| `initDb.enabled` | `false` | Pre-install Job to create database, user, and pgvector extension |
| `gitSync.enabled` | `false` | Sidecar container pulling agent code from Git |
| `externalSecrets.enabled` | `false` | External Secrets Operator integration (Keeper, AWS SSM, Vault, etc.) |

## Architecture

This implementation follows Agno's recommended patterns:

1. **`base_app` pattern** — custom FastAPI app passed to `AgentOS(base_app=app)` to cleanly merge custom routes with AgentOS routes
2. **Constructor lifespan** — startup/shutdown logic passed to `AgentOS()` constructor (not monkey-patched)
3. **Router decomposition** — webhook handlers in separate `APIRouter` modules, keeping `main.py` thin
4. **Daemon threads in lifespan** — persistent workers start only when the app is serving, not at import time

See [docs/agno-architecture.md](docs/agno-architecture.md) for the full design rationale.

## CI/CD

Both the Docker image and Helm chart use semantic versioning (`major.minor.patch`) starting from `1.0.0`, auto-incrementing the patch version on every code change to `main`.

| Artifact | Trigger | Registry |
|----------|---------|----------|
| Docker image | `agentos/**` changes | `ghcr.io/k8s-engineering/agno-k8s/agentos` |
| Helm chart | `helm/**` changes | `oci://ghcr.io/k8s-engineering/agno-k8s/charts` |

## Documentation

- **[Full Documentation](https://k8s-engineering.github.io/agno-k8s)** — hosted on GitHub Pages (architecture, deployment guide, operations)
- [Agno Architecture](docs/agno-architecture.md) — framework patterns and design decisions
- [Kubernetes Deployment](docs/kubernetes-deployment.md) — deployment model, scaling, and operations

### External References

- [Agno Documentation](https://docs.agno.com) — official Agno framework docs
- [AgentOS Overview](https://docs.agno.com/agent-os) — AgentOS runtime documentation
- [Custom FastAPI (base_app)](https://docs.agno.com/agent-os/custom-fastapi/overview) — `base_app` pattern reference
- [Agno GitHub](https://github.com/agno-agi/agno) — Agno framework source code
- [AgentOS Docker Template](https://github.com/agno-agi/agentos-docker-template) — upstream starter template

## License

[MIT](LICENSE)
