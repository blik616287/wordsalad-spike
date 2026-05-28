# Scope guardrails — for the upcoming BCH meeting

Internal-only cheat sheet for Alex, Tommy, and whoever joins the BCH meeting. Anchored to the May 19 scoping conversation (`DICOMweb-QIDO-RS-intial-scoping-16d6d9b4-e0be.md`) and the layered scope document (`ISC_DELIVERABLES.md`).

Adam's note worth remembering as we walk in: *"be ready for a few curve-balls depending on who they invite to the meeting, plus some attempts of scope creep to get us to do other things (security hardening things comes to mind)."*

---

## ISC is delivering

| Layer | Scope | Status |
|---|---|---|
| **L1. Research output** | A research-ticket-shaped write-up of what's missing in CUBE for DICOMweb, what we propose to do, with supporting code that derisks the design. | **Done.** Packaged in `deliverables/`. |
| **L2. MVP implementation** | DICOMweb endpoints on a single CUBE instance, BCH public dataset re-DICOMized, existing BCH inference plugin runnable through it. ~1–2 months, 1 engineer. | **Not started.** Conditioned on BCH greenlight + funding clarity. |
| **L3. Grant §2.6.1.6** | WADO-RS + STOW-RS + QIDO-RS endpoints operational within TD constraints by Month 12 + regression suite by Month 15. $109k, joint with Red Hat. | **Not started.** Conditioned on ARPA-H grant approval. |

## ISC is *not* delivering (within current scope)

Stated explicitly because each of these has come up or is likely to come up:

| Out of scope | Why | Documented where |
|---|---|---|
| Security review of existing CUBE code | Joshua flagged this May 19 as "pre-MVP" and "nebulous in scope." Alex specifically scoped the spike to *API compliance only*, not security or encryption (Tommy [29:21], Alex [29:58], Joshua [30:14] all confirmed). | May 19 transcript [11:00–13:00, 28:43–30:18] |
| Auth hardening / re-architecture | Same as above. CUBE's existing Token / Basic / Session / LDAP chain is what DICOMweb endpoints inherit; we're not redesigning it. | Same |
| HIPAA Safe Harbor / de-identification | Grant §2.7.5 ($1.039M, BCH prime) covers this as its own workstream. Explicitly out of MVP per the MVP proposal. | `ISC_DELIVERABLES.md` L2; MVP proposal §"What the MVP Is Not" |
| Performance hardening of existing endpoints | Not part of §2.6.1.6. May land naturally for the new DICOMweb endpoints, not for the existing collection+json surface. | `QIDO_PLAN.md` §13 (perf scope is just the new endpoints) |
| Test-coverage backfill on existing CUBE | Same. Our test discipline applies to new code; we don't backfill the legacy. | Phase A precedent — 9 new tests for new code, didn't touch the existing 103 |
| Multi-site / multi-institution federation | Phase 2 per the MVP proposal. The "ATLAS DICOMweb gateway" in grant §2.7.1.2 sits above per-TD CUBE instances; that gateway is GH's scope, not ours. | MVP proposal §"What the MVP Is Not" |
| Live researcher UX / cohort builder UI | Phase 2. GH already built this; we're not duplicating it. | MVP proposal §"What the MVP Is Not"; ISC opportunity analysis §"Search & exploration UX" |
| Production deployment / operations | We deliver code + a working demo, not on-call coverage. Separate engagement if BCH wants it. | Inferred — never proposed |
| ChRIS Airflow runtime work (§2.6.1.1–5) | Adjacent to our $109k DICOMweb sub-task in grant §2.6.1, but separately scoped ($1.53M across five sub-tasks). Could become a follow-on engagement; not this one. | Grant TA2 §2.6.1 page 75 |
| OHIF viewer hosting / customization | OHIF should browse CUBE for free once DICOMweb lands. Customizing OHIF itself is separate. | MVP proposal §"What the MVP Is Not" |
| OpenShift / Trusted Domain deployment work | Grant §2.2 territory ($7.86M), separate workstream. ISC has positioning here (Ironic credential, Tier 1 in our opportunity analysis) but it's not this engagement. | ISC opportunity analysis #1, #2 |
| Federated learning / MedGemma training | Grant §2.9.3 ($1.8M), Tier 1 in our opportunity analysis but not this engagement. | ISC opportunity analysis #3 |

---

## Predicted scope-creep asks + how to redirect

Sized to the actual conversation patterns from May 19. Tone for all redirects: never reject the ask, always scope-and-defer.

### "Could you also look at security hardening?"

**Why it'll come up:** Joshua said it was the team's top priority May 19 [10:56]. Was scoped pre-MVP, but it's the thing he genuinely cares about.

**Redirect:** *"That's the hardening work Joshua mentioned earlier — we agree it's important and pre-MVP. We'd want to scope it as a separate engagement after the DICOMweb research is closed out, so we don't entangle the two deliverables. Happy to give you a rough sizing estimate offline. Is the security review your engineer is doing turning up specific items we should be aware of for the DICOMweb endpoint design?"*

The last sentence redirects gracefully — anything genuinely security-relevant to *our* endpoints (auth wiring, CORS, rate-limiting on QIDO queries) we'd want to know about; we don't have to take ownership of CUBE-wide hardening.

### "Could you also do the auth review for the existing endpoints?"

**Why it'll come up:** Natural extension of the hardening ask. Token / LDAP / Basic auth has been in CUBE a long time.

**Redirect:** *"Auth review is bundled with the hardening work for us — we'd want to scope it as a unit. For the DICOMweb endpoints specifically, we're inheriting CUBE's existing auth chain unchanged, so any concerns about the chain affect both us and you equally."*

### "Could you also work on de-identification?"

**Why it'll come up:** Grant §2.7.5 is $1.039M and BCH-led. They may not have it staffed yet.

**Redirect:** *"§2.7.5 is its own workstream with its own budget line. It's adjacent to our work — the deidentified at-rest format that §2.7.1 produces is what feeds our DICOMweb endpoints — but the de-id pipeline is meaningfully separate engineering. We're happy to discuss it as a follow-on once §2.6.1.6 ships."*

### "Could you handle the OHIF integration too?"

**Why it'll come up:** OHIF is the obvious consumer of QIDO-RS. Easy mental jump for a non-engineering stakeholder to make.

**Redirect:** *"OHIF should work out of the box against a DICOMweb-compliant CUBE — that's the whole point of the standards-compliance work. We're not customizing OHIF, but we'll validate against it as part of our smoke testing. If you want bespoke OHIF deployment or branding work, that's a separate engagement."*

### "Could you also handle the §2.7.1 at-rest format work?"

**Why it'll come up:** It's the upstream of our DICOMweb endpoints and shares a Month-12 deadline.

**Redirect:** *"§2.7.1 is $946k of BCH-led work — adjacent to ours but a much larger scope. We'd want to discuss as a follow-on after §2.6.1.6, especially because the at-rest format design and our DICOMweb endpoints have to converge by Month 12. Happy to be a sounding board on the design choices in the meantime."*

### "We need someone full-time on our matrix server / standups"

**Why it'll come up:** Joshua [13:12] explicitly asked for "an engineer thrown at us that could be part of the calls, part of the planning, pointing." Within the spike scope that's fine; outside it we'd want a contract.

**Redirect:** *"We can absolutely have someone in your standups and on your matrix for the duration of the research and L2 work. Beyond that, we'd want to roll the embedded-engineer arrangement into a longer-term contract — happy to discuss what that looks like in terms of FTE allocation and rate."*

### "Could you start the implementation before the grant approval lands?"

**Why it'll come up:** Joshua [27:49, 30:20] implied a June kickoff hope; Rudolph [14:26] mentioned internal headcount budget that could fund pre-approval work.

**Redirect:** *"We can begin the L2 implementation on BCH's internal headcount budget if that's allocated — that's the path Rudolph described on May 19. We'd want a written scope-and-payment agreement before code lands, but it's straightforward to set up once you have a go-ahead from your side."*

---

## Default response template

For asks we haven't pre-walked:

> *"That's outside the current scope of the DICOMweb research and MVP work, but it's a reasonable ask. We can scope it as a separate engagement after §2.6.1.6 ships — happy to give you a rough estimate offline once we understand what you're looking for in more detail."*

Three things this template does:
- Names the current scope (so the boundary is explicit, not implied).
- Doesn't refuse — the ask is still on the table.
- Defers the sizing conversation to offline, which keeps the meeting on track.

---

## Things to expect (not scope creep, just curve-balls)

Adam flagged this separately: *"a few curve-balls depending on who they invite to the meeting."*

If a new BCH stakeholder is in the room (security person, enterprise-readiness person, a clinical informatics PI), they may:

- **Reopen scope assumptions we settled May 19.** The "pre-MVP / API compliance only" agreement is on transcript. Pointing back at it politely is fine — *"That's the framing Joshua and we agreed on May 19; happy to revisit if priorities have shifted."*
- **Bring up timelines that aren't ours.** Grant Month-12 deadlines are real; BCH-internal deadlines may not be ours to commit against. Listen first, agree to nothing in the meeting beyond what was already agreed.
- **Ask architectural questions we already worked through.** `RESEARCH_TICKET_OUTPUT.md` §"Where DICOMweb endpoints live" has the A/B/C analysis. Pull it up if the conversation goes there.
- **Surface non-DICOMweb opportunities.** ISC's broader pipeline (Ironic / Neocloud / IaC / MedGemma — see ISC opportunity analysis) may come up. Stay focused on the DICOMweb conversation; commercial-development conversations are for separate calls.

## What to bring with you

Open tabs (in order of how often you'll need them):

1. `RESEARCH_TICKET_OUTPUT.md` — the BCH-facing summary. Lead with this.
2. `ISC_DELIVERABLES.md` — the layered L1/L2/L3/L4 framing if scope questions come up.
3. `QIDO_PLAN.md` — the deep technical plan if architecture questions go deep.
4. This document (`SCOPE_GUARDRAILS.md`) — pinned for the redirect language if scope-creep asks come up.

Don't open `REVIEW_RESPONSE_ROUND_1.md` in the meeting — that's internal scaffolding.
