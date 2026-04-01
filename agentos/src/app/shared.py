"""
Shared State
------------

Cross-cutting dependencies shared by multiple routers.

This module exists to avoid circular imports between router modules
that need access to the same state (agent_os reference, webhook auth,
agent lookup, shared queues).

The ``agent_os`` reference is set post-construction by ``main.py``.
"""

import hmac
import logging
from os import getenv
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agno.agent import Agent
    from agno.os import AgentOS

logger = logging.getLogger("agno.shared")

# ---------------------------------------------------------------------------
# AgentOS reference — set by main.py after construction
# ---------------------------------------------------------------------------
agent_os: Optional["AgentOS"] = None

# ---------------------------------------------------------------------------
# Webhook authentication
# ---------------------------------------------------------------------------
WEBHOOK_SECRET = getenv("WEBHOOK_SECRET", "")


def verify_webhook_token(authorization: str | None, x_api_key: str | None = None) -> bool:
    """Validate auth against WEBHOOK_SECRET.

    Accepts either:
      - Authorization: Bearer <token>  (standard)
      - x-api-key: <token>             (Jira automation "Send web request")
    """
    if not WEBHOOK_SECRET:
        # No secret configured — allow all (dev mode)
        return True
    # Try x-api-key first (Jira automation style)
    if x_api_key and hmac.compare_digest(x_api_key, WEBHOOK_SECRET):
        return True
    # Fall back to Authorization: Bearer <token>
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return hmac.compare_digest(parts[1], WEBHOOK_SECRET)
    return False


def find_agent_by_id(agent_id: str) -> Optional["Agent"]:
    """Look up a loaded agent by ID."""
    if agent_os is not None and agent_os.agents:
        for ag in agent_os.agents:
            if ag.id == agent_id:
                return ag
    return None
