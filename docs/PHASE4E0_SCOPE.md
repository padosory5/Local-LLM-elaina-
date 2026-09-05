# Phase 4E.0 — scope and exit criteria

Not started. This is the scoping record only; nothing here is implemented.

Phase 4E (mechanics) is complete and merged. The target dialogue is a
**different phase of work**: it is about conversational shape, not about
whether the browser obeys.

## Where the target dialogue actually stands

Checked against the code, beat by beat.

| Beat | Status |
|---|---|
| "Seoul next weekend, nice but not insanely expensive" | **Partial** — `RecommendationProblem` has typed slots for AREA, DATES, BUDGET, PREFERENCE, so there is somewhere for each part to land. Not verified end to end from one vague sentence. |
| "I'll check what's actually looking good right now" | **Actively prevented.** `action_commitment.py` treats "I'll check" as a promise and strips it, because qwen3:8b said it and did nothing. The target wants announce-then-act; today the rule is act-then-report. |
| A visual shortlist appears | **Missing.** The only UI events are `assistant_started`, `speech_started`, `screen_region_ready`, `action_approval_*`. Nothing carries candidates to the front end. |
| "I'd look at these three; the second is the best balance" | **Partial** — `candidate_fit.shortlist_text` ranks and `_enforce_named_recommendation` stops an unnamed claim. Spoken only. |
| "Why that one?" without searching again | **Plausible, unverified** — `reasoning_context()` holds the constraints the answer would come from. No test covers "explain without re-running the search". |
| "Actually I want somewhere quieter" | **Works** — refinement retires only PREFERENCE; budget, area and dates survive. This is the hardest beat and it is built. |
| "The first one seems interesting" | **Works** — ordinal resolution against a held candidate set. |
| "Want me to pull up the page?" → "Sure" → opens | **Works, proven** — offer, acceptance, no second confirmation. |

Three work, two partial, two missing, one contradicted by a deliberate guard.

## The honest summary

The **mechanics** are there: constraints survive refinement, ordinals
resolve, an offer can be accepted without re-asking, and a claim she cannot
support gets refused. What is missing is the **shape** — she reports rather
than converses, and there is nothing to look at.

Two cross-cutting blockers sit underneath all of it:

- **Router latency** (9–12s on `route_model`). No amount of correct
  behaviour reads as conversation at that pace. This is the single largest
  obstacle to the target feel.
- **The promise guard**, which currently forbids the announce-then-act
  rhythm the target dialogue is built on.

## Proposed 4E.0 — the smallest phase that changes the feel

Deliberately narrow. Not the whole dialogue.

1. **Announce, then do, then report.** Turn the promise guard from a ban
   into a contract: saying "I'll check" is allowed *when the action runs in
   the same turn*, and remains forbidden otherwise. The guard already knows
   the difference — it takes `action_performed`.
2. **Emit the shortlist to the UI.** One new event carrying the candidates
   already held in `RecommendationProblem`, so a shortlist can be looked at
   rather than only listened to.
3. **Answer "why that one" from held state.** No new search when the
   reasoning context already contains the answer.

### Exit criteria

Nothing proceeds to a later phase until all of these hold:

- A vague opening sentence ("Seoul next weekend, nice but not insanely
  expensive") produces populated AREA / DATES / BUDGET / PREFERENCE slots,
  verified live.
- An announcement of work is either followed by that work in the same turn
  or is not said. No regression in the promise guard's original purpose.
- A shortlist reaches the UI with the same candidates the spoken answer
  names — no shortlist she cannot name, and no name not on the shortlist.
- "Why that one?" answers without a second search, verified by the absence
  of a search in the turn log.
- Full suite, routing 41/41, consent walls 5/5, browser stress 7/7, screen
  browser 11/11, startup to READY.

### Explicitly out of scope for 4E.0

Router latency, page-interaction reliability, and every item on the Phase 4E
known-limitations list. Those are separate work and stay on the backlog.
