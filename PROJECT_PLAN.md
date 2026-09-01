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
| **Tests green** | **1827** / 105 modules (regression floor — must never drop) |
| **Model** | `qwen3:8b` via Ollama · vision `qwen3-vl:8b` |
| **Phase** | Task 1 complete → next: 4E-B intent routing |
| **Router accuracy** | **91.8%** (123/134) · 0 dangerous false positives · target ≥95% |

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
| 3 | 4E-B intent understanding | `[~]` | ≥95% routing, **zero** dangerous false-positive actions |
| 4 | 4E-C agency & recommendation | `[ ]` | 30 scenarios, offers resolve, rejections stop |
| 5 | 4E-D tool selection | `[ ]` | 40 scenarios, 90–95% first-choice correct |
| 6 | 4E-E execution & verification | `[ ]` | 20 multi-step tasks verified by final state |
| 7 | 4E-F memory & continuity | `[ ]` | 20 conversations, ≥90% reference accuracy |
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

### 3. 4E-B intent understanding `[~]`

Must distinguish: conversation · information · recommendation · research · computer
action · browser task · coding · memory · follow-up · correction · cancellation.

**Baseline: 91.8%** (123/134) · **0 dangerous false positives**. The safety half of
the exit criterion already passes; the accuracy half does not.
Full analysis: [docs/ROUTER_BASELINE.md](docs/ROUTER_BASELINE.md)

The negative class now exists (16 cases) and passed 16/16 on the safety property —
`computer_operation` was `none` or `unsupported` every time:

- "I like Spotify." → conversation ✅
- "I'm thinking about getting a monitor." → conversation ✅
- "Good restaurants in Seattle?" → web_search, **not** browser control ✅

**Remaining work — 8 of 11 failures are prompt/metadata, not model capability:**

- [ ] **A.** Define what `action_target` must contain per operation (4 cases).
      `ui_action` must keep the app qualifier ("in Spotify"); `browser_search`
      must drop the surface qualifier ("in a new browser tab"). Opposite rules,
      neither stated.
- [ ] **B.** Sharpen boundaries between adjacent capabilities (4 cases):
      `ui_action` vs `list_windows`, `browser_action` vs `ui_action`,
      `screen_analysis` vs `list_windows`.
- [ ] **C.** Decide: are Settings-UI changes in scope? Matrix expects `ui_action`,
      model says `unsupported`, README says Phase 4A excludes settings. **Needs you.**
- [ ] **D.** "Spotify won't play anything today." acts on a remark → defer to 4E-C.
      Left deliberately failing.
- [ ] **E.** Two judgment calls, low priority.

Fixing A and B alone clears 95%.

**Exit:** ≥95% correct routing. Any dangerous false-positive machine action is a
critical failure regardless of the percentage.

### 4. 4E-C agency & recommendation `[ ]`

**Already built:** `PendingCapabilityOffer` (`security/capability_offer.py`) carries
`intent`, `capability_id`, `goal`, `task_id`, `task_query`, `expires_at` — so
"Yeah" resolves to *find restaurants nearby*, not to "yeah".
`brain/deliberation/pending.py` refuses replies that are actually new requests.

- [ ] Accept forms: yes / yeah / sure / okay / sounds good / do it / go ahead / why not
- [ ] Reject forms: no / nah / not now / never mind
- [ ] Ambiguous: maybe / I don't know / depends
- [ ] "I'm hungry" asks what they want — it does not open a restaurant search
- [ ] Cooldown preserved: no repeated offers

### 5. 4E-D tool selection `[ ]`

| Tool | Primary use |
|---|---|
| Web search | current info, research, prices, discovery, comparison |
| Browser control | direct interaction, forms, logged-in content, live state, verification |
| Windows UI control | desktop apps, menus, settings, machine actions |
| No tool | conversation, sufficient reasoning, no external data needed |

Compound strategies allowed (search → shortlist → verify). Research must **not**
automatically drive the browser.

- [ ] 40 scenarios; classify every failure by root cause (router / metadata / context / planner / state / model)

### 6. 4E-E execution & verification `[ ]`

**Already built:** `TaskStep` / `TaskStepResult` / `TaskState` / `TaskRunResult`,
a `discover` vs `verify` split, provenance on every fact
(`web_search_snippet` | `browser_observed` | `model_knowledge`), and bounded retries
(`_MAX_CONSECUTIVE_FAILURES = 2`).

**Gap:** outcomes are strings, and `RETRYABLE` vs `TERMINAL` failure is not explicit.

- [ ] 20 multi-step tasks; a task passes only when the **final expected state** is verified
- [ ] Tool success is never treated as goal success

### 7. 4E-F memory & continuity `[ ]`

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
