# Evaluation

**What this is.** The measurement that replaces adding a rule. When she answers
badly, the turn becomes a scenario here before it becomes code. A soft stage
of the reply pipeline ([RESPONSE_PIPELINE.md](RESPONSE_PIPELINE.md)) can be
retired once its failures are scenarios, the scenarios pass without it, its
turn records show it no longer changes anything useful, and nothing else
regresses.

**What it measures.** How she answers, as the person meets it: a live model,
the real backend driven over its WebSocket the way the desktop window drives
it, each scenario on a fresh backend and runtime, speech silenced.

---

## The four kinds of test

| Kind | Where | Needs a model | What it answers |
|---|---|---|---|
| Deterministic unit tests | `tests/` | no | the code does what it says |
| Behavioural evaluation | `evals/scenarios/explanation.json`, `relevance.json`, `modality.json` | yes | one question, one reply: did it serve the question |
| Trajectory evaluation | `evals/scenarios/trajectories.json`, `tests/contamination_matrix.json` | yes | a conversation, scored turn by turn |
| Display and speech | `evals/modality_pairs.json`; the live `modality` suite | pairs: no | notation on screen, words in the voice, same meaning |

## The corpus

| Suite | Scenarios | About |
|---|---|---|
| explanation | 20 (16 English, 4 Korean; 9 domains) | a subject paired with a goal: an example, a reason, an intuition, a definition, a formula, a process, a comparison |
| relevance | 12 | ask for one thing, get that thing: the time without the zone and offset, the value without trivia |
| modality | 6 | answers that need notation: is it intact on screen, is the speech speakable |
| trajectories | 4 | the 2026-09-23 session word for word; "I still don't get it"; the same in Korean; thanks-then-time |
| contamination | 12 | read from `tests/contamination_matrix.json`, not copied |
| display/speech pairs | 24 | display text and the words the voice should say for it (no model) |

A scenario's `mode` records the communicative goal for people reading the
file. Elaina is never shown it, and nothing branches on it.

## How a turn is scored

**Correctness is checked, not judged**, wherever the answer is known
(`evals/checks.py`):

- clock times in each named zone, the gap between zones, any UTC offset the
  reply states, today's date, weekday and year, all computed from the turn's
  own recorded start time;
- quantities in named units, within a tolerance;
- arithmetic and formula answers.

**Mechanical facts** are read from the turn record:

- whether a web search ran, and whether result cards were shown;
- the reply's language;
- whether anything unspeakable reached the voice;
- whether the draft's notation reached the screen intact.

**Behaviour is judged** (`evals/rubric.json`, version 2). Every scored turn
has a `mode`, its communicative goal: fact, definition, formula, example,
cause, purpose, how, intuition, confused, comparison, receipt or remark. The
properties judged are the ones whose `applies_to` includes that mode. A
property that does not apply is not scored, rather than forced to a yes or
no. Only `notation_earned` may be answered n/a, when the reply has no
notation.

- Each property is defined once, with an invented yes and no example, and
  none names a topic.
- The judge (`evals/judge.py`) is the local 27B, never the qwen3:8b being
  judged. It sees the conversation and the displayed reply, nothing about how
  it was produced.
- Every verdict carries the rubric's fingerprint, and a report counts only
  verdicts given under the current wording.

Why the rubric has this shape, and the calibration that forced it:
[EVAL_RUBRIC.md](EVAL_RUBRIC.md).

A scored turn **passes** when every check passes and every applicable
judged property is *yes*; n/a counts as neither. A scenario passes when all
its scored turns do. Reports re-score every run against the current corpus.

**Trusting the judge is measured, not assumed.** `evals.calibration export`
draws a quota of labels per property from judged runs: replies the judge
accepted and replies it rejected, without the verdicts in the file.
`evals.calibration score` reports agreement and kappa per property. A
property below its bar does not decide anything until its definition is
fixed.

## Running it

Close Elaina first; the run needs port 8765. It refuses to start otherwise,
and refuses to drive a backend whose log does not confirm silent speech.

```bash
.venv/Scripts/python.exe -m evals.run --out runtime/evals/<name>            # every suite, ~25 min
.venv/Scripts/python.exe -m evals.run --out runtime/evals/<name> --suites relevance
.venv/Scripts/python.exe -m evals.run --out runtime/evals/<name> --only time_now,taylor_example_when_used
.venv/Scripts/python.exe -m evals.judge --run runtime/evals/<name>
.venv/Scripts/python.exe -m evals.calibration export --runs runtime/evals/<a> runtime/evals/<b> --out runtime/evals/<file>.json
.venv/Scripts/python.exe -m evals.calibration score --file runtime/evals/<file>.json
.venv/Scripts/python.exe -m evals.report runtime/evals/<name> ...           # several runs: mean and range
.venv/Scripts/python.exe -m evals.modality_pairs                             # display/speech, no model
.venv/Scripts/python.exe -m evals.integrity runtime/evals/<name> ...        # what the pipeline did that it never should
.venv/Scripts/python.exe -m evals.run --out runtime/evals/<name> --suites followups   # Phase 3 R9, named explicitly
.venv/Scripts/python.exe -m evals.phase3                                      # the Phase 3 rerun matrix
.venv/Scripts/python.exe -m evals.compare3 --out docs/PHASE3_RESULTS.md        # Phase 3 against Phase 2
```

- **Integrity** (`evals/integrity.py`) is read from the turn records of every
  turn, scored or not: clock turns sent to a web search, values a grounding
  stage deleted although the turn's evidence held them, display text changed
  by speech shaping, and notation broken by a deterministic stage. Phase 2's
  counts are the baseline in `docs/PHASE2_RESULTS.md`.
- **Suites added after Phase 2** (`corpus.EXTRA_SUITES`, today `followups`)
  load only when named, so a run over every suite is still the Phase 2
  corpus and later phases compare like with like.

- An interrupted run resumes where it stopped.
- Each run keeps its replies, check results, turn-record summaries, the full
  turn records and the backend logs in `runtime/evals/<name>/`, which is
  git-ignored.
- One live run of this model moves by several turns on its own, so compare
  three runs to three runs, never one to one.

## When something goes wrong

```bash
.venv/Scripts/python.exe scripts/turn_trace_report.py find "what they said"
.venv/Scripts/python.exe -m evals.from_failure <turn id>
```

`from_failure` prints a scenario already filled in with the conversation up
to that turn, the draft, and which stages changed it. What it leaves blank is
the judgment: what was wrong, what a good reply would have done, and which
rubric properties and checks say so. A person writes that. It goes into
`evals/scenarios/`, and only then does anyone change code.

The baseline these are compared against is [EVAL_BASELINE.md](EVAL_BASELINE.md).
