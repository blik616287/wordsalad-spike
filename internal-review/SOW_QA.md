# Findings Q&A — likely & curveball questions (ISC-only prep)

> **🔒 INTERNAL — ISC prep, do not share with BCH.** Companion to
> `proposal-to-bch/DELIVERABLES_SUMMARY.md`. **Answers are written to be spoken**; `🔒` notes are
> strategy, not for the room. Every answer is anchored to a **specific finding** of our work, grounded in
> `CURRENT_API.md`, `knowledge-base/09–14`, and `implementation/dicomweb-l2/`.

**The work (May 19 scope):** find what CUBE needs to be DICOMweb-compliant — *what's missing and how to
do it* — and propose it. These Q&As are about **what we specifically found.**

---

## Likely questions — the findings

**Q. Bottom line: what does CUBE actually need to become DICOMweb-compliant?**
A. Five additive pieces, no rewrite: an **instance index (`PACSInstance`) + an explicit `PACSStudy`**, a
**DICOM-JSON renderer**, a **QIDO query parser**, the **WADO-RS retrieve surface**, and **STOW-RS**. The
storage and ingest foundation is already there.

**Q. What's already in CUBE that we can build on?**
A. The `pacsfiles` data model, object storage under `SERVICES/PACS/…`, the collection+json API with
existing auth, and **oxidicom**, which already receives DICOM and parses its tags. We reuse all of it.

**Q. What was the single most important finding?**
A. **The indexer has to build the explicit `PACSStudy`, not just per-instance rows.** We found QIDO
`/studies` returned *nothing* for oxidicom-ingested data until the indexer upserted the study object —
which is exactly why we recommend an explicit study model over GROUP-BY rollups. Found by running it
against real CUBE.

**Q. Does QIDO actually return oxidicom-ingested studies now?**
A. Yes — proven live: pushed via oxidicom C-STORE, and the study appeared in QIDO `/studies` **with no
manual reindex** (real-time auto-indexing), full DICOM-JSON.

**Q. Does fuzzy patient-name search work?**
A. Yes, via Postgres **`pg_trgm`** — and a finding: the Django `trigram_similar` **lookup must be
registered**, not just the extension enabled, or the query 500s. Both are handled.

**Q. How did you validate it — mocks or real CUBE?**
A. Real CUBE. The suite runs **97/97 inside a real `ChRIS_ultron_backEnd` checkout** (real `pacsfiles`,
Postgres), and we exercised QIDO/WADO/STOW **live over HTTP** end-to-end.

**Q. What's the recommended architecture, and why?**
A. **Endpoints in Django** (inherit CUBE auth) + index fed from **oxidicom (hybrid)**; **explicit
`PACSStudy`**; **STOW included** (grant requires all three); **`pg_trgm`** fuzzy. Each choice traces to a
finding above.

---

## Curveball questions — the findings

**Q. Does your indexer re-read every DICOM file? That sounds wasteful.** *(expect this — it's the perf crux)*
A. The simple path re-reads via pydicom — correct, but it re-parses what oxidicom already parsed. **Our
finding/answer is the hybrid:** have oxidicom emit the tags it already has, and a small consumer indexes
them **with zero file re-reads**. We **built and proved that consumer** — it indexed a study whose file
wasn't even present in storage.

**Q. But oxidicom doesn't publish tags today, does it?**
A. Correct — that's a specific finding: oxidicom's NATS channel (LONK) is **progress-only**, no tags. So
the no-re-read path needs **one small oxidicom-side change** to emit them. The **entire CUBE-side
consumer is already built and proven**, so that's the only missing piece.

**Q. We've started writing DICOM metadata to Postgres directly from Rust. Does your DICOMweb still work?**
A. Yes — finding: the **DICOMweb layer is indifferent to *how* the index is populated.** QIDO/WADO read
CUBE's Postgres tables (`PACSStudy`/`PACSInstance`); whether oxidicom writes them directly, a consumer
does, or Django re-reads, the same endpoints serve it. Watch-item: a direct Rust→Postgres write
**couples to CUBE's migrations** — worth coordinating the schema.

**Q. What about retrieval — isn't DICOM retrieve the awkward "server pushes to you" model?**
A. That's the legacy **C-MOVE** path CUBE has today. **WADO-RS replaces it with a plain HTTP `GET` pull**
— that's the concrete modernization, and it's what lets a standard viewer (OHIF) read straight from CUBE.

**Q. Pixel data / compressed images / frames?**
A. **Native (uncompressed) frame + bulkdata retrieval works** and is proven. **Compressed/encapsulated**
frames + `rendered`/`thumbnail` return an explicit `501` — transcoding needs a pixel codec
(`pylibjpeg`/`gdcm`); a deliberate, documented boundary.

**Q. What did testing actually catch — any real bugs?**
A. Yes — ~11 across the slice, each only visible at a deeper layer: the indexer not building `PACSStudy`;
the `trigram_similar` lookup needing registration; STOW POST blocked by Django CSRF (the dispatcher
wasn't exempt); a probe that crashed oxidicom's listener; and a deploy/image schema-skew. All fixed.
🔒 *Use this to show rigor — "we ran it against real CUBE, not slideware."*

**Q. Is this a fork? How does it land upstream?**
A. No fork — a **drop-in `dicomweb` Django app** + a few additive `pacsfiles` fields, developed against a
pinned CUBE checkout, so it lands as a **clean PR** into `ChRIS_ultron_backEnd`. Not yet run through
CUBE's upstream CI — that's part of merging.

**Q. Will it scale to grant data volumes?**
A. Indexing is one pass per instance; QIDO is **indexed Postgres queries** (+ a trigram GIN index for
fuzzy) — architecturally sound. Honest: **not load-tested at grant volume** yet (a Phase D /
regression-suite item).

**Q. Security / PHI?**
A. The endpoints **reuse CUBE's existing auth verbatim — no new auth code** — so they inherit whatever
CUBE enforces. **Security hardening was explicitly scoped out** at the May-19 call; it's a named follow-on.

**Q. What's genuinely *not* done?**
A. Three honest items: compressed-transfer-syntax transcoding (`501` stub); the **one oxidicom change** to
emit tags for the no-re-read path (CUBE consumer is built); and load-testing + a CI-green upstream PR.
Everything else across QIDO/WADO/STOW is implemented and tested.

---

## 🔒 Scope-creep redirects (internal — see `SCOPE_GUARDRAILS.md`)

- **"Can you also do security/encryption?"** → scoped out on purpose; happy to scope separately. Don't
  absorb into the current estimate.
- **"Can you start the MVP now?"** → yes, on a written greenlight; reference Joshua's "break into tickets."
- **General:** lead with the **findings**; present architecture as **recommendations/options**, never as
  escalation or a "please decide" block.
