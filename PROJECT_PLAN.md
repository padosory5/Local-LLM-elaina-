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
| **Branch** | `main` · last commit `a4f37b5` *A1 - naturalconversation* |
| **Tests green** | **3035** / 166 modules — regression floor, must never drop |
| **Model** | `qwen3:8b` via Ollama · vision `qwen3-vl:8b` |
| **Router accuracy** | **97.8%** (131/134) · 0 dangerous false positives · target ≥95% ✅ |
| **Agency / consent** | 0 unrequested actions · consent cases green ✅ |
| **Conversation quality** | **94%** clean (30/32) EN · 75% KO · target ≥85% ✅ |
| **Latency** | median **3.8s** · p90 **13.0s** · ⚠️ see the latency budget below |
| **Phase** | A1 done · A2 `[~]` (two guards English-only) · **A3 done** — contamination 9/12 → 12/12 |

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
| A3 | Context & state ownership | `[x]` | contamination matrix ≥95% — **12/12**; conversation quality held at 94% |
| A4 | Capability contracts | `[ ]` | every capability has typed I/O and a declared failure set |
| A5 | Planning gaps | `[ ]` | retry, cancel and partial completion covered by scenario tests |
| A6 | Attribute grounding | `[ ]` | no unsourced attribute stated as fact; unknown stays unknown |
| A7 | Memory & personal context | `[ ]` | useful across sessions, zero cross-task contamination |

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

## A4 — Capability contracts `[ ]`

**Goal:** a capability declares what it takes, what it returns, and how it fails
— as types, not prose.

**Narrowed deliberately.** The "centralized capability inventory" already exists:
[brain/capabilities.py](brain/capabilities.py) has
`Capability(id, name, summary, needs, examples, offer_when)` plus
`blocked_reason()` and `fix_for()`, and A1 added `spoken_summary`. Availability
state and consent requirements are already modelled. What is missing is
narrower than "build an inventory".

**What is missing**

- [ ] **Typed inputs.** A capability's requirements are a prose `summary`. A
      caller cannot ask "what do you need from me" and get an answer it can check.
- [ ] **Typed results.** Tool results reach the reply as strings and are
      re-phrased by the model. A1 put a realization layer in front of that, but
      the underlying result is still prose, which is why a raw accessibility tree
      could ever have reached speech.
- [ ] **A declared failure set.** Each capability enumerates how it can fail, so
      "it didn't work" can become a specific, honest sentence.
- [ ] **One selection path.** Selection currently reads a registry *and*
      hand-written branches in `chat_engine.py`.

**Instrument:** extend `tests/tool_matrix.json` with input/output conformance —
every capability's declared contract is exercised, and a capability whose real
behaviour disagrees with its declaration fails the suite.

**Exit criteria**

- Every capability has typed I/O and a declared failure set
- No tool result reaches the reply as unstructured prose
- Tool selection accuracy holds at **≥95%**
- A capability cannot be added without a contract (registry test enforces it)

---

## A5 — Planning gaps `[ ]`

**Goal:** a multi-step task can be retried, cancelled, and reported on halfway.

**Narrowed deliberately.** [brain/task_planner.py](brain/task_planner.py)
already does Goal → Capability Check → Plan → Execute → Observe → Update State →
Replan, with per-step risk classification and a consent pause before anything
committing. Capability chaining and replanning-after-correction exist. Do not
rebuild them.

**What is missing**

- [ ] **Retry strategy.** A step that fails transiently is not distinguished from
      one that fails permanently.
- [ ] **Cancellation mid-plan.** "Stop" is deterministic at the input layer; a
      plan that is three steps in needs to unwind and say what it did.
- [ ] **Partial completion reporting.** "I got two of the four done, and here is
      where I stopped" is currently either a success or a failure.
- [ ] **Planning vs execution as separate, inspectable phases** — so a plan can
      be shown before it runs, which C and D will both want.

**Instrument:** extend `tests/execution_matrix.json` with interruption and
partial-failure scenarios; every scenario ends in exactly one named terminal
state.

**Exit criteria**

- Every failure ends in one of the named terminal states, **0 unbounded waits**
- Cancellation stops the plan and reports honestly, **10/10**
- Partial completion is reported as partial, never as success or failure
- Execution matrix green

---

## A6 — Attribute grounding `[ ]`

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

## A7 — Memory & personal context `[ ]`

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
