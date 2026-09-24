"""What happened between the model's draft and what the person got.

A reply is not what the model wrote. It is what is left after up to fifty
rewrites in ``ChatEngine._answer_turn`` -- retries, a length rewrite, the
style layer, ten truth guards, offer handling, a final check that can undo
the guards before it, and a speech filter that runs several times. Each
stage announced itself with ``print``, and in real use the backend is
started by Electron with its output thrown away. So the question that
matters after a bad answer -- *what did the model actually say, and which
stage turned it into this?* -- had no answer.

The case that made this necessary: "Can you give me like an example when
Taylor series are used?" was answered with a textbook definition. The
cause turned out to be that the turn had been run as a product
recommendation over five search snippets, with the model told to "recommend
the best of the ones marked FITS". That was recoverable only because the
search evidence happened to be saved as a memory row. The route, the draft
and every rewrite were gone, and the two turns before it could not be
reconstructed at all.

One record per turn, written to ``runtime/turn_trace/<date>.jsonl``:

* the person's words and what the voice loop knew about them;
* the route, the decision, the conversational act, the limits, the state;
* the draft, and every stage after it -- in order, whether it ran, whether
  it changed anything, the text before and after when it did, and the lines
  it printed while it ran, which are its own stated reasons;
* every model call during the turn: who made it, the prompt, the output,
  latency, time to first token and tokens per second, from Ollama's own
  counters;
* the evidence searched for this turn, the text displayed, and the text and
  sentence chunks actually handed to the voice.

Turns where a stage changed the reply are also written, without prompts, to
``runtime/failure_bank/<date>.jsonl`` -- one line per turn, its findings in
order. That is the corpus later evaluation work draws from.

**The chain.** Every recorded stage carries the text it was given. If that
is not the text the previous stage returned, something rewrote the reply
without being recorded, and the trace says so (``chain_ok: false`` and an
``(unrecorded ...)`` step holding the difference). A complete record is
therefore checkable rather than assumed, and a rewrite site added later
without a step shows up as a broken chain in the test suite.

**Behaviour.** None. Every function here returns what it was given, and
nothing here may raise into a turn. With ``ELAINA_TURN_TRACE=off`` or
``debug.turn_trace: false`` nothing is recorded or written.

**Privacy.** The record holds what the person said and parts of what she
knows about them. It stays in ``runtime/``, is git-ignored, and files older
than ``debug.turn_trace_retention_days`` are deleted.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import queue
import sys
import threading
import time
import uuid
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from core.paths import RUNTIME_ROOT


SCHEMA = 1

# Where records go. Module attributes, like surface_log.PATH, so the turn
# harness can point a test suite somewhere that is not the person's own
# runtime directory.
DIRECTORY = Path(RUNTIME_ROOT) / "turn_trace"
BANK_DIRECTORY = Path(RUNTIME_ROOT) / "failure_bank"
RETENTION_DAYS = 30

_OFF = frozenset({"0", "off", "false", "no"})
ENABLED = os.environ.get("ELAINA_TURN_TRACE", "").strip().lower() not in _OFF
# Recorded but not written: the whole-turn test suite checks every trace in
# memory and has no business filling a directory with two thousand of them.
PERSIST = True

# One backend process. Turns in the same session share history, so a record
# that says which session it came from can be read beside its neighbours.
SESSION_ID = uuid.uuid4().hex[:12]

_TEXT_LIMIT = 8000       # a prompt message, a draft, a stage's before/after
_HISTORY_LIMIT = 1500    # each of the last two history messages kept
_LOG_LINES = 600
_LOG_LINE_LIMIT = 600


def configure(*, enabled: bool | None = None,
              retention_days: int | None = None) -> None:
    """Apply config.yaml. The environment variable wins over the file."""
    global ENABLED, RETENTION_DAYS
    if enabled is not None:
        forced_off = (
            os.environ.get("ELAINA_TURN_TRACE", "").strip().lower() in _OFF
        )
        ENABLED = bool(enabled) and not forced_off
    if retention_days is not None:
        try:
            RETENTION_DAYS = max(1, int(retention_days))
        except (TypeError, ValueError):
            pass


# ------------------------------------------------------------ small helpers


def _clip(text, limit: int = _TEXT_LIMIT) -> str:
    """The text, or its head and tail when it is longer than ``limit``."""
    said = "" if text is None else str(text)
    if len(said) <= limit:
        return said
    head = limit * 2 // 3
    tail = limit - head
    return (
        f"{said[:head]}\n...[{len(said) - limit} chars cut]...\n"
        f"{said[-tail:]}"
    )


def _plain(value, depth: int = 0):
    """Something JSON can hold, from whatever the engine had in hand.

    Shallow and bounded on purpose. A route or a capability choice is a
    dataclass of short fields; a held result set can be large, and the
    record needs its shape, not every candidate's every attribute.
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, str):
        return _clip(value, 2000)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, Enum):
        return _plain(value.value, depth)
    if depth >= 4:
        return _clip(repr(value), 300)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _plain(getattr(value, item.name, None), depth + 1)
            for item in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): _plain(item, depth + 1)
            for key, item in list(value.items())[:60]
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item, depth + 1) for item in list(value)[:40]]
    return _clip(str(value), 300)


def _field(obj, name: str):
    """A field of an Ollama response, which may be a dict or a model."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        try:
            value = obj[name]
        except Exception:
            value = None
    return value


def _content(response) -> str:
    message = _field(response, "message")
    text = _field(message, "content") if message is not None else None
    if not text:
        text = _field(response, "response")
    return str(text or "")


def _caller() -> str:
    """The first frame outside this module: who asked the model."""
    frame = sys._getframe(1)
    here = __name__
    while frame is not None and frame.f_globals.get("__name__") == here:
        frame = frame.f_back
    if frame is None:
        return ""
    return f"{frame.f_globals.get('__name__', '')}:{frame.f_code.co_name}"


# ---------------------------------------------------------------- the record


class TurnTrace:
    """Everything one turn did to its reply, in order."""

    def __init__(self, user_input: str, inputs: dict | None = None) -> None:
        self.turn_id = uuid.uuid4().hex[:12]
        self.session_id = SESSION_ID
        self.started_at = datetime.now().isoformat(timespec="milliseconds")
        self.thread = threading.current_thread().name
        self.user_input = str(user_input or "")
        self.inputs = _plain(dict(inputs or {}))
        self.context: dict = {}
        self.evidence: dict = {}
        self.draft: dict | None = None
        self.steps: list[dict] = []
        self.runs: dict[str, int] = {}
        self.model_calls: list[dict] = []
        self.display: str | None = None
        self.speech: list[dict] = []
        self.log: list[str] = []
        self.timings: dict = {}
        self.outcome = "answered"
        self.error = ""
        self.chain_ok = True
        self.elapsed = 0.0

        self._started = time.perf_counter()
        self._mutex = threading.Lock()
        # The reply as it stands, as far as the record knows. None until the
        # draft or the first stage says what it is.
        self._chain: str | None = None
        self._partial = ""
        self._log_mark = 0
        self._call_mark = 0
        self._last_step_at = self._started
        self._systems_seen: set[str] = set()
        self._outer: TurnTrace | None = None

    def _now(self) -> float:
        return round(time.perf_counter() - self._started, 4)

    # -------------------------------------------------------- the reply

    def record_draft(self, text: str, *, source: str, model: str = "") -> None:
        with self._mutex:
            self.draft = {
                "text": _clip(text),
                "source": source,
                "model": model,
                "t": self._now(),
            }
            self._chain = str(text or "")
            # Routing printed plenty before this; none of it is a reason
            # for anything a stage did to the draft.
            self._log_mark = len(self.log)
            self._call_mark = len(self.model_calls)
            self._last_step_at = time.perf_counter()

    def record_step(self, name: str, before, after) -> None:
        now = time.perf_counter()
        with self._mutex:
            if self._chain is not None and before != self._chain:
                self._gap(name, self._chain, before)
            self.runs[name] = self.runs.get(name, 0) + 1
            changed = before != after
            entry: dict = {
                "i": len(self.steps),
                "name": name,
                "changed": changed,
                "dt": round(now - self._last_step_at, 4),
            }
            if changed:
                entry["before"] = _clip(before)
                entry["after"] = _clip(after)
                # What the stage printed while it ran is its own account of
                # why. Only the tagged lines: "[Style] ..." is a reason,
                # the reply echoed to the console is not.
                reasons = [
                    line for line in self.log[self._log_mark:]
                    if line.startswith("[")
                ]
                if reasons:
                    entry["log"] = reasons[:20]
            calls = list(range(self._call_mark, len(self.model_calls)))
            if calls:
                entry["model_calls"] = calls
            self.steps.append(entry)
            self._log_mark = len(self.log)
            self._call_mark = len(self.model_calls)
            self._last_step_at = now
            self._chain = after if after is None else str(after)

    def _gap(self, name: str, expected, found) -> None:
        """Something changed the reply between two recorded stages."""
        self.chain_ok = False
        self.steps.append({
            "i": len(self.steps),
            "name": f"(unrecorded before {name})",
            "changed": True,
            "gap": True,
            "before": _clip(expected),
            "after": _clip(found),
        })

    def record_display(self, text: str) -> None:
        with self._mutex:
            if self._chain is not None and text != self._chain:
                self._gap("display", self._chain, text)
            self.display = str(text or "")
            self._chain = self.display

    # ------------------------------------------------- around the reply

    def record_context(self, values: dict) -> None:
        plain = {key: _plain(value) for key, value in values.items()}
        with self._mutex:
            self.context.update(plain)

    def record_evidence(self, values: dict) -> None:
        plain = {
            key: _clip(value) if isinstance(value, str) else _plain(value)
            for key, value in values.items()
        }
        with self._mutex:
            self.evidence.update(plain)

    def record_speech(self, said: str, spoken: str, chunks) -> None:
        entry = {
            "t": self._now(),
            "said": _clip(said),
            "spoken": _clip(spoken),
            "chunks": [_clip(chunk, 2000) for chunk in list(chunks or ())],
        }
        with self._mutex:
            self.speech.append(entry)

    def record_timings(self, values: dict) -> None:
        plain = {
            str(key): round(float(value), 4)
            for key, value in dict(values or {}).items()
            if isinstance(value, (int, float))
        }
        with self._mutex:
            self.timings.update(plain)

    def record_outcome(self, outcome: str, error: str = "") -> None:
        with self._mutex:
            self.outcome = str(outcome or self.outcome)
            if error:
                self.error = _clip(error, 2000)

    def _take_output(self, text: str) -> None:
        """Printed text, split into lines, from whichever thread printed it."""
        with self._mutex:
            data = self._partial + text
            *lines, self._partial = data.split("\n")
            for line in lines:
                line = line.strip()
                if line and len(self.log) < _LOG_LINES:
                    self.log.append(line[:_LOG_LINE_LIMIT])

    # ----------------------------------------------------- model calls

    def _open_call(self, method: str, args, kwargs, caller: str) -> dict:
        model = kwargs.get("model") or (args[0] if args else "")
        entry: dict = {
            "caller": caller,
            "thread": threading.current_thread().name,
            "method": method,
            "model": str(model or ""),
            "stream": bool(kwargs.get("stream")),
            "t": self._now(),
        }
        for key in ("format", "options", "think", "keep_alive"):
            if key in kwargs:
                entry[key] = _plain(kwargs[key])
        if method == "chat":
            messages = kwargs.get("messages")
            if messages is None and len(args) > 1:
                messages = args[1]
            summary = self._messages(messages)
        else:
            prompt = kwargs.get("prompt")
            if prompt is None and len(args) > 1:
                prompt = args[1]
            summary = {"prompt": _clip(prompt)}
        entry.update(summary)
        with self._mutex:
            entry["i"] = len(self.model_calls)
            self.model_calls.append(entry)
        return entry

    def _messages(self, messages) -> dict:
        """The prompt, kept to what explains the answer.

        The system prompt is stored in full the first time it appears in a
        turn and by hash after that -- the persona is several kilobytes and
        most calls repeat it. History is the previous turns, already in
        the previous records, so only its length and last two messages
        are kept. The final message holds the evidence and instructions for
        this call and is kept whole, up to the text limit.
        """
        items = list(messages or [])

        def role(message) -> str:
            return str(_field(message, "role") or "")

        def text(message) -> str:
            return str(_field(message, "content") or "")

        def shaped(message, limit: int) -> dict:
            shaped_message = {"role": role(message), "content": _clip(text(message), limit)}
            images = _field(message, "images")
            if images:
                shaped_message["images"] = len(list(images))
            return shaped_message

        out: dict = {"messages": []}
        if not items:
            return out
        if role(items[0]) == "system":
            system = text(items[0])
            digest = hashlib.sha1(system.encode("utf-8", "replace")).hexdigest()[:12]
            record = {"role": "system", "sha1": digest, "chars": len(system)}
            with self._mutex:
                first_time = digest not in self._systems_seen
                self._systems_seen.add(digest)
            if first_time:
                record["content"] = _clip(system)
            out["messages"].append(record)
            items = items[1:]
        if not items:
            return out
        history, last = items[:-1], items[-1]
        if history:
            out["history"] = len(history)
            out["messages"].extend(
                shaped(message, _HISTORY_LIMIT) for message in history[-2:]
            )
        out["messages"].append(shaped(last, _TEXT_LIMIT))
        return out

    def _close_call(self, entry: dict, started: float, *, response=None,
                    error: BaseException | None = None, streamed=None,
                    final=None, first_token: float | None = None) -> None:
        latency = time.perf_counter() - started
        update: dict = {"latency": round(latency, 4)}
        if error is not None:
            update["error"] = f"{type(error).__name__}: {error}"[:500]
        stats_from = final if final is not None else response
        output = streamed if streamed is not None else (
            _content(response) if response is not None else ""
        )
        update["output"] = _clip(output)
        if first_token is not None:
            update["ttft"] = round(first_token - started, 4)
        for key in (
            "done_reason", "eval_count", "prompt_eval_count",
            "eval_duration", "prompt_eval_duration", "load_duration",
            "total_duration",
        ):
            value = _field(stats_from, key)
            if value is not None:
                update[key] = _plain(value)
        count = update.get("eval_count")
        duration = update.get("eval_duration")
        if isinstance(count, int) and isinstance(duration, int) and duration > 0:
            update["tokens_per_second"] = round(count / (duration / 1e9), 1)
        with self._mutex:
            entry.update(update)

    # ---------------------------------------------------------- output

    def _close(self) -> None:
        with self._mutex:
            if self._partial.strip() and len(self.log) < _LOG_LINES:
                self.log.append(self._partial.strip()[:_LOG_LINE_LIMIT])
            self._partial = ""
            self.elapsed = round(time.perf_counter() - self._started, 4)

    @property
    def mutated(self) -> bool:
        return any(step.get("changed") for step in self.steps)

    def as_record(self) -> dict:
        with self._mutex:
            return {
                "schema": SCHEMA,
                "turn_id": self.turn_id,
                "session_id": self.session_id,
                "started_at": self.started_at,
                "elapsed": self.elapsed,
                "thread": self.thread,
                "user_input": self.user_input,
                "inputs": self.inputs,
                "context": dict(self.context),
                "draft": self.draft,
                "steps": list(self.steps),
                "runs": dict(self.runs),
                "mutated": any(step.get("changed") for step in self.steps),
                "chain_ok": self.chain_ok,
                "display": self.display,
                "speech": list(self.speech),
                "evidence": dict(self.evidence),
                "model_calls": list(self.model_calls),
                "timings": dict(self.timings),
                "outcome": self.outcome,
                "error": self.error,
                "log": list(self.log),
            }

    def as_findings(self) -> dict:
        """The failure-bank line: what changed, in order, without prompts."""
        with self._mutex:
            route = self.context.get("route") or {}
            return {
                "schema": SCHEMA,
                "turn_id": self.turn_id,
                "session_id": self.session_id,
                "started_at": self.started_at,
                "language": self.context.get("language", ""),
                "user_input": self.user_input,
                "intent": route.get("intent", "") if isinstance(route, dict) else "",
                "act": self.context.get("act", ""),
                "model": (self.draft or {}).get("model", ""),
                "draft": (self.draft or {}).get("text", ""),
                "findings": [
                    {
                        key: step[key]
                        for key in ("i", "name", "before", "after", "log", "gap")
                        if key in step
                    }
                    for step in self.steps if step.get("changed")
                ],
                "display": self.display,
                "speech": [entry["spoken"] for entry in self.speech],
                "chain_ok": self.chain_ok,
            }


# ----------------------------------------------------------- the turn slots
#
# Two slots, for two kinds of writer. The stages of the reply all run on the
# turn's own thread, so they record through a thread-local slot and cannot
# land in another turn's record. Model calls, printed lines and speech can
# come from any thread working for the turn -- the parallel searches, the
# audio worker -- and record through the module-level slot, the same design
# core/timing.py uses: turns are serialised by main.py's response lock.

_lock = threading.Lock()
_current: TurnTrace | None = None
_local = threading.local()
_last: TurnTrace | None = None
_turns_traced = 0
_breaks: list[dict] = []


def begin(user_input: str, **inputs) -> TurnTrace | None:
    """Open the record for a turn that is starting, if tracing is on."""
    global _current
    if not ENABLED or not str(user_input or "").strip():
        return None
    try:
        trace = TurnTrace(str(user_input).strip(), inputs)
        _install_output_capture()
        trace._outer = getattr(_local, "trace", None)
        _local.trace = trace
        with _lock:
            _current = trace
        return trace
    except Exception:
        return None


def current() -> TurnTrace | None:
    """The turn in flight, for anything recording on its behalf."""
    return _current


def _mine() -> TurnTrace | None:
    return getattr(_local, "trace", None)


def step(name: str, before, after):
    """Record one stage of the reply. Returns ``after`` untouched."""
    trace = _mine()
    if trace is not None:
        try:
            trace.record_step(name, before, after)
        except Exception:
            pass
    return after


def draft(text: str, *, source: str, model: str = "") -> None:
    trace = _mine()
    if trace is not None:
        try:
            trace.record_draft(text, source=source, model=model)
        except Exception:
            pass


def display(text: str) -> None:
    trace = _mine()
    if trace is not None:
        try:
            trace.record_display(text)
        except Exception:
            pass


def note_context(**values) -> None:
    trace = _mine() or _current
    if trace is not None:
        try:
            trace.record_context(values)
        except Exception:
            pass


def note_evidence(**values) -> None:
    trace = _mine() or _current
    if trace is not None:
        try:
            trace.record_evidence(values)
        except Exception:
            pass


def note_speech(said: str, spoken: str, chunks) -> None:
    trace = _current
    if trace is not None:
        try:
            trace.record_speech(said, spoken, chunks)
        except Exception:
            pass


def note_timings(values: dict) -> None:
    trace = _mine() or _current
    if trace is not None:
        try:
            trace.record_timings(values)
        except Exception:
            pass


def note_outcome(outcome: str, error: str = "") -> None:
    trace = _mine() or _current
    if trace is not None:
        try:
            trace.record_outcome(outcome, error)
        except Exception:
            pass


def finish(trace: TurnTrace | None = None) -> TurnTrace | None:
    """Close the record, keep it for the report, and write it out."""
    global _current, _last, _turns_traced
    trace = trace or _mine()
    if trace is None:
        return None
    try:
        with _lock:
            if _current is trace:
                _current = trace._outer
        if _mine() is trace:
            _local.trace = trace._outer
        trace._close()
        _turns_traced += 1
        if not trace.chain_ok:
            _breaks.append({
                "turn_id": trace.turn_id,
                "user_input": trace.user_input[:120],
                "gaps": [
                    step["name"] for step in trace.steps if step.get("gap")
                ],
            })
        _last = trace
        if PERSIST:
            day = trace.started_at[:10]
            _write(
                DIRECTORY / f"{day}.jsonl",
                json.dumps(trace.as_record(), ensure_ascii=False, default=str),
            )
            if trace.mutated:
                _write(
                    BANK_DIRECTORY / f"{day}.jsonl",
                    json.dumps(
                        trace.as_findings(), ensure_ascii=False, default=str,
                    ),
                )
    except Exception:
        # The record is never the reason a turn fails.
        pass
    return trace


def last() -> TurnTrace | None:
    """The most recently finished record in this process."""
    return _last


def summary() -> dict:
    """How many turns were traced in this process, and which broke."""
    return {"turns": _turns_traced, "breaks": list(_breaks)}


def reset() -> None:
    """Forget everything recorded in this process (tests only)."""
    global _current, _last, _turns_traced
    with _lock:
        _current = None
    _local.trace = None
    _last = None
    _turns_traced = 0
    _breaks.clear()


# --------------------------------------------------------------- the model


class TracingClient:
    """The Ollama client, with every call written into the turn in flight.

    Pure pass-through: the same arguments reach the real client and the
    same response objects come back, chunk for chunk when streamed. Outside
    a turn -- at startup, in a background job -- it records nothing.
    Attribute reads and writes go to the wrapped client, so anything that
    configures the client configures the real one.
    """

    def __init__(self, inner) -> None:
        object.__setattr__(self, "_inner", inner)

    @property
    def wrapped(self):
        return object.__getattribute__(self, "_inner")

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name, value) -> None:
        setattr(object.__getattribute__(self, "_inner"), name, value)

    def chat(self, *args, **kwargs):
        return self._traced("chat", args, kwargs)

    def generate(self, *args, **kwargs):
        return self._traced("generate", args, kwargs)

    def _traced(self, method: str, args, kwargs):
        call = getattr(object.__getattribute__(self, "_inner"), method)
        trace = _current
        if trace is None:
            return call(*args, **kwargs)
        try:
            entry = trace._open_call(method, args, kwargs, _caller())
        except Exception:
            return call(*args, **kwargs)
        started = time.perf_counter()
        try:
            response = call(*args, **kwargs)
        except BaseException as error:
            _safely(trace._close_call, entry, started, error=error)
            raise
        if kwargs.get("stream"):
            return _streamed(response, trace, entry, started)
        _safely(trace._close_call, entry, started, response=response)
        return response


def _safely(function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except Exception:
        pass


def _streamed(response, trace: TurnTrace, entry: dict, started: float):
    """Yield the stream unchanged, and note when the first words came."""
    parts: list[str] = []
    first_token = None
    final = None
    try:
        for chunk in response:
            text = ""
            try:
                text = _content(chunk)
            except Exception:
                pass
            if text:
                if first_token is None:
                    first_token = time.perf_counter()
                parts.append(text)
            final = chunk
            yield chunk
    finally:
        _safely(
            trace._close_call, entry, started,
            streamed="".join(parts), final=final, first_token=first_token,
        )


# -------------------------------------------------------------- the console
#
# The stages already explain themselves -- "[Style] ...", "[Response Guard]
# The final text repeated the previous answer ..." -- and every word of it
# went to a console nobody could see. The console stays exactly as it was;
# a copy of each line goes into the turn in flight.


class _Tee:
    def __init__(self, stream) -> None:
        object.__setattr__(self, "_stream", stream)

    def write(self, text):
        written = object.__getattribute__(self, "_stream").write(text)
        trace = _current
        if trace is not None:
            try:
                trace._take_output(str(text))
            except Exception:
                pass
        return written

    def flush(self) -> None:
        object.__getattribute__(self, "_stream").flush()

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_stream"), name)


def _install_output_capture() -> None:
    stream = sys.stdout
    if stream is None or isinstance(stream, _Tee):
        return
    sys.stdout = _Tee(stream)


# ---------------------------------------------------------------- the files

_queue: "queue.Queue[tuple[Path, str]]" = queue.Queue()
_writer: threading.Thread | None = None
_writer_lock = threading.Lock()
_pruned: set[Path] = set()


def _write(path: Path, line: str) -> None:
    """Queue one line for a background writer; the turn does not wait."""
    global _writer
    _queue.put((path, line))
    with _writer_lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(
                target=_drain, name="elaina-turn-trace", daemon=True,
            )
            _writer.start()


def _drain() -> None:
    while True:
        path, line = _queue.get()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _prune(path.parent)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass
        finally:
            _queue.task_done()


def _prune(directory: Path) -> None:
    """Delete day files older than the retention window, once per process."""
    if directory in _pruned:
        return
    _pruned.add(directory)
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).date()
    for path in directory.glob("*.jsonl"):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            try:
                path.unlink()
            except OSError:
                pass


def flush(timeout: float = 5.0) -> bool:
    """Wait until every queued record is on disk, or ``timeout`` passes."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return not _queue.unfinished_tasks


atexit.register(flush, 2.0)
