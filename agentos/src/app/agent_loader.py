"""
Agent Loader
------------

Dynamic agent discovery via importlib.

Scans a directory for Python files, imports each module, and collects
any top-level ``agno.agent.Agent`` instances.  Files whose names start
with ``_`` are skipped (convention for helpers / __init__).

If a ``.env`` file exists in the agents directory it is loaded before
any agent modules are imported so that shared secrets (API keys, DB URLs)
are available as environment variables.
"""

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import List

from agno.agent import Agent
from dotenv import load_dotenv

logger = logging.getLogger("agno.loader")


def discover_agents(agents_dir: Path) -> List[Agent]:
    """Scan *agents_dir* for Python files and return all Agent instances.

    The discovery rules are:
    1.  Load ``.env`` from *agents_dir* if present.
    2.  Every ``*.py`` file whose name does **not** start with ``_`` is
        imported as a top-level module.
    3.  After import, every module attribute that is an ``Agent`` is
        collected.
    4.  Sub-directories with an ``__init__.py`` are treated as packages
        and imported the same way (top-level attributes scanned).

    Previously-imported agent modules are **reloaded** so that code
    changes on the git-sync volume take effect without a process restart.
    """
    agents_dir = agents_dir.resolve()

    if not agents_dir.is_dir():
        logger.warning("Agents directory does not exist: %s — starting with zero agents", agents_dir)
        return []

    # Load shared .env (idempotent — only adds missing vars)
    env_file = agents_dir / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)
        logger.info("Loaded .env from %s", env_file)

    # Ensure agents_dir is on sys.path so relative imports inside agent
    # packages work.  We prepend so agent code can shadow library helpers.
    str_dir = str(agents_dir)
    if str_dir not in sys.path:
        sys.path.insert(0, str_dir)

    agents: List[Agent] = []

    # Collect candidate files and packages
    candidates: List[Path] = []
    for item in sorted(agents_dir.iterdir()):
        if item.name.startswith("_") or item.name.startswith("."):
            continue
        if item.is_file() and item.suffix == ".py":
            candidates.append(item)
        elif item.is_dir() and (item / "__init__.py").is_file():
            candidates.append(item / "__init__.py")

    for path in candidates:
        module_name = _module_name_for(path, agents_dir)
        try:
            mod = _import_or_reload(module_name, path)
            found = _collect_agents(mod)
            if found:
                names = [a.name or a.id for a in found]
                logger.info("Discovered %d agent(s) in %s: %s", len(found), module_name, names)
                agents.extend(found)
            else:
                logger.debug("No Agent instances in %s", module_name)
        except Exception:
            logger.exception("Failed to import agent module %s", module_name)

    logger.info("Total agents discovered: %d", len(agents))
    return agents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module_name_for(path: Path, base: Path) -> str:
    """Derive a dotted module name relative to *base*."""
    rel = path.relative_to(base)
    parts = list(rel.with_suffix("").parts)
    # Remove trailing __init__ so packages get a clean name
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else rel.stem


def _import_or_reload(module_name: str, path: Path) -> "types.ModuleType":  # noqa: F821
    """Import *module_name* from *path*, reloading if already cached."""
    import types  # noqa: F811 — local import to avoid circular issues

    if module_name in sys.modules:
        mod = sys.modules[module_name]
        try:
            importlib.reload(mod)
            logger.debug("Reloaded %s", module_name)
        except Exception:
            logger.exception("Reload failed for %s — using cached version", module_name)
        return mod

    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create spec for {path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _collect_agents(mod) -> List[Agent]:
    """Return all ``Agent`` instances defined at module scope."""
    found: List[Agent] = []
    for attr_name in dir(mod):
        if attr_name.startswith("_"):
            continue
        obj = getattr(mod, attr_name, None)
        if isinstance(obj, Agent):
            found.append(obj)
    return found
