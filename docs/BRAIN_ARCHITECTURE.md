# The brain, stage by stage

**What this is.** One turn from sound to speech, named stage by stage, with
the module that decides each one. Milestone A's exit criterion asks for this
document *and* that it describe "stages that exist as separate modules — not
a diagram of a 537 KB file", so the last section says plainly which parts are
still not separate.

**How to read it.** Every stage below is a real file you can open, and most
of them decide their question **deterministically, before any model is
asked**. That ordering *is* the architecture: a model is consulted where
judgement is genuinely needed, and a rule decides everything a rule can.
Where a stage exists because something specific went wrong, its own docstring
says what.

---

## One turn, in order

### 1. Hearing it

| Stage | Module | Decides |
|---|---|---|
| Voice activity | `voice/vad.py` | when speech started and stopped |
| Transcription | `voice/stt.py` | words, per-word confidence, detected language |
| Was it heard at all | `voice/transcription_policy.py` | retry language, silence, "heard unclearly" |

The transcriber is faster-whisper `large-v3-turbo`. Its per-word confidence is
kept rather than discarded, because two later stages need it.

### 2. Is this worth answering as heard

| Stage | Module | Decides |
|---|---|---|
| Nonsense check | `brain/sense_check.py` | could a person have said this at all |
| Near miss | `brain/near_miss.py`, `brain/known_names.py` | CBT vs CPT, 빈천공항 vs 인천공항 |

Both can only ever *add a question*. Neither may lose a turn.

### 3. Which language this turn is in

`brain/turn_language.py` decides once, deterministically, and every stage
downstream reads that one answer. `brain/korean_register.py` enforces 습니다체
at the end; `brain/personality_loader.py` picks the personality file for the
language.

### 4. What they said, before a model is asked

| Stage | Module | Decides |
|---|---|---|
| Front door | `brain/deliberation/front_door.py` | the shapes that need no router at all |
| Interpreter | `brain/deliberation/interpreter.py` | utterance in, typed `Goal` out, or "unknown" |
| Goal | `brain/deliberation/goal.py`, `goal_intent.py` | what was asked for, and where each part came from |
| Which one they mean | `brain/references.py` | "the second one", "that hotel" |
| Does this replace the last thing | `brain/deliberation/supersession.py` | a new subject, or the same one |
| Corrections | `ChatEngine._with_correction_applied` | "no, I meant Portland, Maine" |

### 5. What they want

`brain/intent_router.py` — a model call, ~3.0 s, and currently the slowest
stage of a turn — produces the intent, the normalized request and a search
query. `brain/task_intent_gate.py` decides beforehand whether a goal needs the
task planner instead.

### 6. Does she know enough to act

`brain/deliberation/clarification.py` is the ask-or-assume gate;
`brain/deliberation/pending.py` holds the one unanswered question until it is
answered. `brain/deliberation/interaction.py` decides answer / offer / ask /
act **once**, instead of in thirty-four places.

### 7. Which ability

`brain/capability_selection.py` chooses after the need is known;
`brain/capabilities.py` is the declarative table of what she can do;
`brain/capability_contract.py` says what each takes, what it returns, and how
it is allowed to fail. A capability cannot be added without a contract — a
test enforces that.

### 8. Getting the facts

| Stage | Module | Decides |
|---|---|---|
| Search | `agents/research_agent.py`, `tools/web_search.py` | the queries, run concurrently |
| Encyclopedia | `brain/world_facts.py` | the standing facts a model should not be trusted on |
| Where candidates come from | `brain/acquisition.py`, `brain/entity_discovery.py` | evidence vs things |
| Is it what was asked for | `brain/candidate_fit.py` | a listings page is not a show |
| What was found | `brain/result_state.py`, `brain/task_session.py` | what the next turn can point at |
| Where the person lives | `brain/user_locale.py` | which market, currency and sites |
| Whether they moved | `brain/where_we_are.py` | the clock changed zone since she was last on — asked, never assumed |
| What time it is elsewhere | `brain/world_clock.py` | the other clock, and the arithmetic |

### 9. Memory

| Stage | Module | Decides |
|---|---|---|
| Is there something to keep | `brain/memory_gate.py` | per *sentence*, not per route |
| What to write down | `memory/extractor.py` | in the person's own language |
| What she knows about them | `memory/memory_manager.py` (`profile`, `select_profile`) | the facts in front of her every turn |
| Their own rules | `brain/standing_orders.py` | two files they own, that outlive every restart |
| Forgetting | `MemoryManager.forget` | a soft delete, named out loud |

Full record: [MEMORY_AND_RECALL.md](MEMORY_AND_RECALL.md).

### 10. Saying it

| Stage | Module | Decides |
|---|---|---|
| How long, how many offers | `brain/response_policy.py` | limits, and whether a value is missing |
| What the reply is *doing* | `brain/conversation_style.py` | the act, and how a person doing that would say it |
| Short action replies | `brain/brief_response.py`, `brain/action_status.py` | varied, outcome-locked lines |
| Purely social lines | `brain/social_lines.py` | greetings, chosen locally |
| Shortening | `brain/answer_condenser.py` | without inventing anything |
| Korean register | `brain/korean_register.py` | 해요체 → 습니다체 |
| For the voice | `brain/text_filter.py` | what speech cannot say |

### 11. The guards, which is where honesty lives

Each one *replaces or removes* text. None invents a replacement, and each says
its line through `brain/guard_lines.py`, so a guard never speaks English into
a Korean reply.

| Guard | Module | Stops |
|---|---|---|
| Unsourced values | `brain/grounded_values.py` | a price she never looked up |
| Measured attributes | `brain/attribute_values.py` | specs, checked the way prices are |
| "It does not exist" | `brain/existence_claims.py` | a denial with no evidence either |
| Promised actions | `brain/action_commitment.py` | saying she did what she did not do |
| A false premise | `brain/premise_check.py` | "한국은 엔화 쓰잖아" left standing |
| A fact told, not asked | `brain/told_not_asked.py` | echoing it back as a question |
| A stated number | `brain/world_facts.py` | 694,000 people in Portland, Maine |
| Repetition and echo | `brain/response_quality.py` | her own last answer, again |

Full record: [MISUNDERSTANDINGS.md](MISUNDERSTANDINGS.md).

### 12. Acting

`brain/task_planner.py` plans multi-step goals;
`brain/desktop_action_planner.py` and `brain/browser_action_planner.py` plan
and verify native and page actions; `tools/computer_control/`,
`tools/browser_control/` and `tools/screen_browser/` carry them out. Consent
and preconditions: `agents/consent.py`, `agents/preconditions.py`.

### 13. What the person did, and doing it again

`tools/screen_control/input_watcher.py` tells their input from hers;
`tools/screen_control/activity_recorder.py` writes down what they did;
`memory/activity_log.py` keeps it; `brain/replay_plan.py` turns it into steps
and `brain/replay_runner.py` performs them after a yes. Full record:
[ACTIVITY_MEMORY.md](ACTIVITY_MEMORY.md).

### 14. What happened in the turn, afterwards

`core/turn_trace.py` keeps one record per turn: the draft, every stage in
section 10 and 11 that ran and what it changed, every model call with its
latency and tokens per second, the evidence, and the text displayed and
spoken. `scripts/turn_trace_report.py` reads it back. Full record:
[TURN_TRACE.md](TURN_TRACE.md).

### 15. Phase 3 seams (docs/PHASE3_PLAN.md)

- **One reply, two realizations** (`brain/realize.py`). The reply every stage
  works on is display text: `realize.display` applies the HARD invariants
  (no internals, no foreign script) and damage repair only, and it is what
  the screen, the history and memory get. `realize.speech` runs at the one
  audio boundary (`AudioManager.speak`): notation said in words by a closed
  bilingual operator grammar (`brain/spoken_notation.py`), visual-only
  formatting dropped, substance kept -- measured as content coverage.
- **Computed answers before the model router** (`brain/domain_resolver.py`).
  A whole-utterance clock, arithmetic or unit-conversion request is claimed
  at `tier0` by its own grammar and routed without the model; the clock is
  never sent to a web search whatever a router says about freshness. The
  clock layer (`brain/world_clock.py`) reads every place named, compares two,
  labels the zone from the tz database and says the time in Korean with
  오전/오후 computed.
- **`clarification` is the person asking** about her last answer (R9): it
  keeps the conversation and is answered. Elaina needing to ask is
  `needs_clarification`.
- **One evidence ledger per turn** (`brain/evidence.py`). The grounded-value
  guard reads it -- the clock it read, this turn's search, a previous search
  only when a follow-up carries it -- and records a finding for every value.
- **A budget per kind of reply** (`brain/response_budget.py`): a value keeps
  the short ceiling, an explanation gets room for what it is for and one
  concrete case, a re-explanation or a request for depth gets more.

---

## The rules this shape encodes

1. **Deterministic before model.** A rule decides what a rule can. The router
   is a model call; the gate in front of it is not.
2. **A guard may remove, never invent.** Fewer honest words beat a confident
   wrong name.
3. **A guard's own sentence is fixed text.** `guard_lines.is_fixed_line` keeps
   the style layer from re-saying it — it once turned "I didn't catch that"
   into "Sure. Stop recording."
4. **Bilingual, or declared.** Every text guard either works in both languages
   or says in its own docstring that it does not, and
   `tests/test_guard_languages.py` makes that unavoidable.
5. **The instrument ships with the phase.** Every number in these docs has a
   script in `scripts/` that reproduces it, registered in `LIVE_CHECKS`.

---

## What is still not a separate module

**`brain/chat_engine.py` is 696 KB.** It is the orchestrator: it calls every
stage above in order, and it still holds logic that belongs in stages of its
own. `_answer_turn` remains the largest single thing in the project.

Already extracted, and no longer in it: turn language, the deliberation
stages, capability selection and contracts, conversation style, every guard
above, the memory gate, activity and replay, world facts. What is still
inside, and reads as the next extraction in order:

- **the answer pipeline itself** — draft, rewrite, condense, filter, guard,
  surface: one ordered list of transformations, expressed as roughly 1,500
  lines of inline branches;
- **the post-answer checks** (`_acknowledgement_if_missed`,
  `_premise_corrected`, `_not_her_last_answer`,
  `_checked_against_the_encyclopedia`) — they now form a recognisable family
  and want one registry with one stated ordering;
- **memory turn-handling** (`_store_memory_candidate`, `_what_they_told_her`,
  `_not_told_yet`, `_without_what_they_took_back`) — a memory policy layer
  living in the orchestrator;
- **the search and evidence path**, which currently knows too much about both
  the research agent and the prompt.

Milestone A's criterion is met in the sense that matters — the stages are
real, named, separately tested files — and unmet in the sense that the file
calling them is still 696 KB. Both are true, and the second is Milestone B's
entry price under Rule 2.
