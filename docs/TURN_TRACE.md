# The turn record

**What this is.** One record per turn of everything between the model's
first draft and what the person saw and heard. It is Phase 0 of the
communication architecture work: it changes no behaviour, and every later
phase reads its numbers.

**Why it exists.** "Can you give me like an example when Taylor series are
used?" was answered with a textbook definition. The backend runs under
Electron with its output discarded, so the route, the draft and every
rewrite of that turn were gone. The cause surfaced only because the search
evidence happened to be saved as a memory row. That row showed the turn had
been run as a *product recommendation*, with the query "math Taylor" and the
instruction "Recommend the best of the ones marked FITS". The two turns
before it could not be reconstructed at all.

---

## Where it is

| | |
|---|---|
| Every turn | `runtime/turn_trace/<date>.jsonl`, one JSON line per turn |
| Turns a stage rewrote | `runtime/failure_bank/<date>.jsonl`, the findings in order, no prompts |
| Code | `core/turn_trace.py` |
| Reading it | `scripts/turn_trace_report.py` |
| Switch | `debug.turn_trace` in config.yaml; `ELAINA_TURN_TRACE=off` overrides it |
| Retention | `debug.turn_trace_retention_days` (30). Older day files are deleted |

Both directories are git-ignored. They hold what the person said and part of
what she knows about them.

## What one record holds

- **The turn**: the words, what the voice loop knew (language, confidence,
  heard-unclearly), the session and turn ids.
- **The decisions**: the route (every router field), the interaction
  decision, the capability, the goal, the context policy, the act, the
  length limits, the model and temperature, and the conversation state (open
  problem, held results, pending offer, history length).
- **The evidence**: search queries and results, the clock, a calculation
  plan, a tool result.
- **The draft**: the model's text before any stage touched it, or the locked
  line when there was no model draft.
- **The stages**: every rewrite site in `ChatEngine._answer_turn`, in order,
  each one run. Each entry records whether it changed the reply, and how
  long it took since the stage before it. When it changed the reply it also
  records the text before and after, the lines it printed while it ran (its
  own stated reason) and which model calls it made.
- **The model calls**: every call during the turn. Each has its caller, the
  prompt, the output, latency, time to first token, token counts, tokens per
  second and stop reason, from Ollama's own counters.
- **The output**: the displayed text, and every piece of speech with the
  exact text and sentence chunks the voice was given.
- **The console**: every printed line during the turn.
- **Timings**: the turn's timeline, from VAD to total.

## The chain

Every stage is recorded with the text it was given. If that differs from
what the previous stage returned, something rewrote the reply without being
recorded. The record then says `chain_ok: false` and holds the difference as
an `(unrecorded before ...)` step. Two checks keep it complete:

- `tests/test_turn_trace.py` parses `_answer_turn` and fails on any
  `reply = ...` that is not a `turn_trace.step(...)`.
- `tests/run_tests.py` checks the chain of every whole turn the suite runs
  and fails the run on a break.

## Reading it

```bash
.venv/Scripts/python.exe scripts/turn_trace_report.py last           # the last turn, in full
.venv/Scripts/python.exe scripts/turn_trace_report.py last 10        # one line each
.venv/Scripts/python.exe scripts/turn_trace_report.py find "taylor"  # by what was said
.venv/Scripts/python.exe scripts/turn_trace_report.py stages --days 7
.venv/Scripts/python.exe scripts/turn_trace_report.py calls --days 7
.venv/Scripts/python.exe scripts/turn_trace_report.py bank
```

`--dir <runtime>` reads a verification run's own runtime directory.

## Measured (2026-09-23)

- **Cost.** Recording a heavy synthetic turn (52 stages, 5 model calls with
  8 KB prompts, printed lines, serialisation included) adds 0.29 ms at p50
  and 0.37 ms at p95. Writing happens on a background thread after the reply
  is out.
- **Size.** A live replay of five real turns wrote 155 KB, about 31 KB a
  turn. At 200 turns a day and 30 days' retention that is about 180 MB.
- **Complete.** The full suite (3,769 tests) traced 168 whole turns with no
  broken chain. On/off equivalence gives the same replies and the same model
  calls in the same order.

**What the first live replay showed.** It replayed the session that
prompted this (UW Math 207, then three Taylor turns, then the time) against
a scratch runtime with speech silenced:

- *"Can you give me like an example when Taylor series are used?"* routed as
  `knowledge_question`, normalised to "Provide an example of when Taylor
  series are used", with limits of 45 words and 2 sentences. **23 stages
  ran; none changed a word.** The vague "engineers use it to model
  real-world phenomena" is the model's draft under the current prompt.
- *"What time is it right now?"*: the clock line in the prompt read
  `11:29 PM (태평양 일광 절약 시간, UTC-0700)`, because `%Z` returns the Windows
  zone name in the display language, which is Korean on this machine. The
  model translated it and repeated it with the offset. The draft then went
  on: "That will make for a long day." That is the persona file's example
  reply to "I didn't sleep at all last night", spoken at 11:29 PM. Again 23
  stages ran and none changed it.
- The router took 2.7–2.9 s of every routed turn (83% of a 3.5 s turn). The
  answer took 0.6 s, with the first token at 0.3 s and 126 tokens per second.

## Silent speech for verification runs

Every verification run used to speak through ElevenLabs, which is billed per
character. `ELAINA_TTS=off` (or `tts.enabled: false`) now replaces the voice
with `voice/silent.py`: sentences are still split, queued and recorded, and
nothing is sent.

- `tests/run_tests.py` and every script that starts a backend default to it.
- `tests/test_silent_tts.py` fails if a script that starts a backend does not.
- Set `ELAINA_TTS=on` to hear a run.
