"""
Filesystem Watcher
------------------

Uses the ``watchdog`` library to monitor the agents directory for
``.py`` file changes.  On any create / modify / delete the watcher
re-discovers agents and calls ``agent_os.resync(app)`` to rebuild
routes in-process.

A simple debounce (2 seconds) prevents rapid-fire reloads when
git-sync writes multiple files in quick succession.

A separate symlink-poller thread detects git-sync worktree swaps.
Git-sync creates a new directory for each commit and atomically swaps
a symlink, which inotify/watchdog cannot detect.  The poller checks
``os.path.realpath(agents_dir)`` every few seconds and triggers a
reload when the resolved target changes.

Route Snapshot/Restore
~~~~~~~~~~~~~~~~~~~~~~
``resync()`` unconditionally clears all routes in
``_reprovision_routers()``.  Using ``base_app`` alone does NOT fix
this.  The watcher snapshots custom routes before resync and restores
them after.  An exclude-list of AgentOS prefixes is used so that new
custom routes are automatically preserved without filter updates.
"""

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

# Event types that indicate actual file content changes
_WRITE_EVENTS = (FileCreatedEvent, FileModifiedEvent, FileDeletedEvent, FileMovedEvent)

if TYPE_CHECKING:
    from agno.os import AgentOS
    from fastapi import FastAPI

logger = logging.getLogger("agno.watcher")

# Minimum seconds between consecutive reloads
_DEBOUNCE_SECONDS = 2.0

# AgentOS-owned route prefixes — everything NOT matching these is
# considered a custom route and preserved through resync.
AGENTOS_PREFIXES = (
    "/agents/",
    "/sessions/",
    "/knowledge/",
    "/health",
    "/docs",
    "/openapi.json",
    "/mcp",
)


class _AgentReloadHandler(FileSystemEventHandler):
    """Watchdog handler that triggers an AgentOS resync on .py changes."""

    def __init__(self, agents_dir: Path, agent_os: "AgentOS", app: "FastAPI"):
        super().__init__()
        self._agents_dir = agents_dir
        self._agent_os = agent_os
        self._app = app
        self._last_reload: float = 0.0
        self._lock = threading.Lock()
        self._pending_timer: Optional[threading.Timer] = None

    # ------------------------------------------------------------------
    # Watchdog callbacks
    # ------------------------------------------------------------------
    def on_any_event(self, event: FileSystemEvent) -> None:
        # Only react to actual writes — ignore opened/closed/accessed
        if not isinstance(event, _WRITE_EVENTS):
            return

        # Only react to Python files
        src = event.src_path or ""
        if not src.endswith(".py"):
            return

        # Ignore __pycache__ and hidden dirs
        if "/__pycache__/" in src or "/." in src:
            return

        logger.info("Detected change: %s %s", event.event_type, src)
        self._schedule_reload()

    # ------------------------------------------------------------------
    # Debounced reload
    # ------------------------------------------------------------------
    def _schedule_reload(self) -> None:
        """Schedule a reload after the debounce window."""
        with self._lock:
            if self._pending_timer is not None:
                self._pending_timer.cancel()
            self._pending_timer = threading.Timer(_DEBOUNCE_SECONDS, self._do_reload)
            self._pending_timer.daemon = True
            self._pending_timer.start()

    def _do_reload(self) -> None:
        """Execute the actual reload — runs on the Timer thread."""
        with self._lock:
            now = time.monotonic()
            if now - self._last_reload < _DEBOUNCE_SECONDS:
                return
            self._last_reload = now

        try:
            from app.agent_loader import discover_agents

            new_agents = discover_agents(self._agents_dir)
            self._agent_os.agents = new_agents

            # Snapshot custom routes before resync wipes them.
            # Uses an exclude-list so new custom routes are auto-preserved.
            custom_routes = [
                route for route in self._app.router.routes
                if hasattr(route, "path")
                and not any(route.path.startswith(p) for p in AGENTOS_PREFIXES)
            ]

            self._agent_os.resync(app=self._app)

            # Restore custom routes that resync removed
            existing_paths = {r.path for r in self._app.router.routes if hasattr(r, "path")}
            for route in custom_routes:
                if route.path not in existing_paths:
                    self._app.router.routes.append(route)

            names = [a.name or a.id for a in new_agents]
            logger.info("Resynced AgentOS — %d agent(s): %s", len(new_agents), names)
        except Exception:
            logger.exception("Failed to resync AgentOS after file change")


# ---------------------------------------------------------------------------
# Symlink Poller — detects git-sync worktree swaps
# ---------------------------------------------------------------------------

# How often (seconds) to check for symlink target changes
_SYMLINK_POLL_SECONDS = 5.0


class _SymlinkPoller(threading.Thread):
    """Periodically resolves the agents directory symlink and triggers a
    reload when the target changes (i.e. git-sync swapped worktrees).

    Because git-sync atomically replaces a symlink, inotify events
    never fire on the *old* watched path.  This poller fills that gap.
    """

    def __init__(
        self,
        agents_dir: Path,
        reload_handler: "_AgentReloadHandler",
        observer: Observer,
    ):
        super().__init__(daemon=True, name="symlink-poller")
        self._agents_dir = agents_dir
        self._handler = reload_handler
        self._observer = observer
        self._stop_event = threading.Event()
        # Snapshot the initial resolved path
        self._last_real_path: str = os.path.realpath(agents_dir)

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(_SYMLINK_POLL_SECONDS)
            if self._stop_event.is_set():
                break
            try:
                current = os.path.realpath(self._agents_dir)
                if current != self._last_real_path:
                    logger.info(
                        "Symlink target changed: %s → %s",
                        self._last_real_path,
                        current,
                    )
                    self._last_real_path = current

                    # Re-point watchdog at the new resolved directory so that
                    # in-place edits (rare but possible) are still detected.
                    self._observer.unschedule_all()
                    self._observer.schedule(
                        self._handler,
                        str(self._agents_dir),
                        recursive=True,
                    )

                    # Trigger the debounced reload
                    self._handler._schedule_reload()
            except Exception:
                logger.exception("Symlink poller error")

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_watcher(agents_dir: Path, agent_os: "AgentOS", app: "FastAPI") -> Observer:
    """Start a watchdog ``Observer`` that monitors *agents_dir*.

    Also starts a symlink poller that detects git-sync worktree swaps
    (symlink target changes that watchdog/inotify cannot see).

    Returns the running ``Observer`` so callers can stop it on shutdown.
    """
    handler = _AgentReloadHandler(agents_dir, agent_os, app)
    observer = Observer()
    observer.schedule(handler, str(agents_dir), recursive=True)
    observer.daemon = True
    observer.start()
    logger.info("Watching %s for agent changes", agents_dir)

    # Start symlink poller for git-sync compatibility
    poller = _SymlinkPoller(agents_dir, handler, observer)
    poller.start()
    logger.info(
        "Symlink poller active (every %.0fs) — resolved: %s",
        _SYMLINK_POLL_SECONDS,
        os.path.realpath(agents_dir),
    )

    # Stash the poller on the observer so stop_watcher can shut it down
    observer._symlink_poller = poller  # type: ignore[attr-defined]
    return observer


def stop_watcher(observer: Optional[Observer]) -> None:
    """Gracefully stop the watchdog observer and symlink poller."""
    if observer is None:
        return
    # Stop symlink poller first
    poller = getattr(observer, "_symlink_poller", None)
    if poller is not None:
        poller.stop()
        poller.join(timeout=3)
    observer.stop()
    observer.join(timeout=5)
    logger.info("Stopped filesystem watcher")
