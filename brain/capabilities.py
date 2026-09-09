"""One declarative table of what Elaina can actually do.

Before this module, Elaina's self-knowledge lived in three unrelated places:
a hand-written prose paragraph in ``ChatEngine._capability_context()``, the
router's intent list, and the task planner's capability names. They drifted,
and the drift was visible to the user -- most damagingly as "That PC action
isn't supported yet" for browser control, an ability she has had since
Phase 4C.

The registry fixes that by being the single source of truth for four
questions that were previously answered separately:

1. *What can I do right now?*  -> ``status()`` / ``context_text()``
2. *Which of my abilities fits this request?* -> ``match()``
3. *Why can't I do this one at the moment?* -> ``blocked_reason()``
4. *Should I offer to use it here?* -> ``recommendation_for()``

``match()`` is deliberately a deterministic word-shape check, not a model
call. It exists specifically to catch the case where the model's own
classification already failed -- asking the same model to grade its own
failure would reproduce it. Its job is only to pick between abilities
Elaina really has, never to authorize one: every capability still runs
through its own existing consent, grounding, and risk checks downstream.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Capability:
    """One real, implemented ability, described the way a user would ask."""

    id: str
    name: str
    summary: str
    needs: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    offer_when: str = ""
    #: The same ability described in Korean, with the same dash
    #: convention. Only ever *spoken*: ``context_text`` stays in English
    #: because it is one prompt block the model reads either way, and
    #: duplicating the whole inventory into the prompt would cost tokens
    #: to say the same thing twice.
    #:
    #: Added when a live probe answered "깃 커밋 할 수 있어?" with "Yes. I
    #: can prepare a commit for this project." -- the registry answer path
    #: is deterministic, which is the whole point of it, and deterministic
    #: is exactly what "silently English" looks like from outside.
    name_ko: str = ""
    summary_ko: str = ""

    @property
    def spoken_summary(self) -> str:
        """The summary as a person would say it out loud.

        ``summary`` has two readers with opposite needs. The model gets it
        every turn in ``context_text`` and wants the whole inventory, so
        that it never claims an ability she does not have; the user gets it
        when they ask what she can do, and wants a sentence.

        Measured live: "Yes. I can drive a real browser session, search,
        follow links, read the live page, click buttons, and fill in
        fields. Want me to use it now?" -- six items, read out in registry
        order. That reply is a consent question, so the style layer holds
        off rewording it, which leaves this the place to fix it.

        The rule is the dash the summaries already use: everything before
        it is what the ability *is*, everything after is the enumeration.
        """
        head = re.split(r"\s+(?:--+|[–—])\s+", self.summary, 1)[0]
        return head.strip() or self.summary

    def spoken_summary_in(self, language: str = "en") -> str:
        """The same, in the language of the turn.

        Falls back to English rather than to nothing: a wrong-language
        answer is a bug you can see, and a missing one reads as her not
        having the ability at all -- which is the exact failure this whole
        registry exists to prevent.
        """
        if language == "ko" and self.summary_ko:
            head = re.split(r"\s+(?:--+|[–—])\s+", self.summary_ko, 1)[0]
            return head.strip() or self.summary_ko
        return self.spoken_summary

    def name_in(self, language: str = "en") -> str:
        if language == "ko" and self.name_ko:
            return self.name_ko
        return self.name


@dataclass(frozen=True)
class CapabilityMatch:
    capability: Capability | None
    confidence: float = 0.0
    reason: str = ""

    @property
    def matched(self) -> bool:
        return self.capability is not None


# Requirement keys are resolved against the live state dict passed in by
# ChatEngine, so a capability is never described as available while its
# switch is off.
_REQUIREMENT_LABELS: dict[str, str] = {
    "computer_control_mode": "Desktop Control Mode is off",
    "browser_control_enabled": "browser control is disabled in configuration",
    "web_search_enabled": "web search is disabled in configuration",
    "screen_vision_enabled": "screen vision is disabled in configuration",
    "project_access": "local project access is not connected",
}

_REQUIREMENT_FIXES: dict[str, str] = {
    "computer_control_mode": "turn on the Computer Control toggle",
    "browser_control_enabled": "enable browser_control in config.yaml",
    "web_search_enabled": "enable search in config.yaml",
    "screen_vision_enabled": "enable vision in config.yaml",
    "project_access": "enable project_access in config.yaml",
}


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        id="browser_control",
        name="browser control",
        summary=(
            "drive a real browser session -- search, follow links, read the "
            "live page, click buttons, and fill in fields"
        ),
        name_ko="브라우저 제어",
        summary_ko=(
            "브라우저를 직접 조작합니다 -- 검색하고, 링크를 따라가고, 열린 페이지를 읽고, 버튼을 누르고, "
            "입력란을 채웁니다"
        ),
        needs=("computer_control_mode", "browser_control_enabled"),
        examples=(
            "check the price on that site",
            "open Trip.com and look at Hong Kong hotels",
            "click the first result",
        ),
        offer_when=(
            "the answer depends on what a specific site shows right now, or "
            "the user wants real filters rather than a search snippet"
        ),
    ),
    Capability(
        id="web_search",
        name="web search",
        summary="search the web and answer from what current sources say",
        name_ko="웹 검색",
        summary_ko=(
            "웹을 검색해서 현재 자료를 근거로 답변드립니다"
        ),
        needs=("web_search_enabled",),
        examples=("what's the news on X", "how much does Y cost"),
        offer_when="a quick current answer is enough and speed matters",
    ),
    Capability(
        id="ui_control",
        name="desktop control",
        # The dash is load-bearing: everything before it is what the
        # ability is, and is what gets said aloud; everything after is the
        # full inventory the model needs in order not to over- or
        # under-claim. See Capability.spoken_summary.
        summary=(
            "work inside real Windows apps -- open, close and force-quit "
            "them, create or recycle files and folders in Desktop, "
            "Documents, and Downloads, and click, type, and scroll inside "
            "an app window"
        ),
        name_ko="데스크톱 제어",
        summary_ko=(
            "윈도우 앱 안에서 직접 작업합니다 -- 앱을 열고 닫고 강제 종료하며, 바탕 화면과 문서, 다운로드 "
            "폴더에 파일과 폴더를 만들거나 정리하고, 앱 창 안에서 클릭하고 입력하고 스크롤합니다"
        ),
        needs=("computer_control_mode",),
        examples=("open Spotify", "make a folder on my Desktop", "close Discord"),
        offer_when="the task is in a native Windows app rather than a webpage",
    ),
    Capability(
        id="screen_analysis",
        name="screen vision",
        summary="look at the screen, or a region you pick, and describe it",
        name_ko="화면 인식",
        summary_ko=(
            "화면이나 지정하신 영역을 보고 설명해 드립니다"
        ),
        needs=("screen_vision_enabled",),
        examples=("what's on my screen", "read this error for me"),
        offer_when="the user is pointing at something already visible",
    ),
    Capability(
        id="task_planning",
        name="multi-step tasks",
        summary=(
            "chain the abilities above into one goal -- research, compare, "
            "shortlist, then act -- pausing before anything committing"
        ),
        name_ko="여러 단계 작업",
        summary_ko=(
            "위 기능들을 엮어 하나의 목표를 처리합니다 -- 조사하고, 비교하고, 추려낸 다음 실행하며, 실제로 "
            "바뀌는 작업 전에는 멈춰서 확인합니다"
        ),
        needs=(),
        examples=(
            "find a hotel in Guam and shortlist the best three",
            "compare GPU prices and tell me the cheapest",
        ),
        offer_when="the goal needs gathering and deciding, not one lookup",
    ),
    Capability(
        id="memory",
        name="memory",
        summary="remember what you tell me and bring it up later",
        name_ko="기억",
        summary_ko=(
            "말씀하신 내용을 기억해 두었다가 나중에 다시 꺼내 드립니다"
        ),
        needs=(),
        examples=("remember that I'm allergic to shellfish",),
        offer_when="the user shares something durable about themselves",
    ),
    Capability(
        id="calendar_action",
        name="calendar",
        summary="create Google Calendar events after confirming the details",
        name_ko="캘린더",
        summary_ko=(
            "세부 내용을 확인한 뒤 구글 캘린더에 일정을 만듭니다"
        ),
        needs=(),
        examples=("put dinner with Jay on Friday at 7",),
        offer_when="the user mentions a time-bound plan",
    ),
    Capability(
        id="project_question",
        name="project access",
        summary="read this local project's files to answer questions about it",
        name_ko="프로젝트 접근",
        summary_ko=(
            "이 로컬 프로젝트의 파일을 읽고 관련 질문에 답해 드립니다"
        ),
        needs=("project_access",),
        examples=("what does the router do in this project",),
        offer_when="the question is about the user's own code",
    ),
    # The three below were dispatchable by capability_selection long before
    # they were declared here, which meant the model was never told she
    # could do them -- and personality.txt named two of them in its list of
    # things to deny. A surface the selector can choose and the registry
    # denies is the exact bug this table exists to prevent.
    Capability(
        id="project_edit",
        name="project edits",
        summary=(
            "propose exact changes to this project's files -- you review "
            "the diff on screen and approve or reject before anything is "
            "written"
        ),
        name_ko="프로젝트 수정",
        summary_ko=(
            "이 프로젝트 파일의 정확한 변경안을 제안합니다 -- 화면에서 변경 내용을 확인하시고 승인하거나 거절하시기 "
            "전까지는 아무것도 저장되지 않습니다"
        ),
        needs=("project_access",),
        examples=(
            "fix the typo in the router docstring",
            "add a test for that function",
        ),
        offer_when="the user asks for a change to their own code",
    ),
    Capability(
        id="git",
        name="Git",
        summary=(
            "prepare a commit for this project -- you review the files, the "
            "branch and the message on screen, and nothing is staged, "
            "committed or pushed until you say so"
        ),
        name_ko="깃",
        summary_ko=(
            "이 프로젝트의 커밋을 준비합니다 -- 파일과 브랜치, 메시지를 화면에서 확인하시고 승인하시기 전까지는 "
            "스테이징도 커밋도 푸시도 하지 않습니다"
        ),
        needs=("project_access",),
        examples=("commit this", "push the changes"),
        offer_when="the user has changes they want recorded",
    ),
    Capability(
        id="agent_building",
        name="new abilities",
        summary=(
            "build a new agent when something is genuinely outside what I "
            "have -- proposed for your approval, never installed on my own"
        ),
        name_ko="새 기능 추가",
        summary_ko=(
            "지금 가진 기능으로 정말 안 되는 일이면 새 에이전트를 만들어 제안드립니다 -- 임의로 설치하지는 "
            "않습니다"
        ),
        needs=(),
        examples=("can you make something that tracks my packages",),
        offer_when="the user wants something no current ability covers",
    ),
)

_BY_ID: dict[str, Capability] = {item.id: item for item in CAPABILITIES}


# Word-shape signals, in priority order. These decide only *which existing
# ability* a request is asking for -- never whether it is allowed.
_MATCH_PATTERNS: tuple[tuple[str, re.Pattern[str], float, str], ...] = (
    (
        "browser_control",
        re.compile(
            r"\b(?:on|in|through|using|with|via)\s+(?:the\s+|a\s+|your\s+|her\s+)?"
            r"(?:browser|web\s*browser|chrome|whale|edge|firefox)\b"
            r"|\bbrowser\b.{0,20}\b(?:open|check|look|search|go|use|control)\b"
            # The verb comes first just as often ("control my browser",
            # "use the browser") -- matching only one order missed the
            # single most direct phrasing there is.
            r"|\b(?:open|check|look|search|use|control|drive|browse|operate)\b"
            r"[^.?!]{0,20}\b(?:browser|web\s*browser|chrome|whale|edge|firefox)\b"
            r"|\b(?:open|go\s+to|visit|pull\s+up|bring\s+up|check|look\s+at|"
            r"read|see)\b.{0,30}"
            r"\b(?:\.com|\.net|\.org|\.co\.kr|website|site|page|tab)\b"
            r"|\b(?:click|scroll|fill\s+in|type\s+in)\b.{0,24}"
            r"\b(?:page|link|button|field|result|tab)\b"
            r"|브라우저|웹페이지",
            re.IGNORECASE,
        ),
        0.9,
        "The request names a browser, a website, or a real page action.",
    ),
    (
        "browser_control",
        re.compile(
            r"\b(?:check|verify|confirm|look\s+up|see)\b.{0,40}"
            r"\b(?:actual|real|current|live|直接|directly)\b"
            r"|\b(?:actually|really|directly)\s+(?:check|look|go|see|open)\b"
            # "check it for real" -- but never a bare "for real?", which is
            # disbelief, not a request. Found live: that exclamation was
            # read as a browser request, and the parked offer then carried
            # it to the planner as the goal.
            r"|\b(?:check|look|go|see|verify|confirm)\b[^.?!]{0,24}"
            r"\bfor\s+(?:me\s+)?(?:yourself|real)\b",
            re.IGNORECASE,
        ),
        0.7,
        "The request asks to confirm something directly rather than from a snippet.",
    ),
    # Checked before ui_control: "open wikipedia and tell me what the
    # article says" is a request to read a website, not to launch a native
    # application, and routing it to the desktop planner would hunt for an
    # installed app that does not exist. The signal is list-free -- asking
    # to be *told what something says* is a content request, whereas "open
    # Spotify and play a song" acts inside an app.
    (
        "browser_control",
        re.compile(
            r"\b(?:open|go\s+to|visit|check|look\s+at|read|browse)\b"
            r"[^.?!]{0,40}?"
            r"\b(?:tell\s+me|what\s+(?:it|they|the\s+\w+)\s+says?|"
            r"what\s+comes\s+up|summari[sz]e|says?\s+about|shows?)\b",
            re.IGNORECASE,
        ),
        0.75,
        "The request asks to open something and report what it says.",
    ),
    # Checked before ui_control, and narrowly. "Create a file in my
    # project" is a project edit; "make a folder on my Desktop" is not, so
    # every branch here requires a word that names the project, the code,
    # or the repository. Without these three, "can you commit changes to
    # git for me?" reached match() with nothing to match, fell through
    # _answer_ability_question, and was answered by *running the git
    # action* -- which reported nothing staged, which she then rendered as
    # "I can't commit changes to Git right now." Registering a capability
    # is not enough on its own; the question about it has to find it.
    (
        "git",
        re.compile(
            r"\bgit\b"
            r"|\b(?:commit|push|stage|staged)\b[^.?!]{0,30}"
            r"\b(?:change|changes|code|file|files|project|repo|branch)\b"
            r"|\b(?:change|changes|code|file|files|project|repo)\b[^.?!]{0,30}"
            r"\b(?:commit|committed|push|pushed)\b"
            r"|깃|커밋|푸시",
            re.IGNORECASE,
        ),
        0.8,
        "The request names Git, a commit, or a push.",
    ),
    (
        "project_edit",
        re.compile(
            r"\b(?:edit|change|modify|update|fix|refactor|rewrite|add\s+to)\b"
            r"[^.?!]{0,40}"
            r"\b(?:project|codebase|repo|source|my\s+code|the\s+code)\b"
            r"|\b(?:project|codebase|repo)\b[^.?!]{0,30}"
            r"\b(?:edit|change|modify|fix|refactor)\b"
            r"|\b(?:create|make|add|delete)\s+(?:a\s+|the\s+)?"
            r"(?:file|test|function|method|class)\b[^.?!]{0,30}"
            r"\b(?:project|codebase|repo|code)\b"
            r"|프로젝트[^.?!]{0,20}(?:수정|고쳐|바꿔)"
            r"|코드[^.?!]{0,20}(?:수정|고쳐|바꿔)",
            re.IGNORECASE,
        ),
        0.75,
        "The request asks to change this project's own files.",
    ),
    (
        "agent_building",
        re.compile(
            r"\b(?:make|build|create|write)\b[^.?!]{0,30}"
            r"\b(?:agent|new\s+ability|new\s+capability|new\s+tool|"
            r"new\s+skill)\b"
            r"|\b(?:agent|ability|capability)\b[^.?!]{0,20}\bfor\s+(?:that|this)\b"
            r"|에이전트[^.?!]{0,20}(?:만들|추가)"
            r"|새\s*기능[^.?!]{0,20}만들",
            re.IGNORECASE,
        ),
        0.7,
        "The request asks for a new ability to be built.",
    ),
    (
        "ui_control",
        re.compile(
            # A pronoun is not the name of an application. Measured live:
            # one turn after a browser action failed to open a URL, "So
            # open it." matched here on "open it", was rescued to the
            # desktop planner, and it spent fourteen rounds hunting for a
            # native window -- trying, among other things, to play media --
            # before exhausting its budget. What "it" referred to was the
            # address of the page she had just failed to open.
            r"\b(?:open|launch|start|close|quit|kill)\s+"
            r"(?:the\s+)?"
            r"(?!(?:it|that|this|them|those|these|there|here|one|"
            r"again|up)\b)"
            r"[A-Za-z][\w .+-]{1,30}\s*(?:app|application)?\b"
            r"|\b(?:create|make|delete|move)\s+(?:a\s+|the\s+)?(?:file|folder)\b"
            # "Control my computer" names the ability with no target at
            # all -- the shape an ability question takes.
            r"|\b(?:control|use|operate|drive)\s+(?:my|the|this)\s+"
            r"(?:computer|pc|desktop|laptop|machine)\b",
            re.IGNORECASE,
        ),
        0.6,
        "The request names a native application or a file action.",
    ),
    (
        "screen_analysis",
        re.compile(
            r"\b(?:my|the)\s+screen\b|\bwhat\s+(?:am\s+i|do\s+you)\s+(?:looking|see)"
            r"|\bthis\s+(?:error|screenshot|image)\b",
            re.IGNORECASE,
        ),
        0.7,
        "The request points at something already on screen.",
    ),
    (
        "web_search",
        re.compile(
            r"\b(?:search|google|look\s*up|find\s+out)\b|\bwhat'?s\s+the\s+"
            r"(?:latest|news|current)\b"
            r"|\bhow\s+much\s+(?:does|is|are|do)\b|\bprice\s+of\b",
            re.IGNORECASE,
        ),
        0.5,
        "The request asks for current information from the web.",
    ),
)

# "Can you...?" / "are you able to...?" -- a question *about* her abilities,
# which deserves an honest inventory answer rather than an attempt.
# A question that names a target and an action is an instruction: the
# person is being polite, not curious about the feature list.
_NAMES_SOMETHING_TO_ACT_ON = re.compile(
    r"\b(?:open|close|quit|kill|start|launch|play|pause|stop|resume|"
    r"skip|type|write|enter|click|press|scroll|select|search|find|"
    r"look\s+up|book|reserve|delete|move|copy|rename|create|make|send|"
    r"check)\s+"
    # A vague object is not a target: "click things" asks what she can do,
    # "click Next" asks her to do it.
    r"(?!it\b|that\b|this\b|them\b|something\b|anything\b|stuff\b|things?\b)\S+",
    re.IGNORECASE,
)

_ABILITY_INVENTORY = re.compile(
    r"\bwhat\s+(?:can|could|are)\s+you\b"
    r"|\byour\s+(?:abilities|capabilities|features)\b"
    r"|\bwhat\s+are\s+you\s+capable\s+of\b"
    r"|뭐\s*(?:를)?\s*할\s*수\s*있|무엇을\s*할\s*수\s*있|기능이\s*뭐",
    re.IGNORECASE,
)

_ABILITY_QUESTION = re.compile(
    r"\b(?:can|could)\s+you\b|\bare\s+you\s+able\b|\bdo\s+you\s+(?:know\s+how|have)\b"
    r"|\bwhat\s+(?:can|could)\s+you\s+do\b|\byour\s+(?:abilities|capabilities)\b"
    r"|할\s*수\s*있(?:어|나요|니)|가능해",
    re.IGNORECASE,
)



# ------------------------------------------------------- speech repair
#
# The names of Elaina's own abilities are a closed vocabulary, and a
# transcriber that mishears one produces something that is not in it.
# Measured live, session 9, in the middle of a run of browser actions:
#
#     You said: Yeah, I'm talking about the brass control.
#     Elaina:   I've opened the brass control.
#
# There is no brass control. She invented a capability rather than
# recognising a near-miss of one she has -- which is the worst of both
# answers, because the person is now told a thing exists.
#
# Vowels are what a transcriber loses first, so the comparison is made on
# consonants alone. Measured across the plausible confusions:
#
#     brass -> browser   0.67      mouse  -> browser   0.29
#     desk  -> desktop   0.75      volume -> browser   0.00
#     scream-> screen    0.75      remote -> desktop   0.25
#
# A real request to control the mouse, the volume or a remote is nowhere
# near, and stays untouched.
_SPOKEN_HEADS = ("control", "vision", "search", "access", "tasks", "memory")

_SPOKEN_CAPABILITY = re.compile(
    r"\b([A-Za-z][A-Za-z'-]*)\s+(" + "|".join(_SPOKEN_HEADS) + r")\b",
    re.IGNORECASE,
)

# How alike two consonant skeletons must be before one is read as the
# other. Set from the measurements above: it accepts brass/browser at
# 0.67 and refuses keyboard/browser at 0.40.
_NEARLY_THE_SAME_NAME = 0.6


def _consonants(word: str) -> str:
    """What survives a transcriber: the consonants, in order."""
    letters = re.sub(r"[^a-z]", "", str(word or "").casefold())
    return re.sub(r"[aeiou]", "", letters) or letters


def _sounds_like(said: str, name: str) -> float:
    return difflib.SequenceMatcher(
        None, _consonants(said), _consonants(name),
    ).ratio()


def repair_spoken_name(text: str) -> tuple[str, str]:
    """A misheard ability name and the one it must have been, or nothing.

    Only ever within the closed set of names Elaina actually has, and only
    when exactly one of them is close. Two near-misses mean the turn is
    ambiguous and the caller should ask rather than choose.
    """
    said_text = " ".join(str(text or "").split())
    if not said_text:
        return "", ""
    known = {item.name.casefold() for item in CAPABILITIES}
    for match in _SPOKEN_CAPABILITY.finditer(said_text):
        phrase = match.group(0)
        modifier, head = match.group(1), match.group(2).casefold()
        if phrase.casefold() in known:
            return "", ""
        scored = sorted(
            (
                (_sounds_like(modifier, item.name.split()[0]), item.name)
                for item in CAPABILITIES
                if item.name.casefold().endswith(head)
                and len(item.name.split()) > 1
            ),
            reverse=True,
        )
        if not scored or scored[0][0] < _NEARLY_THE_SAME_NAME:
            continue
        if len(scored) > 1 and scored[1][0] >= _NEARLY_THE_SAME_NAME:
            # Two of her own abilities are equally close. Guessing between
            # them is worse than asking.
            continue
        return phrase, scored[0][1]
    return "", ""


def names_no_ability_she_has(text: str) -> str:
    """An "<x> control" phrase that is not one of her abilities at all.

    Separate from the repair above and needed even when it finds nothing:
    the failure was not only that "brass control" went unrepaired, it was
    that she went on to say she had opened it.
    """
    said_text = " ".join(str(text or "").split())
    known = {item.name.casefold() for item in CAPABILITIES}
    for match in _SPOKEN_CAPABILITY.finditer(said_text):
        if match.group(0).casefold() not in known:
            return match.group(0)
    return ""

class CapabilityRegistry:
    """Answer what Elaina can do, right now, from one place."""

    @staticmethod
    def all() -> tuple[Capability, ...]:
        return CAPABILITIES

    # Reachable from the registry as well as at module level, because this
    # is a question about the ability list and everything else that asks
    # one goes through here.
    repair_spoken_name = staticmethod(repair_spoken_name)
    names_no_ability_she_has = staticmethod(names_no_ability_she_has)

    @staticmethod
    def get(capability_id: str) -> Capability | None:
        return _BY_ID.get(str(capability_id).strip())

    @staticmethod
    def blocked_reason(
        capability: Capability,
        state: Mapping[str, object],
    ) -> str:
        """Why this ability can't run, in the user's words -- or "" if it can."""
        for requirement in capability.needs:
            if not bool(state.get(requirement, False)):
                return _REQUIREMENT_LABELS.get(
                    requirement, f"{requirement} is unavailable"
                )
        return ""

    @staticmethod
    def fix_for(capability: Capability, state: Mapping[str, object]) -> str:
        for requirement in capability.needs:
            if not bool(state.get(requirement, False)):
                return _REQUIREMENT_FIXES.get(requirement, "")
        return ""

    @classmethod
    def is_available(
        cls,
        capability_id: str,
        state: Mapping[str, object],
    ) -> bool:
        capability = cls.get(capability_id)
        if capability is None:
            return False
        return not cls.blocked_reason(capability, state)

    @classmethod
    def available(cls, state: Mapping[str, object]) -> tuple[Capability, ...]:
        return tuple(
            item for item in CAPABILITIES if not cls.blocked_reason(item, state)
        )

    @classmethod
    def match(cls, request: str) -> CapabilityMatch:
        """Pick the ability a free-text request is asking for.

        Used where the model's own routing already failed, so it must not
        depend on the model. Returns an unmatched result rather than
        guessing when nothing scores.
        """
        text = str(request or "")
        if not text.strip():
            return CapabilityMatch(None)
        for capability_id, pattern, confidence, reason in _MATCH_PATTERNS:
            if pattern.search(text):
                capability = _BY_ID.get(capability_id)
                if capability is not None:
                    return CapabilityMatch(capability, confidence, reason)
        return CapabilityMatch(None)

    @staticmethod
    def is_ability_question(text: str) -> bool:
        """Whether this asks what she *can* do, rather than asking her to.

        "Can you close Spotify" is a request wearing a question mark. A
        person hearing it closes Spotify; answering "yes, I can close
        Windows apps -- want me to use it now?" is the pedantic reading,
        and it was the reply to every politely-phrased instruction until
        this distinction existed. The rule: a question that names what to
        act on is a request, and doing the thing is the honest answer to
        it.
        """
        text = str(text or "")
        if not _ABILITY_QUESTION.search(text):
            return False
        if _ABILITY_INVENTORY.search(text):
            # "What can you do" asks about the inventory itself, however
            # many nouns happen to follow it.
            return True
        return not _NAMES_SOMETHING_TO_ACT_ON.search(text)

    @classmethod
    def context_text(cls, state: Mapping[str, object]) -> str:
        """The prompt block describing Elaina's real, current abilities."""
        lines = ["WHAT ELAINA CAN DO RIGHT NOW (she can:)"]
        for capability in CAPABILITIES:
            blocked = cls.blocked_reason(capability, state)
            if blocked:
                fix = cls.fix_for(capability, state)
                suffix = f" -- unavailable: {blocked}"
                if fix:
                    suffix += f" (fix: {fix})"
            elif capability.offer_when:
                # When each ability is worth reaching for, so the choice
                # between "just answer" and "go and check" is informed
                # rather than a coin flip.
                suffix = f" -- worth using when {capability.offer_when}"
            else:
                suffix = ""
            lines.append(f"- {capability.name}: {capability.summary}{suffix}")
        lines.append(
            "Never say an ability listed here is unsupported. If one is "
            "unavailable, say which switch turns it on instead of refusing."
        )
        lines.append(
            "Never promise to do something in a later turn. Either it happens "
            "in this turn or you say plainly that you need a go-ahead first."
        )
        return "\n".join(lines)

    @classmethod
    def recommendation_for(
        cls,
        capability_id: str,
        state: Mapping[str, object],
    ) -> str:
        """One short line offering an ability that would help here.

        This is the "should I use it or not" judgement the user asked for,
        kept deterministic: it states the real trade-off and lets them
        choose, rather than silently spending a slow browser session.
        """
        capability = cls.get(capability_id)
        if capability is None:
            return ""
        blocked = cls.blocked_reason(capability, state)
        if blocked:
            fix = cls.fix_for(capability, state)
            tail = f" Turn it on: {fix}." if fix else ""
            return f"I could use {capability.name}, but {blocked}.{tail}"
        return f"I can use {capability.name} for this -- want me to?"

    # How many abilities a person names before they stop naming them.
    # Measured live: asked "what can you do?", she answered "Right now I can
    # use browser control, web search, desktop control, screen vision,
    # multi-step tasks, memory, calendar, project access." -- the registry
    # read out in registry order, which is an inventory rather than a
    # sentence, and is the single most interface-sounding line in the
    # product. Three is where an English list still parses as speech.
    SPOKEN_LIST_LIMIT = 3

    @classmethod
    def _spoken_list(cls, names: list[str]) -> str:
        """Name a few of them the way a person would, not all of them.

        Nothing is hidden by this: the full registry is still in the model's
        context every turn (``context_text``), so a question about a
        specific ability is still answered from the complete list. This
        governs only the one sentence that is *spoken aloud*.
        """
        if len(names) <= cls.SPOKEN_LIST_LIMIT:
            return ", ".join(names)
        head = ", ".join(names[:cls.SPOKEN_LIST_LIMIT])
        rest = len(names) - cls.SPOKEN_LIST_LIMIT
        return f"{head}, and {rest} other things"

    @classmethod
    def inventory_sentence(cls, state: Mapping[str, object]) -> str:
        """A short spoken summary of live abilities, for "what can you do?"."""
        ready = [item.name for item in cls.available(state)]
        blocked = [
            f"{item.name} ({cls.blocked_reason(item, state)})"
            for item in CAPABILITIES
            if cls.blocked_reason(item, state)
        ]
        parts = []
        if ready:
            parts.append("Right now I can use " + cls._spoken_list(ready) + ".")
        if blocked:
            # What is switched off is the actionable half -- it tells the
            # user what to turn on -- so it keeps a longer list than the
            # ready one, but not an unbounded one.
            parts.append("Currently off: " + ", ".join(blocked) + ".")
        return " ".join(parts)
