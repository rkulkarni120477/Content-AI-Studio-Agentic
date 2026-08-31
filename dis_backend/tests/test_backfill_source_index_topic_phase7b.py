"""Phase 7B: backfill read-path resolution for s3:// payload_key values."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_BACKFILL = _SCRIPTS / "backfill_source_index_topic.py"


def _load_backfill():
    spec = importlib.util.spec_from_file_location("backfill_source_index_topic", _BACKFILL)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def backfill():
    return _load_backfill()


@pytest.fixture
def resolve_kwargs():
    return {
        "processed_bucket": "content-ai-studio",
        "source_prefix": "processed/aim_ns/aim/development",
        "base_prefix": "DIS",
    }


class TestResolvePayloadKey:
    def test_relative_key_unchanged(self, backfill, resolve_kwargs):
        key = "processed/aim_ns/aim/development/job-1/studio_payload/payload.json"
        assert backfill.resolve_payload_key(key, **resolve_kwargs) == key

    def test_valid_s3_uri_extracts_logical_key(self, backfill, resolve_kwargs):
        s3 = (
            "s3://content-ai-studio/DIS/processed/aim_ns/aim/development/"
            "job-1/studio_payload/payload.json"
        )
        expected = "processed/aim_ns/aim/development/job-1/studio_payload/payload.json"
        assert backfill.resolve_payload_key(s3, **resolve_kwargs) == expected

    def test_valid_s3_uri_without_base_prefix_in_object_key(self, backfill, resolve_kwargs):
        s3 = (
            "s3://content-ai-studio/processed/aim_ns/aim/development/"
            "job-1/studio_payload/payload.json"
        )
        expected = "processed/aim_ns/aim/development/job-1/studio_payload/payload.json"
        assert backfill.resolve_payload_key(s3, **resolve_kwargs) == expected

    def test_wrong_bucket_rejected(self, backfill, resolve_kwargs):
        s3 = (
            "s3://other-bucket/DIS/processed/aim_ns/aim/development/"
            "job-1/studio_payload/payload.json"
        )
        assert backfill.resolve_payload_key(s3, **resolve_kwargs) is None

    def test_wrong_tenant_prefix_rejected(self, backfill, resolve_kwargs):
        s3 = (
            "s3://content-ai-studio/DIS/processed/cengage_ns/cengage/development/"
            "job-1/studio_payload/payload.json"
        )
        assert backfill.resolve_payload_key(s3, **resolve_kwargs) is None

    def test_wrong_environment_prefix_rejected(self, backfill, resolve_kwargs):
        s3 = (
            "s3://content-ai-studio/DIS/processed/aim_ns/aim/production/"
            "job-1/studio_payload/payload.json"
        )
        assert backfill.resolve_payload_key(s3, **resolve_kwargs) is None

    def test_malformed_s3_uri_rejected(self, backfill, resolve_kwargs):
        assert backfill.resolve_payload_key("s3://content-ai-studio", **resolve_kwargs) is None
        assert backfill.resolve_payload_key("s3://", **resolve_kwargs) is None

    def test_empty_payload_key_rejected(self, backfill, resolve_kwargs):
        assert backfill.resolve_payload_key("", **resolve_kwargs) is None
        assert backfill.resolve_payload_key("   ", **resolve_kwargs) is None

    def test_non_studio_payload_suffix_rejected(self, backfill, resolve_kwargs):
        s3 = (
            "s3://content-ai-studio/DIS/processed/aim_ns/aim/development/"
            "job-1/source_content/content.json"
        )
        assert backfill.resolve_payload_key(s3, **resolve_kwargs) is None


class TestTopicFromPayload:
    def test_reads_metadata_topic(self, backfill):
        assert backfill._topic_from_payload({"metadata": {"topic": "Engines"}}) == "Engines"

    def test_empty_topic(self, backfill):
        assert backfill._topic_from_payload({"metadata": {}}) == ""
        assert backfill._topic_from_payload({}) == ""

    def test_non_string_topic_coerced(self, backfill):
        assert backfill._topic_from_payload({"metadata": {"topic": 42}}) == "42"


class TestBackfillMainFlow:
    def _tenant_cfg(self):
        storage = SimpleNamespace(
            processed_bucket="content-ai-studio",
            base_prefix="DIS",
            provider="s3",
            s3=SimpleNamespace(region="us-east-1", endpoint_url="", kms_key_id=""),
            local=SimpleNamespace(base_path="/unused"),
        )
        cfg = SimpleNamespace(tenant_id="aim", storage=storage)
        cfg.get_namespace = lambda client_id: "aim_ns/aim"
        return cfg

    def test_dry_run_resolves_s3_payload_key_and_plans_topic(self, backfill):
        job_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        relative = (
            f"processed/aim_ns/aim/development/{job_id}/studio_payload/payload.json"
        )
        s3_key = f"s3://content-ai-studio/DIS/{relative}"
        index = {
            "sources": [
                {
                    "job_id": job_id,
                    "payload_key": s3_key,
                    "source_file_name": "Engines.pdf",
                }
            ]
        }
        payload = {"metadata": {"topic": "Engines"}}

        with (
            patch("config.settings.get_tenant_config", return_value=self._tenant_cfg()),
            patch("services.artifacts.ArtifactWriter") as writer_cls,
            patch("services.source_library.read_source_index", return_value=index),
            patch("services.source_library.write_source_index") as write_index,
            patch.dict("os.environ", {"ENVIRONMENT": "development"}, clear=False),
        ):
            writer = writer_cls.return_value
            writer.processed_bucket = "content-ai-studio"
            writer.read_json.return_value = payload

            argv = ["backfill_source_index_topic.py", "--client", "aim"]
            with patch.object(sys, "argv", argv):
                rc = backfill.main()

        assert rc == 0
        writer.read_json.assert_called_once_with(relative)
        write_index.assert_not_called()

    def test_invalid_s3_payload_key_skipped_without_read(self, backfill):
        job_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
        index = {
            "sources": [
                {
                    "job_id": job_id,
                    "payload_key": "s3://wrong-bucket/DIS/processed/aim_ns/aim/development/x/studio_payload/payload.json",
                    "source_file_name": "bad.pdf",
                }
            ]
        }

        with (
            patch("config.settings.get_tenant_config", return_value=self._tenant_cfg()),
            patch("services.artifacts.ArtifactWriter") as writer_cls,
            patch("services.source_library.read_source_index", return_value=index),
            patch("services.source_library.write_source_index") as write_index,
            patch.dict("os.environ", {"ENVIRONMENT": "development"}, clear=False),
        ):
            writer = writer_cls.return_value
            writer.processed_bucket = "content-ai-studio"

            argv = ["backfill_source_index_topic.py", "--client", "aim"]
            with patch.object(sys, "argv", argv):
                rc = backfill.main()

        assert rc == 0
        writer.read_json.assert_not_called()
        write_index.assert_not_called()

    def test_apply_mutates_only_topic(self, backfill, tmp_path):
        job_id = "cccccccc-dddd-eeee-ffff-000000000001"
        relative = (
            f"processed/aim_ns/aim/development/{job_id}/studio_payload/payload.json"
        )
        index = {
            "sources": [
                {
                    "job_id": job_id,
                    "payload_key": relative,
                    "source_file_name": "Engines.pdf",
                    "document_type": "lesson_pdf",
                    "content_key": f"processed/aim_ns/aim/development/{job_id}/source_content/content.json",
                }
            ]
        }
        fresh = json.loads(json.dumps(index))
        payload = {"metadata": {"topic": "Hydraulics"}}

        with (
            patch("config.settings.get_tenant_config", return_value=self._tenant_cfg()),
            patch("services.artifacts.ArtifactWriter") as writer_cls,
            patch("services.source_library.read_source_index", side_effect=[index, fresh]),
            patch("services.source_library.write_source_index") as write_index,
            patch.dict("os.environ", {"ENVIRONMENT": "development"}, clear=False),
        ):
            writer = writer_cls.return_value
            writer.processed_bucket = "content-ai-studio"
            writer.read_json.return_value = payload

            argv = [
                "backfill_source_index_topic.py",
                "--client",
                "aim",
                "--apply",
                "--manifest-dir",
                str(tmp_path),
            ]
            with patch.object(sys, "argv", argv):
                rc = backfill.main()

        assert rc == 0
        written = write_index.call_args[0][2]
        rec = written["sources"][0]
        assert rec["topic"] == "Hydraulics"
        assert rec["payload_key"] == relative
        assert rec["job_id"] == job_id
        assert rec["document_type"] == "lesson_pdf"
        assert len(written["sources"]) == 1

    def test_idempotent_when_topic_already_matches(self, backfill):
        job_id = "dddddddd-eeee-ffff-0000-111111111111"
        relative = (
            f"processed/aim_ns/aim/development/{job_id}/studio_payload/payload.json"
        )
        index = {
            "sources": [
                {
                    "job_id": job_id,
                    "payload_key": relative,
                    "topic": "Engines",
                    "source_file_name": "Engines.pdf",
                }
            ]
        }

        with (
            patch("config.settings.get_tenant_config", return_value=self._tenant_cfg()),
            patch("services.artifacts.ArtifactWriter") as writer_cls,
            patch("services.source_library.read_source_index", return_value=index),
            patch("services.source_library.write_source_index") as write_index,
            patch.dict("os.environ", {"ENVIRONMENT": "development"}, clear=False),
        ):
            writer = writer_cls.return_value
            writer.processed_bucket = "content-ai-studio"
            writer.read_json.return_value = {"metadata": {"topic": "Engines"}}

            argv = ["backfill_source_index_topic.py", "--client", "aim"]
            with patch.object(sys, "argv", argv):
                rc = backfill.main()

        assert rc == 0
        write_index.assert_not_called()

    def test_relative_and_s3_same_topic(self, backfill, resolve_kwargs):
        job_id = "eeeeeeee-ffff-0000-1111-222222222222"
        relative = (
            f"processed/aim_ns/aim/development/{job_id}/studio_payload/payload.json"
        )
        s3 = f"s3://content-ai-studio/DIS/{relative}"
        assert backfill.resolve_payload_key(relative, **resolve_kwargs) == relative
        assert backfill.resolve_payload_key(s3, **resolve_kwargs) == relative
