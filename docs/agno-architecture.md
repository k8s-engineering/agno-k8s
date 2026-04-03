# Agno Architecture

This document describes the Agno AgentOS framework patterns used in this deployment and the architectural decisions behind them.

## Overview

[AgentOS](https://docs.agno.com/agent-os) is a FastAPI-based runtime for managing AI agents built with the [Agno](https://github.com/agno-agi/agno) framework. It provides:

- Agent lifecycle management (load, register, serve via API)
- Session and knowledge management (PostgreSQL-backed)
- MCP (Model Context Protocol) tool integration
- Health checks, scheduling, and auto-discovery

This repository implements AgentOS following Agno's recommended patterns rather than the monolithic approach that often evolves in POC deployments.

## Application Bootstrap Flow

```mermaid
flowchart LR
    A["Dual-export<br/>Tracing Setup"] --> B["Discover Agents<br/>from /agents"]
    B --> C["Create base_app<br/>+ include routers"]
    C --> D["Construct AgentOS<br/>(base_app, lifespan)"]
    D --> E["agent_os.get_app()"]
    E --> F["Uvicorn serves app"]

    style A fill:#e8eaf6,stroke:#3949ab
    style D fill:#4051b5,color:#fff
    style F fill:#43a047,color:#fff
```

## Four Pillars

### 1. `base_app` Pattern

AgentOS accepts a `base_app` parameter — a custom FastAPI application with your own routes. This cleanly separates your webhook handlers and admin endpoints from AgentOS's internal routes.

```python
base_app = FastAPI(title="AgentOS")
base_app.include_router(admin.router)
base_app.include_router(observability.router)

agent_os = AgentOS(
    agents=agents,
    base_app=base_app,
    on_route_conflict="preserve_base_app",
)
app = agent_os.get_app()
```

**Why `on_route_conflict="preserve_base_app"`**: Your routes (`/api/*`, `/admin/*`) don't overlap with AgentOS routes (`/agents/*`, `/sessions/*`, `/knowledge/*`, `/health`), so the conflict handler rarely fires. Setting it explicitly documents intent and protects against future AgentOS releases adding overlapping routes.

```mermaid
graph LR
    subgraph "base_app (FastAPI)"
        R1["/admin/reload"]
        R2["/api/metrics"]
    end

    subgraph "AgentOS Routes"
        R3["/agents/*"]
        R4["/sessions/*"]
        R5["/knowledge/*"]
        R6["/health"]
    end

    BA["base_app"] -->|passed to| AOS["AgentOS(base_app=...)"]
    AOS -->|merges routes| APP["Final App"]

    style BA fill:#e8eaf6,stroke:#3949ab
    style AOS fill:#4051b5,color:#fff
    style APP fill:#43a047,color:#fff
```

### 2. Constructor Lifespan

Startup and shutdown logic is passed to the `AgentOS()` constructor via the `lifespan` parameter — not monkey-patched after construction.

```python
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    observer = start_watcher(AGENTS_DIR, agent_os, app_instance)
    setup_otlp_metrics()
    # ... start daemon threads ...
    yield
    stop_watcher(observer)

agent_os = AgentOS(
    lifespan=lifespan,
    # ...
)
```

When `lifespan` is passed at construction, `get_app()` calls `_add_agent_os_to_lifespan_function()` which inspects the function signature. If your lifespan accepts an `agent_os` parameter, the framework injects the `AgentOS` instance automatically.

**Lifespan composition order** (from the `base_app` branch of `get_app()`):

1. Your lifespan (startup)
2. DB lifespan
3. MCP tools lifespan
4. httpx cleanup
5. Scheduler lifespan
6. *yield* (app is serving)
7. Scheduler shutdown
8. httpx cleanup
9. MCP tools cleanup
10. DB cleanup
11. Your lifespan (shutdown)

Your lifespan wraps everything — it starts before framework resources initialize and stops after they close.

```mermaid
sequenceDiagram
    participant YL as Your Lifespan
    participant DB as DB Lifespan
    participant MCP as MCP Tools
    participant SCHED as Scheduler
    participant APP as App Serving

    Note over YL,APP: Startup (top to bottom)
    YL->>YL: Start watcher, metrics, workers
    DB->>DB: Init tables, connections
    MCP->>MCP: Register MCP tools
    SCHED->>SCHED: Start scheduler poller
    APP->>APP: Serving requests...
    Note over YL,APP: Shutdown (bottom to top)
    SCHED->>SCHED: Stop scheduler
    MCP->>MCP: Cleanup tools
    DB->>DB: Close connections
    YL->>YL: Stop watcher
```

### 3. Dynamic Agent Loader + Watcher

Agents are **not baked into the container image**. They are plain Python files delivered via git-sync and discovered at runtime using `importlib`.

```
/agents/
├── my_agent.py          # Agent module
├── another_agent.py     # Another agent
├── helpers/
│   ├── __init__.py      # Package with agents
│   └── utils.py
└── .env                 # Shared secrets (loaded before agent import)
```

The `agent_loader.py` module:
1. Loads `.env` from the agents directory (if present)
2. Scans for `*.py` files (skipping `_`-prefixed names)
3. Imports each as a module, collecting top-level `Agent` instances
4. Reloads previously-imported modules so code changes take effect

The `watcher.py` module uses `watchdog` to monitor the agents directory:
- **Debounced reload** (2 seconds) prevents rapid-fire reloads during git-sync writes
- **Symlink poller** detects git-sync worktree swaps that inotify cannot see

#### Route Snapshot/Restore

**Critical invariant**: `AgentOS.resync()` unconditionally clears all routes in `_reprovision_routers()`. Using `base_app` alone does NOT fix this — the route-wiping happens regardless.

The watcher snapshots custom routes before resync and restores them after, using an **exclude-list** of AgentOS-owned prefixes:

```python
AGENTOS_PREFIXES = (
    "/agents/", "/sessions/", "/knowledge/",
    "/health", "/docs", "/openapi.json", "/mcp",
)

# Snapshot: everything NOT matching these prefixes is custom
custom_routes = [
    route for route in app.router.routes
    if hasattr(route, "path")
    and not any(route.path.startswith(p) for p in AGENTOS_PREFIXES)
]
```

This is more defensive than an include-list — new custom routes are automatically preserved without filter updates.

```mermaid
flowchart TD
    FS["Filesystem Event<br/>(symlink swap)"] --> DEB["Debounce<br/>2 seconds"]
    DEB --> SNAP["Snapshot custom routes"]
    SNAP --> DISC["Re-discover agents<br/>importlib reload"]
    DISC --> SYNC["agent_os.resync()<br/>(wipes all routes)"]
    SYNC --> REST["Restore custom routes"]
    REST --> LIVE["New agents live"]

    style FS fill:#ff9800,color:#fff
    style SYNC fill:#d32f2f,color:#fff
    style LIVE fill:#43a047,color:#fff
```

### 4. Daemon Threads in Lifespan

Long-running persistent workers (triage queues, metrics collectors, etc.) use `threading.Thread(daemon=True)` started **inside the lifespan**, not at import time.

```python
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # Workers start only when the app is serving
    threading.Thread(target=collect_db_metrics_loop, daemon=True).start()
    yield
```

**Why daemon threads over asyncio tasks**: Workers that call `agent.arun()` need their own event loops because MCP tools use blocking SSE transports. Running these on the main uvicorn loop would block it. The thread-per-worker model with disposable event loops is the correct pattern.

**Why not import-time**: Starting workers at import time means importing `main.py` in tests spawns threads. Moving them to the lifespan eliminates import-time side effects.

## Tracing Architecture

AgentOS uses dual-export tracing:

1. **Database exporter** — `DatabaseSpanExporter` writes spans to PostgreSQL for the Agno UI
2. **OTLP exporter** — sends spans to an external collector (Phoenix, Grafana, Datadog, etc.)

Both exporters are wired into the same `TracerProvider` via `SimpleSpanProcessor`, so every span goes to both destinations.

```mermaid
graph LR
    AGENT["Agent Run"] --> TP["TracerProvider"]
    TP --> SSP1["SimpleSpanProcessor"]
    TP --> SSP2["SimpleSpanProcessor"]
    SSP1 --> DBE["DatabaseSpanExporter<br/>(PostgreSQL → Agno UI)"]
    SSP2 --> OTLP["OTLPSpanExporter<br/>(External Collector)"]

    style AGENT fill:#4051b5,color:#fff
    style DBE fill:#336791,color:#fff
    style OTLP fill:#f5a623,color:#fff
```

## Agent Packaging Model

- **No per-agent Helm charts** — all agents share a single AgentOS deployment
- **No agent code in the container image** — agents live in a separate Git repository
- **git-sync sidecar** delivers agent code to `/agents` via an `emptyDir` shared volume
- **Hot-reload** picks up changes within seconds of a git push

This means you can update agent behavior (prompts, tools, logic) without rebuilding or redeploying the AgentOS container.

## Metrics

The `metrics.py` module defines pure OpenTelemetry SDK instruments:

| Type | Name | Description |
|------|------|-------------|
| Counter | `agno.webhook.requests` | Total webhook requests |
| Counter | `agno.agent.runs` | Total agent runs |
| Counter | `agno.dedup.decisions` | Dedup hit/miss |
| Histogram | `agno.webhook.duration` | Webhook processing time |
| Histogram | `agno.agent.duration` | Agent run duration |
| UpDownCounter | `agno.agent.in_progress` | Active agent runs |
| Observable Gauge | `agno.agents.loaded` | Loaded agent count |
| Observable Gauge | `agno.queue.depth` | Queue depths |
| Observable Gauge | `agno.db.table.size` | Postgres table sizes |
| Observable Gauge | `agno.db.table.rows` | Approximate row counts |

When `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` is set, metrics are pushed via OTLP gRPC every 30 seconds. When unset, all instruments are no-ops with zero runtime cost.
