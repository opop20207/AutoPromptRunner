"""FastAPI dependencies.

``get_db_path`` returns the resolved SQLite database path used by every request, taken
from the same settings loader as the CLI/worker (config file + ``AUTOPROMPT_*`` env). Tests
override it (via ``app.dependency_overrides``) to point at a temporary database.
"""

from __future__ import annotations

from .. import settings, storage


def get_db_path() -> str:
    """Return the resolved default SQLite path from settings, ensuring the database exists."""
    return storage.init_db(settings.load_settings().storage.db_path)
