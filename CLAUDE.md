# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is **not a software project.** It is the ISC-internal working package (a documentation compendium) for Insight Softmax Consulting's (ISC) engagement with Boston Children's Hospital (BCH) on adding **DICOMweb compliance (QIDO-RS, WADO-RS, STOW-RS)** to `ChRIS_ultron_backEnd` (CUBE), under the ATLAS grant (ARPA-H, prime: Gradient Health). It contains proposal docs, source materials, an internal-review trail, and one embedded code patch.

There is no build, test, or lint here — the deliverable is prose plus a Django patch that targets an **external** repo. Most work in this folder is reading, editing, and reconciling Markdown documents.

`README.md` is the canonical orientation document; read it first. It carries the engagement summary, process timeline, per-document reading order, and current state-of-work snapshot. When the state of the engagement changes, update `README.md` (and `internal-review/ISC_DELIVERABLES.md`) rather than letting them drift.

## Critical operational rules

- **Audience separation is load-bearing.** `proposal-to-bch/` is BCH-facing; `internal-review/` is ISC-only scaffolding and must **never** be shown to BCH. Keep this boundary in mind when editing or generating any content — don't leak internal framing (scope-creep redirects, commercial-pipeline positioning, the "we exceeded the ask, close the loop before doing more" reasoning) into BCH-facing docs.
- **Framing rule (per Adam McArthur's review):** BCH-facing materials are framed as *research output*, not as escalation or "please decide" blocks. Architectural recommendations are baked into `RESEARCH_TICKET_OUTPUT.md` as option presentations, not raised as flags. Preserve this framing when editing proposal docs.
- **Canonical copy:** this repository is the single source of truth. If it and the older `/home/blik/Desktop/chris/deliverables/` bundle drift, this repo wins — it has the framing Adam asked for.
- The 37 MB grant PDF is referenced, not stored: `/home/blik/Desktop/chris/docsend_156_pages.pdf`. Relevant pages: §2.6.1.6 (DICOMweb) p.78; §2.6.1 (Airflow+ChRIS parent) p.75; §2.7.1 (at-rest format) p.82.

## Layout and document roles

```
proposal-to-bch/        BCH-facing (lead with RESEARCH_TICKET_OUTPUT.md)
  RESEARCH_TICKET_OUTPUT.md   5-page lead summary; bakes in the architecture recommendation
  CURRENT_API.md              CUBE today + gap analysis vs QIDO-RS
  QIDO_PLAN.md                phased implementation plan (5 phases A–E)
  PHASE_A_IMPLEMENTATION.md   what Phase A coded + validation log
  schema.yaml / schema.split.yaml   live OpenAPI 3.0.3 dump of CUBE
  code/                       Phase A code as one git-apply patch
internal-review/        ISC-ONLY — do not show BCH
  ISC_DELIVERABLES.md         L1/L2/L3/L4 layered scope reference
  REVIEW_RESPONSE_ROUND_1.md  response to Adam's review
  SCOPE_GUARDRAILS.md         meeting cheat sheet + scope-creep redirect language
source-materials/       read-only inputs (treat as references, not deliverables)
```

The **L1/L2/L3/L4 scope layering** in `ISC_DELIVERABLES.md` is the backbone abstraction: L1 = research spike (done), L2 = MVP implementation (conditional), L3 = grant §2.6.1.6 (conditional on grant approval), L4 = ISC's broader commercial pipeline (aspirational). Use these labels consistently; they encode certainty and audience, not just timeframe.

Two architectural questions are deliberately **reopened** (don't treat the older docs as settled): (1) where DICOMweb endpoints live — Django views vs oxidicom-hosted vs hybrid (ISC recommends hybrid "C", fallback "B"); (2) hierarchy model — GROUP BY rollups vs an explicit `PACSStudy` model (ISC now recommends explicit `PACSStudy`). `QIDO_PLAN.md` v1 still reflects the older "Django + GROUP BY" positions; `RESEARCH_TICKET_OUTPUT.md` carries the updated recommendation.

## The embedded code (`proposal-to-bch/code/`)

The only actual code here is **Phase A** of the QIDO plan — a Django patch for the external CUBE repo, not runnable in this folder. `source/` holds human-readable copies of the new files; `phase-a.patch` is the single applicable diff. It adds a new `dicomweb` Django app (`PACSInstance` model + Celery `index_pacs_instance` task), six new `PACSSeries` fields, and migrations.

To build/test it you apply the patch to a checkout of `ChRIS_ultron_backEnd` and use that repo's `just` tooling (`just build` / `just migrate` / `just test pacsfiles dicomweb --exclude-tag integration`). See `proposal-to-bch/code/README.md` for the exact apply-and-validate sequence. Phase A's stated bar: 103/103 existing pacsfiles tests pass, 9/9 new dicomweb tests pass, zero migration drift. If you modify the code copies in `source/`, regenerate `phase-a.patch` to keep them in sync.
