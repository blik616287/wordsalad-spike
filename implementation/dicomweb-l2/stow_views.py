"""
STOW-RS store endpoint (PS3.18 §10.5).

    POST  studies                      -> store instances (any StudyInstanceUID)
    POST  studies/{StudyInstanceUID}   -> store; instances whose StudyInstanceUID
                                          differs from {study} are rejected.

Request body: ``multipart/related; type="application/dicom"; boundary=...``,
each part a complete PS3.10 DICOM object (``Content-Type: application/dicom``).
We parse each part with pydicom, validate, write the bytes into the PACS storage
tree under ``SERVICES/PACS/<identifier>/...``, create/refresh the
PACSStudy/PACSSeries/PACSFile/PACSInstance rows (so the catalog is consistent
regardless of ingestion route -- STOW is a *non-oxidicom* ingestion path, which
is exactly why D1 Option C's fallback indexer matters), and return the Store
Instances Response (PS3.18 §10.5.3.2).

Response body (single DICOM JSON object, NOT an array):
    (0008,1190) RetrieveURL          -- study URL when >=1 instance stored
    (0008,1199) ReferencedSOPSequence -- successfully stored instances
    (0008,1198) FailedSOPSequence     -- failed instances (+FailureReason US)

Status (PS3.18 §10.5.3):
    200  all instances stored
    202  some stored, some failed/warned (inspect FailedSOPSequence)
    400  bad syntax; could not store any instance due to malformed request
    409  request well-formed but no instance stored (e.g. StudyInstanceUID
         mismatch for every part, or unsupported SOP class)
    415  Content-Type media type not supported

Failure reason codes (US, PS3.4 Annex C / PS3.18 Table 10.5.3-1 + Azure conventions):
    0xA700 (42752) processing failure (out of resources / generic)
    0xA900 (43264) dataset does not match SOP class    (we use 0xA700 generic)
    0xC000 (49152) cannot understand (unparseable / bad media type)
    0xA901 (43265) StudyInstanceUID mismatch with the {study} in the URL
  (values are emitted as DECIMAL in the JSON body: 0xA700->42752, 0xC000->49152,
   0xA901->43265.)

Spec: https://dicom.nema.org/medical/dicom/current/output/chtml/part18/sect_10.5.html
Response shape cross-checked against the Azure DICOM conformance statement.
"""
import io
import logging
import os
import uuid

import pydicom
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.authentication import (TokenAuthentication,
                                            BasicAuthentication,
                                            SessionAuthentication)

from core.models import ChrisFolder
from core.storage import connect_storage
from pacsfiles.models import PACS, PACSSeries, PACSFile
from pacsfiles.permissions import IsChrisOrIsPACSUserReadOnly

from . import dicomjson, serializers as dcm_serializers
from .models import PACSStudy, PACSInstance
from .multipart import (parse_multipart_related, MultipartError,
                        RawPassthroughParser)
from .renderers import DicomJsonRenderer, DicomJsonAsJsonRenderer

logger = logging.getLogger(__name__)

# Failure reason codes (emitted as DECIMAL in the JSON body's FailureReason US).
FAILURE_PROCESSING = 0xA700          # 42752 generic processing/validation failure
FAILURE_CANNOT_UNDERSTAND = 0xC000   # 49152 cannot understand / bad media type
FAILURE_STUDY_MISMATCH = 0xA901      # 43265 StudyInstanceUID != {study} in URL


class StowView(generics.GenericAPIView):
    """
    STOW-RS store. Write requires ``chris`` superuser membership the same way
    the existing PACS write paths do (``IsChrisOrIsPACSUserReadOnly`` makes
    everything but GET ``chris``-only); STOW is the one DICOMweb write surface,
    so it inherits exactly that policy. If grant policy later opens STOW to all
    ``pacs_users``, swap in ``IsChrisOrIsPACSUserOrReadOnly``.
    """
    http_method_names = ['post']
    authentication_classes = (TokenAuthentication, BasicAuthentication,
                              SessionAuthentication)
    permission_classes = (permissions.IsAuthenticated,
                          IsChrisOrIsPACSUserReadOnly)
    # We read the raw body ourselves (multipart/related, not form-data), so use
    # a passthrough parser to stop DRF's MultiPartParser from choking on it.
    parser_classes = (RawPassthroughParser,)
    renderer_classes = (DicomJsonRenderer, DicomJsonAsJsonRenderer)

    def get_pacs(self):
        return get_object_or_404(PACS,
                                 identifier=self.kwargs['pacs_identifier'])

    def post(self, request, *args, **kwargs):
        pacs = self.get_pacs()
        url_study_uid = self.kwargs.get('study_uid')  # None for POST /studies

        content_type = request.headers.get('Content-Type', '')
        if 'multipart/related' not in content_type:
            return Response(
                {'errorMessage': 'STOW-RS requires Content-Type '
                                 'multipart/related; type="application/dicom".'},
                status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        try:
            parts = parse_multipart_related(request.body, content_type)
        except MultipartError as exc:
            return Response({'errorMessage': f'Malformed multipart body: {exc}'},
                            status=status.HTTP_400_BAD_REQUEST)

        if not parts:
            return Response({'errorMessage': 'No DICOM parts in request body.'},
                            status=status.HTTP_400_BAD_REQUEST)

        urls = dcm_serializers.RetrieveURLBuilder(
            request, self.kwargs['pacs_identifier'])
        referenced = []   # success items (00081199)
        failed = []       # failure items (00081198)
        stored_study_uid = None

        storage = connect_storage(settings)

        for part in parts:
            if part.content_type and 'application/dicom' not in part.content_type:
                failed.append(self._fail_item(None, None, FAILURE_CANNOT_UNDERSTAND))
                continue
            try:
                ds = pydicom.dcmread(io.BytesIO(part.content), force=True)
            except Exception as exc:
                logger.warning('STOW-RS: pydicom parse failed: %s', exc)
                failed.append(self._fail_item(None, None,
                                              FAILURE_CANNOT_UNDERSTAND))
                continue

            sop_class = str(getattr(ds, 'SOPClassUID', '') or '')
            sop_inst = str(getattr(ds, 'SOPInstanceUID', '') or '')
            study_uid = str(getattr(ds, 'StudyInstanceUID', '') or '')
            series_uid = str(getattr(ds, 'SeriesInstanceUID', '') or '')

            if not (sop_inst and study_uid and series_uid):
                failed.append(self._fail_item(sop_class, sop_inst,
                                              FAILURE_PROCESSING))
                continue

            # POST /studies/{study}: reject parts from a different study.
            if url_study_uid and study_uid != url_study_uid:
                failed.append(self._fail_item(sop_class, sop_inst,
                                              FAILURE_STUDY_MISMATCH))
                continue

            try:
                with transaction.atomic():
                    inst = self._store_one(pacs, ds, part.content, storage,
                                           sop_class, sop_inst,
                                           study_uid, series_uid)
            except Exception as exc:
                logger.exception('STOW-RS: store failed for %s: %s',
                                 sop_inst, exc)
                failed.append(self._fail_item(sop_class, sop_inst,
                                              FAILURE_PROCESSING))
                continue

            stored_study_uid = study_uid
            referenced.append(self._ref_item(
                sop_class, sop_inst,
                urls.instance(study_uid, series_uid, sop_inst)))

        return self._build_response(urls, stored_study_uid,
                                    referenced, failed)

    # ------------------------------------------------------------------ #
    # Storage + indexing for one instance
    # ------------------------------------------------------------------ #
    def _store_one(self, pacs, ds, raw_bytes, storage,
                   sop_class, sop_inst, study_uid, series_uid):
        """
        Persist one DICOM object: find-or-create PACSStudy + PACSSeries +
        their ChrisFolders, write the .dcm into storage, create the PACSFile
        and PACSInstance rows, and refresh the study roll-up counters.

        Path layout mirrors the oxidicom convention:
          SERVICES/PACS/<identifier>/<StudyInstanceUID>/<SeriesInstanceUID>/<SOPInstanceUID>.dcm
        """
        # --- find-or-create the Study row (denormalized Patient+Study tags) --
        study, _ = PACSStudy.objects.get_or_create(
            pacs=pacs, StudyInstanceUID=study_uid,
            defaults=self._study_defaults(ds))

        # --- find-or-create the Series + its ChrisFolder --------------------
        series = PACSSeries.objects.filter(
            pacs=pacs, SeriesInstanceUID=series_uid).first()
        rel_dir = os.path.join('SERVICES', 'PACS', pacs.identifier,
                               study_uid, series_uid)
        if series is None:
            folder, _ = ChrisFolder.objects.get_or_create(
                path=rel_dir, defaults={'owner': self.request.user})
            series = PACSSeries.objects.create(
                pacs=pacs,
                folder=folder,
                SeriesInstanceUID=series_uid,
                StudyInstanceUID=study_uid,
                StudyDate=getattr(study, 'StudyDate', None),
                PatientID=study.PatientID,
                PatientName=study.PatientName,
                PatientBirthDate=study.PatientBirthDate,
                PatientSex=study.PatientSex,
                AccessionNumber=study.AccessionNumber,
                StudyDescription=study.StudyDescription,
                Modality=str(getattr(ds, 'Modality', '') or '')[:15],
                SeriesDescription=str(getattr(ds, 'SeriesDescription', '') or '')[:400],
                SeriesNumber=_as_int(getattr(ds, 'SeriesNumber', None)),
            )
            # NOTE: in a real CUBE checkout PACSSeries gains FK ``study``; set it:
            #   series.study = study; series.save(update_fields=['study'])
            # Guarded so this module also imports cleanly against Phase A schema.
            if hasattr(series, 'study_id'):
                series.study = study
                series.save(update_fields=['study'])
        else:
            folder = series.folder

        # --- write the .dcm bytes into storage ------------------------------
        fname = f'{rel_dir}/{sop_inst}.dcm'
        storage.upload_obj(fname, raw_bytes, content_type='application/dicom')

        # --- create the PACSFile (ChrisFile proxy) row ----------------------
        pacs_file = PACSFile(owner=self.request.user, parent_folder=folder)
        pacs_file.fname.name = fname
        pacs_file.fsize = len(raw_bytes)
        pacs_file.save()

        # --- create/refresh the Instance index row --------------------------
        transfer_syntax = ''
        try:
            if ds.file_meta is not None:
                transfer_syntax = str(ds.file_meta.TransferSyntaxUID)
        except AttributeError:
            pass

        inst, _ = PACSInstance.objects.update_or_create(
            series=series, SOPInstanceUID=sop_inst,
            defaults=dict(
                pacs_file=pacs_file,
                SOPClassUID=sop_class,
                InstanceNumber=_as_int(getattr(ds, 'InstanceNumber', None)),
                Rows=_as_int(getattr(ds, 'Rows', None)),
                Columns=_as_int(getattr(ds, 'Columns', None)),
                BitsAllocated=_as_int(getattr(ds, 'BitsAllocated', None)),
                NumberOfFrames=_as_int(getattr(ds, 'NumberOfFrames', None)) or 1,
                TransferSyntaxUID=transfer_syntax,
            ),
        )

        # --- refresh study roll-up counters + modality cache ----------------
        self._refresh_study_rollups(study)
        return inst

    def _study_defaults(self, ds):
        from .tasks import _parse_dicom_date, _parse_dicom_time  # reuse Phase A
        return dict(
            PatientID=str(getattr(ds, 'PatientID', '') or '')[:100],
            PatientName=str(getattr(ds, 'PatientName', '') or '')[:150],
            PatientBirthDate=_parse_dicom_date(getattr(ds, 'PatientBirthDate', None)),
            PatientSex=str(getattr(ds, 'PatientSex', '') or '')[:1],
            StudyDate=_parse_dicom_date(getattr(ds, 'StudyDate', None)),
            StudyTime=_parse_dicom_time(getattr(ds, 'StudyTime', None)),
            AccessionNumber=str(getattr(ds, 'AccessionNumber', '') or '')[:100],
            StudyDescription=str(getattr(ds, 'StudyDescription', '') or '')[:400],
            ReferringPhysicianName=str(getattr(ds, 'ReferringPhysicianName', '') or '')[:150],
        )

    def _refresh_study_rollups(self, study):
        series_qs = PACSSeries.objects.filter(
            pacs=study.pacs, StudyInstanceUID=study.StudyInstanceUID)
        modalities = sorted({s.Modality for s in series_qs if s.Modality})
        n_series = series_qs.count()
        n_inst = PACSInstance.objects.filter(
            series__in=series_qs).count()
        study.ModalitiesInStudy = '\\'.join(modalities)
        study.NumberOfStudyRelatedSeries = n_series
        study.NumberOfStudyRelatedInstances = n_inst
        study.save(update_fields=['ModalitiesInStudy',
                                  'NumberOfStudyRelatedSeries',
                                  'NumberOfStudyRelatedInstances'])

    # ------------------------------------------------------------------ #
    # DICOM JSON Model response builders
    # ------------------------------------------------------------------ #
    def _ref_item(self, sop_class, sop_inst, retrieve_url):
        return dicomjson.dataset([
            ('00081150', 'UI', sop_class),
            ('00081155', 'UI', sop_inst),
            ('00081190', 'UR', retrieve_url),
        ])

    def _fail_item(self, sop_class, sop_inst, reason):
        return dicomjson.dataset([
            ('00081150', 'UI', sop_class or ''),
            ('00081155', 'UI', sop_inst or ''),
            ('00081197', 'US', reason),
        ])

    def _build_response(self, urls, study_uid, referenced, failed):
        body = {}
        if study_uid and referenced:
            el = dicomjson.element('UR', urls.study(study_uid))
            if el is not None:
                body['00081190'] = el
        if failed:
            body['00081198'] = dicomjson.sequence(failed)
        if referenced:
            body['00081199'] = dicomjson.sequence(referenced)

        if referenced and not failed:
            code = status.HTTP_200_OK
        elif referenced and failed:
            code = status.HTTP_202_ACCEPTED
        else:
            # Nothing stored. 409 (request well-formed, conflict/unsupported)
            # is the spec's "could not store any" code; 400 is reserved for
            # bad syntax (handled earlier).
            code = status.HTTP_409_CONFLICT
        return Response(body, status=code)


def _as_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
