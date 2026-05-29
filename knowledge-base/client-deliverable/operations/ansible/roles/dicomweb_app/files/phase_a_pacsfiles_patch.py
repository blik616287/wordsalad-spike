#!/usr/bin/env python3
"""Idempotently apply the Phase A field additions to CUBE's pacsfiles model.

Run INSIDE a running CUBE container (after the Phase A `0009` migration has been
copied into pacsfiles/migrations/):

    python phase_a_pacsfiles_patch.py [APP_DIR]

The L2 `dicomweb` app's migrations depend on the Phase A `pacsfiles` migration
`0009_pacsseries_bodypartexamined_pacsseries_manufacturer_and_more`, which adds
six PACSSeries tag columns + two db_index alterations + a composite index. The
prebuilt cube:6.11.x image predates Phase A, so the running container's
pacsfiles/models.py lacks those fields. Without them:
  - `manage.py migrate` would apply 0009 to the DB, but the ORM model would not
    expose the new fields, so the dicomweb indexer/serializers raise at runtime.

This patcher edits pacsfiles/models.py to match Phase A exactly. It is idempotent
(detects the marker field `BodyPartExamined` and no-ops) and verifies each
replacement actually applied (the cube:6.11.0 PACSSeries body matches the Phase A
base verbatim; if a future image diverges, it fails loudly rather than silently
half-patching).
"""
import os
import sys

APP_DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("APP_DIR", "/opt/app-root/src")
MODELS = os.path.join(APP_DIR, "pacsfiles", "models.py")

# (old, new) exact replacements reproducing the Phase A pacsfiles/models.py diff.
REPLACEMENTS = [
    # StudyTime, after StudyDate
    (
        "    StudyDate = models.DateField(db_index=True)\n    AccessionNumber",
        "    StudyDate = models.DateField(db_index=True)\n"
        "    StudyTime = models.TimeField(blank=True, null=True)\n    AccessionNumber",
    ),
    # Modality gains db_index; add Manufacturer + BodyPartExamined
    (
        "    Modality = models.CharField(max_length=15, blank=True)\n    ProtocolName",
        "    Modality = models.CharField(max_length=15, blank=True, db_index=True)\n"
        "    Manufacturer = models.CharField(max_length=64, blank=True)\n"
        "    BodyPartExamined = models.CharField(max_length=16, blank=True)\n    ProtocolName",
    ),
    # StudyInstanceUID gains db_index
    (
        "    StudyInstanceUID = models.CharField(max_length=100)\n    StudyDescription",
        "    StudyInstanceUID = models.CharField(max_length=100, db_index=True)\n    StudyDescription",
    ),
    # SeriesNumber after SeriesInstanceUID; PPSStart Date/Time after SeriesDescription
    (
        "    SeriesInstanceUID = models.CharField(max_length=100, db_index=True)\n"
        "    SeriesDescription = models.CharField(max_length=400, blank=True)\n    folder",
        "    SeriesInstanceUID = models.CharField(max_length=100, db_index=True)\n"
        "    SeriesNumber = models.IntegerField(blank=True, null=True)\n"
        "    SeriesDescription = models.CharField(max_length=400, blank=True)\n"
        "    PerformedProcedureStepStartDate = models.DateField(blank=True, null=True)\n"
        "    PerformedProcedureStepStartTime = models.TimeField(blank=True, null=True)\n    folder",
    ),
    # Composite index in Meta
    (
        "        unique_together = ('pacs', 'SeriesInstanceUID',)\n",
        "        unique_together = ('pacs', 'SeriesInstanceUID',)\n"
        "        indexes = [\n"
        "            models.Index(fields=['pacs', 'StudyInstanceUID'],\n"
        "                         name='pacsseries_pacs_study_idx'),\n"
        "        ]\n",
    ),
]


def main():
    if not os.path.exists(MODELS):
        print(f"  pacsfiles/models.py NOT FOUND at {MODELS} -- is APP_DIR correct?", file=sys.stderr)
        sys.exit(2)
    text = open(MODELS).read()
    if "BodyPartExamined" in text:
        print("  pacsfiles model: Phase A fields already present -> no change")
        print("UNCHANGED (idempotent)")
        return
    for old, new in REPLACEMENTS:
        if old not in text:
            print(f"  FATAL: anchor not found, refusing to half-patch:\n    {old.splitlines()[0]!r}",
                  file=sys.stderr)
            sys.exit(3)
        text = text.replace(old, new, 1)
    open(MODELS, "w").write(text)
    print("  pacsfiles model: added Phase A fields (StudyTime, Manufacturer, "
          "BodyPartExamined, SeriesNumber, PerformedProcedureStepStartDate/Time) "
          "+ db_index + composite index")
    print("CHANGED")


if __name__ == "__main__":
    main()
