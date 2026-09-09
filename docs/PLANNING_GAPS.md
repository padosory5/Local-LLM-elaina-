# A5 — Planning gaps

**What the phase was for:** a multi-step task should be retryable,
cancellable, and reportable halfway.

**Instruments,** both written before the fixes and run before and after:

```bash
.venv/Scripts/python.exe scripts/task_report_check.py     # what the person hears
.venv/Scripts/python.exe scripts/cancellation_check.py    # does "stop" stop it
```

Enforcement is [`brain/task_progress.py`](../brain/task_progress.py) and the
sixth outcome in [`brain/task_outcome.py`](../brain/task_outcome.py) — the same
modules the reports read, per Rule 1.

| | Before | After |
|---|---|---|
| Scenarios reported honestly | **17 / 22** | **25 / 25** |
| Non-success runs told the person "Done." | **4** | **0** |
| Cancellations that say what they'd already done | **0 / 10** | **10 / 10** |
| Named terminal states | 5 | **6** (`partial`) |
| Execution matrix | 22 green | **25 green** |
| Tests | 3086 | **3118** |

---

## Three of the phase's four bullets were already done

Written down plainly, because the plan's own rule is to read the code before
writing the phase, and this is what reading it said:

- **Retry strategy** — *"a step that fails transiently is not distinguished from
  one that fails permanently."* It is. `task_outcome.py` separates `_RETRYABLE`
  from `_TERMINAL`, terminal codes short-circuit the loop instead of spending
  the budget, and its docstring describes the fix in the past tense.
- **Planning vs execution as separate phases** — `TaskPlanner._preview()` is one
  upfront call that settles required capabilities, verification level and a
  stated intent before the first step dispatches.
- **Named terminal states with no unbounded waits** — five states, a code table
  with a drift test, and every loop in the path bounded: the planner by
  `max_steps` and `_MAX_CONSECUTIVE_FAILURES`, `_wait_for_port` by a deadline,
  the browser service by a stop sentinel, and the three `while True` text
  cleaners by strict shrinkage.

What was left was the fourth bullet, partial completion — and, underneath it,
something the bullets did not predict.

---

## The five terminal states were never spoken

`TaskRunResult.outcome()` reads the run's own status and last step into one of
five named outcomes. It has a code table behind it, a drift test that fails when
a planner emits a code the table has not classified, and twenty-two scenarios
exercising it. All green.

**Nothing outside the tests ever called it.**

`ChatEngine._handle_task_action` returned `task_result.summary` — whatever the
model wrote in its final planning decision:

```
goal   : Play my liked songs.
step   : ui_control -> failed, "Pressed play; nothing started."
         failure_code=playback_unverified
plan   : {"done": true, "summary": "Done."}
outcome: retryable_failure          <- correct, and read by no one
heard  : "Done."                    <- what the person is told
```

Four of the twenty-two scenarios did this. The honest sentence existed the whole
time; it was on the step, one field away. `task_outcome.py`'s own docstring names
this exact failure — *"a caller could not tell 'it ran but did not work' from 'it
could not run'"* — and it was still true at the only place it mattered, because
the module that fixed it was wired to nothing.

The scenario notes even said so. `ex06`'s reads: *"the click landed and playback
never started — must never report SUCCESS"*. It never did report success. It said
"Done."

So `brain/task_progress.py` is the wiring, and it carries the rule that makes the
wiring worth having:

> **On a run that did not succeed, the model's summary is not evidence. The steps
> are.**

A successful run still keeps the model's summary — it is the answer, and the
answer is what the task was for.

---

## Stopping is not disappearing

A run that opened Spotify and was then cancelled said:

> You took control, so I stopped.

True, and it leaves the person not knowing whether anything happened to their
machine. What got done was sitting in `task_state.completed_steps`. Now:

> You took control, so I stopped. This much is done — Spotify is open.

And when nothing had happened yet, it says that instead of trailing off:

> You took control, so I stopped. Nothing changed.

Step summaries pass through A1's `conversation_style.speakable_fragment()` before
they are spoken. They are written for a log, and one already went out as speech
once — *"Completed: Window: ChatGPT Button: 최소화 [id=6e663719-e0]"*. Same
boundary, same kind of string.

### The checkpoint nothing was testing

A cancellation can reach the planner at four moments. The execution matrix had
two cancellation scenarios and both used the same one — the tool itself returns
`user_took_over`, by which point the planner is already looking at a failed step.
The three inside `_advance` had no coverage, and the third of them is the
interesting one: **a retry is a new action, so a stop during a failure has to
stop the recovery too.** Every existing case cancels a run in which nothing has
failed, so the recovery path was never what got interrupted.

`tests/test_cancellation.py` cancels on the *n*th time the planner asks, rather
than naming the checkpoints — so a checkpoint added later is walked too — and it
does it on two plans, one clean and one recovering. Ten scenarios, ten moments,
10/10.

---

## `partial`, the sixth state

"I got two of the four done" was previously either a failure, which throws away
the two, or — when the model wrote the summary — a success.

`PARTIAL` is deliberately **not reachable from `classify()`**. A status and a
failure code cannot tell you whether anything was accomplished; only the run's
own completed steps can. So `classify()` stays a table and
`TaskRunResult.outcome()`, which has the steps, calls `with_progress()`.

Cancellation and needs-user-input keep their own outcome even with progress
behind them: *why* it ended is the more useful half, and `PARTIAL` would erase
it. They still report what got done — that is `task_progress`'s job, not the
state's.

---

## Two instrument corrections

Both are recorded in the scripts themselves, because changing an instrument
after seeing its results is the move that most needs to be visible.

1. **`task_report_check.py` condemned the best sentence in the run.** Its
   success-claim check searched the whole sentence for "done", and caught it
   inside the progress frame's own wording — *"You took control, so I stopped.
   This much is done — Spotify is open."* That is exactly what a
   cancelled-with-progress run should say. The progress clause is now removed
   before the question is asked. Without the correction the run reads 24/25 and
   the one failure is correct behaviour.

2. **`cancellation_check.py` read 8/10 on two non-cancellations.** It cancelled
   on asks 1–8, but by ask 7 all three steps had already run and the plan had
   reached `{"done": true}` — the stop arrived after the work was finished, where
   reporting success is right. Its `bounded` check was also looking for a step's
   summary *in the sentence*, which conflates "the step ran" with "the report
   mentioned it". It now compares the executor's own call count at the moment the
   predicate first fired against the count at the end, which is the property that
   was meant, and scenarios that arrive after completion are reported as such
   rather than counted as failures.

---

## Exit criteria

| Criterion | Result |
|---|---|
| Every failure ends in one of the named terminal states, 0 unbounded waits | ✅ six states; every loop bounded |
| Cancellation stops the plan and reports honestly, 10/10 | ✅ **10/10** |
| Partial completion is reported as partial, never as success or failure | ✅ `partial`, with a negative case |
| Execution matrix green | ✅ **25/25** |

No regression elsewhere: capability contracts still 11/11 with zero internals,
execution matrix 25/25, full suite 3118 green.

### What the live arcs actually showed, including the run I would rather not quote

Two runs of the same three English arcs, on identical code, minutes apart:

| Run | Clean | Findings |
|---|---|---|
| first | **28/32 (88%)** | service_phrasing ×2, self_repetition, too_verbose |
| second | **31/32 (97%)** | too_verbose |

The first is reported because it happened. Every finding in it was in a
conversational turn A5 does not touch — a greeting ("How can I assist you
today?"), a social reply, a three-sentence receipt — and the task turns were
clean in both. But the useful conclusion is about the instrument, not the run:

> **The live conversation-quality metric varies by about three turns between
> runs of identical code.** A single-run difference of ±3 is not evidence of
> anything.

That retroactively applies to numbers already recorded. A4's "30/32 → 31/32" was
within this band and was reported as "held, possibly improved" — which was the
right call, and this is the measurement that justifies it. Anything claimed from
these arcs needs repeated runs or a margin wider than three turns.

---

## Declared limit: the frames are bilingual, the step summaries are not

Rule 4, answered honestly rather than claimed. `task_progress` declares
`LANGUAGES = ("en", "ko")` and every frame it speaks exists in both:

> 직접 조작하셔서 멈췄습니다. 여기까지는 되어 있습니다 — Spotify is open.

The trailing clause is English because the **sub-planners** write their step
summaries in English, and this module quotes them rather than inventing a
paraphrase. Dropping them would produce a fully Korean sentence that says less —
and what the person needs from that sentence is which app is now open.

Translating sub-planner summaries is a change across `desktop_action_planner`,
`browser_action_planner` and `web_search_planner`, and it is the same work A6
will need for attribute text. It belongs there, once, not here twice.

---

## Carried forward

- **Sub-planner summaries are English-only** → above.
- **`_preview()` is not shown before it runs.** The plan's fourth bullet wanted a
  plan that "can be shown before it runs, which C and D will both want".
  `plan_preview` exists and is prefixed to the reply; it is not offered as an
  approvable artifact. That is a UI question as much as a planner one, and it is
  better answered when C has a surface to show it on.
- **A live context fault, unrelated to this phase.** In the tooling arc,
  "actually back to the mouse thing" was answered about *windows* — the previous
  subject — with "Check the open windows via the taskbar or Alt + Tab. No need to
  ask for further help." An explicit return-to-subject is A3's territory and its
  contamination matrix is 12/12, so the matrix is missing this shape: a return to
  a subject two turns back, across an intervening task. Worth a case there.
- **`Failure.retryable` from A4 is declared and unread.** The contract layer
  states per failure code whether retrying could plausibly work; the planner
  still decides from `task_outcome`'s own `_RETRYABLE` set. Two tables answering
  one question is the drift A4 spent its time removing, and they should be merged
  — but merging them changes retry behaviour, which needs its own measurement
  rather than a tidy-up at the end of a phase.
