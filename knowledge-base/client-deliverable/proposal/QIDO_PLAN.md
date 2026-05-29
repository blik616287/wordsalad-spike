# QIDO-RS endpoint plan

Implementation plan for **Precondition 1** of the ATLAS MVP: a native QIDO-RS surface on CUBE so any DICOMweb-compliant client (OHIF, 3D Slicer, GH's indexer, …) can browse the CUBE metadata catalog without bespoke API code or an Orthanc mirror.

WADO-RS (retrieval) and STOW-RS (push) are explicitly **not** in this plan. They get separate documents. This plan does call out the WADO-RS dependency at the seams (`RetrieveURL` fields, instance metadata) so it composes cleanly with the follow-on work.

---

## 1. Decisions locked

| Question | Choice | Implication |
|---|---|---|
| URL prefix | **`/dicom-web/pacs/<identifier>/…`** | Per-PACS DICOMweb roots — every upstream PACS source appears as its own DICOMweb server. Cleaner for Phase 2 federation. Single-PACS demo configures OHIF with one root. |
| Study representation | **Compute on the fly via GROUP BY `PACSSeries`** | No new `PACSStudy` model in MVP. Aggregation each request. Materialize later only if query latency demands. |
| Instance indexing | **Celery task per `.dcm` at registration time** | Hook in `PACSSeriesSerializer.create` after `PACSFile.objects.bulk_create`. Decoupled from oxidicom; works for any future ingest path. |
| Plan scope | QIDO-RS, OHIF smoke test, backfill cmd, capacity sizing | WADO/STOW deferred. |

---

## 2. URL surface

All under `/dicom-web/pacs/<pacs_identifier>/`. `<pacs_identifier>` matches `PACS.identifier` (the existing handle, e.g. `BCH`, `MINICHRISORTHANC`). Auth = the existing DRF chain (Token / Basic / Session); permission = `IsChrisOrIsPACSUserReadOnly`.

| Path | Method | Description | Returns |
|---|---|---|---|
| `/dicom-web/pacs/<id>/studies` | GET | Study-level QIDO. | `application/dicom+json` array of Study objects |
| `/dicom-web/pacs/<id>/studies/{StudyInstanceUID}/series` | GET | Series within a study. | array of Series objects |
| `/dicom-web/pacs/<id>/studies/{StudyInstanceUID}/series/{SeriesInstanceUID}/instances` | GET | Instances within a series. | array of Instance objects |
| `/dicom-web/pacs/<id>/series` | GET | Cross-study series search within this PACS. | array of Series objects |
| `/dicom-web/pacs/<id>/instances` | GET | Cross-study instance search within this PACS. | array of Instance objects |

**Out of scope here but reserved**: `…/studies/{…}/metadata`, `…/series/{…}/metadata`, `…/instances/{…}/metadata` (those are WADO-RS Metadata — separate plan).

**Content negotiation**:
- Default and required: `application/dicom+json` (PS3.18 §F).
- Also accept `application/json` (OHIF tolerates both).
- 406 if the client sends anything else.

**HTTP semantics**:
- 200 with empty array `[]` when there are matches but the filtered result is empty.
- 204 when there are no matches and the spec allows it. We'll use 200+`[]` uniformly — simpler for clients.
- 400 on malformed query parameters (bad tag hex, malformed date range).
- 401 / 403 for auth / permission.
- 413 if a query would return more than the hard limit (see §5.3).

URL registration: add a new `dicom_web/` URL include in `chris_backend/config/urls.py`, separate from `core/api.py`. The QIDO surface does not use collection+json or the existing aggregated `format_suffix_patterns`; it must not be polluted by them.

```python
# config/urls.py
urlpatterns = [
    ...
    path('dicom-web/pacs/<str:pacs_identifier>/', include('dicomweb.urls')),
]
```

New Django app: **`dicomweb/`** under `chris_backend/`. Keeps the namespace and post-processing concerns isolated from `pacsfiles/`.

---

## 3. Schema changes

### 3.1 New model: `PACSInstance`

One row per `.dcm` file. Created by a Celery task at ingest, deleted on cascade from `PACSSeries`.

```python
# dicomweb/models.py  (lives in the new app, not pacsfiles)

class PACSInstance(models.Model):
    series = models.ForeignKey(
        'pacsfiles.PACSSeries', on_delete=models.CASCADE, related_name='instances',
    )
    pacs_file = models.OneToOneField(
        'pacsfiles.PACSFile', on_delete=models.CASCADE, related_name='dicom_instance',
    )

    SOPClassUID        = models.CharField(max_length=100, db_index=True)
    SOPInstanceUID     = models.CharField(max_length=100, db_index=True)
    InstanceNumber     = models.IntegerField(null=True, blank=True)
    Rows               = models.IntegerField(null=True, blank=True)
    Columns            = models.IntegerField(null=True, blank=True)
    BitsAllocated      = models.IntegerField(null=True, blank=True)
    NumberOfFrames     = models.IntegerField(null=True, blank=True)
    TransferSyntaxUID  = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = ('series', 'SOPInstanceUID')
        indexes = [
            models.Index(fields=['SOPInstanceUID']),
            models.Index(fields=['SOPClassUID']),
        ]
```

Notes:
- Placed in the **new `dicomweb` app**, not `pacsfiles`. Keeps DICOMweb-specific concerns local; `pacsfiles` continues to own ingest and the existing `/api/v1/pacs/…` surface.
- The `pacs_file` 1-to-1 means each `PACSInstance` is the metadata index for exactly one `PACSFile`. WADO-RS will use this link to find the storage path.
- Patient/Study/Series tags are **not denormalized** onto `PACSInstance` — joins go through `series` FK. This keeps a single source of truth for those tags on `PACSSeries`.

### 3.2 Small additions to `PACSSeries`

The minimum to satisfy QIDO-RS Series Result Attributes (PS3.18 Table 10.4.1-2):

| Field | Type | Why |
|---|---|---|
| `SeriesNumber` | `IntegerField(null=True)` | Mandatory in QIDO series result |
| `PerformedProcedureStepStartDate` | `DateField(null=True)` | Mandatory |
| `PerformedProcedureStepStartTime` | `TimeField(null=True)` | Mandatory |
| `BodyPartExamined` | `CharField(max_length=16, blank=True)` | Common QIDO filter |
| `Manufacturer` | `CharField(max_length=64, blank=True)` | Common QIDO filter |

These get added directly to `pacsfiles/models.py:PACSSeries` rather than to `dicomweb/`. They're general DICOM tags, not DICOMweb-specific.

### 3.3 Migrations

Two migrations, in two PRs:

1. **`pacsfiles/migrations/0XXX_series_qido_tags.py`** — adds the 5 new fields to `PACSSeries` (all nullable; no backfill needed for the columns themselves, populated by the Celery task in §4).
2. **`dicomweb/migrations/0001_initial.py`** — creates `PACSInstance`.

Both are additive and forward-compatible with existing `PACSSeries` data.

---

## 4. Ingest pipeline changes

### 4.1 Hook into `PACSSeriesSerializer.create`

`pacsfiles/serializers.py:153-222` currently `bulk_create`s `PACSFile` rows. After that, fan out one Celery task per file:

```python
# pacsfiles/serializers.py  (additions)
from dicomweb.tasks import index_pacs_instance

# inside PACSSeriesSerializer.create, after PACSFile.objects.bulk_create(files):
transaction.on_commit(lambda: [
    index_pacs_instance.delay(pf.id) for pf in files
])
```

`transaction.on_commit` matters — without it, the worker can pick up the task before the row is visible.

### 4.2 The indexing task

```python
# dicomweb/tasks.py
from celery import shared_task
import pydicom
from django.conf import settings
from core.storage import connect_storage
from pacsfiles.models import PACSFile
from dicomweb.models import PACSInstance

@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def index_pacs_instance(self, pacs_file_id):
    pacs_file = PACSFile.objects.select_related('parent_folder').get(pk=pacs_file_id)
    # Find the parent PACSSeries via folder ancestry
    series = _find_series_for_file(pacs_file)
    if series is None:
        return  # not a series file (e.g. metadata sidecar)

    storage = connect_storage(settings)
    with storage.open(pacs_file.fname.name, 'rb') as f:
        ds = pydicom.dcmread(f, stop_before_pixels=True, force=True)

    PACSInstance.objects.update_or_create(
        series=series,
        SOPInstanceUID=str(ds.SOPInstanceUID),
        defaults=dict(
            pacs_file=pacs_file,
            SOPClassUID=str(ds.SOPClassUID),
            InstanceNumber=getattr(ds, 'InstanceNumber', None),
            Rows=getattr(ds, 'Rows', None),
            Columns=getattr(ds, 'Columns', None),
            BitsAllocated=getattr(ds, 'BitsAllocated', None),
            NumberOfFrames=getattr(ds, 'NumberOfFrames', 1) or 1,
            TransferSyntaxUID=str(ds.file_meta.TransferSyntaxUID) if ds.file_meta else '',
        ),
    )
```

Also backfills the new series-level tags (`SeriesNumber`, `BodyPartExamined`, etc.) on the parent `PACSSeries` if they're not already set — idempotent.

### 4.3 Queue routing

Add to `chris_backend/core/celery.py:task_routes`:
```python
'dicomweb.tasks.index_pacs_instance': {'queue': 'main2'},
```

`main2` because it's per-file work and oxidicom can push thousands of files in a burst — `main1` is reserved for the plugin-instance state machine which is latency-sensitive.

### 4.4 Idempotency

`update_or_create` keyed on `(series, SOPInstanceUID)` makes retries safe. If a file is re-ingested with the same UID but a different `PACSFile` row, the 1-to-1 `pacs_file` field would conflict — handle by checking and reassigning, or by deleting the orphan `PACSInstance` before creating the new one. Edge case; document but don't over-engineer.

---

## 5. Query parsing

A new `dicomweb/query.py` module owns the QIDO query string → Django ORM filter translation.

### 5.1 Accepted query forms

Per PS3.18 §F.7 plus pragmatic OHIF compatibility:

| Form | Example | Notes |
|---|---|---|
| Tag hex | `?00100010=DOE^JANE` | 8 hex chars, no comma. Case-insensitive on the key. |
| Keyword | `?PatientName=DOE^JANE` | Standard DICOM keyword. |
| Multi-value (OR) | `?00080060=CT,MR,US` | Comma-separated. |
| Range (dates) | `?StudyDate=20230101-20231231` | Open-ended `20230101-` and `-20231231` allowed. |
| Wildcard (PN, LO, SH, LT, ST, UT) | `?PatientName=DOE*` | `*` → `%`, `?` → `_`. Translated to `ILIKE`. |
| `includefield` | `?includefield=00081030,00081032` or `?includefield=all` | Adds optional tags to the response. |
| Tag with no value | `?00080060` | Same as `includefield=00080060`. |
| `fuzzymatching` | `?fuzzymatching=true` | **Stub for MVP**: log it, ignore it. PostgreSQL `pg_trgm` is a follow-up if anyone asks. |
| `limit`, `offset` | `?limit=50&offset=100` | Standard. Hard ceiling on `limit` (see §5.3). |

### 5.2 Tag → model field map

A static lookup table keyed by tag hex (uppercase, 8-char). For tags that aren't on any model row (e.g. `ModalitiesInStudy` is computed), the entry points at a `QueryAggregation` rather than a field.

```python
# dicomweb/query.py
TAG_MAP_STUDY = {
    '00100010': ModelField('series__PatientName', vr='PN', wildcard=True),
    '00100020': ModelField('series__PatientID', vr='LO'),
    '00100030': ModelField('series__PatientBirthDate', vr='DA', range_=True),
    '00100040': ModelField('series__PatientSex', vr='CS', choices=['M','F','O']),
    '0020000D': ModelField('series__StudyInstanceUID', vr='UI'),
    '00080020': ModelField('series__StudyDate', vr='DA', range_=True),
    '00080030': ModelField('series__StudyTime', vr='TM', range_=True),  # NEEDS column or omit
    '00080050': ModelField('series__AccessionNumber', vr='SH', wildcard=True),
    '00081030': ModelField('series__StudyDescription', vr='LO', wildcard=True),
    '00080061': Aggregation('ModalitiesInStudy'),
    '00201206': Aggregation('NumberOfStudyRelatedSeries'),
    '00201208': Aggregation('NumberOfStudyRelatedInstances'),
    ...
}
TAG_MAP_SERIES = { ... }
TAG_MAP_INSTANCE = { ... }
```

Tags not present on any of our models and not in an aggregation are **silently dropped from the filter** (returns the unfiltered superset for that constraint) — per spec, servers are allowed to reject or ignore unsupported match keys. We'll ignore, and document.

`StudyTime` is in the QIDO required set but not currently on `PACSSeries`. Add it to the §3.2 list (one more `TimeField`).

### 5.3 Limits

- Default `limit`: 50 (matches OHIF default).
- Max `limit`: **5000**. Beyond that, return 413 with a `Warning: 299 - "Too many matches; refine query"` header per spec §F.6.
- No `limit` set: cap at the default.
- Pagination is via `limit`/`offset`. Don't expose `cursor` (CUBE's existing pagination is `LimitOffsetPagination`; consistent).

---

## 6. DICOM JSON renderer

Add `DicomJsonRenderer` to **`dicomweb/renderers.py`** (not `core/renderers.py` — it's DICOMweb-specific).

```python
class DicomJsonRenderer(BaseRenderer):
    media_type = 'application/dicom+json'
    format = 'dicom+json'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return json.dumps(data, ensure_ascii=False).encode('utf-8')
```

The view assembles the response as a list of dicts already in DICOM JSON Model shape:

```python
{
  "0020000D": {"vr": "UI", "Value": ["1.2.840.113619.2.55.3.604688119.971.1437406488.926"]},
  "00100010": {"vr": "PN", "Value": [{"Alphabetic": "DOE^JANE"}]},
  "00100040": {"vr": "CS", "Value": ["F"]},
  ...
}
```

A `dicomweb/dicomjson.py` helper converts `(tag_hex, vr, raw_value)` tuples to the canonical JSON structure. VR lookup uses `pydicom.datadict.dictionary_VR(tag_int)` — no need to hardcode VRs for every tag.

Special cases the helper must handle:
- **PN** (Person Name): `Value` is `[{"Alphabetic": "..."}]` not `["..."]`.
- **SQ** (Sequence): nested JSON dataset; not used at any QIDO level we emit, but the helper should error loudly if asked.
- **DA / TM / DT**: serialized as their DICOM string form (`"20230102"`, not ISO date), even though the DB stores them as Python `date`/`time`.
- **AT / OB / OW / UN**: not emitted at the QIDO surface (these carry pixel/binary data — QIDO returns metadata only).
- **Empty fields**: emit `{"vr": "...", "Value": []}` or omit the tag entirely. Spec allows either; omit (smaller responses, OHIF handles).

Browsable-API support: also add an `application/json` renderer alias that emits the same structure but with `Content-Type: application/json` — debugging aid, not a spec requirement.

---

## 7. View layer

Six view classes, all `ListAPIView` subclasses (no detail endpoints — QIDO is always list-shaped).

### 7.1 Skeleton (shared base)

```python
# dicomweb/views.py
class QidoBaseView(generics.GenericAPIView):
    permission_classes = (permissions.IsAuthenticated, IsChrisOrIsPACSUserReadOnly)
    renderer_classes = (DicomJsonRenderer, JSONRenderer)

    def get_pacs(self):
        return get_object_or_404(PACS, identifier=self.kwargs['pacs_identifier'])

    def get(self, request, *args, **kwargs):
        pacs = self.get_pacs()
        self.check_object_permissions(request, pacs)
        rows = self.build_queryset(pacs, request.query_params)
        rows = self.apply_pagination(rows, request.query_params)
        return Response([self.serialize_row(r, request) for r in rows])
```

Each subclass overrides `build_queryset` and `serialize_row`. No DRF `ModelSerializer` involved — the DICOM JSON Model is too far from a flat Python dict for the serializer machinery to be load-bearing here.

### 7.2 `StudyListView` — `/studies`

```python
def build_queryset(self, pacs, params):
    qs = PACSSeries.objects.filter(pacs=pacs)
    qs = QueryFilter(TAG_MAP_STUDY).apply(qs, params)
    # GROUP BY study-identifying tags + patient tags
    return (
        qs.values(
            'StudyInstanceUID', 'StudyDate', 'StudyTime', 'AccessionNumber',
            'StudyDescription', 'PatientID', 'PatientName', 'PatientBirthDate',
            'PatientSex',
        )
        .annotate(
            ModalitiesInStudy=ArrayAgg('Modality', distinct=True),
            NumberOfStudyRelatedSeries=Count('id', distinct=True),
            NumberOfStudyRelatedInstances=Count('instances'),
        )
        .order_by('-StudyDate', 'StudyInstanceUID')
    )
```

A `PACSSeries` row's patient tags are assumed consistent across all series with the same `StudyInstanceUID` within a single PACS. Real-world data sometimes violates this; for MVP we accept the natural GROUP BY semantics (the row with the lexicographically smallest patient tag wins). Document the assumption.

### 7.3 `StudySeriesListView` — `/studies/{StudyInstanceUID}/series`

```python
def build_queryset(self, pacs, params):
    qs = PACSSeries.objects.filter(
        pacs=pacs, StudyInstanceUID=self.kwargs['StudyInstanceUID'],
    )
    return QueryFilter(TAG_MAP_SERIES).apply(qs, params)
```

### 7.4 `SeriesInstanceListView` — `/studies/{…}/series/{…}/instances`

```python
def build_queryset(self, pacs, params):
    series = get_object_or_404(
        PACSSeries, pacs=pacs,
        StudyInstanceUID=self.kwargs['StudyInstanceUID'],
        SeriesInstanceUID=self.kwargs['SeriesInstanceUID'],
    )
    qs = PACSInstance.objects.filter(series=series)
    return QueryFilter(TAG_MAP_INSTANCE).apply(qs, params)
```

### 7.5 `AllSeriesListView` and `AllInstanceListView`

Cross-study variants — same as 7.3 / 7.4 minus the StudyInstanceUID filter.

### 7.6 `serialize_row`

Three implementations, one per resource level. Each emits the **required** QIDO attributes plus any tags the client requested via `includefield`. The required sets are PS3.18 §10.6.1.2.2 (Study), §10.6.1.2.2.1 (Series), §10.6.1.2.2.2 (Instance). The DICOM JSON helper from §6 handles the heavy lifting.

---

## 8. `RetrieveURL` strategy

Required at every level. Until WADO-RS lands, point at the URL that *will* exist:

```python
def retrieve_url_study(request, pacs_id, study_uid):
    return request.build_absolute_uri(
        f'/dicom-web/pacs/{pacs_id}/studies/{study_uid}'
    )
def retrieve_url_series(request, pacs_id, study_uid, series_uid):
    return request.build_absolute_uri(
        f'/dicom-web/pacs/{pacs_id}/studies/{study_uid}/series/{series_uid}'
    )
def retrieve_url_instance(...):
    return request.build_absolute_uri(
        f'/dicom-web/pacs/{pacs_id}/studies/{study_uid}/series/{series_uid}/instances/{sop_uid}'
    )
```

Those WADO-RS paths return 404 today. **OHIF will display the catalog but fail to render images** until WADO-RS lands — which is the expected MVP intermediate state. The proposal's MVP demo dispatches an inference plugin programmatically, not a live OHIF render, so this is fine.

Document this clearly in the README / changelog so nobody opens an OHIF "regression" bug.

---

## 9. Auth / permissions

Reuse the existing PACS access model:
- `permissions.IsAuthenticated` + `IsChrisOrIsPACSUserReadOnly` from `pacsfiles/permissions.py`. All QIDO endpoints are read-only.
- LDAP-backed Django auth continues to flow through DRF's `TokenAuthentication` + `BasicAuthentication` + `SessionAuthentication`.
- Browser-based clients (OHIF) authenticate via `Authorization: Bearer <token>` or `Authorization: Basic …` — both already work; document in the OHIF config (§11).

No CORS changes needed — `corsheaders` is already configured with `CORS_ALLOW_ALL_ORIGINS = True` in `local.py`. Production CORS is a deployment concern, not in scope here.

---

## 10. Backfill management command

`chris_backend/dicomweb/management/commands/reindex_pacs_instances.py`

```python
class Command(BaseCommand):
    help = 'Build PACSInstance rows for existing PACSFile data.'

    def add_arguments(self, parser):
        parser.add_argument('--pacs', help='Limit to one PACS identifier')
        parser.add_argument('--series', help='Limit to one SeriesInstanceUID')
        parser.add_argument('--workers', type=int, default=8)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        qs = PACSFile.get_base_queryset()
        if opts['pacs']:
            qs = qs.filter(fname__startswith=f'SERVICES/PACS/{opts["pacs"]}/')
        # … walk files in batches, dispatch index_pacs_instance.delay() …
```

Idempotent. Safe to run repeatedly. Use it once after the DICOMweb app deploys to populate `PACSInstance` for the pre-existing BCH dataset, then again any time we suspect drift (logged on its own; not a routine cron).

Document in the README precondition: "After deploying the DICOMweb app, run `just bash` then `python manage.py reindex_pacs_instances` once."

---

## 11. OHIF smoke-test checklist

End-to-end validation, run by a human after every meaningful change to the DICOMweb surface.

### 11.1 Configure OHIF

Local OHIF Viewer 3.x (`docker run`-able), point at the dev CUBE:

```js
// platform/app/public/config/cube.js
window.config = {
  routerBasename: '/',
  servers: {
    dicomWeb: [{
      name: 'CUBE-BCH',
      qidoRoot:  'http://localhost:8000/dicom-web/pacs/BCH',
      wadoRoot:  'http://localhost:8000/dicom-web/pacs/BCH',  // WADO not implemented yet
      qidoSupportsIncludeField: true,
      supportsReject: false,
      supportsStow: false,
      imageRendering: 'wadors',
      thumbnailRendering: 'wadors',
      requestOptions: {
        auth: 'chris:chris1234',  // dev only
      },
    }],
  },
};
```

### 11.2 Pre-flight (CLI)

```sh
# 1. Auth token works
xh POST :8000/api/v1/auth-token/ username=chris password=chris1234

# 2. Per-PACS QIDO returns Studies
xh -a chris:chris1234 :8000/dicom-web/pacs/BCH/studies \
   Accept:application/dicom+json | jq length

# 3. Specific tags only
xh -a chris:chris1234 ":8000/dicom-web/pacs/BCH/studies?PatientName=*&limit=5" \
   Accept:application/dicom+json

# 4. Drill to Series and Instance
STUDY_UID=$(xh -a chris:chris1234 :8000/dicom-web/pacs/BCH/studies Accept:application/dicom+json | jq -r '.[0]."0020000D".Value[0]')
xh -a chris:chris1234 ":8000/dicom-web/pacs/BCH/studies/$STUDY_UID/series" \
   Accept:application/dicom+json
```

### 11.3 OHIF browser checks (manual)

| Step | Expected | Fail mode to investigate |
|---|---|---|
| OHIF home page loads | List of Studies, count > 0 | If 0: auth or empty PACS; check `?limit=5000` in dev console |
| Filter "Patient Name" | Studies filter without page reload | If broken: query parser not handling wildcard |
| Click into a Study | Series list renders | If broken: 7.3 view or RetrieveURL parsing |
| Click into a Series | Image panel loads but pixels fail to render | **Expected** until WADO-RS — document |
| Change date range | Filtered list | If broken: range parser for DA VR |
| `Accept: application/json` | Same shape via plain JSON | Renderer fallback check |

### 11.4 Headless integration test

Add `chris_backend/dicomweb/tests/test_integration.py`:
- Loads a fixture set of `.dcm` files (small public dataset, committed to the repo or fetched in `setUp`)
- Posts the same ingest registration the real ingest pipeline does
- Hits each QIDO endpoint and asserts on response shape, required tags present, content-type
- Tagged `integration` so it runs under `just test-integration`

---

## 12. Capacity / index sizing

Rough sizing for the BCH dataset; tune once the actual file count is known.

### 12.1 Row scale assumption

- ~10⁴ Studies, ~10⁵ Series, ~10⁶–10⁷ Instances per CUBE node is a reasonable upper bound for a clinical research dataset over years. BCH's MVP dataset is much smaller (low thousands of instances).
- Each `PACSInstance` row is ~250 bytes on disk + indexes; 10⁶ rows ≈ 250 MB table, ~500 MB with indexes. Postgres-trivial.

### 12.2 Indexes to add at migration time

```sql
-- PACSSeries (new fields)
CREATE INDEX pacsseries_studyinstanceuid_idx     ON pacsfiles_pacsseries (StudyInstanceUID);
CREATE INDEX pacsseries_seriesnumber_idx          ON pacsfiles_pacsseries (SeriesNumber);
CREATE INDEX pacsseries_modality_idx              ON pacsfiles_pacsseries (Modality);
CREATE INDEX pacsseries_pacs_study_idx            ON pacsfiles_pacsseries (pacs_id, StudyInstanceUID);

-- PACSInstance
CREATE INDEX pacsinstance_series_id_idx           ON dicomweb_pacsinstance (series_id);
CREATE INDEX pacsinstance_sopinstanceuid_idx      ON dicomweb_pacsinstance (SOPInstanceUID);
CREATE INDEX pacsinstance_sopclassuid_idx         ON dicomweb_pacsinstance (SOPClassUID);
```

`PatientName` wildcard searches will be `ILIKE '...%'` — fine for prefix matches; for arbitrary substring (`'%foo%'`) Postgres needs `pg_trgm` to use an index. Defer trigram support — OHIF defaults to prefix matching.

### 12.3 Aggregation cost

Study-level GROUP BY scans every `PACSSeries` row in the PACS. With 10⁵ series rows the aggregation runs in tens of ms on indexed Postgres — fine. With 10⁶ series rows we'd start seeing seconds; at that scale, materialize `PACSStudy`. Threshold to revisit: **first user report of `/studies` taking >500 ms on a representative dataset**.

`NumberOfStudyRelatedInstances` requires joining through `PACSInstance` — make sure `pacsinstance_series_id_idx` is in place before turning that aggregation on.

### 12.4 Celery throughput

`index_pacs_instance` reads one `.dcm` (just the header, `stop_before_pixels=True`) — ~5-20 ms per file in fslink storage, more for S3 cold reads. With 4 worker processes on the `main2` queue (current `docker-compose.yml` setting) that's ~200 files/sec. A 10⁴-file BCH dataset indexes in ~1 minute. Fine.

---

## 13. Sequencing / milestones

Roughly the **~1-2 months, 1 engineer** the proposal estimates. Each phase ends with something you can demo.

| Phase | Duration | Deliverable | Demo |
|---|---|---|---|
| **A. Schema + ingest** | ~1 week | New `dicomweb` app, `PACSInstance` model, 2 migrations, Celery indexing task wired into series creation | Ingest a series; `select * from dicomweb_pacsinstance` shows rows |
| **B. Query + renderer** | ~1 week | `dicomweb/query.py` tag-map + filter, `dicomweb/dicomjson.py` helper, `DicomJsonRenderer` | Unit tests pass on tag-hex parsing, wildcard / range / multi-value handling, PN/DA/CS VR rendering |
| **C. Views** | ~1 week | All 5 QIDO endpoints under `/dicom-web/pacs/<id>/…`, RetrieveURL placeholders | `xh` calls from §11.2 return correct shapes |
| **D. Backfill + integration** | ~3-5 days | Management command, integration tests tagged `integration`, OHIF smoke test passes for browsing | OHIF lists BCH studies and drills into Series (no pixels yet) |
| **E. Polish** | ~3-5 days | OpenAPI annotations on the new views (spectacular post-processing hook to skip them — they're not collection+json), README update, performance check on the BCH dataset | `just openapi-split` runs clean; perf doc shows /studies under 200 ms on BCH dataset |

WADO-RS is a separate plan that should land **immediately after Phase E** so OHIF can actually render. The proposal's MVP demo doesn't require it (programmatic inference), but the GH integration story in Phase 2 does.

---

## 14. Open questions / risks

### 14.1 To answer before writing code

1. **`StudyTime`** — not currently in `PACSSeries`. Required by QIDO. Either add the column (in §3.2) or always return empty `Value: []`. Recommendation: add the column.
2. **Patient tags inconsistent across series in a Study** — does any current data in CUBE actually exhibit this? If yes, the GROUP BY needs deterministic conflict resolution (e.g. `MIN`). Worth one query against an existing BCH-imported dataset before committing to "natural" GROUP BY.
3. **Fuzzy matching (`fuzzymatching=true`)** — confirmed deferred? OHIF doesn't require it. GH's indexer may. Worth asking GH in the Phase 2 kickoff before we close this off.
4. **Per-PACS root vs. unified `/dicom-web/studies`** — confirmed per-PACS. If GH's indexer is happier with one root that returns all studies across all PACSes, we may need to add the unified root as an alias. Defer until we have GH's actual indexer to test against.
5. **OpenAPI / drf-spectacular** — these endpoints don't fit the collection+json schema-processing pipeline (different content-type, very different shape). Add them to `SPECTACULAR_SETTINGS['PREPROCESSING_HOOKS']` as exclusions, or annotate each view with `@extend_schema(exclude=True)`. Decide before Phase E.

### 14.2 Real risks

- **Indexing latency under burst ingest**. A C-STORE burst of 10⁵ files would queue 10⁵ Celery tasks. The `main2` queue would soak it but plugin-instance state-machine tasks share the queue. If this becomes a problem during the BCH ingest, split off a dedicated `dicomweb-index` queue. Track and decide; don't pre-optimize.
- **Storage cold reads on S3 backend**. If we switch the demo to S3 backend (precondition 1 explicitly contrasts CUBE's storage flexibility with Orthanc's), indexing latency goes up. Pre-warming via the backfill command before the demo is the mitigation.
- **DICOM tag VR edge cases**. The mapping in §5.2 handles standard tags. Private tags (group `0009xxxx` and similar) have no VR in `pydicom.datadict` — they'll cause `dictionary_VR` to raise `KeyError`. Guard with `try/except → "UN"`, log a warning, move on. Public BCH data is unlikely to hit this, but private tags are common in real PACS data.
- **Sort order for `/studies`** — DICOMweb spec doesn't mandate one. We sort by `StudyDate DESC` so OHIF's default Study List looks sensible. GH may want different defaults; make `?orderby=` a future option, not MVP.

---

## 15. Files touched / created (concrete index)

**Created**:
- `chris_backend/dicomweb/__init__.py`
- `chris_backend/dicomweb/apps.py`
- `chris_backend/dicomweb/models.py` — `PACSInstance`
- `chris_backend/dicomweb/migrations/0001_initial.py`
- `chris_backend/dicomweb/tasks.py` — `index_pacs_instance`
- `chris_backend/dicomweb/query.py` — `TAG_MAP_*`, `QueryFilter`
- `chris_backend/dicomweb/dicomjson.py` — DICOM JSON Model helper
- `chris_backend/dicomweb/renderers.py` — `DicomJsonRenderer`
- `chris_backend/dicomweb/views.py` — 5 view classes
- `chris_backend/dicomweb/urls.py`
- `chris_backend/dicomweb/management/commands/reindex_pacs_instances.py`
- `chris_backend/dicomweb/tests/test_query.py`
- `chris_backend/dicomweb/tests/test_dicomjson.py`
- `chris_backend/dicomweb/tests/test_views.py`
- `chris_backend/dicomweb/tests/test_integration.py` (tagged `integration`)

**Modified**:
- `chris_backend/config/urls.py` — include `dicomweb.urls`
- `chris_backend/config/settings/common.py` — `INSTALLED_APPS += ['dicomweb']`; spectacular exclusion
- `chris_backend/core/celery.py` — add `dicomweb.tasks.index_pacs_instance` to `task_routes`
- `chris_backend/pacsfiles/models.py` — add 6 fields to `PACSSeries` (`SeriesNumber`, `StudyTime`, `PerformedProcedureStepStartDate`, `PerformedProcedureStepStartTime`, `BodyPartExamined`, `Manufacturer`)
- `chris_backend/pacsfiles/migrations/0XXX_qido_series_fields.py`
- `chris_backend/pacsfiles/serializers.py` — fan out `index_pacs_instance.delay()` on commit in `PACSSeriesSerializer.create`

Roughly **~15 new files, ~5 modified files**. The blast radius outside `dicomweb/` is small — the design deliberately isolates DICOMweb concerns from the existing PACS app.
