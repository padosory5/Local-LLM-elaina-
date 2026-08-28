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
        needs=("web_search_enabled",),
        examples=("what's the news on X", "how much does Y cost"),
        offer_when="a quick current answer is enough and speed matters",
    ),
    Capability(
        id="ui_control",
        name="desktop control",
        summary=(
            "open, close, and force-quit Windows apps, create or recycle "
            "files and folders in Desktop, Documents, and Downloads, and "
            "click, type, and scroll inside a real app window"
        ),
        needs=("computer_control_mode",),
        examples=("open Spotify", "make a folder on my Desktop", "close Discord"),
        offer_when="the task is in a native Windows app rather than a webpage",
    ),
    Capability(
        id="screen_analysis",
        name="screen vision",
        summary="look at the screen, or a region you pick, and describe it",
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
        needs=(),
        examples=("remember that I'm allergic to shellfish",),
        offer_when="the user shares something durable about themselves",
    ),
    Capability(
        id="calendar_action",
        name="calendar",
        summary="create Google Calendar events after confirming the details",
        needs=(),
        examples=("put dinner with Jay on Friday at 7",),
        offer_when="the user mentions a time-bound plan",
    ),
    Capability(
        id="project_question",
        name="project access",
        summary="read this local project's files to answer questions about it",
        needs=("project_access",),
        examples=("what does the router do in this project",),
        offer_when="the question is about the user's own code",
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
    (
        "ui_control",
        re.compile(
            r"\b(?:open|launch|start|close|quit|kill)\s+"
            r"(?:the\s+)?[A-Za-z][\w .+-]{1,30}\s*(?:app|application)?\b"
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


class CapabilityRegistry:
    """Answer what Elaina can do, right now, from one place."""

    @staticmethod
    def all() -> tuple[Capability, ...]:
        return CAPABILITIES

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
            parts.append("Right now I can use " + ", ".join(ready) + ".")
        if blocked:
            parts.append("Currently off: " + ", ".join(blocked) + ".")
        return " ".join(parts)
