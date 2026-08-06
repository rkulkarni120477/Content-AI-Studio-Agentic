"""P4.3 — DIS HTTP clients are reused, not rebuilt per call (F7).

httpx client construction opens no sockets, so these run without a network: they
assert *identity* of the reused client objects. The async client is loop-bound,
so reuse is verified within a single event loop and rebuild is verified after
``aclose()``.
"""

from __future__ import annotations

import asyncio


def _new_client():
    from app.core.dis_client import DISClient

    return DISClient()


def test_sync_client_is_reused():
    client = _new_client()
    a = client._get_sync_client()
    b = client._get_sync_client()
    assert a is b


def test_async_client_reused_within_loop():
    client = _new_client()

    async def _inner():
        a = client._get_async_client()
        b = client._get_async_client()
        return a, b

    a, b = asyncio.run(_inner())
    assert a is b


def test_async_client_rebuilt_after_aclose():
    client = _new_client()

    async def _inner():
        a = client._get_async_client()
        await client.aclose()
        b = client._get_async_client()
        return a, b

    a, b = asyncio.run(_inner())
    assert a is not b
    assert a.is_closed


def test_aclose_is_idempotent_and_safe_when_unused():
    """aclose() on a client that never issued a request must not raise."""
    client = _new_client()
    asyncio.run(client.aclose())  # nothing built yet
    asyncio.run(client.aclose())  # still fine on a second call
