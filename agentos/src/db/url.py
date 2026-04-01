"""
Database URL
------------

Build database connection URL from environment variables.

Supports the standard PostgreSQL URL convention:
  {driver}://{user}:{password}@{host}:{port}/{database}

Environment variables:
  DB_DRIVER   - SQLAlchemy driver (default: postgresql+psycopg)
  DB_USER     - Database username (default: ai)
  DB_PASS     - Database password (default: ai)
  DB_HOST     - Database hostname (default: localhost)
  DB_PORT     - Database port (default: 5432)
  DB_DATABASE - Database name (default: ai)
"""

from os import getenv
from urllib.parse import quote


def build_db_url() -> str:
    """Build database URL from environment variables."""
    driver = getenv("DB_DRIVER", "postgresql+psycopg")
    user = getenv("DB_USER", "ai")
    password = quote(getenv("DB_PASS", "ai"), safe="")
    host = getenv("DB_HOST", "localhost")
    port = getenv("DB_PORT", "5432")
    database = getenv("DB_DATABASE", "ai")

    return f"{driver}://{user}:{password}@{host}:{port}/{database}"


db_url = build_db_url()
