# ISC deliverables — ATLAS / ChRIS DICOMweb work

This folder contains everything Insight Softmax Consulting has produced for the BCH/ATLAS engagement to date — the research artifacts that satisfy the May 19 scoping ticket plus working Phase A code that lays the foundation for DICOMweb compliance in ChRIS\_ultron\_backEnd (CUBE).

## Quick map

```
deliverables/
├── README.md                              ← you are here
├── writeups/                              ← research-ticket output (the L1 deliverable)
│   ├── ISC_DELIVERABLES.md                ← scope layered by certainty/timeframe (L1–L4)
│   ├── CURRENT_API.md                     ← CUBE API today + QIDO-RS gap analysis
│   ├── QIDO_PLAN.md                       ← phased implementation plan
│   ├── PHASE_A_IMPLEMENTATION.md          ← extensive walkthrough of the Phase A code + validations
│   ├── schema.yaml                        ← live OpenAPI 3.0.3 dump (drf-spectacular)
│   └── schema.split.yaml                  ← split request/response variant for codegen
└── code/                                  ← Phase A implementation
    ├── README.md                          ← how to apply
    ├── phase-a.patch                      ← single `git apply`-able patch
    └── source/                            ← human-readable copies of new files
        └── chris_backend/
            ├── dicomweb/                  ← the new Django app
            └── pacsfiles/migrations/0009_*.py
```

## What was asked vs. what's here

Per the May 19, 2026 scoping meeting, BCH requested a **research / spike ticket**: an engineer goes off, writes up what's missing for DICOMweb compliance, proposes how to do it, returns for discussion. Estimated effort: "a day, maybe two." (See `writeups/ISC_DELIVERABLES.md` §L1 for source citations.)

This deliverable bundle includes that research output and **also** the first phase of implementation (the schema + ingest-pipeline foundation). The implementation went further than the spike scope on the assumption that the ground would shift quickly once BCH greenlights work. If BCH would rather discuss the plan first before any code lands, the `code/` directory can simply be set aside until then; the `writeups/` directory stands on its own as the research-ticket answer.

## Reading order

1. **`writeups/ISC_DELIVERABLES.md`** — start here. Tells you what ISC has committed to, layered by certainty (L1 research → L2 MVP → L3 grant deliverable → L4 broader pipeline).
2. **`writeups/CURRENT_API.md`** — what CUBE's API looks like today and the concrete gap against QIDO-RS. Anchored to the live `schema.yaml` in the same directory.
3. **`writeups/QIDO_PLAN.md`** — the implementation plan. Five phases. Phase A is what's in `code/`; Phases B–E are pending.
4. **`writeups/PHASE_A_IMPLEMENTATION.md`** — extensive walkthrough of the Phase A code, with every changed file documented and a full validation log (what was run, what passed, including a real bug the test suite caught and what's still recommended to validate before broader rollout).
5. **`code/README.md`** — how to apply Phase A.

## Status against the grant

- **§2.6.1.6 Imaging-Native APIs (e.g. DICOMweb) & Regression Testing** — $109k, BCH+Red Hat prime, Month 12 endpoints operational, Month 15 regression suite.
- The grant requires **all three** DICOMweb endpoints (WADO-RS, STOW-RS, QIDO-RS). ISC's own MVP proposal mentioned only QIDO + WADO. **This scope mismatch is flagged in `ISC_DELIVERABLES.md` §L3** and needs resolution with BCH before Phase B starts.
- Phase A's data model (`PACSInstance` with `pacs_file` 1-to-1) already supports all three endpoints, so the foundation is fine either way.

## Provenance

Every claim in `ISC_DELIVERABLES.md` cites a specific source document (the ATLAS MVP proposal, the May 19 scoping meeting transcript, the ATLAS grant proposal PDF, ISC's internal opportunity analysis). Those source documents are **not** included in this folder — they remain in the parent `chris/` workspace as ISC's internal input material.
