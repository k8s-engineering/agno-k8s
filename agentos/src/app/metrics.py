"""
Application Metrics — Pure OpenTelemetry SDK
----------------------------------------------

Custom metrics for the AgentOS application, pushed via OTLP to an
external collector (e.g. Coralogix, Grafana, Datadog).

Instruments are created at import time as OTel API proxies.  When
``setup_otlp_metrics()`` installs a real ``MeterProvider`` (with the
OTLP gRPC exporter), the proxies begin forwarding to the collector.

When observability is disabled (OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
not set), all instruments remain no-ops — zero runtime cost.

Metrics are organised into four groups:

  - **Webhook** — request counts, durations, queue depths
  - **Agent** — run counts, durations, outcomes
  - **System** — loaded agents, DB tables
  - **Dedup** — alert dedup hit/miss rates

All metric names use dotted ``agno.*`` convention.
"""

import logging
from os import getenv

from opentelemetry import metrics

logger = logging.getLogger("agno.metrics")

# ---------------------------------------------------------------------------
# Meter — instruments are API-level proxies until a MeterProvider is set.
# ---------------------------------------------------------------------------
meter = metrics.get_meter("agno.metrics", version="1.0.0")

# ---------------------------------------------------------------------------
# Counters (monotonic)
# ---------------------------------------------------------------------------
WEBHOOK_REQUESTS = meter.create_counter(
    name="agno.webhook.requests",
    description="Total webhook requests received",
    unit="1",
)

AGENT_RUNS = meter.create_counter(
    name="agno.agent.runs",
    description="Total agent runs completed",
    unit="1",
)

DEDUP_DECISIONS = meter.create_counter(
    name="agno.dedup.decisions",
    description="Dedup decisions (hit/miss)",
    unit="1",
)

# ---------------------------------------------------------------------------
# Histograms
# ---------------------------------------------------------------------------
WEBHOOK_DURATION = meter.create_histogram(
    name="agno.webhook.duration",
    description="Webhook request processing time (acceptance, not agent run)",
    unit="s",
)

AGENT_DURATION = meter.create_histogram(
    name="agno.agent.duration",
    description="Agent run duration",
    unit="s",
)

# ---------------------------------------------------------------------------
# UpDownCounters (non-monotonic — can increment and decrement)
# ---------------------------------------------------------------------------
AGENT_IN_PROGRESS = meter.create_up_down_counter(
    name="agno.agent.in_progress",
    description="Number of agent runs currently executing",
    unit="1",
)

# ---------------------------------------------------------------------------
# Observable gauge providers — main.py registers callables that return
# current values; the OTel SDK invokes them at each export interval.
# ---------------------------------------------------------------------------
_gauge_providers: dict[str, callable] = {}


def register_gauge_provider(key: str, provider_fn: callable) -> None:
    """Register a zero-arg callable returning the current value for *key*.

    Called from ``main.py`` during lifespan startup to wire live app state
    (queue sizes, history lengths, etc.) into the OTel observable gauges.
    """
    _gauge_providers[key] = provider_fn


def _safe_observe(key: str):
    """Call a registered provider and return its int value, or 0."""
    fn = _gauge_providers.get(key)
    if fn is None:
        return 0
    try:
        return int(fn())
    except Exception:
        return 0


# --- Observable Gauge callbacks (called by OTel SDK at export time) --------

def _observe_agents_loaded(options):
    yield metrics.Observation(_safe_observe("agents_loaded"))


def _observe_queue_depth(options):
    for key in list(_gauge_providers.keys()):
        if key.startswith("queue_"):
            queue_name = key[len("queue_"):]
            yield metrics.Observation(_safe_observe(key), {"queue": queue_name})


# DB table metrics — populated by a background collector thread
_db_table_cache: dict[tuple[str, str], tuple[int, int]] = {}


def update_db_table_metric(schema: str, table: str, size_bytes: int, row_count: int) -> None:
    """Cache DB table size/rows (called from the background collector thread)."""
    _db_table_cache[(schema, table)] = (size_bytes, row_count)


def _observe_db_table_size(options):
    for (schema, table), (size_bytes, _) in _db_table_cache.items():
        yield metrics.Observation(size_bytes, {"schema": schema, "table": table})


def _observe_db_table_rows(options):
    for (schema, table), (_, row_count) in _db_table_cache.items():
        yield metrics.Observation(row_count, {"schema": schema, "table": table})


# --- Register Observable Gauges -------------------------------------------

meter.create_observable_gauge(
    name="agno.agents.loaded",
    callbacks=[_observe_agents_loaded],
    description="Number of agents currently loaded",
    unit="1",
)

meter.create_observable_gauge(
    name="agno.queue.depth",
    callbacks=[_observe_queue_depth],
    description="Current depth of webhook processing queues",
    unit="1",
)

meter.create_observable_gauge(
    name="agno.db.table.size",
    callbacks=[_observe_db_table_size],
    description="Postgres table size in bytes (updated periodically)",
    unit="By",
)

meter.create_observable_gauge(
    name="agno.db.table.rows",
    callbacks=[_observe_db_table_rows],
    description="Approximate row count for key tables",
    unit="1",
)


# ---------------------------------------------------------------------------
# OTLP Metrics Exporter setup
# ---------------------------------------------------------------------------
def setup_otlp_metrics() -> None:
    """Initialise the OTLP gRPC metric exporter.

    Reads ``OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`` from env.  If not set,
    all instruments remain no-ops (no data exported).
    """
    metrics_endpoint = getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
    if not metrics_endpoint:
        logger.info("[metrics] OTEL_EXPORTER_OTLP_METRICS_ENDPOINT not set — metrics disabled")
        return

    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": getenv("OTEL_SERVICE_NAME", "agno"),
        })

        exporter = OTLPMetricExporter(
            endpoint=metrics_endpoint,
            insecure=True,  # in-cluster, no TLS needed
        )
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=30_000)
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)
        logger.info("[metrics] OTLP gRPC metrics exporter → %s (30s interval)", metrics_endpoint)
    except Exception as exc:
        logger.warning("[metrics] Failed to set up OTLP metrics: %s", exc)


def get_metrics_summary() -> dict:
    """Return a JSON-friendly summary of current observable gauge state for debugging."""
    db_tables = {}
    for (schema, table), (size_bytes, row_count) in _db_table_cache.items():
        db_tables[f"{schema}.{table}"] = {"size_bytes": size_bytes, "row_count": row_count}

    queues = {}
    for key in list(_gauge_providers.keys()):
        if key.startswith("queue_"):
            queues[key[len("queue_"):]] = _safe_observe(key)

    return {
        "agents_loaded": _safe_observe("agents_loaded"),
        "queues": queues,
        "db_tables": db_tables,
    }
