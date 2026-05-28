"""
Row -> DICOM JSON Model serialization for the QIDO/WADO surface.

These are *not* DRF ``ModelSerializer``s: the DICOM JSON Model is too far from a
flat dict for the serializer machinery to be load-bearing, and the existing
``pacsfiles`` serializers are collection+json-shaped. Instead each function
takes a model row (``PACSStudy`` / ``PACSSeries`` / ``PACSInstance``) and a
``RetrieveURLBuilder`` and returns a DICOM JSON dataset dict via
``dicomjson.dataset``.

Each level emits the *full set of indexed attributes* for that level -- which is
a superset of the QIDO required/default set (PS3.18 Tables 10.6.3-3 Study /
10.6.3-4 Series / 10.6.3-5 Instance, cross-checked against the Azure DICOM
conformance statement defaults). PS3.18 §10.6.3 permits a server to return MORE
attributes than the client requested, so returning every indexed column is
conformant and simpler than per-request projection. ``RetrieveURL (0008,1190)``
is synthesized as the WADO-RS URL of the object -- the QIDO->WADO contractual
glue.

The ``include_all`` / ``includefields`` parameters are accepted for forward
compatibility and parsed by ``query_parser`` (so clients see no 400 for them),
but because every indexed attribute is already emitted, they are currently a
no-op at this layer. The only attributes a client could ``includefield`` that we
would NOT return are tags we do not index at all (e.g. ``InstitutionName``,
``StudyID``); those would require schema additions. See README "Known
limitations". ``RetrieveURL`` is always included.

Empty attributes are omitted by ``dicomjson.element`` (returns None) -- PS3.18
F.2.5 permits omission of zero-length attributes.
"""
from . import dicomjson


class RetrieveURLBuilder:
    """
    Build absolute WADO-RS ``RetrieveURL`` values for a request + PACS.

    Centralized so the QIDO surface and the STOW response use identical URLs.
    Uses ``request.build_absolute_uri`` so scheme/host/port follow the request
    (works behind the miniChRIS reverse proxy).
    """
    def __init__(self, request, pacs_identifier):
        self.request = request
        self.base = f'/dicom-web/pacs/{pacs_identifier}'

    def study(self, study_uid):
        return self.request.build_absolute_uri(f'{self.base}/studies/{study_uid}')

    def series(self, study_uid, series_uid):
        return self.request.build_absolute_uri(
            f'{self.base}/studies/{study_uid}/series/{series_uid}')

    def instance(self, study_uid, series_uid, sop_uid):
        return self.request.build_absolute_uri(
            f'{self.base}/studies/{study_uid}/series/{series_uid}'
            f'/instances/{sop_uid}')


def serialize_study(study, urls, include_all=False, includefields=None):
    """Serialize a ``PACSStudy`` row to a Study-level DICOM JSON dataset."""
    pairs = [
        ('00080020', 'DA', study.StudyDate),
        ('00080030', 'TM', study.StudyTime),
        ('00080050', 'SH', study.AccessionNumber),
        ('00080061', 'CS', study.modalities_list()),
        ('00080090', 'PN', study.ReferringPhysicianName),
        ('00081030', 'LO', study.StudyDescription),
        ('00100010', 'PN', study.PatientName),
        ('00100020', 'LO', study.PatientID),
        ('00100030', 'DA', study.PatientBirthDate),
        ('00100040', 'CS', study.PatientSex),
        ('0020000D', 'UI', study.StudyInstanceUID),
        ('00201206', 'IS', study.NumberOfStudyRelatedSeries),
        ('00201208', 'IS', study.NumberOfStudyRelatedInstances),
        ('00081190', 'UR', urls.study(study.StudyInstanceUID)),
    ]
    return dicomjson.dataset(pairs)


def serialize_series(series, urls, study_uid=None,
                     num_instances=None, include_all=False, includefields=None):
    """
    Serialize a ``PACSSeries`` row to a Series-level DICOM JSON dataset.

    ``study_uid`` defaults to the series' own ``StudyInstanceUID`` column.
    ``num_instances`` is ``NumberOfSeriesRelatedInstances`` -- pass the
    annotated count from the queryset (Count('instances')) to avoid an N+1.
    """
    study_uid = study_uid or series.StudyInstanceUID
    if num_instances is None:
        num_instances = getattr(series, 'num_instances', None)
        if num_instances is None:
            num_instances = series.instances.count()
    pairs = [
        ('00080060', 'CS', series.Modality),
        ('00080070', 'LO', series.Manufacturer),
        ('0008103E', 'LO', series.SeriesDescription),
        ('00180015', 'CS', series.BodyPartExamined),
        ('00181030', 'LO', series.ProtocolName),
        ('0020000D', 'UI', study_uid),
        ('0020000E', 'UI', series.SeriesInstanceUID),
        ('00200011', 'IS', series.SeriesNumber),
        ('00201209', 'IS', num_instances),
        ('00400244', 'DA', series.PerformedProcedureStepStartDate),
        ('00400245', 'TM', series.PerformedProcedureStepStartTime),
        ('00081190', 'UR', urls.series(study_uid, series.SeriesInstanceUID)),
    ]
    return dicomjson.dataset(pairs)


def serialize_instance(instance, urls, study_uid=None, series_uid=None,
                       include_all=False, includefields=None):
    """Serialize a ``PACSInstance`` row to an Instance-level DICOM JSON dataset."""
    series = instance.series
    study_uid = study_uid or series.StudyInstanceUID
    series_uid = series_uid or series.SeriesInstanceUID
    pairs = [
        # Study/Series identifiers are part of the Instance result set (PS3.18
        # Table 10.6.3-5) and are what OHIF uses to construct the WADO path.
        ('0020000D', 'UI', study_uid),
        ('0020000E', 'UI', series_uid),
        ('00080016', 'UI', instance.SOPClassUID),
        ('00080018', 'UI', instance.SOPInstanceUID),
        ('00200013', 'IS', instance.InstanceNumber),
        ('00280008', 'IS', instance.NumberOfFrames),
        ('00280010', 'US', instance.Rows),
        ('00280011', 'US', instance.Columns),
        ('00280100', 'US', instance.BitsAllocated),
        ('00081190', 'UR', urls.instance(study_uid, series_uid,
                                         instance.SOPInstanceUID)),
    ]
    return dicomjson.dataset(pairs)
