"""Where one turn's time actually goes.

The engine already kept a ``timings`` dict and printed it once per turn. It
covered everything *after* the transcript arrived -- routing, memory,
generation, tools -- and nothing before it. So the stages a person actually
waits through first, and which are entirely capable of dominating the wait,
were invisible:

* how long the VAD sits on silence before deciding the sentence ended
  (``silence_ms: 900`` -- nearly a second, every single turn, before anything
  else begins);
* how long transcription takes;
* time to *first token*, as opposed to the whole generation;
* how long TTS takes to make its first sound;
* how quickly speech actually stops when interrupted.

Without those, the only stage anyone could point at was the model, which is
why "the LLM is slow" is the assumption this phase exists to test.

This is not a second telemetry system. It is the same per-turn record, moved
somewhere both the voice loop in ``main.py`` and the engine can reach, since
a turn starts in one and finishes in the other. The engine's ``timings`` dict
still exists and is folded in at the end.

Cheap by construction: a dict write and a ``perf_counter`` call per stage, no
I/O, no formatting unless something asks for it. Nothing here is on a token
path -- per-token logging is exactly the noise this avoids.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from statistics import median

# The stages, in the order a turn passes through them. Used to print a
# timeline in a readable order rather than dict order, and to keep the names
# in one place so a report and a log cannot disagree.
STAGES = (
    "vad_trailing_silence",   # end of speech -> VAD says the turn is over
    "stt",                    # audio finalised -> transcript ready
    "route",                  # routing decision, end to end
    "route_model",            # the router's own model call inside it
    "memory_retrieval",       # context and memory lookup
    "ttft",                   # LLM request -> first token
    "generation",             # LLM request -> last token
    "tool_decision",          # capability chosen
    "tool_start",             # decision -> tool actually begins
    "web_search",             # a search, start to finish
    "project_tools",
    "visual_pipeline",
    "tts_start",              # text ready -> first audible audio
    "interrupt_stop",         # user speaks -> speech actually stops
    "total",
)

# What a person experiences, derived from the stages above rather than timed
# separately -- one clock, no double counting.
DERIVED = ("end_of_speech_to_response", "end_of_speech_to_audio")


@dataclass
class Timeline:
    """One turn's stage durations, in seconds."""

    marks: dict[str, float] = field(default_factory=dict)
    label: str = ""
    cold: bool = False
    started_at: float = field(default_factory=time.perf_counter)

    def mark(self, name: str, seconds: float) -> None:
        """Record a stage. Later marks for one stage add rather than replace.

        Adding matters for stages that legitimately happen more than once in
        a turn -- two searches, a retried tool call -- where the honest total
        is the time actually spent, not the last one measured.
        """
        self.marks[name] = self.marks.get(name, 0.0) + max(0.0, float(seconds))

    @contextmanager
    def stage(self, name: str):
        """Time a block and record it."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.mark(name, time.perf_counter() - started)

    def merge(self, other: dict) -> None:
        """Fold in the engine's own ``timings`` dict."""
        for name, seconds in (other or {}).items():
            self.mark(name, seconds)

    # ---------------------------------------------------------- derived

    def end_of_speech_to_response(self) -> float:
        """The number that matters most: silence to something usable.

        Everything the person waits through between stopping speaking and
        seeing an answer -- including the VAD's own trailing silence, which
        is the part most easily mistaken for the model being slow.
        """
        return sum(
            self.marks.get(name, 0.0) for name in (
                "vad_trailing_silence", "stt", "route",
                "memory_retrieval", "ttft",
            )
        )

    def end_of_speech_to_audio(self) -> float:
        return self.end_of_speech_to_response() + self.marks.get(
            "tts_start", 0.0,
        )

    def as_dict(self) -> dict[str, float]:
        record = {
            name: self.marks[name] for name in STAGES if name in self.marks
        }
        record["end_of_speech_to_response"] = self.end_of_speech_to_response()
        record["end_of_speech_to_audio"] = self.end_of_speech_to_audio()
        return record

    def summary(self) -> str:
        """One line, in pipeline order, only for stages that happened."""
        parts = [
            f"{name}={self.marks[name]:.2f}s"
            for name in STAGES if name in self.marks
        ]
        perceived = self.end_of_speech_to_response()
        if perceived > 0:
            parts.append(f"perceived={perceived:.2f}s")
        return "[Timing] " + " ".join(parts)


# ------------------------------------------------------------- the current turn
#
# A turn begins in the microphone loop and ends in the engine, on a different
# thread. One module-level slot is enough because turns are serialised --
# main.py holds response_thread_lock and refuses to start a second one -- and
# a lock here keeps the swap itself safe.

_lock = threading.Lock()
_current: Timeline | None = None
_history: list[Timeline] = []
_HISTORY_LIMIT = 200


def begin(label: str = "", *, cold: bool = False) -> Timeline:
    """Start a fresh timeline for the turn that is beginning."""
    global _current
    with _lock:
        _current = Timeline(label=label, cold=cold)
        return _current


def current() -> Timeline | None:
    return _current


def mark(name: str, seconds: float) -> None:
    """Record a stage on the turn in flight, if there is one."""
    timeline = _current
    if timeline is not None:
        timeline.mark(name, seconds)


@contextmanager
def stage(name: str):
    """Time a block against the turn in flight; a no-op if there is none."""
    started = time.perf_counter()
    try:
        yield
    finally:
        mark(name, time.perf_counter() - started)


def finish() -> Timeline | None:
    """Close the current turn and keep it for the report."""
    global _current
    with _lock:
        timeline = _current
        _current = None
        if timeline is not None:
            _history.append(timeline)
            del _history[:-_HISTORY_LIMIT]
        return timeline


def history(*, cold: bool | None = None) -> list[Timeline]:
    """Completed turns, optionally only the cold or only the warm ones."""
    if cold is None:
        return list(_history)
    return [turn for turn in _history if turn.cold is cold]


def reset() -> None:
    global _current
    with _lock:
        _current = None
        _history.clear()


# ------------------------------------------------------------------ reporting


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile. Small samples are the norm here."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
    return ordered[index]


def summarize(turns: list[Timeline]) -> dict[str, dict[str, float]]:
    """Median, p90 and range per stage, across the turns given.

    Cold and warm are never summarised together -- a first turn pays for
    model loading that no later turn does, and averaging the two describes
    neither.
    """
    collected: dict[str, list[float]] = {}
    for turn in turns:
        for name, seconds in turn.as_dict().items():
            collected.setdefault(name, []).append(seconds)

    report: dict[str, dict[str, float]] = {}
    for name in list(STAGES) + list(DERIVED):
        values = collected.get(name)
        if not values:
            continue
        report[name] = {
            "n": float(len(values)),
            "median": median(values),
            "p90": percentile(values, 0.90),
            "min": min(values),
            "max": max(values),
        }
    return report


def bottlenecks(report: dict[str, dict[str, float]], top: int = 3):
    """The stages costing the most, by median, excluding the totals.

    Ranked on measurement rather than suspicion -- which is the whole point
    of this phase.
    """
    skip = {"total", *DERIVED}
    ranked = sorted(
        (
            (name, stats["median"])
            for name, stats in report.items() if name not in skip
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[:top]
