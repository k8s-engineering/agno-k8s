"""
AgentOS — Clean Bootstrap
--------------------------

Entry point for AgentOS with dynamic agent discovery and the
``base_app`` pattern.

This file is intentionally thin (~100 lines).  All webhook handlers,
admin endpoints, and observability routes live in separate APIRouter
modules under ``app/routers/``.

Architecture:
  1. Dual-export tracing setup (DB + OTLP)
  2. Agent discovery from AGENTS_DIR
  3. Create base_app with all routers included
  4. Construct AgentOS(base_app=..., lifespan=...)
  5. app = agent_os.get_app()

See ARCHITECTURE.md for the full design rationale.
"""

import logging
import threading
from contextlib import asynccontextmanager
from os import getenv
from pathlib import Path

from agno.os import AgentOS
from fastapi import FastAPI

import app.shared as shared
from app.agent_loader import discover_agents
from app.metrics import register_gauge_provider, setup_otlp_metrics
from app.routers import admin, observability
from app.watcher import start_watcher, stop_watcher
from db import get_postgres_db

logger = logging.getLogger("agno")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AGENTS_DIR = Path(getenv("AGENTS_DIR", "/agents"))
runtime_env = getenv("RUNTIME_ENV", "prd")
scheduler_base_url = (
    "http://127.0.0.1:8000" if runtime_env == "dev" else getenv("AGENTOS_URL")
)

# ---------------------------------------------------------------------------
# Dual-export tracing: DB (AgentOS UI) + OTLP (external collector)
# ---------------------------------------------------------------------------
db = get_postgres_db()

try:
    from agno.tracing.exporter import DatabaseSpanExporter
    from openinference.instrumentation.agno import AgnoInstrumentor
    from opentelemetry import trace as trace_api
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    resource = Resource.create({
        "service.name": getenv("OTEL_SERVICE_NAME", "agno"),
    })
    tracer_provider = TracerProvider(resource=resource)

    # 1. Database exporter — keeps traces in Agno's Postgres for the UI
    db_exporter = DatabaseSpanExporter(db=db)
    tracer_provider.add_span_processor(SimpleSpanProcessor(db_exporter))

    # 2. OTLP exporter — sends traces to external collector
    otlp_endpoint = getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        otlp_exporter = OTLPSpanExporter()  # picks up env vars
        tracer_provider.add_span_processor(SimpleSpanProcessor(otlp_exporter))
        print(f"[tracing] OTLP exporter → {otlp_endpoint}")
    else:
        print("[tracing] OTEL_EXPORTER_OTLP_TRACES_ENDPOINT not set — DB-only")

    trace_api.set_tracer_provider(tracer_provider)
    AgnoInstrumentor().instrument(tracer_provider=tracer_provider)
    print("[tracing] Dual-export tracing initialised (DB + OTLP)")
except Exception as exc:
    print(f"[tracing] Failed to set up dual-export tracing: {exc}")

# ---------------------------------------------------------------------------
# Initial agent discovery
# ---------------------------------------------------------------------------
agents = discover_agents(AGENTS_DIR)

# ---------------------------------------------------------------------------
# Lifespan — all daemon threads start here, not at import time
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Start the filesystem watcher, metrics, and background workers."""
    observer = start_watcher(AGENTS_DIR, agent_os, app_instance)

    # Initialise OTLP metric exporter
    setup_otlp_metrics()

    # Register observable gauge providers
    register_gauge_provider("agents_loaded", lambda: len(agent_os.agents or []))

    # Start periodic DB size metrics collector (every 5 min)
    threading.Thread(
        target=observability.collect_db_metrics_loop, daemon=True
    ).start()

    yield
    stop_watcher(observer)


# ---------------------------------------------------------------------------
# base_app — custom FastAPI app with all routers
# ---------------------------------------------------------------------------
base_app = FastAPI(title="AgentOS")
base_app.include_router(admin.router)
base_app.include_router(observability.router)

# ---------------------------------------------------------------------------
# AgentOS — uses base_app pattern + constructor lifespan
# ---------------------------------------------------------------------------
agent_os = AgentOS(
    name="AgentOS",
    tracing=True,
    scheduler=True,
    scheduler_base_url=scheduler_base_url,
    db=db,
    agents=agents,
    base_app=base_app,
    lifespan=lifespan,
    on_route_conflict="preserve_base_app",
    config=str(Path(__file__).parent / "config.yaml"),
)

# Set shared reference so routers can access agent_os
shared.agent_os = agent_os

# ---------------------------------------------------------------------------
# Build the final FastAPI app
# ---------------------------------------------------------------------------
app = agent_os.get_app()
