# AgentOS on Kubernetes

A production-grade reference implementation for deploying [Agno AgentOS](https://docs.agno.com/agent-os) on Kubernetes.

## Overview

This project demonstrates how to run AgentOS in a production Kubernetes environment using Agno's native patterns:

- **`base_app` pattern** — custom FastAPI app passed to AgentOS constructor
- **Constructor lifespan** — startup/shutdown logic composed with framework lifespans
- **Dynamic agent loading** — agents discovered at runtime from a git-sync volume
- **Route snapshot/restore** — custom routes preserved through AgentOS resync cycles

## System Overview

```mermaid
graph TB
    subgraph "Kubernetes Cluster"
        subgraph "AgentOS Pod"
            AOS["AgentOS<br/>FastAPI Server<br/>:8000"]
            GS["git-sync<br/>sidecar"]
            VOL[("/agents<br/>shared volume")]
            GS -->|writes| VOL
            VOL -->|reads| AOS
        end

        SVC["ClusterIP Service<br/>agentos:8000"]
        SVC --> AOS

        subgraph "Data Layer"
            PG[("PostgreSQL<br/>Sessions / RAG / Traces")]
        end

        AOS -->|read/write| PG
    end

    GIT["GitHub Repo<br/>(agent code)"]
    GIT -->|pull| GS

    OTLP["OTLP Collector<br/>(Grafana / Datadog / etc.)"]
    AOS -->|traces & metrics| OTLP

    ISTIO["Istio Gateway<br/>(optional)"]
    ISTIO --> SVC

    style AOS fill:#4051b5,color:#fff
    style PG fill:#336791,color:#fff
    style GIT fill:#24292e,color:#fff
    style OTLP fill:#f5a623,color:#fff
```

## How Agent Code Gets to the Pod

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub
    participant GSync as git-sync sidecar
    participant Vol as Shared Volume
    participant AOS as AgentOS

    Dev->>GH: git push (agent code)
    loop Every 30s
        GSync->>GH: git fetch
        GSync->>Vol: Create new worktree
        GSync->>Vol: Atomic symlink swap
    end
    Vol-->>AOS: Filesystem event
    AOS->>AOS: Snapshot custom routes
    AOS->>AOS: Re-discover agents (importlib)
    AOS->>AOS: resync() + restore routes
    AOS-->>Dev: New agents live (no redeploy)
```

## Quick Links

- [Agno Architecture](agno-architecture.md) — framework patterns and design decisions
- [Kubernetes Deployment](kubernetes-deployment.md) — deployment model, scaling, and operations
- [GitHub Repository](https://github.com/k8s-engineering/agno-k8s)
- [Agno Documentation](https://docs.agno.com) — official Agno framework docs
