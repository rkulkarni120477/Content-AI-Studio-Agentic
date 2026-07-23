"""Unit tests for reference-aware S3 asset cleanup."""

import pytest

from app.core.config import settings
from app.services import asset_cleanup


@pytest.fixture(autouse=True)
def _configure_assets(monkeypatch):
    """Pin asset storage config so URL extraction is deterministic."""
    monkeypatch.setattr(settings, "assets_s3_bucket", "test-bucket", raising=False)
    monkeypatch.setattr(settings, "assets_s3_region", "us-east-1", raising=False)
    monkeypatch.setattr(settings, "aws_endpoint_url", "", raising=False)


BASE = "https://test-bucket.s3.us-east-1.amazonaws.com"


class TestAssetsInContent:
    def test_extracts_our_uploaded_image_key(self):
        content = f'<img src="{BASE}/DIS/cas-assets/abc123.png" alt="x">'
        assert asset_cleanup.assets_in_content(content) == {"DIS/cas-assets/abc123.png"}

    def test_extracts_from_markdown_image(self):
        content = f"![cat]({BASE}/DIS/cas-assets/z9.jpg)"
        assert asset_cleanup.assets_in_content(content) == {"DIS/cas-assets/z9.jpg"}

    def test_ignores_external_images(self):
        assert asset_cleanup.assets_in_content('<img src="https://other.com/a.png">') == set()

    def test_ignores_non_asset_objects_in_same_bucket(self):
        # Only the cas-assets/ prefix is ours; other bucket keys are left alone.
        assert asset_cleanup.assets_in_content(f'<img src="{BASE}/DIS/processed/x.png">') == set()

    def test_ignores_youtube_embed(self):
        content = '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
        assert asset_cleanup.assets_in_content(content) == set()

    def test_multiple_and_dedup(self):
        content = (
            f'<img src="{BASE}/DIS/cas-assets/a.png">'
            f'<img src="{BASE}/DIS/cas-assets/b.png">'
            f'<img src="{BASE}/DIS/cas-assets/a.png">'
        )
        assert asset_cleanup.assets_in_content(content) == {
            "DIS/cas-assets/a.png",
            "DIS/cas-assets/b.png",
        }

    def test_empty(self):
        assert asset_cleanup.assets_in_content("") == set()
        assert asset_cleanup.assets_in_content(None) == set()


class TestCleanupOrchestration:
    def test_deletes_removed_and_unreferenced(self, monkeypatch):
        deleted = []
        monkeypatch.setattr(asset_cleanup, "is_asset_referenced", lambda db, key: False)
        monkeypatch.setattr(asset_cleanup, "delete_asset", lambda key: deleted.append(key) or True)

        old = f'<img src="{BASE}/DIS/cas-assets/gone.png"><img src="{BASE}/DIS/cas-assets/stay.png">'
        new = f'<img src="{BASE}/DIS/cas-assets/stay.png">'
        asset_cleanup.cleanup_removed_assets(db=None, old_content=old, new_content=new)

        assert deleted == ["DIS/cas-assets/gone.png"]

    def test_keeps_removed_but_still_referenced(self, monkeypatch):
        deleted = []
        # Simulates a version still holding the image.
        monkeypatch.setattr(asset_cleanup, "is_asset_referenced", lambda db, key: True)
        monkeypatch.setattr(asset_cleanup, "delete_asset", lambda key: deleted.append(key) or True)

        old = f'<img src="{BASE}/DIS/cas-assets/gone.png">'
        asset_cleanup.cleanup_removed_assets(db=None, old_content=old, new_content="")

        assert deleted == []

    def test_no_change_no_deletes(self, monkeypatch):
        deleted = []
        monkeypatch.setattr(asset_cleanup, "is_asset_referenced", lambda db, key: False)
        monkeypatch.setattr(asset_cleanup, "delete_asset", lambda key: deleted.append(key) or True)

        same = f'<img src="{BASE}/DIS/cas-assets/a.png">'
        asset_cleanup.cleanup_removed_assets(db=None, old_content=same, new_content=same)

        assert deleted == []
