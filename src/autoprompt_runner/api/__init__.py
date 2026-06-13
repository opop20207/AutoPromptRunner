"""HTTP API layer for AutoPromptRunner (FastAPI).

Optional component: it requires the ``api`` extra (``pip install -e ".[api]"``). The API
exposes the same run/project operations as the CLI over HTTP, reusing the existing
services and the same local SQLite database. There is no authentication, no websocket /
live-log streaming, and no frontend in this step.
"""
