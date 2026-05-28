# DICOMweb in CUBE — research ticket output

**For:** Rudolph Pienaar, Joshua Kanner (BCH)
**From:** Insight Softmax Consulting
**Date:** 2026-05-22
**Status:** Research deliverable — closes the spike scoped at the 2026-05-19 meeting

This document is the written output of the research ticket Joshua described on May 19: *"engineer goes off, does the research, writes up a document of what's missing, what needs to happen, what needs to be done, a proposal for how to go about doing that."* It tells you what we found, what we propose, and how we got there. Supporting detail and supporting code are referenced at the bottom.

---

## What the spike covered

1. Read CUBE's current API surface end-to-end. Identify everything that is and isn't there for DICOMweb-compliant query (QIDO-RS), retrieval (WADO-RS), and storage (STOW-RS).
2. Read the existing `pacsfiles` data model. Identify what metadata is captured today and what's missing for QIDO-RS' required attribute set across Patient/Study/Series/Instance levels.
3. Design an implementation that lands inside CUBE without forking the existing ingest path, and is forward-compatible with multi-TD federation.
4. Land the first concrete piece (schema + ingest hook) to validate the design and surface any hidden complexity before the larger phases begin.

Steps 1–3 were the research scope Alex sized as "a day, maybe two." Step 4 went beyond the spike because it derisked the harder design questions. Phase A (step 4) is implemented and validated; it's small (~550 lines diff, all tests pass, zero schema drift) and is forward-compatible with each of the architectural variants discussed below — so it doesn't commit the larger architectural decision.

## What's missing today

CUBE today exposes 18 PACS-related endpoints under `/api/v1/pacs/`. Filed under `application/vnd.collection+json`, the existing surface lets a logged-in PACS user list registered PACS sources, list series within them, search series by tag (Patient/Study/Series level), and download individual files. The data model stores Patient, Study, and Series tags on a single `PACSSeries` row.

The gaps relative to DICOMweb compliance, ordered by significance:

1. **No instance-level row.** Per-`.dcm` tags (`SOPClassUID`, `SOPInstanceUID`, `InstanceNumber`, `Rows`, `Columns`, `BitsAllocated`, `NumberOfFrames`) are only inside the `.dcm` files on disk. QIDO-RS requires instance-level search.
2. **No DICOM JSON Model renderer.** Existing endpoints return collection+json or DRF JSON keyed by Python attribute name. QIDO-RS requires DICOM JSON Model (`application/dicom+json`, tag-hex-keyed objects with `vr` + `Value`).
3. **No QIDO-style query parser.** Existing filters are exact-match (or `icontains` on a handful of string columns) and keyword-only. QIDO-RS query parameters accept tag-hex form, multi-value, range syntax for dates, wildcards on string VRs, and the `includefield` parameter.
4. **Several required QIDO attributes aren't stored**: `StudyTime`, `SeriesNumber`, `BodyPartExamined`, `Manufacturer`, `PerformedProcedureStepStartDate/Time` (covered now in Phase A), plus instance-level attributes (Phase A also).
5. **No WADO-RS retrieval surface.** The closest thing today is `/api/v1/pacs/files/{id}/.<...>` which serves a single `.dcm` as `application/octet-stream`. WADO-RS expects multipart `application/dicom`, plus `/metadata`, `/frames`, `/rendered`, `/thumbnail` variants.
6. **No STOW-RS upload surface.** The existing `POST /api/v1/pacs/series/` is an internal registration callback called by oxidicom after C-STORE receive, not a DICOMweb upload.

For full per-route detail with HTTP methods, filter parameters, and a row-by-row comparison against QIDO-RS' required attribute tables, see `CURRENT_API.md` in this bundle.

## How we propose to close those gaps

### Indexing model

The natural model for DICOMweb is Patient → Study → Series → Instance, and we propose making that explicit in the schema. CUBE today collapses Patient/Study tags onto the `PACSSeries` row; we propose:

- **`PACSInstance`** — new, one row per `.dcm` file. Carries SOP-class, SOP-instance, geometry, transfer-syntax. (Already added in Phase A.)
- **`PACSStudy`** — new, one row per Study within a PACS. Carries Study-level tags plus denormalized `NumberOfStudyRelatedSeries` and `NumberOfStudyRelatedInstances` counters. (Not in Phase A; recommended for Phase B.)
- **`PACSSeries`** — existing, gains FK to `PACSStudy` plus the QIDO-required tags it didn't store before (`StudyTime`, `SeriesNumber`, etc.). (Tag additions in Phase A; FK to be added in Phase B.)
- Patient-level entity stays implicit — Patient tags live on `PACSStudy`, matching how QIDO Study Result Attributes return them. Promote to a `PACSPatient` model only if a concrete query demands it.

The trade-off here: we initially proposed computing Study-level rollups via `GROUP BY PACSSeries` to avoid migration churn. On review, the explicit `PACSStudy` model is the right call at grant scale (Month 12 deliverables in TA2 §2.6.1.6 reference multi-TD federation; rollups under GROUP BY get expensive at scale; complex queries — wildcards and fuzzymatching on patient attributes — are easier with explicit entities). The migration cost is real but bounded: `PACSSeriesSerializer.create` would need to find-or-create the parent `PACSStudy` row at ingest, with tag-consistency checks across series that share a study.

Wildcards and fuzzymatching on string attributes (`PatientName`, etc.) are supported through Postgres' `pg_trgm` trigram-index extension. Adding `pg_trgm` is a one-line migration and supports both kinds of query out of the box.

### Where DICOMweb endpoints live

This is the architectural question worth flagging explicitly, because it affects which BCH team(s) touch the work. Three variants we considered:

**A — Django (CUBE only).** QIDO/WADO/STOW endpoints implemented as DRF views inside CUBE. Indexing runs as a Celery task that reads `.dcm` headers via pydicom. Existing CUBE auth chain (Token / Basic / Session / LDAP) covers DICOMweb without new code.

**B — oxidicom (Rust only).** QIDO/WADO/STOW endpoints implemented in oxidicom. Indexing happens inline during C-STORE receive (oxidicom already parses tags at that point). oxidicom serves the HTTP+JSON surface; CUBE remains the stateful Postgres store. Requires oxidicom to grow an auth layer matching CUBE's chain, and migrations to be coordinated across the CUBE and oxidicom repos.

**C — Hybrid.** oxidicom publishes the parsed tag set on NATS (it already publishes ingest-progress events on the same bus). A small consumer service running inside the CUBE compose network subscribes and upserts `PACSInstance` / `PACSStudy` rows. QIDO/WADO/STOW endpoints stay in Django because that's where auth and the API surface live. The Celery indexing task we built in Phase A becomes a fallback path for any non-oxidicom-sourced files; the primary path is the NATS consumer.

**Our recommendation is C, with a fallback to B if oxidicom is confirmed as the only intended ingestion path.**

Reasoning:
- **Don't re-read files.** oxidicom already parses headers during ingest. Paying pydicom + storage I/O per file from Python is wasted work. Variants B and C both eliminate this. Variant A is the only one that doesn't.
- **Auth lives in one place.** CUBE's auth chain (LDAP-backed, with TokenAuthentication / BasicAuthentication / SessionAuthentication) is a non-trivial integration to reproduce in Rust. Variants A and C inherit it for free; variant B reimplements it.
- **Minimize cross-repo coordination.** Variant B is a heavier engineering coupling between ISC (working on CUBE) and the oxidicom team (BCH). Variant C only shares the NATS event schema.
- **Federation-friendly.** The "ATLAS DICOMweb gateway" referenced in grant §2.7.1.2 sits above per-TD endpoints. Talking to one auth-aware endpoint per TD is cleaner than talking to a sibling oxidicom service.
- **Conditional on ingestion ownership.** If oxidicom is the only DICOM ingestion path going forward, variant B's coupling is acceptable and the auth re-implementation is worth doing once. If alternative ingestion paths (STOW-RS, plugin outputs writing into the PACS tree, bulk import from S3) are in scope, variant C's fallback indexer covers them naturally and B doesn't.

The factual question we'd ask before locking this in: **is oxidicom the only intended ingestion path for DICOM into CUBE going forward, or are other routes planned?**

### Sequencing

Five phases, ~5–6 weeks total for the single-PACS demo. Phase A is shipped; the rest are pending.

| Phase | Scope | Status |
|---|---|---|
| A — Schema + ingest foundation | New `dicomweb` Django app, `PACSInstance` model, six new `PACSSeries` fields, Celery indexing task wired into the existing ingest serializer. | **Done.** Validated against the existing test suite (103/103 pacsfiles tests still pass, 9/9 new tests pass, zero schema drift). Documented in `PHASE_A_IMPLEMENTATION.md`. |
| B — Hierarchy + query layer | Add `PACSStudy` model with denormalized counts. Add `pg_trgm` extension. Build DICOM-tag query parser (hex + keyword forms, multi-value, ranges, wildcards). Build DICOM JSON Model renderer. | Pending. Architecture-independent — same shape under any of A/B/C above. |
| C — View layer | Implement QIDO-RS endpoints; depending on choice above, also WADO-RS and STOW-RS. | Pending. Shape depends on architecture choice. |
| D — Backfill + integration tests | Management command to index pre-existing PACS files. OHIF smoke-test checklist. Integration tests tagged `integration`. | Pending. |
| E — Polish | OpenAPI annotations (or exclusions), README, performance check on the BCH dataset. | Pending. |

Phase A's footprint survives all three architectural variants (A/B/C) intact except for the Celery indexing task itself — which is ~170 lines and trivially replaceable. So no work needs to be undone if we land on variant B or C.

## What's bundled with this document

- **`CURRENT_API.md`** — exhaustive map of CUBE's API surface today, with per-route HTTP methods, filter parameters, and a gap analysis against QIDO-RS' required attribute set.
- **`QIDO_PLAN.md`** — the deeper implementation plan with phase-by-phase milestones, file-level change index, capacity sizing, and open questions.
- **`PHASE_A_IMPLEMENTATION.md`** — extensive walkthrough of what's already implemented, every file changed with rationale, and a full validation log including a real bug the test suite caught during this work.
- **`schema.yaml` / `schema.split.yaml`** — live OpenAPI 3.0.3 dumps of CUBE's current API, generated via `just openapi` against a freshly built dev stack. The diff between this schema and a post-DICOMweb schema is what BCH's API consumers would see change.
- **`code/phase-a.patch` + `code/source/`** — the Phase A code, applicable via `git apply` to a clean ChRIS_ultron_backEnd checkout. Includes the new `dicomweb` Django app, the migration, and modifications to five existing files.

## Open items for follow-up

These are the items where ISC's recommendation could change based on BCH's input. None of them block Phase B from starting on architecture-independent work (the renderer + query parser + `pg_trgm` migration + `PACSStudy` model are all the same shape under any variant).

1. **Ingestion ownership.** Is oxidicom the only intended path for DICOM into CUBE going forward? If yes, the case for variant B (Rust endpoints in oxidicom) gets stronger; if no, variant C (hybrid) is cleaner.
2. **STOW-RS scope.** The MVP framing ISC sent on May 1 had QIDO + WADO only. Grant TA2 §2.6.1.6 has all three under the Month-12 deliverable. We'd close out the variance one way or the other before Phase C view code is sized.
3. **Patient-tag consistency across series within a study.** Does CUBE-imported data today show consistent Patient tags across all series of the same Study? Affects the find-or-create logic in `PACSSeriesSerializer.create` once `PACSStudy` lands. A single query against an existing dataset would answer it.

We're ready to walk through any of this on a call when you're ready, and equally ready to begin breaking the next phases into tickets per Joshua's preferred workflow.
