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

## Phase 3, final: after the four fixes (2026-09-29)

The same matrix, corpus, Ollama 0.34.4, rubric v3 and judge, in
runtime/evals/p3f; the tables are `docs/PHASE3_FINAL_RESULTS.md`. This
section is what reading the runs by hand added to them. No code was changed
after the rerun except the report generator.

**The four fixes, read from the replies.**
- **Explanation room for turns that ask nothing.** Every thanks, "Yeah.",
  "Studio." and remark now gets the value budget; in Phase 3 all of them got
  the explanation budget. The long replies left ("Thanks, that makes sense"
  → 40 words) are the 8B's router filing the thanks as `action_request`.
- **Examples on a formula or fact.** Unasked examples fell where the router
  says `state`: quadratic formula 7/9 → 0/9, cos series 9/9 → 1/9, "x 제곱을
  미분하면" 4/9 → 0/9. The 8B labels "the Taylor series of eˣ" and "the
  formula for a Taylor series" `explain` and keeps the example (9 of 10);
  the 27B does not (0 of 8).
  - Side effect of this fix: splitting the goals dropped "what problem it
    solves … or worked example" from the explain wording. The property
    rates do not show a broad loss (`says_what_it_solves` rose for both
    models), but `hash_table_how` and `overfitting_nontechnical` lost
    `concrete_first` and `terms_explained` in every final 27B run. It is the
    likeliest cause of the 27B soft-off scenario drop (51% → 45%), with
    scenario churn in both directions.
- **Notation is not a claim.** All Phase 3 deletions (the Taylor-series LaTeX
  "1") are gone. Of three values the integrity counter still flags, two are
  correct removals of invented mouse specs, flagged because "12" occurs
  inside "$120" in the search text. The third is a real false positive:
  "12시간에서 24시간" deleted as a damaged "125". The near-miss rule compares
  bare numbers across all evidence, whatever they measure; its docstring
  scopes it to numbers the person said.
- **The 27B's router overflow.** Truncated first answers fell from 15–25 to
  0–1 per run and repairs from 30–34% to 1–2%. Router time per turn went
  from 8.25 to 8.03 s (mean, soft on) and 8.65 to 7.79 s (soft off), with
  p95 at 13.4 → 12.6 and 15.0 → 11.2 s. That is on a day the drift arms put
  about 8% slower. The first call's p50 rose, 6.2 → 7.3 s, because answers
  now finish instead of stopping at the window. The one remaining truncation
  is the 512-token output cap: the model rambles in `reason`.

**Correct values, read by hand.** The scored rates (8B 96%, 27B 93–96%)
invert the truth.
- Every 27B miss is a check that cannot read a right answer:
  - "2x plus 3" (3 times);
  - "UTC minus seven hours" (2);
  - a correct average with its range.
- So by hand the 27B is correct on every final turn.
- The 8B misses "x 제곱을 미분하면" in 4 of 4 runs.
- The same two checks, plus `must_not_contain "8 hours"` matching inside
  "18 hours", also cost the 27B scenarios in Phase 2 and Phase 3. The
  corpus was left as it was for this comparison.

**Defects found reading the runs.** All were present before the four fixes.
All are fixed in the next section, except the 8B's reading of 미분.
- **`final_check` removes a minus sign with the echo it strips.**
  - "The temperature in Chicago right now is -5 degrees." becomes "5
    degrees."
  - The q8 run's "Your UTC offset is -0700" became "0700.".
  - `_ECHO_DASH` reads " -" before a digit as a dash. That is a
    hard-invariant breach.
- **The calculation planner invents arithmetic when there is none.**
  - A request with no arithmetic ("x 제곱을 미분하면", "how long should cold
    brew steep") routed as `calculation` is told twice to call the tool.
  - The model then makes a calculation up ("2 + 2 = 4"; "coffee for 500 g
    of water at 1:4: 125"), and it enters the ledger as a *verified
    calculation*.
  - The invented 125 is what the near-miss rule above read "12 hours"
    against.
- **`not_her_last_answer` destroyed a correct answer every time it acted.**
  - It fired on the cold-brew follow-up in 9 of 9 cases across both phases.
  - The regeneration "without history" read "얼마나 우려야 돼?" as worry (8B)
    or laundry (27B).
  - It fires more in the final runs because that turn is now more often
    routed as a value question.
- **The 8B reads 미분 as 파생상품 (financial derivatives)** in every run of
  both phases, every quantization. That is the model's knowledge, not the
  pipeline.

**The 27B with the speech recogniser loaded is not reproducible.**
- In Phase 3 it ran at 41 tok/s (turn p50 11.9 s). Today it ran at
  10.6–13.7 tok/s (turn p50 62–98 s).
- Rerun back to back at context 6144 and at Ollama's default, the default
  was no faster. So the context window is not the cause: the router fell
  back on 4 of 5 turns at the default window.
- Ollama saw the same free memory before loading on both days, and the
  recogniser's configuration is unchanged.
- The combination sits within a few hundred MB of the 16.3 GB card. Whatever
  else is open (today: Wallpaper Engine, browsers, and more) decides whether
  it runs at full speed or four times slower, and the driver pages
  silently rather than failing.
- The 8B has about 6.5 GB of headroom.

**For the 8B FAST + 27B DEEP evaluation.** These are not a decision.
- The 27B's advantage is concentrated in:
  - explanation (`says_what_it_solves` +50 pts, `only_what_helps` +32,
    `concrete_first` +24). `says_what_it_solves` and `concrete_first` are
    rare-yes properties: their gaps hold by the person's blind labels too,
    with the judge's 27B rates as upper bounds;
  - `adapts_to_confusion` (100% vs 50%, but a property the judge is below
    the bar on);
  - the router's `answer_shape` and intent labels.
- On short, value and social turns the two models score alike.
- The 27B's router first call (about 7 s) is the largest single cost in its
  turn. Routing with the 8B (about 2.4 s) and answering with the 27B only
  where depth pays is where the time is.
- Both models do not fit at once:
  - 8B 5.7 GB + 27B 12.7 GB at context 6144, before the recogniser;
  - so every switch between them is a load, about 7–8 s for the 27B;
  - that is the first thing such an evaluation has to measure.

## After the final rerun: the defects and the checks, fixed (2026-09-29)

Each fix has a regression test that fails without it. The suite is green
(3,918 tests).

| defect | fix | regression tests |
|---|---|---|
| `final_check` removed a minus sign with an echo | `_ECHO_DASH`: a dash with a digit straight after it is a sign or a range, never a break | `test_response_quality` (minus sign, range, spaced dash still breaks) |
| The planner invented arithmetic for a request with no numbers | `CalculationPlanner.plan` declines without asking the model when the request has no digit; the turn is answered the ordinary way | `test_calculation_planner`; whole turn in `test_final_rerun_defects` |
| `not_her_last_answer` re-asked a follow-up bare | It re-asks the router's self-contained `normalized_request`, not the words said | `test_misread_repairs`; whole turn in `test_final_rerun_defects` |
| The near-miss rule read machine evidence | Damaged copies are only of the person's numbers (`grounded_values.PERSON_SOURCES`), in `findings`, `needs_correction` and `correct_values` | `test_grounded_findings`; engine guard in `test_final_rerun_defects` |
| The explain goal lost "what problem it solves … worked example" | Phase 3's wording restored, keeping "why it happens" | `test_response_budget` |

The planner's gate rests on a measurement: every calculation-routed request
in every eval run carried its numbers as digits, and the only ones without
were not arithmetic ("How many ounces are in a cup?" is claimed earlier by
the conversion domain). A request with spelled-out numbers and no digit
would now be answered without the calculator, not wrongly.

**The eval checks** (`evals/checks.py`, `test_eval_corpus`). The checker was
fixed, not the corpus: the same scenarios and patterns, read better.
- A phrase that starts or ends with a digit is not found inside a longer
  number ("8 hours" / "18 hours", "8시간" / "18시간").
- Offsets said in words are read ("UTC minus seven hours", "your UTC offset
  is -7").
- Pattern checks also read the reply's mathematics with LaTeX fractions and
  spoken operators as symbols ("2x plus 3", "\frac{1}{3}").
- A stated range that brackets the true value is one right claim.

Rescored from the stored replies (no regeneration, no new verdicts), the
final run's 27B correctness is 100% in both arms (was 93% / 96%). Scenarios
pass at 54% soft on and 49% soft off (was 50% / 45%). Phase 3's 27B also
reads 100%, and Phase 2's 73–77%. The 8B is unchanged (96%); its miss is
real. `docs/PHASE3_FINAL_RESULTS.md` is regenerated with the corrected
checks.

**Live check** (runtime/evals/p3f-fix: the nine scenarios the fixes touch,
one run per model, soft stages on, same judge):
- "x 제곱을 미분하면 뭐야?" on the 8B: routed as a calculation as before.
  The planner now declines without a model call (it made four), and the
  answer is "X 제곱의 도함수는 2x입니다." It failed 4 of 4 final runs.
- The cold-brew follow-up: both models gave the steeping time, and neither
  reply was rewritten; the 27B passes where a final run answered about
  clothes. This time the routers did not file the turn as a value
  question, so the planner and `not_her_last_answer` paths were not
  exercised live. The whole-turn tests cover them.
- `hash_table_how` on the 27B passes again (it failed both final runs).
  `overfitting_nontechnical` still opens with the definition and fails
  `concrete_first`, so the restored wording does not explain that one.
- The 27B's "UTC minus seven hours" now scores as the right offset.

## FAST/DEEP post-fix baseline (2026-09-29)

The protocol is `docs/FAST_DEEP_PROTOCOL.md` (with its amendments). The
tables come from the frozen analysis, fingerprint `cbd8c3b57913990a`, in
`docs/FAST_DEEP_BASELINE.md`. The runs are in `runtime/evals/pf/`:

- 3× FAST (8B, soft stages on) and 3× DEEP (27B, soft stages off), all six
  suites, alternating;
- the drift arms and the recogniser arms;
- judge `qwen3.6:35b-a3b`, rubric v3.

Every scored turn got a complete set of verdicts, with no judge errors. No
product code changed for the run. This section is the hand reading; the
numbers are the report's.

**End to end.**

| | FAST | DEEP |
|---|---|---|
| scenarios | 26% | 52% |
| turns | 29% | 58% |
| correct values | 100% | 100% |
| turn latency p50 | 3.7–4.0 s | 9.4–10.3 s |
| router's first call | 2.3–2.5 s | 7.9–8.7 s |
| router fallbacks and truncations | none | none |

- The 8B's "x 제곱을 미분하면" is now right. The calculator declines it
  instead of inventing "2 + 2".
- On the five historical suites, against the final rerun under today's
  evaluator:
  - FAST: 29% / 32% scenarios / turns, from 32% / 34%;
  - DEEP: 57% / 62%, from 49% / 52%.

**Resources.**

- No call was under pressure in any arm.
- VRAM: FAST 6.9–7.7 GB, DEEP 13.7–14.5 GB.
- With the speech recogniser loaded:
  - FAST 9.1 GB, p50 3.7 s;
  - DEEP 15.4 GB, p50 9.9 s, at a normal 48.2 tok/s.
- That is the same footprint that ran at 11 tok/s (87 s p50) in the final
  rerun. The configuration fits when nothing else presses on the card, and
  has no headroom when something does.
- Cold loads: 8B 3.3–3.6 s, 27B 6.8–7.0 s.

**Stable labels** (FAST run *i* against DEEP run *i*; 2 of 3 needed):

| label | turns |
|---|---|
| stable DEEP+ | 23 |
| pending blind labels | 1 |
| stable DEEP-worse | 4 |
| no routing ground truth | 45 (42 of them consistently no different) |

- By category:
  - "I still don't understand" follow-ups: 7 of 9 DEEP+, 1 pending, 1
    DEEP-worse (`p_value_last_part`);
  - first-time explanations: 10 of 19 DEEP+, 2 DEEP-worse (the bus and the
    ice, on `terms_explained`);
  - factual, calculation, time and date: 0 of 26.
- The routers agreed on intent on 126 of 135 first-turn pairs and 46 of 84
  later-turn pairs. The later-turn labels are end to end.
- **Replicate noise is higher than in the final rerun.** Pass/fail differs
  across runs on 13 of 73 turns (FAST) and 26 of 73 (DEEP). Three runs were
  needed.

**Routing, against the stable labels.**

| policy | DEEP+ recall | DEEP precision | DEEP use | DEEP-worse turns sent |
|---|---|---|---|---|
| budget explain or elaborate (P1) | 96% | 50% | 60% of turns | all 4 |
| shape explain, example or elaborate (P3) | 91% | 50% | 58% of turns | all 4 |
| budget elaborate only (P2) | 9% | 64% | 4% of turns | none |
| perfect knowledge | 100% | 100% | 32% of turns | none |

- The existing signals find the opportunity. They cannot separate it from
  the rest of the explanation turns, and they cannot avoid the DEEP-worse
  ones, which are explanation turns too.
- **The perfect-knowledge composite passes 45% of turns; all DEEP passes
  58%.** The gap is turns without routing ground truth, mostly FAST's form
  defects. By the protocol, those are FAST realisation targets, not reasons
  to route.
- With swap costs charged (27B load 6.8 s, 8B reload 3.3 s), P1's p50 is
  11.7 s against all FAST's 3.8 s.

**Step 4, classified.** No guard or patch was made; proposals are for review.

| finding | class | evidence | proposal |
|---|---|---|---|
| `overfitting_nontechnical` still fails `concrete_first` (and `terms_explained`) in both arms | response realisation, plus model capability | The restored wording reached all six prompts. Both models open "Overfitting happens when…"; the 27B's student analogy is third. The judge's reasons match the rubric. | Systemic, not implemented. The explain goal never asks to lead with the case, its first clause ("say why it happens") invites a definition, and unlike the state goal it does not ask for technical terms to be explained. `terms_explained` is low for both models (28% / 48%). A wording change would have to be measured as its own change. |
| `hash_table_how` now stable DEEP+ | evaluator variance | FAST's reply is identical in all three runs. DEEP's three replies are near-identical to each other and to the final rerun's ("buckets", "chaining or probing" unexplained), yet `terms_explained` passed 3/3 and `concrete_first` 2/3, where near-identical text failed both in the final rerun. | Add the turn to the blind labels as a spot check. The generation did not change. |
| Unwanted examples on formula and value questions | none from the explain wording | No `state` or `value` prompt carried an explain goal (0 of 146). The remaining `only_what_helps` failures are appended extras (a discriminant, "that's exact", a zone name): FAST 18/63 value turns, DEEP 5/60. | Decision needed: the value goal itself allows "at most one short sentence of context", which the judge then penalises. Either the goal or the property's strictness is wrong for value turns. |
| Regressions from the four fixes | none found | See the rows below. | — |
| The fixes' own actions | as intended | `final_check`: 1 change, not numeric. `grounded_values`: 3, all correct (invented mouse specs, a wrong film runtime). No conflicting near-miss findings. The calculator declined every digitless request without a model call. | — |
| FAST's lower `concrete_first` (24% → 11%) on explanation turns | mostly evaluator variance | Bus brakes: both versions open on the scene ("When a bus suddenly brakes…"), judged yes before and no now. Compound interest: definition then worked example in both. | Blind spot check before believing it. |
| `not_her_last_answer` still fires on the cold-brew follow-up (all three DEEP runs) | routing, plus realisation | When the 27B router's question was self-contained, the re-ask answered correctly (2 runs). When the router left it as "얼마나 우려야 돼?", the re-ask came back about laundry (1 run). The router had called the turn a `clarification` each time. | Systemic, not implemented: don't apply the stage to a turn the router calls a clarification, a re-explanation that keeps history (R9), where repeating the value is expected. |
| The calculator still "verifies" symbolic maths when the request has a digit | tool behaviour, plus routing | "Calculate the derivative of the function x squared plus 3x" (27B router) → planner → "power rule applied: 5". The 27B ignored it and answered "2x plus 3"; the 8B has followed such evidence before. | Systemic, not implemented: symbolic maths should not reach a numbers-only calculator. This is a routing definition, not a digit rule. |
| One FAST answer sentence deleted because it held one Chinese character ("냉萃") | response realisation | `final_form` removed the whole answer sentence; the reply was left without its value. The 8B router had also misread the turn as a question about quantity. Observed once. | Consider removing the foreign token, not the sentence. |
| "I don't really get what a derivative is" answered as finance by both models | corpus ambiguity (evaluator side) | The English turn has no calculus context. The judge accepted the finance reading. | Report only; the corpus stays as it is. The Korean 미분 turn is unambiguous: the 8B answers 파생상품, the 27B answers calculus (DEEP+). |
| FAST cannot recall the school ("where is my school again?") | model capability | Wrong in the final rerun ("you haven't told me") and now ("check your calendar"). | None from the pipeline. |

## After the baseline: blind labels, the hard fixes, shadow routing (2026-09-29)

The user accepted the baseline and asked for three hard fixes, a test of the
value-answer sentence, and an offline routing analysis. There is still no
production router, and no rule is turned on.

**Blind labels** (the user's, 12 replies and 20 labels):
- The judge agreed with them 90% of the time (kappa 0.80): `adapts_to_confusion`
  8 of 8, `concrete_first` 10 of 12. The two disagreements are the known
  pattern: the judge accepts an analogy as concrete where the person does
  not.
- The pending turn (`ko_climate_weather_last_part` t1) is confirmed. The
  frozen report, regenerated, now has 24 stable DEEP+, none pending and 4
  DEEP-worse, and the confusion follow-ups stand at 8 of 9.

**The three hard fixes.** Each has regression tests that fail without it;
the suite is green (3,953).

| issue | fix | tests |
|---|---|---|
| A clarification's context lost by `not_her_last_answer` | The stage no longer asks again, without history, on a turn the router calls a clarification, or on a follow-up whose reading is still the words said (`_reply_rests_on_the_conversation`). A question that stands on its own is still asked again. | `test_final_rerun_defects`: whole turns for both cases, plus the stage's original purpose. |
| A digit-bearing symbolic request reaching the calculator | `asks_for_arithmetic`: a digit **and** no variable. The variable is read with the notation module's own tokens and closed operator, power and function vocabulary (`spoken_notation.names_a_variable`); letters used as words ("Plan A", "a third", "I") are not variables. The router's `calculation` definition now says arithmetic on their numbers, with algebra and calculus on a variable as `knowledge_question`. | `test_calculation_planner`, including spelled-out numbers staying protected; `test_final_rerun_defects`: reader cases and a whole turn. |
| A sentence deleted for one foreign character | `without_foreign_script` removes the kana run, or the Han run written into a Korean word, and keeps the sentence. Real 한자 is untouched. | `test_foreign_script`, `test_glued_han`, including the measured "냉萃 … 1~2컵" case. |

**Targeted live check** (`runtime/evals/pf-fix2`): the 24 scenarios the fixes
touch, FAST and DEEP, 3 runs each, same judge, against the frozen baseline.
- FAST turns passing 35/99 before and after; DEEP 68 → 63/99. Correctness
  48/48 in all four.
- There were no laundry or worry replies, and the calculator made no calls:
  the 12 calculation evidences are all tier-0 domain claims.
- The one `not_her_last_answer` action re-asked the router's self-contained
  reading and corrected a wrong draft (cold brew "in milk").
- The one foreign-script repair kept "…12~24시간…" and removed "萃".
- No regression is attributable to the fixes. The turns that got worse
  were read one by one:

  | turn | what happened | cause |
  |---|---|---|
  | `ko_date_today` (FAST) | The reply appends the time before and after; the judge passed the same pattern in a baseline run. | evaluator variance |
  | `ko_quantum_computer_still_lost` t0 (DEEP) | An added "limitations" sentence. | generation variance |
  | `definite_integral` (FAST) | The value 1/3 is right. Without the calculator the 8B wrote the worked integral, and `length_rewrite` shortened it, failing `display_math_intact`. | realisation |
  | `p_value_last_part` t1 (FAST, 1 of 3 runs) | `existence_claims` read "if the effect … isn't real" as a claim that something does not exist, and replaced the first sentence with "I don't actually remember that one…". | a guard misfiring; not caused by the fixes |

**The value-answer sentence** was treated as communication quality, not a
routing blocker.
- The value goal "add at most one short sentence of context" was replaced
  by "add a sentence only when the value would be misread without it".
- On value turns, `only_what_helps` went from 19 to 22 of 36 (FAST) and from
  34 to 36 of 36 (DEEP). Correctness is unchanged, and `answers_the_ask` is
  98/99 (FAST).
- The gain is small, mostly `cup_ounces`. The Korean date's appended time
  persists.
- It is kept provisionally; the change is one line to revert.

**The ambiguous derivative turn.** `derivative_simply` carries a note (the
loader ignores notes, so the frozen fingerprint is unchanged): it is
ambiguous and not routing evidence. The unambiguous version,
`derivative_simply_in_calculus`, has a calculus context turn and checks the
mathematical meaning. It is staged in `evals/scenarios_next/` for the next
frozen corpus version.

**Shadow routing, offline** (`evals/fastdeep_routing.py`,
`docs/FAST_DEEP_ROUTING.md`).
- It reads only the 8B router's recorded output and the engine's recorded
  state, and scores deterministic rules with the frozen analysis unchanged.
- All numbers are **in-sample**: 73 turns, 24 stable DEEP+.

Within the 44 explanation-budget turns:

| signal | DEEP+ | no difference | DEEP-worse |
|---|---|---|---|
| router says follow-up | 12 | 0 | 1 |
| previous turn was an explanation | 11 | 0 | 1 |
| conversation under way | 13 | 1 | 1 |
| first-time, Korean | 5 | 3 | 0 |
| first-time, English | 6 | 14 | 3 |

- No existing signal separates the English first-time explanations; the
  "second discriminating signal" does not exist in today's fields.
- The best-profiled rule is **R2: explanation budget and the router says
  follow-up**:

  | | value |
  |---|---|
  | DEEP+ recall | 50% |
  | precision | 92% |
  | utilization | 18% |
  | DEEP-worse routed | 1 of 4 (`p_value_last_part` t1) |
  | turns passing | 37% (all FAST 29%, perfect knowledge 46%, all DEEP 58%) |
  | p50 / p95 with swaps | 3.9 / 12.4 s |

- Adding Korean first-time explanations (R5) gives 71% recall, 81% precision
  and 29% utilization.

**The interpretation confound decides the next step.**
- Of R2's 12 DEEP+ turns, 9 are turns the two routers read differently. On 8
  of the 9 confusion follow-ups, the 27B router called the turn a
  `clarification` with the elaborate budget in every run; the 8B called it
  `conversation` with the explain budget.
- So in the DEEP arm those turns were also written under the re-explanation
  instruction. Only `index_fund_what_do_you_mean` t1, where both routers
  agreed, credits the 27B's generation cleanly.
- Two consequences follow. Under an 8B router, the 27B would answer these
  turns with the 8B's reading, so the measured gain may shrink. And part of
  the gain may be reachable in FAST, if the 8B's reading of confusion
  follow-ups were right.
- The labels also move between sessions. In the targeted runs,
  `ko_climate_weather_last_part` t1 came out DEEP-worse and
  `still_confused_derivative` t1 unstable. One session's labels are not
  final ground truth.

## Stage B: DEEP under the FAST router (2026-09-30)

The diagnostic of `docs/FAST_DEEP_PROTOCOL.md` §12:

- three arms in one session, on the current code, 3 runs each, alternating;
- 14 scenarios with follow-up turns;
- the frozen comparison and 2-of-3 rule.

| arm | reads the turn | answers | soft stages |
|---|---|---|---|
| FAST | 8B | 8B | on |
| SPLIT | 8B | 27B | off |
| DEEP | 27B | 27B | off |

The report is `docs/FAST_DEEP_STAGEB.md`.

**The answer: on confusion follow-ups the gain is mainly the 27B's
generation, not its reading.**
- The baseline had 8 confusion follow-ups (D1) that were stable DEEP+.
  - With the 27B answering under the 8B's reading (FAST → SPLIT), 6 are
    better: 3 stable, and 3 resting only on `concrete_first` or
    `adapts_to_confusion`.
  - DEEP with its own reading is better on 6 in this session: 4 stable, 2
    judge-only.
- Across all 14 baseline follow-up DEEP+ turns, SPLIT keeps 6 stably (9
  counting judge-only), and DEEP keeps 9 stably (11 counting judge-only).

**Where the 27B's reading matters.** SPLIT → DEEP is better on 7 later turns,
mostly not confusion follow-ups:

- the cold-brew how-to after a time question;
- a thanks;
- two explanation follow-ups;
- `explanation_thanks_time` t1;
- two confusion follow-ups (`ko_exchange_rate_another_way`,
  `noise_cancelling_another_way`).

It is worse on `ko_taylor_then_time` t1.

**Labels move between sessions.** Of the baseline's 14 follow-up DEEP+ turns,
FAST → DEEP in this session keeps 9 stably. `live_session_2026_09_23` t2 and
`still_confused_derivative` t1–t2 no longer are.
`p_value_last_part` t1 is worse again, in both SPLIT and DEEP.

**Swap costs, measured in the pipeline.** SPLIT swaps on every turn, 101
times each way:

- reloading the 8B takes a median 4.0 s;
- loading the 27B takes a median 7.5 s;
- turn p50 / p95: FAST 3.9 / 8.2 s, SPLIT 17.2 / 19.4 s, DEEP 10.6 / 14.8 s.

A routed system pays the 27B load on each DEEP turn, and the 8B reload on
the turn after it.

**Found along the way.** `_answered_on_its_own`, the re-ask of
`not_her_last_answer`, passes the decision model, not the words model. In a
split system that path is written by the 8B. It fired once in SPLIT.

**Pending.** The three judge-only FAST → SPLIT comparisons need the person's
blind labels (`runtime/evals/pb/split_blind.json`, 16 replies and 18
labels; the report counts them once they are filled in). The history limit
still stands: SPLIT's history is the 27B's own. A system that routes only
the follow-up would have FAST's history, which needs a model switch inside a
conversation.

## The shadow FAST/DEEP router (2026-09-30)

**Stage B, settled.** The person's blind labels on the SPLIT comparisons
(16 replies, 18 labels) confirmed `ko_exchange_rate_another_way` t1.
- FAST → SPLIT now has 7 stably better later turns. On the confusion
  follow-ups, SPLIT and full DEEP match: 4 stable each, and the same 2
  judge-only.
- The judge's `adapts_to_confusion` agreement on these labels was 69%
  (kappa 0.38), still below the bar.
- The interpretation is frozen. On confusion follow-ups, most of DEEP's
  measured advantage is the 27B writing the answer, not the 27B reading the
  turn. SPLIT is not a production architecture: its earlier answers were the
  27B's too.
- Measured switching cost: the 27B loads in 7.5 s, the 8B reloads in
  4.0 s. Turn p50: FAST 3.9 s, SPLIT 17.2 s, DEEP 10.6 s.

**The router, in shadow** (`brain/fastdeep_router.py`).
- Once per answered turn, after routing and the response budget, the
  engine records `context.shadow_route` in the turn's trace: decision
  FAST or DEEP, the reason, the confidence, the continuation signals that
  held, and every input.
- The turn is still answered by the FAST path. A test pins that, and a live
  two-scenario run confirmed it: "What do you mean?" was recorded DEEP with
  high confidence and answered by `qwen3:8b`.
- There is no model call, no phrase list and nothing from the corpus.
- **Signals**, all already in the turn:
  - the 8B router's intent, speech act, answer shape, follow-up flag, topic
    shift and detail flag;
  - the response budget and shape;
  - whether a deterministic domain claimed the turn;
  - the previous turn's budget (moved at each turn's start, so it is never
    stale);
  - the language and the router's confidence.
- **The rule.** DEEP when the turn has explanation room, the topic has not
  shifted, no domain claimed it, and at least one continuation signal holds:
  the router says follow-up, the previous turn was an explanation, or the
  router reads a clarification. Two or more make it high-confidence.
- First-time explanations stay FAST, with that reason recorded. No existing
  signal separated the ones the 27B improved.

**Offline, against the frozen evidence** (`evals/fastdeep_shadow.py`,
`docs/FAST_DEEP_SHADOW.md`). This is development data.

Against the baseline's FAST/DEEP labels:

| | value |
|---|---|
| DEEP+ recall | 50% |
| DEEP precision | 92% |
| DEEP use | 18% |
| DEEP-worse turns sent | 1 of 4 (`p_value_last_part` t1) |
| needless escalations | 0 |
| turns passing (all FAST 29%, perfect knowledge 46%, all DEEP 58%) | 37% |

The 12 misses are first-time explanations, two late turns and a thanks: the
turns the rule leaves FAST by design.

Against Stage B's FAST → SPLIT labels (DEEP under the 8B's reading):

| | value |
|---|---|
| DEEP+ recall | 83% |
| DEEP precision | 53% |
| DEEP use (follow-up-heavy scenarios) | 47% |
| DEEP-worse turns sent | 1 |
| needless escalations | 1 (`live_session_2026_09_23` t2) |
| unstable or judge-only | 4 |
| turns passing (all FAST 28%, all SPLIT 33%, perfect knowledge 36%) | 35% |

Latency, switching included:

| | p50 / p95 |
|---|---|
| all FAST | 3.9 / 7.6 s |
| shadow router | 4.8 / 17.2 s |
| perfect knowledge | 4.1 / 13.9 s |
| the 27B resident for everything | 10.6 / 14.8 s |

One DEEP turn costs about 9.5 s more than FAST (the 27B load plus slower
generation), and the turn after it 4.0 s (the 8B reload).

**`not_her_last_answer`, corrected.** The re-ask and the corrected-question
re-ask are now written by the turn's words model (`active_model`), never the
decision model. With one model configured, nothing changes. Tests:
`TheReAskIsWrittenByTheWordsModelTests`.

**Corpus v2** (`docs/CORPUS_V2_PLAN.md`, staged in
`evals/scenarios_next/followups_v2.json`): 24 held-out scenarios, 44 scored
turns.
- A: confusion follow-ups, several implicit. B: deepening follow-ups.
- C: follow-ups that must stay FAST. D: first-time explanations.
- Nothing is registered or frozen. It waits for the user's review, then
  gets its own freeze (`runtime/evals/pf2/FROZEN.json`, router hash
  included). It runs FAST, SPLIT and DEEP, 3 runs each.

## Corpus v2: review and protocol, frozen (2026-09-30)

`docs/FAST_DEEP_V2_PROTOCOL.md` is the protocol and `evals/fastdeep_v2.py`
the code that runs it. The user approved it, kept "Hm?" and the DEEP arm, and it
was frozen at 22:43 (`runtime/evals/pf2/FROZEN.json`; `--verify` passes). Nothing
in v2 has run.

**Review of the staged corpus.**

- **Held out.** No subject, scenario ID or utterance appears in v1, the
  contamination matrix or the Stage B runs.
- **Two group B follow-ups replaced.** "What's the catch?" and "When would
  you not want it?" carried the `purpose` mode, so the judge would grade
  `says_what_it_solves` (benefits) on a question about drawbacks.
- **Implicit confusion was thinner than stated.** "Simpler, please." and
  "무슨 말인지 모르겠어" are explicit. Two implicit cases were added: a wrong
  restatement, and a "too hard" with no request.
- **Group E added.** A confusion follow-up, then a turn that should return
  to FAST, so that switching back is measured inside one conversation.
- **Measurement fixes:**
  - value checks were made required;
  - a redundant Korean metres turn was replaced and given a check;
  - Korean turns check their language;
  - "the second step" was reworded, so that it does not presuppose steps.
- **Hard cases.** None was removed. "Hm?" stays, flagged as ambiguous
  (confusion or not heard).
- **Group C.** It escapes the rule only through its FAST gates. An
  explanation-shaped follow-up that did not need DEEP is measured by
  precision, not by C.

**Why SPLIT is not the ground truth for a follow-up.** SPLIT's history is
the 27B's own. Two new arms use the real switch inside one conversation
(`brain/fastdeep_apply.py`), and it is evaluation only: unset, every turn is
FAST.

- **ROUTED** is the routed system itself.
- **LATE**, the 27B writing every turn after FAST's first, gives each
  follow-up's ground truth with FAST's history.

**The smoke run on v1 scenarios** (no v2 turn run):

- the switch, the 27B's history and the return all work;
- the swaps cost 7.9–8.2 s (27B) and 4.2 s (8B) on Ollama 0.35.0. It had
  updated itself from 0.34.4 since Stage B, so the freeze pins the version;
- **two consecutive DEEP turns pay both swaps, 18.2 s**, because the 8B
  reads every turn. v2 has none; this is a production design item.

**v1 is untouched.** The v2 suite is made visible to the frozen v1 analysis
only inside v2's processes, so v1's fingerprint (`cbd8c3b57913990a`) and
reports still hold in the working tree. A test checks this.

## Corpus v2: the frozen evaluation, NOT ACCEPTED (2026-10-01)

`docs/FAST_DEEP_V2.md`, generated by the frozen code.

- **Run:** 5 arms × 3 runs, 01:20–06:55. All 15 runs are complete and every
  turn was judged, with no timeouts. The freeze still verifies.
- **The aborted start.** A first start at 23:10 died before its first turn;
  it is kept in `runtime/evals/pf2-aborted-20260930/`.

| criterion | measured | threshold | met |
|---|---|---|---|
| DEEP precision | 21% (13/61) | ≥ 70% | no |
| DEEP+ recall | 87% (13/15) | ≥ 50% | yes |
| DEEP-worse turns sent | 1 (rainbow) | ≤ 1 | yes |
| should-stay-FAST turn-runs sent | 6 | 0 | no |
| DEEP turns p95 | 22.4 s | ≤ 18.0 s | no |
| 8B reload p95 | 4.2 s | ≤ 5.0 s | yes |
| other FAST turns p95 | 4.5 s vs 4.3 s | ≤ +1.0 s | yes |

**The blind labels cannot change the verdict.** If all 7 judge-only
follow-ups were confirmed, precision would be 34/61 = 56%. The other two
failures do not involve labels. Their export (`runtime/evals/pf2/blind.json`,
30 replies) is optional.

**Why each criterion failed.** Diagnosis only; the rule is not tuned on this.

- **Precision.** The gain is in confusion, not in deepening.
  - Group B: sent 18/18 times. 1 stably better (CDN), 1 stably worse (the
    rainbow, where the 27B was inaccurate), 4 with no difference.
  - Group A and E confusion: 3 stably better. 7 are better on the judge's
    `adapts_to_confusion` alone (below its agreement bar), and 2 have no
    difference.
- **Should-stay-FAST sent.** The rule trusts the response budget, and the
  budget called two plain value follow-ups explanations:
  - "No, I meant the largest city." was routed `web_search`, shape `state`,
    budget **explain**;
  - "How fast does sound travel, then?" was routed `knowledge_question`,
    shape `state`, budget **explain**.
  The same weak input missed the implicit "어… 너무 어렵다." in 2 of 3 runs:
  it was read as a correction with a value budget, and FAST answered as if
  she were having a hard day. "Hm?" was read as social ("I'm listening.")
  in all arms.
- **Latency.** The budget was derived from Stage B's confusion turns, where
  the 27B wrote short re-explanations. On v2's deepening turns it writes for
  10–12 s, plus the 8.0 s load. The rainbow turns also reloaded the 8B
  mid-turn for the HARD premise check (`_premise_corrected`), 4.2 s.
  Consecutive DEEP turns (thunder t1 then t2) loaded twice. Measured: DEEP
  p50 14.1 s, p95 22.4 s.

**What else it shows.**

- **SPLIT overstated the gain**, as the user suspected. Of the second turns,
  Stage B's method (FAST against SPLIT) calls 7 stably better; the ground
  truth (FAST against LATE) calls 5. Four are better only with the 27B's own
  history.
- **The routed system is better than FAST, but not stably.**
  - Acceptable turns: 37% against 28%; turns passing: 20% against 14%.
  - Most of the difference rests on judge-only properties.
  - The 27B writing every turn after FAST's history reaches 39%, and the
    DEEP arm 48%.
- **The largest bucket is "neither model good enough" (21 turns), mostly
  first explanations.** The defects that dominate it are the same for both
  models: `concrete_first` (FAST 117, the 27B 100) and `terms_explained`
  (102 and 84). That is the open explain-goal proposal, and no router
  fixes it.

**By the protocol, this rule is not adopted and not tuned.** A different
rule is a new protocol version, with a new held-out corpus.

## Stage C: explanation-quality diagnosis (2026-10-01)

`docs/STAGE_C_DIAGNOSIS.md`; the numbers come from `evals/stagec_diagnosis.py`.
Diagnosis only: nothing changed, and routing stopped at the v2 result.

**The 21 "neither model good enough" turns:**

| failure | FAST | the 27B writing | DEEP |
|---|---|---|---|
| concrete_first | 21 | 21 | 17 |
| terms_explained | 20 | 20 | 18 |
| answers_the_ask | 0 | 0 | 0 |
| unnecessary detail | 0 | 0 | 0 |

The content is right; the shape is wrong.

**One template in all 189 replies:** a general answer, then the mechanism,
then "For example…", then why it matters.
- The illustration is never first: last in 80 replies, absent in 54.
- The example is usually an instance of use, not a scene that carries the
  explanation.
- The 27B adds more jargon (it knows the technical names).
- About a quarter of FAST's terms_explained "no" verdicts look like judge
  errors.

**Generation or pipeline.**
- *After generation: not the cause.* The 27B's replies were never changed
  and fail identically. All 16 of FAST's changed replies already opened
  general in the draft.
- *Before generation: a large part of the cause.* Four layers each set order
  and length:
  - persona: "Lead with the answer. State the fact first", "a sentence or
    two", "no over-explaining";
  - voice requirements: "result before background", 6 sentences;
  - style contract: "Lead with the answer", 4 sentences;
  - the explain goal: one sentence asking for an example, with no position
    and no word on terms.

  About 30 of the prompt's 1,940 words concern explaining. The models
  resolve the conflict faithfully.

**The proposal (not built).** One offline experiment, 8B only, on a new
held-out set:
- A: base;
- B: one explanation contract replacing the four layers, no new call;
- C: B plus a per-question plan from one 8B call (+1.0–1.6 s on explanation
  turns only, no VRAM change);
- D: B plus a 27B-written plan, as a ceiling.

The rejection criteria are fixed in advance: the plan must beat the
contract on at least 20% of held-out turns, with no substance regression
and person-confirmed concrete_first.

**A side defect.** `append_recommendation` offered "I can pull up a
refrigerator" on an explanation of how fridges work.

## Stage C: the experiment, prepared, not frozen (2026-10-01)

`docs/STAGE_C_PROTOCOL.md` (C1.0), `evals/stagec.py`,
`brain/explain_contract.py`. Nothing has run.

**The held-out corpus.** `explain_heldout` is staged in
`evals/scenarios_next/`:
- 24 scenarios, 30 scored turns, 12 English and 12 Korean, 6 with a
  deepening follow-up;
- no ID, utterance or subject from v1, the contamination matrix, Stage B or
  v2 (tested).

**The arms.** All use the 8B and production's post-processing.
- **A:** production.
- **B:** one contract in place of the five conflicting order and length
  rules. The prompt is +2 words; the length is the 4 sentences her_voice
  already enforces.
- **C:** B plus a five-field 8B plan.
- **D:** B plus the same plan written by the 27B beforehand.

The switch is inert unless `ELAINA_EXPLAIN_ARM` is set.

**Format check.** Three v1 questions through the 8B planner: all usable,
1.0–1.4 s.

**Criteria.** The user's, unchanged, plus a blind concrete_first sample
(20 judge-credited replies and 8 distractors, 70% bar). If B suffices, C is
not added.

**Corpus v2's freeze no longer verifies** (the product tree changed). Its
report stands, and the frozen tree is archived at
`runtime/evals/pf2/frozen_tree.tar.gz`.

## Stage C: the result (2026-10-01; final with the user's blind labels)

`docs/STAGE_C_RESULTS.md`, generated by the frozen code.
- **The run:** 12 runs, complete, all turns judged, the freeze verified.
- **The 28 blind labels were made by Claude** at the user's request, not by
  a person.

**The mechanical outcome is B:** one contract produces the improvement, and
the plan does not add value under the frozen rules.

| comparison (30 held-out turns) | better | worse | net | frozen outcome |
|---|---|---|---|---|
| A → B | 12 | 2 | +33% | passes: 1 substance and 1 leads-with-answer regression, both within the limits |
| B → C | 15 | 4 | +37% | rejected: 12 leads-with-answer regressions (limit 2) |
| B → D | 16 | 2 | +47% | rejected: 11 leads-with-answer regressions |
| C → D | 9 | 9 | 0 | the 8B planner is not the limit |

- **Blind confirmation:** 70% (14 of 20), exactly the bar.
  - Claude's four borderline calls move it between 60% and 80%.
  - Five of the six unconfirmed credits are B's: the judge credits "general
    claim, then 'For example'" as concrete-first.
  - If the check fails, the frozen rule gives "neither", although B's gain
    without judge-only evidence is still +30%.
- **Cost and reliability:** C adds +1.03 s; 0 unusable plans in C and D.
- **Failure categories, held-out** (A / B / C / D):

| failure | A | B | C | D |
|---|---|---|---|---|
| concrete-first | 28 | 22 | 12 | 9 |
| terms-explained | 24 | 19 | 11 | 13 |
| leads-with-answer | 1 | 1 | 12 | 11 |

  B's gain also includes accuracy: 17 accuracy defects in A against 7 in B.

**What it shows.**
- **A plan makes the reply scene-first, at the cost of answer-first.** The
  plan's separate "situation" and "answer" lines turn into a scene sentence
  followed by an answer sentence.
- **The contract alone keeps the answer first.** It moves concrete-first
  only modestly; the judge over-credits it.
- **The rubric pulls two ways.** For a "why" question, both properties can be
  met only when the first sentence carries the scene *and* the cause.

**The user's own labels (21:01) replace Claude's.**
- Confirmation is **90%** (18/20), and the outcome stays **B**.
- It holds without the six items Claude's message had named (14 of 14).
- The user's reading is more lenient than the written definition. All eight
  disagreements with Claude are user yes; seven of those replies open with a
  general statement or definition and give the example second, which is
  the very pattern the diagnosis counted as the failure.
- **Open question for the user:** which reading of concrete_first is the
  target?

Nothing has been implemented in production.

## The revised explanation contract: check passed (2026-10-01)

`docs/EXPLANATION_CHECK.md` holds the criteria, fixed before running;
`docs/EXPLANATION_CHECK_RESULTS.md` the result.

**The revision.** After the user's correction (the goal is natural,
understandable explanations; an example is a tool, not a required opening),
the contract was revised to `d398489d1bd7`: a direct answer first, and an
example only when it helps. Stage C's text is kept as a comparison arm.

**The arms.** A (current), B (Stage C's contract) and R (the revision), 3
runs each.
- On 18 new questions, each marked in advance "direct" or "example".
- R also ran on Stage C's held-out set.

**Outcome: R passes every criterion.**

| | R against A |
|---|---|
| answer-first regressions | 0 and 0 |
| answer-first rate | 96% against 94%; 98% against 98% |
| quality net (stably better − worse) | +3 and +3 |
| substance regressions | 0 and 1 |
| forced example openings | 0% |

- **R stops current production's habit of appending an example to every
  reply.** On direct questions, current production adds "For example…" to
  78% of replies; R to 44%.
- **B's quality gain over A is larger** (+9 on the held-out set) because it
  explains terms more often. B pays for it with forced scene openings (22%
  on the example questions) and 3 answer-first regressions.
- **R keeps naturalness but recovers only part of the terms gain.**
  Terms-explained failures on the held-out set: A 71, B 55, R 63 of 90.

**Status.** The production flag stays off. Enabling `split` is the user's
decision. The understanding-loop direction now leads, and this contract is
the default first-explanation behaviour inside it.

## Simulated learners, pilot 2: odd endings and check questions (2026-10-02)

Details are in `docs/SIMULATED_LEARNER_EVAL.md`. Rating pilot 2, the user reported odd sentences
at the end of replies, and "is this correct?" questions not answered with yes or no. Compared
with the model's own drafts in the traces, 14 of 80 replies ended on something the model did not
say.

**Fixed, each with a regression test:**
- **`premise_correction`** (7 of the 14). It is no longer judged on a follow-up inside an
  explanation. The judge sees one utterance, and there the person's "…잖아" points at what was
  just said. 3 of its 7 replacements were wrong. The skip is noted in the trace, and first
  questions are still checked.
- **`grounded_entities`** (2). A name whose initials spell a term the person said is that term's
  expansion ("Application Programming Interface" for "API"), not an unchecked business.
- **`preferences.read`**. "I get that it's not the shadow …" and "I always get confused by this
  part" were saved as favourites, and the question went unanswered. A favourite must now be a
  name.

**Added: a verdict first on check questions.** One rule, `response_budget.CHECKING_UNDERSTANDING`,
appears in every explanation goal and in the revised contract (rule 5, `cc22900f17a9`).

**Measured, not changed yet** (pilot 3 first):
- `repetition_retry`'s garbled retry;
- a `her_voice` re-say that invented "지금까지 그런 생각이셨다면 …";
- the `grounded_values` line on a conceptual follow-up routed to web search;
- the model's own service closers.

**Pilot 3** (the same day, simulator v2) found three more stages saying things the model didn't
write. All are fixed, with tests:
- **`length_rewrite`** was shown the router's reading instead of the person's words. "What's a
  p-value?" was normalized to "Sap", and the reply was about tree sap.
- **`grounded_entities`** only counted the current message as the person's. "API", said in their
  first message, was retracted on a follow-up.
- **A quoted Korean question** ("오늘 날씨 어때?") was read as a title.

Verdict-first held on about 4 of 10 check questions. The rule reached the prompt every time, and
the 8B drops it when a check and a new question come together. That is a case for the per-turn
move (step 3), not for more prompt text.

## Still open

- **Production FAST/DEEP routing**: not enabled. The order agreed:
  1. corpus v2, reviewed and frozen (done, 2026-09-30);
  2. the evaluation in `docs/FAST_DEEP_V2_PROTOCOL.md`: run 2026-10-01,
     **NOT ACCEPTED** (precision 21%, two should-stay-FAST turns sent, DEEP
     p95 22.4 s);
  3. real routing: not on this rule. Whether to try another one, under a new
     protocol version, is the user's decision.
- **New realisation findings** (not fixed):
  - `existence_claims` fires on "isn't real" in an explanation;
  - `length_rewrite` drops a worked derivation's notation.
- **The value-goal candidate**: kept provisionally; the user decides.
- **Blind labels**: done and scored (above). Still recommended as spot
  checks: `hash_table_how`, and FAST's explanation turns on
  `concrete_first`.
- **Stage B**: DEEP under the FAST router is done, and the switch costs were
  measured inside it. The supplementary suite for reasoning, planning,
  coding and synthesis remains.
- **Proposal still open**: the explain goal's ordering and terms. The user
  asked not to tune it now; it is recorded as a model and prompt-quality
  limitation. The other four proposals were taken up above.
- **`docs/RESPONSE_PIPELINE.md`**: to regenerate now that the baseline is
  frozen.
