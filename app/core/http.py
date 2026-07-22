"""HTTP response helpers."""

from __future__ import annotations

from urllib.parse import quote


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Build a Content-Disposition header value that is safe for any filename.

    HTTP header values must be Latin-1 encodable. Filenames derived from
    user content can contain characters outside Latin-1 (e.g. the em-dash
    "—"), which would raise a UnicodeEncodeError when the ASGI server
    renders the response headers. This follows RFC 6266 by emitting both an
    ASCII ``filename`` fallback and a UTF-8 ``filename*`` form, so the value
    is always pure ASCII (and therefore Latin-1 safe) while modern browsers
    still receive the original name.
    """
    name = filename or "download"
    # ASCII fallback for legacy clients — replace anything non-ASCII, and
    # avoid quotes/backslashes that would break the quoted-string.
    ascii_name = name.encode("ascii", "replace").decode("ascii")
    ascii_name = ascii_name.replace('"', "'").replace("\\", "_").replace("?", "_")
    # RFC 5987 UTF-8 form (percent-encoded, so also pure ASCII on the wire).
    utf8_name = quote(name, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
