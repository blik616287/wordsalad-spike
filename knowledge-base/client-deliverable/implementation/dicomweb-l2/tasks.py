import io
import logging
from datetime import datetime

import pydicom
from celery import shared_task
from django.conf import settings
from django.db import transaction

from core.storage import connect_storage
from pacsfiles.models import PACS, PACSFile, PACSSeries

from .models import PACSInstance, PACSStudy

logger = logging.getLogger(__name__)


def _find_series_for_file(pacs_file: PACSFile):
    """
    Walk parent folders until we reach the one that owns a ``PACSSeries``.

    ``oxidicom`` and the existing PACS ingest path nest files under the series
    folder (sometimes through one or more intermediate sub-folders), so the
    immediate ``parent_folder`` of a ``PACSFile`` is not always the series
    folder. Walk up the chain until we hit a folder whose reverse
    ``pacs_series`` accessor resolves.
    """
    folder = pacs_file.parent_folder
    for _ in range(16):  # bound the walk in case of cycles / bad state
        if folder is None:
            return None
        try:
            return folder.pacs_series
        except PACSSeries.DoesNotExist:
            folder = folder.parent
    return None


def _parse_dicom_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y%m%d').date()
    except (TypeError, ValueError):
        return None


def _parse_dicom_time(value):
    if not value:
        return None
    raw = str(value).split('.', 1)[0]  # strip fractional seconds
    # DICOM TM VR is HHMMSS.FFFFFF, but allows truncation at HH, HHMM, HHMMSS.
    # We can't trust strptime's greedy matching on bare digit specs, so
    # dispatch on length explicitly.
    fmt_for_len = {6: '%H%M%S', 4: '%H%M', 2: '%H'}
    fmt = fmt_for_len.get(len(raw))
    if fmt is None:
        return None
    try:
        return datetime.strptime(raw, fmt).time()
    except ValueError:
        return None


def _as_int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def study_defaults(ds):
    """Patient/Study-level PACSStudy field values from a DICOM dataset.

    Single source of truth for both ingest paths (the async indexer below and
    the STOW-RS handler, which imports this).
    """
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


def refresh_study_rollups(study):
    """Recompute a PACSStudy's series/instance counts + ModalitiesInStudy."""
    series_qs = PACSSeries.objects.filter(
        pacs=study.pacs, StudyInstanceUID=study.StudyInstanceUID)
    modalities = sorted({s.Modality for s in series_qs if s.Modality})
    study.ModalitiesInStudy = '\\'.join(modalities)
    study.NumberOfStudyRelatedSeries = series_qs.count()
    study.NumberOfStudyRelatedInstances = PACSInstance.objects.filter(
        series__in=series_qs).count()
    study.save(update_fields=['ModalitiesInStudy',
                              'NumberOfStudyRelatedSeries',
                              'NumberOfStudyRelatedInstances'])


def _study_defaults_from_meta(meta):
    """PACSStudy defaults from an oxidicom-parsed-tags message (dict), the
    message-driven analogue of study_defaults(ds)."""
    return dict(
        PatientID=str(meta.get('PatientID') or '')[:100],
        PatientName=str(meta.get('PatientName') or '')[:150],
        PatientBirthDate=_parse_dicom_date(meta.get('PatientBirthDate')),
        PatientSex=str(meta.get('PatientSex') or '')[:1],
        StudyDate=_parse_dicom_date(meta.get('StudyDate')),
        StudyTime=_parse_dicom_time(meta.get('StudyTime')),
        AccessionNumber=str(meta.get('AccessionNumber') or '')[:100],
        StudyDescription=str(meta.get('StudyDescription') or '')[:400],
        ReferringPhysicianName=str(meta.get('ReferringPhysicianName') or '')[:150],
    )


def index_from_metadata(meta):
    """VARIANT C (hybrid): index PACSInstance + PACSStudy from oxidicom-parsed
    DICOM tags delivered as a message (e.g. NATS), WITHOUT re-reading the .dcm.

    ``meta`` is the JSON payload an *extended* oxidicom would publish per parsed
    instance -- the same tags it already extracts in Rust during C-STORE. The
    only DB reads here are FK lookups (PACS/PACSSeries/PACSFile, created by the
    existing registration handshake); there is NO storage download and NO
    pydicom parse. Contrast index_pacs_instance(), which re-reads each file.

    Returns True if it indexed, False if the series/file isn't registered yet
    (a production consumer would NAK/retry; oxidicom registers before/with the
    metadata event so this is rare).
    """
    pacs = PACS.objects.filter(identifier=meta.get('pacs_name')).first()
    series_uid = meta.get('SeriesInstanceUID')
    sop_uid = meta.get('SOPInstanceUID')
    if not (pacs and series_uid and sop_uid):
        logger.warning('index_from_metadata: missing pacs/series/sop: %r', meta)
        return False
    series = PACSSeries.objects.filter(pacs=pacs,
                                       SeriesInstanceUID=series_uid).first()
    pacs_file = PACSFile.objects.filter(fname=meta.get('fname')).first()
    if series is None or pacs_file is None:
        logger.info('index_from_metadata: series/file not registered yet (%s)',
                    sop_uid)
        return False

    with transaction.atomic():
        PACSInstance.objects.update_or_create(
            series=series, SOPInstanceUID=str(sop_uid),
            defaults=dict(
                pacs_file=pacs_file,
                SOPClassUID=str(meta.get('SOPClassUID') or ''),
                InstanceNumber=_as_int(meta.get('InstanceNumber')),
                Rows=_as_int(meta.get('Rows')),
                Columns=_as_int(meta.get('Columns')),
                BitsAllocated=_as_int(meta.get('BitsAllocated')),
                NumberOfFrames=_as_int(meta.get('NumberOfFrames')) or 1,
                TransferSyntaxUID=str(meta.get('TransferSyntaxUID') or ''),
            ),
        )
        study, _ = PACSStudy.objects.get_or_create(
            pacs=pacs, StudyInstanceUID=str(meta.get('StudyInstanceUID') or ''),
            defaults=_study_defaults_from_meta(meta))
        if hasattr(series, 'study_id') and not series.study_id:
            series.study = study
            series.save(update_fields=['study'])
        refresh_study_rollups(study)
    return True


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def index_pacs_instance(self, pacs_file_id):
    """
    Read the DICOM header for ``pacs_file_id``, upsert the matching
    ``PACSInstance``, and backfill any QIDO-relevant tags on the parent
    ``PACSSeries`` that aren't already populated.

    Idempotent: safe to retry, safe to run via the
    ``reindex_pacs_instances`` management command (Phase D) over the
    existing PACS file tree.
    """
    try:
        pacs_file = (PACSFile.objects
                     .select_related('parent_folder')
                     .get(pk=pacs_file_id))
    except PACSFile.DoesNotExist:
        logger.warning('index_pacs_instance: PACSFile id=%s not found', pacs_file_id)
        return

    fname = pacs_file.fname.name
    if not fname.endswith('.dcm'):
        # Non-DICOM sidecar in the PACS tree (e.g. JSON manifest). Skip.
        return

    series = _find_series_for_file(pacs_file)
    if series is None:
        logger.warning('index_pacs_instance: no parent PACSSeries for file=%s',
                       fname)
        return

    storage = connect_storage(settings)
    try:
        raw = storage.download_obj(fname)
    except Exception as exc:
        logger.error('index_pacs_instance: storage read failed for %s: %s',
                     fname, exc)
        raise self.retry(exc=exc)

    try:
        ds = pydicom.dcmread(io.BytesIO(raw), stop_before_pixels=True,
                             force=True)
    except Exception as exc:
        logger.error('index_pacs_instance: pydicom failed on %s: %s', fname, exc)
        return  # don't retry on parse failures — they won't get better

    sop_instance_uid = getattr(ds, 'SOPInstanceUID', None)
    if not sop_instance_uid:
        logger.warning('index_pacs_instance: missing SOPInstanceUID in %s', fname)
        return

    transfer_syntax = ''
    try:
        if ds.file_meta is not None:
            transfer_syntax = str(ds.file_meta.TransferSyntaxUID)
    except AttributeError:
        pass

    with transaction.atomic():
        PACSInstance.objects.update_or_create(
            series=series,
            SOPInstanceUID=str(sop_instance_uid),
            defaults=dict(
                pacs_file=pacs_file,
                SOPClassUID=str(getattr(ds, 'SOPClassUID', '') or ''),
                InstanceNumber=_as_int(getattr(ds, 'InstanceNumber', None)),
                Rows=_as_int(getattr(ds, 'Rows', None)),
                Columns=_as_int(getattr(ds, 'Columns', None)),
                BitsAllocated=_as_int(getattr(ds, 'BitsAllocated', None)),
                NumberOfFrames=_as_int(getattr(ds, 'NumberOfFrames', None)) or 1,
                TransferSyntaxUID=transfer_syntax,
            ),
        )

        _backfill_series_tags(series, ds)

        # Upsert the Study-level row so QIDO /studies surfaces oxidicom-ingested
        # data. The STOW-RS path creates PACSStudy inline; the async indexer (the
        # primary, oxidicom ingest path) must do the same or /studies stays empty.
        study, _ = PACSStudy.objects.get_or_create(
            pacs=series.pacs,
            StudyInstanceUID=series.StudyInstanceUID,
            defaults=study_defaults(ds),
        )
        # Link the series to its study when the PACSSeries.study FK exists.
        if hasattr(series, 'study_id') and not series.study_id:
            series.study = study
            series.save(update_fields=['study'])
        refresh_study_rollups(study)


def _backfill_series_tags(series: PACSSeries, ds) -> None:
    """
    Populate QIDO-RS tags on PACSSeries that the original ingest path didn't
    capture. Only writes empty/null columns; never overwrites existing values
    (the ingest path is authoritative for what it sets).
    """
    updates = {}

    if not series.StudyTime:
        st = _parse_dicom_time(getattr(ds, 'StudyTime', None))
        if st is not None:
            updates['StudyTime'] = st

    if series.SeriesNumber is None:
        sn = _as_int(getattr(ds, 'SeriesNumber', None))
        if sn is not None:
            updates['SeriesNumber'] = sn

    if not series.Manufacturer:
        m = getattr(ds, 'Manufacturer', None)
        if m:
            updates['Manufacturer'] = str(m)[:64]

    if not series.BodyPartExamined:
        bp = getattr(ds, 'BodyPartExamined', None)
        if bp:
            updates['BodyPartExamined'] = str(bp)[:16]

    if series.PerformedProcedureStepStartDate is None:
        d = _parse_dicom_date(getattr(ds, 'PerformedProcedureStepStartDate', None))
        if d is not None:
            updates['PerformedProcedureStepStartDate'] = d

    if series.PerformedProcedureStepStartTime is None:
        t = _parse_dicom_time(getattr(ds, 'PerformedProcedureStepStartTime', None))
        if t is not None:
            updates['PerformedProcedureStepStartTime'] = t

    if updates:
        PACSSeries.objects.filter(pk=series.pk).update(**updates)
