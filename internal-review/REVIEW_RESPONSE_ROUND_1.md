# Phase A review — response to round 1 comments

**Reviewer**: Adam McArthur (ISC)
**Date of comments**: 2026-05-22 (Slack, 11:28–12:00)
**Material reviewed**: `QIDO_PLAN.md`, `PHASE_A_IMPLEMENTATION.md`, the Phase A code in `code/source/` + `phase-a.patch`.

This document responds point-by-point. Two of Adam's items are large enough to be **reopened design decisions**, not refinements; one of those reopens a choice I had locked in `QIDO_PLAN.md` §1. I've tried to engage honestly with each point rather than defend the shipped Phase A — Phase A's data model survives all three architectural variants below, so reopening the architecture costs less than it might look.

---

## Summary of Adam's comments

1. **Definitions**: we need explicit shared vocabulary for "indexing", "metadata", and how both interplay with QIDO-RS.
2. **Architectural ownership**: consider having oxidicom (Rust) own QIDO-RS rather than CUBE/Django. CUBE remains the stateful store; oxidicom serves the API. Hybrid (oxidicom + a sibling microservice that reuses oxidicom's DICOM parsing) also on the table. Reason: Django/Celery is "scary" (read: slower at scale, foreign to engineers who live in Rust/Python plugin space) and oxidicom already owns the pure-DICOM elements.
3. **Hierarchy**: DICOMs naturally fit Patient → Study → Series → Instance. This should be **explicit in the data model**, not derived. Also need to index for complex queries (patient attributes, wildcards, fuzzymatch).
4. **Coupling concern**: PACSSeries is entangled in the CUBE/Django/Celery flow today, so adding a hierarchical model on top is "a pain in the ass" — flagged as cost of the explicit-hierarchy approach.
5. **Ingestion ownership** (question for BCH): is oxidicom the only DICOM ingestion path going forward, or are others planned?
6. **Methodology**: e2e test suite + AI-driven iteration (e.g. Codex) could handle the implementation well.

---

## 1. Definitions (Adam's point #1)

To put at the top of `QIDO_PLAN.md` so we stop talking past each other:

> **DICOM metadata.** The tag/value pairs in a DICOM Part 10 file's header and any sequence-nested datasets within it. Patient, Study, Series, Instance, and SOP-class attributes. Excludes pixel data.
>
> **Indexing.** Parsing those tags out of the on-disk files once, at ingest time, and storing them in a structured representation (Postgres tables, in our case) that can be queried directly. Avoids re-reading the .dcm files to answer queries.
>
> **QIDO-RS.** The HTTP+JSON specification (DICOM PS3.18 §10.6) for querying a metadata index. Defines URL paths (`/studies`, `/studies/{uid}/series`, `/instances`), query parameters (DICOM tag hex or keyword form, multi-value, ranges, wildcards), response format (DICOM JSON Model, `application/dicom+json`). Knows nothing about how the index is stored.

The chain is: **files → metadata → index → QIDO-RS endpoints**. Adam's "DICOM metadata stored in CUBE acts as the indexer" is exact — the metadata storage *is* the index. There is no separate "indexer" layer in any sensible design.

This vocabulary unlocks the architectural question below: when we say "move QIDO-RS to oxidicom", we mean *where do the QIDO-RS HTTP endpoints get served*, not "where does the metadata live" (that's the index = the DB).

---

## 2. Architecture — where do QIDO-RS endpoints live?

This is the biggest item. Three live options:

### Option A — Django (status quo of `QIDO_PLAN.md`, what Phase A is built toward)

- QIDO-RS endpoints implemented as DRF views in `chris_backend/dicomweb/`.
- Indexing task runs in Celery, reads `.dcm` headers via pydicom.
- Existing CUBE auth chain (Token / Basic / Session / LDAP) covers DICOMweb for free.

### Option B — oxidicom (Adam's primary proposal)

- QIDO-RS endpoints implemented in Rust inside oxidicom.
- oxidicom parses DICOM tags during C-STORE receive (it already does this) and writes them directly to CUBE's Postgres.
- Django retains DB ownership but has no DICOMweb code.
- oxidicom owns the HTTP/JSON serialization.

### Option C — Hybrid (Adam's secondary proposal, with my fleshing-out)

- oxidicom continues C-STORE ingestion as today.
- oxidicom **publishes parsed tag sets on NATS** (it already publishes ingest-progress events on the same bus).
- A small consumer service — Python or Rust, running inside the CUBE compose network — subscribes to those events and upserts `PACSInstance` rows into CUBE's Postgres.
- QIDO-RS endpoints stay in Django because that's where auth lives.
- The Celery indexing task in Phase A gets replaced by the NATS consumer; nothing else changes.

### Honest pros/cons

| Concern | A: Django-only | B: oxidicom | C: Hybrid |
|---|---|---|---|
| **Re-read files at indexing time?** | Yes (pydicom in Celery) | **No** — oxidicom has the parsed tags in memory at C-STORE time | **No** — oxidicom hands the tags off on NATS |
| **Speed of QIDO endpoint serving** | Python/Postgres | Rust/Postgres — faster at hot-path serialization | Python/Postgres (same as A) |
| **Auth surface** | Existing CUBE chain works | oxidicom reimplements Token/Basic/Session/LDAP **or** sits behind a CUBE auth proxy | Existing CUBE chain works |
| **Number of services with DDL/DML on CUBE's DB** | 1 (Django) | 2 (Django + oxidicom) — coordinated migrations across repos | 1 (Django; consumer is read/write but co-owned with Django) |
| **Coupling to oxidicom team** | None | Heavy — every QIDO change is a cross-repo PR | Light — only the NATS event schema is shared |
| **Cost if "is oxidicom the only ingestion path?" is no** | Zero — indexer fires on `PACSFile` creation regardless | Medium — non-oxidicom ingestion paths need their own indexing route | Medium — same problem; could be patched by a fallback Celery task for non-oxidicom paths |
| **Phase 2 federation gateway (ATLAS DICOMweb gateway, grant §2.7.1.2)** | Talks to one auth-aware endpoint per TD | Talks to oxidicom directly; needs oxidicom's auth story | Talks to Django (same as A) |
| **What Phase A keeps** | All of it | `PACSInstance` model + `PACSSeries` fields. Celery indexing task is replaced. | All of it except the Celery indexing task. |
| **Engineers comfortable with the stack** | Django people (us) | Rust people (the oxidicom team — BCH) | Mostly Django; one Rust touchpoint (oxidicom NATS emit) |

### My read

**Option C is the strongest tradeoff for the MVP through grant Month 12**, in my opinion:

- It captures Adam's main efficiency argument (don't re-read the files; let oxidicom's existing DICOM parsing feed the index).
- It keeps auth and the HTTP surface in one place — the auth chain is non-trivial and re-implementing it in oxidicom would be a real engineering cost.
- It minimizes cross-repo coordination — the only shared contract is the NATS event schema.
- It preserves all of Phase A except the indexing task itself.
- It cleanly degrades if non-oxidicom ingestion paths exist: a Celery task can fall back on `.dcm` re-read for files that didn't come through oxidicom (matching Adam's "scary Django stuff" only where it's load-bearing).

**Option B is the right long-term answer if oxidicom is genuinely the only DICOM ingestion path AND the BCH team has appetite to extend oxidicom with auth + HTTP serving.** It's the cleanest design if those two preconditions hold. The cost is the cross-repo coordination during the grant's Month 12 timeline.

**Option A is the wrong answer given Adam's point about re-reading files.** That argument is the one I should have caught when writing `QIDO_PLAN.md`. Even ignoring "scary Django stuff," paying pydicom + storage I/O per file when oxidicom already parsed the header in Rust is just waste.

This is a decision that needs BCH input. **Recommend escalating to Rudolph + Joshua.**

---

## 3. Hierarchy — explicit Patient/Study/Series/Instance vs GROUP BY

Adam: "the model of dicoms is a PACS Study, which can have multiple PACS Series, which can have multiple PACS instances. A patient can have multiple studies. This needs to be more explicit in the code."

I locked GROUP BY for MVP in `QIDO_PLAN.md` §1. The reasoning was:

> No new model, no migration churn. Study-level counts (NumberOfStudyRelatedSeries/Instances) computed by aggregation per request. Faster to ship, risk of slow studies-listing queries on large datasets. Easy to materialize later if perf demands it.

Adam is reopening this. His arguments:

- **Patient-level queries** (`/instances?PatientID=…` → all instances across all studies for a patient) get awkward without a Patient table. We'd join through Series rows, deduplicating on Patient tags that are denormalized across them.
- **Complex query support** — fuzzymatch on `PatientName`, wildcards on Patient attributes — works better with a Patient table to index against.
- **Counts** like `NumberOfStudyRelatedInstances` and `NumberOfStudyRelatedSeries` become O(study size) per request under GROUP BY. Acceptable at 10⁴ series; not at grant scale.

The cost side, which Adam acknowledges himself ("could be a pain in the ass"):

- `PACSSeries.objects.create` is invoked from `PACSSeriesSerializer.create` (the registration callback). That function would need to find-or-create the parent `PACSStudy` and `PACSPatient` rows, with tag consistency checks across series that share a study.
- Three new models means three new migrations and three new sources of denormalization drift.
- Tests across `pacsfiles/` would need updating.

### What I think now

My GROUP-BY choice was correct for "ship a single-PACS demo by next month." It is **not** the right choice if we're building toward grant Month 12 deliverables. The grant's §2.7.1 deliverable references multi-TD scale; the GH indexer in Phase 2 wants to index across many ChRIS instances. Both of those scenarios push past where GROUP BY stops being free.

I'd land here:

- Keep `PACSInstance` as it is (Phase A added it; needed regardless).
- **Add `PACSStudy`** as an explicit model with denormalized `NumberOfStudyRelatedSeries` and `NumberOfStudyRelatedInstances`. Counters updated by signals or by the indexing pipeline.
- **Patient stays implicit** for now — `PatientID` + the patient tags live on `PACSStudy` (matching how QIDO Study Result Attributes treat them — Patient tags are returned at the Study level). Promote to `PACSPatient` only if there's a concrete query that demands it.

That's a partial accept of Adam's hierarchy argument. The full Patient/Study/Series/Instance hierarchy is more change than the QIDO surface strictly requires, but explicit Study definitely earns its keep.

If we go with **Option C (hybrid)** above, oxidicom publishes Patient/Study/Series/Instance tag sets on NATS, and the consumer naturally writes to all the right rows. The hierarchical model is easier to maintain in that architecture than in pure Django/Celery.

---

## 4. Wildcards and fuzzymatching

`QIDO_PLAN.md` §5.1 has wildcards (translate `*` → `%`, `?` → `_`, use `ILIKE`). Fuzzymatching is explicitly deferred ("stub for MVP: log it, ignore it").

Adam: both should be in the index.

Honest answer: he's right that they cost almost nothing to plan for now, and a lot to retrofit later.

- **Prefix wildcards** (`DOE*`) work with a normal B-tree index. Already covered.
- **Substring wildcards** (`*DOE*`) need a Postgres trigram index (`pg_trgm` extension).
- **Fuzzymatch** (`fuzzymatching=true` query param) also wants `pg_trgm` with similarity scoring.

Both use the same extension. Adding `pg_trgm` is one migration line:

```python
from django.contrib.postgres.operations import TrigramExtension

class Migration(migrations.Migration):
    operations = [TrigramExtension()]
```

Then `models.Index(fields=['PatientName'], opclasses=['gin_trgm_ops'], name='...', condition=...)` on the columns we want trigram-indexed.

Update to `QIDO_PLAN.md`: move fuzzymatch from "stub for MVP" to "supported from day one." Adding pg_trgm in Phase A's next migration is cheap.

---

## 5. Indexing efficiency — re-reading files

Adam doesn't say this explicitly but it's implicit in "oxidicom already owns a lot of the pure-DICOM elements." Phase A's Celery task does this:

```
.dcm file on disk → storage.download_obj(whole file) → io.BytesIO →
pydicom.dcmread(stop_before_pixels=True) → tags → DB upsert
```

This is wasted work if oxidicom already parsed those same tags into Rust structs when it received the C-STORE.

Under **Option C** above, the work becomes:

```
oxidicom (already has parsed tags) → NATS event → consumer → DB upsert
```

No filesystem re-read. No pydicom call. Faster, less I/O, lower indexing latency under burst ingest.

This is concretely a better design even ignoring the "scary Django" angle. Capturing it as a sound argument for Option C.

---

## 6. Ingestion ownership (Adam's question for BCH)

Looking at the current CUBE codebase:

- **oxidicom** is the primary path — C-STORE on port 11111, writes under `SERVICES/PACS/<pacs>/`, triggers the registration callback.
- **`POST /api/v1/pacs/series/`** is the registration callback. Anyone in the `pacs_users` group with a `chris` user token could in principle call it. Not strictly enforced to oxidicom-only.
- **Plugin outputs** write to `feed_<id>/` folders, not the PACS tree — not a PACS ingestion path.
- **Userfile upload** writes under `home/<user>/` — also not the PACS tree.

So in practice, oxidicom is the only path that produces PACS data in normal operation. But it's not enforced at the layer that matters (the file/folder model).

This is worth asking Rudolph + Joshua plainly: **"Going forward, is oxidicom the only intended ingestion path for DICOM into CUBE, or do you plan other ingestion routes (S3 bulk import, STOW-RS, plugin outputs, etc.)?"** The answer changes the calculus on Option B vs Option C above:

- "Yes, only oxidicom" → Option B becomes cleaner. oxidicom owns ingest + indexing + serving, with one auth integration.
- "No, others too" → Option C wins. The NATS consumer indexes oxidicom-sourced files; a fallback indexer (Celery, on `PACSFile` creation) covers everything else.

---

## 7. Methodology — e2e tests + AI-driven iteration

Adam: "feels very doable for this to be a change that codex could handle quite well? You could develop a e2e test-suite with a client that queries QIDO-RS and keep going until it passes."

Not architectural — execution methodology. Worth saying explicitly:

- The e2e test suite is **Phase D in `QIDO_PLAN.md` regardless** of how we build it. We want OHIF + xh + a Python client test suite either way.
- AI-driven iteration against that suite is a reasonable accelerator for whoever holds the work. Especially well-suited to Option A or Option C (Python code that has to satisfy a precise spec).
- For Option B (Rust in oxidicom), the AI angle depends on the BCH team's tooling more than ours.

Decision deferred until we know who's executing.

---

## 8. Status of Phase A given these decisions

Phase A's footprint, restated:

- `PACSInstance` model — **kept in all three options**.
- `PACSSeries` new fields (`StudyTime`, `Manufacturer`, `BodyPartExamined`, `SeriesNumber`, `PerformedProcedureStepStart*`) — **kept in all three options**. They're DICOM tags, not opinions about architecture.
- Celery `index_pacs_instance` task — **kept in Option A, replaced in Options B/C**.
- `transaction.on_commit` fan-out in `PACSSeriesSerializer.create` — **kept in Option A, replaced or supplemented in Options B/C** (a Celery task as fallback for non-oxidicom-sourced PACSFile creation).
- `pydicom` dependency — **kept in Option A, optional in Options B/C** (we'd keep it for tests + fallback indexing even there).
- Composite index `(pacs, StudyInstanceUID)` — **kept in all three**.

So even in the worst case for "we reopen the architecture," roughly 80% of Phase A's code stays. The remaining 20% (the Celery task body, the fan-out) is small enough that re-writing it under Option C is a few hours' work.

**No part of Phase A should be reverted today** based on these review comments. The work is forward-compatible with all three options.

---

## 9. Updates to `QIDO_PLAN.md` arising from this review

Concrete changes I'd make to the plan document before any further code lands:

1. **Add the §0 definitions block** at the top of `QIDO_PLAN.md`, with the wording from §1 above.
2. **§1 reopens the architecture decision.** Replace the "Locked decisions" table with three options (A/B/C) and an explicit "this needs BCH input" callout. Move the GROUP-BY-for-studies decision into the reopened set.
3. **§3.1 (PACSInstance) becomes "PACSInstance + PACSStudy"** — partial accept of the explicit-hierarchy argument. Patient stays implicit unless a concrete query demands it.
4. **§5.1 fuzzymatch row** moves from "stub for MVP, log and ignore" to "supported via pg_trgm." Add `pg_trgm` migration to Phase A's recommended next-migration list.
5. **§4 (ingest pipeline changes) gains an Option C variant**: NATS consumer subscribes to oxidicom-published tag events. Celery task retained as a fallback for non-oxidicom-sourced files.

These changes happen *after* BCH weighs in on the architecture question, not before.

---

## 10. Proposed escalation to BCH

Two clean decisions and one factual question. Suggested phrasing for Rudolph + Joshua:

> **1. Where do QIDO-RS endpoints live?**
> Option A (Django views in CUBE), B (Rust endpoints in oxidicom), or C (hybrid: oxidicom emits parsed tags on NATS, a small consumer in the CUBE network indexes them, QIDO-RS endpoints stay in Django for the auth chain). Strong ISC-side preference for **C** for the auth and cross-repo coordination reasons listed in our review response. **B** is cleaner if oxidicom is the only intended ingestion path.
>
> **2. Patient/Study/Series/Instance hierarchy in the data model?**
> ISC initially proposed GROUP BY rollups for studies (faster to ship, materialize later if perf demands). On review, recommend **adding `PACSStudy` as an explicit model now** with denormalized counts. Patient-level entity stays implicit unless a concrete query needs it.
>
> **3. Question**: going forward, is oxidicom the only intended DICOM ingestion path into CUBE, or do you plan other routes (STOW-RS, S3 bulk import, plugin outputs into the PACS tree, etc.)? The answer affects #1.

These three should be sent before Phase B starts. Phase B (renderer + query parser) is mostly architecture-independent — but the view-layer of Phase C **completely** depends on these answers, so a wasted Phase B is small but a wasted Phase C is not.

---

## 11. Open items to keep tracking

Carried forward from this review for future rounds:

- `dicomweb` logger config in `local.py:LOGGING['loggers']` (still missing).
- `StorageManager` range-read support — only relevant if we stay on Option A. Drops off the radar under Options B/C.
- Patient-tag consistency across Series within a Study — still worth checking against an existing BCH-imported dataset before Phase C lands. Becomes a constraint on the `PACSSeries` → `PACSStudy` find-or-create logic.
- drf-spectacular treatment of DICOMweb endpoints — irrelevant under Option B, relevant under A or C.

---

## Acknowledgment

The two architectural reopenings (Option B/C and explicit `PACSStudy`) are legitimate critiques of choices I made. The GROUP-BY choice in particular was a clear MVP-vs-grant-scale tradeoff that I picked wrong for the longer horizon. Phase A as shipped is forward-compatible with the new direction, so no work needs to be undone — but `QIDO_PLAN.md` needs the updates in §9 above, and the three escalation items in §10 need to go to BCH before more code lands.
