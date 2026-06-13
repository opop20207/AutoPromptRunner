"""FastAPI dependencies.

``get_db_path`` returns the resolved SQLite database path used by every request. Tests
override it (via ``app.dependency_overrides``) to point at a temporary database.
"""

from __future__ import annotations

from .. import storage


def get_db_path() -> str:
    """Return the resolved default SQLite path, ensuring the database exists."""
    return storage.init_db(None)
