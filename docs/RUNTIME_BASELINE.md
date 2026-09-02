# Runtime lifecycle baseline

Who owns what, what happens when starting fails halfway, and what is released
on the way out.

```bash
.venv/Scripts/python.exe -m unittest tests.test_runtime_lifecycle
```

| Date | Automated lifecycle cases | Result |
|---|---|---|
| 2026-09-02 | 17 | **17/17** |

---

## The defect this phase existed to fix

Startup ran as a straight line of module-level statements, and the
`try/finally` that cleans up began **after** it:

```python
engine = ChatEngine()
websocket_server = WebSocketServer(...); websocket_server.start()
launch_electron_if_requested()
speech_to_text = SpeechToText(...)
print("Elaina is ready.")

try:   # <- cleanup only starts being possible here
```

A failure anywhere in that block — no microphone, no Node, a port already
bound — exited on an unhandled traceback with **port 8765 still bound, an
Electron window still open, and the engine's browser service and MCP
subprocess still running**. The next launch then met a port collision and a
second window.

`core/lifecycle.py` fixes it with one rule: **a subsystem's cleanup is
registered the instant it starts, and never before.** Whatever came up gets
taken back down, in reverse order, wherever the failure landed.

This was demonstrated live, twice, by accident. A `NameError` in my own
wiring and then a genuinely bound port both produced:

```
[Lifecycle] chat engine ready.
[Lifecycle] websocket server FAILED, and is required: ...
[Lifecycle] NOT READY -- websocket server (...)
[Lifecycle] Shutting down: a required subsystem did not start
[Lifecycle] Released chat engine.
[Lifecycle] Shutdown complete.
```

Clean abort, nothing orphaned. Before this phase that same failure would have
left the engine running.

## Required vs optional, stated rather than implied

| Subsystem | Required | On failure |
|---|---|---|
| chat engine | yes | abort |
| websocket server | yes | abort |
| desktop window (Electron) | **no** | headless; `ELAINA_OPEN_DESKTOP=0` is a supported mode |
| speech to text | **no** | text mode only; the mic loop parks |

A missing microphone used to crash the whole process. It now degrades and
says so: `[Lifecycle] READY (degraded: speech to text)`.

## What else changed

- **`WebSocketServer.stop()`** — it had none. `_serve` awaited a bare
  `asyncio.Future()` that nothing ever resolved, so the port was released only
  by the process dying. Three consecutive bind/release cycles on one port are
  now asserted.
- **Signal handlers** — there were none. SIGTERM/SIGINT/SIGBREAK now reach the
  same cleanup Ctrl+C does.
- **A graceful `shutdown` command** over the WebSocket, so Electron can ask
  before it force-kills.
- **`_begin_stop()`** — `_thread.interrupt_main()` alone was not enough, and
  this is *why* Electron reaches for taskkill: the loop spends nearly all its
  time blocked inside `listen_and_transcribe`, waiting on the microphone down
  in C, where a pending KeyboardInterrupt is not seen. Measured: a shutdown
  request sat unanswered for minutes. The microphone is now paused first to
  unblock the read, then the interrupt follows. All four stop paths
  (Electron close, signal, WebSocket command, watcher) go through it.
- **Electron termination escalates** — `.terminate()` then, if it does not go,
  `.kill()`. Previously an Electron that ignored the request simply stayed.

## Process ownership

Elaina owns exactly what she spawned. Asserted by test:

- **no source file** under `brain/`, `core/`, `tools/`, `agents/`, `voice/`,
  `memory/` or `main.py` contains `/IM`, `killall` or `pkill`;
- `desktop/main.js` kills by **`/pid … /T /F`** — one PID and its tree, never
  by image name.

A user's own Chrome, Python, Electron or Ollama cannot be caught by any of it.

---

## Known limitations

### 1. `ChatEngine()` can hang during startup, outside the lifecycle's reach

The engine is constructed at module import, **before** the lifecycle exists,
and nothing bounds it. During this phase it intermittently hung after the MCP
handshake — sometimes reaching READY in ~90s, sometimes not in 300s.

**Isolated: this is not caused by the lifecycle changes.** Stashing them and
running the original `main.py` reproduced the same stall at the same point.
The trigger appears to be environmental — repeatedly force-killing backends
that held the microphone leaves audio device state that `AudioManager` then
blocks on. It cleared after leaving the devices alone.

It is a real gap: a hang is neither a clean degrade nor a clean abort. Bounding
the engine constructor means putting a watchdog around 592 lines of subsystem
wiring, which is a larger change than this phase should make. **Carried into
the failure-and-recovery phase.**

### 2. Electron's close is a force-kill, so backend cleanup is skipped

`stopPythonBackend()` uses `taskkill /pid … /T /F`. Nothing orphans — `/T`
takes the process tree, and the OS reclaims the microphone, audio streams and
port — but the backend's own graceful cleanup does not run.

Mitigated rather than solved: the `shutdown` WebSocket command now exists so
Electron *can* ask first and escalate only if that does not land. Wiring the
Electron side to use it needs a WebSocket client in `desktop/main.js`, which
is left for after the release freeze.

Note that a force-kill loses nothing durable: the FAISS index is saved on
every write, not at shutdown.

### 3. The venv launcher doubles the process tree

`.venv/Scripts/python.exe` re-execs the system interpreter, so a running
backend is two Python processes, not one:

```
PID 20116  .venv\Scripts\python.exe main.py
PID 26880    C:\Program Files\Python311\python.exe main.py   <- the real one
PID 21240      .venv\Scripts\python.exe  <mcp>
PID 22512        C:\Program Files\Python311\python.exe project_mcp_server
```

Harmless, and worth knowing before anyone counts processes to check for
orphans.

---

## Manual runtime checklist

These need real hardware or a real window and are **not** automated. Nothing
below is claimed as passing by the test suite.

| # | Case | How to check | Expected |
|---|---|---|---|
| M1 | Clean startup with the desktop window | `python main.py` | `[Lifecycle] READY`, one Electron window |
| M2 | Electron closes first | close the window | backend exits; no `python.exe … main.py` remains |
| M3 | Backend exits first | stop the backend | Electron does not remain a dead shell |
| M4 | Three restart cycles | M1 → M2, ×3 | no port collision, no duplicate window, no zombie |
| M5 | Microphone released | close Elaina, open Voice Recorder | the device is available |
| M6 | Microphone unavailable at start | disable the input device, start | `READY (degraded: speech to text)`, text mode works |
| M7 | Ollama not running | stop Ollama, start | aborts cleanly, no orphan |
| M8 | Node/Electron missing | rename `desktop/node_modules`, start | degrades headless, backend still READY |
| M9 | Duplicate launch | start a second instance | the second aborts on the bound port; the first is unaffected |
| M10 | TTS playback stops | interrupt mid-sentence, then close | audio stops, no lingering process |

M4, M6 and M7 were exercised partially during this phase — M7's shape was
observed directly, since a bound port produced exactly the required-failure
abort. M4 could not be completed end-to-end because of limitation 1 above.

## What *is* automated

`tests/test_runtime_lifecycle.py`, 17 cases, in the ordinary suite:

- clean startup reaches READY; READY is not reported before required parts are up
- clean shutdown releases newest-first, and runs once however many times it is asked
- **a required failure unwinds what already started** (the central case)
- nothing further starts after a required failure
- a failed subsystem is never "cleaned up" — its cleanup was never registered
- optional failure degrades and is named; required failure aborts
- one failing cleanup handler does not skip the rest
- three consecutive bind/release cycles on one port
- no source file kills by process name; the Electron kill is PID-scoped
