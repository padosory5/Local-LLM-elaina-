"""How to answer this turn, from what this conversation has already been
through. In shadow: decided and recorded, never acted on.

Diagnosed on the simulated learners (pilot 4, docs/TURN_MOVE_SHADOW.md):
every wrong or repeated explanation was in the model's own draft, and the
way each turn was answered was chosen from the router's label of the
latest sentence alone. Nothing consulted what the person had already asked
or what she had already said:

* the same question asked four times got the same "explain" goal four
  times, the model copied its previous answer word for word, and the
  repetition was only noticed after the draft, by a step that may reword
  and nothing more;
* "how does that become 23?", a challenge to her own number, got the same
  "explain" goal, and the model said it again;
* a sub-question ("학습 데이터가 뭐야?") counted as a new subject, so the
  conversation was dropped from the prompt altogether.

So one owner for the move, decided before the draft, from a small record of
the conversation:

* ``first``          -- not a follow-up, or nothing to go on yet.
* ``continue``       -- a follow-up on a new point.
* ``differently``    -- they asked this before: the earlier explanation did
                        not land, and is not to be reused.
* ``check``          -- they question something she said: check it first.
* ``not_explaining`` -- not an explanation turn, and neither of the above.

The signals, with no model call:

* **Asked again** -- the question is close in meaning to an earlier one of
  theirs: cosine similarity of the sentence embeddings (bge-m3, the model
  memory already loads) at least ``ASKED_AGAIN_SIMILARITY``. v1 compared
  content words and caught 0 of 12 re-asks on a fresh run (pilot 5): people
  paraphrase. With no embedder, the signal is recorded as unavailable.
* **Challenged** -- the router reads the turn as a correction, or the
  message quotes at least two of her numbers back and sets one of its own
  against them.

Both are read before the turn's budget: a re-ask the router files as a
short answer is still a re-ask (pilot 5, "그거 답이 아니잖아" read as a
correction and given a value budget).

The thread is the session's recent exchanges. It is cleared only when the
router says the topic moved, not when it calls a turn "not a follow-up":
that flag was wrong on a re-ask in pilot 5, and whether a turn still belongs
to the thread could not be read from similarity either (off-topic probes
reached 0.62, real sub-questions went down to 0.42). Keeping it is cheap
because both signals are narrow.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Callable

FIRST = "first"
CONTINUE = "continue"
DIFFERENTLY = "differently"
CHECK = "check"
NOT_EXPLAINING = "not_explaining"

EXPLAINING_BUDGETS = ("explain", "elaborate")

# Whether the move is acted on: config.yaml responses.turn_move, overridden
# by ELAINA_TURN_MOVE.
#
# * "off"  -- recorded only (the shadow).
# * "on"   -- a ``differently`` move owns its turn: its line is the goal.
#             A/B, 2026-10-02: no help. The 8B's draft was her earlier
#             answer again on 5 of 7 such turns (docs/TURN_MOVE_AB.md).
# * "deep" -- the same, and the turn is written by the larger model
#             (responses.turn_move_model / ELAINA_TURN_MOVE_MODEL). Replayed
#             offline, the 8B copied her earlier answer in 18 of 28 drafts
#             under the move's line and the 27B in 0 of 28.
#
# ``check`` is never acted on: in pilot 6 none of its flags were right.
MODE_ENV = "ELAINA_TURN_MOVE"
MODEL_ENV = "ELAINA_TURN_MOVE_MODEL"
OFF = "off"
ON = "on"
DEEP = "deep"
MODES = (OFF, ON, DEEP)
ACTED_ON = (DIFFERENTLY,)

# Fixed before pilot 6 from pilots 4 and 5 (hand labels, one labeller):
# 7 of 14 re-asks at or above it, 0 of 80 other follow-ups.
ASKED_AGAIN_SIMILARITY = 0.80
# A challenge to a figure quotes the claim back -- at least this many of her
# numbers -- and sets one of its own against it. One shared number was too
# few: "map a 32-bit float to an 8-bit integer" names one of hers and asks how.
CHALLENGE_NUMBERS = 2
THREAD_LIMIT = 12
QUOTE_CHARS = 220

EMBEDDING = "embedding"
UNAVAILABLE = "unavailable"

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SENTENCE = re.compile(r"(?<=[.!?。])\s+")
_WORD = re.compile(r"[가-힣]{2,}|[a-z]{3,}", re.IGNORECASE)


def mode(config) -> str:
    """ELAINA_TURN_MOVE, else config.yaml ``responses.turn_move``, else off.
    An unknown value is said out loud and read as off."""
    value = os.environ.get(MODE_ENV)
    if value is None:
        try:
            value = config.get("responses", "turn_move", default=OFF, required=False)
        except Exception:
            value = OFF
    # YAML 1.1 reads a bare off / on as a boolean.
    if value is False:
        value = OFF
    elif value is True:
        value = ON
    chosen = str(value if value is not None else OFF).strip().lower()
    if chosen not in MODES:
        print(f"[Turn Move] turn_move={value!r} is not one of {MODES}; running with it off")
        return OFF
    return chosen


def settings(config) -> tuple[str, str]:
    """(mode, the model that writes a ``differently`` turn under "deep").

    "deep" with no model named is said out loud and read as off: a run must
    not measure the 8B under the larger model's name.
    """
    chosen = mode(config)
    model = os.environ.get(MODEL_ENV)
    if model is None:
        try:
            model = config.get("responses", "turn_move_model", default="", required=False)
        except Exception:
            model = ""
    model = str(model or "").strip()
    if chosen == DEEP and not model:
        print(f"[Turn Move] turn_move is {DEEP!r} but no model is named "
              f"(responses.turn_move_model / {MODEL_ENV}); running with it off")
        return OFF, ""
    return chosen, (model if chosen == DEEP else "")


# How response_messages.build_personality_messages introduces the question
# a factual prompt answers.
CURRENT_MESSAGE = "CURRENT USER MESSAGE\n"


def with_their_words(prompt: str, *, shown: str, said: str) -> tuple[str, bool]:
    """The drafting prompt with what the person said as its current message,
    in place of the router's reading of it. (prompt, whether it changed.)

    On a knowledge question or a search the writer is shown the router's
    normalized request. On a question asked again that reading is the wrong
    thing to answer: it is the person's own wording that matched their
    earlier question, and the router rewrites it. Measured (A/B 2,
    docs/TURN_MOVE_AB.md): "그거 그냥 인터넷에서 실시간으로 검색해온
    거잖아?", asked for the third and fourth time, reached the 27B as "Are
    training data just scraped internet content?" and "How does the
    training data for AI models work?" -- and it answered those, correctly.

    Only where the move owns the turn. A prompt built from their words
    already (the conversational path) is left as it is.
    """
    shown, said = str(shown or "").strip(), str(said or "").strip()
    marker = f"{CURRENT_MESSAGE}{shown}\n"
    if not shown or not said or shown == said or marker not in prompt:
        return prompt, False
    return prompt.replace(marker, f"{CURRENT_MESSAGE}{said}\n", 1), True


def numbers(text: str) -> set[str]:
    return {value.replace(",", "") for value in _NUMBER.findall(str(text or ""))}


def _quote(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS].rsplit(" ", 1)[0] + " ..."


def _unit(vector) -> tuple | None:
    if vector is None:
        return None
    values = [float(x) for x in vector]
    norm = math.sqrt(sum(x * x for x in values))
    return tuple(x / norm for x in values) if norm else None


def _cosine(a: tuple, b: tuple) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Exchange:
    said: str
    reply: str = ""
    vector: tuple | None = None         # their words, embedded and normalised


@dataclass(frozen=True)
class Move:
    move: str
    thread_turns: int = 0               # earlier exchanges on record
    asked_before: tuple = ()            # 1-based positions of the same question
    similarity: float | None = None     # the closest earlier question of theirs
    asked_signal: str = EMBEDDING       # or UNAVAILABLE
    challenged: str = ""                # "number" | "correction" | ""
    quoted: str = ""                    # what the move points at: her earlier answer or claim
    their_numbers: tuple = ()           # for a numeric challenge, what they set against hers

    @property
    def attempts(self) -> int:
        """How many times this point has been explained already."""
        return len(self.asked_before)

    def instruction(self, language: str = "en") -> str:
        """The line this move would put in the prompt. Unused in shadow."""
        korean = str(language or "").lower().startswith("ko")
        if self.move == DIFFERENTLY:
            count = self.attempts + 1
            if korean:
                return (f"같은 질문을 {count}번째 하셨습니다. 앞의 설명은 전달되지 않았으니 다시 쓰지도, "
                        f"표현만 바꾸지도 않습니다: \"{self.quoted}\". 이미 알고 계신 것에서 시작해서 "
                        "한 걸음씩 설명합니다.")
            return (f"This is the {count}{_ordinal(count)} time they have asked this, so the earlier "
                    f"explanation did not land. Do not reuse it or reword it: \"{self.quoted}\". Start "
                    "from something they already know and take one small step at a time.")
        if self.move == CHECK:
            if korean:
                return (f"앞서 하신 말씀 중 이 부분을 문제 삼고 계십니다: \"{self.quoted}\". 무엇보다 먼저 "
                        "그것이 맞는지 확인합니다. 틀렸다면 틀렸다고 분명히 말하고 바르게 고칩니다. "
                        "맞다면 왜 맞는지 한 단계로 쉽게 보여 줍니다.")
            return (f"They are questioning something you said: \"{self.quoted}\". Check it before "
                    "anything else. If it was wrong, say so plainly and give the right version; if it "
                    "was right, show why in one simple step.")
        return ""

    def trace(self, *, language: str = "en", production_goal: str = "",
              history_inherited: bool | None = None) -> dict:
        """What the shadow records for the turn."""
        record = {"move": self.move, "thread_turns": self.thread_turns,
                  "attempts": self.attempts, "asked_before": list(self.asked_before),
                  "similarity": None if self.similarity is None else round(self.similarity, 3),
                  "asked_signal": self.asked_signal, "challenged": self.challenged,
                  "production_goal": production_goal, "instruction": self.instruction(language)}
        if self.their_numbers:
            record["their_numbers"] = list(self.their_numbers)
        if history_inherited is not None:
            record["history_inherited"] = history_inherited
        return record


def _ordinal(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


@dataclass
class Record:
    """The session's recent exchanges, for deciding each turn's move.

    ``embed`` turns a sentence into a vector (memory's bge-m3 in her own
    use); without one, "asked again" is recorded as unavailable.
    """

    embed: Callable | None = None
    thread: list = field(default_factory=list)   # [Exchange]
    _pending: tuple = ()                         # (said, vector) decided, not yet recorded

    def decide(self, said: str, *, is_follow_up: bool, topic_shift: bool, speech_act: str,
               budget: str) -> Move:
        """The move for this turn, from the exchanges on record."""
        if topic_shift:
            self.thread = []
        earlier = list(self.thread)
        vector = self._vector(said)
        self._pending = (said, vector)
        count = len(earlier)

        if earlier:
            previous = earlier[-1].reply
            if str(speech_act or "").strip().lower() == "correction":
                return Move(CHECK, thread_turns=count, challenged="correction",
                            quoted=_quote(_closest_sentence(previous, said)),
                            asked_signal=self._signal())
            theirs, hers = numbers(said), numbers(previous)
            if len(theirs & hers) >= CHALLENGE_NUMBERS and theirs - hers:
                claim = next((s for s in _SENTENCE.split(previous) if numbers(s) & theirs), previous)
                return Move(CHECK, thread_turns=count, challenged="number", quoted=_quote(claim),
                            their_numbers=tuple(sorted(theirs - hers)), asked_signal=self._signal())

        similarity, before = None, ()
        if vector is not None:
            scored = [(position, _cosine(vector, exchange.vector))
                      for position, exchange in enumerate(earlier, 1) if exchange.vector is not None]
            if scored:
                similarity = max(score for _, score in scored)
                before = tuple(position for position, score in scored
                               if score >= ASKED_AGAIN_SIMILARITY)
        if before:
            return Move(DIFFERENTLY, thread_turns=count, asked_before=before, similarity=similarity,
                        quoted=_quote(earlier[before[-1] - 1].reply))
        if budget not in EXPLAINING_BUDGETS:
            return Move(NOT_EXPLAINING, thread_turns=count, similarity=similarity,
                        asked_signal=self._signal())
        if not earlier or not is_follow_up:
            return Move(FIRST, thread_turns=count, similarity=similarity, asked_signal=self._signal())
        return Move(CONTINUE, thread_turns=count, similarity=similarity, asked_signal=self._signal())

    def record(self, said: str, reply: str) -> None:
        """What was said and answered, once the turn is over."""
        said = str(said or "")
        vector = self._pending[1] if self._pending and self._pending[0] == said else self._vector(said)
        self._pending = ()
        self.thread.append(Exchange(said, str(reply or ""), vector))
        del self.thread[:-THREAD_LIMIT]

    def _signal(self) -> str:
        return EMBEDDING if self.embed is not None else UNAVAILABLE

    def _vector(self, text: str):
        if self.embed is None or not str(text or "").strip():
            return None
        try:
            return _unit(self.embed(text))
        except Exception as error:  # a shadow never takes a turn down
            print(f"[Turn Move] could not embed: {type(error).__name__}: {error}")
            return None


def _closest_sentence(reply: str, said: str) -> str:
    """The sentence of her reply that what they said is most about."""
    sentences = [s for s in _SENTENCE.split(str(reply or "").strip()) if s.strip()]
    if not sentences:
        return ""
    wanted = {w.casefold() for w in _WORD.findall(said)} | numbers(said)
    return max(sentences, key=lambda s: len(({w.casefold() for w in _WORD.findall(s)} | numbers(s))
                                            & wanted))
