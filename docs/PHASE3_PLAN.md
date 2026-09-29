# Phase 3 plan: pipeline correctness and communication architecture

*Proposed 2026-09-25, before any Phase 3 code. The evidence is Phase 2
(`docs/COMMUNICATION_FINDINGS.md` §Phase 2, `docs/PHASE2_RESULTS.md`) and
the turn records of its 22 runs in `runtime/evals/p2/*/traces/`. Line
numbers refer to the tree at commit `3e8b53f` with Phase 0–2 uncommitted.*

> **Status (2026-09-27): implemented and rerun.**
> - Approved with R9 (fix `clarification` before the rerun) and R8 (speech
>   matches the display by content coverage, not string equality).
> - 3.0–3E are in; the full suite is green (3,878 tests).
> - Results: `docs/PHASE3_RESULTS.md`, read in
>   `docs/COMMUNICATION_FINDINGS.md` §Phase 3, which also lists the
>   regressions the rerun exposed.
> - The model decision is the user's.

The working conclusion carried from Phase 2: the 27B has a real advantage in
raw capability, but the current pipeline introduces correctness failures and
most of the latency. The pipeline is cleaned first. The 8B stays the
production and test model throughout, and the final 8B-vs-27B decision is
made only on the rerun in §6.

---

## 1. Root-cause map

### 1.1 The path a reply takes today

```
router (LLM, 2.6 s on the 8B / 7.6 s on the 27B)
  -> capability selection (direct / web search / browser)
  -> prompt: persona + context sections + "VOICE RESPONSE REQUIREMENTS"
       (45 words / 2 sentences unless router says detailed_response)
  -> collect_answer: the draft                       turn_trace.draft
  -> ONE string, `reply`, through 52 recorded stages  turn_trace.step
       #3  speech_filter        TextFilter.for_voice_response
       ... 40 guards and rewrites, each seeing speech-shaped text ...
       #43 natural_dashes       TextFilter.natural_dashes
       #44 final_speech_filter  repair_structure(for_voice_response(...))
       ... 10 more stages, some adding model text ...
  -> the same `reply` goes to:
       assistant_finished (the screen, textContent, pre-wrap)
       conversation history and grounded memory
       AudioManager.speak -> TextFilter.for_configured_speech -> TTS
```

There is one string. The screen, the history, memory and the voice all get
the same text, after speech shaping has been applied to it three times.

### 1.2 Display is mutated by speech normalization

- **Where.** `TextFilter.for_voice_response` (`brain/text_filter.py:349`) is
  called at 12 sites in `_answer_turn` and its helpers
  (`chat_engine.py:2738, 2863, 2981, 10866, 10919, 11016, 11116, 11185,
  11616, 11629, 11639, 11754`) and once in `brief_response.py:251`. It runs
  `for_speech`, which calls `natural_dashes`: ` - ` becomes `, `, and a dash
  between numbers becomes ` to `. Underscores become spaces, `&` becomes
  "and", and newlines are collapsed.
- **Measured.** Over the control's 172 turns, the speech filter changed
  the displayed reply 21 times and `natural_dashes` 3 times. All five control
  turns the judge marked inaccurate were right in the draft:
  `(x - a)` became `(x, a)`, `b² - 4ac` became `b² to 4ac`, and cos x lost
  its minus signs.
- **What the function actually mixes.** It is four things in one:
  - HARD invariants: `capability_contract.redact_internals`, and the
    foreign-script sentence drop;
  - modality shaping: markdown, links, URLs, dashes, underscores,
    whitespace;
  - a SOFT deletion: a trailing "Anything else?";
  - `conversation_style.repair_structure` on top (called only at #44). It
    strips markdown, turns semicolon lists into sentences, and fixes
    unpaired quotes.
- **The speech side is already a single boundary.** `AudioManager.speak`
  (`voice/audio_manager.py:188`) is the only way anything reaches a voice,
  including status and guard lines. It applies `for_configured_speech`
  again. `turn_trace.note_speech` already records the displayed text next to
  what the voice was given; today they differ only by that second pass.

### 1.3 Deterministic domains are routed by a fuzzy model field

- **The clock is not a capability.** `capability_selection.select` considers
  `DIRECT_ANSWER`, `WEB_SEARCH` and `BROWSER_CONTROL` only
  (`capability_selection.py:581`). A `time_question` whose router output
  says `information_freshness: live` scores web search as the best fit
  (`_RELIABILITY[(WEB_SEARCH, "live")]`).
- **What the search does to the prompt.** It overwrites the messages that
  held the clock (`chat_engine.py:9908` builds the clock prompt; the search
  block at `9946` replaces it).
- **Measured.**
  - Time turns sent to web search: 14–15 of 26 in each 27B pipeline arm,
    0 of 26 in every 8B arm.
  - The 27B read `Sun: ↑ 06:41` (New York's sunrise, from a time.is
    snippet) as the time.
  - The 8B escapes only because it says `unknown`, which is less accurate.
- **A second conversion.** `intent_router.py:1537` turns a `time_question`
  with `requires_external_evidence` into `web_search` unless a place is
  named.
- **World clock reads only the first place.** `world_clock.read_place`
  returns the first match, so "Seattle compared with Seoul" computes
  Seattle (against Seattle), and Seoul's time is left to the model:
  "13 hours ahead", or the right gap with a wrong Seoul clock. It is called
  on `route.normalized_request`, the router's paraphrase, not on what the
  person said.
- **Korean AM/PM is left to the model.** `build_time_context`
  (`chat_engine.py:15690`) gives an English `%I:%M %p` line with
  `now.strftime('%Z')`. On this machine `%Z` is the Korean Windows zone
  name, `태평양 일광 절약 시간`. The model translates `12:11 AM` into
  Korean itself and writes 오후 for 00:xx, in 3 of 3 runs. The UTC offset
  and zone name are in the answer-facing line, so they get repeated back
  (`only_what_helps`).
- **An existing seam.** `_route_turn` already has `tier0`
  (`chat_engine.py:13098`), which returns the `IntentDecision` the model
  would have produced, for sentences that need no classification. After it,
  recall, capability selection and consent run as on a routed turn.

### 1.4 `grounded_values` deletes values that were available

- **What the guard reads.** `_enforce_grounded_values`
  (`chat_engine.py:3057`) checks against `_grounded_context["statement"]`
  plus `_last_research_evidence` plus the user's words.
  - It never sees the clock.
  - The clock is recorded in the turn record (`turn_trace.note_evidence
    (clock=...)`) but is not passed to the guard.
- **Stale evidence.** `_last_research_evidence` is set by any search
  (`chat_engine.py:9987`) and cleared only at construction (`:1248`), so it
  carries into later turns. After any earlier search, a time turn has
  `grounded_subject=True`. The clock value is then "unsupported", and the
  sentence is deleted.
- **Proven from the trace**, `live_session_2026_09_23` turn 4, both models:
  - draft: "It is 3:07 AM on Friday, September 25, 2026."
  - `grounded_values`: "I haven't actually checked that, so I'd rather not
    guess."
  - log: "Removed a value nothing had verified: 07."
- **Frequency.** 0–2 correct time answers deleted per arm across 26 time
  turns.
- **The value parser does not read clock times as values.** It saw `07`,
  and `12, 25`.
- **Semantics.** The guard is a mutator with no record of findings. It
  decides and deletes in one step, and the only trace left is a log line.

### 1.5 The universal 45-word / 2-sentence ceiling

- **Where it comes from.** `config.yaml responses.max_words: 45,
  max_sentences: 2` apply to every non-`detailed_response` turn
  (`chat_engine.py:10599`).
- **The act contract cannot loosen it.** `conversation_style` contracts can
  only tighten it: ANSWER's own contract allows 4 sentences, and the `min()`
  keeps 2.
- **The router rarely lifts it.** It flagged 1 of 86 control turns as
  `detailed_response`.
- **Measured.**
  - Explanation replies had a median of 32 words (8B) and 29 (27B) in the
    pipeline, against 64 and 81 with no pipeline.
  - Nothing rewrote them; the drafts were written to the limit.
  - `says_what_it_solves` was 0% for the 8B. The 27B reached 36% under the
    same cap, so the cap is a ceiling and the model matters within it.
- **The generation budget follows the same pair.**
  `ResponseLimits.generation_budget` gives `max_words * 3 + 64` tokens.

### 1.6 Trajectory failure categories (documented, not fixed here)

Trajectories pass 0–12% in every arm. The failures fall into six
categories:

| | category | evidence | model-dependent? | Phase 3? |
|---|---|---|---|---|
| T1 | **Router label semantics.** The pipeline treats `clarification` as "Elaina must ask", so the prompt carries no history and says "ask one short clarifying question". The 27B uses the label for "the person is asking for clarification". | 27B: 10–11 follow-ups per arm labelled `clarification`; about half of all follow-ups (19–20 of about 40) answered with no history, against 7–11 for the 8B. "I still don't get it" → "What part is unclear?"; "Why would I ever need that?" → "I don't have context for what 'that' refers to". The 27B with no pipeline answered both. | exposed by the 27B | **Decision needed** (§4, R9) |
| T2 | **Paraphrase substitution.** The answer is built from `route.normalized_request`, not the person's words. | 8B: "테일러 급수가 뭐야?" → "What is Taylor's scale?" / "What is Taylor Swift's genre?". The whole Korean trajectory answered about music tempo and a life-satisfaction scale. | 8B | no |
| T3 | **Stale evidence deletes a correct value.** | §1.4 | all | yes (3C) |
| T4 | **A follow-up searches on the wrong entity**, and the answer grounds on it. | "Where is Taylor's scale actually used?" searched; `not_searched` failed. | 8B | no |
| T5 | **A confusion turn gets a restatement**, not a new approach. | `adapts_to_confusion` 0–67%; replies squeezed to 2 sentences. | partly | partly (3D) |
| T6 | **Receipts get service phrasing.** | "Thanks" → "just let me know" | 8B mostly | no (soft stages measured) |
| T7 | **Clock realization**: wrong 오전/오후; zone metadata stated. | §1.3 | all | yes (3B) |

Of these, T1 matters most for the final model decision, because it lands
only on the 27B.

---

## 2. Proposed Phase 3 subphases

Each subphase is behavior-changing, so each one:
- lands with regression tests that encode the *property* (generated or
  parameterised cases), not the eval sentences;
- keeps `_answer_turn`'s trace chain intact;
- ends with the full suite green;
- gets a short 8B check on the suites it touches (about 20 minutes, TTS off)
  before the next subphase starts.

### 3.0 Instrumentation first (no behavior change)

- **Evidence ledger.** A turn-scoped `EvidenceLedger`, created at turn
  start. Every capability writes to it with provenance: clock readings,
  world-clock lines, calculator and unit results, this turn's search, and
  recall reused on purpose.
  - `turn_trace.note_evidence` becomes a view of it, so what a guard checks
    and what the trace shows are the same object.
  - Nothing reads the ledger yet.
- **Turn record fields.** `display` (what the screen got) and
  `speech_realized` (what the voice got) as separate fields, plus
  `findings` (see 3C).
- **New deterministic checks,** over existing and future runs:
  - `display_is_final`: the displayed text equals the final response, with
    no modality transform in between;
  - `clock_turn_searched`: a time turn went to web search;
  - `value_deleted_with_evidence`: a guard removed a value the ledger held.
  - Scored retroactively on the Phase 2 traces to get the "before" numbers.
- **Pin Ollama at 0.34.4** for Phase 3 and the rerun. It updated itself in
  Phase 1; the user holds or disables the auto-update.

### 3A. Display / speech separation (priority 1)

- **Invariants first.** `reply` in `_answer_turn` becomes the final
  response, and HARD invariants are applied to it once, as an explicit
  `invariants` stage:
  - `redact_internals`;
  - the foreign-script sentence drop.

  Nothing else of the speech filter runs on it.
- **Two independent realizations,** as pure functions in a new
  `brain/realize.py`:
  - `display(final, language)` makes the text safe for the screen and
    nothing more:
    - removes emphasis markers and leaked log/role labels (the damage part
      of `repair_structure`);
    - keeps operators, parentheses, dashes between operands, identifiers
      and line breaks. The chat window is `pre-wrap`.
  - `speech(final, language)` lives at the audio boundary
    (`AudioManager.speak`, which every spoken line already passes through).
    It takes over `for_speech`, `natural_dashes`, the markdown-to-prose part
    of `repair_structure`, `for_configured_speech`, and a verbalizer for
    notation spans.
- **Verbalizer.** A closed operator grammar of about 20 operators,
  bilingual: `+ − × ÷ = ≈ ≠ ≤ ≥ ^ ² ³ / ! √`, function application and
  ranges.
  - Out-of-grammar notation is spoken as a reference to the screen ("the
    formula on screen"), never as garbled characters.
  - It is not a symbol table. Spans come from a tokenizer that finds
    operand–operator runs.
- **What happens to the three modality stages** (`speech_filter`'s modality
  part, `natural_dashes`, `final_speech_filter`): they leave the display
  chain.
  - The trailing "Anything else?" deletion moves into the existing,
    switch-gated `closing_offer` stage, so it stays measurable as SOFT.
- **Guards read what is shown.** Guards and completeness checks
  (`grounded_values`, `result_restored`, `AnswerCompletionGuard`) now see
  display-shaped text, which is what is actually displayed and remembered.
- **Where the display goes.** It is what reaches `assistant_finished`,
  conversation history and grounded memory. The speech realization reaches
  only TTS and the turn record.
- **Not done:** no new regex chain on the display text. The display
  function removes nothing that carries meaning.

### 3B. Deterministic domain routing and clock realization (priority 2)

- **A domain resolver at `tier0`** (the existing seam). It claims
  whole-utterance requests in closed domains and returns the
  `IntentDecision` the router would have produced, so topic, task, consent
  and capability logic still run. The domains:
  - local time, date and year;
  - world clock (one or more named places);
  - an arithmetic expression;
  - a unit conversion between known units.

  It parses with the domains' own grammars: `world_clock`'s place grammar,
  `tools/calculator`'s AST evaluator, and the `units` table. No
  reply-shaped regex.
  - **Shadow mode first.** The resolver logs its claim beside the router's
    decision on the corpus and on live turns.
  - **It switches to acting** only after the disagreement log shows no
    misclaims. Its first acting run is measured.
  - A claimed turn skips the router call, saving 2.6 s on the 8B.
- **Capability precedence.** An intent owned by a deterministic capability
  never selects web search from a freshness field:
  - `time_question` goes to the clock;
  - `calculation` goes to the calculator.

  The `intent_router.py:1537` conversion is narrowed to requests that are
  not about a clock at all ("when does the store close").
- **Clock realization,** fixed in the clock layer, not in replies:
  - `world_clock.read_places` returns every named place in order, and is
    read from what the person said as well as the router's paraphrase.
  - `describe` states each place's clock and the gap between the places
    that were compared, not only against local time.
  - A `ClockReading` carries zone, moment, an IANA-derived label (never
    Windows `%Z`), and a spoken-form phrase per language, including the
    Korean 오전/오후 and 12-hour conversion.
  - The prompt puts the answer-ready phrase first, in the reply's language,
    and zone/offset metadata after it as "only if asked".

### 3C. Grounded value preservation (priority 3)

- **The guard reads this turn's ledger** (3.0): the clock, calculator and
  conversion readings, this turn's search, and recall reused on purpose.
  - Previous-turn search evidence counts only when this turn's follow-up
    decision carries it into the ledger.
  - `_last_research_evidence` stays for the cross-turn features that read
    it; the value guard stops reading it.
- **Validator semantics.** `GroundedValueGuard.findings(reply, ledger)`
  returns one finding per value: its span, kind, status (supported,
  conflicting or unsupported) and source.
  - The value parser learns clock times and dates as values:
    `12:07 AM` = `00:07`.
- **Stage policy** (the stage stays HARD):
  - *supported*: keep;
  - *conflicting* with a ledger value: repair to the ledger value when it
    is unambiguous, otherwise drop the sentence;
  - *unsupported, with a grounded subject, and the ledger has nothing of
    that kind*: today's behavior (drop, and say so once).
  - Every finding goes into the turn record, whether or not it changed
    anything.
- **Proof.**
  - For each deletion in the Phase 2 traces, the trace names the value, the
    ledger entry that held it, and the finding that removed it.
  - After 3C, `value_deleted_with_evidence` is 0 over the corpus.
  - The existing invented-value tests still pass: prices, phone numbers,
    and a budget repeated back wrong.

### 3D. Goal-aware response budget (priority 4)

- **Replace the pair of global caps** with a `ResponseBudget` chosen from
  signals the turn already has. No new intent vocabulary:

  | budget | chosen when | prompt says |
  |---|---|---|
  | `value` | calculation, time/date, a claimed conversion, or `asks_for_a_value` | the value first; one sentence of context at most |
  | `explain` | an information request that is not a value | room to say what it is for and show one concrete case; a sentence target, not a word count |
  | `elaborate` | router `detailed_response`, or a follow-up on the same subject after an explanation that was not understood | room to try a different approach |
  | act contracts | greet / receipt / react / close / ask / confirm … | unchanged `conversation_style` contracts |

- **Rewrites and generation follow the budget.** `length_rewrite` fires
  only on a reply far over its budget. `generation_budget` is set from the
  budget, not from `max_words`.
- **Config.** `config.yaml responses` keeps a per-budget ceiling, so the
  person can still say "shorter"; the universal 45/2 goes.
- **Watched in the numbers:**
  - words per explanation turn;
  - characters of speech per turn (TTS cost and time);
  - `only_what_helps`, which must not fall.

### 3E. Verification and the rerun (§6), then the decision

1. Full suite green.
2. Modality pairs scored with the new realization functions.
3. The rerun matrix on the unchanged Phase 2 corpus.
4. A blind human confirmation on any rare-yes property that moved,
   including the pending `runtime/evals/calibration-p2.json`.
5. A comparison against both the Phase 2 control and the Phase 2 27B arms.
   The model decision is written up from that.

### Order

3.0 → 3A → 3B → 3C → 3D → 3E.

- **3A before 3C**: the validator should see display text, not
  speech-shaped text.
- **3B before 3C**: the clock readings are the ledger entries the validator
  needs.
- **3D last**: it changes reply length, which moves every judged number.
  The earlier subphases should be measured without that confound.

---

## 3. Files and components affected

| component | 3.0 | 3A | 3B | 3C | 3D |
|---|---|---|---|---|---|
| `brain/chat_engine.py` `_answer_turn`: stages #3 / #43 / #44, the 12 filter sites, emit / history / speak | trace fields | ● | | | |
| `brain/chat_engine.py` `_route_turn` / `tier0`, `build_time_context`, research block | ledger | | ● | | |
| `brain/chat_engine.py` `_enforce_grounded_values`, `_last_research_evidence` use | ledger | | | ● | |
| `brain/chat_engine.py` limits at `10599–10660`, `generation_budget` use | | | | | ● |
| `brain/text_filter.py` (split; back-compat wrappers kept for scripts) | | ● | | | |
| `brain/realize.py` (new: display, speech, verbalizer) | | ● | | | |
| `brain/conversation_style.py` `repair_structure` (split damage vs prose) | | ● | | | |
| `voice/audio_manager.py` `speak` (speech realization) | | ● | | | |
| `brain/world_clock.py` (places, pairwise, `ClockReading`, phrases) | | | ● | | |
| `brain/capability_selection.py` (deterministic precedence) | | | ● | | |
| `brain/intent_router.py:1537` (narrow the conversion) | | | ● | | |
| `brain/domain_resolver.py` (new: tier0 claims, shadow log) | | | ● | | |
| `brain/grounded_values.py` (findings, clock/date values) | | | | ● | |
| `brain/evidence.py` (new: `EvidenceLedger`) | ● | | ● | ● | |
| `brain/response_policy.py` `ResponseLimits` → `ResponseBudget` | | | | | ● |
| `config/config.yaml` `responses` | | | | | ● |
| `brain/response_stages.py` registry (stages moved, split, retired) | | ● | ● | ● | ● |
| `core/turn_trace.py` (display, speech, ledger, findings) | ● | ● | | ● | |
| `evals/checks.py`, `evals/modality_pairs.py`, `evals/phase2.py` (p3 arms), `evals/compare.py` (previous-phase column) | ● | ● | | | ● |
| tests: `test_text_filter`, `test_response_cases`, `test_turn_behaviour`, world clock, grounded values, response policy, turn trace, stage registry, plus new property tests | ● | ● | ● | ● | ● |
| `docs/RESPONSE_PIPELINE.md` (regenerated), `docs/TURN_TRACE.md`, `docs/BRAIN_ARCHITECTURE.md` | ● | ● | ● | ● | ● |

The renderer needs no change: `.message` is already `white-space: pre-wrap`
and uses `textContent`.

---

## 4. Migration risks

| # | risk | why it is real | mitigation |
|---|---|---|---|
| R1 | Tests assert speech-shaped display text | The filter has shaped every asserted reply since the suite was written. | Run the suite after 3A and classify each failure: *the expectation was speech* (update, with the reason in the commit) or *a regression* (fix). Never bulk-update. |
| R2 | A HARD invariant is lost in the split | `redact_internals` and the foreign-script drop live inside the speech filter today. | An explicit `invariants` stage before display, run again at the speech boundary; the existing capability-contract and foreign-script tests must pass unchanged. |
| R3 | Markdown shows up raw on screen | The filter stripped it for everyone. | `display()` removes emphasis markers and leaked labels. The line-break and list rendering stays pre-wrap. Checked on the modality pairs and on the corpus replies. |
| R4 | Guards behave differently on display text | They were written and tested against filtered text. | They now see what is actually shown and stored, which is the correct input. Differences are listed per guard from the whole-turn tests, not assumed. |
| R5 | The domain resolver misclaims a turn | "What time does the museum open", "time to leave", a multi-part request, or a follow-up like "and in Tokyo?". | Whole-utterance claims only. Shadow mode with a logged disagreement count before it acts. It returns an `IntentDecision`, so state logic still runs. The contamination suite must not regress. |
| R6 | Skipping the router skips state updates | Topic shift, task supersession and consent are updated from the route. | `tier0`'s contract already covers this: downstream runs as on a routed turn. Whole-turn tests cover time after a task, time inside a pending offer, and time after a recommendation. |
| R7 | The ledger lets an invented value through | A value that happens to appear in the ledger passes. | The ledger holds this turn's evidence only; carried-over evidence is explicit. The invented-value regression tests stay. `findings` makes every pass and fail inspectable. |
| R8 | Longer replies cost speech time and ElevenLabs credits | Removing the 45/2 cap lengthens explanations by about 2×. | Measure speech characters per turn in the rerun (TTS stays off in all tests). Budgets have ceilings in config. Whether speech should ever say less than the screen is a product question for you, not assumed here. |
| R9 | **The rerun is unfair to the 27B unless T1 is addressed** | Half of the 27B's follow-ups lose their history because of what one label means in the pipeline. The cleaned-pipeline 27B would still carry it. | Decision needed: (a) keep T1 out of Phase 3 and read the 27B's trajectory numbers with that caveat; or (b) add a small subphase that defines the `clarification` contract (Elaina asks, versus the person asks for clarification) and keeps history for the latter. I recommend (b), because the final decision depends on it. It is a routing-contract fix, not a trajectory redesign. |
| R10 | Environment drift between Phase 2 and the rerun | Ollama auto-updates, and model files can change. | Pin 0.34.4. Record `ollama` and the model digests in each run's meta. Rerun one direct arm per model as a drift check: it must reproduce Phase 2 within the noise. |
| R11 | 3D moves every judged number | Length changes what the judge sees. | 3D is last. 3A–3C are measured before it, so their effect is not confounded with length. |
| R12 | The judge rewards length | It may prefer longer replies. | The Phase 2 blind set, plus a new one on the properties 3D is meant to move, labelled by you before those rises are believed. |

---

## 5. Acceptance criteria

**Every subphase:**
- the full suite is green;
- the turn-trace chain has 0 breaks;
- no new phrase-specific guard;
- no Taylor- or time-response-specific code;
- HARD-invariant tests pass unchanged;
- the stage registry is regenerated and matches the code.

**3.0**
- The ledger and new fields are recorded on every whole-turn test and eval
  turn, with overhead under 1 ms at p95.
- The new checks, scored on the Phase 2 traces, reproduce this document's
  numbers: time turns searched 14–15/26 for the 27B and 0 for the 8B; value
  deletions 0–2 per arm; 24 display changes by modality stages over the
  control's 172 turns.

**3A**
- On `evals/modality_pairs.json`:
  - display preserved 24/24 inside a sentence and 24/24 at the start
    (today 22 and 10);
  - speech speakable 24/24 (today 6);
  - speech equivalent to the display ≥ 20/24 (today 0).
- `display_is_final` holds on every corpus turn.
- 8B quick check: no display mutation by a modality transform in any turn
  record, and `accurate` / `notation_earned` not lower than the Phase 2
  control.

**3B**
- On both models (8B in the quick check; 27B in the rerun):
  - `clock_turn_searched` = 0;
  - `clock_times`, `hour_difference`, `utc_offset` and `calendar` pass in
    every run of the relevant scenarios, including `time_seattle_vs_seoul`
    and `ko_time_now`.
- The resolver's shadow log shows 0 misclaims on the corpus, and on the
  whole-turn tests, before it acts.
- Latency: time p50 for claimed turns is reported (expected under 1 s on
  the 8B).

**3C**
- `value_deleted_with_evidence` = 0 over the corpus and the failure bank.
- Every grounded-value change has a finding in the trace.
- The invented-value regression tests still pass.

**3D**
- Explanation replies are no longer cut to 2 sentences.
- Value turns stay short: median words within +10% of Phase 2.
- On the 8B, `says_what_it_solves` and `concrete_first` rise, confirmed by a
  blind human set; `only_what_helps` does not fall.
- Speech characters per turn are reported.

**Phase 3 overall**
- The rerun (§6) completes on the Phase 2 corpus and rubric v3
  (`c4fac3a80008`), with the same judge and Ollama version.
- The report compares every arm with its own Phase 2 counterpart.
- The model decision is written from it, not before.

---

## 6. Expected rerun matrix

The Phase 2 corpus is unchanged. Any new regression scenarios live in a
separate suite, reported apart, so the comparison stays like for like.
Judge: `qwen3.6:35b-a3b`. Ollama 0.34.4. Speech recognition off for the
quality arms; TTS off throughout.

| arm | pipeline | words + decisions | soft stages | runs | compared with | purpose |
|---|---|---|---|---|---|---|
| `p3-8b-soft` | cleaned | 8B | on | 2 | `p-8b-soft` | the production candidate |
| `p3-8b-nosoft` | cleaned | 8B | off | 2 | `p-8b-nosoft` | do the soft stages still earn their place |
| `p3-27b-soft` | cleaned | 27B | on | 2 | `p-27b-soft` | the model decision |
| `p3-27b-nosoft` | cleaned | 27B | off | 2 | `p-27b-nosoft` | soft-stage retirement evidence |
| `d-8b` / `d-27b` | none | — | — | 1 each | Phase 2 `d-*` | drift check: must reproduce Phase 2 |
| `v3-8b` / `v3-27b` | cleaned, recogniser resident | — | on | 1 each | `v-*` | latency as the app runs |
| *(optional)* `p3-8bq8-soft` | cleaned | 8B-q8 | on | 1 | `p-8bq8-soft` | whether q8's correctness edge survives deterministic routing |

- **Estimate.** About 4 h of generation and about 45 min of judging. The
  verdict cache reuses identical replies.
- **Unchanged from Phase 2.** The Phase 2 `p-*` runs are the "before",
  reused as they are. They were produced on the same Ollama version, and
  the drift check confirms that is still the environment.
- **What the rerun is expected to show:**
  - how much of the 27B's 44% vs 23% lead survives a pipeline that no
    longer corrupts the 8B's answers;
  - what each model costs per turn once clock, date and arithmetic turns
    skip the router;
  - whether the 8B still needs its soft stages.
