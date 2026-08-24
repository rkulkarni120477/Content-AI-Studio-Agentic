"""Finding #10: load_dis_access_config's cache must notice a changed file on
its own, since force_reload only ever clears the calling process's copy and
api/celery_worker/api_server each hold their own — the file (bind-mounted
into every container) is the only thing they actually share.
"""

from __future__ import annotations

import json
import os

import pytest

from app.core import dis_access


@pytest.fixture(autouse=True)
def _reset_cache(tmp_path, monkeypatch):
    """Isolate the module-level cache and point it at a throwaway file."""
    config_path = tmp_path / "dis_access.json"
    monkeypatch.setattr(dis_access, "_access_config_path", lambda: config_path)
    monkeypatch.setattr(dis_access, "_ACCESS_CONFIG_CACHE", None)
    monkeypatch.setattr(dis_access, "_ACCESS_CONFIG_CACHE_MTIME", None)
    return config_path


def _write(path, **overrides):
    payload = {"default_client_id": "cengage", "available_clients": ["cengage"]}
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cache_is_reused_when_file_unchanged(_reset_cache):
    _write(_reset_cache, available_clients=["cengage"])

    first = dis_access.load_dis_access_config()
    second = dis_access.load_dis_access_config()

    assert first == second
    assert first["available_clients"] == ["cengage"]


def test_a_changed_file_is_picked_up_without_force_reload(_reset_cache):
    """The actual fix: no force_reload=True needed — this simulates a
    different process having written the file after this process cached it."""
    _write(_reset_cache, available_clients=["cengage"])
    first = dis_access.load_dis_access_config()
    assert first["available_clients"] == ["cengage"]

    _write(_reset_cache, available_clients=["cengage", "nova-publishing"])
    # Force a distinct mtime — same-second writes can round to an identical
    # timestamp on coarser filesystems.
    newer = os.path.getmtime(_reset_cache) + 2
    os.utime(_reset_cache, (newer, newer))

    second = dis_access.load_dis_access_config()  # no force_reload
    assert second["available_clients"] == ["cengage", "nova-publishing"]


def test_force_reload_still_reloads_even_with_an_unchanged_file(_reset_cache):
    _write(_reset_cache, available_clients=["cengage"])
    dis_access.load_dis_access_config()
    original_mtime = os.path.getmtime(_reset_cache)

    _write(_reset_cache, available_clients=["cengage", "aim"])
    os.utime(_reset_cache, (original_mtime, original_mtime))  # pin the mtime — no bump

    reloaded = dis_access.load_dis_access_config(force_reload=True)
    assert reloaded["available_clients"] == ["cengage", "aim"]
