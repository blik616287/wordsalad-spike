"""
Unit tests for the multipart/related parser (``dicomweb.multipart``).

Verifies that DICOM byte payloads survive parsing exactly (the round-trip that
STOW-RS depends on), including CRLF handling and multiple parts.
"""
from django.test import SimpleTestCase

from dicomweb import multipart
from dicomweb.tests.fixtures import build_multipart_related


class BoundaryTests(SimpleTestCase):
    def test_quoted_boundary(self):
        ct = 'multipart/related; type="application/dicom"; boundary="ABC"'
        self.assertEqual(multipart._extract_boundary(ct), 'ABC')

    def test_unquoted_boundary(self):
        ct = 'multipart/related; boundary=ABC; type="application/dicom"'
        self.assertEqual(multipart._extract_boundary(ct), 'ABC')

    def test_missing_boundary_raises(self):
        with self.assertRaises(multipart.MultipartError):
            multipart._extract_boundary('multipart/related')


class ParseTests(SimpleTestCase):
    def test_single_part_roundtrip(self):
        blob = b'\x00\x01\x02DICM-bytes\xff\xfe'
        body, ct = build_multipart_related([blob])
        parts = multipart.parse_multipart_related(body, ct)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].content, blob)
        self.assertIn('application/dicom', parts[0].content_type)

    def test_multiple_parts(self):
        blobs = [b'AAAA', b'BBBBBB', b'C']
        body, ct = build_multipart_related(blobs)
        parts = multipart.parse_multipart_related(body, ct)
        self.assertEqual([p.content for p in parts], blobs)

    def test_binary_with_embedded_crlf_preserved(self):
        blob = b'before\r\nafter\r\n\r\nend'
        body, ct = build_multipart_related([blob])
        parts = multipart.parse_multipart_related(body, ct)
        self.assertEqual(parts[0].content, blob)
