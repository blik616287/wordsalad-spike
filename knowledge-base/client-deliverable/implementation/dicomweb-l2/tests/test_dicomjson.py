"""
Unit tests for the DICOM JSON Model encoder (``dicomweb.dicomjson``).

These are framework-free (no DB), so they run as plain Django SimpleTestCase
(or pytest functions) inside a CUBE checkout: ``just test dicomweb``.
"""
import datetime

from django.test import SimpleTestCase

from dicomweb import dicomjson


class NormalizeTagTests(SimpleTestCase):
    def test_canonical_passthrough(self):
        self.assertEqual(dicomjson.normalize_tag('0020000D'), '0020000D')

    def test_lowercase_uppercased(self):
        self.assertEqual(dicomjson.normalize_tag('0020000d'), '0020000D')

    def test_comma_and_paren_forms(self):
        self.assertEqual(dicomjson.normalize_tag('(0020,000D)'), '0020000D')
        self.assertEqual(dicomjson.normalize_tag('0020,000D'), '0020000D')

    def test_int_form(self):
        self.assertEqual(dicomjson.normalize_tag(0x0020000D), '0020000D')

    def test_bad_tag_raises(self):
        for bad in ('XYZ', '0020', '0020000DD', 'ZZZZZZZZ'):
            with self.assertRaises(ValueError):
                dicomjson.normalize_tag(bad)


class ElementEncodingTests(SimpleTestCase):
    def test_ui_string(self):
        self.assertEqual(
            dicomjson.element('UI', '1.2.3'),
            {'vr': 'UI', 'Value': ['1.2.3']})

    def test_pn_object_form(self):
        self.assertEqual(
            dicomjson.element('PN', 'DOE^JANE'),
            {'vr': 'PN', 'Value': [{'Alphabetic': 'DOE^JANE'}]})

    def test_pn_multi_component_groups(self):
        el = dicomjson.element('PN', 'Yamada^Tarou=山田^太郎')
        self.assertEqual(el['Value'][0]['Alphabetic'], 'Yamada^Tarou')
        self.assertEqual(el['Value'][0]['Ideographic'], '山田^太郎')

    def test_is_emitted_as_integer(self):
        self.assertEqual(dicomjson.element('IS', 142),
                         {'vr': 'IS', 'Value': [142]})
        self.assertEqual(dicomjson.element('IS', '142'),
                         {'vr': 'IS', 'Value': [142]})

    def test_us_integer(self):
        self.assertEqual(dicomjson.element('US', 256),
                         {'vr': 'US', 'Value': [256]})

    def test_ds_number(self):
        self.assertEqual(dicomjson.element('DS', '1.5'),
                         {'vr': 'DS', 'Value': [1.5]})

    def test_date_serialized_as_dicom_string(self):
        d = datetime.date(2023, 1, 2)
        self.assertEqual(dicomjson.element('DA', d),
                         {'vr': 'DA', 'Value': ['20230102']})

    def test_time_serialized_as_dicom_string(self):
        t = datetime.time(14, 30, 52)
        self.assertEqual(dicomjson.element('TM', t),
                         {'vr': 'TM', 'Value': ['143052']})

    def test_multi_value_cs(self):
        self.assertEqual(dicomjson.element('CS', ['CT', 'MR']),
                         {'vr': 'CS', 'Value': ['CT', 'MR']})

    def test_empty_value_omitted(self):
        self.assertIsNone(dicomjson.element('LO', ''))
        self.assertIsNone(dicomjson.element('LO', None))
        self.assertIsNone(dicomjson.element('CS', []))

    def test_empty_pn_omitted(self):
        self.assertIsNone(dicomjson.element('PN', ''))

    def test_bulk_vr_emitted_without_value(self):
        self.assertEqual(dicomjson.element('OW', b'whatever'), {'vr': 'OW'})

    def test_bulkdata_uri(self):
        self.assertEqual(
            dicomjson.bulkdata_element('OW', 'http://x/frames/1'),
            {'vr': 'OW', 'BulkDataURI': 'http://x/frames/1'})


class DatasetTests(SimpleTestCase):
    def test_dataset_omits_empty_and_keys_by_tag(self):
        ds = dicomjson.dataset([
            ('0020000D', 'UI', '1.2.3'),
            ('00100010', 'PN', 'DOE^JANE'),
            ('00081030', 'LO', ''),            # empty -> omitted
            ('00100040', 'CS', 'F'),
        ])
        self.assertIn('0020000D', ds)
        self.assertIn('00100010', ds)
        self.assertNotIn('00081030', ds)
        self.assertEqual(ds['00100040'], {'vr': 'CS', 'Value': ['F']})

    def test_sequence(self):
        item = dicomjson.dataset([('00081150', 'UI', '1.2')])
        sq = dicomjson.sequence([item])
        self.assertEqual(sq['vr'], 'SQ')
        self.assertEqual(sq['Value'][0]['00081150']['Value'], ['1.2'])
