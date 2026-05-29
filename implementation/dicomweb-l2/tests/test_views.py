"""
DB- and HTTP-backed tests for the QIDO / WADO / STOW endpoints.

These run inside a CUBE checkout (they need the real ``pacsfiles`` models,
``core.models.ChrisFolder``, the storage backend, and the auth chain). They are
written against the SCHEMA THIS SPIKE ADDS: the ``PACSStudy`` model
(``dicomweb.0002``), the nullable ``PACSSeries.study`` FK, and the six new
``PACSSeries`` columns from Phase A. They will NOT pass against stock upstream
CUBE until those migrations are applied -- see README "Known limitations".

Tagged so CI can pick them up alongside the rest of the suite:
``just test dicomweb`` runs them; the heavier storage round-trip in the WADO/STOW
cases is tagged ``integration`` and excluded by ``--exclude-tag integration``.
"""
from django.contrib.auth.models import User, Group
from django.test import tag
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import ChrisFolder
from pacsfiles.models import PACS, PACSSeries, PACSFile

from dicomweb.models import PACSStudy, PACSInstance
from dicomweb.tests.fixtures import (make_dataset, dataset_to_bytes,
                                     build_multipart_related)


def _mk_folder(path, owner):
    folder, _ = ChrisFolder.objects.get_or_create(
        path=path, defaults={'owner': owner})
    return folder


class DicomwebTestBase(APITestCase):
    """Builds a small Patient->Study->Series->Instance tree for one PACS."""

    @classmethod
    def setUpTestData(cls):
        # CUBE seeds a 'chris' user via a data migration, so reuse it rather
        # than create_user (which hits a unique-username IntegrityError when the
        # tests run inside a real CUBE). Tests use force_authenticate, so no
        # password is needed.
        cls.chris, _ = User.objects.get_or_create(username='chris')
        cls.pacs_user = User.objects.create_user(username='alice',
                                                 password='alice1234')
        grp, _ = Group.objects.get_or_create(name='pacs_users')
        cls.pacs_user.groups.add(grp)
        cls.chris.groups.add(grp)

        pacs_folder = _mk_folder('SERVICES/PACS/BCH', cls.chris)
        cls.pacs = PACS.objects.create(identifier='BCH', folder=pacs_folder)

        cls.study_uid = '1.2.840.111.1'
        cls.study = PACSStudy.objects.create(
            pacs=cls.pacs, StudyInstanceUID=cls.study_uid,
            PatientID='MRN0001', PatientName='DOE^JANE', PatientSex='F',
            StudyDate='2023-01-02', AccessionNumber='A12345',
            StudyDescription='CHEST CT',
            ModalitiesInStudy='CT', NumberOfStudyRelatedSeries=1,
            NumberOfStudyRelatedInstances=2)

        cls.series_uid = '1.2.840.111.1.1'
        series_folder = _mk_folder(
            f'SERVICES/PACS/BCH/{cls.study_uid}/{cls.series_uid}', cls.chris)
        cls.series = PACSSeries.objects.create(
            pacs=cls.pacs, folder=series_folder,
            SeriesInstanceUID=cls.series_uid, StudyInstanceUID=cls.study_uid,
            PatientID='MRN0001', PatientName='DOE^JANE', PatientSex='F',
            StudyDate='2023-01-02', Modality='CT', SeriesNumber=1,
            SeriesDescription='AXIAL')

        cls.instances = []
        for n in (1, 2):
            sop = f'1.2.840.111.1.1.{n}'
            pf = PACSFile(owner=cls.chris, parent_folder=series_folder)
            pf.fname.name = (f'SERVICES/PACS/BCH/{cls.study_uid}/'
                             f'{cls.series_uid}/{sop}.dcm')
            pf.fsize = 1024
            pf.save()
            inst = PACSInstance.objects.create(
                series=cls.series, pacs_file=pf,
                SOPClassUID='1.2.840.10008.5.1.4.1.1.2', SOPInstanceUID=sop,
                InstanceNumber=n, Rows=256, Columns=256, BitsAllocated=16,
                NumberOfFrames=1,
                TransferSyntaxUID='1.2.840.10008.1.2.1')
            cls.instances.append(inst)

    def auth(self):
        self.client.force_authenticate(user=self.chris)


@tag('qido')
class QidoStudyTests(DicomwebTestBase):
    def setUp(self):
        self.auth()

    def test_studies_requires_auth(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get('/dicom-web/pacs/BCH/studies')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED,
                                         status.HTTP_403_FORBIDDEN))

    def test_studies_dicom_json(self):
        resp = self.client.get('/dicom-web/pacs/BCH/studies',
                               HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'].split(';')[0],
                         'application/dicom+json')
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['0020000D']['Value'], [self.study_uid])
        self.assertEqual(body[0]['00201208']['Value'], [2])

    def test_studies_filter_no_match_returns_empty_200(self):
        resp = self.client.get('/dicom-web/pacs/BCH/studies?PatientID=NOPE',
                               HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json(), [])

    def test_studies_patientname_wildcard(self):
        resp = self.client.get('/dicom-web/pacs/BCH/studies?PatientName=DOE*',
                               HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(len(resp.json()), 1)

    def test_studies_patientname_fuzzy(self):
        # ?fuzzymatching=true -> Postgres pg_trgm __trigram_similar executed
        # against the real test DB; proves the 0003 pg_trgm migration works.
        resp = self.client.get(
            '/dicom-web/pacs/BCH/studies?PatientName=DOE^JANE&fuzzymatching=true',
            HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.json()), 1)

    def test_bad_query_400(self):
        resp = self.client.get('/dicom-web/pacs/BCH/studies?StudyDate=-',
                               HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_application_json_accepted(self):
        resp = self.client.get('/dicom-web/pacs/BCH/studies',
                               HTTP_ACCEPT='application/json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_unknown_pacs_404(self):
        resp = self.client.get('/dicom-web/pacs/NOPE/studies',
                               HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


@tag('qido')
class QidoSeriesInstanceTests(DicomwebTestBase):
    def setUp(self):
        self.auth()

    def test_study_series(self):
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series',
            HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertEqual(body[0]['0020000E']['Value'], [self.series_uid])
        self.assertEqual(body[0]['00201209']['Value'], [2])  # NumberOfSeriesRelatedInstances

    def test_series_instances(self):
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/instances',
            HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.json()), 2)

    def test_modality_multivalue_filter(self):
        resp = self.client.get('/dicom-web/pacs/BCH/series?00080060=CT,MR',
                               HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(len(resp.json()), 1)

    def test_cross_study_instances(self):
        resp = self.client.get('/dicom-web/pacs/BCH/instances',
                               HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(len(resp.json()), 2)

    def test_unknown_study_series_404(self):
        resp = self.client.get(
            '/dicom-web/pacs/BCH/studies/9.9.9/series',
            HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


@tag('wado')
class WadoMetadataTests(DicomwebTestBase):
    def setUp(self):
        self.auth()

    def test_series_metadata(self):
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/metadata',
            HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertEqual(len(body), 2)
        # PixelData referenced as BulkDataURI, not inlined.
        self.assertIn('7FE00010', body[0])
        self.assertIn('BulkDataURI', body[0]['7FE00010'])

    def test_instance_metadata_404_for_unknown(self):
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/instances/9.9.9/metadata',
            HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # (Native frame + bulkdata retrieval are exercised in WadoRetrieveTests,
    # which stages real pixel bytes in storage.)


@tag('wado', 'integration')
class WadoRetrieveTests(DicomwebTestBase):
    """
    Retrieval streams bytes from storage; needs a writable storage backend.
    Tagged ``integration`` because it round-trips through ``core.storage``.
    """
    def setUp(self):
        self.auth()
        # Write real DICOM bytes to storage for each instance so the multipart
        # stream has content.
        from django.conf import settings
        from core.storage import connect_storage
        storage = connect_storage(settings)
        self.rows, self.cols = 8, 8  # small native frame: 8*8*2 bytes
        for n, inst in enumerate(self.instances, start=1):
            ds = make_dataset(study_uid=self.study_uid,
                              series_uid=self.series_uid,
                              sop_uid=inst.SOPInstanceUID, instance_number=n,
                              with_pixels=True, rows=self.rows, columns=self.cols)
            storage.upload_obj(inst.pacs_file.fname.name,
                               dataset_to_bytes(ds),
                               content_type='application/dicom')

    def test_frames_native_octet_stream(self):
        sop = self.instances[0].SOPInstanceUID
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/instances/{sop}/frames/1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('multipart/related', resp['Content-Type'])
        self.assertIn('application/octet-stream', resp['Content-Type'])
        body = resp.content
        # one native frame = Rows*Cols*1*2 bytes (16-bit, 1 sample) must be present
        self.assertIn(b'Content-Type: application/octet-stream', body)
        self.assertIn(str(self.rows * self.cols * 2).encode(), body)

    def test_frames_out_of_range_404(self):
        sop = self.instances[0].SOPInstanceUID
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/instances/{sop}/frames/9')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_bulkdata_native_octet_stream(self):
        sop = self.instances[0].SOPInstanceUID
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/instances/{sop}/bulkdata')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('application/octet-stream', resp['Content-Type'])

    def test_retrieve_instance_multipart(self):
        sop = self.instances[0].SOPInstanceUID
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/instances/{sop}',
            HTTP_ACCEPT='multipart/related; type="application/dicom"')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('multipart/related', resp['Content-Type'])
        body = b''.join(resp.streaming_content)
        self.assertIn(b'Content-Type: application/dicom', body)
        self.assertIn(b'DICM', body)  # PS3.10 magic in the part payload

    def test_retrieve_unknown_instance_404(self):
        resp = self.client.get(
            f'/dicom-web/pacs/BCH/studies/{self.study_uid}/series/'
            f'{self.series_uid}/instances/9.9.9',
            HTTP_ACCEPT='multipart/related; type="application/dicom"')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


@tag('stow', 'integration')
class StowTests(DicomwebTestBase):
    """
    STOW round-trips through pydicom parse + storage write + DB create.
    Tagged ``integration`` for the storage write.
    """
    def setUp(self):
        self.auth()

    def test_store_new_study(self):
        ds = make_dataset(modality='MR')
        body, ct = build_multipart_related([dataset_to_bytes(ds)])
        resp = self.client.post('/dicom-web/pacs/BCH/studies', data=body,
                                content_type=ct,
                                HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        out = resp.json()
        self.assertIn('00081199', out)  # ReferencedSOPSequence
        self.assertNotIn('00081198', out)  # no failures
        ref = out['00081199']['Value'][0]
        self.assertEqual(ref['00081155']['Value'], [ds.SOPInstanceUID])
        # Rows were created.
        self.assertTrue(PACSInstance.objects.filter(
            SOPInstanceUID=ds.SOPInstanceUID).exists())
        self.assertTrue(PACSStudy.objects.filter(
            StudyInstanceUID=ds.StudyInstanceUID).exists())

    def test_store_to_study_mismatch_409(self):
        # POST to /studies/{study} with a part from a different study -> all
        # parts fail -> 409 with FailedSOPSequence + FailureReason 43265.
        ds = make_dataset()
        body, ct = build_multipart_related([dataset_to_bytes(ds)])
        resp = self.client.post(
            '/dicom-web/pacs/BCH/studies/9.9.9.9', data=body,
            content_type=ct, HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        out = resp.json()
        self.assertIn('00081198', out)
        self.assertEqual(out['00081198']['Value'][0]['00081197']['Value'],
                         [0xA901])

    def test_partial_store_202(self):
        good = make_dataset(study_uid='5.5.5')
        bad = make_dataset(study_uid='6.6.6')
        body, ct = build_multipart_related(
            [dataset_to_bytes(good), dataset_to_bytes(bad)])
        # POST to /studies/5.5.5 -> good stored, bad mismatched -> 202.
        resp = self.client.post(
            '/dicom-web/pacs/BCH/studies/5.5.5', data=body, content_type=ct,
            HTTP_ACCEPT='application/dicom+json')
        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        out = resp.json()
        self.assertIn('00081199', out)
        self.assertIn('00081198', out)

    def test_wrong_content_type_415(self):
        resp = self.client.post('/dicom-web/pacs/BCH/studies', data=b'x',
                                content_type='application/json')
        self.assertEqual(resp.status_code,
                         status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)
