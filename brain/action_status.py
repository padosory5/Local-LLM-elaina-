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
    # 4F.1 made an offer a structured fact about a turn -- ``speech_act:
    # offer``, with something really parked behind it -- and the status
    # layer had no contentless words for one, so the unclear-consent path
    # fell back to a single hard-coded question.
    "offer",
    # Named because the brief names it, and resolved to execution_started
    # rather than duplicated: "Give me a sec, I'll check" is both.
    "commitment",
    # Said when something is dropped or closed. Both lived as hard-coded
    # lines elsewhere -- "You're welcome." was a single string, so every
    # thanks in a session got the identical reply.
    "declined",
    "closing",
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
        "One moment, I'll check.",
        "Let me look into that.",
        "I'll see what I can find.",
        "Give me a moment.",
        "Let me look that up.",
        "I'll find out.",
    ),
    "opening": (
        "Let me pull that up.",
        "Opening it now.",
        "One moment.",
        "Pulling it up.",
        "Bringing that up now.",
    ),
    "checking": (
        "Let me check.",
        "Checking now.",
        "I'll take a look.",
        "Give me a moment to check.",
        "Looking at it now.",
    ),
    "reading": (
        "Let me read through it.",
        "Give me a moment to read it.",
        "Reading it now.",
        "Going through it.",
        "I'm reading it now.",
    ),
    "comparing": (
        "Let me line those up.",
        "Give me a moment to compare.",
        "Comparing them now.",
        "Putting them side by side.",
    ),
    "editing": (
        "Let me work on that.",
        "Making the change now.",
        "Give me a moment.",
        "Changing that now.",
    ),
    "creating": (
        "Let me set that up.",
        "Putting that together.",
        "Give me a moment to make it.",
        "Building that now.",
    ),
    "continuing": (
        "Picking it back up.",
        "I know where we were.",
        "Continuing from there.",
        "Back to it.",
        "Carrying on now.",
    ),
    "analyzing": (
        "Let me take a proper look.",
        "Give me a moment to work through it.",
        "Looking at it now.",
        "I'm reading it.",
    ),
    "executing": (
        "Doing that now.",
        "Give me a moment.",
        "Starting on it.",
        "On it.",
        "Handling it now.",
    ),
}

# When she is not confident the work will help, the line should say so rather
# than promise a result she may not get.
_EN_HEDGED = (
    "That may be worth looking up. Let me check.",
    "I think I can find something better.",
    "Let me see if there is anything current on that.",
    "I'm not sure, but I'll have a look.",
)

_EN_PHASES = {
    "acknowledgement": (
        "Understood.",
        "Right.",
        "Noted.",
        "Got it.",
        "Okay.",
        "Yes.",
    ),
    "thinking": (
        "Let me think for a moment.",
        "Give me a moment.",
        "Working it out.",
        "One moment.",
    ),
    "recommendation": (
        "I can check that if you want.",
        "Shall I look into it?",
        "I can pull that up if it helps.",
        "I can look into that for you.",
    ),
    "permission_request": (
        "Shall I go ahead?",
        "Do you want me to?",
        "Should I do that?",
        "Okay to go ahead?",
    ),
    "offer": (
        "Shall I pull it up?",
        "I can look into that if you want.",
        "Shall I check?",
        "I can find out if that helps.",
        "Say the word and I'll look.",
    ),
    "declined": (
        "Understood, I'll leave it.",
        "No problem.",
        "Alright, leaving it.",
        "Noted.",
        "Fair enough.",
    ),
    "closing": (
        "You're welcome.",
        "Anytime.",
        "No problem.",
        "Of course.",
        "Glad that helped.",
    ),
    "success": (
        "Done.",
        "That's taken care of.",
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
    # "그건 알아보겠습니다." was here and it read stiff: 그건 points at
    # something, and this pool is generic, so the demonstrative referred
    # to nothing and just made the line formal. 한번 is what softens an
    # offer to go and look in Korean, and 검색 is the natural verb when
    # the question is one a search answers:
    #
    #     시애틀에서 인천까지 가는데 몇 시간 걸려?  ->  검색해보겠습니다.
    "searching": (
        "확인해 보겠습니다.",
        "네, 찾아보겠습니다.",
        "한번 알아보겠습니다.",
        "한번 찾아보겠습니다.",
        "검색해보겠습니다.",
        "바로 찾아보겠습니다.",
        "잠시만 기다려 주십시오.",
    ),
    "opening": (
        "바로 열겠습니다.",
        "네, 여는 중입니다.",
        "잠시만 기다려 주십시오.",
        "지금 띄우겠습니다.",
    ),
    "checking": (
        "확인해 보겠습니다.",
        "잠시만 확인하겠습니다.",
        "한번 보겠습니다.",
        "확인 중입니다.",
    ),
    "reading": (
        "읽어 보겠습니다.",
        "내용 확인하겠습니다.",
        "훑어보겠습니다.",
        "지금 읽는 중입니다.",
    ),
    "comparing": (
        "비교해 보겠습니다.",
        "두 가지 같이 보겠습니다.",
        "잠시만, 비교하겠습니다.",
        "나란히 놓고 보겠습니다.",
    ),
    "editing": (
        "수정하겠습니다.",
        "네, 고치겠습니다.",
        "잠시만, 손보겠습니다.",
        "지금 변경 중입니다.",
    ),
    "creating": (
        "만들어 보겠습니다.",
        "네, 준비하겠습니다.",
        "잠시만, 생성하겠습니다.",
        "지금 만드는 중입니다.",
    ),
    "continuing": (
        "이어서 하겠습니다.",
        "어디까지 했는지 알고 있습니다. 계속하겠습니다.",
        "하던 작업 계속하겠습니다.",
        "다시 이어가겠습니다.",
    ),
    "analyzing": (
        "제대로 보겠습니다.",
        "잠시만, 분석하겠습니다.",
        "차근차근 보고 있습니다.",
        "지금 살펴보는 중입니다.",
    ),
    "executing": (
        "네, 진행 중입니다.",
        "바로 하겠습니다.",
        "잠시만, 처리하겠습니다.",
        "지금 하겠습니다.",
    ),
}

_KO_HEDGED = (
    "찾아보면 나올 것 같습니다. 확인해 보겠습니다.",
    "더 나은 것이 있을 듯합니다. 한번 알아보겠습니다.",
    "확실하지는 않지만 한번 찾아보겠습니다.",
    "잠시만, 무엇이 있는지 보겠습니다.",
)

_KO_PHASES = {
    "acknowledgement": (
        "네.",
        "알겠습니다.",
        "확인했습니다.",
        "예.",
    ),
    "thinking": (
        "잠시 생각해 보겠습니다.",
        "잠시만 기다려 주십시오.",
        "생각 중입니다.",
        "조금만 기다려 주십시오.",
    ),
    "recommendation": (
        "원하시면 찾아보겠습니다.",
        "한번 알아볼까요?",
        "필요하시면 띄워 드리겠습니다.",
        "확인해 드릴까요?",
    ),
    "permission_request": (
        "그렇게 할까요?",
        "진행할까요?",
        "해도 되겠습니까?",
        "진행해도 되겠습니까?",
    ),
    "offer": (
        "띄워 드릴까요?",
        "원하시면 찾아보겠습니다.",
        "확인해 볼까요?",
        "필요하시면 알아보겠습니다.",
        "말씀만 주시면 찾아보겠습니다.",
    ),
    "declined": (
        "알겠습니다, 그대로 두겠습니다.",
        "네, 괜찮습니다.",
        "넘어가겠습니다.",
        "알겠습니다, 하지 않겠습니다.",
        "그러겠습니다.",
    ),
    "closing": (
        "아닙니다.",
        "천만의 말씀입니다.",
        "도움이 되었다면 다행입니다.",
        "네, 알겠습니다.",
        "괜찮습니다.",
    ),
    "success": (
        "완료했습니다.",
        "끝났습니다.",
        "처리했습니다.",
        "다 됐습니다.",
    ),
    "failure": (
        "그건 되지 않았습니다.",
        "실패했습니다.",
        "잘 되지 않습니다.",
        "그건 하지 못했습니다.",
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
    # What 4F.2 decided this turn is. ``continue`` means the work carries
    # on from results already in hand, and it should sound like it -- the
    # continuation bank existed but was reachable only through a hard-coded
    # set of two router labels, so picking a monitor back up out of a
    # shortlist announced itself as though it were a fresh search.
    mode: str = ""
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
        # What has been said out of each individual bank. Keyed by the bank
        # itself, which is a tuple of its lines and therefore its identity.
        self._recent_by_pool: dict[tuple[str, ...], deque[str]] = {}

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

    def speak_in(self, language: str) -> None:
        """Switch banks, and forget what was said in the old one.

        The recency memory is about *these* lines. Carrying it across a
        language change would bar a Korean line because an unrelated
        English one was said three turns ago, and would let the first line
        of the new language repeat freely.
        """
        language = self._known_language(language)
        if language == self.language:
            return
        self.language = language
        self.reset()

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
        self._recent_by_pool.clear()

    @property
    def recent(self) -> tuple[str, ...]:
        return tuple(self._recent)

    # ------------------------------------------------------------ choosing

    def _options(self, context: StatusContext) -> tuple[str, ...]:
        execution, phases, hedged = _BANKS[self.language]

        # A commitment and the start of execution are the same sentence:
        # "Give me a sec, I'll check" commits to the work *and* says it is
        # starting. The brief names them separately and 4F.1 distinguishes
        # them in structured state, but a second bank of the same lines
        # would be a duplicate that could drift, so the name resolves here
        # instead of being written out twice.
        phase = (
            "execution_started" if context.phase == "commitment"
            else context.phase
        )
        if phase != "execution_started":
            return tuple(phases.get(phase, ()))

        if context.confidence < self.HEDGE_BELOW_CONFIDENCE:
            return tuple(hedged)

        carrying_on = context.continuing or context.mode == "continue"
        action = "continuing" if carrying_on else context.action
        return tuple(execution.get(action, execution["executing"]))

    def _choose(self, options: tuple[str, ...]) -> str:
        """Prefer a line she has not just used, then a fresh opening.

        Each filter falls back to the wider pool rather than returning
        nothing, so a small bank still answers instead of going silent.

        Freshness is judged against *this* bank as well as the shared
        window. One global deque of five was letting a line come back
        almost immediately: "ok" got "Sure thing.", six turns of unrelated
        status lines flushed it out, and "thanks" got "Sure thing." again.
        A line only ever competes with the lines it could have been chosen
        instead of, so that is the memory that decides whether it is stale.
        """
        used_here = self._recent_by_pool.setdefault(options, deque(maxlen=1_000))
        fresh = [
            line for line in options
            if line not in self._recent and line not in used_here
        ]
        # Every line in this bank has now been said. Start the bank over
        # rather than going silent or repeating the most recent one.
        if not fresh and used_here:
            used_here.clear()
            fresh = [line for line in options if line not in self._recent]
        pool = fresh or list(options)

        varied = [
            line for line in pool
            if self._opening(line) not in self._recent_openings
        ]
        pool = varied or pool

        chosen = self._rng.choice(pool)
        used_here.append(chosen)
        return chosen

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
