"""What Elaina says while she is doing something, chosen locally.

The line that covers slow work must not itself be slow. Before this module,
every "On it." cost an Ollama round-trip through
:class:`~brain.brief_response.BriefResponseGenerator`, and when that call was
rejected or failed it fell back to one flat ten-line list -- so a web search,
a screen analysis, a project read and a Git commit all produced the same few
words. This picks from hand-written banks in microseconds instead, and picks
by *what she is actually about to do*.

Where the boundary sits
-----------------------

``BriefResponseGenerator`` owns lines that report an **outcome** and name a
**subject** -- "Got it, Spotify is open." Those are outcome-locked: a line
that claims success for a failure is a correctness bug, which is why they are
validated against the real result. This module owns the **contentless** lines
that carry no claim at all -- an acknowledgement, a status while work runs, a
generic done/failed when there is no subject worth naming. Nothing here can
misreport a result, because nothing here reports one.

Silence is a valid answer. :meth:`ActionStatusSelector.select` returns ``None``
for work fast enough that narrating it would only be noise; opening an app
finishes before the sentence would.
"""

from __future__ import annotations

import random
import re
from collections import deque
from dataclasses import dataclass


# What she is doing. These name the *shape* of the work, not the tool: a web
# search and a documentation lookup are both "searching", and should sound it.
ACTIONS = (
    "searching",
    "opening",
    "checking",
    "reading",
    "comparing",
    "editing",
    "creating",
    "continuing",
    "analyzing",
    "executing",
)

# Where in the exchange the line falls. Collapsing these was the original
# mistake: "I'm starting" and "that's done" are not the same kind of sentence,
# and a system that cannot tell them apart says the wrong one.
PHASES = (
    "acknowledgement",
    "thinking",
    "recommendation",
    "permission_request",
    "execution_started",
    "success",
    "failure",
)

# Roughly how long each kind of work runs. Anything under the selector's
# threshold is not worth announcing -- the result arrives first.
TYPICAL_SECONDS = {
    "searching": 5.0,
    "opening": 1.0,
    "checking": 3.0,
    "reading": 4.0,
    "comparing": 4.0,
    "editing": 6.0,
    "creating": 4.0,
    "continuing": 3.0,
    "analyzing": 6.0,
    "executing": 3.0,
}

# The intents that reach _announce_work_status, and the shape of work each one
# really is. This lived in chat_engine as a table of English sentences fed to
# a model; the sentences are gone because nothing generates from them now.
ACTION_BY_INTENT = {
    "web_search": "searching",
    "entity_correction": "searching",
    "screen_analysis": "analyzing",
    "project_question": "reading",
    "project_edit": "editing",
    "git_commit": "executing",
    "git_publish": "executing",
    "agent_create": "creating",
    "calendar_action": "creating",
}

# Intents that are a second attempt at something already under way. These get
# the continuation bank instead: "picking it back up" is true, and "let me
# check" pretends the first attempt never happened.
CONTINUING_INTENTS = frozenset({"entity_correction"})


# --------------------------------------------------------------- the banks
#
# Written to sound like her: young, casual, warm, unhurried. No emoji, no
# "certainly", no offer of further help tacked on the end. Each bank needs
# enough entries that the anti-repetition filter always has somewhere to go --
# four is the practical floor, since the last three openings are excluded.

_EN_EXECUTION = {
    "searching": (
        "Give me a sec, I'll check.",
        "Yeah, let me look into that.",
        "Alright, I'll see what I can find.",
        "One sec, I'll check.",
        "Let me look that up.",
        "Hang on, I'll find out.",
    ),
    "opening": (
        "Yeah, let me pull that up.",
        "Sure, opening it.",
        "Yep, one second.",
        "Pulling it up now.",
        "Okay, bringing that up.",
    ),
    "checking": (
        "Let me check.",
        "One sec, checking.",
        "I'll take a look.",
        "Give me a moment to check.",
        "Yeah, checking now.",
    ),
    "reading": (
        "Let me read through it.",
        "Give me a sec to look at it.",
        "Reading it now.",
        "Alright, going through it.",
        "One sec, I'm looking at it.",
    ),
    "comparing": (
        "Let me line those up.",
        "Give me a sec to compare.",
        "Comparing them now.",
        "Alright, putting them side by side.",
    ),
    "editing": (
        "Let me work on that.",
        "Okay, making the change.",
        "Give me a sec, I'll sort it.",
        "On it, changing that now.",
    ),
    "creating": (
        "Let me set that up.",
        "Okay, putting that together.",
        "Give me a sec, I'll make it.",
        "Alright, building that now.",
    ),
    "continuing": (
        "Yep, picking it back up.",
        "Yeah, I know where we were.",
        "Alright, continuing from there.",
        "Right, back to it.",
        "Okay, carrying on.",
    ),
    "analyzing": (
        "Let me take a proper look.",
        "Give me a sec to work through it.",
        "Okay, looking at it now.",
        "Hang on, I'm reading it.",
    ),
    "executing": (
        "Okay, doing that now.",
        "Sure, give me a sec.",
        "Alright, starting on it.",
        "On it.",
        "Yep, handling it.",
    ),
}

# When she is not confident the work will help, the line should say so rather
# than promise a result she may not get.
_EN_HEDGED = (
    "That might be worth looking up. Let me check.",
    "Yeah, I think I can find something better.",
    "Let me see if there's anything current on that.",
    "Not sure, but I'll have a look.",
)

_EN_PHASES = {
    "acknowledgement": (
        "Yeah.",
        "Sure.",
        "Okay.",
        "Got it.",
        "Alright.",
        "Mm-hm.",
    ),
    "thinking": (
        "Let me think for a sec.",
        "Hm, give me a moment.",
        "Thinking about it.",
        "Hang on, let me work that out.",
    ),
    "recommendation": (
        "I can check that if you want.",
        "Want me to look into it?",
        "I could pull that up, if it helps.",
        "Happy to dig into that if you'd like.",
    ),
    "permission_request": (
        "Want me to go ahead?",
        "Should I do that?",
        "Want me to?",
        "Okay to go ahead?",
    ),
    "success": (
        "Done.",
        "All set.",
        "That's done.",
        "Finished.",
    ),
    "failure": (
        "That didn't work.",
        "I couldn't get that done.",
        "That one failed.",
        "No luck with that.",
    ),
}

_KO_EXECUTION = {
    "searching": (
        "잠깐만, 확인해볼게.",
        "응, 한번 찾아볼게.",
        "그거 좀 알아볼게.",
        "잠시만, 찾아볼게.",
        "바로 찾아볼게.",
    ),
    "opening": (
        "응, 바로 열어줄게.",
        "그래, 열고 있어.",
        "잠깐만, 띄울게.",
        "지금 열게.",
    ),
    "checking": (
        "확인해볼게.",
        "잠깐만 볼게.",
        "한번 볼게.",
        "응, 확인 중이야.",
    ),
    "reading": (
        "읽어볼게, 잠깐만.",
        "내용 좀 볼게.",
        "쭉 훑어볼게.",
        "지금 읽고 있어.",
    ),
    "comparing": (
        "비교해볼게.",
        "둘 다 놓고 볼게, 잠깐만.",
        "잠깐만, 비교해볼게.",
        "나란히 놓고 볼게.",
    ),
    "editing": (
        "고쳐볼게, 잠깐만.",
        "응, 수정할게.",
        "잠깐만, 손볼게.",
        "지금 바꾸고 있어.",
    ),
    "creating": (
        "만들어볼게, 잠깐만.",
        "응, 준비할게.",
        "잠깐만, 만들게.",
        "지금 만드는 중이야.",
    ),
    "continuing": (
        "응, 이어서 할게.",
        "어디까지 했는지 알아. 계속할게.",
        "그래, 하던 거 계속할게.",
        "다시 이어갈게.",
    ),
    "analyzing": (
        "제대로 좀 볼게.",
        "잠깐만, 분석해볼게.",
        "천천히 보고 있어.",
        "지금 살펴보는 중이야.",
    ),
    "executing": (
        "응, 하고 있어.",
        "바로 할게.",
        "잠깐만, 처리할게.",
        "지금 할게.",
    ),
}

_KO_HEDGED = (
    "찾아보면 나올 것 같은데, 한번 볼게.",
    "더 나은 게 있을 것 같아. 확인해볼게.",
    "확실하진 않은데 한번 알아볼게.",
    "잠깐만, 뭐가 있는지 볼게.",
)

_KO_PHASES = {
    "acknowledgement": (
        "응.",
        "그래.",
        "알았어.",
        "오케이.",
    ),
    "thinking": (
        "잠깐 생각 좀.",
        "음, 잠깐만.",
        "생각 중이야.",
        "조금만 기다려봐.",
    ),
    "recommendation": (
        "원하면 찾아볼 수 있어.",
        "한번 알아볼까?",
        "필요하면 띄워줄게.",
        "내가 확인해줄까?",
    ),
    "permission_request": (
        "그렇게 할까?",
        "진행할까?",
        "해도 될까?",
        "그럼 할게, 괜찮지?",
    ),
    "success": (
        "다 됐어.",
        "끝났어.",
        "완료.",
        "그거 끝냈어.",
    ),
    "failure": (
        "그건 안 됐어.",
        "실패했어.",
        "잘 안 되네.",
        "그건 못 했어.",
    ),
}

_BANKS = {
    "en": (_EN_EXECUTION, _EN_PHASES, _EN_HEDGED),
    "ko": (_KO_EXECUTION, _KO_PHASES, _KO_HEDGED),
}

DEFAULT_LANGUAGE = "en"


@dataclass(frozen=True)
class StatusContext:
    """Everything the choice depends on, and nothing else.

    ``action`` is what she is doing; ``phase`` is where in the exchange the
    line falls. ``expected_seconds`` decides whether a line is worth saying at
    all -- leave it at zero to use the typical duration for the action.
    """

    action: str = "executing"
    phase: str = "execution_started"
    subject: str = ""
    continuing: bool = False
    expected_seconds: float = 0.0
    confidence: float = 1.0
    force: bool = False

    @property
    def duration(self) -> float:
        if self.expected_seconds > 0:
            return float(self.expected_seconds)
        return TYPICAL_SECONDS.get(self.action, 3.0)


class ActionStatusSelector:
    """Pick a status line locally, and do not repeat yourself.

    No model call, no network, no shared state with a turn. Construct one per
    ChatEngine and let it remember what it has said recently: repetition is
    only visible across turns, so the memory has to outlive them.
    """

    # The last few lines are barred outright. Openings are barred over a
    # shorter window, because "Let me ..." twice in a row reads as repetition
    # even when the rest of the sentence differs.
    RECENT_LINES = 5
    RECENT_OPENINGS = 3

    # Work shorter than this finishes before the sentence would land, so
    # announcing it only adds noise.
    MIN_ANNOUNCED_SECONDS = 1.5

    # Below this, she should not promise a result she may not get.
    HEDGE_BELOW_CONFIDENCE = 0.55

    def __init__(
        self,
        *,
        language: str = DEFAULT_LANGUAGE,
        min_seconds: float | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.language = self._known_language(language)
        self.min_seconds = (
            self.MIN_ANNOUNCED_SECONDS if min_seconds is None
            else float(min_seconds)
        )
        self._rng = rng if rng is not None else random.Random()
        self._recent: deque[str] = deque(maxlen=self.RECENT_LINES)
        self._recent_openings: deque[str] = deque(maxlen=self.RECENT_OPENINGS)

    # ------------------------------------------------------------- public

    def select(self, context: StatusContext) -> str | None:
        """The line to say now, or ``None`` when saying nothing is better."""
        if not self.should_announce(context):
            return None

        options = self._options(context)
        if not options:
            return None

        chosen = self._choose(options)
        self._remember(chosen)
        return chosen

    def should_announce(self, context: StatusContext) -> bool:
        """Whether this work is slow enough to be worth narrating."""
        if context.force:
            return True
        # A result is not "work in progress" -- it is the thing the user was
        # waiting for, and is always worth saying.
        if context.phase in {"success", "failure", "permission_request"}:
            return True
        return context.duration >= self.min_seconds

    def reset(self) -> None:
        """Forget what was said recently. For tests and session restarts."""
        self._recent.clear()
        self._recent_openings.clear()

    @property
    def recent(self) -> tuple[str, ...]:
        return tuple(self._recent)

    # ------------------------------------------------------------ choosing

    def _options(self, context: StatusContext) -> tuple[str, ...]:
        execution, phases, hedged = _BANKS[self.language]

        if context.phase != "execution_started":
            return tuple(phases.get(context.phase, ()))

        if context.confidence < self.HEDGE_BELOW_CONFIDENCE:
            return tuple(hedged)

        action = "continuing" if context.continuing else context.action
        return tuple(execution.get(action, execution["executing"]))

    def _choose(self, options: tuple[str, ...]) -> str:
        """Prefer a line she has not just used, then a fresh opening.

        Each filter falls back to the wider pool rather than returning
        nothing, so a small bank still answers instead of going silent.
        """
        fresh = [line for line in options if line not in self._recent]
        pool = fresh or list(options)

        varied = [
            line for line in pool
            if self._opening(line) not in self._recent_openings
        ]
        pool = varied or pool

        return self._rng.choice(pool)

    def _remember(self, line: str) -> None:
        self._recent.append(line)
        opening = self._opening(line)
        if opening:
            self._recent_openings.append(opening)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _opening(line: str) -> str:
        """The first couple of words, normalized, as a repetition signature.

        Korean has no spaces between a particle and its stem, so falling back
        to leading characters keeps the check meaningful in both languages.
        """
        words = re.findall(r"[\w']+", str(line).casefold(), flags=re.UNICODE)
        if not words:
            return ""
        return " ".join(words[:2])

    @staticmethod
    def _known_language(language: str) -> str:
        key = str(language or "").strip().casefold()
        return key if key in _BANKS else DEFAULT_LANGUAGE


def action_for_intent(intent: str) -> str | None:
    """The shape of work an intent implies, or ``None`` to stay quiet."""
    return ACTION_BY_INTENT.get(str(intent or "").strip())


def is_continuation(intent: str) -> bool:
    """Whether this intent resumes work already under way."""
    return str(intent or "").strip() in CONTINUING_INTENTS
