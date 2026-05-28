# CUBE Current API — spec snapshot

Generated from `ChRIS_ultron_backEnd` master (Dec 2025 / commit on disk) by booting the local dev stack and dumping `drf-spectacular`.

## Artifacts

| File | Lines | Notes |
|---|---|---|
| `schema.yaml` | 11,557 | Output of `just openapi` — single component for request/response |
| `schema.split.yaml` | 11,996 | Output of `just openapi-split` (`SPECTACULAR_SPLIT_REQUEST=true`) — separate request/response component schemas, the form used for client codegen |

OpenAPI **3.0.3**. Title: _"ChRIS Research Integration System: Ultron BackEnd (CUBE) API"_. Version reports `0.0.0+unknown` because `chris_backend/__version__.py` is read from a git tag that isn't set in the workspace's loose clone — not meaningful.

To regenerate from a clean checkout:
```sh
cd ChRIS_ultron_backEnd
just build                                  # one-time, ~5 min
just openapi       > schema.yaml            # unsplit
just openapi-split > schema.split.yaml      # split request/response
just down                                   # stop containers (volumes kept)
```

Three `drf-spectacular` warnings appear at generation time; all three are inside `pacsfiles/views.py` and are harmless schema-introspection fallbacks (no errors).

---

## Top-level shape

- **Base**: `/api/v1/…` for the public surface; `/chris-admin/api/v1/…` for the plugin/compute-resource admin surface; `/schema/`, `/schema/swagger-ui/`, `/schema/redoc/` for the OpenAPI docs.
- **141 path templates, 220 operations** (140 GET, 29 POST, 25 PUT, 26 DELETE, 0 PATCH).
- **101 component schemas** in the spec.
- All routes are declared in **one place**: `chris_backend/core/api.py` (single `format_suffix_patterns([...])`). When adding routes, register them there — there are no per-app `urls.py` files.
- Authentication (`REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` in `config/settings/common.py`):
  - `rest_framework.authentication.TokenAuthentication` — `Authorization: Token <token>`. Tokens obtained via `POST /api/v1/auth-token/`.
  - `rest_framework.authentication.BasicAuthentication` — HTTP Basic. Useful for `chris:chris1234` in dev.
  - `rest_framework.authentication.SessionAuthentication` — for the browsable HTML API.
  - LDAP auth is wired in `config/settings/local.py` (lldap dev server). `users.models.CustomLDAPBackend` runs before `ModelBackend`.
- Content-types served (`REST_FRAMEWORK.DEFAULT_RENDERER_CLASSES`):
  - `application/vnd.collection+json` (**default** — `collectionjson.renderers.CollectionJsonRenderer`)
  - `application/json` (`JSONRenderer`)
  - `text/html` (`BrowsableAPIRenderer`)
- Parsers accept collection+json, JSON, form, and multipart.
- Pagination: `LimitOffsetPagination`, default `PAGE_SIZE=10`.
- Filtering: `django_filters.rest_framework.DjangoFilterBackend` (every `*ListQuerySearch` endpoint exposes a `FilterSet` from the app's `models.py`).
- Spectacular post-processing hooks strip collection+json wrapping from the generated schema (`collectionjson.spectacular_hooks.postprocess_remove_collectionjson`) — so the spec describes the underlying JSON shape, not what the default content-type renderer wraps it in.

---

## Routes by domain (live counts from `schema.yaml`)

The numbers below are **path templates**, not operations.

| Tag | Paths | What it does |
|---|---|---|
| `feeds` (registered at `/api/v1/` root and `/api/v1/{id}/…`) | 10 + root | The Feed/Note/Comment/Tag tree — the root entity of an analysis. |
| `filebrowser` | 28 | Virtual folder browser over the unified storage tree. Files, links, folder/file group & user permissions, search. |
| `plugins` | 24 | Plugin metas, plugins, plugin parameters, plugin instances + their splits/descendants/parameter values, and per-parameter-type detail views. |
| `pipelines` | 21 | Pipelines, pipings, default parameters (string/int/float/bool), source files, and **workflows** (pipeline runs). |
| `pacs` | 18 | PACS sources, queries, retrieves, series, files. See deep dive below. |
| `tags` / `taggings` | 5 + 1 | Tag CRUD and tag↔feed join records. |
| `groups` / `grouppermissions` / `userpermissions` | 6 + 1 + 1 | Group membership and ACL primitives shared with filebrowser. |
| `users` | 3 | User create/detail/groups. |
| `userfiles` | 4 | User-uploaded files. |
| `comments` | 1 | Comments on feeds. |
| `chris-admin` | 4 | Plugin and compute-resource admin (`POST` to register a plugin via `description.json` upload). |
| `chrisinstance` | 1 | Singleton describing this CUBE deployment. |
| `computeresources` | 3 | Read-only listing of registered compute resources. |
| `downloadtokens` | 3 | Short-lived tokens used by the `*-resource` binary download endpoints. |
| `publicfeeds` | 2 | Public listing of feeds with `public=true`. |
| `note{id}` | 1 | Notes attached to feeds. |
| `auth-token` | 1 | `POST` username/password → token. |
| `search` | 1 | `/api/v1/search/` — feed list query-search. |

Special: every file-bearing endpoint also exposes a sibling **`{id}/.<anything>`** path (matched by `re_path(r'.../<pk>/.*$')`) that serves the raw binary via `BinaryFileRenderer` and accepts a `?download_token=…` query-string token from `core.views.TokenAuthSupportQueryString`. These are the only routes where pixel/file payloads cross the API (everything else is JSON).

---

## PACS surface — deep dive

This is the surface that the ATLAS DICOMweb work touches. Source: `chris_backend/pacsfiles/{models,views,serializers,enums,permissions,consumers,services}.py`.

### Authorization model

| Permission class | Used on |
|---|---|
| `IsChrisOrIsPACSUserReadOnly` | All PACS listings, series, files (write = `chris` only, read = any user in `pacs_users` group) |
| `IsChrisOrIsPACSUserOrReadOnly` | `PACSQueryList` (write requires `pacs_users` membership, read open) |
| `IsChrisOrOwnerOrIsPACSUserReadOnly` | `PACSQueryDetail`, `PACSRetrieve*` (per-object: owner can write their own) |

Membership is checked against the Django group named **`pacs_users`** (`Group.objects.get_or_create(name='pacs_users')` is the well-known handle, created on first PACS auto-registration).

### Endpoint matrix

| Path | Methods | Notes |
|---|---|---|
| `/api/v1/pacs/` | GET | **Side effect**: each `list()` call hits `pfdcm`'s `/api/v1/PACSservice/list/` and reconciles known PACS in the DB. New names found there are auto-created with a `SERVICES/PACS/<name>/` folder. |
| `/api/v1/pacs/search/` | GET | Filter `PACSFilter`: `id`, `identifier`, `active`. |
| `/api/v1/pacs/{id}/` | GET | Single PACS. |
| `/api/v1/pacs/{id}/queries/` | GET, POST | Per-PACS queries. POST `{title, query (JSON), description, execute}` — if `execute=true`, async dispatches `pacsfiles.tasks.send_pacs_query` which calls `PfdcmClient.query` (→ `pfdcm`'s `/api/v1/PACS/sync/pypx/`). |
| `/api/v1/pacs/queries/` | GET | Global query list (owner-scoped unless in `pacs_users`). |
| `/api/v1/pacs/queries/search/` | GET | `PACSQueryFilter`: `min/max_creation_date`, `title`, `title_exact`, `status`, `execute`, `description`, `pacs_id`, `pacs_identifier`, `owner_username`. |
| `/api/v1/pacs/queries/{id}/` | GET, PUT, DELETE | PUT can flip `execute` false→true to (re-)dispatch. `query` becomes read-only after creation. |
| `/api/v1/pacs/queries/{id}/retrieves/` | GET, POST | POST triggers `PfdcmClient.retrieve` (→ `pfdcm`'s `/api/v1/PACS/thread/pypx/` with `then=retrieve, withFeedBack=true`). |
| `/api/v1/pacs/queries/{id}/retrieves/search/` | GET | `PACSRetrieveFilter`: `min/max_creation_date`, `status`, `owner_username`. |
| `/api/v1/pacs/queries/retrieves/{id}/` | GET, DELETE | Single retrieve. |
| `/api/v1/pacs/series/` | GET, **POST** | **POST is the registration callback** used by `oxidicom`/services after C-STORE: posts `{path, ndicom, PatientID, …, pacs_name}` and the serializer waits up to 30 s for `ndicom` .dcm files to appear under `path`, then bulk-creates the `PACSFile` rows. Not a researcher-facing endpoint. |
| `/api/v1/pacs/series/search/` | GET | `PACSSeriesFilter` — see full list under "QIDO-RS gap". |
| `/api/v1/pacs/series/{id}/` | GET, DELETE | DELETE marks deletion-pending and dispatches `pacsfiles.tasks.delete_pacs_series` (returns 202). |
| `/api/v1/pacs/{id}/series/` | GET | Series for a specific PACS. |
| `/api/v1/pacs/files/` | GET | Listing of every `.dcm` (or other file) under `SERVICES/PACS/`. |
| `/api/v1/pacs/files/search/` | GET | `PACSFileFilter`: `id`, `min/max_creation_date`, `fname` (prefix), `fname_exact`, `fname_icontains`, `fname_icontains_topdir_unique`, `fname_nslashes`. |
| `/api/v1/pacs/files/{id}/` | GET | File metadata. |
| `/api/v1/pacs/files/{id}/.<...>` | GET | **Binary download** (auth via Token, Basic, Session, or `?download_token=…`). Uses `BinaryFileRenderer` + `FileResponse`. |
| `/api/v1/pacs/sse/` | GET, POST | **SSE stream** of DICOM-reception progress. Not in the OpenAPI schema (declared in `core/api.py` outside `format_suffix_patterns`, no `serializer_class`). Query params: `pacs_name`, `series_uids` (comma-separated). Subscribes to NATS subjects published by `oxidicom`. There is also a parallel WebSocket consumer (`PACSFileProgress` in `pacsfiles/consumers.py`) but no URL pattern routes to it in `core/api.py` — it appears to be unwired in the current code. |

### `PACSSeries` model — current tag coverage

Source: `pacsfiles/models.py` lines 162–195. Stored columns are the indexable DICOM tags:

| Level | Tags stored on `PACSSeries` |
|---|---|
| Patient | `PatientID`, `PatientName`, `PatientBirthDate`, `PatientAge` (computed int), `PatientSex` (M/F/O) |
| Study | `StudyDate`, `AccessionNumber`, `StudyInstanceUID`, `StudyDescription` |
| Series | `Modality`, `ProtocolName`, `SeriesInstanceUID`, `SeriesDescription` |
| Instance | _(none — see gap below)_ |
| Pixel/SOP | _(none — there is no `SOPClassUID`/`SOPInstanceUID` column at all)_ |

Each `PACSSeries` is unique on `(pacs, SeriesInstanceUID)` and owns one `ChrisFolder` whose tree contains the raw `.dcm` files (proxied through the generic `PACSFile` model on top of `ChrisFile`).

### `PACSSeries` filter parameters (what you can query today via `?…`)

```
AccessionNumber, PatientAge, PatientBirthDate, PatientID, PatientName, PatientSex,
ProtocolName, SeriesDescription, SeriesInstanceUID, StudyDate, StudyDescription,
StudyInstanceUID, deletion_status, id, pacs_id, pacs_identifier,
min_PatientAge, max_PatientAge, min_creation_date, max_creation_date,
limit, offset
```

All filters are **exact match** except `PatientName`, `ProtocolName`, `StudyDescription`, `SeriesDescription` which are `icontains` (case-insensitive substring).

### How this compares to DICOMweb / QIDO-RS

| QIDO-RS requirement | CUBE today | Gap to MVP precondition 1 |
|---|---|---|
| Resource path `/studies` | `/api/v1/pacs/series/` (series-only listing) | New endpoint(s): `/studies`, `/studies/{StudyInstanceUID}/series`, `/studies/{StudyInstanceUID}/series/{SeriesInstanceUID}/instances`. Study-level rollup not currently a model — must be computed (group-by `StudyInstanceUID`) or denormalized into a new `PACSStudy` model. |
| Query parameters spelled as DICOM tags or keywords (`?00100010=…` or `?PatientName=…`) and **multi-value support** (`?00080060=CT,MR`) | Keyword-only, single-value, mixed snake_case (`pacs_identifier`) and DICOM-case (`PatientName`) | Parse DICOM tag-hex form, support comma-separated multi-values, range syntax for dates (`StudyDate=20230101-20231231`), wildcard `*?` on `PN` and `LO` VRs. |
| Patient/Study/Series/**Instance** hierarchy | Only Series row; instance metadata lives only inside the `.dcm` files on disk | Need an `Instance` row (or virtual instance index) carrying `SOPClassUID`, `SOPInstanceUID`, `InstanceNumber`, `Rows`, `Columns`, `BitsAllocated`, `NumberOfFrames`, `PhotometricInterpretation`. |
| Response = DICOM JSON Model (`application/dicom+json`, tag-keyed objects with `vr` + `Value`) | Collection+JSON or DRF JSON with keyword-keyed fields and CUBE-specific scaffolding (`url`, `id`, `folder`, etc.) | New renderer + serializer that emits the canonical DICOM JSON Model. Tag set per Table CC.2.5-3 (QIDO Study) / Table CC.2.5-4 (Series) / Table CC.2.5-5 (Instance). |
| Standardised return tags (e.g. study-level requires `0008,0020 StudyDate`, `0008,0030 StudyTime`, `0008,0050 AccessionNumber`, `0008,0061 ModalitiesInStudy`, `0008,1030 StudyDescription`, `0008,1190 RetrieveURL`, `0020,000D StudyInstanceUID`, `0020,1206 NumberOfStudyRelatedSeries`, `0020,1208 NumberOfStudyRelatedInstances`, etc.) | Subset present (`StudyDate`, `AccessionNumber`, `StudyInstanceUID`, `StudyDescription`); **missing**: `StudyTime`, `ModalitiesInStudy`, `NumberOfStudyRelatedSeries`, `NumberOfStudyRelatedInstances`, `RetrieveURL`, `ReferringPhysicianName` and the timezone offset tag | Schema additions or aggregation/computed fields, plus a way to fill `RetrieveURL` pointing at the WADO-RS endpoint we don't yet have. |
| WADO-RS retrieval (`GET /studies/{…}/series/{…}/instances/{…}`, plus `/metadata`, `/frames/{…}`, `/rendered`, `/thumbnail`, multipart `application/dicom`) | `GET /api/v1/pacs/files/{id}/.…` returns one `.dcm` as `application/octet-stream` (via `BinaryFileRenderer`) | Need WADO-RS endpoints. Storage backend already supports streaming via `core.storage`; the work is routing + `multipart/related` packaging + frame extraction for `/frames` and `/rendered`. |
| STOW-RS ingest | None — ingest is via `oxidicom` (DICOM C-STORE on port 11111) + the internal `POST /api/v1/pacs/series/` callback | Not in MVP scope per the proposal; OHIF/Slicer browse but don't push, so STOW-RS can wait. |
| Conformance statement / capabilities | None | Standard `GET /` returning a conformance statement is needed for some clients (OHIF tolerates absence). |

The proposal's _"Extend the schema to capture a well-defined tag set at all four DICOM hierarchy levels"_ work concretely lands in `pacsfiles/models.py` + a migration. The _"Expose QIDO-RS endpoints in Django querying that schema"_ work lands as new view classes registered in `core/api.py` plus a new DICOM-JSON-Model renderer in (likely) `core/renderers.py` next to the existing `BinaryFileRenderer`. The _"WADO-RS endpoints proxying retrieval from storage"_ reuses `core.storage.connect_storage` which already abstracts swift / S3 / fslink.

### Auxiliary moving parts to be aware of

- **`pfdcm`** (`http://pfdcm:4005`, set in `config/settings/local.py:195`) — the upstream DICOM C-FIND/C-MOVE bridge. CUBE's PACS queries do **not** read directly from a remote PACS; they POST a `PACSdirective` to `pfdcm` and store the compressed JSON result on `PACSQuery.result`. This is what QIDO-RS replaces from the consumer side, but on the producer side `pfdcm` stays as the way CUBE pulls from upstream PACS into its own store.
- **`oxidicom`** (`docker-compose.yml:236`, port 11111) — the Rust C-STORE SCP. Receives DICOMs, writes them under `/data` (CUBE storage), and publishes a NATS event. CUBE's `PACSSeries` create endpoint is the registration handshake driven by oxidicom.
- **NATS** — broker between `oxidicom` and CUBE for ingest-progress events. The `/api/v1/pacs/sse/` SSE stream relays these to clients.

---

## Sources

- Live schema dump: `schema.yaml`, `schema.split.yaml` (this directory).
- Route registry: `ChRIS_ultron_backEnd/chris_backend/core/api.py`.
- App code: `ChRIS_ultron_backEnd/chris_backend/pacsfiles/`.
- Settings: `ChRIS_ultron_backEnd/chris_backend/config/settings/{common,local}.py`.
- Storage abstraction: `ChRIS_ultron_backEnd/chris_backend/core/storage/`.
