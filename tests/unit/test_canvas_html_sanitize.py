"""
Verifies the Canvas/LMS HTML builder sanitizes user content while preserving the
trusted single-column layout (classes + <style>). This is the render-time XSS
barrier for exports (finding #2).
"""

from promptops_app.services.canvas_html_service import build_canvas_html_from_markdown

CONTENT = (
    "## Learning Objectives\n\n"
    "- Understand X\n- Apply Y\n\n"
    "A normal paragraph with **bold** text.\n\n"
    '<iframe src="https://www.youtube.com/embed/VID123456"></iframe>\n\n'
    '<iframe src="https://evil.example.com/x"></iframe>\n\n'
    '<img src="a.png" onerror="hack()">\n\n'
    "<script>alert(1)</script>\n"
)


def test_layout_and_safe_content_preserved():
    out = build_canvas_html_from_markdown("Lesson 1", CONTENT)
    # Trusted layout classes survive sanitization.
    assert 'class="cas-lesson"' in out
    assert 'class="objectives"' in out
    # Safe content renders.
    assert "Understand X" in out
    assert "<strong>bold</strong>" in out


def test_unsafe_content_stripped():
    out = build_canvas_html_from_markdown("Lesson 1", CONTENT)
    assert "<script" not in out
    assert "onerror" not in out
    assert "evil.example.com" not in out


def test_youtube_embed_kept():
    out = build_canvas_html_from_markdown("Lesson 1", CONTENT)
    assert "youtube.com/embed/VID123456" in out


def test_empty_content_does_not_crash():
    # Empty content still returns the (empty) layout skeleton — no crash, no unsafe content.
    out = build_canvas_html_from_markdown("Lesson 1", "")
    assert isinstance(out, str)
    assert "<script" not in out
