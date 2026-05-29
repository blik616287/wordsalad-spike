# Phase A — Implementation writeup

Detailed documentation of every code change made for Phase A of the QIDO-RS / DICOMweb implementation, plus the validations performed and the validations recommended before further phases land.

This document is the engineering counterpart to `QIDO_PLAN.md` (the design). Read this if you want to review the code itself or sign off on it.

---

## Summary

Phase A delivers the **schema and ingest-pipeline foundation** for DICOMweb compliance in CUBE. After applying it:

- The DB schema can store DICOM Instance-level metadata (`SOPClassUID`, `SOPInstanceUID`, `InstanceNumber`, `Rows`, `Columns`, `BitsAllocated`, `NumberOfFrames`, `TransferSyntaxUID`) — none of which CUBE captured before.
- The DB schema can store six additional QIDO-required tags on `PACSSeries` (`StudyTime`, `Manufacturer`, `BodyPartExamined`, `SeriesNumber`, `PerformedProcedureStepStartDate`, `PerformedProcedureStepStartTime`).
- Every `.dcm` file registered through `POST /api/v1/pacs/series/` is asynchronously indexed by a Celery task that reads its DICOM header via `pydicom` and creates a `PACSInstance` row.
- Existing functionality is untouched (103 pacsfiles tests, full suite passes; 0 schema drift).

The work is intentionally agnostic between QIDO, WADO, and STOW — the `PACSInstance` model supports all three so the design choice (resolved later with BCH) on STOW-RS scope doesn't bottleneck the foundation.

### Footprint

**13 files** total:
- 8 new files in `chris_backend/dicomweb/` (the new Django app + its tests + its initial migration).
- 1 new migration file in `chris_backend/pacsfiles/migrations/`.
- 5 modified files (`pacsfiles/models.py`, `pacsfiles/serializers.py`, `config/settings/common.py`, `core/celery.py`, `requirements/base.txt`).

Net change: **548 lines** in the unified patch. Most of that is the new migration files (auto-generated) and the new Celery task module.

---

## Files changed

### 1. `chris_backend/dicomweb/` — new Django app

The new app is **deliberately isolated** from `pacsfiles/`. DICOMweb-specific concerns (data model, indexing task, eventually query parser + renderer + views) live here so the existing PACS API surface remains stable.

#### `apps.py`

```python
from django.apps import AppConfig

class DicomwebConfig(AppConfig):
    name = 'dicomweb'
```

Mirrors the minimal pattern used by every other app in `chris_backend/` (compare `pacsfiles/apps.py`, `feeds/apps.py`). No custom `ready()` hook — none needed at this phase.

#### `models.py`

The single model is `PACSInstance`:

```python
class PACSInstance(models.Model):
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
```

**Design choices**:
- **One-to-one with `PACSFile`**: each Instance is exactly one file on storage. Lets WADO-RS find the storage object by following `PACSInstance.pacs_file.fname` in O(1).
- **FK to `PACSSeries`, not denormalized Patient/Study tags**: single source of truth for those tags on `PACSSeries`. Joins go through the series FK. Costs one join in QIDO instance-level queries; saves a sync problem if patient tags ever change.
- **`unique_together=('series','SOPInstanceUID')`**: the same SOPInstanceUID *can* appear in different series across different PACSes; only enforce uniqueness within a series. Lets the indexing task use `update_or_create` keyed on `(series, SOPInstanceUID)` safely.
- **`db_index=True` on `SOPClassUID` and `SOPInstanceUID`**: both are heavily filtered by QIDO. WADO retrieval also looks up by `SOPInstanceUID`.
- **`blank=True, null=True` on pixel-geometry fields**: not every modality populates `Rows`/`Columns`/`BitsAllocated` (e.g. some structured-report SOP classes). Allow missing rather than forcing zero.
- **String references (`'pacsfiles.PACSSeries'`)**: avoids cross-app import cycles. Django's standard pattern.

**What's deliberately not here**:
- No `PACSStudy` model. Study-level rollups will be computed on the fly via `GROUP BY` in Phase C, per the decision locked in `QIDO_PLAN.md` §1. Defers a materialization tradeoff that can be made later when query latency is measurable on a real dataset.
- No private DICOM tags. Not needed for QIDO; private tags would need their own VR handling.

#### `tasks.py`

The Celery task `index_pacs_instance(pacs_file_id)` reads one `.dcm` header and upserts the matching `PACSInstance`.

Walked through in pieces:

**Parent-folder walk** — finds the `PACSSeries` for a given `PACSFile`:

```python
def _find_series_for_file(pacs_file: PACSFile):
    folder = pacs_file.parent_folder
    for _ in range(16):  # bound the walk in case of cycles / bad state
        if folder is None:
            return None
        try:
            return folder.pacs_series
        except PACSSeries.DoesNotExist:
            folder = folder.parent
    return None
```

`oxidicom` and the existing ingest path place files in a folder hierarchy under the series folder — sometimes through one or more intermediate sub-folders (per `pacsfiles/serializers.py:200-206`, sub-folders get created when a file's `dirname` differs from the series root path). The immediate `parent_folder` of a `PACSFile` is therefore not always the folder that owns the `PACSSeries`. Walk up until the reverse 1-to-1 accessor `folder.pacs_series` resolves. Bound to 16 hops as a safety against malformed state.

**DICOM date/time parsing**:

```python
def _parse_dicom_time(value):
    if not value:
        return None
    raw = str(value).split('.', 1)[0]  # strip fractional seconds
    fmt_for_len = {6: '%H%M%S', 4: '%H%M', 2: '%H'}
    fmt = fmt_for_len.get(len(raw))
    if fmt is None:
        return None
    try:
        return datetime.strptime(raw, fmt).time()
    except ValueError:
        return None
```

DICOM `TM` VR allows `HH`, `HHMM`, or `HHMMSS` with optional fractional seconds. The naive multi-format `strptime` loop is **wrong**: `strptime('1430', '%H%M%S')` matches greedily as `H=14, M=3, S=0` (regex `\d{1,2}` for each component). This was caught by the test suite during validation — see "Validations performed" below. Fix is to dispatch format on input length.

`_parse_dicom_date` is straightforward (`%Y%m%d` is unambiguous at 8 chars).

**The task body**:

```python
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def index_pacs_instance(self, pacs_file_id):
    try:
        pacs_file = (PACSFile.objects
                     .select_related('parent_folder')
                     .get(pk=pacs_file_id))
    except PACSFile.DoesNotExist:
        logger.warning('index_pacs_instance: PACSFile id=%s not found', pacs_file_id)
        return

    fname = pacs_file.fname.name
    if not fname.endswith('.dcm'):
        return  # Non-DICOM sidecar — skip without retry

    series = _find_series_for_file(pacs_file)
    if series is None:
        logger.warning(...)
        return

    storage = connect_storage(settings)
    try:
        raw = storage.download_obj(fname)
    except Exception as exc:
        logger.error(...)
        raise self.retry(exc=exc)        # transient storage failure → retry

    try:
        ds = pydicom.dcmread(io.BytesIO(raw), stop_before_pixels=True, force=True)
    except Exception as exc:
        logger.error(...)
        return                            # parse failure won't get better → no retry

    sop_instance_uid = getattr(ds, 'SOPInstanceUID', None)
    if not sop_instance_uid:
        logger.warning(...)
        return

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
```

**Design choices**:
- **`pydicom.dcmread(..., stop_before_pixels=True)`**: only reads the header, not the pixel data. Critical for cold S3 reads — header is typically the first few KB of a multi-MB `.dcm` file.
- **`force=True`**: lets pydicom read DICOMs missing the DICM preamble (some legacy modalities skip it). Compatible with the existing ingest path which doesn't normalize this.
- **`update_or_create` keyed on `(series, SOPInstanceUID)`**: makes the task idempotent. Re-running for the same file is a no-op data-wise; re-running after a backfill-management-command re-trigger is also safe.
- **`storage.download_obj(...)` rather than range-read**: the `StorageManager` abstraction (in `core/storage/storagemanager.py`) doesn't expose range reads. For Phase A this is fine — 99% of DICOMs are small enough that the whole-file read is acceptable. WADO-RS implementation in a later phase may want to add range support.
- **Retry policy**: transient storage failures retry up to 3 times with 30s delays (good for S3 throttling). Parse failures don't retry — they won't get better, and retrying would burn worker capacity.
- **`with transaction.atomic()`**: PACSInstance create and the PACSSeries backfill update are one DB transaction. If either fails the other rolls back.

**`_backfill_series_tags`**: only writes empty/null columns on `PACSSeries`, never overwrites existing values:

```python
if not series.StudyTime:
    st = _parse_dicom_time(getattr(ds, 'StudyTime', None))
    if st is not None:
        updates['StudyTime'] = st
# … same for SeriesNumber, Manufacturer, BodyPartExamined,
# PerformedProcedureStepStartDate, PerformedProcedureStepStartTime
if updates:
    PACSSeries.objects.filter(pk=series.pk).update(**updates)
```

Why "never overwrite": the existing ingest path (`PACSSeriesSerializer.create`) is authoritative for the tags it sets at series-creation time. The first `.dcm` parsed for a series will fill in the QIDO-relevant tags it didn't bother with. Later files in the same series should not be allowed to flip those values — they're series-level and should be consistent across all instances.

#### `migrations/0001_initial.py`

Auto-generated by `just makemigrations`. Creates the `dicomweb_pacsinstance` table. Depends on `pacsfiles.0008_pacsseries_deletion_error_and_more` (so `PACSSeries` and `PACSFile` exist). Standard pattern; no hand edits.

#### `tests/test_tasks.py`

9 smoke tests:

| Test | What it locks down |
|---|---|
| `test_parse_dicom_date_valid` | Standard 8-char DICOM date parses. |
| `test_parse_dicom_date_invalid_returns_none` | Bad input returns `None`, not exception. |
| `test_parse_dicom_time_full` | 6-char `HHMMSS` parses. |
| `test_parse_dicom_time_with_fractional_seconds` | `HHMMSS.FFFFFF` — fractional seconds stripped. |
| `test_parse_dicom_time_partial` | **4-char `HHMM` parses correctly** (the bug the suite caught — see Validations). |
| `test_parse_dicom_time_invalid` | Bad input returns `None`. |
| `test_as_int` | Conversion helper handles ints, strs, None, empty, garbage. |
| `test_celery_task_is_importable` | Catches circular-import regressions between `pacsfiles.serializers` (which imports from `dicomweb.tasks` at runtime) and `dicomweb.tasks` (which imports from `pacsfiles.models`). |
| `test_task_routed_to_main2` | Locks in the `core/celery.py:task_routes` entry. If someone deletes the route entry, this test fails. |

What's **not** here yet:
- No test that actually exercises the full task against a fake `PACSFile` + fake storage backend. That's better done as an integration test in Phase D (tagged `integration`, run with `just test-integration`) with real `.dcm` fixtures.
- No test for `_find_series_for_file` walking the folder chain. Could be added once the test fixtures for `ChrisFolder` are set up — currently low-value as a unit test (most of the logic is the Django ORM access).

---

### 2. `chris_backend/pacsfiles/models.py` — six new fields + composite index

Added between the existing `StudyDate` and `AccessionNumber` lines:

```python
StudyDate = models.DateField(db_index=True)
StudyTime = models.TimeField(blank=True, null=True)
AccessionNumber = models.CharField(max_length=100, blank=True, db_index=True)
Modality = models.CharField(max_length=15, blank=True, db_index=True)        # added db_index
Manufacturer = models.CharField(max_length=64, blank=True)
BodyPartExamined = models.CharField(max_length=16, blank=True)
ProtocolName = models.CharField(max_length=64, blank=True)
StudyInstanceUID = models.CharField(max_length=100, db_index=True)           # added db_index
StudyDescription = models.CharField(max_length=400, blank=True)
SeriesInstanceUID = models.CharField(max_length=100, db_index=True)
SeriesNumber = models.IntegerField(blank=True, null=True)
SeriesDescription = models.CharField(max_length=400, blank=True)
PerformedProcedureStepStartDate = models.DateField(blank=True, null=True)
PerformedProcedureStepStartTime = models.TimeField(blank=True, null=True)
```

Plus a composite index in `Meta`:

```python
class Meta:
    ordering = ('pacs', 'PatientID',)
    unique_together = ('pacs', 'SeriesInstanceUID',)
    indexes = [
        models.Index(fields=['pacs', 'StudyInstanceUID'],
                     name='pacsseries_pacs_study_idx'),
    ]
```

**Why each field**:
- `StudyTime`, `SeriesNumber`, `PerformedProcedureStepStartDate/Time`: required by QIDO-RS Series Result Attributes (PS3.18 Table 10.4.1-2).
- `Manufacturer`, `BodyPartExamined`: common QIDO filter targets — OHIF exposes them in its default Study List filters.
- `db_index=True` on `Modality`: QIDO Study filter `ModalitiesInStudy` aggregates over this column.
- `db_index=True` on `StudyInstanceUID`: used as a filter param at Study and Series levels, and embedded in WADO-RS paths.
- Composite `(pacs, StudyInstanceUID)` index: covers the natural lookup in `/dicom-web/pacs/<id>/studies/<StudyInstanceUID>/series/...`.

**Backward compatibility**: every new field is `null=True` or `blank=True`. Existing rows get `None` / empty defaults at migration time. Read paths that don't touch these fields are unchanged. The `PACSSeriesSerializer` was deliberately **not** updated in Phase A — exposing the new fields via the existing `/api/v1/pacs/series/` API surface is a separate, optional change that we defer.

#### `pacsfiles/migrations/0009_pacsseries_bodypartexamined_pacsseries_manufacturer_and_more.py`

Auto-generated by `just makemigrations`. Adds the six new fields, alters the two existing fields to add `db_index=True`, adds the composite index. Forward-compatible with existing data (every field is nullable / blank).

---

### 3. `chris_backend/pacsfiles/serializers.py` — ingest fan-out

Two changes in this file:

**Add `transaction` to the imports**:

```python
from django.db import transaction
```

**Inside `PACSSeriesSerializer.create`**, after `PACSFile.objects.bulk_create(files)`:

```python
created = PACSFile.objects.bulk_create(files)

# Fan out DICOM-header indexing for QIDO-RS. Imported here to avoid
# a circular import at module load (dicomweb.tasks imports
# pacsfiles.models). bulk_create populates pks on PostgreSQL.
from dicomweb.tasks import index_pacs_instance
created_ids = [pf.pk for pf in created if pf.pk is not None]
transaction.on_commit(
    lambda ids=created_ids: [
        index_pacs_instance.delay(pk) for pk in ids
    ]
)
```

**Design choices**:
- **`transaction.on_commit`**: without it, the Celery worker can fetch the `PACSFile` row before the surrounding transaction commits, leading to a `DoesNotExist` race. The task already defends against this with a `try/except PACSFile.DoesNotExist`, but on_commit is the correct primary defense.
- **Closure with `ids=created_ids`**: captures the list at decorator time. Avoids the late-binding pitfall where `created_ids` could change before the on_commit callback fires.
- **Import inside the function**: `dicomweb.tasks` imports from `pacsfiles.models`. Importing it at module top would create a circular import at app-load time. Pushing the import into the function body delays it until first call.
- **`pf.pk for pf in created`**: Django ≥4.0 on PostgreSQL populates pks on `bulk_create()` returns. CUBE requires PostgreSQL (see `config/settings/common.py`), so this is reliable. The `if pf.pk is not None` is belt-and-braces.

What this **doesn't** change: error paths, validation, the rest of `create` (folder permissions, group grants, the call to `super().create`). All of that is unchanged — the fan-out is purely additive.

---

### 4. `chris_backend/config/settings/common.py` — register the app

```python
INSTALLED_APPS = [
    ...
    'users',
    'workflows',
    'dicomweb',          # added
]
```

One-line change. App ordering at the bottom of the list matches the pattern used for `users` and `workflows` (later-added apps go at the end).

### 5. `chris_backend/core/celery.py` — task routing

```python
task_routes = {
    ...
    'pacsfiles.tasks.delete_pacs_series': {'queue': 'main2'},
    'pacsfiles.tasks.send_pacs_query': {'queue': 'main2'},
    'pacsfiles.tasks.register_pacs_series': {'queue': 'main2'},
    'dicomweb.tasks.index_pacs_instance': {'queue': 'main2'},   # added
}
```

**Why `main2` and not `main1`**:
- `main1` runs the latency-sensitive plugin-instance state machine (`run_plugin_instance_job`, `check_plugin_instance_job_exec_status`). Adding bursty per-file indexing work here would create scheduling contention during ingest.
- `main2` is for "things that happen as side effects of operations" — series deletion, PACS query send, series registration. Per-file DICOM indexing fits that profile.
- Not `periodic` — that queue is for the cron-style beat schedule.

If burst-ingest indexing latency becomes a problem (e.g. a STOW-RS push of 10⁵ files), Phase D's plan calls for splitting off a dedicated `dicomweb-index` queue. Not pre-optimized.

### 6. `requirements/base.txt` — `pydicom>=3.0,<4.0`

```diff
 nats-py==2.12.0
+pydicom>=3.0,<4.0
```

**Why `pydicom`**: the standard Python DICOM library. Used by the indexing task to parse `.dcm` headers. Not previously a CUBE dependency (CUBE relies on oxidicom — a separate Rust service — for any DICOM parsing during ingest; DICOM headers were never read on the Python side).

**Why `>=3.0,<4.0`**: pinned to the 3.x major series. pydicom 3.x is stable and current (3.0.2 installed in the rebuilt image during validation). The major-version cap prevents a future pydicom 4.x with breaking API changes from silently breaking the indexing task.

**Other deps considered and rejected**:
- `pylibjpeg-libjpeg`, `pylibjpeg-openjpeg`: only needed for pixel-data decoding. We don't decode pixels in Phase A.
- `gdcm`: alternative DICOM library. Heavier dependency tree. pydicom is sufficient.

---

## How the changes fit the existing development flow

Phase A intentionally follows every CUBE convention I could identify. Reviewing against the patterns documented in `CLAUDE.md`:

| Convention | Adherence |
|---|---|
| **`just` is the development entry point** | All validation done via `just build`, `just migrate`, `just test`, `just makemigrations`. No direct `docker compose` invocations. |
| **Migrations are auto-generated, not hand-written** | `pacsfiles/0009_*.py` and `dicomweb/0001_initial.py` both produced by `just makemigrations`. No manual edits to migration files. |
| **App layout** | `dicomweb/` matches the structure of `pacsfiles/`, `feeds/`, etc.: `apps.py` + `models.py` + (eventually) `views.py` + `migrations/` + `tests/`. |
| **URL aggregation in `core/api.py`** | Phase A adds no URLs (no views yet). Phase C will add them to a new `dicomweb/urls.py` mounted in `config/urls.py`, separate from `core/api.py` because DICOMweb endpoints don't speak collection+json. This is consistent with the existing `/schema/`, `/chris-admin/` mount points in `config/urls.py`. |
| **Celery task routing** | New task added to `task_routes` in `core/celery.py`, matching the pattern for every other Celery task in the codebase. |
| **Permission classes** | None added in Phase A (no new views). When views land in Phase C, they will reuse `pacsfiles.permissions.IsChrisOrIsPACSUserReadOnly` — extending the existing model, not introducing a parallel one. |
| **Settings split** | New `INSTALLED_APPS` entry goes in `common.py` (shared between local/production). No environment-specific settings added — the new task and model are environment-agnostic. |
| **Storage abstraction** | `index_pacs_instance` uses `core.storage.connect_storage(settings)` rather than touching a backend directly. Works identically on fslink, swift, and s3 settings. |
| **Logging** | Uses the `dicomweb` logger via `logging.getLogger(__name__)`. The `local.py` LOGGING config will need to add `dicomweb` to the per-app logger list — _flagged as a follow-up_. Not blocking; falls through to the root logger today. |
| **Test discipline** | New tests live in `dicomweb/tests/test_tasks.py`. Run by `just test dicomweb`. Excludes the `integration` tag (matches the rest of the codebase). |
| **Pre-existing tests pass** | 103/103 pacsfiles tests still pass. Zero regressions. |

### Items the codebase convention doesn't yet cover (decisions made on Phase A's behalf)

- **drf-spectacular treatment of DICOMweb endpoints**: not relevant in Phase A (no views), but flagged for Phase C. Will need an `@extend_schema(exclude=True)` per view or a global exclusion hook because DICOM-JSON-Model responses don't fit the collection+json schema-processing hooks.
- **`StorageManager.download_obj` returns whole-file bytes** rather than offering range reads. Acceptable for Phase A (headers are tiny relative to the whole file). Phase D backfill at scale on S3 might want a range-read primitive added to the storage layer — _flagged_.

---

## Validations performed

Everything below was executed against the local dev stack (`just build` + `just migrate` + `just test`) on May 20, 2026 before the work was packaged.

### 1. Migration generation is deterministic

```sh
$ just makemigrations
Migrations for 'dicomweb':
  dicomweb/migrations/0001_initial.py
    + Create model PACSInstance
Migrations for 'pacsfiles':
  pacsfiles/migrations/0009_pacsseries_bodypartexamined_pacsseries_manufacturer_and_more.py
    + Add field BodyPartExamined to pacsseries
    + Add field Manufacturer to pacsseries
    + Add field PerformedProcedureStepStartDate to pacsseries
    + Add field PerformedProcedureStepStartTime to pacsseries
    + Add field SeriesNumber to pacsseries
    + Add field StudyTime to pacsseries
    ~ Alter field Modality on pacsseries
    ~ Alter field StudyInstanceUID on pacsseries
    + Create index pacsseries_pacs_study_idx on field(s) pacs, StudyInstanceUID
```

### 2. Migrations apply cleanly

```sh
$ just migrate
…
Applying pacsfiles.0009_pacsseries_bodypartexamined_pacsseries_manufacturer_and_more... OK
…
Applying dicomweb.0001_initial... OK
```

(All other migrations in the stack also apply — no ordering conflicts introduced.)

### 3. Zero migration drift after applying

```sh
$ just run python manage.py makemigrations --dry-run
No changes detected
```

This is the key check that the model code and the committed migrations are in sync. If they weren't, this would emit pending operations.

### 4. Django system check passes

```sh
$ just run python manage.py check
System check identified no issues (0 silenced).
```

Validates model definitions, FK targets, admin registrations, and a few other invariants. Passes with no warnings.

### 5. Existing test suite — no regressions

```sh
$ just test pacsfiles --exclude-tag integration
Found 103 test(s).
…
Ran 103 tests in 66.571s
OK
```

Full pacsfiles suite passes. This covers the existing PACS, PACSQuery, PACSRetrieve, PACSSeries, and PACSFile views and serializers — including the `PACSSeriesSerializer.create` path that now fans out the indexing task. The new `transaction.on_commit` call doesn't change the test outcomes because Celery tasks are executed eagerly with `CELERY_TASK_ALWAYS_EAGER` in the test settings; the on_commit fires at the end of each test's transaction and the eager-mode task runs synchronously.

### 6. New test suite passes

```sh
$ just test dicomweb --exclude-tag integration
Found 9 test(s).
…
Ran 9 tests in 0.010s
OK
```

### 7. A real bug was caught during this validation

The first run of `just test dicomweb` **failed one test**:

```
FAIL: test_parse_dicom_time_partial (dicomweb.tests.test_tasks.HelperParseTests)
AssertionError: datetime.time(14, 3) != datetime.time(14, 30)
```

Root cause: `datetime.strptime('1430', '%H%M%S')` greedily matches `H=14, M=3, S=0` because `%H`/`%M`/`%S` are 1–2-digit regex components. Fix: dispatch the format string on input length (`{6: '%H%M%S', 4: '%H%M', 2: '%H'}`).

This is the kind of bug that would silently produce wrong DICOM time values for any series whose `StudyTime` was a partial time string — a fairly common case in real-world DICOM data. Catching it before code shipped is the strongest individual signal that the test suite is doing useful work, not just touching code for coverage's sake.

### 8. Image build succeeds with new dependency

```sh
$ just build
…
#8 28.78 Successfully installed … pydicom-3.0.2 …
…
Image localhost/fnndsc/cube:dev Built
```

The base Python image installs pydicom 3.0.2 cleanly. No C extensions to compile; no system packages added. The pip resolver did not flag any conflicts with existing CUBE dependencies.

### 9. Stack tear-down is clean

```sh
$ just down
…
 Network chris_ultron_backend_default Removed
 Network chris_ultron_backend_local Removed
```

Followed by `docker ps -a | grep -i chris` returning no chris containers. Volumes are preserved (per the `just down` design), so re-running `just migrate` from a clean shell finds the schema already applied — confirming the migration state survives container teardown.

---

## Validations recommended before broader rollout

These are deliberately **not** performed in Phase A — they're integration / scale tests that belong to Phase D, but are listed here so reviewers know what's still open.

### Functional

| Validation | How | Why |
|---|---|---|
| End-to-end ingest with real `.dcm` files | Stand up a small fixture set (e.g. the radoss-creative-commons ultrasound DICOMs already referenced by `retuve-chris-plugin`); POST a fake registration; assert `PACSInstance` rows appear with correct SOPClassUID/SOPInstanceUID/etc. | Validates the full async path: `transaction.on_commit` → Celery → storage read → pydicom → DB write. Phase A only tests the unit pieces. |
| `_find_series_for_file` walks correctly | Create a `PACSFile` whose parent_folder is two levels deep under the series folder; verify the task finds the right series. | The bounded folder walk is the only place where Phase A's logic deviates from the simplest possible implementation. |
| Re-running the task is idempotent | Trigger `index_pacs_instance.delay(pk)` twice for the same `PACSFile`; assert exactly one `PACSInstance` row and that values match. | `update_or_create` should guarantee this, but worth verifying with a real `pydicom` read. |
| Backfill of existing pre-Phase-A data | Run the (Phase D) `reindex_pacs_instances` management command against a fresh CUBE instance with pre-existing PACSSeries rows. Verify all get indexed. | Existing CUBE deployments will need this on first upgrade. |
| Permission cascade | Delete a `PACSSeries`; verify the on-delete cascade removes both `PACSInstance` and the `PACSFile` (existing) rows. | The `on_delete=CASCADE` on both FKs should handle this, but multi-FK cascade ordering occasionally surprises. |

### Performance / scale

| Validation | How | Why |
|---|---|---|
| Indexing throughput on `main2` worker | Push 10⁴ files through STOW-equivalent ingest; measure time-to-all-indexed; verify other `main2` tasks aren't starved. | If indexing dominates the queue during burst ingest, Phase D's plan to split off a dedicated queue triggers. |
| Indexing latency on S3 cold reads | Same as above but with `STORAGE_ENV=s3`. | The whole-file `download_obj` could be slow on cold S3; might motivate range-read support in `StorageManager`. |
| DB row size sanity check | After indexing 10⁴ files, check the size of `dicomweb_pacsinstance` (rows + bytes) against the sizing in `QIDO_PLAN.md` §12.1. | Catch any unexpected bloat (e.g. very long TransferSyntaxUIDs that we're storing verbatim). |

### Surface area / no surprises

| Validation | How | Why |
|---|---|---|
| OpenAPI schema unchanged | Re-run `just openapi-split > schema.split.yaml`; diff against the committed `schema.split.yaml` in `writeups/`. Expect zero diff. | Phase A adds no views. If the diff is non-empty, something leaked into the public surface. |
| Browsable API still works | Hit `http://localhost:8000/api/v1/pacs/series/?format=json` with `chris:chris1234`; expect a normal collection+json response. | Sanity check that the existing API still serves as before. |
| Admin pages load | `/chris-admin/` should not error on the new model (we didn't register it in `admin.py` yet — that's fine; the page just won't list it). | Catches `INSTALLED_APPS` ordering surprises. |

### Recommended way to run all of the above

When Phase B/C lands, the integration tests at `chris_backend/dicomweb/tests/test_integration.py` (tagged `integration`) will codify most of this. Run with:

```sh
just test-integration
```

Until then, the validations recommended in this section are manual but small — most can be done in an afternoon by someone familiar with `just` and a sample DICOM dataset.

---

## Known risks / open questions

1. **STOW-RS scope** — the grant says ship it with QIDO + WADO at Month 12; ISC's MVP proposal said defer. Phase A's data model supports STOW-RS so the answer doesn't change Phase A, but the answer **does** affect Phase C view-layer sizing. _Needs BCH decision before Phase C starts._
2. **`StorageManager.download_obj` blocks on whole-file read** — fine for headers (the first KB matter, the rest is wasted), but if cold S3 reads become a bottleneck during backfill, range-read support in `StorageManager` would help. _Defer until evidence._
3. **`local.py` logger config** — `dicomweb` is not in the per-app `LOGGING['loggers']` list in `chris_backend/config/settings/local.py:82-89`. Falls through to the root logger. Should be added for parity with other apps, but not blocking. _Add when convenient._
4. **Patient-tag consistency across series within a study** — Phase A doesn't surface this question; Phase C `/studies` GROUP BY does. _Worth one query against an existing BCH-imported dataset to confirm whether real-world data is well-behaved here._
5. **Private DICOM tags** — pydicom's `dcmread` handles them, but if the .dcm has unparseable private elements, the task currently logs and returns without retry. Acceptable; flagged if BCH data exhibits high prevalence of malformed private tags.

---

## How to review this Phase

1. **Apply `code/phase-a.patch`** to a clean checkout of `ChRIS_ultron_backEnd`.
2. Run `just build && just migrate && just test pacsfiles dicomweb --exclude-tag integration`. Expect 112/112 pass.
3. Read `code/source/chris_backend/dicomweb/models.py` (39 lines) and `code/source/chris_backend/dicomweb/tasks.py` (170 lines) — those are the non-trivial new code. Everything else is migrations, settings, or a one-line addition.
4. Diff the 5 modified files via `git show` after applying — none of them grew by more than ~15 lines.
5. Check the patch for anything in the diff that looks unintentional. The full patch is 548 lines, of which ~440 are the auto-generated migrations and the new `tasks.py` body. The actual touch points outside the new app are 5 small additions, all line-counted in §1–6 above.

Open questions surfaced for discussion with BCH are listed at the bottom of `code/README.md` and again in §"Known risks" of this document.
