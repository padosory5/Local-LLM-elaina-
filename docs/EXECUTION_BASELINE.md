# Execution, observation and verification baseline

**A successful tool call is not a successful goal.** This is the record of
how the pipeline now says so.

```bash
.venv/Scripts/python.exe scripts/execution_report.py       # the full record
.venv/Scripts/python.exe scripts/execution_report.py --kind cancelled
```

The scenarios live in `tests/execution_matrix.json` and are asserted by
`tests/test_execution_matrix.py` on **every run of the suite** — they are not
a live check that can quietly stop being run.

| Date | Scenarios | Reached expected outcome | Unverified successes |
|---|---|---|---|
| 2026-09-02 | 22 | **22/22** | 1, reported as such |

| Outcome | Scenarios |
|---|---|
| `success` | 9 (8 verified, 1 executed-but-unverified) |
| `retryable_failure` | 5 |
| `terminal_failure` | 4 |
| `needs_user_input` | 2 |
| `cancelled` | 2 |

---

## What the pipeline already did

Worth stating plainly, because most of this phase was **surfacing** existing
behaviour rather than building new behaviour:

- **Bounded retries already existed** — `_MAX_CONSECUTIVE_FAILURES = 2`,
  `_MAX_CONSECUTIVE_REPEATS = 2`, `_MAX_STEPS_DEFAULT = 8`,
  `_MAX_DISCOVER_MODE_BROWSER_STEPS = 1`, plus `_already_completed` and
  `_trailing_repeat_count` guards. No infinite loop was reachable.
- **The sub-planners already verified.** `BrowserActionResult.verified` is a
  tri-state, and `verified is False` already forced a `failed` status with a
  `verification_failed` code — a click that did not change the page was
  never reported as success.
- **The failure vocabulary was already honest**, including a whole family
  meaning *the action ran and the expected state never appeared*:
  `playback_unverified`, `shuffle_not_observed`, `collection_not_observed`,
  `unchanged_state`, `verification_failed`, `goal_operation_incomplete`.
- **Cancellation already terminated cleanly** via `user_took_over`.

## What was actually missing

### 1. Nothing classified any of it

Those codes were folded into one generic `"failed"` beside
`spotify_not_found` and `source_scope_violation`, so a caller could not tell
*it ran but did not work* from *it could not run* from *it must not run*.

`brain/task_outcome.py` now maps every emitted code to one of the five
states, and `TaskRunResult.outcome()` reads it off the run. The classification
is a lookup, not a heuristic, and `tests/test_task_outcome.py` scans the
source for `failure_code=` literals and **fails if any code is unclassified**
— so the table cannot fall behind the planners.

### 2. Every failure got the same retry budget

`_advance` retried anything that was not `user_took_over`, twice. A source
scope violation, a missing application and a transient stall were treated
identically.

Now: `CANCELLED` and `NEEDS_USER_INPUT` return immediately, `TERMINAL_FAILURE`
stops without spending an attempt, and only `RETRYABLE_FAILURE` reaches the
existing budget.

The distinction that matters most here is between a first miss and a repeat:
`direct_target_not_found` is **retryable** (a different site may work — the
planner already recovers this way), while `repeated_not_found` is **terminal**.
An existing test caught this when the first classification had both as
terminal; the test was right.

### 3. The verification signal was thrown away going up

`BrowserActionResult.verified` had nowhere to go: `ActionPlanResult` had no
such field, so only the `False` case survived (as a failure code) and *verified*
and *unchecked* successes became indistinguishable.

The tri-state now propagates through `ActionPlanResult.verified` and
`TaskStepResult.verified` to the outcome, which is what makes the
VERIFIED / EXECUTED_BUT_UNVERIFIED split real rather than aspirational.

---

## VERIFIED vs EXECUTED_BUT_UNVERIFIED

Reported separately and honestly, exactly as the phase requires.

| Surface | Evidence available | Reported as |
|---|---|---|
| `ui_control` | the foreground application, read back off the real UI tree after the run (`DesktopSurfaceContext.app_name`) | **VERIFIED** when a step lands on one |
| `browser_control` | `BrowserActionResult.verified` — set True by a post-action check, False when the check contradicts it | **VERIFIED** when True |
| `browser_control`, no post-action check | none | **EXECUTED_BUT_UNVERIFIED** |
| `web_search` | none — a snippet is evidence about the world, not about a state change | **EXECUTED_BUT_UNVERIFIED** |

`ex02_browser_unverified` is in the matrix precisely to hold this line: a
click that succeeds with nothing reading the page back is a success, and it
is **not** a verified one.

## What remains fundamentally unverifiable

Stated rather than hidden, as required.

1. **A click on a single-page app that exposes no state change.** The browser
   planner says so itself: "a successful Playwright click is an action we can
   truthfully report even when a SPA exposes no separate state change." The
   click is real; the consequence is not observable through the DOM. These
   are `EXECUTED_BUT_UNVERIFIED` and should stay that way — inventing a
   verification here would be the exact dishonesty this phase removes.
2. **Whether a web search's *answer* is true.** Provenance is tracked
   (`web_search_snippet` vs `browser_observed`) and confidence differs, but no
   observation confirms a fact the way a UI tree confirms a window.
3. **Anything after the process boundary** — whether audio actually reached
   the speakers, whether a submitted form was accepted server-side. The UI
   can say a button was pressed; it cannot say what happened next.

## Scripted tool results, deliberately

The scenarios script the tool result rather than driving a real machine. That
is a choice, not a shortcut: half of them cannot be produced reliably live —
"the click succeeds and playback never starts", "the model stalls three
times", "the user takes the mouse back between step two and step three". What
is under test is **the pipeline's response** to those results, and the
pipeline is real: the actual `TaskPlanner` loop, budgets, preconditions and
result adapters.

Live behaviour of the drivers themselves is covered separately by the
`browser` and `desktop` live-check tiers.

## Note on the harness

Two harness bugs of my own were found and fixed while writing this, both
worth recording because they would have made the numbers meaningless:

- `ui_control` has a real precondition (Desktop Control Mode). Leaving it off
  made every local-application scenario return `capability_unavailable`
  before running a single step — testing the guard, not the behaviour.
- A scripted desktop success carried no `surface_context`, so nothing had
  observed the application. That was reported honestly as
  `executed_but_unverified`; the fix was to model a real desktop result,
  not to loosen the assertion.
