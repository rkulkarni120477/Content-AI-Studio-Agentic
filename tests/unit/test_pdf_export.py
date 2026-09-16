"""PDF Export ticket — Editor "Export failed" bug.

Root cause: promptops_app/exporters/pdf_exporter.py has always required
reportlab (raises ExportUnsupportedError → export_service turns that into
ExportResult(success=False) → the router raises WorkflowError → the frontend
shows a generic "Export failed." toast), but reportlab was never actually
listed in requirements.txt / pyproject.toml / uv.lock. PDF export could not
have worked from a clean install in any environment. Fixed by adding the
dependency; this test pins it so a future dependency-file edit that drops it
again fails loudly here instead of silently at export time.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_reportlab_is_installed():
    """The dependency pdf_exporter.py actually requires. If this fails, PDF
    export is broken for everyone regardless of anything else in this file —
    see requirements.txt / pyproject.toml / uv.lock."""
    import reportlab  # noqa: F401


class TestBuildPdf:
    def test_produces_a_valid_pdf(self):
        from promptops_app.exporters.pdf_exporter import build_pdf

        buf = build_pdf("Test Course", [("Lesson 1", "Hello world.\nSecond line.")])
        data = buf.read()
        assert data.startswith(b"%PDF-")
        assert len(data) > 0

    def test_handles_multiple_blocks_and_blank_lines(self):
        from promptops_app.exporters.pdf_exporter import build_pdf

        buf = build_pdf("Multi-block Course", [
            ("Lesson 1", "Line one.\n\nLine two."),
            ("Lesson 2", "Another lesson's content."),
        ])
        data = buf.read()
        assert data.startswith(b"%PDF-")


class TestExportServicePdf:
    def _request(self, **overrides):
        from promptops_app.services.export_service import ExportRequest

        defaults = dict(
            fmt="pdf", topic="Test Course",
            blocks=[("Lesson 1", "Some real content.")],
            user_name="tester", is_admin=False,
        )
        defaults.update(overrides)
        return ExportRequest(**defaults)

    def test_export_content_succeeds_for_pdf(self):
        from promptops_app.services.export_service import export_content

        result = export_content(MagicMock(), self._request())
        assert result.success is True
        assert result.data.startswith(b"%PDF-")
        assert result.mime_type == "application/pdf"
        assert result.error_message is None or result.error_message == ""

    def test_export_content_reports_a_clean_error_for_empty_content(self):
        """Unrelated to the reportlab bug — pins that a genuinely invalid
        request still fails informatively (acceptance criteria: meaningful
        error, no raw exception dumped to the user)."""
        from promptops_app.services.export_service import export_content

        result = export_content(MagicMock(), self._request(blocks=[("Lesson 1", "")]))
        assert result.success is False
        assert "empty" in result.error_message.lower()


class TestTemplatesAreHonoured:
    """Acceptance criterion: the PDF must follow the SELECTED export template.

    build_pdf accepted `template` and ignored it, so every template produced a
    byte-identical file. These pin that each one is actually distinct and that
    the set matches the DOCX exporter's — a user picking "Teacher Guide" should
    get the same document in either format, not two unrelated layouts.
    """

    BLOCKS = [
        ("Lesson 1", "\n".join(["Intro text.", "1. What is lift?", "Some prose."])),
        ("Lesson 2", "\n".join(["More content.", "Is drag a force?"])),
    ]

    def test_every_template_produces_a_valid_pdf(self):
        from promptops_app.exporters.pdf_exporter import TEMPLATES, build_pdf

        for name in TEMPLATES:
            data = build_pdf("Course", self.BLOCKS, template=name).read()
            assert data.startswith(b"%PDF-"), name
            assert len(data) > 500, name

    def test_templates_differ_from_each_other(self):
        from promptops_app.exporters.pdf_exporter import TEMPLATES, build_pdf

        sizes = {n: len(build_pdf("Course", self.BLOCKS, template=n).read())
                 for n in TEMPLATES}
        # Identical byte counts across all five is exactly the bug: the
        # parameter accepted and discarded.
        assert len(set(sizes.values())) > 1, sizes

    def test_the_pdf_template_set_matches_the_docx_one(self):
        from promptops_app.exporters.docx_exporter import TEMPLATES as DOCX
        from promptops_app.exporters.pdf_exporter import TEMPLATES as PDF

        assert set(PDF) == set(DOCX)

    def test_unknown_template_falls_back_to_default(self):
        from promptops_app.exporters.pdf_exporter import build_pdf

        a = build_pdf("Course", self.BLOCKS, template="no_such_template").read()
        b = build_pdf("Course", self.BLOCKS, template="default").read()
        # Same structure; only the embedded timestamp/ids differ in length terms.
        assert a.startswith(b"%PDF-")
        assert abs(len(a) - len(b)) < 200

    def test_markup_in_content_does_not_break_the_build(self):
        """reportlab parses Paragraph text as mini-HTML and LLM content routinely
        contains <, > and &. Unescaped, this raised and surfaced as the same
        generic 'Export failed.' the ticket reports."""
        from promptops_app.exporters.pdf_exporter import build_pdf

        data = build_pdf("Course", [
            ("Lesson <1> & more", "Use <div> tags & 5 < 10 for a > b."),
        ]).read()
        assert data.startswith(b"%PDF-")


class TestErrorsAreUserFacing:
    """Acceptance criteria: a meaningful message for the Author, and no
    technical/backend detail exposed to them."""

    def _request(self, **overrides):
        from promptops_app.services.export_service import ExportRequest

        defaults = dict(fmt="pdf", topic="Test Course",
                        blocks=[("Lesson 1", "Some real content.")],
                        user_name="tester", is_admin=False)
        defaults.update(overrides)
        return ExportRequest(**defaults)

    def test_a_missing_library_does_not_leak_the_install_command(self, monkeypatch):
        from promptops_app.exporters.pdf_exporter import ExportUnsupportedError
        from promptops_app.services import export_service

        def boom(_request):
            raise ExportUnsupportedError(
                "PDF generation requires the 'reportlab' library. "
                "Install it with: pip install reportlab"
            )

        monkeypatch.setitem(export_service._BUILDERS, "pdf", boom)
        result = export_service.export_content(MagicMock(), self._request())

        assert result.success is False
        msg = result.error_message
        for leak in ("reportlab", "pip install", "Traceback", "ImportError"):
            assert leak not in msg, msg
        assert "DOCX" in msg or "HTML" in msg, "no alternative offered: " + msg

    def test_an_unexpected_failure_names_the_format_and_a_next_step(self, monkeypatch):
        from promptops_app.services import export_service

        def boom(_request):
            raise ValueError("paraparser: syntax error at /srv/app/foo.py line 42")

        monkeypatch.setitem(export_service._BUILDERS, "pdf", boom)
        result = export_service.export_content(MagicMock(), self._request())

        assert result.success is False
        assert "paraparser" not in result.error_message
        assert "/srv/app" not in result.error_message
        assert "PDF" in result.error_message

    def test_other_formats_still_export(self):
        """Regression guard for the ticket's last criterion."""
        from promptops_app.services.export_service import export_content

        for fmt in ("md", "html", "docx", "json", "xlsx"):
            result = export_content(MagicMock(), self._request(fmt=fmt))
            assert result.success is True, f"{fmt}: {result.error_message}"
