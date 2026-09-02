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
| **Tests green** | **1946** / 113 modules (regression floor — must never drop) |
| **Model** | `qwen3:8b` via Ollama · vision `qwen3-vl:8b` |
| **Phase** | 4E-H complete → next: latency |
| **Router accuracy** | **97.0–97.8%** (130–131/134) · 0 dangerous false positives · target ≥95% ✅ |
| **Agency accuracy** | **100%** (35/35) · 0 unrequested actions · 15/15 consent · target ≥90% ✅ |
| **Tool selection** | **95.6–97.8%** (43–44/45) · 0 research→browser · 0 UI false positives · target ≥90% ✅ |
| **Execution** | **22/22** scenarios · verified vs unverified reported separately · target ≥20 ✅ |
| **Continuity** | **59/59** checks over 22 conversations · target ≥90% ✅ |
| **Runtime lifecycle** | **17/17** automated cases · 10-item manual checklist ✅ |
| **Failure & recovery** | **26/26** scenarios · 0 unbounded waits · cancellation stops the plan ✅ |

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
| 7 | 4E-F memory & continuity | `[x]` | 20 conversations, ≥90% reference accuracy |
| 8 | Startup & shutdown | `[x]` | clean start, clean stop, mic released |
| 9 | Failure & recovery | `[x]` | every failure ends in one of 5 terminal states |
| 10 | Latency | `[~]` | ~2–3s conversation, fast interrupt |
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

### 7. 4E-F memory & continuity `[x]`

**59/59 checks** across **22 conversations / 57 turns**, asserted on every suite
run. Full analysis: [docs/MEMORY_CONTINUITY_BASELINE.md](docs/MEMORY_CONTINUITY_BASELINE.md)

| Measure | Result |
|---|---|
| reference resolution | 5/5 |
| correction accuracy | 3/3 |
| goal continuity | 47/47 |
| stale-context errors | 1/1 |
| ambiguity handling | 3/3 |

**Context minimisation already held** — the prompt carries a bounded 20-message
window, the router 6 turns, and a follow-up's whole continuity payload is a
subject, a little background and ≤8 candidate names. A test now pins that so
"just send the transcript" cannot creep back in.

**Two real gaps, both fixed:**

- **Result references resolved against nothing** *(referent-resolution failure)*.
  `RecommendationProblem.candidates` was stored, logged and **never read back**,
  so "open the second one" resolved against nothing. `brain/references.py` now
  owns the counting vocabulary — the browser planner imports the same tables
  instead of keeping its own. An index past the end is **deliberately not
  clamped**: handing back the last one is how the wrong thing gets opened.
- **Similarity counted for nothing in ranking** *(ranking failure)*.
  `MemoryRanker` weights similarity at **0.50**, its largest term, and
  `MemoryManager.search` discarded the FAISS distances — so every memory scored
  an identical 1.0 and ranking was decided purely by importance, recency and
  access count. Distances now flow through.

**Known limitations, stated rather than hidden:** an analogical follow-up
("do the same for keyboards") inherits the target but **not** the criteria;
long-term recall is verified by hand rather than in the suite (it needs the
embedding model); a deleted memory leaves an orphaned FAISS vector, which the
search loop skips.

### 8. Startup & shutdown `[x]`

**17/17** automated lifecycle cases, plus a 10-item manual checklist for what
needs real hardware. Full analysis: [docs/RUNTIME_BASELINE.md](docs/RUNTIME_BASELINE.md)

- [x] Startup reaches an explicit READY, never reported before required parts are up
- [x] **Partial startup unwinds what already came up** — the central fix
- [x] Optional subsystems degrade (no mic → text mode); required ones abort
- [x] `WebSocketServer.stop()` added — it had none; 3 bind/release cycles asserted
- [x] Signal handlers (SIGTERM/SIGINT/SIGBREAK) reach the same cleanup as Ctrl+C
- [x] Electron termination escalates `.terminate()` → `.kill()`
- [x] **No broad kill logic** — asserted by test; Electron kills by `/pid … /T`, never `/IM`

**The defect.** Startup ran as module-level statements with the `try/finally`
beginning *after* them, so any failure exited on a traceback with port 8765
bound, an Electron window open, and the browser service and MCP subprocess
still running. `core/lifecycle.py` registers each subsystem's cleanup the
instant it starts — demonstrated live twice when a `NameError` and a bound
port both produced a clean abort with nothing orphaned.

**Also found:** `_thread.interrupt_main()` could not stop the loop while it
was blocked on the microphone in C — which is *why* Electron resorts to
taskkill. The mic is now paused first to unblock the read.

> **Known limitation, carried to the next phase:** `ChatEngine()` is built at
> module import, outside the lifecycle, and nothing bounds it. It
> intermittently hung after the MCP handshake. **Isolated by stashing my
> changes and reproducing on the original code** — pre-existing, and
> environmental (repeated force-kills leaving audio device state). A hang is
> neither a clean degrade nor a clean abort.

### 9. Failure & recovery `[x]`

Every failure must terminate as exactly one of:
`SUCCESS` · `SAFE RETRY` · `NEEDS USER INPUT` · `CLEAR FAILURE` · `CANCELLED`

Never: infinite retry · silent failure · random machine actions · crash loops.

**26/26** scenarios, all deterministic and in the ordinary suite.
Full analysis: [docs/FAILURE_RECOVERY_BASELINE.md](docs/FAILURE_RECOVERY_BASELINE.md)

**Three real defects found and fixed:**

- **`TaskPlanner` had no notion of cancellation at all** — `grep -c cancel`
  returned **0**. A cancelled multi-step task carried on dispatching browser
  and UI actions to the end. The engine's cancel token is now asked at the
  three moments a new action can begin: before planning a step, before
  dispatching it, and before a retry. A cancelled 3-step plan now dispatches
  **zero** tool calls and reports `CANCELLED`, not a bare `stopped`.
- **An unbounded `_ready.wait()`** in browser-service startup — the call path
  above it already had a timeout for exactly this reason; startup was missed.
  A test now asserts **no bare `.wait()`/`.join()`** remains anywhere.
- **A raw tool exception reached the retry with nothing attached** — no
  failure code, no `info`, so the replanner could not see what went wrong and
  the retry was the same attempt again.

**Startup hang containment.** `build_within()` bounds `ChatEngine()` (240s,
`ELAINA_ENGINE_TIMEOUT`). Python cannot interrupt a thread blocked in C, so it
does not try: it bounds how long the *caller* waits and leaves the stuck work
on a daemon thread the interpreter abandons at exit. Honest limitation — a
constructor abandoned midway may hold things nobody references; **the root
cause remains unknown**, contained rather than fixed.

**Mid-task correction:** cancel-and-replace, the simpler reliable option.

### 10. Latency `[~]`

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
- **4E-F complete.** Continuity **59/59** across 22 conversations. Fixed two
  real gaps: ordinal references never read the stored candidate list, and
  memory ranking discarded FAISS distances so its largest weight was a
  constant. Regressions held — tool 44/45, agency 35/35, router 131/134,
  0 dangerous false positives. Suite 1892 → **1903**.
- New: `brain/references.py`, `tests/continuity_matrix.json` (22 conversations),
  `tests/test_continuity_matrix.py`, `scripts/continuity_report.py`,
  `docs/MEMORY_CONTINUITY_BASELINE.md`.
- Reverted an alias-consistency change to `conversation_focus` after **eight
  existing tests** showed a correction is deliberately taken verbatim.
- **4E-G complete.** Startup/shutdown given an explicit owner:
  `core/lifecycle.py`, **17/17** automated cases, plus `WebSocketServer.stop()`,
  signal handlers, a graceful `shutdown` command, and Electron kill escalation.
  Regressions held — tool 44/45, agency 35/35, router 131/134, 0 dangerous
  false positives. Suite 1903 → **1920**.
- New: `core/lifecycle.py`, `tests/test_runtime_lifecycle.py`,
  `docs/RUNTIME_BASELINE.md` (incl. a 10-item manual checklist).
- Open: `ChatEngine()` can hang at import — now **contained** by a bounded
  build; root cause still unknown.
- **4E-H complete.** Failure/recovery: **26/26** scenarios. Found and fixed
  three real defects — the planner ignoring cancellation entirely, an
  unbounded wait in browser startup, and tool exceptions reaching retries with
  no reason attached. Regressions held — tool 44/45, agency 35/35, router
  131/134, 0 dangerous false positives. Suite 1920 → **1946**.
- New: `tests/test_failure_recovery.py`, `core.lifecycle.build_within`,
  `docs/FAILURE_RECOVERY_BASELINE.md` (incl. 6 manual cases).
