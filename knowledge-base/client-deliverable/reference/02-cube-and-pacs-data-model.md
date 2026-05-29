# CUBE Internals & the PACS / DICOM Data Model

> Bridge document between DICOM concepts (Patient / Study / Series / Instance) and the actual
> Django code in `ChRIS_ultron_backEnd` (CUBE). Use this to ground every DICOMweb conversation
> tomorrow: when someone says "where does the StudyInstanceUID live?", this file has the model,
> the field, the index, and the gap.

**Audience:** an engineer new to this stack.
**Scope:** CUBE's REST API style + auth, the `pacsfiles` app (`PACS` / `PACSFile` / `PACSSeries`),
how `oxidicom` ingests DICOM into the PACS file tree, and the Phase A additions in this repo
(the `dicomweb` app, `PACSInstance`, the `index_pacs_instance` Celery task).

Sources are cited inline. The two load-bearing local artifacts are
`proposal-to-bch/CURRENT_API.md` (API + gap analysis) and `proposal-to-bch/schema.yaml` (live
OpenAPI 3.0.3 dump). Upstream code references are to `github.com/FNNDSC/ChRIS_ultron_backEnd`
(CUBE) and `github.com/FNNDSC/oxidicom`.

---

## 1. What CUBE is, in one paragraph

CUBE (ChRIS Ultron BackEnd) is the **Django + Django REST Framework** backend of the ChRIS
medical-imaging platform (FNNDSC / Boston Children's Hospital). It models analyses as *Feeds*
(trees of plugin instances), brokers compute to remote sites via `pfcon`/`pman`, and stores all
data in a single virtual filesystem ("the unified storage tree") abstracted over swift / S3 /
local-fslink backends. DICOM data is one citizen of that tree, living under `SERVICES/PACS/`. The
DICOMweb work adds a standards-compliant query/retrieve/store surface (QIDO-RS / WADO-RS / STOW-RS)
on top of that existing PACS storage. Architecture overview: <https://chrisproject.org/docs/architecture>.

```
                         ChRIS platform (high level)
 ┌───────────┐   collection+json    ┌──────────────────────────────────────────┐
 │  ChRIS_ui │◀────────REST────────▶│                  CUBE                      │
 │  / OHIF / │   (Django + DRF)     │  feeds · plugins · pipelines · pacsfiles   │
 │  3D Slicer│                      │  ─ Postgres (metadata)                     │
 └───────────┘                      │  ─ storage tree  (swift / S3 / fslink)     │
       ▲                            │  ─ Celery workers (main1 / main2 / period.)│
       │ DICOMweb endpoints         └──────┬───────────────────────┬─────────────┘
       │ (QIDO/WADO/STOW —                 │ NATS (LONK)            │ Celery task
       │  views not built yet)      ┌──────┴──────┐          ┌──────┴───────┐
       └────────────────────────────│  oxidicom   │          │   pfdcm      │
        (the DICOMweb arrow termin-  │ C-STORE SCP │          │ C-FIND/C-MOVE│
         ates on CUBE, not oxidicom; │  :11111     │          │  bridge      │
         oxidicom itself is built)   └─────────────┘          └──────────────┘
```

---

## 2. CUBE's API style (DRF + collection+json)

These facts matter because **DICOMweb endpoints deliberately break with all of them** — DICOMweb
speaks `application/dicom+json`, not collection+json, and mounts outside the existing router.

- **Base paths:** `/api/v1/…` (public surface), `/chris-admin/api/v1/…` (plugin/compute-resource
  admin), `/schema/…` (OpenAPI docs). Source: `proposal-to-bch/CURRENT_API.md` §"Top-level shape".
- **Surface size:** 141 path templates, 220 operations (140 GET, 29 POST, 25 PUT, 26 DELETE, 0
  PATCH), 101 component schemas — from the live `schema.yaml` dump (OpenAPI **3.0.3**).
- **Route registration is centralized.** Every route is declared in one place,
  `chris_backend/core/api.py`, via a single `format_suffix_patterns([...])`. There are **no
  per-app `urls.py` files**. (This is why the DICOMweb plan mounts a *new* `dicomweb/urls.py` from
  `config/urls.py` instead — see `proposal-to-bch/QIDO_PLAN.md` §2.)
- **Default renderer is collection+json.** `DEFAULT_RENDERER_CLASSES` =
  `collectionjson.renderers.CollectionJsonRenderer` (default, `application/vnd.collection+json`),
  `JSONRenderer` (`application/json`), `BrowsableAPIRenderer` (`text/html`). Collection+JSON is a
  hypermedia format — responses carry `links`, `items`, `queries`, `template` envelopes around the
  data. drf-spectacular post-processing strips that wrapping so the OpenAPI spec describes the
  underlying shape. Source: `CURRENT_API.md` §"Top-level shape".
- **Pagination:** `LimitOffsetPagination`, default `PAGE_SIZE=10`.
- **Filtering:** `django_filters.rest_framework.DjangoFilterBackend`. Every `*ListQuerySearch`
  endpoint exposes a `FilterSet` defined from the app's `models.py`.

### Authentication / authorization

`REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` (`config/settings/common.py`):

| Class | How a client uses it |
|---|---|
| `TokenAuthentication` | `Authorization: Token <token>`; token from `POST /api/v1/auth-token/` (username+password) |
| `BasicAuthentication` | HTTP Basic — e.g. `chris:chris1234` in dev |
| `SessionAuthentication` | cookie session, for the browsable HTML API |

LDAP is wired in `config/settings/local.py` (an `lldap` dev server); `users.models.CustomLDAPBackend`
runs before Django's `ModelBackend`. **Key DICOMweb implication:** because auth lives in this DRF
chain, keeping the DICOMweb *HTTP endpoints* (QIDO/WADO/STOW views) in Django lets them inherit
Token / Basic / Session / LDAP for free; reproducing that chain in Rust (oxidicom) would be real work.

> ⚠️ Be precise tomorrow: this is the argument for keeping the **endpoint / API surface** in Django,
> **not** for doing all DICOMweb work in Django. ISC's actual recommendation
> (`proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` §"Where DICOMweb endpoints live") is **variant C
> (hybrid), with a fallback to variant B**, *not* variant A (Django-only). In variant C the QIDO/WADO/STOW
> endpoints stay in Django (for exactly this auth reason), but the *indexing* moves out of Python: oxidicom
> already parses DICOM tags during C-STORE ingest and would publish them on NATS for a small in-network
> consumer to upsert `PACSInstance`/`PACSStudy`. The Phase A Celery indexer (which re-reads each `.dcm`
> with pydicom — §6) is treated as a **fallback path for non-oxidicom-sourced files**, not the primary
> design. The chief reason ISC does *not* recommend variant A is precisely that re-reading every file from
> Python is wasted work when oxidicom has already parsed it (`RESEARCH_TICKET_OUTPUT.md` §"Reasoning").

---

## 3. The `pacsfiles` app — data model

Source of record: `chris_backend/pacsfiles/models.py` (upstream master), confirmed against the live
`proposal-to-bch/schema.yaml` component schemas (`PACS`, `PACSFile`, `PACSSeries`, `PACSQuery`,
`PACSRetrieve`). The app owns the `/api/v1/pacs/…` surface (18 path templates).

### 3.1 Entity relationships

```
        PACS (one row per upstream DICOM source, e.g. "MINICHRISORTHANC", "BCH")
         │  identifier (unique), active
         │  folder ── OneToOne ──▶ ChrisFolder  (SERVICES/PACS/<identifier>/)
         │
         ├──< PACSQuery     (a C-FIND request dispatched to pfdcm; result JSON stored)
         │       └──< PACSRetrieve  (a C-MOVE pull triggered from a query)
         │
         └──< PACSSeries   (ONE ROW PER DICOM SERIES — the finest grain CUBE stores today)
                 │  Patient + Study + Series tags  (all flattened onto this one row)
                 │  unique_together = (pacs, SeriesInstanceUID)
                 │  folder ── OneToOne ──▶ ChrisFolder  (the series' directory)
                 │
                 └── (the .dcm files live in that folder, surfaced as PACSFile rows)

        PACSFile  =  PROXY MODEL over ChrisFile, filtered to fname startswith 'SERVICES/PACS/'
                     (there is NO foreign key from PACSFile to PACSSeries — the link is the
                      folder tree: a PACSFile's parent folder chain leads up to the series folder)
```

The single most important structural fact for DICOMweb: **CUBE's finest-grain metadata row is the
Series (`PACSSeries`)**. There is no per-instance (per-`.dcm`) row, and Patient + Study tags are
*denormalized onto the Series row* rather than living in their own Patient/Study entities. QIDO-RS
needs a Patient→Study→Series→**Instance** hierarchy; Phase A adds the missing Instance level (see §6).

### 3.2 `PACS` model

| Field | Type | Notes |
|---|---|---|
| `identifier` | `CharField(max_length=100, unique=True)` | The handle, e.g. `BCH`, `MINICHRISORTHANC`. Becomes the per-PACS DICOMweb root `/dicom-web/pacs/<identifier>/`. |
| `active` | `BooleanField(default=True)` | |
| `folder` | `OneToOneField(ChrisFolder, related_name='pacs')` | Points at `SERVICES/PACS/<identifier>/`. |

`GET /api/v1/pacs/` has a **side effect**: each `list()` call hits `pfdcm`'s
`/api/v1/PACSservice/list/` and auto-creates DB rows (and the `SERVICES/PACS/<name>/` folder) for any
new PACS names found there. Source: `CURRENT_API.md` §"PACS surface — Endpoint matrix".

### 3.3 `PACSSeries` model — the central table

Field list from `pacsfiles/models.py` (upstream) cross-checked against `schema.yaml:9099`. Patient,
Study, and Series tags are all columns on this one row:

| DICOM level | Field | Type (pre-Phase-A) | Index |
|---|---|---|---|
| — | `creation_date` | `DateTimeField(auto_now_add=True)` | |
| Patient | `PatientID` | `CharField(max_length=100)` | `db_index=True` |
| Patient | `PatientName` | `CharField(max_length=150, blank=True)` | |
| Patient | `PatientBirthDate` | `DateField(null=True, blank=True)` | |
| Patient | `PatientAge` | `IntegerField(null=True, blank=True)` | computed integer (years) |
| Patient | `PatientSex` | `CharField(max_length=1, choices=M/F/O, blank=True)` | |
| Study | `StudyDate` | `DateField` | `db_index=True` |
| Study | `AccessionNumber` | `CharField(max_length=100, blank=True)` | `db_index=True` |
| Study | `StudyInstanceUID` | `CharField(max_length=100)` | (pre-A: none) |
| Study | `StudyDescription` | `CharField(max_length=400, blank=True)` | |
| Series | `Modality` | `CharField(max_length=15, blank=True)` | (pre-A: none) |
| Series | `ProtocolName` | `CharField(max_length=64, blank=True)` | |
| Series | `SeriesInstanceUID` | `CharField(max_length=100)` | `db_index=True` |
| Series | `SeriesDescription` | `CharField(max_length=400, blank=True)` | |
| — | `folder` | `OneToOneField(ChrisFolder, related_name='pacs_series')` | |
| — | `pacs` | `ForeignKey(PACS, related_name='series_list')` | |

`Meta`: `ordering = ('pacs', 'PatientID')`, `unique_together = ('pacs', 'SeriesInstanceUID')`.
A post-delete signal cascades to delete the associated `ChrisFolder`.

### 3.4 `PACSFile` — a proxy over `ChrisFile`

`PACSFile` is a **Django proxy model** (`proxy = True`, `ordering = ('-fname',)`) over the generic
`ChrisFile`. It is *not* a separate table — it is `ChrisFile` rows filtered to
`fname__startswith='SERVICES/PACS/'` via `PACSFile.get_base_queryset()`. So a `.dcm` file is a
`ChrisFile` row whose path is under the PACS tree; there is **no FK from `PACSFile` to
`PACSSeries`** — the only link is the folder ancestry (this is exactly why the Phase A indexing task
has to *walk parent folders* to find the owning series — see §6.3).

`schema.yaml:8943` (the serialized shape): `url`, `id`, `creation_date`, `fname`, `fsize`, `public`,
`owner_username`, `file_resource` (hyperlink to the bytes), `parent_folder`, `owner`. The raw bytes
are served only via the special binary route `/api/v1/pacs/files/{id}/.<...>` using
`BinaryFileRenderer` + `FileResponse`, accepting `?download_token=…` (`CURRENT_API.md` §"Special").

### 3.5 `PACSQuery` / `PACSRetrieve` — the C-FIND / C-MOVE bridge (NOT DICOMweb)

These are CUBE's *outbound* pull mechanism from upstream PACS, via the `pfdcm` service
(`http://pfdcm:4005`). They are conceptually the inverse of QIDO-RS/WADO-RS (CUBE-as-client, not
CUBE-as-server) and **stay in place** under the DICOMweb work — DICOMweb replaces the *consumer*
side, not the upstream-pull side. Source: `CURRENT_API.md` §"Auxiliary moving parts".

- `PACSQuery`: `title`, `query` (JSONField — a `PACSdirective`), `description`, `execute`,
  `result` (TextField — compressed JSON), `status` (created/sent/succeeded/…), FK `pacs`, FK `owner`.
  `POST …/queries/` with `execute=true` dispatches `pacsfiles.tasks.send_pacs_query` →
  `pfdcm`'s `/api/v1/PACS/sync/pypx/`.
- `PACSRetrieve`: `result`, `status`, FK `pacs_query`, FK `owner`. `POST …/retrieves/` triggers
  `pfdcm`'s `/api/v1/PACS/thread/pypx/` with `then=retrieve, withFeedBack=true` — i.e. a C-MOVE that
  ends with the data being C-STORE'd back into CUBE via oxidicom (§4).

### 3.6 PACS authorization

All PACS access keys off the well-known Django group **`pacs_users`**
(`Group.objects.get_or_create(name='pacs_users')`). Source: `CURRENT_API.md` §"Authorization model".

| Permission class | Applied to |
|---|---|
| `IsChrisOrIsPACSUserReadOnly` | PACS listings, series, files (write = `chris` superuser only, read = any `pacs_users` member) |
| `IsChrisOrIsPACSUserOrReadOnly` | `PACSQueryList` (write requires `pacs_users`, read open) |
| `IsChrisOrOwnerOrIsPACSUserReadOnly` | per-object query/retrieve (owner can write their own) |

The DICOMweb plan reuses `IsChrisOrIsPACSUserReadOnly` for the (read-only) QIDO/WADO endpoints
(`QIDO_PLAN.md` §9) — no new permission model.

---

## 4. How `oxidicom` ingests DICOM into the PACS tree

`oxidicom` is the **Rust C-STORE Service Class Provider (SCP)** — the "server" that receives DICOM
files pushed over TCP from an upstream PACS (or from a C-MOVE triggered by §3.5). It is the primary
ingestion path into the PACS file tree. Docs: <https://chrisproject.org/docs/oxidicom>;
architecture: <https://chrisproject.org/docs/oxidicom/architecture>; source:
<https://github.com/FNNDSC/oxidicom>.

### 4.1 The five-step ingest flow

From the oxidicom architecture page (<https://chrisproject.org/docs/oxidicom/architecture>):

1. **Association** — oxidicom receives DICOM data from a peer PACS. One push = one "association".
2. **Storage** — for *every* `.dcm` received, oxidicom writes the file directly into CUBE's storage
   tree (under `files_root`, into `SERVICES/PACS/…`). It does **not** go through CUBE's HTTP API.
3. **Progress messaging** — oxidicom publishes study/series reception-progress messages to **NATS**.
4. **Task queue** — when the association completes, oxidicom enqueues a **Celery** task.
5. **DB registration** — CUBE's Celery worker runs that task and registers the received series as a
   `PACSSeries` (plus the `PACSFile` rows) in Postgres.

```
 peer PACS ──C-STORE (DICOM/TCP :11111)──▶ oxidicom
                                            │
                  ┌─────────────────────────┼────────────────────────────┐
                  │ (2) write .dcm bytes     │ (3) LONK progress           │ (4) Celery task
                  ▼                          ▼                             ▼
        CUBE storage tree            NATS subject:                 register_pacs_series
        SERVICES/PACS/<pacs>/...     oxidicom.<pacs>.<SeriesUID>   (queue "main2")
                                                                          │
                                                                          ▼  (5)
                                                          CUBE Celery worker creates
                                                          PACSSeries + PACSFile rows
```

### 4.2 Configuration (authoritative, from oxidicom `src/settings.rs`)

Settings are environment variables (deserialized via serde; the deployed names are `OXIDICOM_*`).
Confirmed defaults from <https://github.com/FNNDSC/oxidicom/blob/master/src/settings.rs>:

| Setting | Default | Meaning |
|---|---|---|
| `files_root` | (required) | Root of CUBE's storage tree where `.dcm` files are written. |
| `celery_broker` | (required) | Broker URL; oxidicom enqueues the registration task here. |
| `queue_name` | `"main2"` | Celery queue for the `register_pacs_series` task — **same `main2` queue Phase A routes its indexing task to** (§6.5). |
| `nats_address` | (optional) | NATS server; if unset, progress messaging is disabled. |
| `listener_port` | `11111` | TCP port the C-STORE SCP listens on. |
| `listener_threads` | `8` | Concurrent associations. |
| `scp_max_pdu_length` | `16384` | DICOM PDU size. |
| `progress_interval` | `500ms` | Rate-limit window for progress messages. |
| `root_subject` | `"oxidicom"` | NATS subject prefix (see §4.4). |

The source comments explicitly tie `queue_name`'s default to CUBE's
`register_pacs_series` Celery task in `chris_backend/core/celery.py`
(<https://github.com/FNNDSC/oxidicom/blob/master/src/settings.rs>).

### 4.3 Folder layout in the storage tree

Files land under `SERVICES/PACS/<pacs_name>/…` (the `PACS.folder` root from §3.2). The exact
sub-path is derived from DICOM tags (patient / study / series / instance numbers) and sanitized
(`oxidicom/src/sanitize.rs`, `src/types.rs::DicomFilePath`). The structurally important point for
the Phase A indexer: **a `.dcm` file's immediate parent folder is not necessarily the series
folder** — oxidicom may nest files through intermediate sub-folders, so the series-owning folder can
be one or more levels up. This is why the indexing task walks the parent chain (§6.3).

### 4.4 NATS / LONK progress events

oxidicom's progress protocol is **LONK** ("Light Oxidicom NotifiKations"). From
`oxidicom/src/lonk.rs` (<https://github.com/FNNDSC/oxidicom/blob/master/src/lonk.rs>) the NATS
subject for a series is:

```
<root_subject>.<pacs_name>.<SeriesInstanceUID>      e.g.  oxidicom.MINICHRISORTHANC.1.2.840...
```

Message kinds published on that subject: a **progress** message (`ndicom` = running count of files
received), a **done** message (association/series complete), and an **error** message. CUBE relays
these to UI clients through `/api/v1/pacs/sse/` (Server-Sent Events; query params `pacs_name`,
`series_uids`). Source: `CURRENT_API.md` §"PACS surface" + `oxidicom/src/lonk_publisher.rs`.

### 4.5 The registration handshake (`POST /api/v1/pacs/series/`)

Two registration mechanisms exist in the codebase, and it's worth being precise tomorrow:

- **Modern path:** oxidicom enqueues the `register_pacs_series` **Celery task** directly (step 4/5
  above), which creates the `PACSSeries` + `PACSFile` rows server-side. (oxidicom `settings.rs`
  references this task by name.)
- **HTTP callback path:** `POST /api/v1/pacs/series/` is an internal registration endpoint whose
  serializer accepts `{path, ndicom, PatientID, …, pacs_name}`, **waits up to 30 s** for `ndicom`
  `.dcm` files to appear under `path`, then `bulk_create`s the `PACSFile` rows. This is *not* a
  researcher-facing endpoint and is *not* STOW-RS — it is the ingest registration handshake.
  Source: `CURRENT_API.md` §"PACS surface — Endpoint matrix" (`/api/v1/pacs/series/` POST).

Either way, **`PACSSeriesSerializer.create` is the choke point** where Phase A hooks the per-file
DICOMweb indexing (§6.4). Whichever registration path runs, the new instance index gets populated.

---

## 5. DICOM hierarchy → CUBE model/field map (the table that matters)

This is the bridge table. "Today" = upstream CUBE before this repo's Phase A. "Phase A (this repo)"
= what the code in `proposal-to-bch/code/source/chris_backend/` adds. QIDO required-attribute
references are PS3.18 (DICOMweb / Web Services); see the DICOM standard browser at
<https://www.dicomlibrary.com/dicom/>.

| DICOM level | Key attributes (tag) | Where it lives **today** | Phase A (this repo) | Remaining gap |
|---|---|---|---|---|
| **Patient** | `PatientID (0010,0020)`, `PatientName (0010,0010)`, `PatientBirthDate (0010,0030)`, `PatientSex (0010,0040)` | Columns on `PACSSeries` (denormalized) | unchanged | No `PACSPatient` model. Patient tags returned via Study rollup. Plan keeps Patient implicit (`RESEARCH_TICKET_OUTPUT.md`). |
| **Study** | `StudyInstanceUID (0020,000D)`, `StudyDate (0008,0020)`, `StudyTime (0008,0030)`, `AccessionNumber (0008,0050)`, `StudyDescription (0008,1030)`, `ModalitiesInStudy (0008,0061)`, `NumberOfStudyRelatedSeries (0020,1206)`, `NumberOfStudyRelatedInstances (0020,1208)` | `StudyInstanceUID/StudyDate/AccessionNumber/StudyDescription` columns on `PACSSeries`; **no Study row** | **`StudyTime` column added** to `PACSSeries`; `StudyInstanceUID` gains `db_index`; composite `(pacs, StudyInstanceUID)` index added | No `PACSStudy` model — Study is a `GROUP BY PACSSeries` rollup (MVP). `ModalitiesInStudy` / `NumberOf*` are computed aggregates, not stored. `PACSStudy` recommended for Phase B (`RESEARCH_TICKET_OUTPUT.md`). |
| **Series** | `SeriesInstanceUID (0020,000E)`, `Modality (0008,0060)`, `SeriesNumber (0020,0011)`, `SeriesDescription (0008,103E)`, `BodyPartExamined (0018,0015)`, `Manufacturer (0008,0070)`, `ProtocolName (0018,1030)`, `PerformedProcedureStepStartDate (0040,0244)`, `PerformedProcedureStepStartTime (0040,0245)` | `SeriesInstanceUID/Modality/ProtocolName/SeriesDescription` columns on `PACSSeries` | **5 columns added** to `PACSSeries`: `SeriesNumber`, `BodyPartExamined`, `Manufacturer`, `PerformedProcedureStepStartDate`, `PerformedProcedureStepStartTime`; `Modality` gains `db_index` | Series row already existed; gap is closed for the QIDO-required Series attribute set. |
| **Instance** | `SOPInstanceUID (0008,0018)`, `SOPClassUID (0008,0016)`, `InstanceNumber (0020,0013)`, `Rows (0028,0010)`, `Columns (0028,0011)`, `BitsAllocated (0028,0100)`, `NumberOfFrames (0028,0008)`, `TransferSyntaxUID (0002,0010)` | **Nowhere** — only inside the `.dcm` bytes on disk; no DB row | **New `PACSInstance` model** stores all of these, one row per `.dcm` (§6) | Closed at the data layer. The *query/render/retrieve* layer (QIDO/WADO views + DICOM-JSON renderer) is still unbuilt (Phases B–C). |

**Headline for the meeting:** before this spike CUBE had no Instance-level row at all and was missing
~7 QIDO-required Series/Study columns. Phase A adds the `PACSInstance` model and 6 new `PACSSeries`
columns, so the **schema can now hold every attribute QIDO-RS requires at all four levels**. What
remains is the *view layer* — the query parser, the `application/dicom+json` renderer, and the
QIDO/WADO/STOW endpoints (the decided scope of this spike).

---

## 6. Phase A additions in this repo

Code lives at `proposal-to-bch/code/source/chris_backend/`. Full writeup:
`proposal-to-bch/PHASE_A_IMPLEMENTATION.md`. Phase A is the **schema + ingest-pipeline foundation**;
it adds **no HTTP endpoints** and no new public API surface (verified: zero OpenAPI schema drift).

### 6.1 The new `dicomweb` Django app

A new app `chris_backend/dicomweb/`, deliberately isolated from `pacsfiles/` so DICOMweb concerns
(model, indexing task, later the query parser + renderer + views) don't perturb the stable
`/api/v1/pacs/…` surface. Registered via `INSTALLED_APPS += ['dicomweb']` in
`config/settings/common.py`. Files: `apps.py`, `models.py`, `tasks.py`, `migrations/0001_initial.py`,
`tests/test_tasks.py`.

### 6.2 The `PACSInstance` model

`dicomweb/models.py` — one row per `.dcm` file:

```python
class PACSInstance(models.Model):
    series    = models.ForeignKey('pacsfiles.PACSSeries', on_delete=CASCADE,
                                   related_name='instances')
    pacs_file = models.OneToOneField('pacsfiles.PACSFile', on_delete=CASCADE,
                                      related_name='dicom_instance')
    SOPClassUID       = models.CharField(max_length=100, db_index=True)
    SOPInstanceUID    = models.CharField(max_length=100, db_index=True)
    InstanceNumber    = models.IntegerField(null=True, blank=True)
    Rows              = models.IntegerField(null=True, blank=True)
    Columns           = models.IntegerField(null=True, blank=True)
    BitsAllocated     = models.IntegerField(null=True, blank=True)
    NumberOfFrames    = models.IntegerField(null=True, blank=True)
    TransferSyntaxUID = models.CharField(max_length=100, blank=True)
    class Meta:
        unique_together = ('series', 'SOPInstanceUID')
        ordering = ('series', 'InstanceNumber', 'SOPInstanceUID')
```

Design choices worth being able to defend tomorrow (from `PHASE_A_IMPLEMENTATION.md` §1):

- **`OneToOneField` to `PACSFile`** — each Instance is exactly one storage object, so WADO-RS can
  resolve bytes by `PACSInstance.pacs_file.fname` in O(1). (Closes the missing `PACSFile↔series`
  link too, but at instance grain.)
- **FK to `PACSSeries`, no denormalized Patient/Study tags** — single source of truth stays on
  `PACSSeries`; instance-level QIDO joins go through the series FK.
- **`unique_together=('series', 'SOPInstanceUID')`** — the *same* SOPInstanceUID can legitimately
  recur across different series/PACSes; uniqueness is only enforced within a series, which lets the
  task `update_or_create` keyed on `(series, SOPInstanceUID)` and stay idempotent.
- **Pixel-geometry fields are nullable** — some SOP classes (e.g. structured reports) don't carry
  `Rows`/`Columns`/`BitsAllocated`.
- **No `PACSStudy` model in Phase A** — deferred; Study is a runtime `GROUP BY` for the MVP.

### 6.3 Finding the owning series (the parent-folder walk)

Because there is no FK from `PACSFile` to `PACSSeries` and oxidicom may nest files under
sub-folders (§4.3), the task walks up the folder chain until a folder's reverse `pacs_series`
accessor resolves (`dicomweb/tasks.py::_find_series_for_file`, bounded to 16 hops):

```python
folder = pacs_file.parent_folder
for _ in range(16):                      # bound against cycles / bad state
    if folder is None: return None
    try:    return folder.pacs_series     # OneToOne reverse accessor on ChrisFolder
    except PACSSeries.DoesNotExist:
        folder = folder.parent
```

### 6.4 The `index_pacs_instance` Celery task

`dicomweb/tasks.py` — reads one `.dcm` header via **pydicom** and upserts the matching
`PACSInstance`. Signature `@shared_task(bind=True, max_retries=3, default_retry_delay=30)`.
Flow (full body in `tasks.py`):

1. Load the `PACSFile` (`select_related('parent_folder')`); `DoesNotExist` → log + return (race
   guard, since the task may outrun the surrounding transaction).
2. Skip non-`.dcm` files (sidecars) without retry.
3. `_find_series_for_file` (§6.3); `None` → log + return.
4. `connect_storage(settings).download_obj(fname)` — abstracts swift / S3 / fslink. A storage
   failure **retries** (transient, e.g. S3 throttling).
5. `pydicom.dcmread(BytesIO(raw), stop_before_pixels=True, force=True)` — header only (fast on cold
   S3 reads); `force=True` tolerates DICOMs missing the DICM preamble. A parse failure logs and
   **does not** retry (won't get better).
6. In a `transaction.atomic()`: `PACSInstance.objects.update_or_create(series=…,
   SOPInstanceUID=…, defaults={SOPClassUID, InstanceNumber, Rows, Columns, BitsAllocated,
   NumberOfFrames or 1, TransferSyntaxUID})`, then `_backfill_series_tags`.

`_backfill_series_tags` populates the 6 new `PACSSeries` columns (`StudyTime`, `SeriesNumber`,
`Manufacturer`, `BodyPartExamined`, `PerformedProcedureStepStartDate/Time`) **only when empty** — the
ingest path stays authoritative for what it already set; the first parsed `.dcm` fills the rest.

> **Bug the test suite caught** (`PHASE_A_IMPLEMENTATION.md` §7): the naive multi-format
> `strptime` for DICOM `TM` values mis-parses `'1430'` as `14:03:00` because `%H%M%S` matches
> greedily on 1–2-digit components. Fixed by dispatching the format string on input length
> (`{6:'%H%M%S', 4:'%H%M', 2:'%H'}` in `_parse_dicom_time`). This is the kind of detail to cite
> tomorrow as evidence the tests do real work.

### 6.5 Ingest fan-out + queue routing

In `pacsfiles/serializers.py`, after `PACSFile.objects.bulk_create(files)` inside
`PACSSeriesSerializer.create`:

```python
from dicomweb.tasks import index_pacs_instance     # imported inside the fn to avoid a cycle
created_ids = [pf.pk for pf in created if pf.pk is not None]
transaction.on_commit(lambda ids=created_ids:
    [index_pacs_instance.delay(pk) for pk in ids])
```

`transaction.on_commit` ensures the worker can't fetch the row before commit. The task is routed in
`core/celery.py` `task_routes` to queue **`main2`** — the same queue oxidicom's
`register_pacs_series` uses, and deliberately *not* `main1` (the latency-sensitive plugin-instance
state machine). New dependency: `pydicom>=3.0,<4.0` in `requirements/base.txt`.

### 6.6 What Phase A does NOT do (the work still ahead — this spike's scope)

- No QIDO/WADO/STOW HTTP endpoints, no `dicomweb/urls.py` (Phase C; mounted from `config/urls.py`,
  separate from `core/api.py` because DICOMweb isn't collection+json).
- No `application/dicom+json` renderer and no DICOM-tag query parser (Phase B —
  `QIDO_PLAN.md` §§5–6: tag-hex `?00100010=`, multi-value `?00080060=CT,MR`, date ranges,
  wildcards, `includefield`).
- No `PACSStudy` model — Study rollups computed via `GROUP BY` for MVP; `PACSStudy` recommended for
  scale (`RESEARCH_TICKET_OUTPUT.md`).
- No backfill management command yet (`reindex_pacs_instances`, Phase D) for pre-existing data.

---

## 7. Cheat sheet for tomorrow

- **CUBE = Django + DRF, default content-type `application/vnd.collection+json`.** DICOMweb breaks
  from that with `application/dicom+json` and a separate URL mount.
- **All routes registered in `core/api.py`** (no per-app urls). DICOMweb adds a *new* `dicomweb/urls.py`.
- **Auth = Token / Basic / Session / LDAP, in DRF.** This is the reason the QIDO/WADO/STOW **endpoints**
  stay in Django — but ISC's recommendation is **variant C (hybrid)**, not Django-only: endpoints in
  Django, *indexing* fed by oxidicom-over-NATS, with the Phase A pydicom indexer as a fallback
  (`RESEARCH_TICKET_OUTPUT.md`). Note this is variant C (hybrid), not variant A (Django-only); variant A
  was explicitly **not** recommended.
- **`PACSSeries` is the central row.** Patient + Study tags are denormalized onto it; `unique_together
  (pacs, SeriesInstanceUID)`.
- **`PACSFile` is a proxy over `ChrisFile`** under `SERVICES/PACS/`; **no FK to the series** — the link
  is the folder tree (hence the parent-folder walk in the indexer).
- **oxidicom = Rust C-STORE SCP on :11111.** Writes `.dcm` into the storage tree, publishes LONK
  progress on NATS subject `oxidicom.<pacs>.<SeriesUID>`, enqueues `register_pacs_series` on `main2`.
- **`PACSQuery`/`PACSRetrieve` (via `pfdcm` :4005) are the C-FIND/C-MOVE *pull* side** — they stay;
  DICOMweb replaces the *consumer* side.
- **Phase A (this repo) added** the `dicomweb` app, the `PACSInstance` model (Instance level, new),
  6 `PACSSeries` columns, and the `index_pacs_instance` Celery task — schema + ingest only, zero new
  endpoints, zero schema drift.

---

## 8. Sources

- `proposal-to-bch/CURRENT_API.md` — CUBE API surface + QIDO-RS gap analysis (local).
- `proposal-to-bch/PHASE_A_IMPLEMENTATION.md` — Phase A code walkthrough + validation log (local).
- `proposal-to-bch/QIDO_PLAN.md` — phased plan, query/renderer/view design (local).
- `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` — architecture recommendation A/B/C (local).
- `proposal-to-bch/schema.yaml` — live OpenAPI 3.0.3 dump (`PACS`, `PACSFile`, `PACSSeries`, etc.).
- `proposal-to-bch/code/source/chris_backend/dicomweb/{models,tasks}.py` + migrations (local).
- ChRIS architecture: <https://chrisproject.org/docs/architecture>
- oxidicom overview / architecture: <https://chrisproject.org/docs/oxidicom>, <https://chrisproject.org/docs/oxidicom/architecture>
- oxidicom source (config, ports, NATS/LONK, Celery): <https://github.com/FNNDSC/oxidicom> (`src/settings.rs`, `src/lonk.rs`, `src/lonk_publisher.rs`)
- CUBE pacsfiles models: <https://github.com/FNNDSC/ChRIS_ultron_backEnd> (`chris_backend/pacsfiles/models.py`)
- DICOM standard browser: <https://www.dicomlibrary.com/dicom/>
- pydicom: <https://pydicom.github.io/>
