"""Unit tests for the Tier 2 Step 3 shared-state lock primitive.

These run with NO real Redis and without the `redis` package — RedisLock takes an
injected client, so we feed it a minimal in-memory fake implementing exactly the
operations the lock uses (`set` with nx/px, and `eval` for compare-and-delete).
"""
import time

import pytest

from services.locks import NullLock, RedisLock, LockError


class FakeRedis:
    """Minimal Redis stand-in: SET NX PX + the release Lua (get==token then del)."""

    def __init__(self):
        self.store = {}          # key -> (value, expiry_monotonic or None)
        self.fail = False        # flip to simulate Redis being unreachable

    def _expired(self, key):
        v = self.store.get(key)
        if v is None:
            return True
        _, exp = v
        if exp is not None and time.monotonic() >= exp:
            del self.store[key]
            return True
        return False

    def set(self, key, value, nx=False, px=None):
        if self.fail:
            raise ConnectionError("fake redis down")
        if nx and not self._expired(key):
            return None
        exp = (time.monotonic() + px / 1000.0) if px else None
        self.store[key] = (value, exp)
        return True

    def get(self, key):
        if self._expired(key):
            return None
        return self.store[key][0]

    def eval(self, script, numkeys, key, arg):
        # Emulate the compare-and-delete release script.
        if self.fail:
            raise ConnectionError("fake redis down")
        if not self._expired(key) and self.store[key][0] == arg:
            del self.store[key]
            return 1
        return 0


def test_null_lock_is_noop():
    with NullLock("x") as lk:
        assert lk is not None  # no raise, no state


def test_redis_lock_acquire_and_release():
    r = FakeRedis()
    with RedisLock(r, "idx"):
        assert r.get("dis:lock:idx") is not None      # held
    assert r.get("dis:lock:idx") is None              # released


def test_redis_lock_is_mutually_exclusive():
    r = FakeRedis()
    outer = RedisLock(r, "idx", acquire_timeout_s=0.2)
    outer.__enter__()
    # A second holder cannot acquire while the first holds it → times out.
    blocked = RedisLock(r, "idx", acquire_timeout_s=0.2, strict=True)
    with pytest.raises(LockError):
        blocked.__enter__()
    outer.__exit__(None, None, None)
    # Now it is free again.
    with RedisLock(r, "idx", acquire_timeout_s=0.2, strict=True):
        pass


def test_release_only_deletes_own_token():
    r = FakeRedis()
    a = RedisLock(r, "idx")
    a.__enter__()
    token_a = r.get("dis:lock:idx")
    # Simulate a's TTL expiring and b taking the lock.
    r.store["dis:lock:idx"] = ("someone-elses-token", None)
    a.__exit__(None, None, None)                       # a must NOT delete b's lock
    assert r.get("dis:lock:idx") == "someone-elses-token"
    assert token_a != "someone-elses-token"


def test_ttl_expiry_frees_a_crashed_holder():
    r = FakeRedis()
    holder = RedisLock(r, "idx", ttl_ms=50)
    holder.__enter__()                                 # acquired, never released (simulated crash)
    assert r.get("dis:lock:idx") is not None
    time.sleep(0.08)                                   # wait past the 50ms TTL
    # A new writer can now acquire.
    with RedisLock(r, "idx", acquire_timeout_s=0.2, strict=True):
        pass


def test_degrades_to_noop_when_redis_errors_non_strict():
    r = FakeRedis()
    r.fail = True
    # Non-strict (default): Redis errors must not break the body.
    with RedisLock(r, "idx", strict=False):
        pass  # no raise


def test_strict_raises_when_redis_errors():
    r = FakeRedis()
    r.fail = True
    with pytest.raises(LockError):
        with RedisLock(r, "idx", strict=True):
            pass
