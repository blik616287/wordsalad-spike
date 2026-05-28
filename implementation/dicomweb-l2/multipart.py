"""
Minimal ``multipart/related`` parser for STOW-RS request bodies.

STOW-RS sends ``Content-Type: multipart/related; type="application/dicom";
boundary=...`` where each part is a complete PS3.10 DICOM stream. This is NOT
``multipart/form-data`` (so Django's request.FILES / DRF MultiPartParser do not
apply) -- it is RFC 2387 multipart/related, which differs in that parts are
identified by ``Content-Type`` / ``Content-Location`` rather than form field
names. We parse it by hand to keep the binary DICOM bytes intact.

Framework-free and unit-testable -- see ``tests/test_multipart.py`` (parser
round-trip) and the STOW cases in ``tests/test_views.py`` (end-to-end).

References: RFC 2387 (multipart/related); PS3.18 §8.7.3, §10.5.2.
"""
from dataclasses import dataclass


try:
    from rest_framework.parsers import BaseParser
except Exception:  # pragma: no cover - DRF absent in standalone unit runs
    BaseParser = object


class RawPassthroughParser(BaseParser):
    """
    DRF parser that hands the view the raw request bytes for any media type.

    STOW-RS bodies are ``multipart/related`` (NOT ``multipart/form-data``), so
    DRF's ``MultiPartParser`` must not touch them. We attach this with
    ``media_type = '*/*'`` so content negotiation always selects it, and the
    STOW view reads ``request.body`` and parses the multipart itself.
    """
    media_type = '*/*'

    def parse(self, stream, media_type=None, parser_context=None):
        return stream.read()


class MultipartError(ValueError):
    """Raised on a malformed multipart/related body -> HTTP 400."""


@dataclass
class Part:
    content_type: str
    headers: dict
    content: bytes


def _extract_boundary(content_type):
    # boundary may be quoted: boundary="abc" or boundary=abc
    marker = 'boundary='
    idx = content_type.lower().find(marker)
    if idx == -1:
        raise MultipartError('no boundary in Content-Type')
    raw = content_type[idx + len(marker):].strip()
    # boundary runs to the next ';' or end.
    raw = raw.split(';')[0].strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    if not raw:
        raise MultipartError('empty boundary')
    return raw


def parse_multipart_related(body, content_type):
    """
    Parse ``body`` (bytes) into a list of ``Part``.

    Tolerates both CRLF and bare-LF line endings between headers, and an
    optional epilogue after the closing delimiter. Each part's leading CRLF and
    its single trailing CRLF before the next delimiter are stripped from the
    content (so the DICOM bytes are exact).
    """
    if isinstance(body, str):
        body = body.encode('latin-1')
    boundary = _extract_boundary(content_type)
    delim = b'--' + boundary.encode('ascii')

    # Split on the delimiter. The first chunk is the preamble (ignored); the
    # last is the epilogue after '--boundary--' (ignored).
    chunks = body.split(delim)
    parts = []
    for chunk in chunks[1:]:
        # Closing delimiter: starts with '--'
        if chunk[:2] == b'--':
            break
        # Strip the leading CRLF/LF that follows the delimiter line.
        if chunk[:2] == b'\r\n':
            chunk = chunk[2:]
        elif chunk[:1] == b'\n':
            chunk = chunk[1:]
        # Strip the trailing CRLF/LF before the next delimiter.
        if chunk[-2:] == b'\r\n':
            chunk = chunk[:-2]
        elif chunk[-1:] == b'\n':
            chunk = chunk[:-1]

        headers, content = _split_headers(chunk)
        ct = headers.get('content-type', '')
        parts.append(Part(content_type=ct, headers=headers, content=content))
    return parts


def _split_headers(chunk):
    # Header block ends at the first blank line (CRLFCRLF or LFLF).
    for sep in (b'\r\n\r\n', b'\n\n'):
        idx = chunk.find(sep)
        if idx != -1:
            header_blob = chunk[:idx]
            content = chunk[idx + len(sep):]
            return _parse_headers(header_blob), content
    # No header/body separator -> treat the whole chunk as content.
    return {}, chunk


def _parse_headers(blob):
    headers = {}
    text = blob.decode('latin-1', errors='replace')
    for line in text.replace('\r\n', '\n').split('\n'):
        if not line.strip():
            continue
        if ':' in line:
            name, _, value = line.partition(':')
            headers[name.strip().lower()] = value.strip()
    return headers
