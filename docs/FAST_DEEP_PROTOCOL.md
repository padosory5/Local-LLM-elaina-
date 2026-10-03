# FAST/DEEP evaluation protocol

*Approved 2026-09-29 with the amendments in §0.*

- **Frozen analysis:** fingerprint `cbd8c3b57913990a`, at 18:36 on
  2026-09-29 (`runtime/evals/pf/FROZEN.json`). It covers
  `evals/fastdeep_analysis.py`, the evaluator files it scores with, the
  corpus and the rubric.
- **Reproduction:** `runtime/evals/pf/reproduction.md`.
- **Run script:** `evals/fastdeep.py`.
- **§9:** its numbers come from the Phase 3 final rerun (pre-fix
  generation, corrected checks). They check the method; they are not
  results.

The candidates:

- **FAST** = qwen3:8b, soft stages on.
- **DEEP** = the 27B (`hf.co/jrell/Qwen3.8-27B-i1-IQ4_XS-GGUF-Smaller`),
  soft stages off.

Everything reuses what exists:

| part | what is used |
|---|---|
| corpus | `evals/corpus.py`, all six suites |
| arms | `evals/phase2.generate` |
| judge | `qwen3.6:35b-a3b` through `evals/judge.py`, with its verdict cache |
| rubric | v3, fingerprint `c4fac3a80008` |
| scoring | `evals/report.scored_turns`, with the corrected `evals/checks.py` |
| traces | `core/turn_trace.py` |
| blind human confirmation | `evals/calibration.py` |

No product behaviour changes for this evaluation. The corpus and the rubric
are not changed.

## 0. Approved amendments (2026-09-29)

The protocol was approved with the amendments below. Where they differ from
the text further down, the amendments win. The analysis implements them in
`evals/fastdeep_analysis.py`, and the rules are pinned by
`tests/test_fastdeep_analysis.py`.

1. **The primary comparison is end-to-end FAST vs DEEP capability.**
   - FAST is the 8B router and the 8B answer; DEEP is the 27B router and the
     27B answer. The two arms stay exactly as designed.
   - A DEEP win is not attributed to the 27B's generation alone. The router
     confound is documented: on the final rerun, the routers disagreed on
     intent in 11 of 28 later turns.
   - The report measures the confound again on the baseline.
   - A same-router comparison of generation alone is a possible later
     diagnostic, not part of this baseline.
2. **Stable labels over 3 runs.** Per turn, FAST run *i* is compared with
   DEEP run *i*.

   | label | rule |
   |---|---|
   | **Stable DEEP+** | DEEP removes a substance or explanation defect, and adds no substance defect, in at least 2 of 3 comparisons. |
   | **Stable DEEP-worse** | The mirror image, in at least 2 of 3 comparisons. |
   | **Unstable / no routing ground truth** | Everything else. It is never forced into a FAST or DEEP label. |

   - The tiers are §3.1's. "Correctness" in the amendment is read as §3.1's
     substance row: the correctness checks, `accurate`, and the checks that
     test reading the turn.
   - Form-only differences never create DEEP+.
   - A comparison resting only on `concrete_first` or `adapts_to_confusion`
     (below the agreement bar) counts toward DEEP+ only after a person's
     blind labels confirm it. Until then the turn is **pending DEEP+**.
   - One case the amendment leaves open: a comparison where DEEP trades one
     explanation defect for another, with no substance change either way,
     meets both conditions. It is counted as **mixed**, toward neither.
3. **Routing is reported on several metrics, none alone.** For each policy:
   - DEEP+ recall;
   - DEEP precision;
   - DEEP utilization;
   - the DEEP-worse routing rate;
   - combined end-to-end quality;
   - latency with and without model-switch cost.

   The bounds are all FAST, all DEEP and perfect-knowledge routing. The
   central question is how much of the stable DEEP+ opportunity a policy
   captures while avoiding needless DEEP use and preserving end-to-end
   quality.
4. **Categories are fixed corpus metadata.** A category is computed from the
   turn's rubric mode, its suite and its scenario, never from a router's or a
   model's reading of it. `category(scenario, index)` takes nothing else.
   Categories are hypothesis groups only.
5. **The analysis is frozen before the run.**
   1. It must reproduce the historical numbers from stored data (see the
      reproduction below).
   2. Then its fingerprint is recorded in `runtime/evals/pf/FROZEN.json`.
   3. The run script refuses to start unless the analysis still matches that
      fingerprint.
   4. The report states whether it was produced by the frozen analysis.
6. **The runs**:
   - FAST (8B, soft stages on) and DEEP (27B, soft stages off), both with
     speech recognition off, over all six suites including `followups`;
   - 3 runs each, alternating;
   - the drift arms and the recogniser-resident arms (§2).
7. **No FAST/DEEP product routing is implemented from these results.** The
   evidence comes first: stable DEEP+, stable DEEP-worse, unstable turns,
   policy performance, the bounds, end-to-end quality and costs.
8. **The corpus limitation stands.** Nothing is extrapolated to multi-step
   reasoning, planning, coding tasks or synthesis.
9. **Product code is frozen for the experiment.** There are no guards,
   patches or generation changes. A problem the analysis exposes is
   reported, not fixed during the run.

## 1. The two questions, kept apart

1. **Model capability.** Given the same turn, where does DEEP give a
   materially better answer than FAST? This is answered by comparing the two
   arms turn by turn (§3).
2. **Routing capability.** Can Elaina tell those turns apart before
   answering, and send only them to DEEP? This is answered offline, from the
   FAST arm's recorded router output, against the answer to question 1
   (§4).

A policy can only be as good as question 1's labels, so the labels must be
reliable before routing is scored. That is why §3 needs replicate runs.

The headline number is not "27B scores higher". It is:

> **DEEP+ recall.** Of the turns where DEEP materially improves the answer,
> the share the router sends to DEEP.

It is read together with the DEEP rate and the cost of each DEEP turn.

## 2. The baseline arms (Stage A)

| arm | settings (beyond defaults) | suites | runs |
|---|---|---|---|
| `pf-fast` | `ELAINA_STT=off`, `ELAINA_DOMAINS=act`; the router, planners and words are all qwen3:8b; soft stages on | all six (the five default suites and `followups`) | **N** (§11) |
| `pf-deep` | `ELAINA_STT=off`, `ELAINA_DOMAINS=act`, `ELAINA_MODEL` and `ELAINA_CONVERSATION_MODEL` both the 27B, `ELAINA_SOFT_STAGES=off` | all six | **N** |

These are the Phase 3 arms `p3-8b-soft` + `f3-8b` and `p3-27b-nosoft` +
`f3-27b` with the post-fix code. The two arms keep that shape so the
baseline stays comparable with Phase 3 and the final rerun (§7).

Latency-only arms run in the same session:

| arm | what it is for |
|---|---|
| `pd-8b`, `pd-27b` | The direct drift arms, the persona prompt only. They normalise speed across days and define the pressure threshold (§6). |
| `pv-8b`, `pv-27b` | Six scenarios with the speech recogniser resident, as in the app. These measure resource pressure. They are not quality arms (§6). |

Common conditions:

- TTS is off (the harness enforces it).
- The Elaina app is closed.
- Every model is unloaded before each run (`phase2.unload_models`).
- The GPU's tenants and memory are logged at the start and end of every arm.
- Ollama is 0.34.4 and `num_ctx` is 6144.
- The root is `runtime/evals/pf/`.

In the DEEP arm the 27B also routes. In a FAST/DEEP system the 8B would
route. §3.4 measures the size of that confound, and Stage B removes it
(§10).

## 3. Model capability: what counts as a material improvement

### 3.1 Defects, from the existing machinery

For each scored turn in each run, the **defect set** holds every check that
failed plus every rubric property the judge answered "no". Turns with an
incomplete verdict set, or a judge "error", are excluded and counted.

Defects are grouped by what they say about the answer. This is a grouping
of existing checks and properties; it adds no score.

| tier | checks | properties |
|---|---|---|
| **substance** | the correctness checks (`checks.CORRECTNESS`: clock, offset, hour difference, calendar, quantity, `must_match`, `must_match_any`); `must_contain_any`, `must_not_contain`, `language`, `answers_not_asks` | `answers_the_ask`, `takes_the_turn_as_meant`, `accurate`, `adapts_to_confusion` |
| **explanation** | none | `says_what_it_solves`, `concrete_first`, `terms_explained` |
| **form** | none | `only_what_helps`, `leads_with_the_answer`, `no_service_phrasing`, `notation_earned`, `no_unrelated_remark` |
| **pipeline** (not a model difference) | `not_searched`, `no_cards`, `speakable`, `display_math_intact` | none |

Why tiers are needed: §9 shows that a plain pass/fail flip counts "the 27B
did not add a sentence to '15% of 80'" as an improvement. Sending arithmetic
to DEEP for that would be wrong. The fix is FAST's value realisation, not
routing.

### 3.2 Stable defects across replicate runs

A defect is **present** for an arm on a turn when it appears in a majority
of that arm's runs (2 of 3, or both of 2). It is **absent** when it appears
in none. Otherwise it is **unstable**.

### 3.3 The turn label

Compare FAST's and DEEP's present defects on the same turn. Unstable
defects decide nothing.

| label | rule |
|---|---|
| **DEEP+** | DEEP removes at least one substance or explanation defect that FAST has, and adds no substance defect. |
| **DEEP−** | The mirror image. |
| **form-only** | The two sets differ only in form defects. These are FAST realisation targets, not routing targets. |
| **same** | The two sets are equal. |
| **uncertain** | The label would depend on an unstable defect. |

**Judge-dependent.** A DEEP+ or DEEP− that rests only on properties below
the calibration bar (`concrete_first` 75%, `adapts_to_confusion` 75% with
kappa 0.38, from the 2026-09-29 blind labels) is marked judge-dependent.
Those turns are exported for blind labelling:

- both arms' replies, shuffled, with the arm hidden;
- in `evals/calibration.py`'s existing format.

A judge-dependent DEEP+ enters the routing ground truth only if your labels
confirm it. This is the "not the judge alone" requirement.

### 3.4 Comparability classes, reported separately

- **First turns** (45): both arms see identical input. In the final rerun
  the two routers agreed on budget and shape for 39 of 45 and on intent for
  42 of 45. This is the clean comparison.
- **Later turns** (28): each arm follows its own earlier replies, and the
  routers disagreed on intent for 11 of 28. A DEEP+ here mixes three things:
  a better answer, a better earlier answer, and a different routing
  decision. Every later-turn DEEP+ is tagged with whether the two routers
  made the same decision.
- The same-history counterfactual is Stage B (§10): FAST's history, with
  DEEP answering one turn. It needs a mid-conversation model switch, which
  the backend does not have yet.

### 3.5 What question 1 reports

- Label counts overall, by comparability class and by category (§5).
- Every DEEP+ and DEEP− turn listed, with the defects it hinges on.
- The form-only turns, listed as FAST realisation findings.

## 4. Routing capability, separately from model quality

**Ground truth.** The §3 labels: DEEP+ (confirmed), DEEP−, form-only, same.
Uncertain turns are excluded, and their count is reported.

**Signal.** The FAST arm's recorded 8B route for each turn: `intent`,
`answer_shape`, `speech_act`, `detailed_response`, `is_follow_up`,
`confidence`, the tier-0 domain claim, and the budget and shape derived from
them. The 8B is the only practical router for FAST/DEEP, since the 27B's
first routing call takes about 7 s. No new classifier, prompt or model call
is added. A tier-0 claim (clock, arithmetic, conversion) is FAST by
construction.

**Policies, fixed before the baseline is seen:**

| policy | sends to DEEP |
|---|---|
| all-FAST, all-DEEP | nothing / everything (the bounds) |
| P1 | budget `explain` or `elaborate` |
| P2 | budget `elaborate` only (clarification or asked-for depth) |
| P3 | shape `explain`, `example` or `elaborate` |
| oracle | exactly the DEEP+ turns (the upper bound) |

**Metrics for each policy.**

- **DEEP+ recall.** DEEP+ turns sent to DEEP, divided by all DEEP+ turns.
  This is the primary metric.
- **Precision.** DEEP+ turns sent, divided by all turns sent.
- **DEEP rate.** Turns sent, divided by all turns. This drives latency and
  swapping.
- **Harm.** DEEP− turns sent to DEEP.
- **Waste.** Form-only and "same" turns sent to DEEP.
- **Composite outcome.** Take each turn's result from the arm the policy
  picks, and compute pass rate and property rates for the mixed system. This
  is exact for first turns and approximate for later turns (§3.4).
- **Composite latency.** The chosen arm's turn latency, plus the switch cost
  under the residency model in §6. Until Stage B measures the switch cost,
  it is reported as a function of it.

**Router reliability.**

- On first turns, the 8B router sees identical input in every run. The
  share of turns where a policy's decision changes between runs is the
  router's noise.
- The 8B and 27B routers' agreement is reported as a diagnostic.

**Attributing a composite failure.**

| cause | when |
|---|---|
| **routing miss** | sent to FAST, and the turn is DEEP+ |
| **DEEP failure** | sent to DEEP, and DEEP fails there |
| **capability limit** | both arms fail |

This keeps "the router chose wrong" apart from "the model was not good
enough".

## 5. Categories: hypotheses, not labels

Every scored turn is assigned one category from its rubric `mode` and its
suite. The assignment is fixed here, before the run. The categories say
where DEEP is *expected* to help. Only §3 measures whether it does.

| category | turns | assigned from |
|---|---|---|
| **D1** "I still don't understand" and conceptual follow-ups | 9 | mode `confused`: 6 in `followups` t1, `live_session` t2, `still_confused_derivative` t1, `ko_taylor_then_time` t1 |
| **D2** A genuinely different approach for someone who does not follow | 4 | mode `intuition`: `taylor_still_dont_understand`, `derivative_simply`, `overfitting_nontechnical`, `ko_derivative_simply` |
| **D4** Comparisons | 2 | mode `comparison`: `ram_vs_storage`, `ko_climate_weather` t0 |
| **D8** Ambiguous, interpretation-heavy | 2 | `which_one_means_the_nearest_subject` t2, `a_new_subject_closes_the_old_one` t1 |
| **C** First-time explanations (contested: DEEP helped in Phase 3, but these are not "hard") | 19 | modes `cause`, `how`, `purpose`, `example` on a first ask. 4 of them are technical-concept turns (`hash_table_how`, `cpu_cache_why`, `recursion_example`, `ko_hash_table_when`), the nearest the corpus has to coding and architecture. |
| **F1** Simple factual | 4 | `moon_distance`; symbolic maths facts `derivative_polynomial`, `definite_integral`, `ko_derivative` |
| **F2** Deterministic calculation and conversion | 4 | `fifteen_percent`, `celsius_to_fahrenheit`, `cup_ounces`, `arithmetic_in_a_social_thread` t3 |
| **F3** Time and date | 11 | the 8 time/date `relevance` turns and 3 trajectory time turns |
| **F4** Straightforward definitions and formulas | 9 | modes `definition` and `formula` |
| **F5** Simple follow-ups | 3 | `steep_time_after_coffee` t1, `a_finished_task_does_not_reopen` t2, `a_correction_outranks_what_was_held` t2 |
| **F6** Conversational remarks and receipts | 6 | modes `receipt` and `remark` |

The existing corpus has no turns for:

- D3, multi-step reasoning;
- D5, complex planning;
- D6, coding and architecture tasks (only concept explanations);
- D7, synthesis across several pieces of information;
- F7, routine actions.

Actions touch the machine; they are covered by the action test harness and
stay on FAST by construction. For D3, D5, D6 and D7 the baseline can say
nothing, and it will say so.

Stage B proposes a supplementary suite for them (§10). It would be written
only with the existing modes, so that the rubric fingerprint, and with it
every stored verdict, stays valid.

## 6. Resources and latency

**Reported per arm:**

- turn latency p50 and p95;
- the router's first call p50 and p95, fallback rate and truncations;
- answer generation speed in tok/s;
- model loads (count, and each `load_duration`);
- VRAM at the end of each scenario, and Ollama's GPU/CPU split, both already
  recorded in each result's `resources`;
- the GPU's tenants and free memory at the start and end of each arm.

**Pressure rule.**

- A model call is **pressure-affected** when its generation speed falls
  below 60% of the same model's median speed in that session's drift arm.
- Latency is reported with and without those calls.
- An arm where more than 5% of calls are affected is marked contaminated,
  and its latency is not used. It is rerun, or reported as a pressure
  condition.

**The recogniser.**

- Quality arms run with speech recognition off, as in Phase 2 and Phase 3,
  because it does not change the text.
- The `pv-*` arms measure it separately, and are classified by the pressure
  rule.
- The 87-second 27B result from the final rerun is a pressure condition, not
  the model's latency. It reproduced at both context sizes, at 11 tok/s
  against 41. It is recorded that way.

**Switching.**

- The two models cannot both stay resident at `num_ctx` 6144: 5.7 + 12.7 GB
  against 16.3 GB, before the recogniser.
- So under an 8B router, a DEEP turn implies loading the 27B, and the next
  FAST turn implies reloading the 8B.
- The traces put the cold loads at about 1.7 s (8B) and 7.4 s (27B), one
  observation each with nothing else loaded. That is about 9 s per DEEP
  turn before the turn itself.
- Alternating is not assumed to be free. Stage B measures it (§10). Until
  then, composite latency is given as a function of the swap cost.

## 7. Historical comparability

Every headline metric is reported in four columns, arm by arm:

| column | replies | checks | verdicts |
|---|---|---|---|
| Phase 3 as reported (2026-09-27) | Phase 3 | old | cached |
| Phase 3, rescored | Phase 3 | corrected | the same cache |
| Final rerun, rescored | final | corrected | the same cache |
| **Post-fix baseline** | new | corrected | new, judged now |

- Evaluator-only change is column 2 minus column 1: the same replies and
  the same verdicts, so only the checker moved.
- Generation change is column 4 minus column 3: the same checker, with new
  replies from the fixed code.
- Corrected-check numbers remain the basis for every historical comparison
  from here on.

## 8. Known behaviour to investigate (Step 4)

Each is read by hand from the post-fix replies. No phrase-specific guard
comes out of it. Each finding is classified as one of:

- model capability;
- routing;
- deterministic resolver or tool;
- response realisation;
- evaluator error.

A code change is proposed only for a systemic cause.

| question | how it is checked |
|---|---|
| `overfitting_nontechnical` failing `concrete_first` | The reply in every run of both arms, with the judge's reasons and the goal text the model received. Is it the model (definition-first despite the instruction), the instruction, or the judge (it accepts or rejects an analogy; `concrete_first` is at 75% agreement)? |
| `hash_table_how` | Pass/fail and defects in every run of both arms, against the final rerun. |
| Unwanted examples on direct formula and value questions | Every scored turn where the budget shape is `state` or `value`: `only_what_helps` verdicts, whether the explain goal reached the prompt (from the trace), and the replies read by hand. |
| Regressions from the fixes | Every scenario whose stable outcome is worse than in the rescored final rerun is read and classified. The four fixes each have a trace signature to look for: `final_check` and `not_her_last_answer` steps, planner calls, and `grounded_values` findings. |

## 9. The method, checked on existing data

On the final rerun (pre-fix code, corrected checks), with 2 runs of each
arm:

- **Replicate noise.** Two identical runs disagree on a turn's pass/fail on
  9 of 73 turns (FAST) and 15 of 73 (DEEP). They agree on the exact defect
  set only 48 and 49 times out of 73.
- **One run per arm is not enough to label turns.**
  - Run 1 alone gives 19 turns where DEEP flips fail to pass, and 6 the
    other way.
  - Requiring both runs to agree leaves 12 and 2.
  - So about 40% of single-run "DEEP is better" labels were noise.
- **Plain flips mislabel.** 5 of those 12 consistent flips are value turns
  where FAST failed only `only_what_helps`: ounces in a cup, 15% of 80,
  today's date, 2+2, eˣ.
- **The tiered labels.**

  | label | turns |
  |---|---|
  | DEEP+ | 15 |
  | form-only | 8 |
  | DEEP− | 4 |
  | same | 17 |
  | unstable | 29 |

  The DEEP+ turns are:
  - 6 of the 9 D1 turns;
  - 7 C turns: inflation, recursion, the Korean hash table, the cold-brew
    how-to, two Taylor-series applications, and why a derivative matters;
  - the Korean derivative (F1). The calculator-decline fix may since have
    closed that one;
  - a thanks (F6).
- **Routing, previewed with the 8B router.**
  - P1 (budget explain or elaborate) caught 13 of 15 DEEP+ at 52% precision,
    sending 25 of 44 labelled turns to DEEP, 3 of them DEEP−.
  - P2 caught 3 of 15 at 75% precision.
  - The existing signals are informative, not sufficient.

## 10. Stages

**Stage A: the post-fix baseline (this protocol).**

- The arms in §2.
- The reports in §3 to §8.
- It establishes the baseline, question 1's labels (version 1) and question
  2's policy scores (version 1).
- Estimated time:

  | runs per arm | generation | judging | drift and resource arms | total |
  |---|---|---|---|---|
  | 3 | about 2 h 20 min | about 35 min | about 15 min | about 3 h 10 min |
  | 2 | about 1 h 35 min | about 25 min | about 15 min | about 2 h 15 min |
  | 1 | about 50 min | about 12 min | about 15 min | about 1 h 15 min |

**Stage B: after Stage A is reviewed. Not part of this run.**

1. **A switch benchmark.** Five times each: 27B cold with the 8B resident,
   8B back after the 27B, with and without the recogniser. It records load
   times, time to first token and VRAM peaks, and feeds the composite
   latency.
2. **DEEP under the FAST router.** The 8B routes and the 27B answers,
   removing §3.4's router confound. The first turns of this are the "same
   turn" comparison question 1 asks for. Its latency includes swapping by
   construction, so it is read for quality only.
3. **A supplementary suite** for D3, D5, D6 and D7, about four scenarios
   each, written with existing modes and reviewed before it is run. It sits
   in `EXTRA_SUITES`, outside every historical comparison.
4. **The same-history counterfactual for later turns**, once FAST/DEEP
   switching exists: FAST answers every turn but the one routed to DEEP.

## 11. What must be decided or built before Stage A

*Resolved by the amendments: 3 runs per arm, `evals/fastdeep.py`, the
frozen `evals/fastdeep_analysis.py`, and `followups` in both arms. The
original text follows.*

All of these are on the evaluation side. None changes how Elaina behaves.

1. **Runs per arm.**
   - The brief says once. §9 shows one run cannot label turns: about 40% of
     single-run DEEP+ labels were noise.
   - Proposed: 3 per arm (majority labels), about 3 h 10 min.
   - The minimum is 2 (both runs must agree), about 2 h 15 min.
   - One run gives the baseline's aggregate numbers, which average out the
     noise, but not usable routing labels.
2. **An orchestrator, `evals/fastdeep.py`.** It is `evals/phase3.py`'s
   pattern with this protocol's arms and root, reusing
   `phase2.generate`/`judge`/`unload_models`, plus the GPU tenant log.
3. **The analysis for §3 to §7, written and frozen before the run.**
   - It includes the tiers, the labels, the policies and the category map
     of §5.
   - It is checked by reproducing §9's numbers on the final rerun's data.
   - Once the baseline has been seen, changing any of it is a new protocol
     version, not an edit.
4. **The `followups` suite is included in both arms.** It holds 6 of the 9
   D1 turns.

Nothing else is needed: no product changes, no corpus changes, no rubric
changes. `docs/RESPONSE_PIPELINE.md` is left as it is until the baseline is
frozen.

## 12. Stage B diagnostic: DEEP under the FAST router

*Written 2026-09-29, before its runs.*

**Why.**
- In the baseline, 9 of the 12 follow-up DEEP+ turns that routing rule R2
  catches are turns the two routers read differently. On confusion
  follow-ups, the 27B called the turn a clarification, which also gave the
  answer the re-explanation instruction; the 8B called it conversation.
- A FAST/DEEP system routes with the 8B. So the question is what the 27B's
  answer is worth under the 8B's reading.

**Arms.** Run by `evals/fastdeep_stageb.py`:
- one session, on the current code (after the hard fixes);
- 3 runs each, alternating FAST, SPLIT, DEEP;
- speech recognition and TTS off;
- the frozen analysis required.

| arm | reads the turn | answers | soft stages |
|---|---|---|---|
| `pb-fast` (FAST) | 8B | 8B | on |
| `pb-split` (SPLIT) | 8B | 27B | off |
| `pb-deep` (DEEP) | 27B | 27B | off |

**Scenarios**: the 14 with follow-up turns.
- the six `followups` scenarios;
- `live_session_2026_09_23`, `still_confused_derivative`,
  `ko_taylor_then_time`, `explanation_thanks_time`;
- `fresh_question_after_time`, `a_correction_outranks_what_was_held`,
  `thanks_closes_rather_than_continues`, `steep_time_after_coffee`.

**Comparisons.** Each uses the frozen `compare_turn` and the 2-of-3 rule,
run *i* against run *i*. The later turns are the primary result; first
turns are reported apart.

| comparison | what it measures |
|---|---|
| FAST → SPLIT | what sending the turn to DEEP buys under the 8B's reading: the routing question |
| SPLIT → DEEP | what the 27B's reading adds, with the answer model and soft stages held: the interpretation effect |
| FAST → DEEP | the baseline comparison repeated in this session: its stability across sessions |

**The answer it gives.**
- Take the baseline's follow-up turns that were stable DEEP+, and count
  how many stay stably better in FAST → SPLIT.
- If most do, the gain is generation, and R2-style routing is worth a
  held-out check.
- If few do, and SPLIT → DEEP carries the gain, the follow-up opportunity
  is mostly interpretation. The first lever is then the 8B's reading of
  confusion follow-ups, not the model.

This diagnostic does not choose a policy.

**Known limit.** SPLIT's and DEEP's histories are the 27B's own earlier
answers. A system that routes only the follow-up would have FAST's history
before it, and measuring that needs a model switch inside a conversation.

**Latency.** SPLIT swaps the 8B and the 27B on every turn, so its load
times are real swap costs: the worst case a routed system could see.
