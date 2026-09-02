# Elaina v1 — Seattle Release Plan

**Deadline:** September 13, 2026 · **Target tag:** `elaina-v1-seattle` (alias `v0.4e-stable`)

**The goal is not more capabilities.** It is making the capabilities that already exist
dependable enough to use every day. Prefer 8 that work reliably over 30 that work
sometimes.

---

## Current status

| | |
|---|---|
| **Branch** | `phase4e-stabilization` |
| **Last checkpoint** | `v0.4e-baseline` — 851c8bd5 |
| **Tests green** | **1892** / 110 modules (regression floor — must never drop) |
| **Model** | `qwen3:8b` via Ollama · vision `qwen3-vl:8b` |
| **Phase** | 4E-E complete → next: 4E-F memory & continuity |
| **Router accuracy** | **97.0–97.8%** (130–131/134) · 0 dangerous false positives · target ≥95% ✅ |
| **Agency accuracy** | **100%** (35/35) · 0 unrequested actions · 15/15 consent · target ≥90% ✅ |
| **Tool selection** | **95.6–97.8%** (43–44/45) · 0 research→browser · 0 UI false positives · target ≥90% ✅ |
| **Execution** | **22/22** scenarios · verified vs unverified reported separately · target ≥20 ✅ |

**Rollback at any time:**

```bash
git checkout v0.4e-baseline
```

**Run the full suite** (use the venv — system Python lacks the deps and will
report ~46 phantom import failures):

```bash
.venv/Scripts/python.exe tests/run_tests.py
```

---

## Status legend

`[ ]` not started · `[~]` in progress · `[x]` done, exit criteria met · `[!]` blocked / at risk · `[-]` deferred past v1

---

## Milestones

| # | Phase | Status | Exit criteria |
|---|---|---|---|
| 1 | Baseline & intent benchmark | `[x]` | tag exists, router baseline recorded, ≥50 routing cases |
| 2 | 4E-A natural status messages | `[~]` | 20 tool interactions, no repetition or spam |
| 3 | 4E-B intent understanding | `[x]` | ≥95% routing, **zero** dangerous false-positive actions |
| 4 | 4E-C agency & recommendation | `[x]` | 30 scenarios, offers resolve, rejections stop |
| 5 | 4E-D tool selection | `[x]` | 40 scenarios, 90–95% first-choice correct |
| 6 | 4E-E execution & verification | `[x]` | 20 multi-step tasks verified by final state |
| 7 | 4E-F memory & continuity | `[~]` | 20 conversations, ≥90% reference accuracy |
| 8 | Startup & shutdown | `[ ]` | clean start, clean stop, mic released |
| 9 | Failure & recovery | `[ ]` | every failure ends in one of 5 terminal states |
| 10 | Latency | `[ ]` | ~2–3s conversation, fast interrupt |
| 11 | Long-session soak | `[ ]` | zero crashes, bugs triaged |
| 12 | Freeze & release | `[ ]` | full suite, release report, tag |
| — | 27B/35B model experiment | `[-]` | **deferred** — see Risks |

---

## Phase detail

### 1. Baseline & intent benchmark `[x]`

- [x] Commit verified-green working tree
- [x] Tag `v0.4e-baseline`
- [x] Record live router accuracy — **91.8%** (123/134), see [docs/ROUTER_BASELINE.md](docs/ROUTER_BASELINE.md)
- [x] Extend matrix to 129 cases incl. 16 `conversational_lookalike` negatives
- [x] Add an invariant test so the negative class cannot be quietly weakened

**Why this is first, not 4E-A:** the project had 2,764 uncommitted lines and zero
tags. "Easy to revert" was impossible. Everything after this needs a baseline number
to prove it helped.

### 2. 4E-A natural status messages `[~]`

**Already built — do not rebuild.** `brain/action_status.py` (`ActionStatusSelector`)
picks status lines locally with **no model call**, from 10 execution banks × 6 phase
banks, with a non-repeating window. `should_announce()` returns `None` for work under
1.5s, so trivial turns stay silent.

`brain/social_lines.py` does the same for greetings; `brain/brief_response.py` owns
outcome-locked lines that name a subject and are validated against real status.

- [ ] Run 20 consecutive tool interactions, confirm no repetition or spam
- [ ] Confirm no status message on casual conversation

### 3. 4E-B intent understanding `[x]`

Must distinguish: conversation · information · recommendation · research · computer
action · browser task · coding · memory · follow-up · correction · cancellation.

**97.0%** (130/134) · **0 dangerous false positives** · reproduced 3 consecutive
runs with identical failures. **Both exit criteria met** (baseline was 91.8%).
Full analysis: [docs/ROUTER_BASELINE.md](docs/ROUTER_BASELINE.md)

The negative class now exists (16 cases) and passed 16/16 on the safety property —
`computer_operation` was `none` or `unsupported` every time:

- "I like Spotify." → conversation ✅
- "I'm thinking about getting a monitor." → conversation ✅
- "Good restaurants in Seattle?" → web_search, **not** browser control ✅

**Remaining work — 8 of 11 failures are prompt/metadata, not model capability:**

- [x] **A.** `action_target` contract enforced deterministically; `_SPOKEN_SEARCH`
      now strips "in **a** new browser tab".
- [x] **B.** Capability boundaries sharpened in the existing correction layer —
      page deictics with modifiers ("this **hotel** page"), a named page beating
      an unknown surface, the previously-unguarded reverse direction, and
      raise-a-window vs list-windows.
- [x] **C.** Settings-UI changes are **out of scope for v1**. Matrix expectation
      corrected to `unsupported`; the model already answered that way unaided.
- [x] **D.** "Spotify won't play anything today." — **fixed in 4E-C** at the
      correct layer; the case now passes legitimately.
- [ ] **E.** Two judgment calls (`health_advice_3`, `offer_3`) + `screen_3`.
      Low value, deferred.

14 offline tests in `tests/test_router_surface_policy.py` hold these fixed without
needing a live model.

> **Lesson recorded:** a note added to the router prompt for this phase cost two
> *unrelated* cases and was reverted. A small model reads the prompt as one
> weighted whole — prefer the deterministic correction layer over prompt text.

**Exit:** ≥95% correct routing. Any dangerous false-positive machine action is a
critical failure regardless of the percentage.

### 4. 4E-C agency & recommendation `[x]`

**100%** (35/35 scenarios) · **0 unrequested actions** · **15/15** consent replies ·
stable over 3 runs. Full analysis: [docs/AGENCY_BASELINE.md](docs/AGENCY_BASELINE.md)

- [x] Accept forms: yes / yeah / sure / okay / sounds good / do it / go ahead / why not
- [x] Reject forms: no / nah / not now / never mind — nothing executes
- [x] Ambiguous: maybe / I don't know / depends — never accept
- [x] Every acceptance resolves to the **stored goal**, never to "yeah"
- [x] "I'm hungry" answers conversationally; it does not open a restaurant search
- [x] Cooldowns preserved: refusal buys silence, no back-to-back offers

**The remark fix.** The router promoted research intents to `action_requested`
without ever asking whether anything had been requested, and the interaction
layer's `NEED_FRESH` branch then executed unconditionally — while the
`NEED_MACHINE` branch beside it had always applied that test. Three separate
rules did the promoting, so the question is now settled once, last, in
`_withhold_unrequested_research`.

`request_explicitness` already existed for this and the model answers `direct`
to **everything**, so `_REQUEST_SHAPE` reads sentence shape deterministically
instead. It is a grammatical test — question mark, wh-opener, auxiliary
inversion, request frame, indirect question, bare imperative — and nothing in
it may ever name a product, app or topic.

> **Also fixed:** `"why not"` was answered `unclear` by the consent classifier
> and the offer silently dropped. The strict local acceptance test already
> knew better but was consulted only for offers carrying a `task_id`; it now
> short-circuits any unambiguous yes, removing a model round-trip from the
> most common path in the flow.

### 5. 4E-D tool selection `[x]`

| Tool | Primary use |
|---|---|
| Web search | current info, research, prices, discovery, comparison |
| Browser control | direct interaction, forms, logged-in content, live state, verification |
| Windows UI control | desktop apps, menus, settings, machine actions |
| No tool | conversation, sufficient reasoning, no external data needed |

**95.6%** (43/45) · **0** research→browser · **0** UI false positives · stable
over 3 runs (baseline 68.9%). Full analysis: [docs/TOOL_BASELINE.md](docs/TOOL_BASELINE.md)

- [x] 45 scenarios covering every boundary in the brief
- [x] Research never defaults to browser control
- [x] Browser control chosen when real page interaction is required (8/8, follow-ups 3/3)
- [x] No-tool answers stay no-tool; remarks reach no surface (4/4)
- [x] Compound `search → shortlist → verify` preserved

**The big one — a map keyed on the wrong field.** `_MACHINE_CAPABILITY` mapped
`route.intent`, carrying `browser_action` / `browser_tab` / `browser_search`
as intents. **The router has never emitted those as intents** — every machine
request arrives as `computer_action` and carries the surface in
`computer_operation`. All three keys were unreachable, so *every* page action
was filed as Windows UI control: 8/8 browser cases and 3/3 follow-ups.
`_SURFACE_BY_OPERATION` now decides, which also makes this layer inherit
4E-B's `ui_action`/`browser_action` corrections instead of re-deriving them.

**A domain read as a verb.** `_AVAILABILITY` matches `book(?:ing)?`, and
"booking**.com**" contains "booking" — so "what do reviews on booking.com say
about the Peninsula?" scored as *live availability* and went to the browser.
Host names are now stripped before the live-state tests; general, not a fix
for one site.

**A mention is not an instruction.** `_NAMES_A_SURFACE` counted a bare domain
as naming a page to drive — the brief's rule encoded backwards. Removed; a
domain with an actual verb still matches, on the verb.

### 6. 4E-E execution & verification `[x]`

**Already built:** `TaskStep` / `TaskStepResult` / `TaskState` / `TaskRunResult`,
a `discover` vs `verify` split, provenance on every fact
(`web_search_snippet` | `browser_observed` | `model_knowledge`), and bounded retries
(`_MAX_CONSECUTIVE_FAILURES = 2`).

**22/22** scenarios reached their expected outcome, asserted on every suite run
(not a live check that can stop being run).
Full analysis: [docs/EXECUTION_BASELINE.md](docs/EXECUTION_BASELINE.md)

- [x] 22 multi-step scenarios covering every case in the brief
- [x] Tool success + failed verification **never** reports SUCCESS
- [x] Retries bounded, and only spent on plausibly-recoverable failures
- [x] Missing information returns NEEDS_USER_INPUT rather than guessing
- [x] Cancellation stops before the remaining steps
- [x] VERIFIED vs EXECUTED_BUT_UNVERIFIED reported separately

**Most of this was surfacing, not building.** Bounded retries already existed;
the sub-planners already verified (`BrowserActionResult.verified` is a
tri-state, and `False` already forced a failure); the failure vocabulary was
already honest, including a whole family meaning *ran but the state never
appeared*. Three things were missing:

- **Nothing classified any of it.** `brain/task_outcome.py` maps every emitted
  code to one of the five states; a drift test scans the source for
  `failure_code=` literals and fails if one is unclassified.
- **Every failure got the same retry budget.** A scope violation was retried
  exactly like a transient stall. Terminal failures now stop without spending
  an attempt; only retryable ones reach the budget.
- **The verification signal was discarded going up.** `ActionPlanResult` had
  no `verified` field, so only the `False` case survived and *verified* vs
  *unchecked* successes were indistinguishable. The tri-state now propagates.

> **A test caught me:** my first classification put `direct_target_not_found`
> in the terminal set. An existing planner test proved the planner legitimately
> recovers from it by trying a different site — the `repeated_` prefix is what
> marks the already-retried case.

**Unverifiable, and documented as such:** a click on an SPA that exposes no
state change, whether a search *answer* is true, and anything past the process
boundary (audio reaching speakers, a form accepted server-side).

### 7. 4E-F memory & continuity `[~]`

Do not rebuild memory. Do not solve continuity by sending the whole transcript.

- [ ] "My major is ECE." → later → "What major did I tell you?"
- [ ] "I'm looking at keyboards." → later → "Find me something cheaper."
- [ ] "I meant Seattle, not Seoul."
- [ ] "Open the second one." / "Do the same for monitors."
- [ ] 20 conversations, 5–10 turns, ≥90% reference accuracy

### 8. Startup & shutdown `[ ]`

- [ ] Backend, Electron, LLM, STT, TTS, VAD, memory, tools, browser, UI, screen, event bus all initialize
- [ ] Shutdown closes Electron, terminates backend, **releases the microphone**, stops audio, cleans owned subprocesses
- [ ] Never kills unrelated system processes

### 9. Failure & recovery `[ ]`

Every failure must terminate as exactly one of:
`SUCCESS` · `SAFE RETRY` · `NEEDS USER INPUT` · `CLEAR FAILURE` · `CANCELLED`

Never: infinite retry · silent failure · random machine actions · crash loops.

- [ ] no internet · browser closes mid-task · app missing · wrong window focused
- [ ] permission rejected · UI element missing · STT timeout · silence
- [ ] interruption · cancellation · tool exception · LLM timeout
- [ ] "Open Chrome — actually never mind."
- [ ] Regression test added for every discovered failure

### 10. Latency `[ ]`

**Gap:** `timings{}` covers route, memory retrieval, generation, web search, project
tools, visual pipeline, total. **No TTFT, STT, TTS or VAD timing** — so today the LLM
is the only thing that *can* be blamed.

- [ ] Instrument VAD end-detect, STT, LLM TTFT, tool startup, TTS startup
- [ ] Conversation ~2–3s perceived; local action begins ≤2s; TTS stops fast on interrupt
- [ ] Do not trade reliability for benchmark gains

### 11. Long-session soak `[ ]`

Real use, not just unit tests. Triage into `bugs.md` as P0/P1/P2/P3.
Fix P0 → P1 → important P2. **Do not spend the final days on P3 polish.**

### 12. Freeze & release `[ ]`

No experiments, no refactors, no model work.

- [ ] ~210 scenarios pass at target rates
- [ ] Unsafe actions 100% resolved · task completion ≥95% · intent ≥95% · tool ≥90% · recommendation ≥90% · memory ≥90% · crashes 0
- [ ] Release report: test counts, known limitations, model, config, rollback commit, open non-critical issues
- [ ] Configs + memory DB backed up; clean-restart verified; rollback instructions tested
- [ ] Tag `elaina-v1-seattle`

---

## Risks

1. **`chat_engine.py` is 8,133 lines.** `_answer_turn` is **1,356**, `_route_turn`
   **984**, `__init__` **592** wiring ~45 collaborators. Every remaining phase edits
   this file, and blast radius is invisible. Mitigation: lean on the 1,826-test floor
   after every change; do not refactor during freeze.

2. **Model identity was mislabelled.** Config says `qwen3:8b` — Qwen **3**, not 3.6.
   Installed: `qwen3:8b` (5.2GB), `qwen3-vl:8b` (6.1GB), `qwen3.6:35b-a3b` (23.9GB).
   There is no 27B. Fix the label before any benchmark or the comparison is meaningless.

3. **The big-model experiment is deferred.** `qwen3.6:35b-a3b` is 23.9GB against a
   **16GB RTX 5080** — it cannot fit in VRAM and would run partly on CPU. Expect a
   large latency cost for little gain, against a hard deadline. Revisit only if
   everything above is stable and time remains.

4. **Latency is unmeasurable today** (see Phase 10) — cannot optimize what isn't timed.

5. **No `bugs.md` yet** — created in Phase 11.

---

## Commit convention

One scoped commit per phase; no giant mixed commits.

```
phase4e-intent-baseline
phase4e-agency-fix
phase4e-tool-routing
phase4e-execution-verification
phase4e-memory-continuity
elaina-v1-rc1
```

Checkpoint tag after each phase passes its exit criteria.

---

## Ground rules

- Repository is the source of truth, not any plan document.
- Improve existing systems; never create `*_v2.py` / `new_*.py` / `better_*.py`.
- No systemic fix built from a growing pile of hardcoded phrase checks.
- Diagnose the correct layer: architecture / prompt / state / context / tool metadata /
  planner / router / execution / observation / memory / model capability.
- Bug workflow: reproduce → failing regression test → fix → prove it passes.
- Never weaken or delete a test to make it pass.
- Do not move to the next phase until exit criteria pass.

**Can wait past Sept 13:** fine-tuning · LoRA · major UI redesign · new tool
ecosystems · autonomous agents · model-switching systems.

---

## Progress log

Newest first. One line per meaningful step.

### 2026-09-01

- Assessed repository: 86,100 LOC, 1,826 tests green, 113-case router matrix,
  17 live checks across 3 tiers. Found zero git tags and 2,764 uncommitted lines.
- Committed the verified-green tree as `851c8bd5`, tagged **`v0.4e-baseline`**
  on branch `phase4e-stabilization`. First rollback point the project has had.
- Created this plan.
- **Task 1 complete.** Router baseline measured at **91.8%** (123/134) with
  **zero dangerous false-positive machine actions** — the critical criterion
  passes, the 95% accuracy criterion does not.
- Added a `conversational_lookalike` negative class (16 cases). It passed
  **16/16** on safety: `computer_operation` was `none` or `unsupported` every
  time, including "I like Spotify" and "I'm thinking about getting a monitor".
- Corrected two of my own mis-specified expectations; left
  `lookalike_not_playing` deliberately failing rather than relaxing it — acting
  on a remark is a real finding for 4E-C.
- Classified all 11 failures by root cause: **8 are prompt/metadata, not model
  capability.** See [docs/ROUTER_BASELINE.md](docs/ROUTER_BASELINE.md).
- **4E-B complete.** Fixed Groups A and B in the router's deterministic
  correction layer: **91.8% → 97.0%** (130/134), 0 dangerous false positives,
  stable across 3 consecutive runs. Suite 1827 → **1841**.
- Settings-UI changes ruled **out of scope for v1**; matrix corrected.
- Reverted a router *prompt* edit that cost two unrelated cases — isolated by
  stashing only the router change and re-running, which restored both.
- **4E-C complete.** Fixed "acting on a remark": **35/35** agency scenarios,
  **0** unrequested actions, **15/15** consent replies, stable over 3 runs.
  Router held at **97.8%** (131/134) with 0 dangerous false positives —
  `lookalike_not_playing` now passes legitimately rather than being excused.
  Suite 1841 → **1857**.
- New: `tests/agency_matrix.json` (35 scenarios),
  `scripts/live_agency_check.py`, `tests/test_agency_offers.py` (16 offline
  offer-lifecycle tests).

### 2026-09-02

- **4E-D complete.** Tool selection **68.9% → 95.6%** (43/45), **0** research
  requests defaulting to browser control, **0** Windows UI false positives,
  stable over 3 runs. Router held at 130/134 (97.0%), agency at 35/35.
  Suite 1857 → **1872**.
- Root cause was architectural, not tuning: the capability map keyed on
  `route.intent` and carried three intents the router never emits, so every
  page action became Windows UI control.
- New: `tests/tool_matrix.json` (45 scenarios), `scripts/live_tool_check.py`,
  `tests/test_tool_surface_policy.py` (12 offline tests),
  `docs/TOOL_BASELINE.md`.
- Two failures reported unweakened: `nt_worldcup` (router prefers a checked
  source for a 2018 fact) and `sc_display` (known `describe_window` gap).
- **4E-E complete.** Execution outcomes made explicit: **22/22** multi-step
  scenarios, verified vs unverified reported separately, terminal failures no
  longer spend the retry budget. Regressions all held — tool 44/45 (97.8%),
  agency 35/35, router 131/134 (97.8%), 0 dangerous false positives.
  Suite 1872 → **1892**.
- New: `brain/task_outcome.py`, `tests/execution_matrix.json` (22 scenarios),
  `tests/test_execution_matrix.py`, `tests/test_task_outcome.py`,
  `scripts/execution_report.py`, `docs/EXECUTION_BASELINE.md`.
- Repaired `docs/ROUTER_BASELINE.md`: an earlier `str.replace` silently
  no-op'd, leaving the doc describing the pre-fix state as current.
