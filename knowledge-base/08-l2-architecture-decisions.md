# L2 Architecture Decisions & Recommendations

**Audience:** ISC engineer carrying the DICOMweb work into the BCH stakeholder meeting.
**Purpose:** A self-contained decision-record reference for the open architectural questions this meeting must resolve, grounded in the prior spike artifacts in this repo. Every recommendation here is ISC's position; each carries the reasoning so you can defend it live and absorb pushback without losing the thread.

**What "L2" means.** Per `internal-review/ISC_DELIVERABLES.md`, the engagement is layered:
- **L1 — research spike** (DONE): the write-up of what's missing and how to do it (`CURRENT_API.md`, `QIDO_PLAN.md`, Phase A code).
- **L2 — MVP implementation** (NOT STARTED, ~1–2 months, 1 engineer): working DICOMweb endpoints on a single CUBE instance + the BCH dataset + an inference plugin running through.
- **L3 — grant §2.6.1.6** (Month 12 / Month 15, $109k, joint with Red Hat): WADO-RS + STOW-RS + QIDO-RS operational within TD constraints + a regression suite.
- **L4 — broader pipeline** (aspirational).

The decisions in this document are the ones L2 must lock before the Phase C view layer is sized, because Phase C's shape depends entirely on them (`internal-review/REVIEW_RESPONSE_ROUND_1.md` §10). Phase A is already shipped and is **forward-compatible with every option below** (`proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`, "Phase A's footprint survives all three architectural variants").

---

## 0. Shared vocabulary (read this first)

These definitions are from `internal-review/REVIEW_RESPONSE_ROUND_1.md` §1 and are the agreed terms so the room stops talking past each other:

> **DICOM metadata.** The tag/value pairs in a DICOM Part 10 file's header and any sequence-nested datasets. Patient, Study, Series, Instance, SOP-class attributes. Excludes pixel data.
>
> **Indexing.** Parsing those tags out of the on-disk files *once*, at ingest time, into a structured representation (Postgres tables) that can be queried directly — so we never re-read the `.dcm` files to answer a query.
>
> **QIDO-RS.** The HTTP+JSON spec (DICOM PS3.18 §10.6) for *querying* a metadata index. Defines URL paths, query parameters, and the DICOM JSON Model response format. Knows nothing about how the index is stored.

The pipeline is: **files → metadata → index → DICOMweb endpoints.** "Move QIDO-RS to oxidicom" means *where the HTTP endpoints are served*, not *where the metadata lives* (that's always the index = the Postgres DB). Source: PS3.18 §10.6 (https://dicom.nema.org/medical/dicom/current/output/html/part18.html#sect_10.6).

The three DICOMweb services:

| Service | Verb | What it does | PS3.18 §        |
|---|---|---|---|
| **QIDO-RS** | Query | Search the metadata catalog (Studies / Series / Instances), return DICOM JSON Model | §10.6 |
| **WADO-RS** | Retrieve | Fetch the actual DICOM objects, metadata, frames, rendered images, thumbnails | §10.4 |
| **STOW-RS** | Store | Push new DICOM instances into the archive | §10.5 |

Reference index: https://www.dicomstandard.org/using/dicomweb

---

## Decision summary (the cheat-sheet)

| # | Decision | ISC recommendation | Fallback | Blocks Phase C? |
|---|---|---|---|---|
| D1 | Where do the DICOMweb endpoints live? | **C — Hybrid** (oxidicom is *extended* to emit parsed tags on NATS → consumer in CUBE network indexes → endpoints in Django) | **B — oxidicom-hosted**, if oxidicom is confirmed the *only* ingestion path | **Yes** |
| D2 | Hierarchy model | **Explicit `PACSStudy` model** with denormalized counts; Patient stays implicit | GROUP-BY rollups (only if pure-MVP, throwaway) | Yes |
| D3 | STOW-RS in scope? | **IN** (decided — matches grant §2.6.1.6) | n/a | Yes (sizes Phase C) |
| D4 | Fuzzymatching / wildcards | **Supported day-one via `pg_trgm`** | Prefix-only B-tree (defer substring/fuzzy) | No (architecture-independent) |
| D5 | Layering | L1→L2→L3→L4 (research → MVP → grant → pipeline) | n/a | Framing only |

The single factual question that swings D1 (and partly D2): **"Going forward, is oxidicom the only intended DICOM ingestion path into CUBE, or are other routes planned (STOW-RS, S3 bulk import, plugin outputs into the PACS tree)?"** (`RESEARCH_TICKET_OUTPUT.md` open item #1; `REVIEW_RESPONSE_ROUND_1.md` §6.)

---

## Background: the relevant moving parts in the stack today

From `proposal-to-bch/CURRENT_API.md` ("Auxiliary moving parts") and the ChRIS architecture docs (https://chrisproject.org/docs/architecture):

```
                         DICOM C-STORE (TCP :11111)
   remote PACS / scanner ───────────────────────────► oxidicom (Rust SCP)
                                                          │
                                  writes .dcm under       │ publishes ingest-progress
                                  SERVICES/PACS/<name>/    │ events on NATS
                                  in CUBE storage          ▼
                                       │              ┌─────────┐
                                       │              │  NATS   │  broker
                                       ▼              └────┬────┘
                              ┌──────────────────┐         │ relayed to clients via
   researcher / OHIF ────────►│  CUBE (Django/DRF)│◄────────┘ /api/v1/pacs/sse/
   (Token/Basic/Session/LDAP) │  + Postgres + Celery
                              └──────────────────┘
                                       ▲
                                       │ C-FIND / C-MOVE bridge
                                  pfdcm (:4005)  ── pulls from upstream PACS into CUBE's store
```

Key facts to keep straight (all from `CURRENT_API.md`):
- **oxidicom** (image `ghcr.io/fnndsc/oxidicom:3.0.0`, host port **11111**) is the Rust C-STORE SCP. It receives DICOMs, writes them under `SERVICES/PACS/<pacs>/` in CUBE storage, parses DICOM tags at receive time, and on association completion dispatches a Celery task that CUBE's worker consumes to register the received series into the database. Described upstream as "a high-performance DICOM receiver for the ChRIS backend (CUBE)" implementing a C-STORE SCP (https://chrisproject.org/docs/oxidicom).
- **CUBE's `POST /api/v1/pacs/series/`** is the registration callback path; it is *not* a researcher-facing endpoint and *not* DICOMweb STOW-RS.
- **NATS** is the broker oxidicom uses to publish **DICOM-reception *progress*** to CUBE/ChRIS_ui. Critically, those messages are *progress-only* — the **LONK** ("Light Oxidicom NotifiKations") encoding on NATS carries exactly three message kinds: `done` (`0x00`), `progress` (`0x01` + a little-endian u32 file count), and `error` (`0x02` + UTF-8 string). **It does not carry any DICOM tags or metadata** (https://chrisproject.org/docs/oxidicom/lonk; LONK-WS is the same payload as JSON over WebSocket: https://chrisproject.org/docs/oxidicom/lonk-ws). The NATS subject pattern is `oxidicom.{pacs_name}.{SeriesInstanceUID}.ndicom` (dots in the UID replaced by underscores). CUBE's `/api/v1/pacs/sse/` SSE stream subscribes to those subjects. **This is *not* a tag-carrying bus today** — see the D1 correction below: Option C requires oxidicom to be *extended* with a new tag-carrying event, it cannot simply reuse the existing progress bus.
- The compose image versions in the spike stack (from `miniChRIS-docker/docker-compose.yml`, https://github.com/FNNDSC/miniChRIS-docker): `chris` = `ghcr.io/fnndsc/cube:6.11.0` (and the same image runs the `worker`, `worker_periodic`, `scheduler` services), `nats` = `docker.io/library/nats:2.11.4-alpine`, `db` = `docker.io/library/postgres:17`, `queue` = `docker.io/library/rabbitmq:3` (Celery broker). The Celery broker is **RabbitMQ (port 5672), not NATS** — NATS is only the LONK progress bus.
- **CUBE auth chain:** `TokenAuthentication` + `BasicAuthentication` + `SessionAuthentication`, with LDAP wired via `users.models.CustomLDAPBackend`. Permission class for PACS read is `IsChrisOrIsPACSUserReadOnly` (membership in the `pacs_users` Django group).
- **miniChRIS-docker** is the compose distribution that bundles the stack (https://github.com/FNNDSC/miniChRIS-docker). Service names and host ports (verified against `docker-compose.yml`): `chris` = CUBE API + Django admin, both under `:8000` (the admin is the `/chris-admin/` path on the same service, not a separate port); `chris_ui` `:8020`; `orthanc` `:8042` (HTTP/DICOMweb) and `:4242` (DIMSE C-STORE); `pfcon` `:5005`; `pman` `:5010`; `pfdcm` `:4005`; `oxidicom` `:11111`; `nats` `:4222`; `queue` (RabbitMQ) `:5672`; `chrisomatic` (no host port). **Deployment for this spike must WRAP this stack**, not fork it.

---

## D1 — Where do the DICOMweb endpoints live?

This is the largest decision and the one that determines *which BCH team(s) touch the work*. Three variants (`RESEARCH_TICKET_OUTPUT.md` "Where DICOMweb endpoints live"; `REVIEW_RESPONSE_ROUND_1.md` §2).

### The three options

```
 OPTION A — Django only                OPTION B — oxidicom hosts             OPTION C — Hybrid (RECOMMENDED)
 ┌───────────────────────┐             ┌───────────────────────┐            ┌────────────────────────┐
 │ oxidicom: C-STORE only │            │ oxidicom: C-STORE +    │            │ oxidicom: C-STORE +    │
 └──────────┬────────────┘             │  QIDO/WADO/STOW HTTP + │            │  NEW tag-carrying event│
            │ writes .dcm              │  parse→write Postgres  │            │  (must be BUILT; the   │
            ▼                          │  + reimplement auth    │            │  current NATS bus is   │
 ┌───────────────────────┐            └──────────┬────────────┘             │  progress-only/LONK)   │
 │ CUBE Celery task:      │                       │ DML                      └──────────┬─────────────┘
 │  re-read .dcm w/pydicom│                       ▼                                     │ new event
 │  → index Postgres      │            ┌───────────────────────┐             ┌──────────▼─────────────┐
 │ CUBE Django: endpoints │            │ Postgres (CUBE owns)   │             │ consumer svc (CUBE net)│
 └───────────────────────┘            └───────────────────────┘             │  upsert PACSInstance/  │
                                        Django: DB owner, no                 │  PACSStudy rows        │
                                        DICOMweb code                        └──────────┬─────────────┘
                                                                                        │
                                                                             ┌──────────▼─────────────┐
                                                                             │ CUBE Django: QIDO/WADO/│
                                                                             │  STOW endpoints + auth │
                                                                             │ (Celery = fallback     │
                                                                             │  indexer for non-oxi   │
                                                                             │  ingest paths)         │
                                                                             └────────────────────────┘
```

- **A — Django (CUBE only).** QIDO/WADO/STOW as DRF views in `chris_backend/dicomweb/`. Indexing is a Celery task that reads `.dcm` headers with pydicom (this is exactly what Phase A built). Existing CUBE auth covers DICOMweb with zero new code.
- **B — oxidicom (Rust only).** Endpoints in oxidicom. Indexing happens inline during C-STORE (oxidicom already has the parsed tags in memory). oxidicom serves HTTP+JSON; CUBE stays the Postgres store. Requires oxidicom to grow an auth layer matching CUBE's chain, and migrations coordinated across two repos.
- **C — Hybrid.** oxidicom is **extended to publish the parsed tag set on NATS** as a *new* event type. **Important correction:** oxidicom does *not* emit DICOM tags on NATS today — its current NATS traffic (LONK) is strictly reception *progress* (`done`/`progress`/`error` with a file count), not metadata (https://chrisproject.org/docs/oxidicom/lonk). So Option C is not a free reuse of an existing tag stream; it requires a new oxidicom feature: serialize the already-parsed-in-memory tags into a new NATS subject/payload. A small consumer service inside the CUBE compose network subscribes to that new event and upserts `PACSInstance`/`PACSStudy` rows. QIDO/WADO/STOW endpoints stay in Django because that's where auth and the API surface already live. The Phase A Celery task becomes a **fallback** indexer for any non-oxidicom-sourced files. (Note: because the tag event must be built either way, the "shared contract" with the oxidicom/BCH team is a *real* new artifact, not zero-cost — see the recommendation caveats below.)

### Tradeoff table

Adapted from `REVIEW_RESPONSE_ROUND_1.md` §2:

| Concern | A: Django-only | B: oxidicom | C: Hybrid |
|---|---|---|---|
| Re-read files at index time? | **Yes** (pydicom in Celery) | No — tags in memory at C-STORE | No — tags handed off on NATS (via a **new** tag event oxidicom must add) |
| QIDO/WADO serving speed | Python/Postgres | Rust/Postgres (fastest hot path) | Python/Postgres (= A) |
| Auth surface | Existing CUBE chain (free) | **Reimplement** Token/Basic/Session/LDAP in Rust, or proxy | Existing CUBE chain (free) |
| Services with DML on CUBE's DB | 1 (Django) | 2 (Django + oxidicom), cross-repo migrations | 1 (Django; consumer co-owned) |
| Coupling to oxidicom team (BCH) | None | **Heavy** — every change is a cross-repo PR | Light-to-medium — a **new** tag-carrying NATS event must be built in oxidicom (one cross-repo feature + an event-schema contract), then only that schema is shared ongoing |
| Cost if "oxidicom-only?" is *no* | Zero (indexer fires on any `PACSFile`) | Medium (other paths need their own route) | Medium → patched by fallback Celery indexer |
| Federation gateway (grant §2.7.1.2) | One auth-aware endpoint per TD | Talk to oxidicom directly + its auth story | One auth-aware endpoint per TD (= A) |
| What of Phase A survives | All | Model + fields; Celery task replaced | All except Celery task |
| Engineers comfortable | Django people (us) | Rust people (oxidicom/BCH) | Mostly Django; one Rust touchpoint |

### Recommendation: **C (Hybrid), with fallback to B if oxidicom is confirmed the only ingestion path.**

Reasoning (`RESEARCH_TICKET_OUTPUT.md`; `REVIEW_RESPONSE_ROUND_1.md` §2, §5):

1. **Don't re-read files.** oxidicom already parses headers during C-STORE. Paying pydicom + storage I/O per file from Python is wasted work. B and C both eliminate it; A does not. This is the efficiency argument that motivated reopening the original Django-only plan.
2. **Auth lives in one place.** CUBE's LDAP-backed Token/Basic/Session chain is non-trivial to reproduce in Rust. A and C inherit it for free; B reimplements it (or sits behind a CUBE auth proxy).
3. **Minimize cross-repo coordination.** B couples ISC (on CUBE) to the oxidicom team (BCH) on *every* change. C requires **one** cross-repo feature up front — oxidicom must learn to publish its parsed tags as a new NATS event (today it only publishes progress/LONK, no tags: https://chrisproject.org/docs/oxidicom/lonk) — after which the only ongoing shared contract is that event schema. So C is *not* zero-coupling, but its coupling is a single bounded contract versus B's continuous cross-repo surface. **Be honest about this in the room:** if BCH cannot commit to adding a tag event to oxidicom, Option C collapses toward Option A (the Celery `.dcm` re-read becomes the *primary* indexer, not a fallback).
4. **Federation-friendly.** The "ATLAS DICOMweb gateway" (grant §2.7.1.2) sits above per-TD endpoints. Talking to one auth-aware endpoint per TD is cleaner than talking to a sibling oxidicom service.
5. **Degrades gracefully.** If non-oxidicom ingestion paths exist (STOW-RS uploads, plugin outputs into the PACS tree, S3 bulk import), C's fallback Celery indexer covers them; B does not naturally.

**When B wins:** if oxidicom is genuinely the only DICOM ingestion path AND the BCH team has appetite to extend oxidicom with auth + HTTP serving. Then the one-time auth reimplementation is worth it and the design is cleanest. This is exactly why D1 is conditional on the factual question below.

**Why A is the weakest:** even ignoring the "Django/Celery is scary at scale" concern, A is the only option that re-reads every file from storage when oxidicom already parsed it. That's pure waste (`REVIEW_RESPONSE_ROUND_1.md` §2 "My read").

### The question to ask in the room

> *"Going forward, is oxidicom the only intended ingestion path for DICOM into CUBE, or do you plan other routes — STOW-RS uploads, S3 bulk import, plugin outputs writing into the PACS tree?"*

- **"Only oxidicom"** → B gets cleaner; the auth reimplementation is worth doing once.
- **"Others too"** → C wins; the NATS consumer indexes oxidicom files and the fallback Celery task covers the rest.

Note the current reality (`REVIEW_RESPONSE_ROUND_1.md` §6): in practice oxidicom is the only path that produces PACS data today, but it is **not enforced** at the file/folder model layer — `POST /api/v1/pacs/series/` could in principle be called by any `pacs_users` member with a `chris` token. So "only oxidicom" is a *policy* statement, not a code-enforced invariant.

---

## D2 — Hierarchy model: GROUP-BY rollups vs explicit `PACSStudy`

DICOM is naturally **Patient → Study → Series → Instance** (`REVIEW_RESPONSE_ROUND_1.md` §3). The question is whether that hierarchy is *explicit in the schema* or *derived at query time*.

### Where CUBE is today

`PACSSeries` (in `pacsfiles/models.py`) is the only row. It carries Patient + Study + Series tags collapsed onto one table, unique on `(pacs, SeriesInstanceUID)` (`CURRENT_API.md` "PACSSeries model — current tag coverage"). There is **no instance-level row and no study-level row** in stock CUBE.

Phase A already added **`PACSInstance`** (one row per `.dcm`, FK to `PACSSeries`, 1-to-1 with `PACSFile`) — needed under every option (`PHASE_A_IMPLEMENTATION.md`). The open question is the Study level.

### The two options

**Option D2-a — GROUP BY `PACSSeries`** (the original `QIDO_PLAN.md` §1 lock):
- Study-level results computed per request by aggregating series rows that share a `StudyInstanceUID`.
- `NumberOfStudyRelatedSeries` = `Count('id')`; `NumberOfStudyRelatedInstances` = `Count('instances')`; `ModalitiesInStudy` = `ArrayAgg('Modality', distinct=True)` (`QIDO_PLAN.md` §7.2).
- No new model, no migration churn, fastest to ship.

```python
# QIDO_PLAN.md §7.2 — study list under GROUP BY
qs.values('StudyInstanceUID', 'StudyDate', 'StudyTime', 'AccessionNumber',
          'StudyDescription', 'PatientID', 'PatientName', 'PatientBirthDate', 'PatientSex')
  .annotate(ModalitiesInStudy=ArrayAgg('Modality', distinct=True),
            NumberOfStudyRelatedSeries=Count('id', distinct=True),
            NumberOfStudyRelatedInstances=Count('instances'))
  .order_by('-StudyDate', 'StudyInstanceUID')
```

**Option D2-b — explicit `PACSStudy` model** (the reviewed recommendation):
- One row per Study within a PACS. Carries Study-level + Patient-level tags plus **denormalized** `NumberOfStudyRelatedSeries` / `NumberOfStudyRelatedInstances` counters.
- `PACSSeries` gains an FK to `PACSStudy`.
- `PACSSeriesSerializer.create` does find-or-create of the parent `PACSStudy` at ingest, with tag-consistency checks across series sharing a study.

### Recommendation: **explicit `PACSStudy` now; Patient stays implicit.**

Reasoning (`RESEARCH_TICKET_OUTPUT.md` "Indexing model"; `REVIEW_RESPONSE_ROUND_1.md` §3):

- GROUP BY was the right call for "ship a single-PACS demo next month." It is the **wrong** call for grant Month-12 scale. Grant §2.6.1.6 / §2.7.1 reference multi-TD federation, and the GH indexer (Phase 2) wants to index across many ChRIS instances — both push past where GROUP BY is free.
- **Cost of GROUP BY at scale:** `NumberOfStudyRelatedInstances` / `…Series` become O(study size) per request — fine at ~10⁴ series, seconds at ~10⁶ (`QIDO_PLAN.md` §12.3; revisit threshold: first `/studies` request >500 ms). The study-level GROUP BY scans every `PACSSeries` row in the PACS.
- **Complex queries** (wildcards + fuzzymatch on Patient attributes) are easier against explicit entities than against denormalized-and-deduplicated series rows.
- **Patient stays implicit:** Patient tags live on `PACSStudy`, which matches how QIDO **Study Result Attributes** return Patient tags (e.g. `PatientName`, `PatientID`, `PatientBirthDate`, `PatientSex`) at the Study level — the Study search response payload is PS3.18 §10.6.3.3, Table 10.6.3-3 (https://dicom.nema.org/medical/dicom/current/output/html/part18.html). Promote to a `PACSPatient` model only if a concrete query demands it.

```
RECOMMENDED SHAPE
   PACS ──< PACSStudy ──< PACSSeries ──< PACSInstance ──1:1── PACSFile (storage)
            (Study+         (Series tags,    (SOP-class/instance,
             Patient tags,   FK→Study)        geometry, xfer-syntax;
             counts)                          Phase A; needed in all options)
```

### The real cost (be honest about it in the room)

From `REVIEW_RESPONSE_ROUND_1.md` §3 and `RESEARCH_TICKET_OUTPUT.md`:
- `PACSSeriesSerializer.create` (the oxidicom registration callback) is entangled in the CUBE/Django/Celery flow already; adding find-or-create-parent logic is bounded but real work ("a pain in the ass" per the reviewer).
- New model = new migration + a new source of denormalization drift (the counters).
- Tests across `pacsfiles/` need updating.
- **Open data question** (`RESEARCH_TICKET_OUTPUT.md` open item #3): do real CUBE-imported datasets show *consistent Patient tags across all series of the same Study*? If not, find-or-create needs deterministic conflict resolution (e.g. `MIN`). One query against an existing BCH dataset answers this — worth raising.

**Synergy with D1:** under Option C, *if* oxidicom's new tag event is designed to carry the full Patient/Study/Series/Instance tag sets, the consumer can write to all the right rows directly — the explicit hierarchy is then *easier* to maintain than in pure Django/Celery (`REVIEW_RESPONSE_ROUND_1.md` §3). This synergy is contingent on that event being built (oxidicom emits no tags on NATS today, only LONK progress: https://chrisproject.org/docs/oxidicom/lonk); it is a design goal for the event schema, not a property of the current system.

---

## D3 — STOW-RS scope: **decided IN**

### The history (so you can explain the change cleanly)

There was a real variance in ISC's own framing (`internal-review/ISC_DELIVERABLES.md` L3 "Key reconciliation"):
- The **MVP proposal** (May 1) listed Precondition 1 as QIDO-RS + WADO-RS only — STOW-RS deferred.
- The **grant TA2 §2.6.1.6** lists all three under one $109k sub-task with a single Month-12 deliverable: *"DICOMweb WADO-RS, STOW-RS, and QIDO-RS endpoints operational within TD constraints."*

That was "the single biggest mismatch between ISC's own framing and the grant's contractual language."

### Decision: STOW-RS is **in scope** for this spike.

This matches the grant's contractual Month-12 deliverable and the DECIDED SCOPE for this spike (implement and test all three: QIDO-RS + WADO-RS + STOW-RS).

### What STOW-RS implies technically

- **STOW-RS** = `POST .../studies` (and `POST .../studies/{StudyInstanceUID}`) accepting `multipart/related; type="application/dicom"` (or `application/dicom+json` + bulkdata parts). Spec: PS3.18 §10.5 (https://dicom.nema.org/medical/dicom/current/output/html/part18.html#sect_10.5).
- **Phase A already supports it at the data layer.** The `PACSInstance` model and the new `PACSSeries` tags are the same rows STOW-RS would create as a side-effect of an upload (`PHASE_A_IMPLEMENTATION.md` Summary; `ISC_DELIVERABLES.md` L3 note). So D3 does *not* change Phase A — it changes Phase C view-layer sizing.
- **STOW-RS is itself a non-oxidicom ingestion path** — which is precisely why it strengthens the case for **D1 Option C** (the fallback indexer covers STOW-written files; pure Option B would need a STOW route into oxidicom). Resolving D3 = IN effectively pre-answers part of the D1 "are there other ingestion paths?" question with "yes."
- STOW-RS must write into the same storage tree (`SERVICES/PACS/<name>/`) and create the same `PACSStudy`/`PACSSeries`/`PACSInstance`/`PACSFile` rows the oxidicom path produces, so the catalog stays consistent regardless of ingestion route.

---

## D4 — Fuzzymatching & wildcards (pg_trgm)

QIDO-RS query parameters allow wildcards via `*` (matches zero-or-more characters) and `?` (matches a single character), and an optional `fuzzymatching=true` parameter (QIDO-RS query semantics, PS3.18 §8.3.4; https://dicom.nema.org/medical/dicom/current/output/html/part18.html). The set of VRs eligible for wild-card matching is fixed by PS3.4 §C.2.2.2.4: **AE, CS, LO, LT, PN, SH, ST, UC, UR, UT** (https://dicom.nema.org/medical/dicom/current/output/html/part04.html). Wild-card matching is *not* defined for date/time (DA, TM, DT — use range matching instead), UID (UI — use list-of-UID matching), or numeric VRs (IS, DS, US, SS, UL, SL, FL, FD).

### State of the plan

`QIDO_PLAN.md` §5.1 originally:
- **Prefix wildcards** (`DOE*`) → `ILIKE 'DOE%'`, served by a normal B-tree index. Already covered.
- **`fuzzymatching=true`** → "stub for MVP: log it, ignore it."

`REVIEW_RESPONSE_ROUND_1.md` §4 reopened this:
- **Substring wildcards** (`*DOE*` → `ILIKE '%DOE%'`) cannot use a B-tree index — they need a Postgres **trigram** index (`pg_trgm`).
- **Fuzzymatch** also wants `pg_trgm` with similarity scoring.
- Both use the *same* extension; planning for it now costs ~one migration line, retrofitting later costs much more.

### Recommendation: **support via `pg_trgm` from day one.**

```python
# one-line migration enabling the extension
from django.contrib.postgres.operations import TrigramExtension

class Migration(migrations.Migration):
    operations = [TrigramExtension()]
```

```python
# then a GIN trigram index on the columns we want substring/fuzzy search on
models.Index(fields=['PatientName'], opclasses=['gin_trgm_ops'], name='pacsseries_patientname_trgm')
```

Notes:
- This is **architecture-independent** — it lands the same under D1 A/B/C and does not block Phase C (`REVIEW_RESPONSE_ROUND_1.md` §9, §11). It's a Phase A/B migration addition.
- OHIF defaults to prefix matching, so the demo works even without trigram; the GH indexer (Phase 2) may want fuzzy/substring, which is the concrete reason to bake it in now (`QIDO_PLAN.md` §14.1 #3, §12.2).
- Sizing: `pg_trgm` GIN index on `PatientName` over ~10⁶ rows is Postgres-trivial (the `PACSInstance` table itself is ~250 MB at 10⁶ rows, `QIDO_PLAN.md` §12.1).

`pg_trgm` reference: https://www.postgresql.org/docs/current/pgtrgm.html

---

## D5 — The L1→L2→L3→L4 layering

This is framing, not an engineering choice, but it governs what you commit to in the room. Source: `internal-review/ISC_DELIVERABLES.md`, `internal-review/SCOPE_GUARDRAILS.md`.

| Layer | Scope | Status | Gating |
|---|---|---|---|
| **L1 — Research spike** | Write-up of what's missing + how + Phase A code | **DONE** (exceeds the "day or two" Alex sized) | none |
| **L2 — MVP implementation** | DICOMweb endpoints on one CUBE + BCH dataset re-DICOMized + inference plugin run-through. ~1–2 months, 1 engineer | Not started | BCH greenlight + funding clarity |
| **L3 — Grant §2.6.1.6** | QIDO+WADO+STOW operational within TD constraints (Month 12) + regression suite (Month 15). $109k, joint w/ Red Hat | Not started | ARPA-H grant approval |
| **L4 — Broader pipeline** | $1.39M §2.6.1 workstream + 8 other ranked opportunities | Aspirational | relationship + grant |

**The decisions in this document are L2 decisions.** They are forward-compatible with L3 because L3 is the same three endpoints "operational within TD constraints" plus a regression suite. The D2 explicit-`PACSStudy` and D4 `pg_trgm` recommendations are specifically the ones that pay off at L3/grant scale, not just at L2 demo scale.

**Scope-guardrail reminders for the room** (`SCOPE_GUARDRAILS.md`):
- ISC is **not** delivering security hardening / auth re-architecture / de-identification / OHIF customization. The DICOMweb endpoints **inherit** CUBE's existing auth chain unchanged — that's a feature of D1 options A and C.
- If a security or auth ask comes up: scope-and-defer, don't refuse. The one genuinely in-scope security touchpoint is anything specific to the *new* endpoints (auth wiring, CORS, rate-limiting on QIDO queries).
- Do **not** open `REVIEW_RESPONSE_ROUND_1.md` in the meeting — it's internal scaffolding. This document distills it for external use.

---

## Phasing: how the decisions sequence into work

From `QIDO_PLAN.md` §13 and `RESEARCH_TICKET_OUTPUT.md` "Sequencing." ~5–6 weeks total for the single-PACS demo, 1 engineer.

| Phase | Scope | Status | Depends on which decision? |
|---|---|---|---|
| **A — Schema + ingest** | `dicomweb` app, `PACSInstance`, 6 `PACSSeries` fields, Celery indexing task | **DONE** (103/103 existing + 9/9 new tests pass, zero schema drift) | none — survives all options |
| **B — Hierarchy + query layer** | Add `PACSStudy` (D2), add `pg_trgm` (D4), DICOM tag query parser, DICOM JSON Model renderer | Pending | D2, D4 — but mostly architecture-independent |
| **C — View layer** | QIDO-RS + WADO-RS + STOW-RS endpoints | Pending | **D1 + D3** (shape depends entirely on these) |
| **D — Backfill + integration** | Reindex management command, OHIF smoke test, integration tests | Pending | D1 |
| **E — Polish** | OpenAPI annotations/exclusions, README, perf check on BCH dataset | Pending | — |

**Critical-path note** (`REVIEW_RESPONSE_ROUND_1.md` §10): Phase B is *mostly* architecture-independent (renderer + query parser + `pg_trgm` + `PACSStudy` are the same shape under any D1 option), so it can start before D1 is locked. But Phase C's view layer **completely** depends on D1 and D3. A wasted Phase B is small; a wasted Phase C is not. **Lock D1 + D3 before Phase C is sized.**

---

## Talking points (verbatim-ready)

These are the escalation phrasings from `REVIEW_RESPONSE_ROUND_1.md` §10, adjusted for the decided STOW-RS scope:

> **1. Where do the DICOMweb endpoints live?**
> Three options: A (Django views in CUBE), B (Rust endpoints in oxidicom), C (hybrid — oxidicom is extended to emit its parsed tags as a *new* NATS event, a small consumer in the CUBE network indexes them, endpoints stay in Django for the auth chain). **ISC recommends C** for the auth and cross-repo-coordination reasons. **One caveat to state plainly:** oxidicom does not publish DICOM tags on NATS today — its NATS traffic (LONK) is reception-progress only — so C depends on BCH adding that tag event to oxidicom. If they won't, C degrades to A (Celery `.dcm` re-read as the primary indexer). **B is cleaner if oxidicom is the only intended ingestion path.**
>
> **2. Patient/Study/Series/Instance hierarchy in the data model?**
> ISC initially proposed GROUP-BY rollups for studies; on review we recommend **adding `PACSStudy` as an explicit model now** with denormalized counts. Patient-level entity stays implicit unless a concrete query needs it.
>
> **3. STOW-RS:** in scope, per grant §2.6.1.6 (all three endpoints ship together at Month 12). This is decided. It also means there *is* a non-oxidicom ingestion path, which informs question 1 toward C.
>
> **4. One factual question that swings #1:** going forward, is oxidicom the only intended DICOM ingestion path into CUBE, or do you plan other routes (STOW-RS, S3 bulk import, plugin outputs into the PACS tree)?

---

## Quick reference: example QIDO/WADO/STOW calls (target surface)

URL surface is per-PACS: `/dicom-web/pacs/<pacs_identifier>/…` where `<pacs_identifier>` matches `PACS.identifier` (e.g. `BCH`, `MINICHRISORTHANC`). Auth = existing DRF chain. (`QIDO_PLAN.md` §2.)

```sh
# QIDO-RS — list studies in a PACS, DICOM JSON Model response
curl -u chris:chris1234 \
  -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies?PatientName=DOE*&limit=5'

# QIDO-RS — drill to series within a study
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/<StudyInstanceUID>/series'

# WADO-RS — retrieve an instance (multipart/related; type="application/dicom") [Phase C]
curl -u chris:chris1234 \
  -H 'Accept: multipart/related; type="application/dicom"' \
  'http://localhost:8000/dicom-web/pacs/BCH/studies/<S>/series/<Se>/instances/<I>'

# STOW-RS — store instances into a study [Phase C, scope D3=IN]
curl -u chris:chris1234 -X POST \
  -H 'Content-Type: multipart/related; type="application/dicom"; boundary=BOUND' \
  --data-binary @body.multipart \
  'http://localhost:8000/dicom-web/pacs/BCH/studies'
```

Example DICOM JSON Model object (tag-hex-keyed, `vr` + `Value`; PS3.18 §F):

```json
{
  "0020000D": {"vr": "UI", "Value": ["1.2.840.113619.2.55.3.604688119.971.1437406488.926"]},
  "00100010": {"vr": "PN", "Value": [{"Alphabetic": "DOE^JANE"}]},
  "00100040": {"vr": "CS", "Value": ["F"]},
  "00080061": {"vr": "CS", "Value": ["CT", "MR"]},
  "00201206": {"vr": "IS", "Value": [3]},
  "00201208": {"vr": "IS", "Value": [482]}
}
```

---

## Sources

**This repo (authoritative for all recommendations):**
- `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` — A/B/C analysis, indexing model, sequencing, open items.
- `proposal-to-bch/QIDO_PLAN.md` — URL surface, schema changes, query parsing, GROUP-BY decision, capacity sizing.
- `proposal-to-bch/CURRENT_API.md` — current CUBE API surface, PACS deep dive, QIDO-RS gap analysis, auxiliary services.
- `proposal-to-bch/PHASE_A_IMPLEMENTATION.md` — what Phase A built (`PACSInstance`, 6 `PACSSeries` fields, Celery task), validations.
- `internal-review/REVIEW_RESPONSE_ROUND_1.md` — definitions, the A/B/C reopening, explicit-`PACSStudy`, pg_trgm, escalation phrasing.
- `internal-review/ISC_DELIVERABLES.md` — L1/L2/L3/L4 layering, STOW-RS scope reconciliation.
- `internal-review/SCOPE_GUARDRAILS.md` — out-of-scope items, scope-creep redirects.

**External (cited inline above):**
- DICOM PS3.18 (Web Services): https://dicom.nema.org/medical/dicom/current/output/html/part18.html — QIDO-RS §10.6 (Search; result payloads Tables 10.6.3-3/-4/-5), WADO-RS §10.4 (Retrieve), STOW-RS §10.5 (Store), query parameter semantics §8.3.4, DICOM JSON Model Annex F.
- DICOM PS3.4 (matching rules): https://dicom.nema.org/medical/dicom/current/output/html/part04.html — wild-card matching VR list, §C.2.2.2.4.
- DICOMweb overview: https://www.dicomstandard.org/using/dicomweb
- ChRIS architecture: https://chrisproject.org/docs/architecture
- oxidicom: https://chrisproject.org/docs/oxidicom; **LONK NATS protocol (progress-only, no tags): https://chrisproject.org/docs/oxidicom/lonk and https://chrisproject.org/docs/oxidicom/lonk-ws**; source: https://github.com/FNNDSC/oxidicom
- miniChRIS-docker (service names, images, ports): https://github.com/FNNDSC/miniChRIS-docker
- PostgreSQL pg_trgm: https://www.postgresql.org/docs/current/pgtrgm.html
- pydicom `dcmread` (param `stop_before_pixels: bool = False`): https://pydicom.github.io/pydicom/stable/reference/generated/pydicom.filereader.dcmread.html
