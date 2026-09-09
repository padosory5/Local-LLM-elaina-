# Elaina — Milestone Plan

**Where this starts:** `elaina-v1-seattle` shipped. The capabilities work. What
follows is not "more capabilities" either — it is making Elaina *think* like one
system and *sound* like one person, then making her act, then giving her a body,
then giving her a home.

Four milestones, in order, each with a different subject:

| | Milestone | Subject | One-line goal |
|---|---|---|---|
| **A** | **The Mind** | what she decides, and how she says it | One coherent thinking system with a written architecture |
| **B** | **The Hands** | how reliably she acts on the world | Every tool works every time, or fails legibly |
| **C** | **The Body** | expression, movement, presence | She feels like someone in the room |
| **D** | **Elaina Home** | a dedicated always-available device | She lives somewhere, not in a terminal |

Milestone A is specified phase by phase below. B, C and D carry a goal and a
sketch only — their detail should be written when we reach them, against the
Elaina that exists then, not the one we imagine now.

---

## Current status

| | |
|---|---|
| **Branch** | `main` · last commit `c58fe9c` *A5 planning gaps, A6 attribute grounding* |
| **Tests green** | **3167** / 168 modules — regression floor, must never drop |
| **Model** | `qwen3:8b` via Ollama · vision `qwen3-vl:8b` |
| **Router accuracy** | **97.8%** (131/134) · 0 dangerous false positives · target ≥95% ✅ |
| **Tool selection** | **95.6%** (43/45) · 0 research→browser · 0 UI false positives · target ≥95% ✅ |
| **Agency / consent** | 0 unrequested actions · consent cases green ✅ |
| **Conversation quality** | **97%** clean (31/32) EN · 75% KO · target ≥85% ✅ · ⚠️ ±3 turns run-to-run on identical code |
| **Capability contracts** | **11/11** declared · **0** sentences naming internals (was 14) · ability answers bilingual ✅ |
| **Latency** | median **3.8s** · p90 **13.0s** · ⚠️ see the latency budget below |
| **Task reporting** | **25/25** scenarios honest (was 17/22) · cancellation **10/10** ✅ |
| **Attribute grounding** | invented specs **1/6 → 8/8** caught · true sentences **11/11** kept ✅ |
| **Memory** | recall gate **4/18 → 18/18** reachable · forgetting built from nothing · local-first asserted ✅ |
| **Phase** | A1 done · A2 `[~]` · A3 done · A4 `[~]` · A5 done · A6 done · **A7 `[~]`** |

```bash
.venv/Scripts/python.exe tests/run_tests.py     # the full suite, nothing running
```

## Status legend

`[ ]` not started · `[~]` in progress · `[x]` done, exit criteria met ·
`[!]` blocked / at risk · `[-]` deferred

---

# Rules that apply to every phase

These are not phases. They are the conditions a phase has to satisfy to count as
done, and they exist because of specific things that have already gone wrong.

### 1. Name the instrument before the target

A phase ships its **evaluator** as a deliverable, not its conclusion. A1 only
produced a defensible answer because the measuring instrument was built first
and is *the same module the system enforces* — a metric that measures something
the product does not enforce drifts away from it, and then a green report and a
robotic assistant are true at the same time.

A target written as prose ("should rarely need to think about which tool") is
not an exit criterion. A target written as a number against a named script is.

### 2. Extraction is the entry price

`brain/chat_engine.py` is **537 KB**. `_answer_turn` takes 20 parameters and
runs ~1,550 lines; its own docstring calls that "the honest size of what this
phase still needs to know." Every phase below adds a layer to it. A1 added five.

So: **no big-bang refactor, but every phase extracts the stage it touches** into
a named module with a typed hand-off. You are already in that code when you do
the work; the seam is cheap then and expensive later. Milestone A is not
finished until `docs/BRAIN_ARCHITECTURE.md` describes stages that actually exist
as separate things.

### 3. Latency is in the contract

A1 took p90 from 7.7s → 13.0s by adding one conditional model call. Milestone C
is *facial expression and movement* — an avatar that reacts 13 seconds after you
speak is worse than no avatar at all.

**Budget: p50 ≤ 4s, p90 ≤ 8s for a conversational turn** by the end of
Milestone A. A phase that pushes past it either buys the time back or does not
ship. Tool-using turns get their own, looser budget, to be set in B.

### 4. Bilingual is a property, not a feature

After A2, *every* deterministic guard must either work in both languages or
declare itself English-only in its own docstring. A guard that silently passes
everything in Korean is worse than no guard: a quiet detector and a clean
conversation are indistinguishable, which is exactly how two corrupted regexes
survived a full green suite during A1.

---

# Milestone A — The Mind

**Goal:** one coherent thinking system, described in a document that matches the
code, that speaks two languages and sounds like one person in both.

| # | Phase | Status | Exit criterion |
|---|---|---|---|
| A1 | Natural conversation | `[x]` | ≥85% clean turns on the dogfood arcs |
| A2 | Bilingual mind | `[~]` | Korean scores within 10 points of English; no guard silently passes |
| A3 | Context & state ownership | `[x]` | contamination matrix ≥95% — **12/12**; ⚠️ A7 found the matrix is flaky per case, see below |
| A4 | Capability contracts | `[~]` | every capability has typed I/O and a declared failure set — **11/11**; search payload deferred to A6 |
| A5 | Planning gaps | `[x]` | retry, cancel and partial completion covered by scenario tests — **25/25**, cancellation **10/10** |
| A6 | Attribute grounding | `[x]` | no unsourced attribute stated as fact; unknown stays unknown — **20/20**, invented specs 1/6 → 8/8 caught |
| A7 | Memory & personal context | `[~]` | useful across sessions, zero cross-task contamination — recall gate **34/34**, contamination **11–12/12** (matrix is flaky) |

---

## A1 — Natural conversation `[x]`

**Goal:** stop sounding like an assistant interface; sound like one consistent
person, in ordinary chat and in tool-use turns alike.

Complete. Full record in
[docs/CONVERSATION_QUALITY_BASELINE.md](docs/CONVERSATION_QUALITY_BASELINE.md).

- Clean turns **41/64 (64%) → 58/64 (91%)**, findings **29 → 6**
- Tests 2881 → 2960 · router held at 97.8% · consent unchanged
- New layer: [brain/conversation_style.py](brain/conversation_style.py) — the
  conversational **act** a reply performs, a **contract** per act, and fourteen
  named failure classes
- Instrument: [scripts/live_dogfood_conversation.py](scripts/live_dogfood_conversation.py)
  + [scripts/conversation_quality_report.py](scripts/conversation_quality_report.py)

**Carried forward:** `self_repetition` halved but did not clear (model limit);
a factual question inside a social thread is intermittently answered from the
thread — **that one is A3's**, not a style fault.

---

## A2 — Bilingual mind `[~]`

**Goal:** she replies in the language you spoke, switches cleanly when you
switch, and is the *same person* in both.

**Why here and not later.** Nearly every deterministic guard in the system is an
English regex — `conversation_style`'s failure classes, `preferences`' closed-class
word sets, `response_policy`'s closing-offer guard, `response_quality`'s echo
detection. In Korean they do not fire; they silently pass everything. Every phase
after this one adds more such guards. Doing language now costs one audit; doing
it last costs re-auditing everything built in between.

**What already exists**

- `config.yaml` `language.response` — but it is read **once at construction** and
  is session-global (`ChatEngine.response_language`)
- `voice/stt.py:289` already computes `detected_language` and
  `language_probability` from Whisper — **and discards both.** They are used only
  to decide whether to retry transcription. The signal is free and thrown away.
- Korean line banks already exist in `action_status.py` and `recommendation.py`
- `brain/user_locale.py` knows the user's market and language

**What is missing**

- [x] **Language becomes a property of the turn, not the session.** Thread the
      detected language from STT through to `ChatEngine`; typed input uses a
      Hangul-ratio test.
- [x] **A sticky switch policy.** You code-switch — "그 monitor 어때?" is normal
      Korean. Per-utterance detection flip-flops. The reply language changes only
      on a *decisive* full-utterance switch, never on a borrowed noun.
- [x] **An explicit override that wins.** A UI switch and a spoken command
      ("영어로 말해줘" / "speak English"), authoritative until revoked.
- [x] **One personality spec, two renderings.** `personality_en.txt` (42 lines)
      and `personality_ko.txt` (76 lines) **have already drifted into two
      different characters** — Korean has an 예시 section English lacks; English
      has ACTIONABLE ADVICE / KNOWLEDGE AND CALCULATIONS / IMAGES AND SCREEN that
      Korean lacks. One spec, two renderings, plus a test asserting both carry
      the same rules.
- [x] **Guard audit.** Every deterministic guard either works in Korean or says
      in its own docstring that it is English-only. A registry test enumerates
      them so a new guard cannot quietly skip the question.
- [x] **TTS voice per language** — Piper is configured for one voice; the
      switch has to reach the audio boundary too.

**Instrument:** the three dogfood arcs translated into natural Korean, plus a
fourth **code-switching** arc; `conversation_quality_report.py` scores Korean
with Korean-aware detectors.

**Exit criteria**

- Korean clean-turn rate within **10 points** of English on the same arcs
- Language choice correct on **≥95%** of a switch matrix that includes
  code-switching negatives (a borrowed English noun must **not** flip her)
- Explicit override correct **100%** of the time
- **Zero** guards that pass silently in Korean without declaring it
- One Elaina: memory, preferences and task state are shared across languages —
  something told to her in Korean is known in English

**Decisions recorded**

- *One* Elaina who speaks two languages, not two Elainas. Two versions would
  mean either two states (she forgets across languages) or one state with two
  voices — which is one Elaina anyway, built more expensively.
- Korean register is **습니다체**, enforced deterministically by
  `brain/korean_register.py` rather than by prompt wording.
- TTS is **ElevenLabs**: one voice for both languages. Piper voices are
  language-locked, so a Korean Piper voice would be a different woman
  answering.
- **Stay on `qwen3:8b`.** A larger model speaks better Korean and routes
  worse (94.8% against the 95% gate), and the two cannot both be resident on
  this machine — a two-model turn costs ~24s. Korean content quality is a
  documented model limit, not a bug to chase in the style layer.

**Where A2 stands.** Switching is 8/8 across five live runs, register drift
46% → 9%, mixed-language replies 1/16 → 0/16, English quality up from 91% to
94%. Korean sits 19 points behind English, so the exit criterion is **not
met** — but the style metric measures register and shape, not sense, and what
is actually behind is grounding (A6) and context (A3). Remaining A2 work:
`action_commitment`, `response_policy` and `response_quality` are still
English-only, and now declare so.
Full record: [docs/BILINGUAL_BASELINE.md](docs/BILINGUAL_BASELINE.md).

---

## A3 — Context & state ownership `[x]`

**Goal:** every piece of conversational state has exactly one owner, a defined
lifetime, and a rule for what a new turn inherits.

**Why this is the real "brain architecture" phase.** This is the layer that most
makes her feel unpredictable, and it has failed in the same way twice:

- Dogfooding session 1: an open rental task never closed and absorbed every later
  turn containing a number, a date or a noun. By turn 30 the query was
  `studio apartments September 13th 206-221 in South Korea`.
- A1's residual: after four sympathy turns, `what's 2+2` came back
  *"That's straightforward. Need help with."* The router classified it correctly
  as a calculation. **The answer path lost it to the social thread.**

Neither is a planning fault, a tool fault, or a grounding fault. Both are
ownership faults, and they come before capability work.

**What already exists (and is part of the problem)**

`conversation_focus.py`, `recommendation_state.py` (94 KB), `task_session.py`,
`result_state.py`, `context_policy.py`, `references.py`, plus `supersession` and
`_turn_visual_subject` living directly on `ChatEngine`. Several of these can
answer "what are we talking about?" and they do not always agree.

**What to build**

- [ ] **A written state map**: every piece of turn-scoped and session-scoped
      state, its owner, its lifetime, and what clears it. This is the document
      the phase exists to produce.
- [ ] **One subject authority.** Where two modules can answer "what is this
      about", one of them stops.
- [ ] **An inheritance rule.** What a new turn inherits is a decision made in one
      place, not the emergent result of six modules each holding on.
- [ ] **Expiry that is stated, not implicit.** A task that is finished says so; a
      subject that is stale says so.
- [ ] **Extraction:** the routing/state half of `_answer_turn` becomes a named
      stage with a typed `Turn` hand-off (see Rule 2).

> **Correction, found in A7.** A3's 12/12 was a *single run* of a matrix with at
> least two cases that fail intermittently. Measured by re-running them alone:
> `a_new_subject_closes_the_old_one` failed 3 of 7 (a real bug — a held
> recommendation was checked against its own subject, so the check could never
> fail; now 5/5 after `supersession.drops_a_named_subject` was wired into it),
> and `arithmetic_in_a_social_thread` fails about 1 in 6. A live matrix needs
> repeated runs before a number from it means anything.

**Instrument:** a **contamination matrix** — pairs of (prior state, next turn)
where the next turn must *not* inherit, including the two failures above. This is
the negative-class equivalent of the router's `conversational_lookalike` set.

**Exit criteria**

- Contamination matrix **≥95%**, with the two known failures as named cases
- Every state field in the map has exactly one writer
- A factual question after an unrelated thread is answered from the question,
  **10/10 runs**
- Conversation-quality and router numbers do not regress

---

## A4 — Capability contracts `[~]`

**Goal:** a capability declares what it takes, what it returns, and how it fails
— as types, not prose.

**Narrowed deliberately.** The "centralized capability inventory" already exists:
[brain/capabilities.py](brain/capabilities.py) has
`Capability(id, name, summary, needs, examples, offer_when)` plus
`blocked_reason()` and `fix_for()`, and A1 added `spoken_summary`. Availability
state and consent requirements are already modelled. What is missing is
narrower than "build an inventory".

**What is missing**

- [x] **Typed inputs.** `Need(key, kind, asks, inferable)` — a caller can ask
      what is missing and get a checkable answer, and the question to ask for it
      in either language. `calendar_action.when` is declared **not inferable**:
      a guessed time on a real calendar is a wrong appointment.
- [~] **Typed results.** `CapabilityResult` carries `facts`, `source`, and a
      `detail` field that is logged and never spoken. Every **failure** path is
      typed; three of four success paths already were (`browser_outcome`,
      `task_outcome`, `CalculationPlan`). The web-search payload is the
      exception — see below.
- [x] **A declared failure set.** 44 named failures across 11 capabilities,
      each with a sentence in both languages, an optional fix, and `retryable`
      for A5 to read.
- [x] **One selection path.** Turned out to be a different problem than this
      bullet assumed: `capability_selection.select()` is already the only
      dispatcher, and the four `CapabilityRegistry.match()` sites answer a
      different question (an ability *question*, a router dead-end rescue, and
      the commitment guard). The real drift was that the selector could dispatch
      three surfaces the registry denied existing — now registered.

**Instrument:** [`scripts/capability_contract_report.py`](scripts/capability_contract_report.py),
plus `tests/test_capability_contract.py` as the enforcement half. The tool
matrix was *not* extended: contract conformance is a property of the
declaration, not of a routed turn, and `live_tool_check.py` already covers
selection.

**Exit criteria**

- ✅ Every capability has typed I/O and a declared failure set — **11/11**
- ⚠️ No tool result reaches the reply as unstructured prose — **partial**
- ✅ Tool selection accuracy holds at ≥95% — **95.6%** (43/45)
- ✅ A capability cannot be added without a contract — registry test

**The partial one.** The web-search payload still reaches the prompt as
formatted prose, even though `search_web_structured` sits beside it returning
title/url/summary. Typing it properly is **A6's** work: A6 attaches every
attribute to its source with per-attribute confidence, and doing it to A4's
shape now means doing it twice. The concern the criterion was written for — a
raw accessibility tree reaching speech — is separately held by A1's
`speakable_fragment()` and by A4's `redact_internals()`.

**What the phase actually found**

- 14 sentences the user hears named a Python exception class, an inference
  runtime, or a JavaScript framework. Now 0, with a runtime guard on the method
  every reply path ends in.
- **Registering the three surfaces was not enough.** A live probe answered
  "can you commit changes to git for me?" by *running* git, which reported
  nothing staged, which came out as "I can't commit changes to Git right now" —
  the question could not find the capability because `match()` had no pattern
  for it. And "깃 커밋 할 수 있어?" was answered in English, because the registry
  answer path is deterministic and **deterministic is what silently-English
  looks like from outside**. Both fixed; all 11 abilities now describe
  themselves in both languages.
- Three surfaces (`project_edit`, `git`, `agent_building`) were dispatchable by
  the selector and absent from the registry — and `personality_en.txt` line 6
  listed **editing files, Git and calendar** as things to deny, all three of
  which she can do. Same shape as the bug the registry was built to kill.
- **A failed web search was returned as a string and used as evidence** — and
  cached, so one network blip was served as research for the rest of the cache
  window. What separated failure from evidence was a match on the English prefix
  of the failure sentence, in a phase that spent its first hour rewording exactly
  those sentences.

Full record: [docs/CAPABILITY_CONTRACTS.md](docs/CAPABILITY_CONTRACTS.md).

---

## A5 — Planning gaps `[x]`

**Goal:** a multi-step task can be retried, cancelled, and reported on halfway.

**Narrowed deliberately.** [brain/task_planner.py](brain/task_planner.py)
already does Goal → Capability Check → Plan → Execute → Observe → Update State →
Replan, with per-step risk classification and a consent pause before anything
committing. Capability chaining and replanning-after-correction exist. Do not
rebuild them.

**What was missing** — three of these four turned out to be already done, and
reading the code before writing the phase is the rule that caught it:

- [x] **Retry strategy.** Already there. `task_outcome.py` separates `_RETRYABLE`
      from `_TERMINAL`, and a terminal code short-circuits the loop instead of
      spending the budget.
- [x] **Cancellation mid-plan.** The stopping worked; the *reporting* did not.
      A run that had opened Spotify said only "You took control, so I stopped."
      And one of the four places a cancellation can arrive — during a retry —
      had no coverage at all.
- [x] **Partial completion reporting.** The real gap. `partial` is now a sixth
      terminal state, decided from the run's own steps rather than from
      `classify()`, which cannot know whether anything was accomplished.
- [x] **Planning vs execution as separate, inspectable phases.** `_preview()`
      already settles capabilities, verification level and stated intent before
      the first dispatch. Showing a plan for approval before it runs is carried
      forward to C, which will have a surface for it.

**Instrument:** [`scripts/task_report_check.py`](scripts/task_report_check.py)
(what the person hears) and
[`scripts/cancellation_check.py`](scripts/cancellation_check.py) (does "stop"
stop it), plus three new matrix cases including a negative one.

**Exit criteria**

- ✅ Every failure ends in a named terminal state, **0 unbounded waits** — six
  states; planner bounded by `max_steps` and `_MAX_CONSECUTIVE_FAILURES`,
  `_wait_for_port` by a deadline, the service loop by a stop sentinel
- ✅ Cancellation stops the plan and reports honestly — **10/10**
- ✅ Partial completion is reported as partial — with a negative case asserting
  a run that got nowhere is *not* softened
- ✅ Execution matrix green — **25/25**

**What the phase actually found**

The five terminal states, their code table, their drift test and twenty-two
green scenarios — **none of it was ever read outside the tests.**
`_handle_task_action` returned the model's own final planning summary, so four
scenarios told the person **"Done."** on a run whose last step had failed
verification. `ex06`'s own note says *"must never report SUCCESS"*; it never
reported success, it said "Done."

Two instrument corrections, both recorded in the scripts: the report check
condemned the single best sentence in the run (it caught "done" inside the
progress frame), and the cancellation check scored two non-cancellations as
failures (the stop arrived after the plan had finished).

Full record: [docs/PLANNING_GAPS.md](docs/PLANNING_GAPS.md).

---

## A6 — Attribute grounding `[x]`

**Goal:** when she says a thing has a price, a refresh rate, a rating or a
battery life, that number came from evidence.

Names are already grounded — 4E/4F built candidate identity, name quality and
"did it actually find anything" guards. **Attributes are not.** This is the
oldest open item in the project: four grounding issues were left open honestly
after dogfooding session 1, and the model's own memory can still supply a spec
that reads exactly like a retrieved fact.

**What to build**

- [ ] Candidate-level evidence — an attribute is attached to the candidate *and*
      to where it came from
- [ ] Internal source attribution and per-attribute confidence
- [ ] Conflicting-value handling — two sources disagreeing is a state, not a race
- [ ] **Unknown stays unknown.** No filling a gap from model memory.
- [ ] **How she says "I don't know."** A distinct skill from grounding and most
      of what makes an assistant feel honest — she should be able to say what she
      is unsure of *without* it becoming a disclaimer footer, which A1 spent
      effort removing.

**Instrument:** an attribute-grounding matrix — for each stated attribute, is
there evidence, and does the stated value match it.

**Exit criteria**

- **Zero** attributes stated as fact without a source in the evidence
- Unknown attributes are said to be unknown, not guessed
- Conflicting sources produce a stated conflict
- No regression in conversation quality — honesty must not sound like a
  disclaimer

---

## A7 — Memory & personal context `[~]`

**Goal:** she is useful over weeks, not only within one conversation, without
becoming unpredictable.

**Depends on A3.** Memory that contaminates is worse than no memory, and A3 is
where contamination is defined and measured. Do not start this before A3 lands.

**What to build**

- [ ] Preference memory that is actually used (the profile layer exists; the
      *use* is thin)
- [ ] Task history and prior decisions — "you picked the M330 last time"
- [ ] Continuation across sessions
- [ ] **Explicit user control** over what is remembered and forgotten. A1
      already found that a bare "forget X" could silently eat a turn; forgetting
      needs to be a real, visible operation.
- [ ] A hard line between temporary task state (A3's) and long-term memory
- [ ] Local-first storage — **privacy is a design constraint, not a setting**
- [ ] **Shared memory across devices** — see Milestone D. If D is real, this is
      not optional, and it changes the storage design. Decide it here.

**Instrument:** long-session and cross-session recall scenarios, plus the A3
contamination matrix re-run *with* memory enabled — recall must not reintroduce
contamination the previous phase removed.

**Exit criteria**

- Recall accuracy ≥90% on the continuity scenarios
- **Zero** cross-task contamination with memory on
- Forgetting works and is visible to the user
- Nothing leaves the machine without the user asking

---

## Milestone A exit

- [ ] All seven phases at `[x]`
- [ ] `docs/BRAIN_ARCHITECTURE.md` exists **and describes stages that exist as
      separate modules** — not a diagram of a 537 KB file
- [ ] Latency budget met: p50 ≤ 4s, p90 ≤ 8s conversational
- [ ] Full suite green; router ≥95%; conversation quality ≥85% in **both**
      languages
- [ ] A week of real daily use without a "why did she do that" moment worth
      filing

---

# Milestone B — The Hands

**Goal:** every tool works every time, or fails in a way you can act on.

Milestone A decides *which* ability to use and *how to talk about it*. B is about
the abilities themselves being dependable when they meet the real world — a page
that loads slowly, an app that moves its buttons, a site that changes overnight.

Roughly: browser control robustness, desktop UI automation against real
applications, screen understanding, recovery from a world that changed under her,
and the honest reporting of all of it. Plus whatever new tools turn out to be
worth having once A makes selecting them reliable.

*Detail to be written when we get here, against the Elaina that exists then.*

---

# Milestone C — The Body

**Goal:** she feels like someone present, not a voice from a box.

Expression, movement, gaze, timing, reaction. The avatar work.

**Two things to carry in from A:**

- **Latency is the whole illusion.** An expression that arrives after the
  sentence is worse than none. This is why Rule 3 above exists.
- **Barge-in.** She has a deterministic "stop it" path, but not conversational
  interruption — talking over her should work the way it does with a person.
  It may belong at the end of A rather than here; decide when A6 lands.

*Detail to be written when we get here.*

---

# Milestone D — Elaina Home

**Goal:** a dedicated, always-available Elaina that lives on a wall, a desk, or
another permanent display.

**What it changes architecturally, decided early because it reaches backwards:**

- The backend becomes a **service with a real protocol**. The WebSocket channel
  is currently a test harness; D makes it a product surface.
- **Shared memory across devices** stops being optional (A7).
- **Always-on microphone in a room** is a different privacy question from a
  microphone on your own PC. This deserves its own phase inside D — consent,
  wake behaviour, what is stored, who else is in the room — not a bullet.

*Detail to be written when we get here.*

---

## Risks

| Risk | Where it bites | Mitigation |
|---|---|---|
| `chat_engine.py` keeps growing | every phase gets slower | Rule 2 — extraction is the entry price |
| Latency compounds phase by phase | Milestone C becomes impossible | Rule 3 — stated budget, enforced per phase |
| Guards silently do nothing in Korean | reliability work only covers English | Rule 4 + A2's guard audit and registry test |
| `qwen3:8b` is the ceiling | repetition, register, long-context faults | measure the gap before assuming a bigger model fixes it; the 27B experiment stays deferred until a phase's own numbers say the model is the limit |
| Phases re-build what exists | months spent on solved problems | A4 and A5 are deliberately narrowed; read the code before writing the phase |
