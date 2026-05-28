"""
DICOMweb metadata index models.

This module owns the *explicit* DICOM hierarchy that QIDO-RS needs but stock
CUBE does not have. Stock CUBE collapses Patient + Study + Series tags onto a
single ``pacsfiles.PACSSeries`` row and stores nothing per-instance. The
DICOMweb work adds:

  - ``PACSStudy``    -- one row per Study within a PACS (Study + Patient tags,
                        denormalized roll-up counters). NEW in this spike (L2).
  - ``PACSInstance`` -- one row per ``.dcm`` (Instance / SOP / geometry tags).
                        Already shipped in Phase A; kept verbatim here.

Design (see ``proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`` "Indexing model" and
``knowledge-base/08-l2-architecture-decisions.md`` D2):

    PACS ──< PACSStudy ──< PACSSeries ──< PACSInstance ──1:1── PACSFile (storage)
             Study+Patient   Series tags   SOP/geometry/        the .dcm bytes
             tags + counts    (+FK→Study)   xfer-syntax

``PACSSeries`` itself lives in ``pacsfiles.models`` (it is the central existing
table); the FK ``PACSSeries.study -> PACSStudy`` is added there by an additive
migration (see ``MAPPING.md`` and the migration note at the bottom of this
file). Patient stays *implicit* -- its tags ride on ``PACSStudy``, matching how
QIDO Study Result Attributes (PS3.18 Table 10.6.3-3,
https://dicom.nema.org/medical/dicom/current/output/html/part18.html) return
Patient tags at the Study level. Promote to a ``PACSPatient`` model only if a
concrete query demands it.
"""
from django.db import models


class PACSStudy(models.Model):
    """
    DICOM Study-level metadata, one row per (PACS, StudyInstanceUID).

    Carries the Study + Patient attribute set that QIDO returns at the Study
    level, plus two *denormalized* roll-up counters
    (``NumberOfStudyRelatedSeries`` / ``...Instances``) and a small modality
    cache (``ModalitiesInStudy``, stored as a backslash-joined CS string the way
    DICOM multi-valued CS is encoded on the wire). The counters are maintained
    at ingest (find-or-create in ``PACSSeriesSerializer.create``) and on series
    delete; computing them per-request via ``GROUP BY`` was the MVP shortcut, but
    an explicit model is the recommended choice at grant scale -- see D2 in
    ``knowledge-base/08-l2-architecture-decisions.md``.

    Patient tags are duplicated here from ``PACSSeries`` deliberately: this is
    the single source of truth for Study-level reads, and find-or-create
    resolves cross-series patient-tag conflicts once at ingest rather than per
    query.
    """
    creation_date = models.DateTimeField(auto_now_add=True)

    # ---- Patient-level attributes (Patient stays implicit; tags live here) ---
    PatientID = models.CharField(max_length=100, db_index=True)
    PatientName = models.CharField(max_length=150, blank=True)
    PatientBirthDate = models.DateField(blank=True, null=True)
    PatientSex = models.CharField(
        max_length=1,
        choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')],
        blank=True,
    )

    # ---- Study-level attributes -------------------------------------------- #
    StudyInstanceUID = models.CharField(max_length=100, db_index=True)
    StudyDate = models.DateField(blank=True, null=True, db_index=True)
    StudyTime = models.TimeField(blank=True, null=True)
    AccessionNumber = models.CharField(max_length=100, blank=True, db_index=True)
    StudyDescription = models.CharField(max_length=400, blank=True)
    ReferringPhysicianName = models.CharField(max_length=150, blank=True)

    # ---- Denormalized roll-ups (maintained at ingest / delete) ------------- #
    # ModalitiesInStudy (0008,0061) is multi-valued CS. DICOM encodes multi-value
    # on the wire with a '\' separator; we store the same joined form and split
    # on render. e.g. "CT\\MR".
    ModalitiesInStudy = models.CharField(max_length=255, blank=True)
    NumberOfStudyRelatedSeries = models.IntegerField(default=0)
    NumberOfStudyRelatedInstances = models.IntegerField(default=0)

    pacs = models.ForeignKey(
        'pacsfiles.PACS', on_delete=models.CASCADE, related_name='studies',
    )

    class Meta:
        ordering = ('pacs', '-StudyDate', 'StudyInstanceUID')
        unique_together = ('pacs', 'StudyInstanceUID')
        indexes = [
            models.Index(fields=['pacs', 'StudyInstanceUID'],
                         name='pacsstudy_pacs_study_idx'),
            models.Index(fields=['pacs', 'PatientID'],
                         name='pacsstudy_pacs_patient_idx'),
        ]
        verbose_name_plural = 'PACS studies'

    def __str__(self):
        return self.StudyInstanceUID

    def modalities_list(self):
        """``ModalitiesInStudy`` as a Python list for DICOM-JSON rendering."""
        return [m for m in self.ModalitiesInStudy.split('\\') if m]


class PACSInstance(models.Model):
    """
    DICOM Instance-level metadata index for a single PACSFile.

    Created at ingest by ``dicomweb.tasks.index_pacs_instance`` (Phase A);
    consumed by the QIDO-RS read surface and resolved to storage bytes by
    WADO-RS via the ``pacs_file`` 1-to-1. Patient/Study tags live on
    ``PACSStudy`` and Series tags on ``PACSSeries`` (single sources of truth);
    only Instance-level tags are stored here.

    Kept verbatim from Phase A
    (``proposal-to-bch/code/source/chris_backend/dicomweb/models.py``) so the
    already-shipped migration ``dicomweb.0001_initial`` is unchanged.
    """
    series = models.ForeignKey(
        'pacsfiles.PACSSeries',
        on_delete=models.CASCADE,
        related_name='instances',
    )
    pacs_file = models.OneToOneField(
        'pacsfiles.PACSFile',
        on_delete=models.CASCADE,
        related_name='dicom_instance',
    )

    SOPClassUID = models.CharField(max_length=100, db_index=True)
    SOPInstanceUID = models.CharField(max_length=100, db_index=True)
    InstanceNumber = models.IntegerField(blank=True, null=True)
    Rows = models.IntegerField(blank=True, null=True)
    Columns = models.IntegerField(blank=True, null=True)
    BitsAllocated = models.IntegerField(blank=True, null=True)
    NumberOfFrames = models.IntegerField(blank=True, null=True)
    TransferSyntaxUID = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = ('series', 'SOPInstanceUID')
        ordering = ('series', 'InstanceNumber', 'SOPInstanceUID')

    def __str__(self):
        return self.SOPInstanceUID


# ---------------------------------------------------------------------------- #
# Migration note (what would follow this model addition):
#
#   1. dicomweb/migrations/0002_pacsstudy.py  -- CreateModel(PACSStudy) with the
#      two composite indexes above. Additive; no data migration required to
#      create the table.
#
#   2. pacsfiles/migrations/00XX_pacsseries_study_fk.py  -- adds
#         study = models.ForeignKey('dicomweb.PACSStudy', null=True, blank=True,
#                                    on_delete=models.SET_NULL,
#                                    related_name='series_list')
#      to pacsfiles.PACSSeries. NULLABLE so the migration is non-breaking on
#      existing rows; the find-or-create logic in PACSSeriesSerializer.create
#      and the reindex management command backfill it.
#
#   Both migrations are auto-generated via ``just makemigrations`` against a CUBE
#   checkout (Phase A validated this flow). The dependency edge is
#   dicomweb.0002 -> pacsfiles.00XX (the FK target must exist first).
#
#   Why SET_NULL and nullable: a PACSSeries can momentarily exist before its
#   parent PACSStudy is resolved (and deleting a study should not cascade-delete
#   its series rows out from under in-flight ingest). The reindex command
#   reconciles orphans.
# ---------------------------------------------------------------------------- #
