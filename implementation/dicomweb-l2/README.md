# `dicomweb` — DICOMweb (QIDO-RS / WADO-RS / STOW-RS) for CUBE (L2 test implementation)

A reviewable Django app that implements all three DICOMweb services on top of
CUBE's existing `pacsfiles` storage tree, building on the Phase A foundation
(the `PACSInstance` model + `index_pacs_instance` Celery task already shipped in
`proposal-to-bch/code/source/chris_backend/dicomweb/`). It is a **drop-in for
`chris_backend/dicomweb/`** in a [`FNNDSC/ChRIS_ultron_backEnd`](https://github.com/FNNDSC/ChRIS_ultron_backEnd)
checkout, and is also the **overlay source** consumed by the Ansible role
`deploy/ansible/roles/dicomweb_app/` (its contents are `docker cp`'d into the
running CUBE container at `/home/localuser/chris_backend/dicomweb/`, then
migrated + restarted). The deployment must **wrap** the
[`FNNDSC/miniChRIS-docker`](https://github.com/FNNDSC/miniChRIS-docker) compose
stack, not fork it.

> Status honesty: this is an **L2 test implementation** produced for a
> stakeholder spike, not a merged, CI-green CUBE PR. The framework-free core
> (DICOM JSON Model encoder, the multipart/related parser, and the QIDO
> query-parser term-building logic) **was executed standalone during this review**
> and passes — including PN/IS/DA/TM encoding, binary-payload multipart
> round-tripping with embedded CRLF, and date/time/wildcard/fuzzy Q-building. The
> HTTP/DB tests (`tests/test_views.py`) are written against the schema this spike
> adds and require a CUBE checkout (Django + Postgres + storage) with the new
> migrations applied to run. See [Known limitations](#known-limitations) — do not
> present this as turn-key runnable against stock upstream master.

---

## Layout

```
dicomweb/
├── apps.py
├── models.py          # PACSStudy (NEW, D2) + Phase-A PACSInstance (kept)
├── dicomjson.py        # DICOM JSON Model (PS3.18 Annex F) encoder — framework-free
├── query_parser.py     # QIDO query string → Django ORM Q filters; keyword↔tag↔field map
├── renderers.py        # DicomJsonRenderer / DicomJsonAsJsonRenderer / MultipartRelatedRenderer
├── serializers.py      # row → DICOM JSON Model (Study/Series/Instance) + RetrieveURLBuilder
├── multipart.py        # multipart/related parser for STOW bodies — framework-free
├── qido_views.py       # QIDO-RS endpoints
├── wado_views.py       # WADO-RS endpoints (retrieve + metadata + native frames/bulkdata)
├── stow_views.py       # STOW-RS store
├── urls.py             # /dicom-web/pacs/<id>/... routing + config wiring notes
├── signals.py          # post_save(PACSFile) -> auto-index (real-time oxidicom ingest)
├── tasks.py            # index_pacs_instance (re-read) + index_from_metadata (variant C)
├── management/commands/reindex_pacs_instances.py     # backfill (re-read path)
├── management/commands/consume_dicomweb_index.py     # variant-C NATS consumer (no re-read)
├── migrations/         # 0001 PACSInstance, 0002 PACSStudy, 0003 pg_trgm (fuzzy)
├── tests/
├── MAPPING.md          # attribute → model-field map, per level
└── README.md           # this file
```

## Two indexing paths (re-read vs. variant C)

The DICOMweb index can be populated two ways:

- **Re-read (default, Phase A):** `index_pacs_instance` downloads each `.dcm` and
  parses it with pydicom. Triggered in real time by `signals.py` (oxidicom
  ingest) and by `reindex_pacs_instances` (backfill). Simple; re-parses bytes
  oxidicom already parsed.
- **Variant C (hybrid, prototype):** `index_from_metadata(meta)` upserts
  `PACSInstance`/`PACSStudy` **from a tags message — no storage read, no
  pydicom**. `consume_dicomweb_index` is a NATS subscriber that feeds it. This is
  the architecture doc's D1 recommendation: an *extended* oxidicom publishes the
  tags it already parsed in Rust; this consumer indexes them. oxidicom's current
  NATS (LONK) carries progress only, so the tag-bearing event is the one new
  piece oxidicom would add (subject `oxidicom-meta.<pacs>.<series>`; payload
  schema in the command's docstring). **Proven live:** publishing a parsed-tags
  event for a PACS whose file does not exist in storage still produced a complete
  QIDO `/studies` result — so it came from the message, not a re-read.

## What this implements

Per-PACS DICOMweb roots at `/dicom-web/pacs/<pacs_identifier>/` where
`<pacs_identifier>` is `pacsfiles.PACS.identifier` (e.g. `BCH`). Auth is CUBE's
existing DRF chain (Token / Basic / Session, LDAP-backed); permission is
`pacsfiles.permissions.IsChrisOrIsPACSUserReadOnly` (read for any `pacs_users`
member, write/STOW for `chris`). URL surface and status semantics follow DICOM
**PS3.18**: <https://dicom.nema.org/medical/dicom/current/output/html/part18.html>.

### QIDO-RS (PS3.18 §10.6) — `qido_views.py`
`GET studies`, `studies/{study}/series`,
`studies/{study}/series/{series}/instances`, `studies/{study}/instances`,
`series`, `instances`. DICOM JSON Model as `application/dicom+json` (and
`application/json`, treated as equivalent per §10.6.2). Matching by keyword or
8-hex tag, multi-value OR, UID lists, date/time ranges, string-VR wildcards,
`includefield` (incl. `=all`), `fuzzymatching`, `limit`/`offset`. 200 + `[]` on
empty match, 400 on malformed query, 413 over the hard ceiling, 406 on bad
`Accept`.

### WADO-RS (PS3.18 §10.4) — `wado_views.py`
Retrieve study / series / instance as `multipart/related; type="application/dicom"`
(streamed from `core.storage`, no transcoding — stored Transfer Syntax only);
`/metadata` at all three levels as `application/dicom+json` (with `PixelData`
referenced via `BulkDataURI`, not inlined). **`/frames` and `/bulkdata` return
raw pixel octets for native transfer syntaxes** (`501` for compressed; see
limitations).

### STOW-RS (PS3.18 §10.5) — `stow_views.py`
`POST studies` / `POST studies/{study}`. Parses `multipart/related;
type="application/dicom"` with pydicom, writes each instance into
`SERVICES/PACS/<id>/<study>/<series>/<sop>.dcm`, find-or-creates the
`PACSStudy` / `PACSSeries` / `PACSFile` / `PACSInstance` rows, refreshes the
study roll-up counters, returns the Store Instances Response
(`00081199 ReferencedSOPSequence` / `00081198 FailedSOPSequence` /
`00081197 FailureReason`) with 200 / 202 / 409 / 400 / 415.

---

## How it maps to CUBE

| Concern | CUBE convention | What this app does |
|---|---|---|
| Data model | `PACSSeries` is the central row; Patient+Study tags denormalized onto it; `PACSFile` is a proxy over `ChrisFile` (no FK to series — folder ancestry is the link) | Adds explicit `PACSStudy` (Study/Patient tags + counters), keeps Phase A's `PACSInstance` (1-to-1 with `PACSFile`). `PACSSeries` gains a nullable FK `study`. See `MAPPING.md`. |
| URL routing | All routes in `core/api.py` via one `format_suffix_patterns` | Mounts a **separate** `dicomweb/urls.py` from `config/urls.py` (not collection+json; hierarchical). Matches how `/schema/` and `/chris-admin/` mount. |
| Renderers | Default `collectionjson` + `JSONRenderer` + `BrowsableAPIRenderer` | New `DicomJsonRenderer` (`application/dicom+json`), `DicomJsonAsJsonRenderer` (`application/json` alias), `MultipartRelatedRenderer` (WADO passthrough). |
| Auth / permissions | Token/Basic/Session/LDAP + `IsChrisOrIsPACSUserReadOnly`, `pacs_users` group | Reused verbatim — no new auth code. |
| Storage | `core.storage.connect_storage(settings)` over swift / s3 / fslink; `download_obj` / `upload_obj` | WADO reads via `download_obj`; STOW writes via `upload_obj`. No backend-specific code. |
| Async indexing | Phase A `index_pacs_instance` on Celery queue `main2` | A `post_save(PACSFile)` signal (`signals.py`) auto-dispatches it in real time, so **oxidicom-ingested DICOM reaches QIDO/WADO with no manual reindex** (proven live). STOW indexes in-request; `reindex_pacs_instances` backfills pre-existing data. The indexer also upserts `PACSStudy` + roll-ups. |
| Migrations | auto-generated via `just makemigrations` | `0001_initial` (Phase A, `PACSInstance`) kept; `0002_pacsstudy` added. Companion `pacsfiles` migration adds the `study` FK — regenerate in a real checkout. |

Architecture context (variants A/B/C, `PACSStudy` vs GROUP BY, STOW scope,
`pg_trgm`) lives in `../../knowledge-base/08-l2-architecture-decisions.md` and
`../../proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`. This code follows the
Django-served-endpoints shape of variants A/C (endpoints in Django for the auth
chain); under variant C, STOW + the reindex command are exactly the "fallback
indexer for non-oxidicom ingestion" the recommendation calls for.

---

## How to apply it

1. Copy this directory to `chris_backend/dicomweb/` (supersedes the Phase A
   `dicomweb/`: `models.py` keeps `PACSInstance` and adds `PACSStudy`;
   `tasks.py` is unchanged from Phase A).
2. Register + mount:
   ```python
   # config/settings/common.py
   INSTALLED_APPS += ['dicomweb']
   # config/urls.py
   urlpatterns += [
       path('dicom-web/pacs/<str:pacs_identifier>/', include('dicomweb.urls')),
   ]
   ```
3. Add the nullable FK on `PACSSeries` (note at bottom of `models.py`) and
   regenerate migrations:
   ```sh
   just makemigrations dicomweb pacsfiles && just migrate
   ```
   (The included `0002_pacsstudy.py` is illustrative; let `makemigrations`
   author the real ones to match your migration graph, as Phase A validated.)
4. Optionally add the `PACSStudy` find-or-create to `PACSSeriesSerializer.create`
   (the one new ingest hook — described in `models.py`).
5. Backfill: `just bash` then `python manage.py reindex_pacs_instances`.

## How to run the tests

```sh
just test dicomweb --exclude-tag integration   # fast: encoder, parser, multipart, qido/wado metadata
just test dicomweb                              # + storage round-trips (WADO retrieve, STOW store)
```

| File | DB? | Storage? | What it covers |
|---|---|---|---|
| `tests/test_dicomjson.py` | no | no | DICOM JSON encoding (PN, IS, DA/TM, omission). Run standalone during this spike. |
| `tests/test_query_parser.py` | no | no | QIDO query forms → `Q`; 400 paths. |
| `tests/test_multipart.py` | no | no | multipart round-trip incl. binary-with-CRLF. Run standalone. |
| `tests/test_serializers.py` | no | no | row → DICOM JSON via stand-ins. |
| `tests/test_views.py` (`qido`/`wado`) | yes | metadata: no | full HTTP path against the new schema. |
| `tests/test_views.py` (`integration`) | yes | yes | WADO byte streaming + STOW store. |
| `tests/test_tasks.py` | no | no | Phase A helpers (carried over verbatim). |

Synthetic DICOM fixtures are built with pydicom in `tests/fixtures.py` — no
external sample data needed.

---

## Known limitations

Deliberate scoping (not oversights) plus the gaps the adversarial review found.
State these in review.

### Functional scope (deliberate)

1. **Frames / bulkdata: implemented for NATIVE (uncompressed) transfer syntaxes;
   compressed + rendered/thumbnail still `501`.** `/frames` and `/bulkdata`
   return raw pixel octets (`multipart/related; type="application/octet-stream"`,
   slicing `PixelData`) for native syntaxes — verified live against an
   oxidicom-ingested MR. **Encapsulated/compressed** syntaxes and
   `/rendered` + `/thumbnail` still return `501` (transcoding needs
   `pylibjpeg`/`gdcm`, deliberately out of scope).
2. **No transcoding in WADO retrieve.** Stored Transfer Syntax only
   (`transfer-syntax=*`/default); a specific different syntax → `406`. Each
   multipart part's `Content-Type` is `application/dicom` **without** an explicit
   `transfer-syntax=` MIME parameter (PS3.18 says clients SHOULD receive it;
   OHIF/dcm4che tolerate its absence). Add it when transcoding lands.
3. **`includefield` is currently a no-op at the response layer.** It is parsed
   and validated (so no 400), but the serializers always emit the *full indexed
   attribute set* for each level, which is a superset of the QIDO required set —
   PS3.18 §10.6.3 permits returning more than requested, so this is conformant.
   The only thing `includefield` cannot surface is a tag CUBE does not index at
   all (e.g. `StudyID`, `InstitutionName`); those need schema additions. The
   `include_all`/`includefields` parameters on `serialize_*` are accepted for
   forward-compat but presently unused — documented in `serializers.py`.
4. **QIDO empty/universal matching (`?Tag=`) is rejected, not honored.** A
   present-but-empty match key raises `QidoQueryError` → 400 rather than matching
   "attribute present with any value". MVP scope.

### Correctness items fixed in this review (note what changed)

5. **DICOM date/time query values are now coerced to native `date`/`time`.**
   Previously the parser passed the raw DICOM `YYYYMMDD` / `HHMMSS` string
   straight into `Q(StudyDate='20230102')`, which **errors at query time** on a
   Postgres `DateField`/`TimeField` (they expect ISO). `query_parser._coerce_temporal`
   now parses DA/TM/DT (single value and both range bounds) to Python objects;
   an unparseable temporal value is a clean 400. Unit-validated this review.
6. **Test fixtures use the pydicom 3.x writer idiom.** `dataset_to_bytes` now
   calls `dcmwrite(..., enforce_file_format=True)` and lets encoding derive from
   `file_meta.TransferSyntaxUID`, instead of the `is_little_endian` /
   `is_implicit_VR` / `write_like_original` knobs that are deprecated in 3.0 and
   removed in 4.0 (`requirements/base.txt` pins `pydicom>=3.0,<4.0`).

### Integration / data-shape caveats

7. **Schema dependency.** `test_views.py` and STOW need the `PACSStudy` model
   (`0002`), the nullable `PACSSeries.study` FK, and the Phase A `PACSSeries`
   columns. Against stock master without those migrations the view tests won't
   pass; STOW's `series.study` assignment is `hasattr`-guarded so the module
   still imports.
8. **STOW rejects DICOM objects with no `StudyDate`.** Upstream
   `PACSSeries.StudyDate` is `DateField(db_index=True)` — **NOT NULL**. STOW's
   `_store_one` sets the new series' `StudyDate` from the parsed `StudyDate`,
   which can be `None` for some SOP classes (e.g. some SRs); that row then fails
   the NOT-NULL constraint and the part is reported in `FailedSOPSequence`
   (`0xA700`) rather than stored. Most image objects carry `StudyDate`, so this
   is an edge case, but a production STOW should either default `StudyDate` or
   relax the column. Not changed here (it would require a `pacsfiles` migration).
9. **STOW `PACSFile`/`ChrisFolder` creation is simplified.** It sets
   `pacs_file.fname.name`/`fsize` and `ChrisFolder.objects.get_or_create(path=...,
   defaults={'owner': ...})` directly (verified against the live `ChrisFolder`,
   whose `save()` recursively creates parents and whose `path` is unique). The
   real ingest path (`PACSSeriesSerializer.create`) does more (wait-for-files,
   FileGroup/FolderGroup permission grants, folder bookkeeping). Production STOW
   should route through / share that logic.
10. **Study roll-up counters are refreshed at STOW time only here.** The
    oxidicom/`PACSSeriesSerializer.create` path must also do the find-or-create +
    counter refresh for non-STOW ingestion to keep the catalog consistent (the
    one new ingest hook noted in `models.py`). Not wired in this drop-in.
11. **Wildcards use `__iregex`** (portable, correct) — unanchored substring
    scans won't use a B-tree index; add the `pg_trgm` GIN index (D4) for
    substring/fuzzy at scale. `fuzzymatching` emits `__trigram_similar`, which
    **requires `pg_trgm`** (one-line `TrigramExtension()` migration); without it
    that query errors at the DB.
12. **413 is effectively only reachable when a client asks for `limit >= MAX_LIMIT`.**
    `MAX_LIMIT` (5000) is a paging ceiling, not a result-set guard; ordinary
    broad queries page normally at the default limit. This is intentional but
    means 413 is rare in practice.

### Process

13. **HTTP/DB path not executed in a live CUBE/miniChRIS stack here.** The DICOM
    JSON encoder, multipart parser, and query-parser term-building were validated
    standalone this review; the DB/HTTP tests are written and byte-compiled but
    need a CUBE checkout (Django + Postgres + storage) to run.
14. **No conformance/capabilities document** (PS3.18 §8.9). OHIF tolerates its
    absence; add a minimal one later for strict clients.
15. **drf-spectacular**: exclude these non-collection+json views
    (`@extend_schema(exclude=True)` or a preprocessing hook) before regenerating
    the OpenAPI dump.

## Enabling the deploy overlay

Once this code is in place, the Ansible overlay can be turned on:

```bash
cd ../../deploy/ansible
ansible-playbook -i inventory.ini site.yml -e dicomweb_overlay_enabled=true
```

The smoke test (`deploy/ansible/scripts/smoke.sh`) then exercises the
QIDO/WADO/STOW endpoints instead of reporting `SKIP`.

---

## Sources

- DICOM PS3.18 (Web Services): QIDO §10.6, WADO §10.4, STOW §10.5, JSON Model
  Annex F — <https://dicom.nema.org/medical/dicom/current/output/html/part18.html>
- DICOM PS3.4 matching rules (§C.2.2.2.4) —
  <https://dicom.nema.org/medical/dicom/current/output/html/part04.html>
- DICOMweb overview — <https://www.dicomstandard.org/using/dicomweb>
- Azure DICOM conformance statement (response shapes, STOW failure codes) —
  <https://learn.microsoft.com/en-us/azure/healthcare-apis/dicom/dicom-services-conformance-statement-v2>
- Orthanc DICOMweb plugin — <https://orthanc.uclouvain.be/book/plugins/dicomweb.html>
- pydicom — <https://pydicom.github.io/>
- CUBE: `FNNDSC/ChRIS_ultron_backEnd` (`pacsfiles/`, `core/storage/`,
  `core/api.py`, `config/`); miniChRIS-docker — <https://github.com/FNNDSC/miniChRIS-docker>
- This repo: `proposal-to-bch/{RESEARCH_TICKET_OUTPUT,CURRENT_API,QIDO_PLAN,PHASE_A_IMPLEMENTATION}.md`,
  `knowledge-base/{02,05,06,08}-*.md`, `proposal-to-bch/code/source/chris_backend/dicomweb/` (Phase A).
