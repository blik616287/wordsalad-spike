"""
Unit tests for the QIDO-RS query parser (``dicomweb.query_parser``).

Framework-free w.r.t. the DB: we build ``Q`` objects and inspect their
structure rather than hitting Postgres. Run with ``just test dicomweb``.
"""
import datetime

from django.http import QueryDict
from django.test import SimpleTestCase

from dicomweb import query_parser as qp


def _qd(query_string):
    return QueryDict(query_string)


def _children(q):
    """Flatten a Q tree into a list of (lookup, value) leaves + connector."""
    leaves = []

    def walk(node):
        for child in node.children:
            if isinstance(child, type(node)):
                walk(child)
            else:
                leaves.append(child)
    walk(q)
    return leaves


class ResolveAttrTests(SimpleTestCase):
    def test_keyword_resolves(self):
        a = qp.resolve_attr('study', 'PatientName')
        self.assertEqual(a.tag, '00100010')

    def test_tag_hex_resolves(self):
        a = qp.resolve_attr('study', '00100010')
        self.assertEqual(a.keyword, 'PatientName')

    def test_unknown_tag_returns_none(self):
        # well-formed hex but not in the study map
        self.assertIsNone(qp.resolve_attr('study', '00080070'))

    def test_malformed_attribute_raises(self):
        with self.assertRaises(qp.QidoQueryError):
            qp.resolve_attr('study', 'NotAKeyword!!')


class SingleValueTests(SimpleTestCase):
    def test_uid_exact(self):
        pq = qp.parse('study', _qd('StudyInstanceUID=1.2.3'))
        self.assertIn(('StudyInstanceUID', '1.2.3'), _children(pq.filter_q))

    def test_tag_hex_form(self):
        pq = qp.parse('study', _qd('0020000D=1.2.3'))
        self.assertIn(('StudyInstanceUID', '1.2.3'), _children(pq.filter_q))

    def test_integer_value(self):
        pq = qp.parse('series', _qd('SeriesNumber=3'))
        self.assertIn(('SeriesNumber', 3), _children(pq.filter_q))

    def test_non_integer_for_int_field_400(self):
        with self.assertRaises(qp.QidoQueryError):
            qp.parse('series', _qd('SeriesNumber=abc'))


class MultiValueTests(SimpleTestCase):
    def test_uid_list(self):
        pq = qp.parse('study', _qd('StudyInstanceUID=1.2,3.4'))
        leaves = _children(pq.filter_q)
        self.assertIn(('StudyInstanceUID__in', ['1.2', '3.4']), leaves)

    def test_cs_or_list(self):
        pq = qp.parse('series', _qd('Modality=CT,MR'))
        leaves = _children(pq.filter_q)
        self.assertIn(('Modality', 'CT'), leaves)
        self.assertIn(('Modality', 'MR'), leaves)


class RangeTests(SimpleTestCase):
    def test_closed_range(self):
        # DA values are coerced from DICOM YYYYMMDD into native date() so the
        # ORM lookup against a DateField is valid (raw strings would error in PG).
        pq = qp.parse('study', _qd('StudyDate=20230101-20231231'))
        leaves = dict(_children(pq.filter_q))
        self.assertEqual(leaves['StudyDate__gte'], datetime.date(2023, 1, 1))
        self.assertEqual(leaves['StudyDate__lte'], datetime.date(2023, 12, 31))

    def test_single_date_coerced(self):
        pq = qp.parse('study', _qd('StudyDate=20230102'))
        self.assertIn(('StudyDate', datetime.date(2023, 1, 2)),
                      _children(pq.filter_q))

    def test_open_lower(self):
        pq = qp.parse('study', _qd('StudyDate=20230101-'))
        leaves = dict(_children(pq.filter_q))
        self.assertEqual(leaves['StudyDate__gte'], datetime.date(2023, 1, 1))
        self.assertNotIn('StudyDate__lte', leaves)

    def test_open_upper(self):
        pq = qp.parse('study', _qd('StudyDate=-20231231'))
        leaves = dict(_children(pq.filter_q))
        self.assertEqual(leaves['StudyDate__lte'], datetime.date(2023, 12, 31))

    def test_bad_date_value_400(self):
        with self.assertRaises(qp.QidoQueryError):
            qp.parse('study', _qd('StudyDate=2023XX01'))

    def test_bare_dash_invalid(self):
        with self.assertRaises(qp.QidoQueryError):
            qp.parse('study', _qd('StudyDate=-'))


class WildcardTests(SimpleTestCase):
    def test_pn_wildcard_to_iregex(self):
        pq = qp.parse('study', _qd('PatientName=DOE*'))
        leaves = dict(_children(pq.filter_q))
        self.assertIn('PatientName__iregex', leaves)
        self.assertEqual(leaves['PatientName__iregex'], '^DOE.*$')

    def test_question_mark_wildcard(self):
        pq = qp.parse('study', _qd('PatientName=DO?'))
        leaves = dict(_children(pq.filter_q))
        self.assertEqual(leaves['PatientName__iregex'], '^DO.$')

    def test_wildcard_on_numeric_vr_rejected(self):
        with self.assertRaises(qp.QidoQueryError):
            qp.parse('series', _qd('SeriesNumber=1*'))


class FuzzyTests(SimpleTestCase):
    def test_fuzzy_pn_uses_trigram(self):
        pq = qp.parse('study', _qd('PatientName=joh&fuzzymatching=true'))
        leaves = dict(_children(pq.filter_q))
        self.assertIn('PatientName__trigram_similar', leaves)
        self.assertEqual(leaves['PatientName__trigram_similar'], 'joh')


class IncludeFieldTests(SimpleTestCase):
    def test_includefield_keyword(self):
        pq = qp.parse('study', _qd('includefield=StudyDescription'))
        self.assertIn('STUDYDESCRIPTION', pq.includefields)

    def test_includefield_comma_list_and_tag(self):
        pq = qp.parse('study', _qd('includefield=00081030,AccessionNumber'))
        self.assertIn('STUDYDESCRIPTION', pq.includefields)
        self.assertIn('ACCESSIONNUMBER', pq.includefields)

    def test_includefield_all(self):
        pq = qp.parse('study', _qd('includefield=all'))
        self.assertTrue(pq.include_all)

    def test_return_key_no_value(self):
        pq = qp.parse('study', _qd('AccessionNumber'))
        self.assertIn('ACCESSIONNUMBER', pq.includefields)


class PaginationTests(SimpleTestCase):
    def test_defaults(self):
        pq = qp.parse('study', _qd(''))
        self.assertEqual(pq.limit, qp.DEFAULT_LIMIT)
        self.assertEqual(pq.offset, 0)

    def test_limit_offset(self):
        pq = qp.parse('study', _qd('limit=20&offset=40'))
        self.assertEqual(pq.limit, 20)
        self.assertEqual(pq.offset, 40)

    def test_limit_capped(self):
        pq = qp.parse('study', _qd('limit=999999'))
        self.assertEqual(pq.limit, qp.MAX_LIMIT)

    def test_negative_rejected(self):
        with self.assertRaises(qp.QidoQueryError):
            qp.parse('study', _qd('limit=-1'))

    def test_non_integer_rejected(self):
        with self.assertRaises(qp.QidoQueryError):
            qp.parse('study', _qd('offset=foo'))


class UnsupportedKeyTests(SimpleTestCase):
    def test_unsupported_match_key_ignored(self):
        # 00080070 (Manufacturer) is not a study-level filter -> ignored,
        # no filter term, no error.
        pq = qp.parse('study', _qd('00080070=ACME'))
        self.assertEqual(len(_children(pq.filter_q)), 0)
