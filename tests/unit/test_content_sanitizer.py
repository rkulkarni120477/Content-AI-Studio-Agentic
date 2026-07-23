"""Unit tests for the backend content sanitizer."""

from promptops_app.services.content_sanitizer import (
    is_allowed_embed_src,
    sanitize_html,
    sanitize_stored_content,
)


class TestIsAllowedEmbedSrc:
    def test_accepts_youtube_embed(self):
        assert is_allowed_embed_src("https://www.youtube.com/embed/abc123")
        assert is_allowed_embed_src("https://youtube-nocookie.com/embed/x")

    def test_rejects_non_youtube_and_non_embed(self):
        assert not is_allowed_embed_src("https://evil.com/embed/x")
        assert not is_allowed_embed_src("https://www.youtube.com/watch?v=abc")
        assert not is_allowed_embed_src("http://www.youtube.com/embed/x")  # not https
        assert not is_allowed_embed_src("")
        assert not is_allowed_embed_src(None)


class TestSanitizeHtml:
    def test_strips_script_and_handlers(self):
        assert "script" not in sanitize_html("<p>ok</p><script>alert(1)</script>")
        assert "onerror" not in sanitize_html('<img src="x" onerror="e()">')

    def test_drops_javascript_url(self):
        assert "javascript:" not in sanitize_html('<a href="javascript:alert(1)">x</a>')

    def test_keeps_youtube_iframe_only(self):
        assert "youtube.com/embed" in sanitize_html('<iframe src="https://www.youtube.com/embed/x"></iframe>')
        assert "evil.com" not in sanitize_html('<iframe src="https://evil.com/x"></iframe>')

    def test_allows_data_image_on_img_only(self):
        assert "data:image" in sanitize_html('<img src="data:image/png;base64,iVBOR">')
        assert "data:text/html" not in sanitize_html('<a href="data:text/html,<b>">x</a>')

    def test_style_reduced_to_text_align(self):
        out = sanitize_html('<p style="text-align: center; position: fixed">c</p>')
        assert "text-align: center" in out
        assert "position" not in out

    def test_empty(self):
        assert sanitize_html("") == ""
        assert sanitize_html(None) == ""


class TestSanitizeStoredContent:
    def test_pure_markdown_is_untouched(self):
        # The critical property: HTML-sensitive prose must NOT be escaped.
        md = "# Heading\n\nText with a < b and x > y and A & B.\n\n- one\n- two"
        assert sanitize_stored_content(md) == md

    def test_strips_script_blocks(self):
        assert "<script" not in sanitize_stored_content("hi <script>alert(1)</script> bye")

    def test_strips_event_handlers(self):
        assert "onerror" not in sanitize_stored_content('<img src="x" onerror="e()">')

    def test_neutralizes_javascript_urls(self):
        assert "javascript:" not in sanitize_stored_content('<a href="javascript:bad()">l</a>')

    def test_removes_non_youtube_iframe_keeps_youtube(self):
        assert sanitize_stored_content('<iframe src="https://evil.com/x"></iframe>') == ""
        keep = '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
        assert "youtube.com/embed/abc" in sanitize_stored_content(keep)

    def test_keeps_safe_image_figure(self):
        island = '<figure style="text-align: center"><img src="https://x/a.png" width="300"></figure>'
        assert sanitize_stored_content(island) == island

    def test_none_and_empty(self):
        assert sanitize_stored_content(None) == ""
        assert sanitize_stored_content("") == ""
