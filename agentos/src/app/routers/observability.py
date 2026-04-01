"""
Observability Router
--------------------

Debug metrics endpoint and background DB metrics collector.

Primary metric delivery is via OTLP push to the configured collector.
The /api/metrics endpoint is for ad-hoc inspection only.
"""

import logging
import time
from os import getenv

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.metrics import get_metrics_summary, update_db_table_metric

logger = logging.getLogger("agno.observability")

router = APIRouter(prefix="/api", tags=["Observability"])


@router.get("/metrics")
async def debug_metrics():
    """JSON debug endpoint showing current observable gauge values."""
    return JSONResponse(content=get_metrics_summary())


def collect_db_metrics_loop():
    """Background thread: collect Postgres table sizes every 5 minutes."""
    import sqlalchemy

    raw_url = getenv("POSTGRES_URL", "").replace("+psycopg", "")
    if not raw_url:
        logger.warning("[metrics] POSTGRES_URL not set — skipping DB metrics")
        return

    engine = sqlalchemy.create_engine(raw_url, pool_pre_ping=True)
    while True:
        try:
            with engine.connect() as conn:
                rows = conn.execute(sqlalchemy.text(
                    "SELECT schemaname, relname, "
                    "pg_total_relation_size(schemaname || '.' || relname) AS total_bytes, "
                    "n_live_tup "
                    "FROM pg_stat_user_tables "
                    "WHERE schemaname IN ('ai', 'public', 'agno') "
                    "ORDER BY total_bytes DESC LIMIT 30"
                )).fetchall()
                for schema, table, total_bytes, row_count in rows:
                    update_db_table_metric(schema, table, total_bytes, row_count)
        except Exception as exc:
            logger.debug("[metrics] DB metrics collection failed: %s", exc)
        time.sleep(300)  # 5 minutes
