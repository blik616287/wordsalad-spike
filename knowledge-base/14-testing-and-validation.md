# 14 — Testing & Validation: the Evidence Ledger

> **Purpose.** This is the file Marty reaches for when someone in the BCH room asks
> *"how do you know it actually works?"* It is the **evidence ledger** for the L2
> DICOMweb spike: exactly what was tested, by what mechanism, what passed, and — just
> as important — what is **not** proven. Every claim below is grounded in a test file,
> a merged PR, or a live run observed this session. Where something is unproven it says
> so in plain language.
>
> Keep the distinction sharp in the meeting:
> - **Unit / pure-logic tests** — run anywhere, no DB. Prove the encoders/parsers.
> - **Integration tests** — run *inside a real ChRIS_ultron_backEnd (CUBE) checkout*
>   via CUBE's own `just test` harness, against real Postgres + real storage.
> - **Live end-to-end** — real HTTP against a running CUBE stack on `:8000`, with data
>   ingested by **oxidicom C-STORE** and by **STOW**.
>
> Headline number: **97/97 tests pass** under `just test dicomweb` in a real CUBE
> checkout (the suite grew from 91 as frames/bulkdata/fuzzy/metadata/variant-C tests
> were added). The pure-logic subset (61 tests) also ran standalone in a venv.

Source material for this file:
- `implementation/dicomweb-l2/tests/` (the seven test modules + `fixtures.py`)
- `proposal-to-bch/PHASE_A_IMPLEMENTATION.md` (the original Phase A validation log)
- `implementation/dicomweb-l2/README.md` (Known limitations §1–15)
- Git history (PRs #5–#14) for the merged fixes cited in the bug ledger

---

## 1. The big picture in three sentences

1. The **logic** (DICOM JSON encoding, multipart parsing, QIDO query-string → ORM
   filters, row → JSON serialization) is locked down by fast, DB-free unit tests that
   run anywhere.
2. The **wiring** (URL routing, auth, permissions, the Postgres schema this spike adds,
   the storage round-trip, the Celery indexer) is locked down by DB/HTTP tests that
   run **inside a genuine CUBE checkout** with the new migrations applied.
3. The **behaviour** (oxidicom-ingested data appearing in QIDO/WADO with no manual
   step; STOW storing instances; native frames/bulkdata; fuzzy matching; the variant-C
   no-re-read indexer) was **observed live over real HTTP** against a running stack.

If someone says "those are just unit tests," the honest answer is: no — the suite
runs in CUBE's own container with real Postgres and storage, and the headline
behaviours were additionally exercised over the wire on a live stack.

---

## 2. Test inventory

Every test module, what it asserts, whether it touches the DB / storage, and the test
count. Counts are the number of `test_*` methods in each file. The framework-free
modules (`dicomjson`, `multipart`, `query_parser`, `serializers`) are the ones that
ran **standalone** (61 passing in a venv early in the session); all modules run under
`just test dicomweb` inside CUBE.

### 2.1 Pure-logic modules (no DB, no storage)

| File | What it asserts | DB? | Tests |
|---|---|---|---|
| `tests/test_dicomjson.py` | DICOM JSON Model encoder (PS3.18 Annex F): tag normalization (canonical/lowercase/`(gggg,eeee)`/comma/int forms; malformed raises), VR encoding (`UI`, `PN` single + multi-component groups incl. Ideographic, `IS`/`US`/`DS` numeric coercion, `DA`/`TM` → DICOM strings), empty-value omission, bulk-VR emitted without `Value`, `BulkDataURI` form, dataset keyed-by-tag with omission, `SQ` sequence nesting. | no | 18 |
| `tests/test_query_parser.py` | QIDO query-string → Django `Q` tree: keyword↔tag resolution + unknown/malformed handling; single value, integer coercion + 400 on non-int; multi-value UID `__in` and CS OR-lists; **date ranges coerced to native `date()`** (closed/open-lower/open-upper, bad date 400, bare-dash 400); wildcards `*`/`?` → anchored `__iregex`, wildcard-on-numeric rejected; **fuzzy → `__trigram_similar`**; `includefield` (keyword/tag/comma-list/`all`/return-key); pagination defaults, cap to `MAX_LIMIT`, negative/non-int 400; unsupported match key ignored (no 400). | no | ~28 |
| `tests/test_serializers.py` | Row → DICOM JSON Model for Study / Series / Instance using `SimpleNamespace` stand-ins + injected `RetrieveURLBuilder`: required attributes present with correct VR/tag/value (UID, DA, TM, CS multi-value modalities, PN object form, IS counts), WADO `RetrieveURL` (`00081190`) points at the right `/dicom-web/pacs/BCH/...` path per level, empty optional attributes omitted. | no | 6 |
| `tests/test_multipart.py` | `multipart/related` parser (the STOW wire format): boundary extraction quoted/unquoted, missing-boundary raises; single-part byte round-trip is **exact**; multiple parts preserved in order; **binary payload with embedded CRLF survives** (the case naive parsers corrupt). | no | 6 |

The `query_parser` count includes the per-class methods across `ResolveAttr`,
`SingleValue`, `MultiValue`, `Range`, `Wildcard`, `Fuzzy`, `IncludeField`,
`Pagination`, `UnsupportedKey` test classes.

### 2.2 Helper / task modules (DB for the roll-up tests; no storage)

| File | What it asserts | DB? | Tests |
|---|---|---|---|
| `tests/test_tasks.py` → `HelperParseTests` | `_parse_dicom_date` (valid 8-char; bad/empty/None → None), `_parse_dicom_time` (6-char `HHMMSS`; **4-char `HHMM` and 2-char `HH` partials** — the bug Phase A caught; fractional seconds stripped; bad → None), `_as_int` (int/str/None/empty/garbage). | no | 7 |
| `tests/test_tasks.py` → `TaskImportSmokeTests` | `index_pacs_instance` is importable + named correctly (catches circular-import regression between `pacsfiles.serializers` and `dicomweb.tasks`); task is routed to Celery queue **`main2`** per `core/celery.py:task_routes`. | no | 2 |
| `tests/test_tasks.py` → `IndexerStudyRollupTests` | **(a)** `index_pacs_instance` (mocked storage returning synthetic bytes) creates `PACSInstance` **and** a `PACSStudy` with correct `NumberOfStudyRelatedSeries`/`Instances` and `ModalitiesInStudy` roll-ups; **(b)** variant-C `index_from_metadata(meta)` indexes purely from a tags dict with **no storage configured at all** (a pass proves it never reads the file); **(c)** `index_from_metadata` on an unregistered PACS returns `False` and indexes nothing. | yes | 3 |

### 2.3 HTTP / DB / storage modules — run inside a real CUBE checkout

`tests/test_views.py` builds a Patient→Study→Series→Instance tree for PACS `BCH` in
`setUpTestData` and authenticates as the seeded `chris` user via `force_authenticate`.
Tagged so CUBE's harness can select/exclude them: `qido`, `wado`, `stow`, and
`integration` (the storage round-trips).

| Class (tags) | What it asserts | DB? | Storage? | Tests |
|---|---|---|---|---|
| `QidoStudyTests` (`qido`) | `/studies`: requires auth (401/403); `application/dicom+json` content-type + body shape (`0020000D` study UID, `00201208` instance count = 2); empty filter → `200 []`; `PatientName=DOE*` wildcard matches; **`fuzzymatching=true` executes `__trigram_similar` against the real test DB** (proves the `0003` pg_trgm migration); malformed `StudyDate=-` → 400; `application/json` accepted as equivalent; unknown PACS → 404. | yes | no | 8 |
| `QidoSeriesInstanceTests` (`qido`) | `/studies/{s}/series` (`0020000E` series UID, `00201209` instance count); `/series/{s}/instances` (count = 2); cross-level `/series?00080060=CT,MR` multi-value filter; cross-study `/instances`; unknown study → 404. | yes | no | 5 |
| `WadoMetadataTests` (`wado`) | `/series/{s}/metadata` → `application/dicom+json`, 2 instances, **`PixelData` (`7FE00010`) referenced as `BulkDataURI`, not inlined**; unknown instance metadata → 404. | yes | no | 2 |
| `WadoRetrieveTests` (`wado`,`integration`) | Stages real DICOM bytes (8×8 native, 16-bit) into storage, then: `/frames/1` → `multipart/related; type=application/octet-stream` with one native frame of exactly `Rows*Cols*2` bytes; out-of-range frame → 404; `/bulkdata` → octet-stream; instance retrieve → `multipart/related; type=application/dicom` whose part payload contains the **`DICM`** PS3.10 magic; unknown instance → 404. | yes | **yes** | 5 |
| `StowTests` (`stow`,`integration`) | `POST /studies` of a `multipart/related` body → 200 with `00081199 ReferencedSOPSequence` (and `00081155` echoes the SOPInstanceUID), **no** `00081198`, and the `PACSInstance`+`PACSStudy` rows exist; POST to wrong `/studies/{study}` → **409** with `FailedSOPSequence` + `FailureReason 0xA901`; partial good/bad batch → **202** with both sequences; wrong content-type → **415**. | yes | **yes** | 4 |

**Totals.** The seven modules sum to roughly **97** test methods, which matches the
authoritative count from the live harness run (`97/97 pass`). The suite started at 91
and grew as the native-frames/bulkdata, fuzzy, metadata, and variant-C tests landed in
PRs #13 and #14.

### 2.4 The fixtures (`tests/fixtures.py`) — why the synthetic data is trustworthy

- `make_dataset(...)` builds an in-memory pydicom `Dataset` with every attribute the
  index/serializers read (Patient/Study/Series/Instance tags, optional native
  `PixelData`). No external sample data, no PACS needed — the tests are hermetic.
- `dataset_to_bytes(ds)` serializes to a **conformant PS3.10 stream** (128-byte
  preamble + `DICM` + File Meta + dataset) using the **pydicom 3.x** idiom
  `dcmwrite(..., enforce_file_format=True)` with encoding derived from
  `file_meta.TransferSyntaxUID`. This matters: it means the STOW and WADO-retrieve
  tests parse/emit exactly the byte format real C-STORE/STOW traffic carries (the
  `DICM` magic the retrieve test greps for is real, not faked).
- `build_multipart_related(...)` assembles the exact STOW wire body
  (`multipart/related; type="application/dicom"`), so the multipart and STOW tests
  exercise the same framing a real client (OHIF, dcm4che) sends.

---

## 3. The harness — how `just test dicomweb` actually runs

This is the answer to "did you run it against *real* CUBE, or a mock?" — **real CUBE.**

`just` is CUBE's own developer entry point (a `justfile` in
`FNNDSC/ChRIS_ultron_backEnd`). The flow `just test dicomweb` performs:

1. **Builds `cube:dev` from source.** A `docker build` of the CUBE image from the
   checkout's `Dockerfile`, including this spike's added dependency `pydicom>=3.0,<4.0`
   (Phase A validated the image builds clean — pydicom 3.0.2, no C-extension compile,
   no resolver conflicts).
2. **Starts the ancillary services** the test DB needs — notably a real **PostgreSQL**
   (CUBE requires Postgres; the spike relies on PG-specific features like
   `bulk_create` returning pks and the `pg_trgm` extension).
3. **Runs `manage.py test dicomweb`** inside the container. Django's test runner
   creates a throwaway **test database** and **applies all migrations** into it —
   including this spike's `dicomweb/0001_initial` (PACSInstance), `0002_pacsstudy`
   (PACSStudy + the nullable `PACSSeries.study` FK), and **`0003_pg_trgm`** which runs
   `TrigramExtension()` (`CREATE EXTENSION pg_trgm`) and adds the trigram GIN index on
   `PACSStudy.PatientName`. `CREATE EXTENSION` needs a superuser DB role — the CUBE
   dev/test Postgres is one, so this succeeds in the harness.
4. **Tag selection.** `--exclude-tag integration` runs the fast subset (encoders,
   parser, multipart, QIDO/WADO *metadata* — no storage). The full `just test dicomweb`
   additionally runs the `integration`-tagged storage round-trips (WADO byte streaming,
   STOW store) against the real `core.storage` backend.
5. **Eager Celery.** Test settings run Celery tasks eagerly
   (`CELERY_TASK_ALWAYS_EAGER`), so the indexer roll-up tests execute the task body
   synchronously; the `transaction.on_commit` auto-index hook is a deliberate no-op
   inside atomic `TestCase`s (callbacks don't fire there), which keeps the unit suite
   broker-independent.

**Why this is strong evidence.** The tests are not run against stubs of the CUBE
models — they import `core.models.ChrisFolder`, `pacsfiles.models.{PACS,PACSSeries,
PACSFile}`, the real auth chain, and the real storage manager. A green run means the
spike's schema, URL wiring, permissions, renderers, and storage I/O all cooperate with
genuine CUBE. To make this reproducible, CUBE and miniChRIS-docker are pinned as git
**submodules** (PRs #9 and #7), so the harness builds against a known CUBE revision.

**Standalone subset.** The four framework-free modules were additionally executed in a
plain Python venv (no CUBE, no DB): **61 passed**. That is the portable proof that the
encoding/parsing logic is correct independent of any infrastructure.

---

## 4. The live end-to-end matrix

Beyond the automated suite, the endpoints were driven over **real HTTP against a
running CUBE stack** (CUBE on `:8000`). Crucially, the data under test arrived by
**both** real ingestion paths: oxidicom **C-STORE** (a DICOM SCP push) and **STOW**
POST. So this is not "we inserted rows and read them back" — the rows were produced by
the same machinery a production deployment uses.

| Service | Endpoint | Method | Result observed | What it proves |
|---|---|---|---|---|
| QIDO | `/studies` | GET | 200 + DICOM JSON study list | study-level roll-up surfaces ingested data |
| QIDO | `/studies/{s}/series` | GET | 200 + series list | series hierarchy navigable |
| QIDO | `/studies?ModalitiesInStudy=...` | GET | 200 filtered | modality aggregate filter works |
| QIDO | `/studies?PatientName=...*` | GET | 200 filtered | wildcard matching live |
| QIDO | `/studies?...&fuzzymatching=true` | GET | 200 filtered | pg_trgm trigram path executes on real PG |
| WADO | `/.../metadata` | GET | 200 `application/dicom+json` | metadata with `BulkDataURI` for pixels |
| WADO | `/.../frames/{n}` (native) | GET | 200 octet-stream frame | native frame slicing of real pixel data |
| WADO | `/.../bulkdata` | GET | 200 octet-stream | native bulkdata retrieval |
| WADO | `/.../instances/{sop}` | GET | 200 `multipart/related; type="application/dicom"` | full instance retrieve streams stored bytes |
| STOW | `/studies` | POST | 200 + `ReferencedSOPSequence` | store accepts + persists, returns conformant response |

Every cell above corresponds to an automated test in `test_views.py` *and* was
confirmed by hand over the wire. The live run is what lets Marty say "I watched it
return the right thing on a real stack," not merely "the test asserts it."

---

## 5. The two "prove the hard part" demonstrations

These are the demonstrations most likely to draw a curve-ball, because they sound too
good ("it just appears?" / "it works without the file?"). Here is the exact reasoning
that makes each one airtight — i.e., why the observation *cannot* be explained any
other way.

### 5.1 Real-time auto-index (oxidicom → QIDO with no manual reindex)

**Claim.** A brand-new PACS sends DICOM by C-STORE and the study shows up in QIDO
automatically — no `reindex_pacs_instances` run, no human step.

**What was observed.** A fresh PACS calling AE-Title `PROOFSCU`:
- `GET /dicom-web/pacs/PROOFSCU/studies` → **404** (PACS unknown / no data) *before*.
- An oxidicom C-STORE push of one study.
- `GET .../studies` → **1 study** *after*, with no intervening manual command.
- The **worker-mains** log showed `index_pacs_instance ... succeeded`.

**Why it's airtight.** oxidicom does **not** register files through the REST
`PACSSeriesSerializer` that Phase A hooked — it registers `PACSFile` rows via its own
NATS → Celery `register_pacs_series` path. So the only thing that could have indexed
the new study automatically is the `post_save(PACSFile)` receiver in `signals.py`
(`dispatch_uid='dicomweb_autoindex_pacsfile'`), which fires on every newly created
`.dcm` `PACSFile` and queues the idempotent `index_pacs_instance` task inside
`transaction.on_commit`. The presence of the `index_pacs_instance ... succeeded` line
in the worker log (the worker consumes queues `main1,main2`; the indexer is routed to
`main2` — locked by `test_task_routed_to_main2`) is the smoking gun: the task ran as a
side effect of ingest, with no operator action. The 404→1-study transition with
nothing in between rules out a manual reindex.

### 5.2 Variant C (index from oxidicom-pushed tags, with the file *absent*)

**Claim.** The index can be populated from a tags message alone — CUBE never re-reads
the `.dcm`.

**What was observed.** A parsed-tags event was published to NATS on subject
`oxidicom-meta.<pacs>.<series>` for a PACS whose `.dcm` **does not exist in storage**.
`consume_dicomweb_index` (the NATS subscriber) consumed it and called
`index_from_metadata`. `GET .../studies` then returned the study — with a value
(`PUSHED^META`) that was present **only in the message**. The storage path was
confirmed **absent**, so a re-read was physically impossible.

**Why it's airtight.** Three independent facts converge:
1. The file is not in storage — so `index_pacs_instance` (the re-read path) would have
   failed its `storage.download_obj`; it cannot be what populated the row.
2. The returned study carried `PUSHED^META`, a value that exists nowhere except the
   NATS payload — so the data demonstrably came from the message, not from any prior
   re-read or seeded row.
3. The unit test `test_index_from_metadata_no_file_read` reinforces this: it calls
   `index_from_metadata` with **no storage backend configured or mocked at all**; the
   fact that it passes proves the code path never touches a file.

This is the architecture doc's **D1** recommendation in miniature: an *extended*
oxidicom publishes tags it already parsed in Rust during C-STORE, and a small
in-network consumer (`consume_dicomweb_index`) upserts the index with no Python-side
DICOM parsing and no storage I/O. **Honest boundary:** only the **consumer** side is
built and proven; oxidicom does **not yet emit** this tag event (its current LONK NATS
carries progress counts only). See §7.

---

## 6. The bug ledger — 11 bugs found and fixed, by the layer that caught each

This is the most persuasive evidence that the testing was *real* and not theater: each
bug surfaced only at a deeper layer of verification, and several were impossible to
catch any earlier. The pattern — bugs that only appear against real CUBE, or only over
live HTTP — is exactly what you want to be able to point at. Fixes are cited to the
merged PRs in git history.

### 6.1 Deployment / Ansible layer (caught running the actual bring-up)

| # | Bug | How found | Fix | PR |
|---|---|---|---|---|
| 1 | Playbook wouldn't start — a removed `yaml` callback was still referenced | Running `ansible-playbook` (it errored at startup) | Drop the dead callback config | #6 (`fix/deploy-runnability`) |
| 2 | `prereqs` role needed a non-interactive interpreter (`auto_silent`) | Bring-up prompted / failed under automation | Set the interpreter discovery to `auto_silent` | #6 |
| 3 | A `wait_for` TCP probe against oxidicom's DICOM port **crashed oxidicom** (panic: *"Unknown association ULID"*) — the bare TCP connect looked like a malformed DICOM association | oxidicom panicked when the playbook probed it | Stop TCP-probing the DICOM port that way | #6 |
| 4 | CUBE port hardcoded to 8000 | Deploying with a different port | Make the CUBE port configurable (compose override `cube-port.yml`) | #8 (`fix/overlay-wiring-port-verify`) |
| 5 | Overlay copied to the wrong path; container missing `pydicom`; app not wired into `INSTALLED_APPS`/urls; overlay step had to run as **root** | Overlay applied but app didn't load / import-errored in the running container | Fix overlay target path, install pydicom, wire `INSTALLED_APPS` + urls, run the `docker cp` step as root | #8 |
| 6 | `end_play` short-circuited the **verify** role (smoke test never ran) | Playbook finished "green" but had skipped verification | Keep the verify role running to the end | #8 |

The deploy bugs all share a signature: they could only be found by *actually running
the bring-up against a live stack*. A dry-run or lint would not have surfaced the
oxidicom panic or the missing-in-container pydicom.

### 6.2 L2 application-code layer (caught only against real CUBE / live HTTP)

| # | Bug | How found — the layer that caught it | Fix | PR |
|---|---|---|---|---|
| 7 | Async indexer populated `PACSInstance` but **not `PACSStudy`**, so QIDO `/studies` was blind to all oxidicom-ingested data (STOW path always did the roll-up; the indexer didn't) | Only surfaced against **real CUBE** with oxidicom data — the `/studies` endpoint returned empty for ingested studies | Indexer now find-or-creates `PACSStudy` + refreshes roll-up counters (`test_indexer_creates_pacsstudy_and_rollups` locks it) | #11 (`fix/indexer-populates-pacsstudy`) |
| 8 | Test fixture created a `chris` user that **collided** with CUBE's seeded `chris` (a data-migration creates it) → `IntegrityError` | Only surfaced **inside a real CUBE test DB** (the seed user exists there, not in a bare test DB) | Fixtures `get_or_create(username='chris')` and `force_authenticate` instead of `create_user` | #10 (`fix/test-views-chris-user`) |
| 9 | `reindex_pacs_instances` used a broken relative import (`from ..`) | Running the management command in the real app package | Correct the import | #12 (`fix/stow-csrf-and-reindex-import`) |
| 10 | **STOW POST returned CSRF 403** — the plain-`View` STOW dispatchers weren't `csrf_exempt` | Only surfaced via a **live HTTP POST** (DRF's `APITestCase` client doesn't enforce CSRF the same way; a real browser/curl POST does) | `csrf_exempt` the STOW dispatchers | #12 |
| 11 | `__trigram_similar` lookup was **unregistered** — having the `pg_trgm` *extension* alone is insufficient; Django needs the lookup/index registered too, or fuzzy queries error at the DB | Running a real `fuzzymatching=true` query against Postgres | `0003_pg_trgm` migration registers `TrigramExtension()` **and** the GIN trigram index; query parser emits `__trigram_similar` | #13 (`feat/prove-frames-fuzzy-autoindex`) |

**Talking point for Marty.** Bugs #7, #8, #10, #11 are the headline ones: each was
*invisible* to unit tests and to a bare Django test DB. #7 needed oxidicom data in real
CUBE; #8 needed CUBE's seeded user; #10 needed a real HTTP POST; #11 needed real
Postgres. They are precisely the class of defect that "we wrote some unit tests" never
finds — and they were found because the work was driven all the way to a live stack.

(Phase A separately caught a 12th, earlier bug — the `_parse_dicom_time('1430')`
greedy-`strptime` bug that returned `14:03` instead of `14:30`; fixed by dispatching
the format on input length and locked by `test_parse_dicom_time_partial`. See
PHASE_A_IMPLEMENTATION.md §7. It predates this session's ledger but is the same kind of
signal.)

---

## 7. NOT proven — honest boundaries

State these proactively. Claiming more than this is the fastest way to lose
credibility in the room.

1. **Compressed / encapsulated WADO frames & bulkdata are NOT supported.** `/frames`
   and `/bulkdata` work for **native (uncompressed) transfer syntaxes only** — they
   slice raw pixel octets. Encapsulated/compressed syntaxes return **501**. So do
   `/rendered` and `/thumbnail`. This needs `pylibjpeg`/`gdcm` transcoding, which is
   deliberately out of scope for the spike (README Known-limitations §1).
2. **No transcoding in WADO retrieve.** Stored Transfer Syntax only; a request for a
   different specific syntax → 406. Each multipart part's `Content-Type` omits an
   explicit `transfer-syntax=` MIME parameter (OHIF/dcm4che tolerate this; PS3.18 says
   clients SHOULD receive it). (README §2.)
3. **oxidicom does NOT yet EMIT the variant-C tag event.** Only the CUBE **consumer**
   (`consume_dicomweb_index` + `index_from_metadata`) is built and proven. The
   end-to-end variant-C path requires a (small, well-scoped) change to oxidicom to
   publish the tags it already parses — that change is **not** done. What's proven is
   that *if* oxidicom emits it, the consumer indexes it correctly (§5.2).
4. **`includefield` is a no-op at the response layer.** Parsed and validated (no 400),
   but serializers always emit the full indexed attribute set (a superset of the QIDO
   required set — conformant per §10.6.3). It cannot surface a tag CUBE doesn't index
   (e.g. `StudyID`, `InstitutionName`). (README §3.)
5. **QIDO empty/universal matching (`?Tag=`) is rejected (400), not honored.** (§4.)
6. **STOW rejects objects with no `StudyDate`.** Upstream `PACSSeries.StudyDate` is
   NOT-NULL; objects lacking it (some SRs) land in `FailedSOPSequence` (`0xA700`)
   rather than being stored. Most image objects carry it. (README §8.)
7. **STOW `PACSFile`/`ChrisFolder` creation is simplified** vs. the real ingest path
   (no wait-for-files, no FileGroup/FolderGroup permission grants). Production STOW
   should share `PACSSeriesSerializer.create` logic. (README §9.)
8. **The `pacsfiles` companion migration (the `study` FK) is illustrative.** In a real
   checkout you must let `makemigrations` author it to match the live migration graph
   (README §7; the included `0002_pacsstudy.py` is hand-illustrative).
9. **Not run through CUBE's upstream CI; not a merged upstream PR.** This is an **L2
   test implementation** for a stakeholder spike. It passes in a real CUBE checkout via
   `just test`, but it has not gone through FNNDSC's CI or review. No conformance /
   capabilities document (PS3.18 §8.9) yet; drf-spectacular exclusion of these
   non-collection+json views is still a to-do. (README §13–15.)

---

## 8. "How do you KNOW it works?" — curve-ball Q&A

Short, specific answers Marty can give. Each ends with the evidence to cite.

**Q: Are these just unit tests, or did you run it against real ChRIS?**
A: Both. The 97-test suite runs inside a **genuine ChRIS_ultron_backEnd checkout** via
CUBE's own `just test dicomweb` — it builds `cube:dev` from source, stands up real
Postgres, applies our migrations into a real test DB, and exercises the real auth chain
and storage backend. On top of that I drove the endpoints over **live HTTP** against a
running stack on `:8000`. So it's unit + integration-in-real-CUBE + live e2e.

**Q: 97 out of how many — and did any fail?**
A: 97/97 pass. It started at 91 and grew as I added native-frames/bulkdata, fuzzy
matching, metadata, and the variant-C tests. The pure-logic core (encoders, multipart,
query parser, serializers — 61 tests) also passes standalone in a plain venv.

**Q: Where did the test data come from? Did you just insert rows?**
A: No. The data came from the two real ingestion paths: oxidicom **C-STORE** pushes and
**STOW** POSTs. The synthetic DICOM in the unit tests is built with pydicom into
**conformant PS3.10 bytes** (real preamble + `DICM` magic), so STOW/WADO tests parse
and emit the exact byte format a real client sends.

**Q: How do you know oxidicom data actually reaches QIDO?**
A: I watched a brand-new PACS (`PROOFSCU`) go from **404** to **1 study** on a single
C-STORE push with **no manual reindex**, and the worker log showed
`index_pacs_instance ... succeeded`. oxidicom doesn't use the REST ingest path, so the
only thing that could have indexed it is our `post_save(PACSFile)` signal — which is
exactly what the log confirms fired. (Bug #7 is why this didn't work at first: the
indexer wasn't populating `PACSStudy`; fixed in PR #11.)

**Q: The "index without reading the file" thing sounds like a trick. Prove it.**
A: I published a tags message to NATS for a PACS whose `.dcm` **doesn't exist in
storage**, and QIDO then returned that study — including a value (`PUSHED^META`) that
existed *only* in the message. A re-read was physically impossible (no file). The unit
test `test_index_from_metadata_no_file_read` reinforces it: it runs with **no storage
configured at all** and still indexes. Caveat: only the CUBE consumer side is built —
oxidicom doesn't yet *emit* that event.

**Q: Does fuzzy matching really work, or just parse?**
A: Really works against real Postgres. The query parser emits `__trigram_similar` (unit
test `test_fuzzy_pn_uses_trigram`), and the DB test `test_studies_patientname_fuzzy`
executes it against the live test DB. It required the `0003_pg_trgm` migration to both
`CREATE EXTENSION pg_trgm` **and** register the trigram lookup + GIN index — having the
extension alone wasn't enough (that was bug #11).

**Q: Did testing actually catch anything, or did it all pass first try?**
A: It caught **11 bugs** this session (plus an earlier one in Phase A), and the
important ones were only catchable at the layer I ran. STOW CSRF (#10) only showed up on
a real HTTP POST. The `chris`-user collision (#8) only showed up in CUBE's seeded test
DB. The empty `/studies` (#7) only showed up with oxidicom data in real CUBE. Fuzzy
(#11) only showed up against real Postgres. That's the proof the testing was real:
shallow testing finds none of those.

**Q: What WON'T it do today?**
A: Compressed/encapsulated frames, bulkdata, rendered and thumbnail (all 501 — needs
JPEG transcoding). WADO retrieve doesn't transcode. `includefield` doesn't trim the
response. Empty universal matching is rejected. STOW needs a `StudyDate`. And it hasn't
gone through CUBE's upstream CI — it's an L2 spike, not a merged PR. (Full list in §7.)

**Q: Could a reviewer reproduce your run?**
A: Yes. CUBE and miniChRIS-docker are pinned as git **submodules** (PRs #9 and #7), so
`just test dicomweb` builds against a known revision. The fixtures are synthetic
(pydicom-generated), so no external sample data is required for the suite.

**Q: Is the deployment proven too, or just the code?**
A: The Ansible bring-up was run and hardened — six deploy bugs (#1–#6) were found and
fixed *by running it*, including one where a naive TCP probe **crashed oxidicom**. The
overlay (`docker cp` the `dicomweb` app into the running CUBE container, migrate,
restart) and the smoke test (`deploy/ansible/scripts/smoke.sh`) work; see KB
`13-deployment` once written and `deploy/ansible/`.

---

## 9. Quick-reference: claim → evidence

| Claim | Primary evidence |
|---|---|
| 97/97 pass in real CUBE | `just test dicomweb` run this session; `tests/` (§2) |
| 61 pure-logic tests pass standalone | venv run; `test_dicomjson/_multipart/_query_parser/_serializers.py` |
| QIDO studies/series/instances + matching | `QidoStudyTests`, `QidoSeriesInstanceTests`; live e2e (§4) |
| Fuzzy matching works on real PG | `test_fuzzy_pn_uses_trigram` + `test_studies_patientname_fuzzy` + migration `0003_pg_trgm` |
| WADO metadata with BulkDataURI | `WadoMetadataTests.test_series_metadata` |
| Native frames/bulkdata | `WadoRetrieveTests.test_frames_native_octet_stream`, `test_bulkdata_native_octet_stream`; live e2e |
| Instance retrieve (multipart, DICM) | `WadoRetrieveTests.test_retrieve_instance_multipart`; live e2e |
| STOW store + 200/202/409/415 | `StowTests` (4 tests); live e2e |
| Auto-index on oxidicom ingest | `signals.py`; 404→1 study live; worker log `index_pacs_instance ... succeeded`; PR #11 |
| Variant-C no-re-read indexing | `test_index_from_metadata_no_file_read`; live `PUSHED^META` with file absent; PR #14 |
| Indexer routed to `main2` | `test_task_routed_to_main2` |
| 11 bugs found + fixed | PRs #6, #8, #10, #11, #12, #13, #14 (§6) |
| Deploy actually runs | Ansible run; PRs #6, #8; `deploy/ansible/` + `scripts/smoke.sh` |
| Not proven: compressed/rendered, oxidicom emit, upstream CI | §7; README Known-limitations §1, §13–15 |
