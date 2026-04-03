# AI Agent Instructions

This document provides context for AI coding assistants working in this repository.

## Repository Purpose

This is a **public reference implementation** showing how to deploy [Agno AgentOS](https://docs.agno.com/agent-os) on Kubernetes. It contains:

- The AgentOS container image source (`agentos/`)
- A feature-flag Helm chart (`helm/agentos/`)
- CI/CD pipelines for both artifacts (`.github/workflows/`)
- Architecture documentation (`docs/`)

## Key Architecture Documents

- **[docs/agno-architecture.md](docs/agno-architecture.md)** — Agno framework patterns (base_app, lifespan, route snapshot/restore, daemon threads)
- **[docs/kubernetes-deployment.md](docs/kubernetes-deployment.md)** — K8s deployment model (git-sync, external secrets, Istio, init-db)

## Architectural Constraints

1. **No agent code in this repo** — agents are plain Python files delivered via git-sync from a separate repository
2. **No per-agent Helm charts** — all agents share a single AgentOS deployment
3. **`main.py` is bootstrap only** (~120 lines) — all webhook/API logic lives in `app/routers/` as `APIRouter` modules
4. **Route snapshot/restore is required** — `AgentOS.resync()` unconditionally wipes routes; the watcher preserves custom routes via an exclude-list filter
5. **Daemon threads for persistent workers** — long-running workers use `threading.Thread(daemon=True)` started inside the lifespan, not at import time
6. **`base_app` pattern** — custom FastAPI app is passed to `AgentOS(base_app=app)` constructor
7. **Constructor lifespan** — lifespan function passed to `AgentOS()` constructor, not monkey-patched

## Build & CI

- **Docker image**: triggered by changes to `agentos/**`, pushes to GHCR with semantic version tags
- **Helm chart**: triggered by changes to `helm/**`, pushes OCI artifact to GHCR

Both use `major.minor.patch` versioning starting from `1.0.0`, auto-incrementing patch on each change.

## Development Notes

- Base image: `agnohq/python:3.12`
- Non-root user: UID/GID 61000
- Python path: `/app`
- Agents directory: `/agents` (mounted dynamically in K8s)
- Dependencies managed via `uv pip sync` with pinned `requirements.txt`
