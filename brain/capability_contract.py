"""What a capability takes, what it returns, and how it is allowed to fail.

:mod:`brain.capabilities` already answers *what can I do* and *may I do it
right now*. It does not answer the three questions a caller actually needs
before and after running one:

===========================  ==============================================
what does it need from me    ``summary`` is prose. A caller cannot ask
                             "what is missing" and get a checkable answer.
what do I get back           a string, phrased at the call site, which the
                             model then re-phrases. Structure is lost at
                             the first step and guessed at the second.
how can it fail              nowhere. Each call site invents a sentence.
===========================  ==============================================

That last row is not a tidiness problem, and it is the reason this module
leads with failure rather than with success. Counted across ``brain/`` at
the start of A4: **43 user-facing strings interpolate a Python exception
class name.** They read, out loud, in her voice:

    I couldn't complete that web search: ConnectionError:
    HTTPSConnectionPool(host='duckduckgo.com', port=443)

Three things are wrong with that sentence and only one of them is cosmetic.
It says nothing the person can act on; it is written in English regardless
of the language the turn is in, so a Korean turn ends in a foreign-language
stack fragment; and *the caller chose those words at the moment it caught
the exception*, which means the failure vocabulary of the product is the
union of twenty-two independent guesses.

So a failure becomes a declared thing with a name, a sentence in each
language, and a fix where one exists. The exception keeps existing -- it
goes to ``detail``, which is logged and never spoken.

:func:`leaks_internals` is the enforcement and the metric at once, per Rule
1 of the milestone plan: the report scores replies with the same function
the reply path runs. A number that measures something the product does not
enforce drifts away from it, and then a green report and a leaking sentence
are true at the same time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

# Every sentence in this module exists in both languages, and the registry
# test below refuses a contract that is missing one. See Rule 4.
LANGUAGES = ("en", "ko")

ENGLISH = "en"
KOREAN = "ko"


# --------------------------------------------------------------- internals
#
# Vocabulary that describes how Elaina is built rather than what happened.
# Narrow on purpose. Two things that look like they belong here do not:
#
#   config.yaml -- the person owns that file and edits it. Telling them
#                  which switch to flip is the most actionable sentence a
#                  blocked capability can produce.
#   a URL or an app name -- that is the subject of the turn, not plumbing.
#
# What is left is the set of words that are true, and useless: they name a
# component the person never chose and cannot act on.

# The shape, not the word. Anchored on the suffix rather than a list --
# the list is whatever the next library raises -- but bounded by the
# punctuation that marks a class name as *debris* rather than as subject.
#
# The difference matters, because Elaina reads this project's own code:
#
#     I couldn't reach it: ConnectionError: max retries    <- debris
#     That function raises a ValueError on an empty list.   <- the answer
#
# Every one of the forty-three leaking sites produced the first shape,
# because they were all written as f"...: {type(error).__name__}: {error}".
# A version of this rule that matched the bare word would have started
# censoring project answers the day project access was used in anger.
_CLASS_NAME = r"[A-Z][A-Za-z0-9]*(?:Error|Exception|Warning|Timeout|Failure)"

_EXCEPTION_NAME = re.compile(
    # Introduced by a colon or a bracket, or trailing a colon of its own.
    r"(?:[:(\[]\s*)" + _CLASS_NAME + r"\b"
    r"|\b" + _CLASS_NAME + r"\s*:",
)

_TRACEBACK = re.compile(
    r"Traceback \(most recent call last\)"
    r"|\bFile \"[^\"]+\", line \d+"
    r"|\bbrain\.[a-z_]+\.[a-z_]+|\bvoice\.[a-z_]+\.[a-z_]+",
)

# Stricter, and only for the static scan of authored sentences. A source
# file naming another source file inside a string the user will hear is a
# leak with no innocent reading; the same words coming out of a project
# question at runtime are the answer to it.
_AUTHORED_MODULE_PATH = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\.py\b|\bbrain\.[a-z_]+|\bagents\.[a-z_]+",
)

_CONNECTION_DEBRIS = re.compile(
    r"\bHTTPS?ConnectionPool\b"
    r"|\bhost='[^']*'"
    r"|\bport=\d+"
    r"|\[Errno \d+\]"
    r"|\bstatus[_ ]code[= ]\d{3}\b",
    re.IGNORECASE,
)

# Components the person never chose and cannot act on. "Electron" is the
# one that mattered: ten sentences told the user to review a proposal "in
# Electron". What they see is a window. Measured live, the reply to a git
# proposal was "A Git proposal is visible in Electron" -- a true sentence
# naming a JavaScript framework to someone looking at a button.
_FRAMEWORK_NAME = re.compile(
    r"\bElectron\b|\bOllama\b|\bWhisper\b|\bWebSocket\b|\bPiper\b"
    r"|\bqwen[\w.:-]*|\bstdout\b|\bstderr\b|\bsubprocess\b",
    re.IGNORECASE,
)

# Sending the person to a developer surface. Found by the live probe that
# checked whether registering the three new capabilities had changed
# anything:
#
#     You said: can you commit changes to git for me?
#     Elaina:   I can't commit changes to Git right now. The Git Agent says
#               nothing was staged, committed, or pushed. Check the console
#               error for details.
#
# There is no console in front of the person -- they are talking to her,
# and the last sentence is the software asking to be debugged. Matched as
# a phrase rather than as the bare word, so a question about a games
# console or a terminal application is untouched.
_DEVELOPER_SURFACE = re.compile(
    r"\b(?:check|see|look\s+at|read|open)\s+(?:the\s+)?"
    r"(?:console|terminal|log|logs|log\s+file|stack\s+trace|debug\s+output)\b"
    r"|\bconsole\s+(?:error|output|log)\b"
    r"|\bin\s+the\s+(?:console|terminal|logs?)\b",
    re.IGNORECASE,
)

_INTERNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("exception_name", _EXCEPTION_NAME),
    ("traceback", _TRACEBACK),
    ("connection_debris", _CONNECTION_DEBRIS),
    ("framework_name", _FRAMEWORK_NAME),
    ("developer_surface", _DEVELOPER_SURFACE),
)


def leaks_internals(text: str, *, authored: bool = False) -> tuple[str, str]:
    """The first internal fragment in this sentence, and what kind it is.

    ``("", "")`` when the sentence is safe to say. Used two ways, and the
    fact that it is the same function both times is the point: the reply
    path runs it before speaking, and the A4 report runs it to score.

    ``authored`` is the one place the two differ, and the difference is
    deliberate. The static scan reads sentences a developer *wrote into
    the source*, where naming a module is a leak with no innocent reading.
    At runtime the same words can be the answer to a question about this
    project's code, so the module-path rule is not applied there. Erring
    the other way would mean the reply path quietly deleting the substance
    of every project answer.
    """
    said = str(text or "")
    if not said.strip():
        return "", ""
    checks = _INTERNALS
    if authored:
        checks = checks + (("module_path", _AUTHORED_MODULE_PATH),)
    for kind, pattern in checks:
        found = pattern.search(said)
        if found:
            return found.group(0).strip(" :(["), kind
    return "", ""


# Sentence ends, in both scripts. Korean declaratives end on a period like
# English ones -- 습니다. -- so the split is shared, and the Korean-only
# case is the one with no space after it.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?])(?=[가-힣])")


def redact_internals(text: str) -> str:
    """The same sentence with any unsayable clause removed.

    The runtime half of :func:`leaks_internals`, and the reason the static
    scan is not the whole phase. Fixing the fourteen authored sentences
    stops Elaina from *choosing* those words; it does nothing about the
    routes nobody authored -- a model echoing an exception back out of a
    tool-result prompt, a library's own message arriving through an MCP
    tool, a future call site written in a hurry.

    Drops the whole clause rather than snipping the fragment out of it.
    "I couldn't reach the site: ConnectionError" minus two words is "I
    couldn't reach the site:", which is a sentence that stops mid-promise.
    The detail is not lost -- it is in the log, where it was always the
    only useful place for it.
    """
    said = str(text or "").strip()
    if not said:
        return ""
    fragment, _ = leaks_internals(said)
    if not fragment:
        return said
    kept = [
        part for part in _SENTENCE_END.split(said)
        if part.strip() and not leaks_internals(part)[0]
    ]
    return " ".join(part.strip() for part in kept).strip()


# ------------------------------------------------------------------ inputs


@dataclass(frozen=True)
class Need:
    """One typed input a capability cannot run without.

    ``asks`` is not a prompt for the model. It is the sentence Elaina says
    when the input is missing, which is why it is written per language and
    per capability rather than generated -- "which app?" and "which site?"
    are different questions and a generic one is neither.
    """

    key: str
    #: What kind of value satisfies it. Checkable, unlike a prose summary.
    kind: str
    asks: Mapping[str, str] = field(default_factory=dict)
    #: Whether the router may fill this from the turn, or the person must
    #: say it. ``False`` means never infer -- a calendar event's time is
    #: not something to guess at.
    inferable: bool = True

    def ask_in(self, language: str = ENGLISH) -> str:
        return self.asks.get(language) or self.asks.get(ENGLISH, "")


KINDS = (
    "text",       # free words: a question, a goal, a fact to remember
    "query",      # something to search for
    "target",     # a site, a page, a URL
    "app",        # a native application name
    "path",       # a file or folder
    "datetime",   # a point in time
    "region",     # a part of the screen
)


# ---------------------------------------------------------------- failures


@dataclass(frozen=True)
class Failure:
    """One named way a capability fails, and what she says about it.

    ``says`` carries no exception name, no host, no framework. What went
    wrong technically lives in :attr:`CapabilityResult.detail` and reaches
    the log. The person gets the half they can act on.
    """

    code: str
    #: For us, in the log. Never spoken.
    means: str
    says: Mapping[str, str] = field(default_factory=dict)
    #: What the person can do about it, when there is something. Kept
    #: separate from ``says`` so a failure can be reported without a fix
    #: rather than inventing one.
    fix: Mapping[str, str] = field(default_factory=dict)
    #: Whether trying the same thing again could plausibly work. A5 will
    #: read this; it is declared here because the capability is what knows.
    retryable: bool = False

    def say_in(self, language: str = ENGLISH) -> str:
        line = self.says.get(language) or self.says.get(ENGLISH, "")
        hint = self.fix.get(language) or self.fix.get(ENGLISH, "")
        return f"{line} {hint}".strip() if hint else line


# ----------------------------------------------------------------- results


@dataclass(frozen=True)
class CapabilityResult:
    """What a capability hands back, with the sayable half separated out.

    The A4 exit criterion is "no tool result reaches the reply as
    unstructured prose", and this is the shape that makes it true. Before
    it, a result was a string built at the call site, so the only way to
    know whether a number in a reply came from a tool or from the model was
    to have watched it happen.

    ``detail`` is the concession that makes the rest honest: the exception
    text is genuinely useful and genuinely unspeakable, so it has a field
    that never reaches speech instead of being dropped or leaked.
    """

    capability: str
    ok: bool = True
    #: A declared failure code from this capability's contract. Empty on
    #: success, and validated -- an undeclared code is a contract breach,
    #: not a new failure mode invented at the call site.
    failure: str = ""
    #: One short sentence of what happened, already free of internals.
    summary: str = ""
    #: The structured payload. Whatever the capability found, as data.
    facts: Mapping[str, object] = field(default_factory=dict)
    #: Where the facts came from, for A6.
    source: str = ""
    #: The technical truth. Logged, never spoken.
    detail: str = ""

    def spoken(self, language: str = ENGLISH) -> str:
        """The sentence for the person, in their language.

        Falls back through the declared failure line rather than through
        the raw summary, so a call site that forgets to phrase something
        produces the contract's sentence and not silence.

        Redacted on the way out, even though ``detail`` is the field
        exceptions are supposed to go in. The type should be honest by
        construction rather than by everyone remembering which field is
        which -- and ``summary`` is written at call sites, which is where
        the forty-three leaks came from in the first place.
        """
        if self.ok:
            return redact_internals(self.summary)
        failure = failure_for(self.capability, self.failure)
        if failure is not None:
            return failure.say_in(language)
        return redact_internals(self.summary)

    def log_line(self) -> str:
        head = "ok" if self.ok else f"failed:{self.failure or 'undeclared'}"
        tail = f" -- {self.detail}" if self.detail else ""
        return f"[{self.capability}] {head}{tail}"

    def as_trusted_result_text(self) -> str:
        """The prompt block, for the paths that still let the model phrase.

        Deliberately not the same as :meth:`spoken`. The model is given the
        structured facts, so it phrases *from data* rather than re-phrasing
        a sentence someone already wrote -- which is how a raw
        accessibility tree once reached speech.
        """
        lines = [self.summary] if self.summary else []
        for key, value in self.facts.items():
            lines.append(f"{key}: {value}")
        if self.source:
            lines.append(f"source: {self.source}")
        return "\n".join(lines)


# --------------------------------------------------------------- contracts


@dataclass(frozen=True)
class Contract:
    """One capability's declared interface."""

    capability_id: str
    needs: tuple[Need, ...] = ()
    #: What the facts look like on success. Keys, not a schema library --
    #: the point is that a caller can check them, not that they are typed
    #: all the way down.
    returns: tuple[str, ...] = ()
    failures: tuple[Failure, ...] = ()

    def failure(self, code: str) -> Failure | None:
        for item in self.failures:
            if item.code == code:
                return item
        return None

    def missing(self, given: Mapping[str, object]) -> tuple[Need, ...]:
        """Which required inputs are not present, in declared order."""
        return tuple(
            need for need in self.needs
            if not str(given.get(need.key, "") or "").strip()
        )


_ASK_SITE = {
    ENGLISH: "Which site should I open?",
    KOREAN: "어느 사이트를 열까요?",
}
_ASK_QUERY = {
    ENGLISH: "What should I look up?",
    KOREAN: "무엇을 찾아볼까요?",
}
_ASK_APP = {
    ENGLISH: "Which app?",
    KOREAN: "어떤 앱을 말씀하시는 겁니까?",
}

_DISABLED_FIX = {
    ENGLISH: "Turn it on in the settings and I'll go again.",
    KOREAN: "설정에서 켜 주시면 다시 시도하겠습니다.",
}


CONTRACTS: tuple[Contract, ...] = (
    Contract(
        capability_id="web_search",
        needs=(Need("query", "query", _ASK_QUERY),),
        returns=("results", "query"),
        failures=(
            Failure(
                "unreachable",
                "the search backend raised a network or HTTP error",
                {
                    ENGLISH: "I couldn't reach the web just now.",
                    KOREAN: "지금은 웹에 연결하지 못했습니다.",
                },
                retryable=True,
            ),
            Failure(
                "no_results",
                "the search ran and returned nothing usable",
                {
                    ENGLISH: "I searched and nothing useful came back.",
                    KOREAN: "검색했지만 쓸 만한 결과가 없었습니다.",
                },
            ),
            Failure(
                "disabled",
                "web_search_enabled is off",
                {
                    ENGLISH: "Web search is switched off at the moment.",
                    KOREAN: "웹 검색이 현재 꺼져 있습니다.",
                },
                _DISABLED_FIX,
            ),
        ),
    ),
    Contract(
        capability_id="browser_control",
        needs=(Need("target", "target", _ASK_SITE),),
        returns=("page", "url", "read"),
        failures=(
            Failure(
                "no_browser",
                "no controllable browser window was found",
                {
                    ENGLISH: "I couldn't find a browser window to work in.",
                    KOREAN: "작업할 브라우저 창을 찾지 못했습니다.",
                },
                {
                    ENGLISH: "Open one and I'll pick it up.",
                    KOREAN: "하나 열어 주시면 이어서 진행하겠습니다.",
                },
                retryable=True,
            ),
            Failure(
                "page_did_not_load",
                "navigation timed out or the page never rendered",
                {
                    ENGLISH: "The page didn't load.",
                    KOREAN: "페이지가 열리지 않았습니다.",
                },
                retryable=True,
            ),
            Failure(
                "element_not_found",
                "the target control was not on the page",
                {
                    ENGLISH: "I couldn't find that on the page.",
                    KOREAN: "페이지에서 해당 항목을 찾지 못했습니다.",
                },
            ),
            Failure(
                "blocked_by_site",
                "a login wall, consent gate or bot check stopped the run",
                {
                    ENGLISH: "The site stopped me before I could read it.",
                    KOREAN: "사이트가 막고 있어 내용을 읽지 못했습니다.",
                },
            ),
            Failure(
                "disabled",
                "browser control or desktop control mode is off",
                {
                    ENGLISH: "Browser control is switched off at the moment.",
                    KOREAN: "브라우저 제어가 현재 꺼져 있습니다.",
                },
                _DISABLED_FIX,
            ),
            Failure(
                "interrupted",
                "the user stopped it, or took the input back",
                {
                    ENGLISH: "I stopped there.",
                    KOREAN: "거기서 멈췄습니다.",
                },
            ),
        ),
    ),
    Contract(
        capability_id="ui_control",
        needs=(Need("app", "app", _ASK_APP),),
        returns=("app", "window", "did"),
        failures=(
            Failure(
                "app_not_found",
                "no installed application matched the name",
                {
                    ENGLISH: "I couldn't find that app on this machine.",
                    KOREAN: "이 컴퓨터에서 해당 앱을 찾지 못했습니다.",
                },
            ),
            Failure(
                "window_not_responding",
                "the window was found but would not accept input",
                {
                    ENGLISH: "The window isn't responding to me.",
                    KOREAN: "창이 응답하지 않습니다.",
                },
                retryable=True,
            ),
            Failure(
                "path_not_found",
                "the file or folder does not exist",
                {
                    ENGLISH: "I couldn't find that file.",
                    KOREAN: "해당 파일을 찾지 못했습니다.",
                },
            ),
            Failure(
                "permission_denied",
                "Windows refused the operation",
                {
                    ENGLISH: "Windows wouldn't let me do that.",
                    KOREAN: "윈도우가 해당 작업을 허용하지 않았습니다.",
                },
            ),
            Failure(
                "disabled",
                "computer_control_mode is off",
                {
                    ENGLISH: "Desktop control is switched off at the moment.",
                    KOREAN: "데스크톱 제어가 현재 꺼져 있습니다.",
                },
                _DISABLED_FIX,
            ),
        ),
    ),
    Contract(
        capability_id="screen_analysis",
        # The whole screen is a real default, so the region is optional and
        # the contract says so by not requiring it.
        needs=(),
        returns=("described", "region"),
        failures=(
            Failure(
                "capture_failed",
                "the screenshot could not be taken",
                {
                    ENGLISH: "I couldn't capture the screen.",
                    KOREAN: "화면을 가져오지 못했습니다.",
                },
                retryable=True,
            ),
            Failure(
                "unreadable",
                "the image was captured but nothing could be made out",
                {
                    ENGLISH: "I looked, but I couldn't make that out.",
                    KOREAN: "봤지만 내용을 알아보지 못했습니다.",
                },
            ),
            Failure(
                "disabled",
                "screen_vision_enabled is off",
                {
                    ENGLISH: "Screen vision is switched off at the moment.",
                    KOREAN: "화면 인식이 현재 꺼져 있습니다.",
                },
                _DISABLED_FIX,
            ),
        ),
    ),
    Contract(
        capability_id="task_planning",
        needs=(
            Need(
                "goal",
                "text",
                {
                    ENGLISH: "What would you like me to get done?",
                    KOREAN: "무엇을 해 드리면 될까요?",
                },
            ),
        ),
        returns=("steps", "done", "remaining"),
        failures=(
            Failure(
                "step_failed",
                "one step failed and the plan could not continue",
                {
                    ENGLISH: "I got part of the way and then hit a wall.",
                    KOREAN: "중간까지 진행했지만 더는 진행하지 못했습니다.",
                },
                retryable=True,
            ),
            Failure(
                "cancelled",
                "the user stopped the plan mid-run",
                {
                    ENGLISH: "I stopped where I was.",
                    KOREAN: "진행하던 지점에서 멈췄습니다.",
                },
            ),
            Failure(
                "needs_approval",
                "the next step commits something and is waiting on consent",
                {
                    ENGLISH: "The next step changes something, so I'm waiting "
                             "on your go-ahead.",
                    KOREAN: "다음 단계는 실제로 변경이 생기는 작업이라 "
                            "확인을 기다리고 있습니다.",
                },
            ),
            Failure(
                "budget_exhausted",
                "the plan ran out of its step budget",
                {
                    ENGLISH: "I tried for a while and didn't get there.",
                    KOREAN: "한동안 시도했지만 끝내지 못했습니다.",
                },
            ),
        ),
    ),
    Contract(
        capability_id="memory",
        needs=(
            Need(
                "fact",
                "text",
                {
                    ENGLISH: "What would you like me to remember?",
                    KOREAN: "무엇을 기억해 두면 될까요?",
                },
            ),
        ),
        returns=("remembered",),
        failures=(
            Failure(
                "nothing_to_remember",
                "no durable fact could be extracted from the turn",
                {
                    ENGLISH: "I'm not sure what to hold on to there.",
                    KOREAN: "어떤 내용을 기억해야 할지 분명하지 않습니다.",
                },
            ),
            Failure(
                "storage_failed",
                "the write did not land",
                {
                    ENGLISH: "That didn't save.",
                    KOREAN: "저장되지 않았습니다.",
                },
                retryable=True,
            ),
        ),
    ),
    Contract(
        capability_id="calendar_action",
        needs=(
            Need(
                "title",
                "text",
                {
                    ENGLISH: "What should I call it?",
                    KOREAN: "일정 이름을 어떻게 할까요?",
                },
            ),
            # Never inferred. A guessed time on a real calendar is a wrong
            # appointment, not a wrong sentence.
            Need(
                "when",
                "datetime",
                {
                    ENGLISH: "When is it?",
                    KOREAN: "언제로 잡을까요?",
                },
                inferable=False,
            ),
        ),
        returns=("event", "when"),
        failures=(
            Failure(
                "not_signed_in",
                "no valid Google credentials",
                {
                    ENGLISH: "I'm not signed in to your calendar.",
                    KOREAN: "캘린더에 로그인되어 있지 않습니다.",
                },
            ),
            Failure(
                "missing_details",
                "a required field was not supplied",
                {
                    ENGLISH: "I need a bit more before I can put that in.",
                    KOREAN: "일정을 넣으려면 정보가 조금 더 필요합니다.",
                },
            ),
            Failure(
                "rejected",
                "the user declined the proposal",
                {
                    ENGLISH: "I left it alone.",
                    KOREAN: "그대로 두었습니다.",
                },
            ),
            Failure(
                "create_failed",
                "the calendar API refused the write",
                {
                    ENGLISH: "The event didn't get created.",
                    KOREAN: "일정이 생성되지 않았습니다.",
                },
                retryable=True,
            ),
        ),
    ),
    Contract(
        capability_id="project_question",
        needs=(
            Need(
                "question",
                "text",
                {
                    ENGLISH: "What would you like to know about it?",
                    KOREAN: "어떤 점이 궁금하십니까?",
                },
            ),
        ),
        returns=("answer", "files"),
        failures=(
            Failure(
                "not_connected",
                "project_access is off or no project is open",
                {
                    ENGLISH: "I don't have the project open.",
                    KOREAN: "프로젝트에 접근할 수 없습니다.",
                },
                _DISABLED_FIX,
            ),
            Failure(
                "file_not_found",
                "the named file is not in the project",
                {
                    ENGLISH: "That file isn't in the project.",
                    KOREAN: "해당 파일은 프로젝트에 없습니다.",
                },
            ),
            Failure(
                "too_large",
                "the read exceeded what could be considered at once",
                {
                    ENGLISH: "That's more than I can take in at once.",
                    KOREAN: "한 번에 살펴보기에는 양이 너무 많습니다.",
                },
            ),
        ),
    ),
    # The three that capability_selection could dispatch while the registry
    # denied they existed. Their failure sets are the most load-bearing in
    # this table, because all three are one approval away from changing
    # something real: "it didn't work" and "you said no" and "it is waiting
    # for you" have to be three different sentences.
    Contract(
        capability_id="project_edit",
        needs=(
            Need(
                "change",
                "text",
                {
                    ENGLISH: "What would you like changed?",
                    KOREAN: "어떤 부분을 수정할까요?",
                },
            ),
        ),
        returns=("proposal", "files", "diff"),
        failures=(
            Failure(
                "not_connected",
                "project_access is off or no project is open",
                {
                    ENGLISH: "I don't have the project open.",
                    KOREAN: "프로젝트에 접근할 수 없습니다.",
                },
                _DISABLED_FIX,
            ),
            Failure(
                "file_not_found",
                "the file to change is not in the project",
                {
                    ENGLISH: "That file isn't in the project.",
                    KOREAN: "해당 파일은 프로젝트에 없습니다.",
                },
            ),
            Failure(
                "already_waiting",
                "an earlier proposal has not been resolved",
                {
                    ENGLISH: "There's already a change waiting for your "
                             "approval on screen.",
                    KOREAN: "이미 승인을 기다리는 변경 사항이 화면에 있습니다.",
                },
                {
                    ENGLISH: "Review that one first.",
                    KOREAN: "먼저 그것부터 확인해 주십시오.",
                },
            ),
            Failure(
                "rejected",
                "the user declined the proposal",
                {
                    ENGLISH: "I left the files as they were.",
                    KOREAN: "파일은 그대로 두었습니다.",
                },
            ),
            Failure(
                "proposal_failed",
                "the diff could not be produced",
                {
                    ENGLISH: "I couldn't put that change together.",
                    KOREAN: "해당 변경 사항을 만들지 못했습니다.",
                },
                retryable=True,
            ),
        ),
    ),
    Contract(
        capability_id="git",
        needs=(),
        returns=("proposal", "branch", "files", "message"),
        failures=(
            Failure(
                "not_connected",
                "project_access is off or no project is open",
                {
                    ENGLISH: "I don't have the project open.",
                    KOREAN: "프로젝트에 접근할 수 없습니다.",
                },
                _DISABLED_FIX,
            ),
            Failure(
                "nothing_to_commit",
                "the working tree is clean",
                {
                    ENGLISH: "There's nothing to commit.",
                    KOREAN: "커밋할 변경 사항이 없습니다.",
                },
            ),
            Failure(
                "already_waiting",
                "an earlier proposal has not been resolved",
                {
                    ENGLISH: "There's already a commit waiting for your "
                             "approval on screen.",
                    KOREAN: "이미 승인을 기다리는 커밋이 화면에 있습니다.",
                },
                {
                    ENGLISH: "Review that one first.",
                    KOREAN: "먼저 그것부터 확인해 주십시오.",
                },
            ),
            Failure(
                "rejected",
                "the user declined the proposal",
                {
                    ENGLISH: "Nothing was committed.",
                    KOREAN: "아무것도 커밋하지 않았습니다.",
                },
            ),
            Failure(
                "proposal_failed",
                "the commit proposal could not be prepared",
                {
                    ENGLISH: "I couldn't prepare that commit, so nothing has "
                             "been staged.",
                    KOREAN: "커밋을 준비하지 못했습니다. 스테이징된 것은 "
                            "없습니다.",
                },
                retryable=True,
            ),
        ),
    ),
    Contract(
        capability_id="agent_building",
        needs=(
            Need(
                "ability",
                "text",
                {
                    ENGLISH: "What should the new ability do?",
                    KOREAN: "새 기능이 무엇을 하면 될까요?",
                },
            ),
        ),
        returns=("definition", "permissions"),
        failures=(
            Failure(
                "unsupported",
                "nothing available could implement the request",
                {
                    ENGLISH: "I can't build that one.",
                    KOREAN: "그 기능은 만들 수 없습니다.",
                },
            ),
            Failure(
                "already_waiting",
                "an earlier proposal has not been resolved",
                {
                    ENGLISH: "There's already an ability waiting for your "
                             "approval on screen.",
                    KOREAN: "이미 승인을 기다리는 기능이 화면에 있습니다.",
                },
                {
                    ENGLISH: "Review that one first.",
                    KOREAN: "먼저 그것부터 확인해 주십시오.",
                },
            ),
            Failure(
                "cancelled",
                "the user stopped it before installation",
                {
                    ENGLISH: "I stopped there. Nothing was installed.",
                    KOREAN: "거기서 멈췄습니다. 설치된 것은 없습니다.",
                },
            ),
            Failure(
                "input_required",
                "the definition needs something only the user can give",
                {
                    ENGLISH: "I need one more thing before I can build that.",
                    KOREAN: "만들기 전에 한 가지가 더 필요합니다.",
                },
            ),
        ),
    ),
)


_BY_ID: dict[str, Contract] = {item.capability_id: item for item in CONTRACTS}


def contract_for(capability_id: str) -> Contract | None:
    return _BY_ID.get(str(capability_id or "").strip())


def failure_for(capability_id: str, code: str) -> Failure | None:
    contract = contract_for(capability_id)
    if contract is None:
        return None
    return contract.failure(str(code or "").strip())


def failed(
    capability_id: str,
    code: str,
    *,
    detail: str = "",
    language: str = ENGLISH,
) -> CapabilityResult:
    """A declared failure, phrased from the contract rather than the site.

    The one function the twenty-two ``except`` blocks call. ``detail`` is
    where the exception goes: kept, logged, never said.
    """
    failure = failure_for(capability_id, code)
    return CapabilityResult(
        capability=capability_id,
        ok=False,
        failure=code if failure is not None else "",
        summary=(
            failure.say_in(language) if failure is not None
            else f"That didn't work: {code}" if code
            else "That didn't work."
        ),
        detail=str(detail or ""),
    )


def ask_for(
    capability_id: str,
    keys,
    *,
    language: str = ENGLISH,
) -> str:
    """The question that gets the missing inputs, in one sentence.

    What makes the typed inputs load-bearing rather than documentation.
    The calendar agent had this as a hand-written list --

        "I still need the event title and the date and start time."

    -- written in English, asked of someone who may well have said
    "금요일 저녁 약속 잡아줘". The agent still decides *what* is missing,
    because it is the thing holding the draft; it stops deciding what
    language to say so in, which it has no way to know.

    Unknown keys are skipped rather than guessed at, so a caller that
    names an input the contract does not declare asks a shorter question
    instead of a wrong one.
    """
    contract = contract_for(capability_id)
    if contract is None:
        return ""
    wanted = [str(key) for key in (keys or ())]
    questions = [
        need.ask_in(language)
        for need in contract.needs
        if need.key in wanted and need.ask_in(language)
    ]
    return " ".join(questions)


def describe(error: BaseException) -> str:
    """An exception, in the form that belongs in a log line.

    Exists so that call sites have somewhere obvious to put what they used
    to interpolate into speech. Everything this returns is internal by
    construction; :func:`leaks_internals` will find it if it ever escapes,
    which is the intended relationship between the two.
    """
    return f"{type(error).__name__}: {error}"
