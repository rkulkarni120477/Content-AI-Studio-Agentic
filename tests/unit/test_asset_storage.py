"""Unit tests for asset storage helpers (image type sniffing)."""

from app.services.asset_storage import sniff_image_type

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
GIF = b"GIF89a" + b"\x00" * 16
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8


class TestSniffImageType:
    def test_detects_real_images(self):
        assert sniff_image_type(PNG) == "image/png"
        assert sniff_image_type(JPEG) == "image/jpeg"
        assert sniff_image_type(GIF) == "image/gif"
        assert sniff_image_type(WEBP) == "image/webp"

    def test_rejects_non_images(self):
        assert sniff_image_type(b"%PDF-1.7 ...") is None
        assert sniff_image_type(b"<html>not an image</html>") is None
        assert sniff_image_type(b"MZ\x90\x00") is None  # exe
        assert sniff_image_type(b"") is None

    def test_rejects_disguised_content(self):
        # Claims to be an image via header, but bytes are a script.
        assert sniff_image_type(b"<script>alert(1)</script>") is None
