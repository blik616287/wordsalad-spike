"""
Unit tests for the row -> DICOM JSON Model serializers
(``dicomweb.serializers``), using lightweight stand-in objects so they do not
require the DB. The serializers only read attributes off the row and build URLs
via the injected ``RetrieveURLBuilder``, so a ``types.SimpleNamespace`` is a
faithful stand-in for the model rows here.

The full DB-backed path is exercised by ``test_qido_views``.
"""
from types import SimpleNamespace
import datetime

from django.test import SimpleTestCase

from dicomweb import serializers as dcm


class _FakeRequest:
    def build_absolute_uri(self, path):
        return 'http://testserver' + path


def _urls():
    return dcm.RetrieveURLBuilder(_FakeRequest(), 'BCH')


class SerializeStudyTests(SimpleTestCase):
    def _study(self):
        return SimpleNamespace(
            StudyInstanceUID='1.2.3',
            StudyDate=datetime.date(2023, 1, 2),
            StudyTime=datetime.time(14, 30, 52),
            AccessionNumber='A12345',
            StudyDescription='CHEST CT',
            ReferringPhysicianName='SMITH^JOHN',
            PatientName='DOE^JANE',
            PatientID='MRN0001',
            PatientBirthDate=datetime.date(1980, 1, 1),
            PatientSex='F',
            NumberOfStudyRelatedSeries=3,
            NumberOfStudyRelatedInstances=142,
            modalities_list=lambda: ['CT', 'MR'],
        )

    def test_required_study_attributes_present(self):
        ds = dcm.serialize_study(self._study(), _urls())
        self.assertEqual(ds['0020000D'], {'vr': 'UI', 'Value': ['1.2.3']})
        self.assertEqual(ds['00080020'], {'vr': 'DA', 'Value': ['20230102']})
        self.assertEqual(ds['00080030'], {'vr': 'TM', 'Value': ['143052']})
        self.assertEqual(ds['00080061'], {'vr': 'CS', 'Value': ['CT', 'MR']})
        self.assertEqual(ds['00100010'],
                         {'vr': 'PN', 'Value': [{'Alphabetic': 'DOE^JANE'}]})
        self.assertEqual(ds['00201206'], {'vr': 'IS', 'Value': [3]})
        self.assertEqual(ds['00201208'], {'vr': 'IS', 'Value': [142]})

    def test_retrieve_url_points_at_wado_study(self):
        ds = dcm.serialize_study(self._study(), _urls())
        self.assertEqual(
            ds['00081190']['Value'][0],
            'http://testserver/dicom-web/pacs/BCH/studies/1.2.3')


class SerializeSeriesTests(SimpleTestCase):
    def _series(self):
        return SimpleNamespace(
            SeriesInstanceUID='1.3.4', StudyInstanceUID='1.2.3',
            Modality='CT', Manufacturer='ACME', SeriesDescription='AXIAL',
            BodyPartExamined='CHEST', ProtocolName='ROUTINE', SeriesNumber=2,
            PerformedProcedureStepStartDate=None,
            PerformedProcedureStepStartTime=None,
        )

    def test_series_attributes_and_retrieve_url(self):
        ds = dcm.serialize_series(self._series(), _urls(), num_instances=50)
        self.assertEqual(ds['0020000E'], {'vr': 'UI', 'Value': ['1.3.4']})
        self.assertEqual(ds['00080060'], {'vr': 'CS', 'Value': ['CT']})
        self.assertEqual(ds['00200011'], {'vr': 'IS', 'Value': [2]})
        self.assertEqual(ds['00201209'], {'vr': 'IS', 'Value': [50]})
        self.assertEqual(
            ds['00081190']['Value'][0],
            'http://testserver/dicom-web/pacs/BCH/studies/1.2.3/series/1.3.4')

    def test_empty_optional_attributes_omitted(self):
        ds = dcm.serialize_series(self._series(), _urls(), num_instances=1)
        self.assertNotIn('00400244', ds)  # PerformedProcedureStepStartDate


class SerializeInstanceTests(SimpleTestCase):
    def _instance(self):
        series = SimpleNamespace(StudyInstanceUID='1.2.3',
                                 SeriesInstanceUID='1.3.4')
        return SimpleNamespace(
            series=series, SOPClassUID='1.2.840.10008.5.1.4.1.1.2',
            SOPInstanceUID='1.4.5', InstanceNumber=7, NumberOfFrames=1,
            Rows=256, Columns=256, BitsAllocated=16)

    def test_instance_attributes(self):
        ds = dcm.serialize_instance(self._instance(), _urls())
        self.assertEqual(ds['00080018'], {'vr': 'UI', 'Value': ['1.4.5']})
        self.assertEqual(ds['00280010'], {'vr': 'US', 'Value': [256]})
        self.assertEqual(ds['00200013'], {'vr': 'IS', 'Value': [7]})
        self.assertTrue(ds['00081190']['Value'][0].endswith(
            '/studies/1.2.3/series/1.3.4/instances/1.4.5'))
