# 15 — Engagement & Scoping: the non-code context

> **Marty's curve-ball prep for the BCH stakeholder meeting.** This file is about the
> *business and scope framing* — who's in the room, why they asked, what was agreed, what's
> committed, what's conditional, what's still open. The code lives elsewhere (KB 01–10,
> `MEETING_BRIEF.md`); this is the "who/why/what-was-agreed" layer so Marty can answer
> confidently when a stakeholder pushes on scope, money, or commitments.
>
> **AUDIENCE WARNING — this file mixes internal and BCH-facing framing.** Sections marked
> 🔒 **INTERNAL-ONLY** are ISC scaffolding (the L1–L4 layering, scope-creep redirects,
> commercial positioning, the "we exceeded the ask" reasoning). **Never say the 🔒 material
> in the room.** Sections marked 🟢 **BCH-FACING** are safe to speak aloud. When in doubt,
> default to the 🟢 framing: **this is research output, not an escalation** (Adam's explicit
> instruction — see §9).
>
> Primary sources, in order of load-bearing weight:
> 1. `source-materials/DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md` — the **May 19, 2026
>    scoping meeting transcript**. The single most authoritative doc for "what BCH asked for."
>    Timestamps below (e.g. `[25:32]`) refer to it.
> 2. `source-materials/ATLAS_ ChRIS Federated Research Platform -- MVP Proposal.txt` — ISC's
>    own MVP framing (~May 1, 2026).
> 3. `source-materials/ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx` — ISC-internal
>    opportunity analysis (April 2026). 🔒 internal-only.
> 4. `internal-review/ISC_DELIVERABLES.md`, `SCOPE_GUARDRAILS.md`, `REVIEW_RESPONSE_ROUND_1.md`
>    — 🔒 internal-only.
> 5. `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md`, `QIDO_PLAN.md` — 🟢 BCH-facing.

---

## 1. The players 🟢 (facts) + 🔒 (positioning)

### BCH — Boston Children's Hospital (the client in the room)

| Person | Role | What to know |
|---|---|---|
| **Rudolph Pienaar** | BCH principal / ChRIS lead (FNNDSC). The senior technical voice and the PI on the BCH side. | Owns the product vision and the architecture story. He framed the MVP as "a microcosm of the larger workflow" (`[31:47]`) and is the one who *deferred indexing* out of scope (`[33:13]`). He controls the budget conversation (`[14:26]`). Was at Red Hat Summit; long history with Red Hat / Mass Open Cloud. Defer architecture/vision questions to his framing. |
| **Joshua Kanner** | BCH **engineering manager**. Reports the team; Rudolph is above him. | He is the one who **defined the ask** as a research ticket (`[25:32]`). His top stated priority is **hardening CUBE for enterprise use** (`[10:56]`) — but he explicitly put that *out of this scope* (pre-MVP, "nebulous", `[13:05]`). He runs the workflow: kickoff → research → write-up → discuss → break into tickets (`[25:32]`). |

BCH team size: **Rudolph + Joshua + ~3 engineers** (`[43:02]`), "with some churn." Small team. They
use a **Matrix server + daily standups** for comms (`[42:10]`). Others with specific component
expertise may join calls (`[41:43]`).

> **Where BCH sits in the grant** 🟢: BCH is the **primary technical execution partner** on ATLAS.
> They lead the largest share of the technical work (OpenShift TDs, Airflow pipelines, DICOM
> processing, DevOps). The ChRIS platform (CUBE) is theirs.

### ISC — Insight Softmax Consulting (us)

| Person | Role | At the May 19 meeting |
|---|---|---|
| **Alex Scammon** | ISC lead / partner. Ran the ISC side of the call. | Drove the scoping, sized the spike at **"a day, maybe two"** (`[38:02]`), and steered scope *away* from security ("keep the MVP to API spec," `[28:43]`). |
| **Tommy (Aldo) Sonin** | ISC. Resourcing + delivery. | Called the work **"a spike by the security person"** and pinned down that we're "not pushing anything outside the API" (`[29:21]`). He and Alex were to "powwow" after the call to pick the engineer (`[38:02]`). |
| **Adam McArthur** ("adamm") | ISC. Reviewer / DICOM-architecture conscience. | Reframed the indexing question as "an open question… part of the resulting research" (`[36:29]`). Later did the **Round 1 review** of the code (`REVIEW_RESPONSE_ROUND_1.md`) and gave the **"research output, not escalation"** instruction (§9). |
| **Marty** (you) | ISC engineer fielding this meeting as the technical voice. | Did the spike, wrote the deliverables, built the L2 code. The reason this prep exists. |

### The grant — ATLAS (the money behind it all)

- **ATLAS** = *Adaptive Technology for Large-scale Academic Sciences*. A nation-scale **federated
  medical data + compute platform** ("omnicloud") funded by **ARPA-H**.
- **Total value: ~$60M+ over 36 months.** Phase 1 began 2025. (source: opportunity analysis exec
  summary)
- **Prime contractor: Gradient Health (GH).** Technical partners: **BCH** and **University of Utah
  (UoU)**. Red Hat is a consulting partner (OpenShift).
- Goal in one line: give researchers access to de-identified imaging data covering 5–20% of the US
  population, via OpenShift **"Trusted Domains" (TDs)** at each institution, connected through a
  central Hub. Train MedGemma (Google's 27B medical model) on federated data.

### The budget line that is *our* foothold 🟢

**TA2 §2.6.1.6 — "Imaging-Native APIs (e.g. DICOMweb) & Regression Testing"**
- **Budget: $109k.** Prime: **BCH + Red Hat.**
- **Month 12:** DICOMweb **WADO-RS, STOW-RS, and QIDO-RS** endpoints operational within TD
  constraints.
- **Month 15:** Regression test suite (pipeline I/O, async execution, DICOMweb endpoints) for
  release gating.
- Parent task **§2.6.1** (Airflow Pipelines with ChRIS + DICOMweb) is **$1.39M** across six
  sub-tasks. Our $109k is the named DICOMweb entry point inside it.

---

## 2. The ask — what BCH actually requested 🟢 (this is the most important section)

This is the section to be word-perfect on. If anyone in the room asks "what did we agree to?",
the answer is **on the transcript** and you can quote it.

### Joshua's exact framing of the ticket (`[25:32]`)

> *"For the scope of the work that we initially reached out about — not like the grand scope of
> things, but just this one ticket — we're talking about getting DICOMweb compliance. That ticket
> is just a **research ticket** to figure out what is involved in achieving that compliance… we'd
> have a kickoff call, engineer goes off, does the research, **writes up a document of what's
> missing, what needs to happen, what needs to be done, a proposal for how to go about doing
> that.** We discuss it, break it up into tickets and get going."*

So the deliverable BCH asked for is literally: **a written research document** answering
*what's missing / what needs to happen / a proposal*, followed by a *discussion* and
*ticket-breaking*. Not code. A document.

### The "day or two" sizing (`[38:02]`–`[39:00]`)

Alex sized it: *"a couple hours of getting somebody read in… some hours taking a look at the spec…
they'll come back with questions… it's not weeks and weeks of work… maybe a day, maybe two worth
of time, something like that at this point."* Rudolph and Joshua agreed ("That's fine").

### What "compliance" means — explicitly narrowed to the API only (`[28:43]`–`[30:18]`)

This exchange matters because "compliance" almost drifted into security:
- Alex asked: *"When you're talking about compliance, are you just meaning complying with the API
  spec, or… security and encryption?"*
- Joshua: *"We hadn't discussed the latter. We'd merely discussed the API spec."*
- Tommy double-checked we're "not pushing anything outside the API."
- Joshua: *"We're currently just talking about communication… for interface."*
- Conclusion (Alex): *"I would love it if we could just keep the MVP to [the API]."* — agreed all
  around.

**Takeaway: the scope is API/interface compliance only.** QIDO/WADO/STOW *communication*, not
security, not encryption, not auth re-architecture.

### What was explicitly deferred

| Deferred item | Who deferred it | Quote |
|---|---|---|
| **Security / encryption / hardening** | Joshua (it's his top priority but he put it *outside* this) | "It's really pre-MVP. It's before any of that stuff even starts… a little bit nebulous in scope" `[13:05]` |
| **The security review of CUBE** that BCH's own engineer is doing | Joshua | confirmed "out of scope for what we're doing" `[29:47]` |
| **Indexing as a separate service / hooking into the ingest workflow** | Rudolph | "that is again a little bit out of scope… a very directed MVP… if I dial the MVP to a proof of concept, what we're talking about right now is sufficient" `[33:13]`–`[34:56]` |
| **Whether to build "good enough for MVP" vs "prod-grade with indexing built in"** | Joshua reframed it as *part of the research itself* | "this part of the conversation is part of the resulting research" `[37:22]` |

### The "story" Rudolph wants to prove (`[31:47]`, `[35:37]`)

Keep this in your back pocket — it's the *why*: *"We want to prove that it is possible to do
research on data without actually necessarily having to have direct access to the data."* The MVP
is: query a resource for what data it has → choose a subset → run something on it → look at the
results. DICOMweb is "point zero" — the *"defensible, specific, missing industry-standard
compliance protocol"* the rest diverges from.

---

## 3. 🔒 INTERNAL-ONLY — the L1/L2/L3/L4 scope layering

> **DO NOT SAY "L1/L2/L3/L4" IN THE ROOM.** This is ISC's internal map of what's promised vs
> aspirational (`ISC_DELIVERABLES.md`). It exists so we never over-commit. To BCH, there is just
> "the research output (done)" and "the next phases (pending your input)."

| Layer | Scope | Status | Conditional on | Audience |
|---|---|---|---|---|
| **L1 — Research spike** | A written doc: what CUBE needs for DICOMweb compliance + how to do it. | **DONE.** Covered by `RESEARCH_TICKET_OUTPUT.md` + `CURRENT_API.md` + `QIDO_PLAN.md` + Phase A code. | nothing — this is what was scoped May 19 | BCH (Rudolph + Joshua) |
| **L2 — MVP implementation** | DICOMweb endpoints on a single CUBE instance + BCH dataset re-DICOMized + an inference plugin running through. | **Not started.** ~1–2 months, 1 engineer (per ISC's MVP proposal). | **BCH greenlight + funding clarity** | BCH eng team |
| **L3 — Grant §2.6.1.6** | WADO+STOW+QIDO operational within TD constraints (Month 12) + regression suite (Month 15). | **Not started.** $109k, joint with Red Hat. | **ARPA-H grant approval** | ARPA-H deliverable |
| **L4 — Broader §2.6.1 + ISC pipeline** | The full $1.39M Airflow+ChRIS+DICOMweb workstream + 8 other ranked grant opportunities + a marketplace product play. | **Aspirational.** | relationship-building + grant approval | 🔒 ISC commercial pipeline |

**The key mental model:** L1 is what was *scoped* May 19. L2 is what ISC's *own MVP proposal*
sketched. L3 is what the *grant* contractually budgets and dates. L4 is ISC's internal commercial
positioning. **Only L1 has any current BCH-side commitment, and even that was a "day or two"
verbal agreement, not a signed contract.**

### 🔒 The "we exceeded the ask" reasoning

The honest internal truth (`ISC_DELIVERABLES.md` §L1): we were asked for a *document* sized at
"a day or two." We delivered the document **plus Phase A schema/ingest code plus a full L2
QIDO/WADO/STOW implementation.** That is *more* than the spike. Internally we flagged: *"We should
send L1 back to BCH before doing more engineering"* — closing the loop before sinking more on-spec
time. **In the room, do not lead with "we did way more than you asked."** Lead with "here's the
research output you asked for; we also de-risked the design by prototyping" (see §9 framing).

---

## 4. The deliverables produced and their status 🟢 (safe to show) / 🔒 (don't over-claim)

| Artifact | What it is | Status | Show BCH? |
|---|---|---|---|
| `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` | The lead BCH-facing doc: what's missing, proposed model, architecture options A/B/C, sequencing, open items. | Done | 🟢 **Lead with this.** |
| `proposal-to-bch/CURRENT_API.md` | Exhaustive map of CUBE's API today + gap analysis vs QIDO-RS. | Done | 🟢 pull up for depth |
| `proposal-to-bch/QIDO_PLAN.md` | The 5-phase implementation plan (A–E). | Done | 🟢 pull up for depth |
| `proposal-to-bch/PHASE_A_IMPLEMENTATION.md` | Walkthrough of the shipped Phase A code + validation log. | Done | 🟢 if asked about code |
| `proposal-to-bch/schema.yaml` / `schema.split.yaml` | Live OpenAPI dumps of CUBE today. | Done | 🟢 |
| `proposal-to-bch/code/` (`phase-a.patch`, 548 lines) | Phase A: new `dicomweb` app, `PACSInstance` model, 6 new `PACSSeries` fields, Celery indexer. 103/103 existing + 9/9 new tests pass, zero schema drift. | Done + validated | 🟢 |
| `implementation/dicomweb-l2/` | Full L2 QIDO/WADO/STOW implementation on the Phase A foundation. 95/95 tests, live stack served all three over HTTP. | Done — but **L2 test impl, not a merged CUBE PR** | 🟢 *with the honest limits* (see `MEETING_BRIEF.md` §5) |
| `internal-review/ISC_DELIVERABLES.md` | L1–L4 layering. | Done | 🔒 **keep closed** |
| `internal-review/SCOPE_GUARDRAILS.md` | Meeting cheat-sheet + redirect language. | Done | 🔒 **keep closed** |
| `internal-review/REVIEW_RESPONSE_ROUND_1.md` | Adam's review + responses. | Done | 🔒 **keep closed** |

> **The 5-phase plan** (`QIDO_PLAN.md`, restated in `RESEARCH_TICKET_OUTPUT.md`): **A** schema +
> ingest foundation (**done**) → **B** hierarchy + query layer (`PACSStudy`, `pg_trgm`, query
> parser, DICOM-JSON renderer) → **C** view layer (QIDO/WADO/STOW endpoints) → **D** backfill +
> integration tests → **E** polish. ~5–6 weeks total, 1 engineer, for the single-PACS demo.

---

## 5. The architecture decisions — framed as RESEARCH OUTPUT, not escalation 🟢

> **Critical framing (Adam, §9):** present these as **options we researched and have a
> recommendation on**, with *one factual question* for BCH — **not** as a "you must decide this
> now" escalation. `RESEARCH_TICKET_OUTPUT.md` deliberately bakes the recommendation into prose
> rather than presenting a "please decide" block. Full technical detail: `MEETING_BRIEF.md` §3 and
> KB `08-l2-architecture-decisions.md`.

### D1 — Where do the DICOMweb endpoints live? → recommend **C (Hybrid)**, fallback **B**

- **A — Django-only:** endpoints as DRF views; a Celery task re-reads each `.dcm` with pydicom to
  index. **Weakest** — re-reads files oxidicom already parsed (waste).
- **B — oxidicom (Rust):** endpoints in Rust, indexed inline at C-STORE. Fastest, but must
  **reimplement CUBE's Token/Basic/Session/LDAP auth in Rust** + coordinate cross-repo migrations.
- **C — Hybrid (RECOMMENDED):** endpoints stay in **Django** (inherit the auth chain for free);
  oxidicom is extended to **publish its already-parsed tags on a *new* NATS event**; a small
  consumer in the CUBE network upserts the index; the Phase A Celery indexer becomes the
  **fallback** for non-oxidicom files (STOW uploads, plugin outputs, S3 import).
- **Honest caveat for the room:** C is not free reuse — oxidicom emits **no tags on NATS today**
  (the LONK/NATS bus carries progress only). C depends on BCH agreeing to add that tag event. If
  they won't, C degrades to A.

### D2 — Hierarchy model → recommend **explicit `PACSStudy` now; Patient stays implicit**

GROUP-BY rollups were fine for a single-PACS demo, but `NumberOfStudyRelated*` counts become
O(study size) per request at grant scale. Recommended shape:
`PACS ──< PACSStudy ──< PACSSeries ──< PACSInstance`. Patient tags ride on `PACSStudy`.
🔒 *Internal note:* this **reverses** the GROUP-BY choice originally locked in `QIDO_PLAN.md` v1 —
Adam's review reopened it (`REVIEW_RESPONSE_ROUND_1.md` §3). Phase A code survives the reversal
intact; don't volunteer the "we changed our mind" framing.

### D3 — STOW-RS scope → **IN (decided)**

🟢 **This is the load-bearing scope reconciliation — be ready for it.** The **grant** (§2.6.1.6)
puts **all three** (WADO+STOW+QIDO) under the Month-12 deliverable. ISC's **May-1 MVP proposal**
listed only **QIDO + WADO** (STOW was *not* mentioned). We reconciled **to the grant**: STOW is IN.
Phase A already supports it at the data layer. (STOW being a *non-oxidicom* ingestion path is also
why D3=IN nudges D1 toward variant C.)

### D4 — Fuzzy/wildcard matching → **`pg_trgm` from day one**

Substring (`*DOE*`) and fuzzy `PatientName` matching need a Postgres trigram GIN index; it's a
one-line `TrigramExtension()` migration. Architecture-independent; cheap now, expensive to
retrofit.

---

## 6. The OPEN questions needing BCH input 🟢

These are the things the next conversation needs to close. Per Adam's instruction, in
`RESEARCH_TICKET_OUTPUT.md` these are folded in as **recommendations + one factual question**, not
a "please decide" block. But internally, Marty should know the full list:

1. **Architecture (D1).** A vs B vs C. ISC recommends **C**, fallback **B**.
2. **Ingestion ownership — the one factual question that swings D1:** *"Going forward, is
   oxidicom the **only** intended DICOM ingestion path into CUBE, or are other routes planned —
   STOW-RS uploads, S3 bulk import, plugin outputs into the PACS tree?"* → "only oxidicom" favors
   **B**; "others too" favors **C**. (Raised by Adam in review §5/§6.)
3. **STOW-RS scope (D3).** MVP proposal said no; grant says yes. We reconciled to the grant
   (IN) — confirm BCH agrees before Phase C view code is sized.
4. **MOC instance.** Is there a ChRIS instance already running on the Mass Open Cloud, or does one
   need to be stood up? Lead time? (Open since the MVP proposal, Open Item #2.)
5. **Pre-approval funding.** Confirmation (even informal/in writing) that BCH's internal headcount
   budget can fund the L2 work *before* grant approval lands.
6. **Comms channel + kickoff.** ISC needs inviting to BCH's Matrix server; the kickoff call Joshua
   mentioned (`[42:10]`) is not yet scheduled.
7. **Patient-tag consistency across a study** (data question for D2): are Patient tags consistent
   across all series of a study in BCH-imported data? One query answers it; affects `PACSStudy`
   find-or-create logic.

🔒 **Internal-side open items** (don't raise these in the room): who is the **engineer of record**
for L2 (Alex + Tommy were to decide post-May-19); whether to adopt the **Codex / AI-driven
iteration** methodology Adam suggested; whether to share `REVIEW_RESPONSE_ROUND_1.md` with Adam
formally.

---

## 7. Funding & contracting state 🟢 (facts) / 🔒 (implications)

As of the May 19 meeting:

- **Grant approval: NOT YET GIVEN.** Rumor (via Red Hat Summit) had it *"sitting on the CIO of
  ARPA-H's desk — someone called Clark Minor"* awaiting final approval (`[19:09]`). Rudolph called
  this "word on the street," not confirmed. **No green light.**
- **No written agreement with ISC yet.** Verbal scoping only.
- **BCH has internal headcount budget.** Rudolph: *"the budget currently has at least space for
  two more people to be onboarded"* (`[14:26]`). This is **grant-allocated BCH budget**, not BCH
  discretionary money — clarified at `[17:46]`–`[18:00]` ("it was BCH budget, the BCH budget part
  of the grant"). It *could* fund ISC pre-approval as a contractor instead of BCH hiring directly.
- **Working partly on spec.** Joshua: *"we've already established that you guys are able to work
  to some degree on spec while we're waiting for funding to get approved"* (`[10:56]`).
- **Hire-from-scratch alternative is slow.** If BCH hired directly instead of contracting ISC,
  there's a **~4-month lag** before someone's onboarded (`[15:10]`) — which is part of why
  contracting ISC is attractive to them.
- **Budget structure:** each prime (GH / BCH / UoU) **gets its own chunk** to spend as it sees fit
  — it does *not* all flow through Gradient Health (`[22:48]`). So ISC's work is "building ATLAS,
  the BCH part" — Rudolph corrected Alex's "building for BCH vs Gradient" to "building Atlas"
  (`[20:39]`–`[22:35]`).
- **Gradient relationship:** ISC and BCH discussed wanting to sit down with Gradient at some point
  so GH isn't surprised to find ISC on grant budget (`[19:44]`). Not yet done.

> 🔒 **Implication (ISC_DELIVERABLES.md):** L1 effort is bounded and reasonable to absorb on spec.
> **L2 should be conditioned on at least a verbal commitment** that the headcount budget applies to
> ISC. **L3 requires the grant to actually land.** Don't let the room talk us into starting L2
> implementation with no funding commitment.

---

## 8. 🔒 INTERNAL-ONLY — scope-creep guardrails + redirect language

> Entirely from `SCOPE_GUARDRAILS.md`. **None of this framing is spoken aloud as "guardrails."**
> The *redirect sentences* themselves are fine to use; the *labels* and *predictions* are not.
> Adam's note walking in: *"be ready for a few curve-balls depending on who they invite… plus some
> attempts of scope creep to get us to do other things (security hardening comes to mind)."*

**Tone for all redirects: never reject the ask, always scope-and-defer.**

### Likely ask: "Could you also look at security hardening?"
*Why it'll come up:* it's Joshua's stated #1 priority (`[10:56]`).
> **Redirect:** *"That's the hardening work Joshua mentioned — we agree it's important and it's
> pre-MVP. We'd want to scope it as a separate engagement after the DICOMweb research is closed
> out, so we don't entangle the two deliverables. Happy to give a rough sizing offline. Is the
> security review your engineer is doing turning up anything we should know about for the DICOMweb
> endpoint design?"*

### Likely ask: "Could you do the auth review for the existing endpoints?"
> **Redirect:** *"Auth review is bundled with the hardening work for us — we'd scope it as a unit.
> For the DICOMweb endpoints specifically, we're inheriting CUBE's existing auth chain unchanged,
> so concerns about the chain affect us and you equally."*

### Likely ask: "Could you also do de-identification?"
*Why:* grant §2.7.5 ($1.039M, BCH-led) may not be staffed yet.
> **Redirect:** *"§2.7.5 is its own workstream with its own budget. It's adjacent — the
> de-identified at-rest format feeds our DICOMweb endpoints — but it's meaningfully separate
> engineering. Happy to discuss as a follow-on once §2.6.1.6 ships."*

### Likely ask: "Could you handle the OHIF integration?"
> **Redirect:** *"OHIF should work out of the box against a DICOMweb-compliant CUBE — that's the
> whole point of the standards work. We'll validate against it in smoke testing, but we're not
> customizing OHIF. Bespoke OHIF deployment/branding is a separate engagement."*

### Likely ask: "Could you handle the §2.7.1 at-rest format work?"
> **Redirect:** *"§2.7.1 is $946k of BCH-led work — adjacent but much larger. We'd discuss as a
> follow-on after §2.6.1.6, especially because the at-rest format and our endpoints converge by
> Month 12. Happy to be a sounding board in the meantime."*

### Likely ask: "We need someone full-time on our Matrix / standups."
*Why:* Joshua asked for "an engineer thrown at us" in calls/planning/pointing (`[13:12]`).
> **Redirect:** *"We can absolutely have someone in your standups and Matrix for the duration of
> the research and L2 work. Beyond that we'd roll the embedded-engineer arrangement into a
> longer-term contract — happy to discuss FTE allocation and rate."*

### Likely ask: "Could you start implementation before grant approval?"
> **Redirect:** *"We can begin L2 on BCH's internal headcount budget if that's allocated — the
> path Rudolph described May 19. We'd want a written scope-and-payment agreement before code lands,
> but it's straightforward to set up once you have a go-ahead on your side."*

### Default template for un-pre-walked asks
> *"That's outside the current scope of the DICOMweb research and MVP work, but it's a reasonable
> ask. We can scope it as a separate engagement after §2.6.1.6 ships — happy to give you a rough
> estimate offline once we understand it in more detail."*

### 🔒 What ISC is NOT delivering (the boundary, for Marty's own reference)
Security review · auth hardening/re-architecture · HIPAA/de-identification (grant §2.7.5) ·
performance hardening of *existing* endpoints · test-coverage backfill on legacy CUBE · multi-site
federation (Phase 2, GH's "ATLAS DICOMweb gateway" §2.7.1.2) · live researcher UX / cohort builder
(Phase 2, GH already built it) · production ops / on-call · ChRIS Airflow runtime work (§2.6.1.1–5,
$1.53M) · OHIF hosting/customization · OpenShift/TD deployment (§2.2) · federated learning/MedGemma
(§2.9.3).

---

## 9. 🔒 The "research output, not escalation" framing (Adam's instruction)

This is the single most important *internal* instruction for the meeting, from
`REVIEW_RESPONSE_ROUND_1.md` and the README timeline:

- Adam's Round-1 review surfaced the architecture choices as things he'd originally written as
  **"Recommend escalating to Rudolph + Joshua"** (REVIEW_RESPONSE §2, §10).
- Adam then **approved the response framing but warned**: BCH-facing materials must be framed as
  **research output, not escalation.** `RESEARCH_TICKET_OUTPUT.md` therefore **bakes the
  architectural recommendation into the prose** ("our recommendation is C, with a fallback to B")
  rather than presenting a "please make these decisions" block.

**What this means for Marty in the room:**
- ❌ Don't say: "We need you to decide between A, B, and C." (escalation)
- ✅ Do say: "We researched where the endpoints should live, and we recommend the hybrid approach
  for these reasons; the one thing that would change our recommendation is whether oxidicom is your
  only ingestion path going forward." (research output + one genuine question)
- The whole posture is: *we did the research you asked for, here's what we found, here's what we'd
  do, here's the one thing we need from you.* Confident, not asking permission.

---

## 10. Curve-ball Q&A (scope / business / engagement)

> Technical Q&A is in `MEETING_BRIEF.md` §6 (Q1–Q16). This is the *scope/engagement/money* set.
> 🟢 = safe to say aloud; 🔒 = internal context Marty should *know* but not necessarily volunteer.

**Q. What exactly did we commit to?** 🟢
A research write-up of what CUBE needs for DICOMweb compliance, plus a discussion to break it into
tickets. That's it — Joshua's "research ticket" (`[25:32]`), sized at "a day or two." No code was
contractually promised; no signed agreement exists. Everything beyond the document is conditional
on your greenlight.

**Q. Why did you build code if it was just a research ticket?** 🟢 (lead) / 🔒 (don't over-claim)
*Say:* "The research surfaced design questions — where indexing hooks in, whether the hierarchy
should be explicit, where the endpoints live. The cleanest way to *de-risk* those answers was to
prototype the foundation (Phase A) and validate it against the real test suite. So the code isn't
scope creep — it's evidence the proposed design actually works. Phase A is small (~550 lines), all
tests pass, zero schema drift, and it survives every architecture variant we're recommending."
*🔒 Internally:* yes, we went well past "a day or two" — we built a full L2 QIDO/WADO/STOW impl. The
internal recommendation was to close the L1 loop with BCH *before* sinking more on-spec time. Don't
frame it as "we did way more than you asked"; frame it as "we de-risked the design."

**Q. Why only QIDO + WADO in the MVP but all three (incl. STOW) in the grant?** 🟢
"Two different documents written at different times. ISC's May-1 MVP framing listed QIDO + WADO as
the minimum to prove the 'research without raw-data access' story — STOW (upload) wasn't needed for
that demo. The grant's §2.6.1.6 contractual language puts all three under one Month-12 deliverable.
We reconciled **to the grant** — STOW is in scope, and Phase A already supports it at the data
layer." (D3.)

**Q. Is *this* in scope for the day-or-two ticket?** 🟢 (the scope-creep test)
Use the §8 redirect language. The honest answer for security/hardening/de-id/OHIF-customization is:
"That's outside the current DICOMweb research and MVP scope — it was explicitly deferred on May 19
as pre-MVP. Reasonable ask; we'd scope it as a separate engagement after §2.6.1.6 ships." For
indexing-as-a-service specifically: "That was deferred May 19 to 'the actual product down the
line' — Rudolph's framing — and our research treats it as an open question, not part of this MVP."

**Q. Who's funding this?** 🟢
"The DICOMweb work is grant budget — TA2 §2.6.1.6, $109k, BCH + Red Hat as the named primes inside
the $1.39M §2.6.1 parent task. Each prime gets its own budget chunk to spend; it doesn't all route
through Gradient. The grant isn't formally approved yet — last we heard it was awaiting final
ARPA-H sign-off. In the meantime, BCH has internal headcount budget (grant-allocated) that could
fund early work; that's the conversation we'd want to have before more implementation lands."

**Q. Has the grant been approved?** 🟢
"Not as of our last conversation. The word at Red Hat Summit was that it was sitting with ARPA-H's
CIO awaiting final approval — but that was rumor, not confirmed. We've been working partly on spec
on that understanding."

**Q. Is there a contract / written agreement?** 🟢
"Not yet — it's been verbal scoping so far. For the implementation phase we'd want a written
scope-and-payment agreement before code lands, but that's straightforward to set up once you have a
go-ahead on your side."

**Q. What's the timeline?** 🟢
"For the single-PACS MVP (L2): roughly **5–6 weeks, one engineer** — Phase A is already done, the
remaining work is the query/render layer (B), the endpoints (C), integration tests + a backfill
command (D), and polish (E). That's separate from the grant deliverable, where §2.6.1.6 wants all
three endpoints **operational within TD constraints by Month 12** and a **regression suite by Month
15**. There'd been an early hope of a June kickoff (Joshua) but that depends on funding clarity."

**Q. What do you need from us?** 🟢
"Really one decision and one fact. The decision: confirm you're happy with the hybrid architecture
recommendation. The fact that drives it: *going forward, is oxidicom your only DICOM ingestion path,
or are other routes planned?* Beyond that: confirm STOW is in scope (we reconciled to the grant),
let us know the MOC instance status, get us onto your Matrix server and a kickoff call, and confirm
whether the internal headcount budget can fund the next phase pre-approval."

**Q. Who on your side does the engineering work?** 🟢 / 🔒
*Say:* "We'd put an engineer in your standups and Matrix for the research and MVP work." *🔒 Internally:*
the specific engineer-of-record for L2 was to be decided by Alex + Tommy after May 19; confirm this
internally before committing a name in the room.

**Q. Can you just join our team as embedded engineers full-time?** 🟢
Use the §8 redirect: "Absolutely for the duration of the research and L2 work. Beyond that we'd roll
it into a longer-term contract — happy to discuss FTE allocation and rate."

**Q. Why are you (ISC) involved at all — isn't this BCH's platform?** 🟢
"BCH leads the technical execution on ATLAS, and your team is small (you mentioned ~3 engineers
plus churn). Hiring directly has a ~4-month onboarding lag. We can plug in immediately on a
well-defined, standards-based piece — DICOMweb on CUBE — and we have the DICOM background for it.
The intent is to *help build ATLAS, the BCH part of it* — Rudolph's framing." *🔒 Don't volunteer
the broader opportunity-pipeline framing (§11) — that's a separate-call conversation.*

**Q. A new stakeholder reopens a scope assumption from May 19.** 🟢
"That's the framing Joshua and we agreed on May 19 — API/interface compliance only, security
deferred as pre-MVP. Happy to revisit if priorities have shifted, but it's on the record as the
agreed scope." Listen first; agree to nothing in the room beyond what was already agreed.

**Q. Does this lock us into ISC for the rest of the grant?** 🟢
"No. This is a defined, standards-based deliverable. If you later hire internally or bring in
another partner, DICOMweb compliance is exactly the kind of clean, documented, standards-conformant
work that hands off well. We're not building anything proprietary or lock-in-shaped."

---

## 11. 🔒 INTERNAL-ONLY — ISC's broader commercial positioning (L4)

> **NEVER raise this in the BCH room.** From `ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx`
> (April 2026, marked *Confidential*). This is ISC's internal map of where else on the ~$8M+ of
> addressable ATLAS work we could win business. It exists so Marty *recognizes* these topics if a
> stakeholder wanders into them — and **stays focused on DICOMweb** (Adam: commercial-development
> conversations are for separate calls).

ISC's stated competitive advantages: (1) **core maintainers of OpenStack Ironic** (bare-metal
provisioning the neocloud depends on — "we literally own the upstream code"); (2) an **open-source
DICOM AI platform** (infant hip-dysplasia detection — the Retuve plugin); (3) broad
**OpenShift/K8s/DevOps/HPC**.

9 ranked opportunities (>$8M total):

| Tier | Task | Budget | ISC angle |
|---|---|---|---|
| T1 | §2.2.2.2 Neocloud Orchestration (MOC bare-metal OpenShift) | $300–500k | Ironic maintainers — unique edge |
| T1 | §2.2.1 TD Infrastructure as Code | $1.09M | OpenShift/K8s/DevOps |
| T1 | §2.9.3 Federated Learning / MedGemma 27B training | $1.8M | DICOM AI alignment — highest $ |
| **T2** | **§2.6.1 Airflow + ChRIS + DICOMweb** | **$1.39M** | **Our current foothold** — the $109k DICOMweb sub-task is the entry point |
| T2 | §3.4.3 VA VINCI integration | $1.5M | explicit FTE slots |
| T2 | §2.9.1.1 Radiology DICOM data loader | $520k | DICOM expertise |
| T2 | §2.7.1 / 2.7.2 DICOM ingestion + standardization | $787k | DICOM standardization |
| T3 | §2.3.2 Developer Ops Tooling | $437k | DevOps |
| T3 | §2.11.17/18 FDA RSTKs scaling | $300k | HPC + OpenShift |

Plus a **marketplace product play**: list the Retuve hip-dysplasia plugin in the ATLAS App
Marketplace (§3.5.1, $700k integration budget) for compute-credit revenue — engaged via Gradient
Health's commercial team.

**These are positioning artifacts, not deliverables.** They become real only if BCH/UoU/GH engage
ISC on those sub-tasks. The DICOMweb work is the *foothold* — do it well, and the relationship
opens the rest. **In the room: stay on DICOMweb.**

---

## 12. The 60-second engagement summary (for Marty to internalize)

> BCH (Rudolph Pienaar, Joshua Kanner) is the primary technical partner on the **ATLAS** grant
> (ARPA-H, ~$60M+, prime **Gradient Health**). On **May 19, 2026** they asked ISC (Alex, Tommy,
> Adam) for a **research/spike ticket**: scope what CUBE needs for **DICOMweb (QIDO/WADO/STOW)
> compliance** — Joshua's "engineer goes off, writes up what's missing and a proposal, we break it
> into tickets." Sized at **"a day or two."** Scope was narrowed to **API/interface compliance
> only** — security and indexing-architecture were explicitly deferred. The grant budgets this as
> **TA2 §2.6.1.6 ($109k)**, with all three endpoints due **Month 12** and a regression suite due
> **Month 15**. ISC delivered the **research output** (`RESEARCH_TICKET_OUTPUT.md` + supporting
> docs) and **de-risked the design** with Phase A + a full L2 prototype. The open questions for
> BCH: **architecture (recommend hybrid C), the ingestion-path question, STOW scope (reconciled IN
> to the grant), MOC instance, pre-approval funding, and comms/kickoff.** **Funding is not yet
> approved; no written agreement exists; BCH has internal headcount budget that could fund early
> work.** Present everything as **research output, not escalation.** Keep the internal L1–L4 /
> commercial-pipeline / guardrail framing **out of the room.**

---

## Sources

- `source-materials/DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md` — May 19, 2026 scoping
  transcript (all `[mm:ss]` timestamps).
- `source-materials/ATLAS_ ChRIS Federated Research Platform -- MVP Proposal.txt` — ISC MVP
  framing (~May 1, 2026).
- `source-materials/ATLAS_Grant_Opportunity_Analysis_BCH_Final.docx` — ISC opportunity analysis
  (April 2026, Confidential). 🔒
- `source-materials/notes.txt` — QIDO-RS / dcm4chee / Mass Open Cloud reference URLs.
- `internal-review/ISC_DELIVERABLES.md` — L1–L4 layering. 🔒
- `internal-review/SCOPE_GUARDRAILS.md` — meeting cheat-sheet + redirects. 🔒
- `internal-review/REVIEW_RESPONSE_ROUND_1.md` — Adam McArthur's review + the "research output, not
  escalation" instruction. 🔒
- `proposal-to-bch/RESEARCH_TICKET_OUTPUT.md` — the BCH-facing lead doc. 🟢
- `proposal-to-bch/QIDO_PLAN.md` — the 5-phase plan. 🟢
- `README.md`, `MEETING_BRIEF.md`, `knowledge-base/00-INDEX.md`,
  `knowledge-base/08-l2-architecture-decisions.md` — repo framing + technical cross-refs.
- Grant proposal `docsend_156_pages.pdf` (referenced, not in-repo) — §2.6.1 p.75, §2.6.1.6 p.78,
  §2.7.1 p.82, §2.7.1.2 p.83.
```
