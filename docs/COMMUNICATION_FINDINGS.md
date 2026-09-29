# Communication: what the first measurements found

Phase 0 (the turn record) and Phase 1 (the stage inventory, the eval corpus,
the baseline), 2026-09-23 and 24. The generated documents hold the numbers:

- [RESPONSE_PIPELINE.md](RESPONSE_PIPELINE.md): every stage of the reply;
- [EVAL_BASELINE.md](EVAL_BASELINE.md): the baseline itself;
- [EVALS.md](EVALS.md): how the evaluation works.

This page is what they mean.

## The baseline, in one table

Three runs of 54 scenarios (258 turns). Each scenario ran on a fresh backend
with speech silenced, and the local 27B judged the replies. Figures are the
mean, with the range across runs in brackets.

| Suite | Scenarios passing | Passing the deterministic checks alone |
|---|---|---|
| explanation (20) | **5%** (5–5) | 100% |
| relevance (12) | 19% (17–25) | 75% |
| modality (6) | 0% | 0% |
| trajectories (4) | 0% | 25% |
| contamination (12, earlier failures) | 47% (42–58) | 100% |
| **all 54** | **17%** (15–20) | 78% |

**What goes wrong in explanations.** Across 20 scenarios, the reply answers
the question it was asked (*answers the ask* 76%). It does not start
anywhere concrete (18%), say why the idea helps (15%), open with anything but
a definition (24%), or sound like a person (22%). The failures repeat run for
run. That is a systematic behaviour, not noise: knowledge answers are
generated at temperature 0.1.

**Relevance.** Only 16% of replies add nothing unasked. "What year is it?"
also gets the date. "15% of 80" gets "That's the exact result from the
calculation." Every clock answer carries the zone name and the UTC offset,
and the Korean one carries the Korean zone name the machine reports.

## What no stage caught

Every one of these reached the person unchanged. Twenty hard guards ran on
each turn.

- **Seattle against Seoul, wrong in all three runs.** "12:14 AM in Seattle
  … 1:14 PM in Seoul … 13 hours". Seoul was sixteen hours ahead.
  `world_clock.read_place` returns only the first place a question names, so
  Seoul was never looked up and the model supplied its time. The clock is
  arithmetic the code can do; this is a missing value, not a model failing
  to follow a rule.
- **"What time zone am I in?"**: "one hour behind Coordinated Universal
  Time", with `UTC-0700` in the same prompt.
- **"How far away is the moon?"**: "384,400 kilometers … roughly 2.5 million
  miles". It is about 239,000. The scenario passed, because the corpus does
  not check the correctness of extra detail. That is a gap in the corpus;
  it was not fixed after the fact.
- **"That will make for a long day."**: the persona file's own example reply
  to "I didn't sleep at all last night", added to a time answer at 11:29 PM
  in the replay.

Rubric version 2 checks stated values against the truth at each turn's
moment, and that found two more:

- **Every Korean clock answer names the wrong half of the day.** At 00:16
  the reply says "오후 12시 16분", which is 12:16 *PM*. This happened in both
  Korean time scenarios, in all three runs.
- **A correct time answer was deleted by the pipeline** (run 3 of the
  session trajectory). The turn record shows the sequence:
  1. The draft ended "…12:58 AM … Would you like me to help with anything
     else?"
  2. The speech filter's closer rule cut "anything else?" out of the middle
     of that question, leaving "Would you like me to help with".
  3. The style layer's structural repair added a full stop to the fragment.
  4. `grounded_values` then read "12" as a value nothing had verified. The
     clock line in the prompt is not registered as grounding evidence. The
     guard replaced the whole reply with "I haven't checked the specific
     number, so I've left it out."

  Correctness here depended on the order of a soft stage and a hard one, and
  on a hard guard that could not see the clock as a value.

The first three are hard invariants: time arithmetic and grounded values.
They argue for giving the model values it can state rather than sentences it
must copy (Phase 4, facts as values). They do not argue for a guard that
reads the reply for them.

## Where the reply is rewritten

From 258 live turns:

- **The style layer** (`her_voice`) changed 18% of replies, with 36 re-say
  calls.
- **The length rewrite** changed 17%, with 43 accepted rewrites and more
  rejected ones.
- **The speech filter** changed 10%.
- **The closing-offer strip** changed 8%.
- **The twenty hard guards** changed 0–2% each.
- **Ten stages never fired.** Twelve more ran on every turn and never
  changed a word.

In the whole-turn tests of the deterministic suite, most stages never change
a reply. The guards are tested by calling their own code, not through a turn.

On the turns that failed worst, the replay showed **no stage changed a word**
("Taylor series … example", "what time is it"). The failures are the
model's drafts under the current prompt. Guards neither caused them nor
caught them.

## Time

- A turn takes **3.8 s** at p50 and 10.8 s at p95.
- Routing takes 2.3 s of it: 234 of 708 model calls, and the single largest
  cost.
- The answer's first token arrives at 0.29 s.
- A turn makes 2.74 model calls on average.
- Nothing is streamed, so every model call after the draft adds directly to
  the wait before the first word.

## Display and speech, without a model

Over 24 display/speech pairs:

- **On screen**: notation survives 22/24 inside a sentence. The speech filter
  turns an ASCII minus into a comma (`1 - x²/2` becomes `1, x²/2`) and an
  en-dash range into "to".
- **At the start of a reply** it survives only 10/24. The final repair
  capitalises the first letter: `f(x)` becomes `F(x)`, `n!` becomes `N!`.
- **Speakable**: 6/24.
- **Spoken with the same meaning**: 0/24. The voice is handed the notation
  as it is.

## What this changes in the plan

- **Phase 2 (the model experiment)** needs the no-pipeline arm most. The
  worst explanation failures pass through the pipeline untouched, so the
  question is what the model writes when it is asked differently. The
  question is not what the guards do to it.
- **The recommendation path that produced the original Taylor answer did not
  recur.** It showed up in 0 of 3 trajectory runs and in the replay. It
  depended on state (an open "math" recommendation, "really" read as a
  constraint), and it is intermittent. It remains a state-integrity fix for
  Phase 4.
- **Ollama updated itself** (to 0.34.4) between generating this baseline and
  judging it. Every run now records the Ollama version. The model
  experiment must be run on one version.

## Phase 2: how much of the problem is the model

Measured on 2026-09-25 over 22 runs: nine judged arms at two runs each, plus
four arms that were only timed. Every run used one Ollama (0.34.4) and one
commit. The judge was qwen3.6:35b-a3b under rubric v3. The tables are in
`docs/PHASE2_RESULTS.md` (`evals/compare.py`); this section is what they
mean. The control is today's Elaina (`p-8b-soft`). Its run-to-run spread is
1–2 points, so gaps of 5 points or more are real.

| | scenarios passing | correct values | p50 / p95 per turn | VRAM |
|---|---|---|---|---|
| 8B, pipeline (today) | 23% | 75% | 4.2 / 12.7 s | 7.8 GB |
| 8B-q8, pipeline | 27% | **89%** | 5.3 / 14.0 s | 11.7 GB |
| 27B, pipeline | **44%** | 70% | 11.3 / 19.9 s | 14.9 GB |
| 27B, no pipeline | 31% | 57% | 1.5 / 4.7 s | – |
| 8B, no pipeline | 15% | 50% | 0.5 / 1.6 s | – |

**1. The bigger model roughly doubles quality, at 2.7× the time.** The 27B
in the pipeline passes 44% of scenarios against 23%, and 43% of scored turns
against 22%. Twelve scenarios pass in every 27B run and in no control run.

- Routing is where most of the time goes. The 27B takes 7.6 s to route
  against the 8B's 2.6 s, and 1.0 s to its first answer token.
- The split (27B words, 8B decisions) is the slowest arrangement: 17.2 s.
  The two models do not fit together, so every turn reloads both.
- With the speech recogniser resident, the 27B still ran fully on the GPU
  (12.5 s, 56 tok/s). That left 0.8 GB free of 16.3, and no speech was
  actually transcribed. The pilot's 3.3 tok/s collapse probably had another
  model resident, so it was not a clean card.

**2. The soft stages repair the 8B and do nothing measurable for the 27B.**

- Turning them off drops the 8B from 23% to 13% of scenarios, and the 8B-q8
  from 27% to 11%.
- Almost all of that loss is one habit. Inside the pipeline both 8B models
  end with "Let me know if you need anything else", and `closing_offer`
  deletes it. `only_what_helps` falls 60 → 32 and `no_service_phrasing`
  falls 80 → 62.
- For the 27B, soft on and soft off are within the noise: 44% vs 41%,
  `only_what_helps` 87 vs 85. It does not produce what the stages remove.
- So the soft stages are compensation for the 8B, not a property of the
  pipeline. They can retire together with the 8B, and not before it.

**3. The pipeline damages what the model got right.** The same 8B with only
the persona beats itself inside the pipeline:

| property | 8B no pipeline | 8B pipeline |
|---|---|---|
| `accurate` | 98% | 84% |
| `notation_earned` | 88% | 46% |
| `says_what_it_solves` | 36% | 0% |

All five control turns judged inaccurate were correct in the draft and
corrupted afterwards:
- three by the speech filter on the display: `(x - a)` → `(x, a)`,
  `b² - 4ac` → `b² to 4ac`, cos x's minus signs dropped;
- one by search evidence about an unrelated "Taylor scale" of life
  satisfaction (the Korean trajectory);
- one by a Korean reply that called Taylor series "not an accurate concept".

The display/speech split (phase 3) is therefore a correctness fix, not
polish.

**4. The explanation budget, not the model, caps "why it helps".**

- Every explanation turn in the pipeline gets `max_words 45,
  max_sentences 2`. The replies fit it: a median of 32 words (8B) and
  29 words (27B), against 64 and 81 words with no pipeline.
- The judge may favour longer replies, but the 27B is under the same cap and
  says what the idea solves on 3 of 11 turns, against 0 of 11 for the 8B.
  So the cap is a ceiling, and within it the model matters.
- The fix is the budget, not a rule about purpose sentences (phase 4).

**5. The pipeline is tuned to how the 8B routes.** Asked "what time is it",
the 27B router sets `information_freshness: live`, which is correct. The 8B
says `unknown`.
- "live" sends the turn to a web search, and the search evidence replaces
  the local clock in the prompt.
- The 27B then reads New York's *sunrise* ("Sun: ↑ 06:41", from a time.is
  snippet) as the current time.
- This is why the 27B arms have the worst `clock_times`: 17–25%, against
  33–58% for the 8B arms.
- A `time_question` must be answered from the clock whatever the freshness
  field says. That is a hard invariant belonging in the routing code, not a
  guard on the reply.
- The same reasoning applies to every field the router emits: a model swap
  changes which paths run.

**6. q8 buys correctness, not judged quality.**
- q8 gets 89% correct values against 75%: `time_zone`, `utc_offset` and
  `celsius_to_fahrenheit` pass in every run.
- Its judged properties are within the noise of the 8B's.
- Cost: +1.1 s at p50 and +4 GB.

**7. What no model fixes.**
- Trajectories pass 0–12% in every arm.
- `time_seattle_vs_seoul` still invents Seoul's time: "13 hours ahead" in
  the 27B arms, and a wrong Seoul clock with the right 16 h gap in q8. That
  is the single-place `world_clock.read_place` bug.
- Korean clock answers still say 오후 for small hours.
- "I haven't actually checked that, so I'd rather not guess" still deletes
  a time that was available, in both models. That is `grounded_values` not
  counting the clock as evidence.
- These are pipeline defects, visible in every arm.

**Caveats.**
- The judge is below the agreement bar on `adapts_to_confusion` (the 27B
  shows 0–33% there on three turns per run), and weaker on Korean accuracy.
- `says_what_it_solves` and `concrete_first` are the properties the 27B
  raises, and they are rare yes verdicts. A blind confirmatory set (16
  replies, 20 labels) is in `runtime/evals/calibration-p2.json`, with its
  reading sheet beside it. Until it is labelled, those two rises are the
  judge's opinion.

**Trajectory failure categories** (0–12% in every arm). These are
documented here, and planned in `docs/PHASE3_PLAN.md` §1.6.

- **T1: router label semantics.** The pipeline reads `clarification` as
  "Elaina must ask", so the prompt carries no history. The 27B labels 10–11
  follow-ups per arm this way and answers about half of all follow-ups
  (19–20 of about 40) with no history, against 7–11 for the 8B.
- **T2: paraphrase substitution.** The 8B's `normalized_request` turned
  "테일러 급수" into "Taylor's scale" or "Taylor Swift's genre", and the
  answer followed the paraphrase.
- **T3: stale search evidence** deleted correct clock answers (Phase 3C).
- **T4: follow-up search on the wrong entity.**
- **T5: confusion turns restated**, not approached differently.
- **T6: receipts with service phrasing.**
- **T7: clock realization** (Phase 3B).

## Phase 3: what the rerun shows

The rerun finished 2026-09-27. Tables are in `docs/PHASE3_RESULTS.md`
(`evals/compare3.py`). Setup:
- the same corpus, judge (`qwen3.6:35b-a3b`, rubric v3), Ollama 0.34.4 and
  instrumentation as Phase 2;
- two runs per quality arm;
- each Phase 3 arm read against its own Phase 2 counterpart.

**1. The cleaned pipeline is better for both models.** Model held fixed,
scenarios passing / scored turns passing / correct values:

| | Phase 2 | Phase 3 |
|---|---|---|
| 8B | 23% / 22% / 75% | 31% / 34% / 95% |
| 27B | 44% / 43% / 70% | 47% / 51% / 95% |

- Model calls per turn fell (2.8 → 2.3 on the 8B). The p50 turn is faster:
  8B 4.2 → 3.8 s, 27B 11.3 → 9.5 s.
- The drift check (§6) shows this machine generated 7–12% more tokens per
  second in this session, so part of the latency gain is the environment.
  The replies themselves did not drift: the no-pipeline arms repeat Phase
  2 verbatim as often as Phase 2's own two runs repeat each other.

**2. Each Phase 3 fix did what it was for.**

| fix | before | after |
|---|---|---|
| 3A: display changed by speech shaping | 22–26 per arm | 0 |
| 3A: `notation_earned`, 8B | 46% | 91% |
| 3A: speech covering the display's substance | — | 100% of turns |
| 3B: 27B time turns sent to a web search | 14–15 of 26 | 0 |
| 3B: `clock_times` (8B / 27B) | 33% / 17% | 100% |
| 3B: `hour_difference`, both models (Seattle vs Seoul) | 0% | 100% |
| 3D: explanation length, median words (8B / 27B) | 32 / 29 | 57 / 60 |
| 3D: relevance (value) answers, median words (8B / 27B) | 14 / 8 | 12 / 5 |

- 3B also answers claimed clock, arithmetic and conversion turns in about
  1–1.5 s of turn time, with no router call.
- 3D moved 8B `says_what_it_solves` 0 → 14% and `concrete_first` 10 → 20%.
  27B `concrete_first` went 24 → 38%. Both need the blind check below.

**3. The model decision, pipeline held fixed.**

| | 8B | 27B |
|---|---|---|
| scenarios passing | 31% | 47% |
| scored turns passing | 34% | 51% |
| correct values | 95% | 95% |
| p50 / p95 per turn | 3.8 / 12.0 s | 9.5 / 19.1 s |
| routing, p50 | 2.4 s | 6.8 s |
| VRAM | 8.0 GB | 15.2 GB |

- The gap is 16 points of scenarios, down from 21 in Phase 2.
- Correctness is now equal: the clock and calculator answer those turns,
  not the model.
- What remains is explanation and conversation quality (§4, §5), at 2.5×
  the wait and nearly the whole card.

**4. Soft stages: the 8B still needs them, the 27B does not.**
- 8B: soft off takes scenarios from 31% to 18% (●▼). `only_what_helps` falls
  53 → 33% and `no_service_phrasing` 88 → 69%.
- 27B: soft off is 51%, against 47% with them on (within the noise, and if
  anything better).
- This is the evidence the plan asked for: retiring the soft stages goes
  with the 27B, not with the 8B.

**5. Follow-ups (R9, the `followups` suite).**
- Both models now answer rather than ask back, and stay on the subject, on
  every follow-up turn.
- The 27B takes a genuinely different route on 100% of them (`adapts_to_confusion`)
  against the 8B's 8%, which mostly restates. The judge is below the bar on
  that property, so read it as a direction.

**6. Regressions and gaps the rerun exposed (not fixed; for the review):**

- **3D gave the explain budget to turns that are not questions.** The
  `conversation` intent covers receipts and remarks as well as information
  requests. When such a turn is given the ANSWER act, it got the explain
  goal ("show one concrete case"):
  - the 8B answered "Thanks, that makes sense" by explaining hash tables
    again, and a worried remark about a big PC case with "Desktop Control
    Mode can…";
  - 8B `takes_the_turn_as_meant` fell 83 → 58% (●▼).
  - Fix: explain only when the router's `speech_act` is
    `information_request`. That is a signal the router already gives, and
    one condition.
- **3D's example goal lands on formula and fact questions.** "What's the
  quadratic formula?", "the Taylor series of eˣ", "x 제곱을 미분하면" gained
  a worked example the judge counts as not asked for: 27B
  `only_what_helps` 87 → 73% (●▼). There is no signal today that tells
  "what is the formula" from "why does it work" without adding a
  classifier. Options:
  - accept the trade (examples help the explanation properties);
  - make the goal conditional in its own wording ("when the answer is a
    formula or a value, give it and stop");
  - carry a formula/definition signal from the router.
- **3C still deletes numbers inside mathematics in a follow-up after a
  search.** 1–2 turns per arm, all the same shape. The Taylor trajectory
  carries the UW Math 207 search into its follow-ups, and the attribute
  reader then takes the "1" in "1 + x + x²/2…" as an unverified value. The
  turn's findings record named the value and its status at once.
  - Fix: the value reader skips notation spans.
  - The acceptance target of zero is therefore not met (0–2 per arm, from
    1–3 in Phase 2).
- **R9 on the 27B also catches follow-ups that are not re-explanations.**
  "the second one" and "얼마나 우려야 돼?" were labelled `clarification`, so
  they got the elaborate budget and the "it did not land" framing. No harm
  was measured on those turns (27B `takes_the_turn_as_meant` 92%), but the
  label is wider than its meaning.
- **The 27B's router overflows its 4,096-token context.** Its first routing
  answer is cut off mid-JSON on 22 of 62 routed turns (30 of 78 in Phase 2),
  and each overflow costs a repair call of about 6 s.
  - A longer R9 definition (+60 tokens) doubled that to 59 of 63 in a first
    attempt of the rerun; the definition was shortened below its Phase 2
    length and the rerun restarted.
  - Part of the 27B's routing time is therefore this limit, not the model.
    A larger context or compact JSON would reduce it, at a VRAM cost for
    the former.
- **The 27B with the speech recogniser resident is at the card's limit.**
  - 15.8 GB of 16.3 in this run (15.5 GB in Phase 2).
  - Generation fell to 44 tok/s, from 55 with the same six scenarios in
    Phase 2.
  - Every model was still "100% GPU", but the headroom is gone.
- **Two rerun interruptions, both environmental.**
  - The Hugging Face hub timed out while a backend started; the rerun now
    runs with the hub offline, and the model was cached.
  - The real Elaina app took port 8765 mid-run.
  - Both runs resumed; no scenario was lost or doubled.

**Caveats.**
- The judge (87% agreement, kappa 0.73) is below the bar on
  `adapts_to_confusion` and weaker on Korean accuracy.
- The rises in `concrete_first`, `says_what_it_solves` and
  `adapts_to_confusion` are rare-yes properties. The blind set
  `runtime/evals/calibration-p3.json` (20 replies, 28 labels) is waiting
  for a person before they are believed.

## Still open

- **The blind labels**:
  - `runtime/evals/calibration-p2.json` (Phase 2's rises);
  - `runtime/evals/calibration-p3.json` (Phase 3's).
- **The model decision**: the user's, from `docs/PHASE3_RESULTS.md` and
  §Phase 3 above.
- **The fixes the rerun exposed** (§6 above), if they are to be in before
  the decision:
  - the explain budget for non-questions;
  - numbers inside notation;
  - the formula/example tension;
  - the 27B's routing context.
