# ISC Findings & Deliverables — DICOMweb for ChRIS (CUBE)

**Insight Softmax Consulting → Boston Children's Hospital / ATLAS** · Deliverables, findings & rationale

## Scope of the work

> At the **May 19, 2026** scoping call, BCH asked for a **research / spike ticket**: an engineer
> investigates what `ChRIS_ultron_backEnd` (CUBE) needs to become **DICOMweb-compliant**
> (QIDO-RS / WADO-RS / STOW-RS), writes up what's missing and how to do it, and brings back a
> proposal to discuss and break into tickets.

## Why this matters — DICOM, DICOMweb, and where CUBE stands

**DICOM** is the universal medical-imaging standard (Patient → Study → Series → Instance; every scanner,
PACS, and viewer speaks it). But it predates the web (ACR-NEMA **1985**), and its classic network layer
(**DIMSE**) is a port- and AE-title-based protocol, not HTTP. Most tellingly, **retrieval uses C-MOVE,
where the *server pushes* the image to a pre-registered endpoint rather than the client simply pulling
it** — brittle across networks/firewalls, and invisible to ordinary web clients.

**DICOMweb** is the DICOM standard's own **RESTful HTTP face** — **QIDO-RS** (query), **WADO-RS**
(retrieve), **STOW-RS** (store), all over HTTP with `application/dicom+json`. It keeps the data model but
replaces the awkward parts: a **plain HTTP `GET` pull** (firewall-friendly, no AE-title negotiation),
JSON, and **native support in zero-footprint viewers like OHIF**.

**Where CUBE stands:** CUBE exposes its **own `collection+json` REST API** and ingests DICOM via
oxidicom — but it **does not speak DICOMweb.** So a clinician's DICOMweb viewer (OHIF) or a hospital
PACS **cannot query, retrieve, or store against CUBE today**, and retrieval still rides the legacy
C-MOVE/pfdcm path.

**Why compliance is the unlock — four reasons:**
1. **Research → clinic.** Research outputs only become useful to clinicians through the tools they
   already use, which speak DICOMweb. Without it, ChRIS is an island requiring custom per-site glue.
2. **Contractual grant deliverable.** ATLAS **§2.6.1.6** requires WADO-RS + STOW-RS + QIDO-RS
   **operational by Month 12** (regression suite Month 15).
3. **Federation.** A federated research platform exchanges imaging **across sites over standard
   DICOMweb**, not a bespoke API.
4. **Modernization.** It replaces the brittle C-MOVE push with an HTTP pull any standard client can drive.

*(Full narrative: `knowledge-base/16-why-and-how-dicomweb-compliance.md`.)*

Below is what we found, organized exactly as the ask: **what CUBE has today → what's missing → how to
do it (and proof it works).**

## 1. What CUBE has today — the foundation is solid

- **Data + storage:** the `pacsfiles` app models `PACS` / `PACSSeries` / `PACSFile`; the bytes live in
  pluggable object storage (fslink/Swift/S3) under `SERVICES/PACS/<pacs>/<study>/<series>/…`. The REST
  API is collection+json with token/basic auth.
- **Ingest:** **oxidicom** (the Rust C-STORE SCP) receives DICOM and creates the `PACSSeries`/`PACSFile`
  rows via a Celery task — and it **already parses the DICOM tags** during ingest.
- **Two concrete gaps we confirmed:** (a) stored tags are **Study/Series/Patient-level on `PACSSeries`
  only — there is no instance-level index and no explicit study object**; (b) today's retrieve path is
  the legacy **C-MOVE / pfdcm** flow (server-push, "reversed client-server"), and `GET /api/v1/pacs/`
  even **500s when pfdcm is unreachable** — i.e. no modern HTTP query/retrieve surface exists.

## 2. What's missing for DICOMweb compliance — and it's additive, not a rewrite

CUBE needs five concrete additions, each mapped to a change site in `CURRENT_API.md`:
1. an **instance index** (`PACSInstance`) + an **explicit `PACSStudy`** study object;
2. a **DICOM-JSON renderer** (`application/dicom+json`, tag-keyed `{vr, Value}`);
3. a **QIDO query parser** (tag *and* keyword matching, UID-lists, date/time ranges, wildcards, fuzzy,
   `includefield`, `limit`/`offset`);
4. the **WADO-RS retrieve surface** (`multipart/related; application/dicom`, metadata, frames);
5. the **STOW-RS upload surface**.

## 3. How to do it — proven against a *real* CUBE

We built the additive `dicomweb` Django app to de-risk the plan, and validated it — these are the
findings that make Phase B/C costing concrete:

- **All three endpoints work.** The suite runs **97/97 inside a real `ChRIS_ultron_backEnd` checkout**
  (real `pacsfiles` + Postgres), and we served **QIDO/WADO/STOW live over HTTP**, ingesting via both
  oxidicom C-STORE and STOW.
- **Key finding — the indexer must build the study object.** QIDO `/studies` was **blind to
  oxidicom-ingested data** until the indexer upserts the explicit `PACSStudy` (not just instances);
  found and fixed by testing end-to-end. This is *why* we recommend the explicit study model.
- **Real-time auto-indexing works** (a brand-new study appeared in QIDO with no manual reindex).
- **Fuzzy patient-name search works** via Postgres `pg_trgm` — with the finding that the Django
  `trigram_similar` lookup must be **registered**, not just the extension enabled.
- **Native frame/bulkdata retrieval works**; compressed-transfer-syntax transcoding is the documented
  boundary (returns `501`).

## 4. Recommendation (grounded in the findings)

Keep the **endpoints in Django** (inherit CUBE's auth), add the **explicit `PACSStudy`**, **include
STOW** (the grant requires all three), use **`pg_trgm`** for fuzzy. **Efficiency finding:** oxidicom
already parses the tags but **publishes none of them on NATS today** (its LONK channel is progress-only),
so the most efficient "never re-read a file" indexing path needs **one small oxidicom change** to emit
those tags — and we've already **built and proven the CUBE-side consumer** for it (it indexed a study
whose file wasn't even present in storage).

## Honest gaps

Compressed-transfer-syntax transcoding (`501` stub); the one oxidicom-side change to emit tags for the
no-re-read path; load-testing at grant volume; a CI-green upstream PR. Security hardening was **scoped
out** at the May-19 call.

## Where the detail lives

`CURRENT_API.md` (gap analysis) · `QIDO_PLAN.md` (5-phase plan) · `RESEARCH_TICKET_OUTPUT.md`
(recommendation) · `knowledge-base/` (engineering reference) · `implementation/dicomweb-l2/` +
`deploy/ansible/` (the working, tested slice).
