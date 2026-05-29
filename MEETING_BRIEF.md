# MEETING BRIEF — DICOMweb on CUBE (ARPA-H ATLAS)

> **Marty's cheat sheet** to be the technical expert in the BCH stakeholder meeting.
> Prime: Gradient Health · Partner: Boston Children's Hospital (BCH/FNNDSC) · Program: ARPA-H ATLAS.
> Deliverable: add **QIDO-RS + WADO-RS + STOW-RS** (DICOMweb, DICOM PS3.18) to **CUBE**, the Django
> backend of ChRIS. Deployment **wraps** FNNDSC/miniChRIS-docker.
>
> Everything here is grounded in the KB. Deep dives: [`knowledge-base/00-INDEX.md`](knowledge-base/00-INDEX.md).
> Unfamiliar term? [`knowledge-base/00-glossary.md`](knowledge-base/00-glossary.md).
> **Do not bring internal-review docs into the room** (per [08](knowledge-base/08-l2-architecture-decisions.md) D5).

---

## 1. Executive narrative (what / why / current state)

- **What:** Give CUBE a standards-compliant DICOMweb interface — **query** (QIDO-RS), **retrieve**
  (WADO-RS), and **store** (STOW-RS) — so any standard DICOM client (OHIF, 3D Slicer, Weasis,
  Gradient Health's indexer) can talk to a ChRIS site over the DICOM web protocol instead of
  CUBE-specific routes. ([05](knowledge-base/05-dicomweb-qido-wado-stow.md) §0)
- **Why:** It's the contractual ATLAS Month-12 deliverable (TA2 **§2.6.1.6**: "DICOMweb WADO-RS,
  STOW-RS, and QIDO-RS endpoints operational within TD constraints"), and it's the clean federation
  seam — one auth-aware DICOMweb endpoint per site under the planned ATLAS gateway (§2.7.1.2).
  ([08](knowledge-base/08-l2-architecture-decisions.md) D3, D5)
- **Current state:** CUBE has **18 PACS endpoints** under `/api/v1/pacs/` in `collection+json`, but
  **no DICOMweb**: no instance-level row, no DICOM-JSON renderer, no QIDO query parser, no WADO
  multipart surface, no STOW upload. **Phase A is already shipped** — it added the schema + ingest
  foundation (the `dicomweb` app, the `PACSInstance` model, 6 new `PACSSeries` columns, a Celery
  indexer), zero new endpoints, zero schema drift, all tests green. The remaining work is the
  **view layer** (Phases B–C). ([02](knowledge-base/02-cube-and-pacs-data-model.md) §5–§6;
  [RESEARCH_TICKET_OUTPUT.md](proposal-to-bch/RESEARCH_TICKET_OUTPUT.md))

**One-liner:** *"CUBE already stores the DICOM and (after Phase A) indexes every instance; we're adding
the standard HTTP face on top of it, in Django, where the auth already lives."*

---

## 2. The stack in 5 minutes (the mental model)

```
 Browser: ChRIS_ui (React + Cornerstone3D/Niivue)  ── REST/collection+json ──┐
                                                                             ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  CUBE  (ChRIS_ultron_backEnd, Django+DRF)  :8000                                │
 │   auth: Token / Basic / Session / LDAP      feed/plugin/pipeline model          │
 │   pacsfiles app (PACS/PACSSeries/PACSFile)   dicomweb app (PACSInstance + index) │
 └──┬───────────────┬──────────────────┬───────────────────────┬──────────────────┘
    │ Postgres      │ RabbitMQ:5672     │ NATS:4222 (LONK,      │ object storage
    │ (users/files/ │ (Celery main1/    │  progress only,       │ swift/s3/POSIX
    │  jobs+PACS)   │  main2/periodic)  │  NO tags)             │  unified /data tree
    │                                                            ▲ writes .dcm under
    │  COMPUTE: CUBE → pfcon:5005 → pman:5010 → Docker/K8s/SLURM │  SERVICES/PACS/
    │                                                  ┌─────────┴────────┐
 hospital PACS ◀── C-FIND/C-MOVE ── pfdcm:4005         │ oxidicom (Rust)  │
 (Orthanc in dev)  ────────── C-STORE push :11111 ────▶│ C-STORE SCP      │
                                                       └──────────────────┘
```

Say this out loud:

1. **CUBE (Django) is the brain.** REST API, the DB (Postgres), the feed/plugin/pipeline model, and
   the auth chain. ([01](knowledge-base/01-chris-architecture.md) §2)
2. **Plugins run as containers** via **pfcon** (file/job broker, :5005) → **pman** (scheduler shim,
   :5010) on any backend. Not on the DICOMweb critical path, but it's why ChRIS exists.
   ([01](knowledge-base/01-chris-architecture.md) §4)
3. **DICOM enters two ways:** *pushed* via **oxidicom** (Rust C-STORE SCP, :11111, AET `ChRIS`, writes
   `.dcm` to `SERVICES/PACS/`, emits LONK progress on NATS, enqueues `register_pacs_series` on Celery
   `main2`); or *pulled* via **pfdcm** (C-FIND/C-MOVE bridge to an upstream PACS).
   ([01](knowledge-base/01-chris-architecture.md) §5)
4. **All bytes live in one abstracted storage tree** (`core.storage` → swift/S3/POSIX). **WADO-RS just
   streams from there.** ([01](knowledge-base/01-chris-architecture.md) §5; [02](knowledge-base/02-cube-and-pacs-data-model.md) §1)
5. **`PACSSeries` is the central row today** (Patient+Study tags denormalized onto it). **Phase A added
   the missing instance level (`PACSInstance`).** DICOMweb is the view layer on top.
   ([02](knowledge-base/02-cube-and-pacs-data-model.md) §3, §6)

**Two traps to avoid saying wrong:**
- **NATS/LONK carries progress only — no DICOM tags.** Don't claim oxidicom already streams metadata.
  ([08](knowledge-base/08-l2-architecture-decisions.md) Background)
- **ChRIS_ui bundles Cornerstone3D + Niivue, not OHIF.** OHIF in this stack is served by the bundled
  *Orthanc* plugin only. ([01](knowledge-base/01-chris-architecture.md) §6; [03](knowledge-base/03-minichris-docker.md) §3.2)

---

## 3. Architecture recommendation — talking points

Source of record: [`knowledge-base/08-l2-architecture-decisions.md`](knowledge-base/08-l2-architecture-decisions.md).

### D1 — Where do the endpoints live? → **C (Hybrid), fallback B**

- **A (Django-only):** QIDO/WADO/STOW as DRF views; index via a Celery task that re-reads each `.dcm`
  with pydicom. **Weakest** — it re-reads files oxidicom already parsed (pure waste).
- **B (oxidicom-hosted):** endpoints in Rust; index inline at C-STORE. Fastest hot path, but must
  **reimplement CUBE's Token/Basic/Session/LDAP auth in Rust** and coordinate cross-repo migrations.
- **C (Hybrid, RECOMMENDED):** **endpoints stay in Django** (inherit the auth chain for free); oxidicom
  is **extended to publish its already-parsed tags as a *new* NATS event**; a small consumer in the
  CUBE network upserts `PACSInstance`/`PACSStudy`; the Phase A Celery indexer becomes the **fallback**
  for non-oxidicom files (STOW uploads, plugin outputs, S3 import).

**Be honest in the room:** C is *not* free reuse — oxidicom emits **no tags on NATS today** (LONK is
progress-only), so C depends on BCH agreeing to add that tag event. **If they won't, C degrades to A**
(the Celery `.dcm` re-read becomes the primary indexer). **B wins** only if oxidicom is confirmed the
*sole* ingestion path AND BCH will grow oxidicom's auth + HTTP serving.

> **The one factual question that swings D1:** *"Going forward, is oxidicom the only intended DICOM
> ingestion path into CUBE, or are other routes planned — STOW-RS uploads, S3 bulk import, plugin
> outputs writing into the PACS tree?"* → "only oxidicom" favors **B**; "others too" favors **C**.

### D2 — Hierarchy model → **explicit `PACSStudy` now; Patient stays implicit**

- GROUP-BY rollups were fine for a single-PACS demo, but `NumberOfStudyRelated*` becomes O(study size)
  per request and scans every `PACSSeries` row — fine at ~10⁴ series, seconds at ~10⁶ (grant scale).
- Recommended shape: `PACS ──< PACSStudy ──< PACSSeries ──< PACSInstance ──1:1── PACSFile`. Patient
  tags ride on `PACSStudy` (matches how QIDO returns Patient attrs at the Study level).
- **Honest cost:** new model + migration + denormalized counters to keep fresh; `PACSSeriesSerializer.create`
  grows a find-or-create-parent step. **Open data question for BCH:** are Patient tags consistent
  across all series of a study? (one query answers it.)

### D3 — STOW-RS scope → **IN (decided)**

- Matches grant §2.6.1.6 (all three ship together at Month 12). Phase A already supports it at the data
  layer. STOW is itself a *non-oxidicom* ingestion path — which is why D3=IN nudges D1 toward **C**.

### D4 — Fuzzy/wildcard matching → **`pg_trgm` from day one**

- Substring (`*DOE*`) and fuzzy `PN` matching need a Postgres trigram GIN index; enabling it is a
  one-line `TrigramExtension()` migration. Architecture-independent; cheap now, expensive to retrofit.

### Phasing

| Phase | Scope | Status | Gated by |
|---|---|---|---|
| **A** | `dicomweb` app, `PACSInstance`, 6 `PACSSeries` cols, Celery indexer | **DONE** (zero schema drift) | — |
| **B** | `PACSStudy` (D2), `pg_trgm` (D4), query parser, DICOM-JSON renderer | pending | mostly arch-independent |
| **C** | QIDO + WADO + STOW endpoints | pending | **D1 + D3** |
| **D** | reindex backfill command, OHIF smoke test, integration tests | pending | D1 |
| **E** | OpenAPI exclusions, README, perf check on BCH dataset | pending | — |

**Lock D1 + D3 before Phase C is sized** — a wasted Phase B is small; a wasted Phase C is not.

---

## 4. LIVE DEMO SCRIPT

Goal: stand up miniChRIS via the Ansible deploy, seed DICOM through Orthanc → oxidicom, and (when the
L2 overlay is enabled) hit QIDO/WADO/STOW. All commands/paths are from
[`deploy/ansible/README.md`](deploy/ansible/README.md) and `group_vars/all.yml`.

> **Honesty flag for the room:** the Ansible spec has **not** been executed end-to-end here, and the L2
> overlay is **off by default** because the Phase B/C view code is exactly what's being scoped. With the
> overlay off, the QIDO/WADO/STOW smoke checks report **SKIP** (not FAIL) — the endpoints genuinely
> don't exist yet. ([deploy README](deploy/ansible/README.md) caveats 1, 6)

### Step 0 — Prereqs (once)
Docker daemon running, Compose v2.6+, Ansible 2.14+, then:
```sh
cd deploy/ansible
ansible-galaxy collection install -r requirements.yml
pip install 'docker>=6'
```

### Step 1 — Bring up the wrapped stack + test Orthanc + seed data
```sh
ansible-playbook -i inventory.ini site.yml
```
This: asserts prereqs → clones & `docker compose up` miniChRIS with the **`pacs` profile** (CUBE +
compute + oxidicom + nats + bundled Orthanc + pfdcm) → waits for CUBE on `/api/v1/` → runs a **separate
test Orthanc** (`orthancteam/orthanc`, host ports **8142** HTTP / **4342** DIMSE, AET `SPIKEORTHANC`) →
loads sample DICOM into it → **C-STORE-pushes** each study to oxidicom (AET `ChRIS`, TCP `11111`).

Run phases individually with tags:
```sh
ansible-playbook -i inventory.ini site.yml --tags minichris      # just the stack
ansible-playbook -i inventory.ini site.yml --tags sample_data    # reload/seed data
ansible-playbook -i inventory.ini site.yml --tags verify         # just smoke tests
```
Use the **BCH dataset** instead of the public sample:
```sh
ansible-playbook -i inventory.ini site.yml \
  -e sample_data_mode=local_dir -e sample_data_local_dir=/path/to/bch/dicoms
```

### Step 2 — Show DICOM landed in CUBE (the real ingest path)
```sh
curl -u chris:chris1234 http://localhost:8000/api/v1/                       # CUBE liveness
curl -u chris:chris1234 -H 'Accept: application/json' \
  http://localhost:8000/api/v1/pacs/series/                                 # ingested series (≥1 SeriesInstanceUID)
curl -H 'Accept: application/dicom+json' http://localhost:8142/dicom-web/studies   # source Orthanc speaks DICOMweb
```
Storyline: Orthanc held the studies → C-STORE to oxidicom:11111 → oxidicom wrote `.dcm` to `/data` and
posted a job to RabbitMQ → CUBE's Celery worker created `PACSSeries` (and Phase A `PACSInstance`) rows.
([03](knowledge-base/03-minichris-docker.md) §6, §8; [07](knowledge-base/07-orthanc.md) §6.1)

### Step 3 — (Manual alt) push DICOM yourself
Load into the test Orthanc, then C-STORE-push to oxidicom:
```sh
curl -u orthanc:orthanc -X POST -H 'Expect:' http://localhost:8142/instances --data-binary @slice.dcm
curl -u orthanc:orthanc -X POST http://localhost:8142/modalities/ChRIS/store -d '<orthanc-study-uuid>'
# or bypass Orthanc entirely with DCMTK:
storescu -aec ChRIS localhost 11111 +sd +r /path/to/dicom/dir
```
([03](knowledge-base/03-minichris-docker.md) §8; [07](knowledge-base/07-orthanc.md) §6.1)

### Step 4 — Enable the L2 overlay, then hit QIDO/WADO/STOW
Flip the toggle (overlays `implementation/dicomweb-l2/` into the running `chris` + `worker`
containers, migrates `PACSStudy` + `pg_trgm`, restarts):
```sh
ansible-playbook -i inventory.ini site.yml -e dicomweb_overlay_enabled=true
```
Then (note the per-PACS root `/dicom-web/pacs/<id>/`; `<id>` = `PACS.identifier`, which for
oxidicom-ingested data is **`ChRIS`**):
```sh
# QIDO-RS — list studies as DICOM JSON
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/ChRIS/studies?PatientName=*&limit=5'

# WADO-RS — study-level metadata (DICOM JSON, no pixels)
curl -u chris:chris1234 -H 'Accept: application/dicom+json' \
  'http://localhost:8000/dicom-web/pacs/ChRIS/studies/<StudyUID>/metadata'

# WADO-RS — retrieve an instance as multipart/related
curl -u chris:chris1234 -H 'Accept: multipart/related; type="application/dicom"' \
  'http://localhost:8000/dicom-web/pacs/ChRIS/studies/<S>/series/<Se>/instances/<I>' --output instance.multipart

# STOW-RS — store (prefer a real client; curl -F does NOT produce multipart/related)
#   python: DICOMwebClient("http://localhost:8000/dicom-web/pacs/ChRIS").store_instances([ds])
```
The smoke test (`deploy/ansible/scripts/smoke.sh`, also `CUBE_USER=chris CUBE_PASSWORD=chris1234
./scripts/smoke.sh`) then exercises these instead of SKIP. ([deploy README](deploy/ansible/README.md);
[05](knowledge-base/05-dicomweb-qido-wado-stow.md) §2–§4; [08](knowledge-base/08-l2-architecture-decisions.md) "example calls")

### Step 5 — Conformance cross-check & teardown
Diff CUBE's QIDO/WADO output against Orthanc's `/dicom-web/` for the same study (Orthanc is the
behavioral oracle); point OHIF at both. ([07](knowledge-base/07-orthanc.md) §6.3)
```sh
./scripts/teardown.sh                  # remove test Orthanc + miniChRIS down -v (destroys volumes)
KEEP_VOLUMES=1 ./scripts/teardown.sh   # keep miniChRIS volumes
```

---

## 5. What I built and what it proves (and its limits)

**Built:** a reviewable Django `dicomweb` app at [`implementation/dicomweb-l2/`](implementation/dicomweb-l2/)
implementing all three services on the Phase A foundation — a drop-in for `chris_backend/dicomweb/` and
the overlay source the Ansible role copies into the running CUBE container.
([README](implementation/dicomweb-l2/README.md))

- **QIDO-RS** — all six resource paths; keyword/8-hex tag matching, multi-value OR, UID lists, date/time
  ranges, string-VR wildcards, `includefield` (incl. `=all`), `fuzzymatching`, `limit`/`offset`; `200`+`[]`
  empty, `400` malformed, `413` over ceiling, `406` bad `Accept`.
- **WADO-RS** — study/series/instance retrieve as `multipart/related; type="application/dicom"` streamed
  from `core.storage` (no transcoding); `/metadata` at all levels with `PixelData` via `BulkDataURI`.
- **STOW-RS** — `POST studies[/{study}]`, pydicom-parses each part, find-or-creates
  `PACSStudy`/`PACSSeries`/`PACSFile`/`PACSInstance`, returns the Store Instances Response
  (`ReferencedSOPSequence`/`FailedSOPSequence`/`FailureReason`) with `200`/`202`/`409`/`400`/`415`.

**What it proves:** the design is real and **runs**. The full suite (**95/95**) was executed inside a real
`ChRIS_ultron_backEnd` checkout (Django + Postgres + `pacsfiles`), and a **live stack served QIDO/WADO/STOW
over real HTTP** — including **real-time auto-indexing** (oxidicom C-STORE → a brand-new PACS surfaced in
QIDO `/studies` with no manual reindex), **fuzzy PN matching** (`pg_trgm`), and **native frame/bulkdata
retrieval**. It reuses CUBE's auth and storage verbatim — **no new auth code**.

**Honest limits — STATE THESE** ([README "Known limitations"](implementation/dicomweb-l2/README.md)):
1. This is an **L2 test implementation, not yet a merged CI-green CUBE PR** — validated against a real CUBE
   checkout + live stack (above), but not yet run through CUBE's own CI / submitted upstream.
2. **`/frames` + `/bulkdata` work for NATIVE (uncompressed) transfer syntaxes** (raw octets, proven live);
   **compressed/encapsulated** frames/bulkdata and `/rendered` + `/thumbnail` return `501` — transcoding
   needs `pylibjpeg`/`gdcm`, deliberately out of scope.
3. **No WADO transcoding** — stored Transfer Syntax only; a different requested syntax → `406`.
4. **`includefield` is a no-op at the response layer** (always emits the full indexed set, which is a
   conformant superset); it can't surface a tag CUBE doesn't index (e.g. `StudyID`).
5. **QIDO empty/universal match (`?Tag=`) → `400`** (MVP scope).
6. **STOW rejects objects with no `StudyDate`** (upstream column is NOT NULL) → reported in
   `FailedSOPSequence` (`0xA700`); edge case for some SRs.
7. **STOW `PACSFile`/folder creation is simplified** vs the full ingest path; production STOW should route
   through `PACSSeriesSerializer.create`'s logic. Study roll-up counters refreshed at STOW time only here.
8. **Wildcards use `__iregex`; fuzzy uses `__trigram_similar`** — needs the `pg_trgm` index (D4) to be
   efficient/work at scale.
9. **No conformance/capabilities doc** (PS3.18 §8.9) yet; drf-spectacular must exclude these views.

---

## 6. Likely stakeholder questions (Q&A)

**Q1. What exactly is in scope?** All three endpoints — **QIDO-RS, WADO-RS, STOW-RS** — implemented and
tested, deployed by wrapping miniChRIS-docker. STOW is **decided IN** (matches grant §2.6.1.6). The
deeper view layer is Phases B–C; Phase A (schema+ingest) is already shipped.
([08](knowledge-base/08-l2-architecture-decisions.md) D3)

**Q2. Why is STOW in scope when the May-1 MVP framing deferred it?** Because the grant's contractual
language (TA2 §2.6.1.6) puts all three under one Month-12 deliverable; we reconciled to the grant.
Phase A already supports it at the data layer. ([08](knowledge-base/08-l2-architecture-decisions.md) D3)

**Q3. Where do the endpoints physically live — Django or oxidicom?** Recommendation **C (hybrid)**:
endpoints in **Django/CUBE** (for the existing auth chain), indexing fed by a **new** oxidicom→NATS tag
event consumed in the CUBE network, Phase A's Celery indexer as fallback. Fallback to **B** (Rust
endpoints in oxidicom) only if oxidicom is the sole ingestion path. ([08](knowledge-base/08-l2-architecture-decisions.md) D1)

**Q4. Why not just put it all in oxidicom (Rust) — it's faster?** The serving hot path would be faster,
but oxidicom would have to **reimplement CUBE's Token/Basic/Session/LDAP auth** and we'd coordinate
migrations across two repos on every change. Worth it only if oxidicom is the single ingestion path.
([08](knowledge-base/08-l2-architecture-decisions.md) D1; [02](knowledge-base/02-cube-and-pacs-data-model.md) §2)

**Q5. What's the one decision you need from us?** *"Is oxidicom the only intended DICOM ingestion path
going forward, or are STOW/S3-import/plugin-output paths also planned?"* "Only oxidicom" → B; "others
too" → C. ([08](knowledge-base/08-l2-architecture-decisions.md) D1)

**Q6. How do you handle security / auth?** The DICOMweb endpoints **inherit CUBE's existing auth chain
unchanged** (Token/Basic/Session, LDAP-backed) and the `pacs_users` permission group — read for any
`pacs_users` member, write/STOW for the `chris` superuser. No new auth code. We are **not** doing auth
re-architecture, de-identification, or security hardening in this scope — those scope-and-defer.
([02](knowledge-base/02-cube-and-pacs-data-model.md) §2, §3.6; [08](knowledge-base/08-l2-architecture-decisions.md) D5)

**Q7. Will this perform at grant scale?** Yes, with two choices: an **explicit `PACSStudy`** (so study
rollups are cached counters, not O(study) GROUP-BY scans per request) and **`pg_trgm` GIN indexes** for
substring/fuzzy `PatientName`. A `PACSInstance` table at ~10⁶ rows is ~250 MB — Postgres-trivial; we'd
do a real perf check on the BCH dataset in Phase E. ([08](knowledge-base/08-l2-architecture-decisions.md) D2, D4)

**Q8. How does fuzzy/wildcard matching work?** PS3.4 fixes which VRs allow wildcards (PN, LO, SH, CS, LT,
ST, UC, UR, AE). Prefix `DOE*` → B-tree `ILIKE`; substring `*DOE*` and `fuzzymatching=true` on `PN` need
`pg_trgm` (one-line migration). UIDs use list matching, dates/times use range matching — not wildcards.
([04](knowledge-base/04-dicom-standard.md) §4; [05](knowledge-base/05-dicomweb-qido-wado-stow.md) §2.4; [08](knowledge-base/08-l2-architecture-decisions.md) D4)

**Q9. What about the C-FIND/C-MOVE path (MOC) — does DICOMweb replace it?** No. `PACSQuery`/`PACSRetrieve`
via **pfdcm** are CUBE's *outbound pull* from upstream PACS (CUBE-as-client). DICOMweb replaces the
*consumer* side (CUBE-as-server); the pull bridge stays. ([02](knowledge-base/02-cube-and-pacs-data-model.md) §3.5)

**Q10. How do we know it's conformant? OHIF / 3D Slicer compatibility?** **Orthanc is our conformance
oracle** — we diff CUBE's QIDO/WADO output byte-for-byte against Orthanc's `/dicom-web/` for the same
study and point the same `dicomweb-client` harness and OHIF at both. Caveat: pixel **rendering** needs
WADO `/frames`, which is a `501` stub today — OHIF will list studies and read metadata but not render
until frames land. ([07](knowledge-base/07-orthanc.md) §4, §6.3; [implementation README](implementation/dicomweb-l2/README.md) limitations)

**Q11. Is OHIF already wired into ChRIS?** No — ChRIS_ui bundles **Cornerstone3D + Niivue**, not OHIF (the
OHIF you see in the stack is the bundled *Orthanc* plugin). The win is that Cornerstone's
`@cornerstonejs/dicom-image-loader` is *already* a WADO-RS client, so a standard WADO surface on CUBE is
consumable by it and by any third-party DICOMweb viewer. ([01](knowledge-base/01-chris-architecture.md) §6)

**Q12. Does this re-read every file from disk? Isn't that slow?** Phase A's indexer reads headers only
(`stop_before_pixels=True`), ~5–20 ms/file, on Celery `main2` so it never starves plugin jobs. The
*recommendation* (variant C) avoids even that for oxidicom files by reusing oxidicom's already-parsed
tags via the new NATS event; pydicom re-read remains only as the fallback for non-oxidicom files.
([06](knowledge-base/06-pydicom.md) §1; [02](knowledge-base/02-cube-and-pacs-data-model.md) §6.4; [08](knowledge-base/08-l2-architecture-decisions.md) D1)

**Q13. What about existing/already-ingested data — does it become queryable?** A backfill management
command (`reindex_pacs_instances`, Phase D) walks existing `PACSFile`s and populates
`PACSInstance`/`PACSStudy`. It's stubbed in the L2 app; production runs it once after migration.
([implementation README](implementation/dicomweb-l2/README.md) "How to apply"; [02](knowledge-base/02-cube-and-pacs-data-model.md) §6.6)

**Q14. What's the timeline?** ~5–6 weeks, 1 engineer, for the single-PACS L2 MVP (Phases B–E; A done).
That's L2; L3 is "operational within TD constraints" + a regression suite at grant Month 12/15.
([08](knowledge-base/08-l2-architecture-decisions.md) D5; [RESEARCH_TICKET_OUTPUT.md](proposal-to-bch/RESEARCH_TICKET_OUTPUT.md))

**Q15. Why wrap miniChRIS instead of forking or using Helm?** miniChRIS is the canonical, version-pinned
arrangement of CUBE + the full PACS ingest pipeline; wrapping it keeps upstream pins authoritative and
the DICOMweb work layered on top. It's demo-grade (hard-coded secrets) — production would use the
`fnndsc/charts` Helm path and rebuild the CUBE image with the `dicomweb` app baked in.
([03](knowledge-base/03-minichris-docker.md) §1, §9; [deploy README](deploy/ansible/README.md))

**Q16. Does adding `PACSStudy` break existing data / the ingest path?** It's additive: a new model + a
nullable `PACSSeries.study` FK + a find-or-create-parent step in the ingest serializer. The risk is
denormalized-counter drift and the open question of **Patient-tag consistency across a study's series** —
one query on the BCH dataset answers it. ([08](knowledge-base/08-l2-architecture-decisions.md) D2)

---

## 7. Risks & honest unknowns

- **D1 hinges on a BCH answer.** If BCH won't extend oxidicom with a tag-carrying NATS event, variant C
  collapses to A (Celery re-read as primary). Frame this as "tell us the ingestion-path policy and we'll
  pick B vs C." ([08](knowledge-base/08-l2-architecture-decisions.md) D1)
- **Pixel rendering is not done.** WADO `/frames`, `/bulkdata`, `/rendered`, `/thumbnail` are `501` stubs;
  needs a `pylibjpeg`/`gdcm` decode path. OHIF browses metadata but won't render until then.
  ([implementation README](implementation/dicomweb-l2/README.md) limitation 1)
- **The L2 code's HTTP/DB path is unrun in a live stack.** Core logic validated standalone; full
  integration needs a CUBE checkout + the new migrations. The Ansible playbook is also a spec authored
  ahead of a first real run — expect to iterate on image tags / sample-dataset shape.
  ([implementation README](implementation/dicomweb-l2/README.md) limitation 13; [deploy README](deploy/ansible/README.md) caveat 1)
- **Patient-tag consistency across a study** is an unverified data assumption that affects `PACSStudy`
  find-or-create. ([08](knowledge-base/08-l2-architecture-decisions.md) D2)
- **STOW `StudyDate` NOT-NULL** edge case rejects some SR objects; production needs a default or a column
  relax. ([implementation README](implementation/dicomweb-l2/README.md) limitation 8)
- **De-identification / anonymization** will come up at BCH (clinical data) but is **out of scope** here —
  flag as a follow-on, don't refuse. ([06](knowledge-base/06-pydicom.md) §8.1; [08](knowledge-base/08-l2-architecture-decisions.md) D5)
- **Demo throttle:** oxidicom runs with `OXIDICOM_DEV_SLEEP=150ms` in miniChRIS — drop it before any
  throughput measurement on the BCH dataset. ([03](knowledge-base/03-minichris-docker.md) §9)

---

*Cross-references: full index [`knowledge-base/00-INDEX.md`](knowledge-base/00-INDEX.md) ·
glossary [`knowledge-base/00-glossary.md`](knowledge-base/00-glossary.md) ·
decisions [`knowledge-base/08-l2-architecture-decisions.md`](knowledge-base/08-l2-architecture-decisions.md) ·
implementation [`implementation/dicomweb-l2/README.md`](implementation/dicomweb-l2/README.md) ·
deploy [`deploy/ansible/README.md`](deploy/ansible/README.md).*
