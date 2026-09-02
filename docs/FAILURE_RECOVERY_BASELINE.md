# Failure, recovery and cancellation baseline

Three questions, asked of every layer that can fail:

1. does it end, or can it wait forever?
2. does it end as one of the five named outcomes, or as a traceback?
3. is the assistant still usable afterwards?

```bash
.venv/Scripts/python.exe -m unittest tests.test_failure_recovery
```

| Date | Scenarios | Result |
|---|---|---|
| 2026-09-02 | 26 | **26/26** |

All deterministic, all in the ordinary suite — not a live check that can
quietly stop being run.

---

## The three real defects found

### 1. `TaskPlanner` had no notion of cancellation at all — *cancellation*

`grep -c cancel brain/task_planner.py` returned **0**.

`cancel_active_turn()` stopped generation and speech, and a multi-step task
carried on dispatching browser and UI actions to the very end. "Never mind"
mid-task left the remaining steps running — the plan finished around the
person who had cancelled it.

The engine's existing cancel token is now passed to the planner and asked
three times, which are the three moments a new action can begin:

- **before planning** the next step, so a cancellation between two steps is
  not discovered only after the next one has already run;
- **before dispatching** it, because planning a step is a model call and the
  person may have cancelled while it ran;
- **before a retry**, because a retry is a new action — cancelling during a
  failure must stop the recovery too.

Measured: a three-step plan cancelled at the first check dispatches **zero**
browser calls and reports `CANCELLED`, not a bare `stopped` (which is
indistinguishable from giving up).

### 2. An unbounded wait in browser-service startup — *timeout/hang*

`_start_if_needed` ended in a bare `self._ready.wait()` with no timeout. If
the worker hung while attaching to a browser — rather than failing — the
caller blocked forever.

The call path a few lines above it already had `_CALL_TIMEOUT_SECONDS`, with a
comment about a browser that "loads forever". Startup was simply missed. It
now has `_STARTUP_TIMEOUT_SECONDS = 90.0` and raises
`BrowserServiceTimeoutError`.

A test now asserts **no bare `.wait()` or `.join()`** remains anywhere in
`brain/`, `core/`, `tools/`, `voice/`, `agents/` or `memory/`, apart from three
deliberate ones that wait on a stop signal or a child's whole lifetime.

### 3. A raw tool exception reached the retry with nothing attached — *tool*

`_run_step` caught `Exception` and produced a step failure with **no failure
code and no `info`**. So it never appeared in `task_state.errors`, never
reached `collected_information`, and the next planning call could not see what
had gone wrong. The retry was the same attempt again.

It now carries `failure_code="tool_exception"` and the exception text as
`info`, so the replanner has a reason to work with. Classified **retryable** —
the planner can legitimately recover by choosing a different capability.

---

## Startup hang containment (the 4E-G carry-over)

`ChatEngine()` is built at module import and had no bound. Measured in 4E-G it
sometimes reached ready in ~90s and sometimes not in 300s, with nothing to
report either way. **Reproduced on the original code with the 4E-G changes
stashed**, so it is pre-existing.

`build_within()` bounds it, and the design deserves stating plainly because of
what it does *not* claim:

> Python cannot interrupt a thread blocked in C. There is no safe way to abort
> a constructor part-way and be left with a usable process. So this does not
> try to. It bounds how long the **caller** waits, and leaves the stuck work on
> a **daemon** thread, which the interpreter abandons at exit rather than
> joining.

**What it guarantees:** the caller always gets an answer, so startup can report
a required failure and the lifecycle can release what it already holds. Default
240s, overridable via `ELAINA_ENGINE_TIMEOUT`. The cap is generous because a
cold model load is legitimately slow; what it rules out is waiting forever.

**What it cannot guarantee, stated honestly:** a constructor abandoned midway
may have opened things nobody holds a reference to. Mitigations rather than
solutions — its own children carry their own bounds (the MCP client times out
at 15s), the process exits immediately afterwards, which returns handles to the
OS, and a partially-built object is never handed back or used.

**Root cause not established.** The trigger appeared environmental — repeatedly
force-killing backends that held the microphone. Containment was the right
move per the phase brief; finding the exact blocking call inside a 592-line
constructor is future work.

---

## Scenario coverage (26)

| Group | Cases | Covers |
|---|---|---|
| Startup hangs | 5 | bounded build, error propagation, daemon thread, cleanup on timeout, optional degrade |
| Stale resources | 2 | bound port as a clean required failure; no process-name kills |
| Tool failure | 4 | raised exception contained, unavailable dependencies, no traceback as an answer |
| Retry policy | 5 | transient recovers, repeats exhaust, terminal does not spend budget, step budget holds, missing info asks |
| Cancellation | 5 | before action, midway, during retry, broken predicate, reported as CANCELLED |
| Post-failure usability | 3 | new task after terminal failure, after cancellation, no shared state |
| Runtime exceptions | 2 | one failing cleanup does not skip others; no unbounded waits remain |

## Mid-task correction (requirement 5)

**Cancel and replace**, which is the simpler reliable option for v1 and what
the code already does: a new request cancels the turn in flight
(`engine.on_speech_start()` / `cancel_active_turn()`), and the planner now
honours that cancellation rather than finishing the stale plan. There is no
partial-plan modification, and there should not be one before the freeze —
mutating a plan mid-flight is where a stale step becomes a wrong action.

## Failure classification, as required

| Defect | Class |
|---|---|
| Planner ignored cancellation | cancellation |
| Bare `_ready.wait()` in browser startup | timeout/hang |
| Tool exception carried no reason | tool |
| `ChatEngine()` unbounded at import | startup/lifecycle |
| Intermittent stall reproduced on original code | environment |
| My 3-cycle restart script's short timeout | test harness |

The last two were **not** patched by changing production architecture: the
stall was isolated to the environment, and the script's timeout was a harness
bug of mine.

---

## Known limitations and manual checks

Not automated, and not claimed to be:

| # | Case | Why it is manual |
|---|---|---|
| F1 | Microphone physically disappears mid-session | needs real hardware removal |
| F2 | Ollama stopped while a turn is in flight | needs the service stopped under load |
| F3 | Internet lost mid-search | needs a real network interruption |
| F4 | Electron force-closed mid-task | needs a real window |
| F5 | Machine sleeps and wakes mid-task | needs a real suspend |
| F6 | Browser closed by the user mid-navigation | needs a real browser |

Remaining known gaps:

- **The root cause of the `ChatEngine` init stall is unknown** — contained, not
  fixed. If it recurs in normal use, the next step is bisecting the
  constructor's subsystem wiring, most likely `AudioManager`.
- **Electron's close remains a force-kill** (see `RUNTIME_BASELINE.md`), so the
  backend's graceful cleanup is skipped on that path. Nothing orphans and
  nothing durable is lost.
- **Cancellation reaches the planner between steps, not inside one.** A single
  long-running browser call still runs to its own 60s bound before the
  cancellation is seen. Bounded, so not a hang, but not instant either.
