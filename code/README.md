# Phase A — DICOMweb schema + ingest foundation

This is the first phase of the `QIDO_PLAN.md` implementation. Lands a new `dicomweb` Django app with the `PACSInstance` model, six new `PACSSeries` fields for QIDO-RS, and a Celery task that indexes DICOM headers at ingest time.

The `PACSInstance` model is designed to support **all three** DICOMweb endpoints (QIDO-RS, WADO-RS, STOW-RS), not just QIDO. Subsequent phases (B = renderer + query parser, C = views, D = backfill + integration tests, E = polish) build on this foundation.

> **For an extensive walkthrough of the changes** — every changed file documented, design choices justified, edge cases noted, full validation log, recommended further validations — read **`../writeups/PHASE_A_IMPLEMENTATION.md`**. This README is the operational summary; that document is the engineering review companion.

## What's in this directory

| Path | What |
|---|---|
| `phase-a.patch` | Single `git apply`-able patch with everything below in one go (13 files: 5 modified + 8 new). |
| `source/chris_backend/dicomweb/` | The new Django app, human-readable copies — same files as bundled in the patch. |
| `source/chris_backend/pacsfiles/migrations/0009_*.py` | The new migration adding the QIDO-relevant fields to `PACSSeries`. |

## Apply to a clean checkout

```sh
cd ChRIS_ultron_backEnd
git checkout -b dicomweb-phase-a
git apply --index /path/to/deliverables/code/phase-a.patch
git commit -m "Phase A: DICOMweb schema + ingest foundation"
```

Then bring up the dev stack and run migrations + tests:

```sh
just build           # rebuilds the cube:dev image (pulls pydicom>=3.0,<4.0)
just migrate         # applies pacsfiles.0009 + dicomweb.0001
just test pacsfiles dicomweb --exclude-tag integration
```

Expected: 103 pacsfiles tests pass (no regressions), 9 dicomweb tests pass.

## Files changed

### New (8)

- `chris_backend/dicomweb/__init__.py`
- `chris_backend/dicomweb/apps.py` — `DicomwebConfig`.
- `chris_backend/dicomweb/models.py` — `PACSInstance` model. FKs to `pacsfiles.PACSSeries` and `pacsfiles.PACSFile`; stores `SOPClassUID`, `SOPInstanceUID`, `InstanceNumber`, `Rows`, `Columns`, `BitsAllocated`, `NumberOfFrames`, `TransferSyntaxUID`. `unique_together=('series','SOPInstanceUID')`.
- `chris_backend/dicomweb/tasks.py` — `index_pacs_instance` Celery task. Reads the .dcm header via `pydicom.dcmread(..., stop_before_pixels=True)`, upserts the `PACSInstance` row, and backfills QIDO-relevant tags on the parent `PACSSeries` only when those columns are empty. Helpers for DICOM DA / TM parsing (handles 4-char truncated times correctly — `strptime '1430'` against `%H%M%S` is greedy in the wrong way, fixed with length-dispatched format selection).
- `chris_backend/dicomweb/migrations/__init__.py`
- `chris_backend/dicomweb/migrations/0001_initial.py` — creates `dicomweb_pacsinstance`.
- `chris_backend/dicomweb/tests/__init__.py`
- `chris_backend/dicomweb/tests/test_tasks.py` — 9 smoke tests: DICOM date/time/int parsing correctness, task importability (catches circular-import regressions), Celery queue routing.
- `chris_backend/pacsfiles/migrations/0009_pacsseries_bodypartexamined_pacsseries_manufacturer_and_more.py` — auto-generated migration adding `StudyTime`, `Manufacturer`, `BodyPartExamined`, `SeriesNumber`, `PerformedProcedureStepStartDate`, `PerformedProcedureStepStartTime`, indexes on `Modality` and `StudyInstanceUID`, and a composite `(pacs, StudyInstanceUID)` index.

### Modified (5)

- `chris_backend/pacsfiles/models.py` — adds the six new fields to `PACSSeries`, adds `db_index=True` on `Modality` and `StudyInstanceUID`, adds the composite index in `Meta.indexes`.
- `chris_backend/pacsfiles/serializers.py` — `PACSSeriesSerializer.create` now fans out one `index_pacs_instance.delay(pk)` per file after `bulk_create`, gated by `transaction.on_commit` so workers don't run before the row is visible.
- `chris_backend/config/settings/common.py` — `'dicomweb'` added to `INSTALLED_APPS`.
- `chris_backend/core/celery.py` — `dicomweb.tasks.index_pacs_instance` routed to the `main2` queue (per-file work shouldn't share `main1` with the latency-sensitive plugin-instance state machine).
- `requirements/base.txt` — `pydicom>=3.0,<4.0` added.

## Validation done before delivery

- `just makemigrations --dry-run` → `No changes detected` (zero drift).
- `python manage.py check` → `System check identified no issues (0 silenced)`.
- `just test pacsfiles --exclude-tag integration` → 103/103 pass.
- `just test dicomweb --exclude-tag integration` → 9/9 pass.
- Containers stopped (`just down`), volumes preserved.

## Demo gate (from `QIDO_PLAN.md` Phase A)

> Ingest a series; `select * from dicomweb_pacsinstance` shows rows.

The model exists, the migration is applied, the Celery task is wired and queue-routed. End-to-end demonstration with real DICOM data is Phase D integration-test territory; the foundation is ready for it.

## Open items to discuss with BCH before Phase B

These are flagged in `QIDO_PLAN.md` §14.1 — picked out here because Phase B's design depends on them:

1. **STOW-RS in scope?** The MVP proposal said no; the grant says yes. Phase A doesn't depend on the answer but Phase C view-layer wiring does.
2. **Patient tags inconsistent across Series within a Study?** Affects the GROUP BY semantics in the `/studies` view. A quick query against an existing BCH-imported dataset would resolve this.
3. **Fuzzy matching (`fuzzymatching=true`)?** OHIF doesn't need it. GH's indexer might. Confirm before Phase B closes the query parser.
4. **drf-spectacular treatment of the new endpoints.** They emit `application/dicom+json`, not collection+json, so the existing schema-processing hooks don't apply cleanly. Decide whether to `@extend_schema(exclude=True)` per view or add an exclusion in `SPECTACULAR_SETTINGS['PREPROCESSING_HOOKS']`.
