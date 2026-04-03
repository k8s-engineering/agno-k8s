"""
Admin Router
------------

Administrative endpoints for AgentOS operations.
"""

import logging
import threading
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import shared
from app.agent_loader import discover_agents
from app.watcher import AGENTOS_PREFIXES

logger = logging.getLogger("agno.admin")

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/reload")
async def admin_reload():
    """Re-scan AGENTS_DIR and resync AgentOS (routes + registry)."""
    from os import getenv

    agents_dir = Path(getenv("AGENTS_DIR", "/agents"))
    new_agents = discover_agents(agents_dir)

    if shared.agent_os is None:
        return JSONResponse(status_code=503, content={"error": "AgentOS not initialised"})

    shared.agent_os.agents = new_agents

    # Snapshot custom routes before resync wipes them
    app = shared.agent_os.get_app()
    custom_routes = [
        route for route in app.router.routes
        if hasattr(route, "path")
        and not any(route.path.startswith(p) for p in AGENTOS_PREFIXES)
    ]

    shared.agent_os.resync(app=app)

    # Restore custom routes
    existing_paths = {r.path for r in app.router.routes if hasattr(r, "path")}
    for route in custom_routes:
        if route.path not in existing_paths:
            app.router.routes.append(route)

    names = [a.name or a.id for a in new_agents]
    return JSONResponse({"status": "reloaded", "agents": names})
