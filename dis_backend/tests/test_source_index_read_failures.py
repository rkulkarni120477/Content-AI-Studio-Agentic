"""A source index that cannot be read must say so, not return an empty library.

read_source_index returns an empty index on failure so a brand-new client — one
that has never had an upload, and so has no index object yet — opens to an empty
library instead of an error page. That is the only case it is meant to cover.

It used to cover every case, with a bare `except Exception: pass` and no logging.
So these all produced an identical answer, zero documents and a silent log:

    the client genuinely has no documents yet
    the bucket name is wrong
    the credentials cannot read the object
    the AWS session token has expired
    ENVIRONMENT points at a deployment whose data lives elsewhere

The last one cost a real debugging session: an 8.23 GB copy landed correctly in
the dev bucket and the library still showed 0, with nothing anywhere saying the
service could not read what it had been pointed at.

The legitimate empty case stays quiet. Everything else is loud, and names the
bucket and key it failed on.
"""
from __future__ import annotations

import logging

import pytest

from config.settings import get_tenant_config
from services import source_library


class _Boom(Exception):
    """A botocore-shaped error: the code lives under .response['Error']['Code']."""

    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


@pytest.fixture
def cfg():
    return get_tenant_config("aim")


def read_with(monkeypatch, exc, cfg):
    def fake_read(self, key):
        raise exc
    monkeypatch.setattr(source_library.ArtifactWriter, "read_json", fake_read)
    return source_library.read_source_index(cfg, "aim")


def test_a_missing_index_is_an_empty_library_not_an_error(monkeypatch, caplog, cfg):
    """A client with no uploads yet. Expected, and not worth alarming anyone."""
    with caplog.at_level(logging.INFO, logger=source_library.__name__):
        index = read_with(monkeypatch, _Boom("NoSuchKey"), cfg)
    assert index["sources"] == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], \
        "an empty library for a new client must not be reported as a failure"


@pytest.mark.parametrize("code", ["AccessDenied", "ExpiredToken",
                                  "InvalidAccessKeyId", "SignatureDoesNotMatch"])
def test_storage_we_cannot_read_is_reported_loudly(monkeypatch, caplog, cfg, code):
    """The cases that look identical to 'no documents' but are not."""
    with caplog.at_level(logging.INFO, logger=source_library.__name__):
        index = read_with(monkeypatch, _Boom(code), cfg)
    assert index["sources"] == [], "still degrades rather than raising"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, f"{code} must be logged at error level"
    message = errors[0].getMessage()
    assert code in message, "the log must carry the underlying cause"
    assert "source_index/source_list.json" in message, \
        "the log must name the key, so a wrong ENVIRONMENT is visible in it"


def test_the_log_names_the_bucket_it_could_not_read(monkeypatch, caplog, cfg):
    """The wrong-bucket and wrong-ENVIRONMENT cases are only distinguishable
    from each other by the full path that was attempted."""
    with caplog.at_level(logging.INFO, logger=source_library.__name__):
        read_with(monkeypatch, _Boom("AccessDenied"), cfg)
    message = [r for r in caplog.records if r.levelno >= logging.ERROR][0].getMessage()
    assert cfg.storage.processed_bucket in message


def test_a_corrupt_index_is_not_mistaken_for_an_empty_one(monkeypatch, caplog, cfg):
    """read_json succeeding with the wrong shape is its own failure."""
    monkeypatch.setattr(source_library.ArtifactWriter, "read_json",
                        lambda self, key: ["not", "an", "index"])
    with caplog.at_level(logging.INFO, logger=source_library.__name__):
        index = source_library.read_source_index(cfg, "aim")
    assert index["sources"] == []
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_healthy_index_is_returned_untouched(monkeypatch, caplog, cfg):
    """Negative control: if this logged too, the assertions above prove nothing."""
    monkeypatch.setattr(source_library.ArtifactWriter, "read_json",
                        lambda self, key: {"sources": [{"job_id": "j1"}]})
    with caplog.at_level(logging.INFO, logger=source_library.__name__):
        index = source_library.read_source_index(cfg, "aim")
    assert [s["job_id"] for s in index["sources"]] == ["j1"]
    assert not caplog.records


def test_an_index_with_no_sources_key_still_lists(monkeypatch, cfg):
    monkeypatch.setattr(source_library.ArtifactWriter, "read_json",
                        lambda self, key: {"schema_version": "source_index_v1"})
    assert source_library.read_source_index(cfg, "aim")["sources"] == []
