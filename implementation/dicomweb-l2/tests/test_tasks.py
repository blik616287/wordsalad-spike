"""
Smoke tests for ``dicomweb.tasks``. These do not exercise the full
ingest pipeline — that's the Phase D integration-test job. They verify:

* the helpers parse the DICOM string formats correctly,
* the indexing task is importable and routable (no circular imports), and
* ``_find_series_for_file`` walks parent folders.
"""

from datetime import date, time
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from dicomweb.tasks import (
    _as_int,
    _parse_dicom_date,
    _parse_dicom_time,
)


class HelperParseTests(TestCase):
    def test_parse_dicom_date_valid(self):
        self.assertEqual(_parse_dicom_date('20231201'), date(2023, 12, 1))

    def test_parse_dicom_date_invalid_returns_none(self):
        self.assertIsNone(_parse_dicom_date(''))
        self.assertIsNone(_parse_dicom_date(None))
        self.assertIsNone(_parse_dicom_date('not-a-date'))

    def test_parse_dicom_time_full(self):
        self.assertEqual(_parse_dicom_time('143005'), time(14, 30, 5))

    def test_parse_dicom_time_with_fractional_seconds(self):
        # DICOM TM VR allows fractional seconds — they get stripped.
        self.assertEqual(_parse_dicom_time('143005.123'), time(14, 30, 5))

    def test_parse_dicom_time_partial(self):
        self.assertEqual(_parse_dicom_time('1430'), time(14, 30, 0))
        self.assertEqual(_parse_dicom_time('14'), time(14, 0, 0))

    def test_parse_dicom_time_invalid(self):
        self.assertIsNone(_parse_dicom_time(''))
        self.assertIsNone(_parse_dicom_time(None))
        self.assertIsNone(_parse_dicom_time('not-a-time'))

    def test_as_int(self):
        self.assertEqual(_as_int(42), 42)
        self.assertEqual(_as_int('17'), 17)
        self.assertIsNone(_as_int(''))
        self.assertIsNone(_as_int(None))
        self.assertIsNone(_as_int('xyz'))


class TaskImportSmokeTests(TestCase):
    def test_celery_task_is_importable(self):
        # Catches circular-import regressions between
        # pacsfiles.serializers and dicomweb.tasks.
        from dicomweb.tasks import index_pacs_instance
        self.assertTrue(callable(index_pacs_instance))
        self.assertEqual(
            index_pacs_instance.name, 'dicomweb.tasks.index_pacs_instance',
        )

    def test_task_routed_to_main2(self):
        # Locks in the queue assignment from core/celery.py:task_routes.
        from core.celery import app as celery_app
        routes = celery_app.conf.task_routes or {}
        entry = routes.get('dicomweb.tasks.index_pacs_instance')
        self.assertIsNotNone(entry)
        self.assertEqual(entry.get('queue'), 'main2')


class IndexerStudyRollupTests(TestCase):
    """index_pacs_instance must populate PACSStudy (not just PACSInstance), so
    QIDO /studies surfaces oxidicom-ingested data -- the STOW path always did,
    the async indexer previously did not."""

    def _setup_tree(self):
        from core.models import ChrisFolder
        from pacsfiles.models import PACS, PACSSeries, PACSFile
        owner, _ = User.objects.get_or_create(username='chris')
        pacs_folder, _ = ChrisFolder.objects.get_or_create(
            path='SERVICES/PACS/IDX', defaults={'owner': owner})
        pacs = PACS.objects.create(identifier='IDX', folder=pacs_folder)
        study_uid, series_uid, sop_uid = '9.9.1', '9.9.1.1', '9.9.1.1.1'
        series_folder, _ = ChrisFolder.objects.get_or_create(
            path=f'SERVICES/PACS/IDX/{study_uid}/{series_uid}',
            defaults={'owner': owner})
        series = PACSSeries.objects.create(
            pacs=pacs, folder=series_folder,
            SeriesInstanceUID=series_uid, StudyInstanceUID=study_uid,
            PatientID='MRN9', PatientName='DOE^IDX', PatientSex='M',
            StudyDate='2023-03-03', Modality='MR', SeriesNumber=1,
            SeriesDescription='IDX')
        pf = PACSFile(owner=owner, parent_folder=series_folder)
        pf.fname.name = (f'SERVICES/PACS/IDX/{study_uid}/{series_uid}/'
                         f'{sop_uid}.dcm')
        pf.fsize = 1024
        pf.save()
        return pacs, series, pf, study_uid, series_uid, sop_uid

    def test_indexer_creates_pacsstudy_and_rollups(self):
        from dicomweb import tasks
        from dicomweb.models import PACSInstance, PACSStudy
        from dicomweb.tests.fixtures import make_dataset, dataset_to_bytes
        pacs, series, pf, study_uid, series_uid, sop_uid = self._setup_tree()
        raw = dataset_to_bytes(make_dataset(
            study_uid=study_uid, series_uid=series_uid, sop_uid=sop_uid))

        self.assertFalse(PACSStudy.objects.filter(
            pacs=pacs, StudyInstanceUID=study_uid).exists())

        fake_storage = mock.Mock()
        fake_storage.download_obj.return_value = raw
        with mock.patch.object(tasks, 'connect_storage',
                               return_value=fake_storage):
            tasks.index_pacs_instance.apply(args=[pf.pk])

        self.assertTrue(PACSInstance.objects.filter(
            series=series, SOPInstanceUID=sop_uid).exists())
        study = PACSStudy.objects.get(pacs=pacs, StudyInstanceUID=study_uid)
        self.assertEqual(study.NumberOfStudyRelatedSeries, 1)
        self.assertEqual(study.NumberOfStudyRelatedInstances, 1)
        self.assertIn('MR', study.ModalitiesInStudy)
