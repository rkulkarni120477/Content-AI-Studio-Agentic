"""
LLM client initialisation for the FastAPI application.

This module replaces the ``@st.cache_resource`` pattern from Streamlit.
In Streamlit, ``@st.cache_resource`` created a singleton per process by
caching on the first call.  In FastAPI we achieve the same result by
initialising the clients once in the application lifespan and storing
them as module-level singletons.

The actual LLM call logic lives in ``promptops_app/core/llm_client.py``
(the existing code, unchanged).  This module just handles initialisation
so the existing client is ready before the first request arrives.

Why initialise at startup?
--------------------------
Creating a ``requests.Session`` (OpenAI) and a ``boto3`` client (Bedrock)
is expensive.  Doing it on the first request adds latency to that request.
Doing it at startup means the first user request is just as fast as the rest.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def initialise_llm_clients() -> None:
    """
    Warm up the LLM client singletons at application startup.

    Imports the client modules so their module-level singleton factories run
    and the HTTP sessions / boto3 clients are created and cached.

    Called once from the FastAPI lifespan context in ``app/main.py``.
    """
    try:
        # Importing the module triggers client creation via the lazy singleton
        # pattern in the existing llm_client.py.
        import promptops_app.core.llm_client as _client  # noqa: F401
        _log.info("llm_clients_initialised  providers=openai,bedrock")
    except Exception as exc:
        # Log but do not crash — the app can still serve non-LLM endpoints
        # even if AWS credentials are missing in a local dev environment.
        _log.warning("llm_client_init_failed  error=%s", exc)
