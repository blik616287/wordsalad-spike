# 12 — L2 DICOMweb Implementation (the `dicomweb` Django app), in depth

> **Scope.** This is an engineering reference to **our** DICOMweb implementation:
> the `dicomweb` Django app that adds QIDO-RS / WADO-RS / STOW-RS on top of
> CUBE's existing `pacsfiles` storage tree. It documents the *actual code* in
> `implementation/dicomweb-l2/`, faithfully and with citations of the form
> `file:line`. For the **standard** (what QIDO/WADO/STOW *are*), see
> `knowledge-base/05-dicomweb-qido-wado-stow.md` — this file does not duplicate it.
> For the **architecture decisions** (variants A/B/C, `PACSStudy` vs GROUP BY,
> STOW scope, pg_trgm) see `knowledge-base/08-l2-architecture-decisions.md`.
>
> **Validation status (this session, authoritative).** 97/97 tests pass in a
> real CUBE checkout, plus live HTTP exercise of the endpoints. Several bugs were
> found *by running it in CUBE* and fixed; they are called out below as
> **HARDENING** so you can speak to them honestly. Variant-C indexing (no file
> re-read) was proven live by indexing a study whose `.dcm` does not exist in
> storage.

---

## 0. One-paragraph orientation

The app is a **drop-in** for `chris_backend/dicomweb/` (`README.md:6`). It is
deliberately isolated from `pacsfiles` so its `application/dicom+json` /
`multipart/related` surface never perturbs CUBE's stable collection+json
`/api/v1/pacs/...` API (`apps.py:5-14`). It mounts at
`/dicom-web/pacs/<pacs_identifier>/...` from `config/urls.py` (NOT
`core/api.py`), bypassing CUBE's `format_suffix_patterns` router (`urls.py:1-37`,
`urls.py:127-138`). It reuses CUBE's auth chain (Token/Basic/Session, LDAP-backed)
and the existing `IsChrisOrIsPACSUserReadOnly` permission verbatim — no new auth
code (`qido_views.py:60-63`, `README.md:117`).

The module layout (`README.md:30-51`):

```
dicomweb/
├── apps.py            # AppConfig.ready(): register signal + trigram lookup
├── models.py          # PACSStudy (NEW, L2) + PACSInstance (Phase A)
├── dicomjson.py        # DICOM JSON Model (PS3.18 Annex F) encoder — framework-free
├── query_parser.py     # QIDO query string → Django ORM Q; keyword↔tag↔field map
├── renderers.py        # DicomJson / DicomJsonAsJson / MultipartRelated
├── serializers.py      # row → DICOM JSON Model + RetrieveURLBuilder
├── multipart.py        # multipart/related parser for STOW — framework-free
├── qido_views.py       # QIDO-RS endpoints
├── wado_views.py       # WADO-RS retrieve + metadata + native frames/bulkdata
├── stow_views.py       # STOW-RS store
├── urls.py             # routing + dispatcher views + config wiring notes
├── signals.py          # post_save(PACSFile) → auto-index
├── tasks.py            # index_pacs_instance (re-read) + index_from_metadata (variant C)
├── management/commands/{reindex_pacs_instances,consume_dicomweb_index}.py
├── migrations/         # 0001 PACSInstance, 0002 PACSStudy, 0003 pg_trgm
└── tests/
```

---

## 1. Data model and migrations

### 1.1 The hierarchy

Stock CUBE collapses Patient + Study + Series tags onto a **single**
`pacsfiles.PACSSeries` row and stores nothing per-instance (`models.py:4-7`).
QIDO needs an explicit hierarchy, so the app adds two models and a FK
(`models.py:17-28`):

```
PACS ──< PACSStudy ──< PACSSeries ──< PACSInstance ──1:1── PACSFile (storage)
         Study+Patient   Series tags   SOP/geometry/        the .dcm bytes
         tags + counts    (+FK→Study)   xfer-syntax
```

- **`PACSStudy`** — one row per `(PACS, StudyInstanceUID)`. **NEW in L2.** Carries
  Patient + Study tags plus denormalized roll-up counters (`models.py:33-96`).
- **`PACSInstance`** — one row per `.dcm`, 1-to-1 with `pacsfiles.PACSFile`.
  Shipped in Phase A, kept verbatim (`models.py:103-142`).
- **`PACSSeries`** lives in `pacsfiles.models` (the central existing table). It
  gains a *nullable* FK `study → dicomweb.PACSStudy` via an additive `pacsfiles`
  migration (`models.py:21-23`, `models.py:146-168`).

**Patient stays implicit** — its tags ride on `PACSStudy`, matching how QIDO
returns Patient tags at the Study level (PS3.18 Table 10.6.3-3). Promote to a
`PACSPatient` model only if a concrete query demands it (`models.py:24-28`,
`models.py:54`). This is decision **D2** in `08-l2-architecture-decisions.md`.

### 1.2 `PACSStudy` fields (`models.py:52-100`)

- Patient: `PatientID` (db_index), `PatientName`, `PatientBirthDate`,
  `PatientSex` (choices M/F/O).
- Study: `StudyInstanceUID` (db_index), `StudyDate` (db_index), `StudyTime`,
  `AccessionNumber` (db_index), `StudyDescription`, `ReferringPhysicianName`.
- **Denormalized roll-ups, maintained at ingest/delete** (`models.py:72-78`):
  - `ModalitiesInStudy` — multi-valued CS, stored backslash-joined the way DICOM
    encodes multi-value on the wire (`"CT\\MR"`); split on render via
    `modalities_list()` (`models.py:98-100`).
  - `NumberOfStudyRelatedSeries`, `NumberOfStudyRelatedInstances` — integer counters.
- `Meta`: `unique_together = ('pacs', 'StudyInstanceUID')`, two composite indexes
  `(pacs, StudyInstanceUID)` and `(pacs, PatientID)` (`models.py:84-93`).

Patient tags are *deliberately duplicated* here from `PACSSeries`: `PACSStudy` is
the single source of truth for Study-level reads, and find-or-create resolves
cross-series patient-tag conflicts once at ingest rather than per query
(`models.py:47-51`).

### 1.3 `PACSInstance` fields (`models.py:117-142`)

`series` FK → `PACSSeries`; `pacs_file` **OneToOne** → `PACSFile` (the WADO byte
resolver). Instance/geometry columns: `SOPClassUID` (db_index), `SOPInstanceUID`
(db_index), `InstanceNumber`, `Rows`, `Columns`, `BitsAllocated`,
`NumberOfFrames`, `TransferSyntaxUID`. `unique_together = ('series',
'SOPInstanceUID')` (`models.py:137-139`).

### 1.4 Migrations

| Migration | What it does | Citation |
|---|---|---|
| `0001_initial` | `CreateModel(PACSInstance)` — Phase A, unchanged | `migrations/0001_initial.py:16-35` |
| `0002_pacsstudy` | `CreateModel(PACSStudy)` + the two composite indexes | `migrations/0002_pacsstudy.py:22-55` |
| `0003_pg_trgm` | `TrigramExtension()` (CREATE EXTENSION pg_trgm) **+** a trigram GIN index on `PACSStudy.PatientName` (`gin_trgm_ops`) | `migrations/0003_pg_trgm.py:16-25` |

`0001` depends on `pacsfiles.0008` (`migrations/0001_initial.py:11-13`); `0002`
depends on `pacsfiles.0009` (the Phase A `PACSSeries` columns) + `dicomweb.0001`
(`migrations/0002_pacsstudy.py:16-19`). The companion `pacsfiles` migration that
adds `PACSSeries.study` is NOT in this app (it lives in `pacsfiles`) and is
authored by `just makemigrations` in a real checkout (`models.py:152-162`); the
FK is `null=True, on_delete=SET_NULL` so the migration is non-breaking and a
series can exist before its study is resolved (`models.py:153-167`).

`0003` matters for fuzzy matching — see §4.5. `CREATE EXTENSION` needs a
superuser DB role; the CUBE dev/test Postgres is one
(`migrations/0003_pg_trgm.py:3-5`).

---

## 2. The DICOM JSON Model encoder (`dicomjson.py`) — framework-free

This module has **no Django/DRF imports** so it is unit-testable in isolation
(`dicomjson.py:32-33`, tested by `tests/test_dicomjson.py`). It implements
PS3.18 Annex F (`dicomjson.py:1-7`).

### 2.1 Encoding rules implemented (`dicomjson.py:8-31`)

- A dataset is a JSON object keyed by the attribute's **8-char uppercase hex
  tag** (`(0020,000D) → "0020000D"`; no comma, no `0x`).
- Each value is `{"vr": <2-letter VR>, ...}` with **at most one** of `"Value"` /
  `"BulkDataURI"` / `"InlineBinary"`. An empty attribute is `{"vr": "PN"}` with
  none of the three (`dicomjson.py:11-15`).
- Multiple datasets → a top-level JSON **array** (`dicomjson.py:16`).

### 2.2 `normalize_tag` (`dicomjson.py:49-64`)

Accepts `"0020000D"`, `"0020,000D"`, `"(0020,000D)"`, lower-case, or an int
(`0x0020000D` → `"0020000D"`). Strips parens/commas/spaces, requires exactly 8
hex chars, else raises `ValueError` — which callers turn into a QIDO **400**.

### 2.3 VR-specific value coercion (`_coerce_scalar`, `dicomjson.py:112-133`)

| VR class | VRs | JSON form |
|---|---|---|
| Person Name | `PN` | `Value` is an array of **objects**: `[{"Alphabetic": "DOE^JANE"}]` |
| Integer | `IS, US, SS, UL, SL, AT` | JSON integer (`dicomjson.py:38`) |
| Float | `DS, FL, FD` | JSON number (`dicomjson.py:39`) |
| Date | `DA` | DICOM **string** `"20230102"`, NOT ISO (`dicomjson.py:86-93`) |
| Time | `TM` | DICOM `"HHMMSS"`/`"HHMMSS.FFFFFF"` if microseconds present (`dicomjson.py:95-102`) |
| Datetime | `DT` | `"YYYYMMDDHHMMSS"` (`dicomjson.py:105-109`) |
| String | `UI, SH, LO, CS, UR, ST, LT, UT, AE, AS, ...` | plain string in `Value` |

**PN encoding** (`encode_pn`, `dicomjson.py:67-83`): a DICOM PN has up to three
`=`-separated component groups — `Alphabetic` / `Ideographic` / `Phonetic`. The
`^` separators *inside* a group (`family^given^middle^prefix^suffix`) are
preserved verbatim. So `"DOE^JANE=..."` → `{"Alphabetic": "DOE^JANE", ...}`,
emitting only non-empty groups.

### 2.4 Empty-value omission + bulk data

- `element(vr, value)` (`dicomjson.py:136-180`): normalizes scalar/list/None to a
  list of non-empty coerced scalars; if nothing remains it returns `None`, and
  `dataset()` then **omits** the tag entirely (`dicomjson.py:178-180`,
  `dicomjson.py:200-206`). PS3.18 F.2.5 permits omission of zero-length attributes
  (`serializers.py:28-30`).
- **Bulk VRs** `OB, OW, OF, OD, OL, OV, UN` are never inlined here — a bare bulk
  VR is emitted empty as `{"vr": vr}` (`dicomjson.py:45-46`, `dicomjson.py:155-158`).
- `bulkdata_element(vr, uri)` → `{"vr": vr, "BulkDataURI": uri}` for WADO metadata
  PixelData references (`dicomjson.py:183-185`).
- `SQ`: `sequence(items)` / `element('SQ', items)` wrap a list of already-shaped
  dataset dicts (`dicomjson.py:149-153`, `dicomjson.py:188-190`).

---

## 3. Row → DICOM JSON serializers (`serializers.py`)

These are **not** DRF `ModelSerializer`s — the DICOM JSON Model is too far from a
flat dict, and the `pacsfiles` serializers are collection+json-shaped
(`serializers.py:3-9`). Each `serialize_*` function takes a model row + a
`RetrieveURLBuilder` and returns a dataset dict via `dicomjson.dataset`.

- `serialize_study` (`serializers.py:59-77`): emits Study + Patient tags,
  `ModalitiesInStudy` (via `modalities_list()`), the two `NumberOf*` counters,
  and `RetrieveURL` (0008,1190).
- `serialize_series` (`serializers.py:80-108`): Series tags + `StudyInstanceUID` +
  `NumberOfSeriesRelatedInstances` (0020,1209) from the annotated count + series
  `RetrieveURL`. `num_instances` is passed from `Count('instances')` to avoid an
  N+1 (`serializers.py:84-93`).
- `serialize_instance` (`serializers.py:111-132`): instance geometry + SOP UIDs +
  `StudyInstanceUID`/`SeriesInstanceUID` (so OHIF can build the WADO path) +
  instance `RetrieveURL`.

**`RetrieveURLBuilder`** (`serializers.py:34-56`) centralizes WADO URL
construction so QIDO and the STOW response use identical URLs; it uses
`request.build_absolute_uri` so scheme/host/port follow the request (works behind
the miniChRIS reverse proxy).

**`includefield` is a no-op at this layer** (deliberate, conformant): the
serializers always emit the *full indexed attribute set*, which is a **superset**
of the QIDO required set, and PS3.18 §10.6.3 permits returning more than requested
(`serializers.py:11-26`). The `include_all`/`includefields` args are accepted for
forward-compat but unused. The only thing `includefield` cannot surface is a tag
CUBE does not index at all (`StudyID`, `InstitutionName`) — those need schema
additions (`serializers.py:23-26`).

---

## 4. QIDO-RS: query parser + views

### 4.1 The attribute map (`query_parser.py:54-151`)

An `Attr` dataclass (`query_parser.py:54-70`) binds a canonical 8-hex `tag`, a
DICOM `keyword`, a 2-letter `vr`, an `orm_field` (the Django lookup path relative
to the level's base queryset), and an optional `fuzzy_field`. `orm_field=None`
means the attribute is computed/synthesized (e.g. `RetrieveURL`,
`NumberOfSeriesRelatedInstances`) and **cannot be filtered**, but is still emitted
by the serializer (`query_parser.py:62-65`, `query_parser.py:202-203`).

There is one map per level (`query_parser.py:82-132`). ORM paths assume these base
querysets (`query_parser.py:76-80`):

- **STUDY** → `PACSStudy.objects.filter(pacs=...)`
- **SERIES** → `PACSSeries.objects.filter(pacs=...)`
- **INSTANCE** → `PACSInstance.objects.filter(series__pacs=...)`

Cross-level constraints work via FK joins: an instance query can filter
`series__SeriesInstanceUID` / `series__StudyInstanceUID`
(`query_parser.py:129-130`); a series query can constrain on Study/Patient attrs
(`query_parser.py:112-115`). The full attribute→field table is in `MAPPING.md`.

### 4.2 The matching matrix (`query_parser.py:197-335`)

`parse(level, query_params)` (`query_parser.py:377-435`) walks the query string,
splitting reserved keys (`includefield`, `fuzzymatching`, `limit`, `offset`,
`query_parser.py:154`) from matching attributes, then ANDs each matching term into
a single `Q` (`query_parser.py:430-433`). `_build_term` dispatches on VR + value
shape (`query_parser.py:197-231`):

| Form | Example | VR rule | Behavior | Citation |
|---|---|---|---|---|
| Single value | `?StudyInstanceUID=1.2.3` | any | exact (`Q(field=value)`) | `query_parser.py:334-335` |
| List-of-UID | `?0020000D=1.2.3,4.5.6` | `UI` only, on `,` or `\` | `__in` | `query_parser.py:214-217` |
| Multi-value OR | `?00080060=CT,MR` | non-range VRs | OR over `_build_single` | `query_parser.py:219-225` |
| Range | `?StudyDate=20230101-20231231` | `DA/TM/DT` only | inclusive `__gte`/`__lte` | `query_parser.py:227-229`, `282-295` |
| Open range | `?StudyDate=20230101-` / `-20231231` | `DA/TM/DT` | one-sided | `query_parser.py:289-292` |
| Wildcard | `?PatientName=DOE*` | wildcard VRs only | `__iregex` (`*`→`.*`, `?`→`.`) | `query_parser.py:302-310` |
| Fuzzy PN | `?PatientName=DOE^JANE&fuzzymatching=true` | `PN` | `__trigram_similar` | `query_parser.py:312-318` |
| PN exact | `?PatientName=DOE^JANE` | `PN` | `__iexact` (case-insensitive) | `query_parser.py:319-321` |
| Integer VR | `?SeriesNumber=3` | `IS/US/SS/UL/SL` | `int()` cast, else 400 | `query_parser.py:323-327` |
| Return key | `?00080060` (no `=value`) | any mapped | added to `includefields` | `query_parser.py:421-426` |
| includefield | `?includefield=00081030,Modality` / `=all` | — | adds keyword(s) / `include_all` | `query_parser.py:404-414` |
| limit/offset | `?limit=50&offset=100` | — | pagination | `query_parser.py:396-401` |

VR eligibility is enforced (`query_parser.py:43-47`): wildcard only for
`AE,CS,LO,LT,PN,SH,ST,UC,UR,UT`; range only for `DA,TM,DT`; list-of-UID only for
`UI`. A wildcard on a numeric VR raises `QidoQueryError` → 400
(`query_parser.py:303-305`).

**Spec-permitted ignore:** an unsupported match key (a syntactically valid
tag/keyword unknown at this level) is *ignored* for filtering
(`query_parser.py:427-429`, `resolve_attr` returns `None` at
`query_parser.py:175`). A key that is neither a valid 8-hex tag nor a known
keyword raises `QidoQueryError` → 400 (`query_parser.py:171-174`).

**Empty/universal matching is rejected** (MVP scope): `?Tag=` (present-but-empty)
raises `QidoQueryError` → 400 rather than matching "present with any value"
(`query_parser.py:209-212`, README limitation #4).

### 4.3 HARDENING — DICOM date/time coercion (`_coerce_temporal`)

A latent bug found this session: passing the raw DICOM string straight into
`Q(StudyDate='20230102')` **errors at query time** against a Postgres `DateField`
(it expects ISO `2023-01-02`). `_coerce_temporal` (`query_parser.py:241-279`)
parses DA (`%Y%m%d`), TM (length-dispatched `%H%M%S`/`%H%M`/`%H`, dropping
fractional seconds), and DT to native Python objects for both single values
(`query_parser.py:329-332`) and both range bounds (`query_parser.py:290-292`);
unparseable → clean 400.

### 4.4 Wildcard → regex (`_wildcard_to_like` + `_like_to_iregex`)

`*`→`%`, `?`→`_`, with `%`/`_` escaped (`query_parser.py:178-190`); then the LIKE
pattern is converted to an anchored POSIX regex for Django's `__iregex`
(`query_parser.py:338-361`). `__iregex` is portable (no custom ILIKE lookup) and
correct, but **unanchored substring scans won't use a B-tree index** — at scale,
lean on the pg_trgm GIN index (README limitation #11).

### 4.5 HARDENING — fuzzy matching needs BOTH the extension AND a registered lookup

`?fuzzymatching=true` on a PN attribute emits `Q(PatientName__trigram_similar=...)`
(`query_parser.py:312-318`). This requires **two** things, and *both* were needed
— the extension alone gave `"Unsupported lookup"`:

1. **The pg_trgm extension** (and the GIN index) — `0003_pg_trgm`.
2. **The `TrigramSimilar` lookup registered** on `CharField`/`TextField`. Normally
   provided by putting `django.contrib.postgres` in `INSTALLED_APPS`; we register
   it locally in `apps.ready()` so the app stays self-contained and does not force
   a CUBE-wide `INSTALLED_APPS` change (`apps.py:23-36`). If registration fails it
   logs a warning and `fuzzymatching=true` is simply unavailable
   (`apps.py:32-36`).

### 4.6 QIDO views (`qido_views.py`)

All QIDO endpoints are GET-only, list-shaped, inherit `QidoBaseView`
(`qido_views.py:51-104`). Endpoints (`qido_views.py:9-16`):

- `StudyListView` → `GET /studies` (`qido_views.py:114-136`)
- `StudySeriesListView` → `GET /studies/{study}/series` (`qido_views.py:166-173`)
- `AllSeriesListView` → `GET /series` (cross-study) (`qido_views.py:176-179`)
- `SeriesInstanceListView` → `GET /studies/{study}/series/{series}/instances`
  (`qido_views.py:209-216`)
- `StudyInstanceListView` → `GET /studies/{study}/instances` (`qido_views.py:219-226`)
- `AllInstanceListView` → `GET /instances` (cross-study) (`qido_views.py:229-232`)

Series queries annotate `num_instances=Count('instances')`
(`qido_views.py:153`); instance queries `select_related('series')`
(`qido_views.py:195`). Parent-resource-absent → 404 via `get_object_or_404` on the
study/series (`qido_views.py:170-171`, `212-215`, `222-223`).

**HTTP semantics** (`qido_views.py:17-28`):

- **200 + `[]`** on a valid query that matches nothing. PS3.18 §10.6.3's status
  table does not define 204 for Search, so we **never emit 204**
  (`qido_views.py:85-93`) — what OHIF/dcm4che expect.
- **400** on malformed query: `QidoQueryError` is caught in `parse_query` and
  re-raised as the internal `_BadQuery` marker (`qido_views.py:76-83`), which
  `handle_exception` turns into a 400 with `{"errorMessage": ...}`
  (`qido_views.py:100-108`).
- **413** only when the client asks for `limit >= MAX_LIMIT` (5000) AND the count
  exceeds it (`qido_views.py:126-127`, `235-242`). `MAX_LIMIT` is a paging ceiling
  (`query_parser.py:41`, `400-401`), not a result-set guard, so 413 is rare
  (README limitation #12). It sets a `Warning: 299` header (`qido_views.py:240-241`).
- **404** on unknown PACS / parent (`qido_views.py:69-74`).

### 4.7 QIDO request lifecycle (worked example)

```
GET /dicom-web/pacs/BCH/studies?PatientName=DOE*&StudyDate=20230101-20231231&limit=25
Accept: application/dicom+json
Authorization: Token <...>
```

1. URL resolves to `StudiesRootDispatcher` → `.get()` → `StudyListView.as_view()`
   (`urls.py:56-61`, `81`).
2. Auth (Token) + `IsChrisOrIsPACSUserReadOnly` (read OK for any `pacs_users`
   member) (`qido_views.py:60-63`).
3. `get_pacs()` resolves PACS `BCH` or 404 (`qido_views.py:69-74`).
4. `parse_query()` → `query_parser.parse('study', ...)`: `PatientName=DOE*` →
   `PatientName__iregex='^DOE.*$'`; `StudyDate=...` → `StudyDate__gte=date(2023,1,1)
   & __lte=date(2023,12,31)`; `limit=25`.
5. `PACSStudy.objects.filter(pacs).filter(Q).order_by('-StudyDate',
   'StudyInstanceUID')`, sliced `[offset:offset+limit]` (`qido_views.py:121-128`).
6. Each row → `serialize_study` → `DicomJsonRenderer` → 200 + JSON array.

Example response body (one study):

```json
[
  {
    "00080020": {"vr": "DA", "Value": ["20230102"]},
    "00080050": {"vr": "SH", "Value": ["A12345"]},
    "00080061": {"vr": "CS", "Value": ["CT"]},
    "00081030": {"vr": "LO", "Value": ["CHEST CT"]},
    "00100010": {"vr": "PN", "Value": [{"Alphabetic": "DOE^JANE"}]},
    "00100020": {"vr": "LO", "Value": ["MRN0001"]},
    "00100040": {"vr": "CS", "Value": ["F"]},
    "0020000D": {"vr": "UI", "Value": ["1.2.840.111.1"]},
    "00201206": {"vr": "IS", "Value": [1]},
    "00201208": {"vr": "IS", "Value": [2]},
    "00081190": {"vr": "UR", "Value": ["http://.../dicom-web/pacs/BCH/studies/1.2.840.111.1"]}
  }
]
```

(Shape verified by `tests/test_views.py:103-112`: `0020000D.Value == [study_uid]`,
`00201208.Value == [2]`.)

---

## 5. WADO-RS: retrieve + metadata + frames/bulkdata

> **Note on the file header.** The module docstring still labels frames/bulkdata
> as "STUB (501)" (`wado_views.py:9-11`), and `urls.py` comments echo that
> (`urls.py:25-26`). That header is **stale** — the code below it (and the README,
> `README.md:95-97`, `185-191`) implements **native** frames/bulkdata; only
> compressed/encapsulated returns 501. Trust the code, not the header.

### 5.1 Object retrieval — `multipart/related; type="application/dicom"`

`_RetrieveBase` (`wado_views.py:81-171`) streams stored `.dcm` bytes from
`core.storage.connect_storage(settings)` into a `multipart/related` body, one part
per instance, `Content-Type: application/dicom`. Bytes resolve via
`PACSInstance.pacs_file.fname.name` → `storage.download_obj(fname)`
(`wado_views.py:149-152`). **No transcoding** — the stored Transfer Syntax is
returned as-is (`wado_views.py:14-17`).

Views:
- `RetrieveStudyView` → `GET studies/{study}`, ordered by
  `series__SeriesNumber, InstanceNumber` (`wado_views.py:174-183`).
- `RetrieveSeriesView` → `GET studies/{study}/series/{series}` (`wado_views.py:186-195`).
- `RetrieveInstanceView` → `GET .../instances/{sop}` (`wado_views.py:198-206`).

Wire format (`_iter_multipart`, `wado_views.py:146-171`): for each instance a part
header with `Content-Type`, `Content-Location` (the instance WADO URL),
`Content-Length`, then the raw bytes, then CRLF; a closing `--boundary--`. A
storage miss for one part is **logged and skipped**, not fatal
(`wado_views.py:153-156`). The response is a `StreamingHttpResponse` with a
per-response random `boundary` set on the Content-Type
(`wado_views.py:135-144`). Empty instance set → 404 (`wado_views.py:120-122`).

**Accept negotiation** (`_accept_ok`, `wado_views.py:88-109`): `*/*` or empty → OK;
must contain `multipart/related`; `transfer-syntax=` must be `*`/empty/Explicit VR
Little Endian (`1.2.840.10008.1.2.1`, `wado_views.py:59`), otherwise the requested
TS is returned to the caller, which then checks **per-instance** that every stored
`TransferSyntaxUID` already matches — any mismatch → **406** (we don't transcode)
(`wado_views.py:126-133`). Each part's `Content-Type` is bare
`application/dicom` **without** an explicit `transfer-syntax=` MIME parameter;
OHIF/dcm4che tolerate its absence (README limitation #2).

### 5.2 Metadata — `application/dicom+json`

`_MetadataBase` (`wado_views.py:212-244`) builds metadata from the **index**
(PACSStudy/Series/Instance columns) — **no file re-read** for the indexed
attributes (`wado_views.py:19-22`). `PixelData (7FE00010)` is emitted as a
`BulkDataURI` pointing at the instance's `/frames/1` URL (so OHIF fetches pixels
lazily), and `AvailableTransferSyntaxUID (0008,3002)` is added when known
(`wado_views.py:226-244`). Views: `StudyMetadataView`, `SeriesMetadataView`,
`InstanceMetadataView` (`wado_views.py:247-280`). Verified by
`tests/test_views.py:194-204` (`7FE00010` present with `BulkDataURI`).

### 5.3 Native frames + bulkdata (implemented) and the 501 boundary

`_PixelBaseView` (`wado_views.py:286-326`) reads the full dataset via pydicom
(`_read_dataset`, `wado_views.py:298-301`) and detects encapsulation from
`file_meta.TransferSyntaxUID.is_encapsulated` (`wado_views.py:303-306`).

**`FramesView`** → `GET .../instances/{sop}/frames/{frameList}`
(`wado_views.py:329-382`):
- Parses the 1-based comma frame list (bad/empty → 400, `wado_views.py:343-350`).
- **Encapsulated/compressed → 501** (`wado_views.py:358-364`): splitting
  encapsulated fragments / transcoding needs `pylibjpeg`/`gdcm`, out of scope.
- For **native** syntaxes it slices `PixelData`:
  `frame_size = Rows × Columns × SamplesPerPixel × ceil(BitsAllocated/8)`
  (`wado_views.py:369-373`), slices `px[(n-1)*frame_size : n*frame_size]`
  (`wado_views.py:375-381`), out-of-range frame → 404 (`wado_views.py:377-380`).
- Returns `multipart/related; type="application/octet-stream"; transfer-syntax=<ts>`
  (`_multipart_octets`, `wado_views.py:308-326`).

**`BulkdataView`** → `GET .../instances/{sop}/bulkdata` (`wado_views.py:385-412`):
same encapsulation gate (501), else returns the whole `PixelData` as one octet
part. No PixelData → 404 (`wado_views.py:365-367`, `408-410`).

Verified live against an oxidicom-ingested MR; tested by
`tests/test_views.py:240-266` (native frame octet-stream, out-of-range 404,
bulkdata octet-stream). **Still 501 / not implemented:** compressed/encapsulated
frames + `/rendered` + `/thumbnail` (README limitation #1).

---

## 6. STOW-RS: store (`stow_views.py` + `multipart.py`)

### 6.1 The multipart/related parser (`multipart.py`) — framework-free

STOW bodies are RFC 2387 **`multipart/related`** (NOT `multipart/form-data`), so
Django's `request.FILES` / DRF's `MultiPartParser` do not apply
(`multipart.py:1-14`). `parse_multipart_related(body, content_type)`
(`multipart.py:67-103`):
- Extracts the boundary, tolerating a quoted `boundary="..."` (`_extract_boundary`,
  `multipart.py:51-64`).
- Splits on `--<boundary>`; ignores preamble (first chunk) and stops at the
  closing `--boundary--` (`multipart.py:81-87`).
- Strips the **single** leading and trailing CRLF/LF around each part so the DICOM
  bytes are exact (`multipart.py:89-98`), tolerating CRLF or bare-LF.
- Splits headers at the first blank line (`_split_headers`, `multipart.py:106-115`)
  and returns `Part(content_type, headers, content)`.

Tested for binary-with-embedded-CRLF round-trip (`tests/test_multipart.py`,
README table).

**`RawPassthroughParser`** (`multipart.py:25-37`): a DRF parser with
`media_type = '*/*'` that returns `stream.read()`, so content negotiation always
selects it and the STOW view reads `request.body` itself
(`stow_views.py:88-90`).

### 6.2 The store flow (`StowView.post`, `stow_views.py:97-172`)

1. Resolve PACS or 404 (`stow_views.py:93-95`); `url_study_uid` is `None` for
   `POST /studies` (`stow_views.py:99`).
2. **415** if `Content-Type` is not `multipart/related` (`stow_views.py:101-106`).
3. Parse the body; `MultipartError` → **400** (`stow_views.py:108-112`); no parts →
   400 (`stow_views.py:114-116`).
4. For each part:
   - Non-`application/dicom` part → `FailureReason 0xC000` (cannot understand)
     (`stow_views.py:127-129`).
   - pydicom parse failure → `0xC000` (`stow_views.py:130-136`).
   - Missing SOPInstanceUID / StudyInstanceUID / SeriesInstanceUID →
     `0xA700` (processing) (`stow_views.py:143-146`).
   - `POST /studies/{study}` with a part from a different study →
     `0xA901` (study mismatch) (`stow_views.py:148-152`).
   - Otherwise `_store_one` inside `transaction.atomic()`; on success append to
     `ReferencedSOPSequence`, on exception `0xA700`
     (`stow_views.py:154-169`).

### 6.3 `_store_one` — persist one object (`stow_views.py:177-259`)

- **find-or-create `PACSStudy`** with denormalized Patient/Study defaults from
  `study_defaults(ds)` (shared with the indexer, §7.4) (`stow_views.py:188-190`).
- **find-or-create `PACSSeries`** + its `ChrisFolder` at
  `SERVICES/PACS/<id>/<study>/<series>` (`stow_views.py:192-224`). The
  `series.study` FK is set only when the column exists (`hasattr(series,
  'study_id')`) so the module also imports against the Phase A schema
  (`stow_views.py:216-221`).
- **write the `.dcm`**: `storage.upload_obj(fname, raw_bytes,
  content_type='application/dicom')` at
  `<study>/<series>/<sop>.dcm` (`stow_views.py:225-227`).
- **create `PACSFile`** (sets `fname.name`, `fsize`) (`stow_views.py:229-233`).
- **upsert `PACSInstance`** via `update_or_create`, reading TransferSyntaxUID from
  `ds.file_meta` (`stow_views.py:235-255`).
- **`refresh_study_rollups(study)`** (§7.4) (`stow_views.py:257-258`).

### 6.4 The Store Instances Response (`_build_response`, `stow_views.py:288-308`)

A **single DICOM JSON object** (NOT an array) (`stow_views.py:16-21`):
- `(0008,1190) RetrieveURL` (study) when ≥1 stored.
- `(0008,1198) FailedSOPSequence` of `{ReferencedSOPClassUID,
  ReferencedSOPInstanceUID, FailureReason (US)}` (`_fail_item`,
  `stow_views.py:281-286`).
- `(0008,1199) ReferencedSOPSequence` of `{ReferencedSOPClassUID,
  ReferencedSOPInstanceUID, RetrieveURL}` (`_ref_item`, `stow_views.py:274-279`).

Status codes (`stow_views.py:299-307`): all stored → **200**; mixed → **202**;
nothing stored → **409** (well-formed but no instance stored); bad syntax → **400**
(earlier); bad media type → **415**. **Failure reason codes are emitted as
DECIMAL** in the body: `0xA700`→42752, `0xC000`→49152, `0xA901`→43265
(`stow_views.py:30-36`, `69-72`).

### 6.5 STOW lifecycle (worked example — partial store → 202)

```
POST /dicom-web/pacs/BCH/studies/5.5.5
Content-Type: multipart/related; type="application/dicom"; boundary=DICOMWEBBOUNDARY
Authorization: Token <chris-token>
<part1: study 5.5.5>  <part2: study 6.6.6>
```

Part 1 stored; part 2 rejected `0xA901` (mismatch) → **202** with both
`00081199` and `00081198` (`tests/test_views.py:329-341`). Example body:

```json
{
  "00081190": {"vr": "UR", "Value": ["http://.../dicom-web/pacs/BCH/studies/5.5.5"]},
  "00081199": {"vr": "SQ", "Value": [
    {"00081150": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.1.1.2"]},
     "00081155": {"vr": "UI", "Value": ["<sop1>"]},
     "00081190": {"vr": "UR", "Value": ["http://.../instances/<sop1>"]}}
  ]},
  "00081198": {"vr": "SQ", "Value": [
    {"00081150": {"vr": "UI", "Value": ["1.2.840.10008.5.1.4.1.1.2"]},
     "00081155": {"vr": "UI", "Value": ["<sop2>"]},
     "00081197": {"vr": "US", "Value": [43265]}}
  ]}
}
```

A curl single-instance store:

```bash
curl -X POST 'http://localhost:8000/dicom-web/pacs/BCH/studies' \
  -H 'Authorization: Token <chris-token>' \
  -H 'Accept: application/dicom+json' \
  -H 'Content-Type: multipart/related; type="application/dicom"; boundary=B' \
  --data-binary @stow_body.bin
```

---

## 7. The TWO indexing paths (and when each runs)

This is the single most likely curve-ball cluster. There are two distinct ways a
`PACSInstance` + `PACSStudy` row gets created.

### 7.1 Path A — `index_pacs_instance`: RE-READS the `.dcm` with pydicom

`@shared_task` (`tasks.py:175-262`). Given a `pacs_file_id`, it:
1. Loads the `PACSFile`; skips non-`.dcm` (`tasks.py:194-197`).
2. Walks parent folders up to 16 levels to find the owning `PACSSeries`
   (`_find_series_for_file`, `tasks.py:18-36`) — oxidicom nests files under
   intermediate folders, so the immediate `parent_folder` isn't always the series.
3. **Downloads the bytes** from storage and **parses with pydicom**
   (`stop_before_pixels=True`) (`tasks.py:205-218`). Storage read failure →
   `self.retry` (up to 3, `tasks.py:175`, `208-211`); parse failure → no retry
   (`tasks.py:216-218`).
4. `update_or_create` the `PACSInstance` (`tasks.py:233-246`).
5. `_backfill_series_tags` — fills only **empty/null** QIDO columns on
   `PACSSeries`, never overwriting existing values (`tasks.py:248`, `265-304`).
6. **HARDENING — upsert `PACSStudy` + roll-ups** (`tasks.py:250-262`): see §7.5.

**When it runs:**
- **Real-time, on every newly-created `.dcm`** via the `post_save(PACSFile)`
  signal (`signals.py:28-37`) — see §7.3.
- **Backfill** via `reindex_pacs_instances` (§7.6).

### 7.2 Path B — `index_from_metadata`: NO storage read, NO pydicom (Variant C)

`index_from_metadata(meta)` (`tasks.py:123-172`) indexes from a **tags dict**
delivered as a message. The only DB reads are FK lookups (PACS / PACSSeries /
PACSFile); **there is no storage download and no pydicom parse**
(`tasks.py:129-135`). It `update_or_create`s `PACSInstance`, `get_or_create`s
`PACSStudy` from `_study_defaults_from_meta(meta)` (`tasks.py:107-120`,
`165-167`), links `series.study` when the FK exists, and refreshes roll-ups, all
in one `transaction.atomic()` (`tasks.py:151-172`). Returns `False` (skip) if the
series/file isn't registered yet — a production consumer would NAK/retry
(`tasks.py:137-149`).

**When it runs:** fed by `consume_dicomweb_index`, a NATS subscriber
(`management/commands/consume_dicomweb_index.py`). It subscribes to subject
`oxidicom-meta.>` by default (the per-instance subject is
`oxidicom-meta.<pacs>.<series>`) and calls `index_from_metadata` per message via
`sync_to_async` (`consume_dicomweb_index.py:49-79`). The payload schema (the new
tag-bearing event an *extended* oxidicom would publish, in addition to its
progress-only LONK subject) is documented at
`consume_dicomweb_index.py:13-23`.

**This is decision D1 (Variant C).** Rather than CUBE re-parsing every file,
oxidicom publishes the tags it *already parsed in Rust during C-STORE* and this
in-network consumer upserts the index directly (`tasks.py:123-135`,
`consume_dicomweb_index.py:1-9`). **Proven live this session:** publishing a
parsed-tags event for a PACS whose `.dcm` does not exist in storage still produced
a complete QIDO `/studies` result — so it came from the message, not a re-read
(`README.md:67-70`).

### 7.3 The real-time signal (`signals.py`)

oxidicom registers `PACSFile` rows via its own NATS → Celery `register_pacs_series`
path, **NOT** through the REST `PACSSeriesSerializer` Phase A hooked
(`signals.py:1-7`). The gap is closed by a `post_save(PACSFile)` receiver
(`signals.py:28-37`): on `created` + `.dcm`, it queues `index_pacs_instance.delay(pk)`
wrapped in `transaction.on_commit` so the worker runs only once the row is visible
**and** it is a no-op inside atomic `TestCase`s (callbacks don't fire there),
keeping unit tests broker-free (`signals.py:8-13`, `34-37`). Registered in
`apps.ready()` (`apps.py:18-21`). The task routes to Celery queue `main2`
(`urls.py:146-147`).

### 7.4 Shared helpers (single source of truth)

`study_defaults(ds)` (`tasks.py:74-90`) and `refresh_study_rollups(study)`
(`tasks.py:93-104`) live in `tasks.py` and are imported by **both** the indexer
and the STOW view (`stow_views.py:263-269`) so the two ingest paths stay
consistent. `refresh_study_rollups` recomputes `ModalitiesInStudy` (sorted,
backslash-joined), `NumberOfStudyRelatedSeries`, and
`NumberOfStudyRelatedInstances` from the series set (`tasks.py:95-104`).
`_study_defaults_from_meta(meta)` (`tasks.py:107-120`) is the message-driven
analogue for Variant C.

### 7.5 HARDENING — the indexer didn't populate `PACSStudy`

Found by running in real CUBE: the original async indexer indexed `PACSInstance`
but **not** `PACSStudy`, so QIDO `/studies` was blind to oxidicom-ingested data
(`/studies` reads `PACSStudy`, but only STOW created those rows). Fixed at
`tasks.py:250-262`: the async indexer now `get_or_create`s the `PACSStudy`, links
`series.study`, and refreshes roll-ups — mirroring what STOW does inline. The code
comment documents exactly this (`tasks.py:250-252`).

### 7.6 Backfill command (`reindex_pacs_instances`)

`reindex_pacs_instances` (`management/commands/reindex_pacs_instances.py`)
backfills pre-existing PACS data after deploying the app onto a CUBE with existing
rows. It filters `PACSFile.get_base_queryset().filter(fname__endswith='.dcm')`,
optionally by `--pacs`/`--series`, and dispatches `index_pacs_instance` per file
(`.delay` by default, or `--sync` in-process; `--dry-run` counts only)
(`reindex_pacs_instances.py:38-62`). Idempotent (the task uses
`update_or_create`). **HARDENING:** the task is imported *inside* `handle()` to
avoid an app-load import cycle (`reindex_pacs_instances.py:39`).

---

## 8. URL wiring, the dispatcher, and the CSRF subtlety

### 8.1 Routing (`urls.py:79-120`)

Mounted at `/dicom-web/pacs/<str:pacs_identifier>/` from `config/urls.py`
(`urls.py:127-138`). UID path segments use `_UID = r'[0-9A-Za-z.\-]+'`
(dotted/alphanumeric, never crossing `/`) (`urls.py:49`). More-specific
`studies/<study>/...` patterns are registered **before** the bare
`studies/<study>` so resolution is unambiguous (`urls.py:34-36`, `86-119`).

### 8.2 The two dual-method paths need dispatcher Views

`/studies` and `/studies/<study>` carry **both** a GET (QIDO/WADO) and a POST
(STOW). Django binds one path to one view, so two thin plain-Django `View`
dispatchers route by method (`urls.py:28-30`, `55-77`):

- `StudiesRootDispatcher`: GET → `qido_views.StudyListView`, POST →
  `stow_views.StowView` (`urls.py:56-64`).
- `StudyDispatcher`: GET → `wado_views.RetrieveStudyView`, POST →
  `stow_views.StowView` (`urls.py:68-76`).

### 8.3 HARDENING — `@csrf_exempt` must be on the OUTER dispatcher

This is the subtlest fix and a likely curve-ball. **DRF's per-view `csrf_exempt`
does not reach the inner `as_view()` through the outer plain `View`.** So STOW
POST returned **403** until the *dispatcher itself* was exempted with
`@method_decorator(csrf_exempt, name='dispatch')` (`urls.py:52-55`, `55`, `67`).
The inner DRF views still authenticate (Token/Basic) and never rely on CSRF
(`urls.py:52-54`). Plain QIDO/WADO views (`/series`, `/instances`, the scoped
`re_path`s) are mounted directly, not through a dispatcher, so they don't need
this.

---

## 9. Renderers + content negotiation (`renderers.py`)

The views hand the renderers data **already** in DICOM JSON Model shape, so the
renderers only serialize (`renderers.py:6-8`):

- **`DicomJsonRenderer`** → `application/dicom+json`, UTF-8, `json.dumps(...,
  ensure_ascii=False)` to keep names intact (ISO_IR 192) (`renderers.py:29-39`).
- **`DicomJsonAsJsonRenderer`** → identical bytes under `application/json`,
  because QIDO requires `Accept: application/json` be treated as equivalent
  (PS3.18 §10.6.2) — registering it avoids a 406 for OHIF
  (`renderers.py:42-51`). Verified by `tests/test_views.py:139-142`.
- **`MultipartRelatedRenderer`** → passthrough for WADO retrieval; `media_type =
  'multipart/related'` (wildcard subtype) so DRF negotiation matches any
  `multipart/related; ...` Accept, but the **view** sets the real Content-Type
  with the concrete `boundary`/`type=`/`transfer-syntax=` (`renderers.py:54-77`).

---

## 10. Auth and permissions (across all views)

Every view uses the same chain (`qido_views.py:60-63`, `wado_views.py:64-67`,
`stow_views.py:84-91`):

- `authentication_classes = (TokenAuthentication, BasicAuthentication,
  SessionAuthentication)` — LDAP is wired behind Token/Basic via CUBE's
  `users.models.CustomLDAPBackend` (`qido_views.py:54-57`).
- `permission_classes = (IsAuthenticated, IsChrisOrIsPACSUserReadOnly)`.

`IsChrisOrIsPACSUserReadOnly` makes everything-but-GET **`chris`-only**, so the
read surface (QIDO/WADO) is open to any `pacs_users` member and the one write
surface (**STOW**) is `chris`-only — STOW inherits exactly CUBE's existing
PACS-write policy (`stow_views.py:76-81`). If grant policy later opens STOW to all
`pacs_users`, swap in `IsChrisOrIsPACSUserOrReadOnly` (`stow_views.py:80-81`).
Unauthenticated → 401/403 (`tests/test_views.py:97-101`).

---

## 11. Tests (`tests/`)

97/97 passing in real CUBE this session. The framework-free core
(`test_dicomjson`, `test_query_parser`, `test_multipart`, `test_serializers`) runs
standalone with no DB/storage; the HTTP/DB tests (`test_views.py`) need a CUBE
checkout (`README.md:163-174`).

- `test_views.py` builds a Patient→Study→Series→Instance tree for PACS `BCH`
  (`tests/test_views.py:34-89`); QIDO/WADO-metadata cases need DB only, WADO-retrieve
  + STOW are `@tag('integration')` (storage round-trip)
  (`tests/test_views.py:217-219`, `288-293`).
- Fixtures build synthetic DICOM with pydicom (`tests/fixtures.py`); no external
  sample data.

**HARDENING — test fixture `chris` collision.** CUBE seeds a `chris` user via a
data migration, so the suite uses `User.objects.get_or_create(username='chris')`
(not `create_user`, which hit a unique-username `IntegrityError` when run inside a
real CUBE), and `force_authenticate` so no password is needed
(`tests/test_views.py:40-48`).

**HARDENING — pydicom 3.x writer idiom.** `dataset_to_bytes` uses
`dcmwrite(..., enforce_file_format=True)` and derives encoding from
`file_meta.TransferSyntaxUID`, instead of the `is_little_endian` /
`is_implicit_VR` / `write_like_original` knobs deprecated in 3.0 / removed in 4.0
(`tests/fixtures.py:78-95`, README #6).

---

## 12. Honest limitations (state these in the room)

From `README.md:178-269`, with citations:

1. **Frames/bulkdata: native only.** Compressed/encapsulated + `/rendered` +
   `/thumbnail` → 501 (transcoding needs pylibjpeg/gdcm) (`wado_views.py:358-364`).
2. **No transcoding in WADO retrieve.** Stored TS only; specific different TS →
   406. Part Content-Type omits the `transfer-syntax=` MIME param
   (`wado_views.py:126-133`, `161-164`).
3. **`includefield` is a no-op at the response layer** — we always emit the full
   indexed set (a conformant superset); only un-indexed tags (`StudyID`,
   `InstitutionName`) can't be surfaced (`serializers.py:11-26`).
4. **Empty/universal matching (`?Tag=`) rejected → 400** (`query_parser.py:209-212`).
5. **STOW rejects objects with no `StudyDate`.** Upstream `PACSSeries.StudyDate` is
   `DateField(db_index=True)` NOT NULL; an object lacking StudyDate (some SRs)
   fails the constraint and lands in `FailedSOPSequence` (`0xA700`). Fixing it
   needs a `pacsfiles` migration (`stow_views.py:204-205`, README #8).
6. **STOW PACSFile/ChrisFolder creation is simplified** vs the real ingest path
   (`PACSSeriesSerializer.create` does wait-for-files + permission grants); a
   production STOW should share that logic (`stow_views.py:229-233`, README #9).
7. **Roll-ups refreshed at STOW time only here**; the oxidicom/serializer path must
   also find-or-create + refresh for non-STOW ingestion — partly addressed by the
   §7.5 indexer fix (README #10).
8. **Wildcards use `__iregex`** (correct but no B-tree index); fuzzy needs pg_trgm
   (`query_parser.py:302-318`, README #11).
9. **413 is rare** — only when `limit >= MAX_LIMIT` (`qido_views.py:126-127`).
10. **No conformance/capabilities document** (PS3.18 §8.9); OHIF tolerates its
    absence (README #14).
11. **drf-spectacular**: exclude these non-collection+json views before
    regenerating the OpenAPI dump (`urls.py:141-144`, README #15).

---

## 13. Curve-ball Q&A (BCH meeting prep)

**Q: Does QIDO re-read every file on each query?**
No. QIDO reads the **index** (`PACSStudy`/`PACSSeries`/`PACSInstance` columns) —
pure ORM, no storage I/O (`qido_views.py:121-128`). The only place we ever
re-read a `.dcm` at request time is WADO `/frames` and `/bulkdata` (which must
touch real pixels) (`wado_views.py:298-301`). WADO `/metadata` is also
index-only (`wado_views.py:19-22`).

**Q: How does oxidicom-ingested data reach QIDO? oxidicom doesn't call your REST
serializer.**
Right — it registers `PACSFile` via its own NATS→Celery path. We hook
`post_save(PACSFile)` (`signals.py:28-37`), which queues `index_pacs_instance`
(re-read path) on commit. We found and fixed a bug here: the indexer originally
created `PACSInstance` but not `PACSStudy`, so `/studies` was blind to oxidicom
data — now fixed (`tasks.py:250-262`).

**Q: You re-parse files oxidicom already parsed in Rust. Isn't that wasteful?**
Yes, and that's exactly why **Variant C** exists. `index_from_metadata` indexes
from a tags message with no storage read and no pydicom (`tasks.py:123-172`); the
`consume_dicomweb_index` NATS subscriber feeds it (`consume_dicomweb_index.py`).
We proved it live: indexed a study whose file doesn't exist in storage. The one
new piece oxidicom needs is to publish a tag-bearing event (its current LONK
subject carries progress only) (`README.md:61-70`).

**Q: Why a `PACSStudy` table instead of `GROUP BY` over `PACSSeries`?**
D2 (`08-l2-architecture-decisions.md`). An explicit Study row is the recommended
choice at grant scale — denormalized counters maintained at ingest beat a per-
request `GROUP BY` (`models.py:38-45`, `MAPPING.md:50-54`). Patient stays implicit
on `PACSStudy` (`models.py:24-28`).

**Q: STOW POST kept returning 403 — what was that?**
CSRF. The dual-method paths use a plain-Django `View` dispatcher, and DRF's
inner `csrf_exempt` doesn't propagate through it; we had to exempt the **outer
dispatcher** (`@method_decorator(csrf_exempt, name='dispatch')`)
(`urls.py:52-55`). Token/Basic auth still applies.

**Q: `fuzzymatching=true` — does just `CREATE EXTENSION pg_trgm` make it work?**
No — that was the gotcha. You need **both** the extension/GIN index (`0003`) **and**
the `TrigramSimilar` lookup registered on `CharField`/`TextField`; the extension
alone gives `"Unsupported lookup"`. We register the lookup in `apps.ready()`
(`apps.py:23-36`) so the app is self-contained.

**Q: A DA query like `?StudyDate=20230102` — does that just work?**
Only because we fixed it. The raw DICOM string errors against a Postgres
`DateField` (expects ISO); `_coerce_temporal` parses DA/TM/DT to native
`date`/`time` first (`query_parser.py:241-279`, `329-332`). Unparseable → 400.

**Q: What does WADO return for a JPEG2000-compressed series?**
Object retrieval streams the stored compressed bytes as-is (no transcoding); a
request for a *different* specific transfer-syntax → 406 (`wado_views.py:126-133`).
For pixel-level `/frames` or `/bulkdata` on an **encapsulated/compressed**
instance we return **501** — splitting encapsulated fragments needs
pylibjpeg/gdcm (`wado_views.py:358-364`, `402-407`). Native syntaxes are sliced
and returned as octet-stream (`wado_views.py:369-382`).

**Q: STOW status codes — when 200 vs 202 vs 409?**
200 = all parts stored; 202 = some stored, some failed (inspect
`FailedSOPSequence`); 409 = well-formed request but nothing stored (e.g. every
part is a study mismatch); 400 = malformed body; 415 = wrong media type
(`stow_views.py:299-307`, `22-29`). Failure reasons are decimal in the body:
study mismatch = 43265 (`0xA901`) (`tests/test_views.py:326-327`).

**Q: What's the empty-result behavior — 204 or 200?**
Always **200 + `[]`**. PS3.18 §10.6.3's Search status table doesn't define 204, so
we never emit it; OHIF/dcm4che expect `[]` (`qido_views.py:85-93`,
`tests/test_views.py:114-118`).

**Q: Does this fork miniChRIS or upstream CUBE?**
Neither is forked. The app is a drop-in for `chris_backend/dicomweb/`, and the
deploy overlay `docker cp`s it into the running container and **wraps** the
miniChRIS compose stack rather than forking it (`README.md:6-13`).

---

## 14. Quick reference — endpoint → handler

| Method + path (under `/dicom-web/pacs/<id>/`) | Handler | Citation |
|---|---|---|
| GET `studies` | `StudyListView` (via `StudiesRootDispatcher`) | `urls.py:81`, `qido_views.py:114` |
| POST `studies` | `StowView` (via `StudiesRootDispatcher`) | `urls.py:81`, `stow_views.py:75` |
| GET `studies/{s}/series` | `StudySeriesListView` | `urls.py:112-113` |
| GET `studies/{s}/series/{se}/instances` | `SeriesInstanceListView` | `urls.py:101-104` |
| GET `studies/{s}/instances` | `StudyInstanceListView` | `urls.py:114-115` |
| GET `series` | `AllSeriesListView` | `urls.py:82` |
| GET `instances` | `AllInstanceListView` | `urls.py:83-84` |
| GET `studies/{s}` | `RetrieveStudyView` (via `StudyDispatcher`) | `urls.py:118-119`, `wado_views.py:174` |
| POST `studies/{s}` | `StowView` (via `StudyDispatcher`) | `urls.py:118-119`, `stow_views.py:75` |
| GET `studies/{s}/series/{se}` | `RetrieveSeriesView` | `urls.py:108-109` |
| GET `studies/{s}/series/{se}/instances/{sop}` | `RetrieveInstanceView` | `urls.py:96-98` |
| GET `.../metadata` (study/series/instance) | `*MetadataView` | `urls.py:87-117` |
| GET `.../instances/{sop}/frames/{list}` | `FramesView` | `urls.py:90-92`, `wado_views.py:329` |
| GET `.../instances/{sop}/bulkdata` | `BulkdataView` | `urls.py:93-95`, `wado_views.py:385` |

---

## 15. Sources

- Code: `implementation/dicomweb-l2/` (every file cited above).
- `implementation/dicomweb-l2/MAPPING.md` (attribute→field map), `README.md`
  (status, limitations, run instructions).
- The standard: `knowledge-base/05-dicomweb-qido-wado-stow.md`.
- Decisions (D1 variants, D2 PACSStudy, D4 pg_trgm): `knowledge-base/08-l2-architecture-decisions.md`.
- `proposal-to-bch/QIDO_PLAN.md`, `proposal-to-bch/PHASE_A_IMPLEMENTATION.md`,
  `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`.
- DICOM PS3.18 (QIDO §10.6, WADO §10.4, STOW §10.5, JSON Model Annex F), PS3.4
  matching (§C.2.2.2.4).
