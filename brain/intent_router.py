from __future__ import annotations

import json
import re
import time

from brain import world_clock
from core import timing
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from tools.computer_control.computer_control import (
    COMPUTER_OPERATIONS,
    OBSERVATION_OPERATIONS,
    UI_ACTION_OPERATIONS,
    transcript_names_location,
    transcript_names_target,
)


ALLOWED_INTENTS = {
    "conversation",
    "calculation",
    "agent_offer",
    "agent_consent",
    "web_search",
    "project_question",
    "project_edit",
    "git_commit",
    "git_publish",
    "screen_analysis",
    "computer_action",
    "selected_text_question",
    "knowledge_question",
    "time_question",
    "pending_approval",
    "agent_create",
    "calendar_action",
    "entity_correction",
    "fact_check",
    "clarification",
}

ACTION_INTENTS = {
    "web_search",
    "project_question",
    "project_edit",
    "git_commit",
    "git_publish",
    "screen_analysis",
    "agent_create",
    "calendar_action",
    "entity_correction",
    "fact_check",
}

INFORMATION_FRESHNESS_VALUES = {
    "stable",
    "historical_record",
    "changing",
    "live",
    "unknown",
}

ADVICE_DOMAINS = {
    "general",
    "health",
    "financial",
    "legal",
    "product",
    "technical",
    "safety",
}

# Creating a file and *putting something in it* are two requests, and only
# the first is in scope: the Phase 4A command set creates an empty file and
# has no way to write content. The model drops the second half and reports
# create_file, which produces an empty notes.txt for someone who asked for
# one with "hello" in it -- a wrong outcome reported as success.
#
# Measured: "Create notes.txt in Documents and write hello inside it."
# returned create_file with action_requested=True on five consecutive runs,
# and identically at the previous checkpoint, so this is behaviour rather
# than variance. Grammar, not a keyword list: a second verb applied to the
# same object is what puts the request out of scope.
_COMPOUND_FILE_CONTENT_REQUEST = re.compile(
    r"\b(?:create|make|add|new)\b.{0,60}?"
    r"\b(?:and|then)\s+(?:also\s+)?"
    r"(?:write|type|put|insert|save)\b",
    flags=re.IGNORECASE,
)

_IRREVERSIBLE_DELETE_REQUEST = re.compile(
    r"(?:"
    r"\bpermanent(?:ly)?\b.{0,40}\b(?:delete|erase|remove|wipe)\b|"
    r"\b(?:delete|erase|remove|wipe)\b.{0,40}\bpermanent(?:ly)?\b|"
    r"\b(?:without|bypass|skip|avoid)\b.{0,40}\brecycl(?:e|ing)\s+bin\b"
    r")",
    flags=re.IGNORECASE,
)

# ui_action vs. browser_action is a genuinely hard call for the model to get
# right from wording alone every time (both use nearly the same phrasing).
# When the request is deictic ("this page", "here", "in it") and the real,
# already-known active surface is a browser page, that is ground truth the
# model doesn't have to guess at -- correct the operation deterministically
# rather than hoping the prompt alone lands it every time.
_DEICTIC_SURFACE_REFERENCE = re.compile(
    # "this page" was required to be adjacent, so "this hotel page" and
    # "this GitHub page" -- the way anyone actually refers to the page they
    # are looking at -- did not match at all, and the request fell through
    # to unsupported. Up to two intervening words covers the natural forms
    # without letting the reference drift to another sentence.
    r"\bthis\s+(?:[\w-]+\s+){0,2}(?:page|window|screen|tab|site|article)\b"
    r"|\bthese\s+(?:[\w-]+\s+){0,2}(?:listings|results|items)\b"
    r"|\bhere\b|\bin\s+it\b",
    flags=re.IGNORECASE,
)

# Which *kind* of surface the deictic names. The correction below used to
# ask only whether a deictic was present and then default to the native
# desktop when no live surface was known -- so "compare the prices in these
# hotel listings on this page" became a ui_action against whatever desktop
# app happened to be in front. A reference to a page is evidence about the
# surface in its own right, independent of what is currently focused.
_DEICTIC_PAGE_REFERENCE = re.compile(
    r"\bthis\s+(?:[\w-]+\s+){0,2}(?:page|tab|site|article)\b"
    r"|\bthese\s+(?:[\w-]+\s+){0,2}(?:listings|results|items)\b"
    r"|\bon\s+(?:this|the)\s+page\b",
    flags=re.IGNORECASE,
)

# A command to raise a named window, as opposed to a question about which
# windows exist. list_windows legitimately owns "which window is in front
# right now"; it was also swallowing "bring VS Code to the front", which is
# an instruction to change focus and belongs to the verified UI planner.
# Command verbs only -- a question never opens with "bring"/"switch".
_FOCUS_WINDOW_COMMAND = re.compile(
    r"\b(?:bring|switch|move|put|pull|raise)\b[^.?!]*?"
    r"\b(?:to\s+the\s+(?:front|foreground|top)|into\s+focus|up\s+front)\b"
    r"|\bswitch\s+to\s+(?:the\s+)?[\w.+-]+"
    r"|\bfocus\s+(?:on\s+)?(?:the\s+)?[\w.+-]+\s+window\b",
    flags=re.IGNORECASE,
)

# A short follow-up after an Elaina-opened search rarely repeats "on this
# page" ("click Images", "show pictures", "open the first result").  The
# foreground browser is stronger evidence than a small model's ambiguous
# ui_action/browser_action label.  Explicit native-app names retain their
# desktop route. Commit verbs (book/reserve/buy/...) are included here too:
# "book the best one" is exactly this same page-action shape, and it must
# reach browser_action to hit is_committing_element's already-correct
# pause-for-confirmation at all -- found live, this request otherwise had
# no way to reach that existing checkpoint.
_IMPLICIT_BROWSER_ACTION = re.compile(
    r"\b(?:click|press|tap|open|show|fill|type|enter|select|choose|scroll|"
    r"read|compare|play|pause|resume|skip|book|reserve|buy|purchase|order)\b|"
    r"예약|구매|주문",
    flags=re.IGNORECASE,
)
_UNAMBIGUOUS_BROWSER_PAGE_ACTION = re.compile(
    r"\b(?:click|press|tap|fill|type|enter|select|choose|scroll|read|"
    r"compare|book|reserve|buy|purchase|order)\b|예약|구매|주문",
    flags=re.IGNORECASE,
)
# Whether the sentence is shaped like a request at all.
#
# This is a grammatical test and is meant to be one -- English marks a
# request in a small closed set of ways, and a sentence showing none of them
# is a remark about the world. It is not a topic list and must not grow into
# one: nothing here mentions Spotify, restaurants, or any other subject.
#
# It exists because the model's own ``request_explicitness`` field, which was
# added for exactly this, comes back "direct" for every input measured --
# statements and questions alike -- so it carries no information. The shape
# of the sentence does.
#
# The failure it prevents: "Spotify won't play anything today." was answered
# with a web search. Nobody asked for anything; a complaint was read as a
# request for help. Getting this wrong in the other direction merely offers
# ("want me to look that up?") instead of searching silently, so the bias is
# deliberately toward asking.
_REQUEST_SHAPE = re.compile(
    # An actual question.
    r"\?\s*$"
    # A wh-question, or an elliptical one ("any good ramen nearby").
    r"|^\s*(?:what|when|where|which|who|whom|whose|why|how|any|anyone|"
    r"anything|anywhere)\b"
    # Subject-auxiliary inversion, which is how English opens a yes/no
    # question.
    r"|^\s*(?:is|are|was|were|am|do|does|did|can|could|would|will|shall|"
    r"should|have|has|had|may|might)\b"
    # A request aimed at the listener.
    r"|\b(?:can|could|would|will)\s+you\b|\bplease\b"
    # First-person want/need frames.
    r"|\b(?:i|we)\s+(?:need|want|would\s+like|'d\s+like)\b"
    r"|\b(?:help|tell|show|find|give|get|remind)\s+me\b|\blet\s+me\s+know\b"
    # Indirect questions. "I wonder what Nvidia is trading at" is a request
    # for a fact wearing the clothes of a statement, and answering it is
    # what a person would do. This is the line between it and a complaint:
    # an indirect question names something unknown that it wants known.
    r"|\b(?:i\s+wonder|wondering|i(?:'m| am)\s+curious|"
    r"i(?:'d| would)\s+(?:love|like)\s+to\s+know|"
    r"i\s+(?:do\s+not|don't)\s+know\s+(?:what|how|when|where|why|which|if|"
    r"whether)|no\s+idea\s+(?:what|how|when|where|why|which))\b"
    # A bare imperative. English imperatives drop the subject, so a sentence
    # opening on a base-form verb is an instruction rather than a report.
    # An inventory of verbs rather than a parser, deliberately: it is the
    # imperative mood this is testing for, and the list stays closed to
    # verbs -- no subject, product or site name may ever be added to it.
    r"|^\s*(?:please\s+|just\s+|now\s+|then\s+)*(?:find|show|tell|give|look|"
    r"search|check|open|play|get|fetch|pull|bring|recommend|suggest|compare|"
    r"list|read|explain|describe|remind|send|make|create|write|run|start|"
    r"stop|close|click|type|fill|browse|visit|navigate|verify|confirm|use|"
    r"go|inspect|review|count|calculate|"
    r"translate|define|name|walk|take|analy[sz]\w*|summari[sz]\w*)\b"
    # Korean request endings, the same closed grammatical class.
    r"|찾아|알려|해\s*줘|보여|추천",
    flags=re.IGNORECASE,
)

# Intents whose whole job is to fetch evidence. Acting on one is cheap and
# reversible, which is why they never ask permission -- and exactly why the
# only thing standing between a passing remark and an unrequested lookup is
# whether the turn was a request at all. project_question and
# screen_analysis are deliberately absent: both are asked about the thing in
# front of the user, and neither was ever observed firing on a remark.
_RESEARCH_INTENTS_WITHHELD = frozenset({
    "web_search", "fact_check", "entity_correction",
})

_EXPLICIT_NATIVE_APP_REFERENCE = re.compile(
    r"\b(?:spotify|notepad|calculator|discord|slack|steam|settings|"
    r"visual\s+studio\s+code|vs\s*code)\b",
    flags=re.IGNORECASE,
)

# A referential delete ("delete the folder we just made", "delete that")
# names no exact item -- it points at something Elaina herself just created
# this session. Small closed-class grammar pattern, the same technique
# already used for _DEICTIC_SURFACE_REFERENCE above, not a semantic
# keyword list: it only ever unlocks resolving against real local session
# state (see SessionItemMemory), never lets the model invent a target.
_REFERENTIAL_ITEM_REFERENCE = re.compile(
    r"\b(?:it|that|the\s+one)\b|"
    r"\b(?:we|you|i)\s+(?:just\s+)?(?:made|created)\b|"
    r"\bjust\s+(?:made|created)\b",
    flags=re.IGNORECASE,
)

# Ability-boundary intents: real requests where the model chooses between
# two meaningfully different things Elaina could do next (act on the real
# computer vs. research and report back). Confidence only matters here --
# other intents either have no such boundary or are already deterministically
# corrected by policy above using real local state.
_ABILITY_BOUNDARY_INTENTS = frozenset({"web_search", "computer_action"})

# ``open_app`` has a deliberately narrow semantic contract: launching one
# application is the whole requested outcome. These are grammar/function words
# that can surround that outcome, not sentence triggers. Any other substantive
# word left after removing the grounded app name means the request contains an
# in-app goal and belongs to the verified UI planner instead. This prevents a
# model from silently reducing "play Dynamite in Spotify" to merely opening
# Spotify while still accepting arbitrary natural launch phrasing.
_OPEN_APP_GRAMMAR_WORDS = frozenset({
    "a", "an", "app", "application", "bring", "can", "could", "default",
    "desktop", "for", "in", "launch", "me", "my", "now", "on", "open",
    "over", "pc", "please", "run", "start", "take", "takeover", "the",
    "to", "up", "would", "you",
})

# These are narrow, local normalizers for structured computer requests. They
# do not decide whether an arbitrary sentence may control the computer: that
# remains the model's intent decision plus _apply_computer_control_policy.
# They only recover the exact user-provided target when the model has already
# selected a supported operation but copied the whole sentence into
# action_target (a common small-model failure in live routing checks).
_SPOKEN_URL = re.compile(
    r"\b(?:(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*"
    r"(?:\.[a-z0-9][a-z0-9-]*)+)(?:/[^\s]*)?",
    flags=re.IGNORECASE,
)
# How many tokens the router is allowed to emit. Not a target -- decode
# stops at the stop token -- so headroom above the measured median is
# free, and running out of it truncates the JSON irrecoverably.
ROUTER_OUTPUT_CAP = 512

# What makes a turn a question about the local codebase rather than
# about the world. "Project" alone is not enough: it is an ordinary
# English word, and the model reads it as one.
_NAMES_THE_PROJECT = re.compile(
    r"\b(?:this|the|your|our|my)\s+(?:project|codebase|code|repo|repository|app|program)\b"
    r"|\b(?:codebase|repo|repository|source\s+tree|source\s+code)\b"
    r"|\b(?:function|class|method|module|variable|import|test|tests|file|files|folder|directory|script|bug|stack\s+trace|traceback)\b"
    r"|\b(?:elaina|main\s?\.py)\b"
    r"|\b[\w-]+\.(?:py|js|ts|json|yaml|yml|md|toml|cfg)\b"
    r"|\b(?:in|from|inside)\s+(?:the\s+)?(?:code|project|repo)\b"
    r"|코드|소스|프로젝트",
    re.IGNORECASE,
)

# The model sees recent turns, and sometimes reproduces a transcription
# error from one of them in place of what was actually just said. Measured
# live, after "universe" was misheard once:
#
#     You said: Do you remember what kind of university I'm going to?
#     [Router] Interpreted transcript as: ... what kind of universe ...
#     [Recall] Set aside 5 memory item(s) unrelated to 'Universe'.
#
# The user had corrected themselves and the correction was thrown away.
# This is the same rule as everywhere else in this project: what the person
# just said outranks anything held from before -- including the model's own
# memory of mishearing it.
_WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)
_NEARLY_THE_SAME = 0.7

# Never restored, in either direction. Function words are a closed class
# and none of them is ever a transcription error worth repairing, but they
# are short enough that difflib finds them "nearly the same" as each other
# and as anything they are a prefix of. "The" and "there" score 0.75, so
# a transcript containing "are there casinos" rewrote every "the" in the
# paraphrase to "there".
_NEVER_A_MISHEARING = frozenset({
    "the", "and", "but", "for", "not", "you", "are", "was", "were", "has",
    "had", "have", "his", "her", "its", "our", "their", "them", "they",
    "this", "that", "these", "those", "there", "here", "then", "than",
    "with", "from", "into", "onto", "over", "under", "about", "your",
    "who", "what", "when", "where", "which", "why", "how", "all", "any",
    "can", "could", "will", "would", "should", "may", "might", "must",
    "one", "two", "some", "more", "most", "much", "many", "such", "very",
})


def _restore_misheard_words(paraphrase: str, transcript: str) -> str:
    """Put back a word the paraphrase swapped for a near-miss from history.

    Only a near-miss, and only in that direction: a paraphrase is allowed
    to reword freely, so a word it introduces is left alone unless the
    transcript holds one that is almost the same. "Universe" for
    "university" is a mishearing carried forward; "hotel" for "place" is
    ordinary paraphrase and is untouched.
    """
    import difflib

    said = {
        word.casefold() for word in _WORD.findall(transcript or "")
    } - _NEVER_A_MISHEARING
    if not said:
        return paraphrase

    def swap(match):
        word = match.group(0)
        lowered = word.casefold()
        if lowered in said or lowered in _NEVER_A_MISHEARING:
            return word
        close = difflib.get_close_matches(
            lowered, said, n=1, cutoff=_NEARLY_THE_SAME,
        )
        if not close or close[0] == lowered:
            return word
        restored = close[0]
        return restored.capitalize() if word[:1].isupper() else restored

    return _WORD.sub(swap, paraphrase or "")


_SPOKEN_SEARCH = re.compile(
    r"\b(?:search|look\s+up|find)\s+(?:for\s+)?"
    # "search it up", "look it up", "find them" -- a phrasal verb with a
    # pronoun object. What to search for is whatever the pronoun refers to,
    # which lives in the conversation. Taking the pronoun literally is how
    # "search it up on Zelo" became a search for the string "it up on Zelo"
    # and the reply "Got it, it up on Zelo is open."
    r"(?!(?:it|that|this|them|those|these|one|ones)\b)"
    r"(?P<query>.+?)"
    # The article was missing "a"/"an", so "in a new browser tab" never
    # matched this trailing group and the surface ended up inside the
    # query: "wireless keyboards in a new browser tab" was searched for
    # verbatim. The destination is never part of what to search for.
    r"(?:\s+(?:in|on|using)\s+(?:(?:my|the|a|an)\s+)?"
    r"(?:new\s+)?(?:browser|tab).*)?[.?!]*$",
    flags=re.IGNORECASE,
)
_SPOKEN_FILESYSTEM_ACTION = re.compile(
    r"^\s*(?P<verb>create|make|add|delete|remove|trash|get\s+rid\s+of|move)"
    r"\s+(?:(?:me\s+)?(?:an?|the)\s+)?(?:(?:empty|blank)\s+)?"
    r"(?:(?P<kind>file|folder|directory)\s+)?(?:(?:named|called)\s+)?"
    r"(?P<target>.+?)\s+(?:in|inside|from|on|under)\s+"
    r"(?:(?:my|the)\s+)?(?P<location>documents|downloads|desktop)\b",
    flags=re.IGNORECASE,
)
_MEMORY_RECALL_QUESTION = re.compile(
    r"^\s*(?:what\s+do\s+you\s+remember(?:\s+about)?|"
    r"tell\s+me\s+what\s+you\s+remember)\b",
    flags=re.IGNORECASE,
)

# "World Cup" without a sport qualifier conventionally means the men's FIFA
# tournament. A small model sometimes binds "recent" to the current calendar
# year even while that edition is still scheduled, or turns a national-team
# match into the tournament winner. This is narrow periodic-event
# normalization, not a sports keyword router: explicitly named cricket, rugby,
# women's, hockey, basketball, or other World Cups remain model-owned.
_OTHER_WORLD_CUP = re.compile(
    r"\b(?:women(?:'s)?|cricket|rugby|hockey|basketball|fiba|icc)\b",
    flags=re.IGNORECASE,
)


def _open_app_has_unresolved_goal(transcript: str, target: str) -> bool:
    words = re.findall(r"[^\W_]+", str(transcript).casefold())
    target_words = set(re.findall(r"[^\W_]+", str(target).casefold()))
    remaining = [
        word
        for word in words
        if word not in target_words and word not in _OPEN_APP_GRAMMAR_WORDS
    ]
    return bool(remaining)


TIME_SCOPES = {"timeless", "current", "historical", "future", "unknown"}
REQUEST_EXPLICITNESS_VALUES = {"direct", "indirect", "statement", "unknown"}


@dataclass(frozen=True)
class IntentDecision:
    intent: str
    confidence: float
    normalized_request: str
    reason: str = ""
    search_query: str = ""
    topic: str = ""
    entity: str = ""
    aliases: tuple[str, ...] = ()
    is_follow_up: bool = False
    speech_act: str = ""
    action_requested: bool = False
    action_target: str = ""
    topic_shift: bool = False
    consent_decision: str = ""
    offered_intent: str = ""
    offered_request: str = ""
    memory_relevant: bool = False
    memory_candidate: bool = False
    detailed_response: bool = False
    screen_target: str = "configured"
    verification_required: bool = False
    information_freshness: str = "unknown"
    requires_external_evidence: bool = False
    recommendation_needed: bool = False
    urgent_safety: bool = False
    advice_domain: str = "general"
    time_scope: str = "unknown"
    request_explicitness: str = "unknown"
    computer_operation: str = "none"
    computer_location: str = ""
    computer_url: str = ""


class SemanticIntentRouter:
    """Use a small structured LLM call to select exactly one Elaina feature."""

    def __init__(
        self,
        client: Any,
        model: str,
        keep_alive: int | str = -1,
        safety_mode: str = "enforce",
        medium_confidence_threshold: float = 0.5,
        clarification_enabled: bool = True,
        print_confidence_log: bool = True,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self.safety_mode = (
            safety_mode
            if safety_mode in {"enforce", "shadow", "off"}
            else "enforce"
        )
        self.medium_confidence_threshold = float(medium_confidence_threshold)
        self.clarification_enabled = bool(clarification_enabled)
        self.print_confidence_log = bool(print_confidence_log)

    def route(
        self,
        user_input: str,
        *,
        recent_turns: list[dict[str, str]] | None = None,
        has_screen_selection: bool = False,
        has_selected_text: bool = False,
        project_tools_available: bool = False,
        conversation_state: dict[str, Any] | None = None,
        pending_action: str = "",
        computer_control_enabled: bool = False,
    ) -> IntentDecision:
        # Clicking Screen is an explicit user action, not an ambiguous phrase.
        # The next spoken request must use the attached image even if the
        # classifier would otherwise interpret "What game is this?" as normal
        # conversation or a general-knowledge question.
        if has_screen_selection:
            return IntentDecision(
                intent="screen_analysis",
                confidence=1.0,
                normalized_request=user_input,
                reason="A manually selected screen region is attached.",
            )

        # Copied/highlighted text follows the same one-turn attachment rule.
        if has_selected_text:
            return IntentDecision(
                intent="selected_text_question",
                confidence=1.0,
                normalized_request=user_input,
                reason="Highlighted text is attached to this request.",
            )

        state = conversation_state or {}
        spelled_entity = self._extract_spelled_entity(user_input)
        if spelled_entity:
            aliases = []
            previous_entity = str(state.get("active_entity", "")).strip()
            if previous_entity and previous_entity.casefold() != spelled_entity.casefold():
                aliases.append(previous_entity)

            # Capture a likely misheard name immediately before the spelling.
            correction_match = re.search(
                r"\b(?:said|mean|for)\s+([A-Za-z][A-Za-z'-]{1,30})",
                user_input,
                flags=re.IGNORECASE,
            )
            if correction_match:
                alias = correction_match.group(1)
                if alias.casefold() != spelled_entity.casefold():
                    aliases.append(alias)

            return IntentDecision(
                intent="entity_correction",
                confidence=1.0,
                normalized_request=spelled_entity,
                reason=(
                    "The user explicitly spelled the canonical entity name."
                ),
                topic=str(state.get("active_topic", "")).strip(),
                entity=spelled_entity,
                aliases=tuple(dict.fromkeys(aliases)),
                is_follow_up=True,
            )

        routed_input = self._apply_scoped_entity_alias(
            user_input,
            state,
        )
        context = recent_turns or []
        prompt = self._build_prompt(
            user_input=routed_input,
            recent_turns=context[-6:],
            has_screen_selection=has_screen_selection,
            has_selected_text=has_selected_text,
            project_tools_available=project_tools_available,
            conversation_state=state,
            pending_action=pending_action,
            computer_control_enabled=computer_control_enabled,
        )

        try:
            _route_call_started = time.perf_counter()
            response = self.client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": prompt,
                    },
                    {
                        "role": "user",
                        "content": routed_input,
                    },
                ],
                stream=False,
                format="json",
                options={
                    "temperature": 0,
                    # Measured in 4E-I: this schema emits a median of 268
                    # output tokens across 31 fields. A 320 cap left 52
                    # tokens of headroom, and a turn whose free-text
                    # "reason" ran long was truncated mid-JSON -- which is
                    # not a parse failure the repair pass can fix, because
                    # the missing half was never generated. Seen once in 55
                    # turns of the first dogfooding session, and a router
                    # failure puts every layer after it on a fallback.
                    #
                    # The cap costs nothing when it is not reached: decode
                    # stops at the stop token either way.
                    "num_predict": ROUTER_OUTPUT_CAP,
                },
                keep_alive=self.keep_alive,
                think=False,
            )
            timing.mark(
                "route_model", time.perf_counter() - _route_call_started,
            )
            raw_content = self._value(
                self._value(response, "message", {}),
                "content",
                "",
            )
            decision = self._parse_decision(raw_content, routed_input)
            if decision is None:
                print(
                    "[Router] Invalid structured output; retrying once in "
                    "JSON repair mode."
                )
                repair_response = self.client.chat(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Repair an intent-router response. Return one "
                                "valid JSON object only. Required keys: "
                                "intent, confidence, normalized_request, "
                                "reason, search_query, topic, entity, aliases, "
                                "is_follow_up, speech_act, action_requested, "
                                "action_target, topic_shift, consent_decision, "
                                "offered_intent, offered_request, "
                                "memory_relevant, memory_candidate, "
                                "detailed_response, screen_target, "
                                "verification_required, information_freshness, "
                                "requires_external_evidence, "
                                "recommendation_needed, urgent_safety, "
                                "advice_domain, time_scope, "
                                "request_explicitness, computer_operation, "
                                "computer_location, computer_url. "
                                "Intent must "
                                "be one of: "
                                + ", ".join(sorted(ALLOWED_INTENTS))
                                + ". Do not answer the user."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"Current transcript: {routed_input}\n"
                                "Invalid response:\n"
                                f"{str(raw_content)[:1200]}"
                            ),
                        },
                    ],
                    stream=False,
                    format="json",
                    options={
                        "temperature": 0,
                        "num_predict": ROUTER_OUTPUT_CAP,
                    },
                    keep_alive=self.keep_alive,
                    think=False,
                )
                repair_content = self._value(
                    self._value(repair_response, "message", {}),
                    "content",
                    "",
                )
                decision = self._parse_decision(
                    repair_content,
                    routed_input,
                )
            if decision is not None:
                decision = self._normalize_deterministic_request_shape(
                    decision,
                    original_input=user_input,
                )
                decision = self._apply_periodic_event_policy(
                    decision,
                    original_input=user_input,
                )
                decision = self._apply_computer_control_policy(
                    decision,
                    original_input=user_input,
                    computer_control_enabled=computer_control_enabled,
                    active_desktop_surface=state.get("active_desktop_surface"),
                    recently_created_items=tuple(
                        state.get("recently_created_items", ()) or ()
                    ),
                )
                safe_decision = self._apply_action_safety_policy(
                    decision,
                    original_input=user_input,
                )
                if (
                    self.safety_mode == "shadow"
                    and safe_decision.intent != decision.intent
                ):
                    print(
                        "[Router Shadow] "
                        f"{decision.intent} -> {safe_decision.intent}: "
                        f"{safe_decision.reason}"
                    )
                elif self.safety_mode == "enforce":
                    decision = safe_decision
                decision = self._apply_optional_capability_policy(
                    decision, original_input=user_input,
                )
                # The model sees recent turns and can reproduce a
                # transcription error from one of them instead of what was
                # just said -- "universe" for "university", one turn after
                # the user had corrected exactly that. Their words win.
                #
                # Every field the model wrote, not only the request. The
                # restoration used to apply to ``normalized_request``
                # alone, so a turn could leave here saying two different
                # things at once -- measured live:
                #
                #   [Router] restored 'Are there Casinos in Seattle?'
                #   [Router] conversation (0.95): The user is asking about
                #            the existence of Cousinos in Seattle
                #
                # The corrected transcript has to be the only version of
                # the turn that survives, or every layer downstream picks
                # whichever field it happens to read.
                restored = _restore_misheard_words(
                    decision.normalized_request, user_input,
                )
                if restored != decision.normalized_request:
                    print(
                        f"[Router] restored {restored!r} from the transcript."
                    )
                    decision = replace(
                        decision,
                        normalized_request=restored,
                        **{
                            field: _restore_misheard_words(
                                getattr(decision, field), user_input,
                            )
                            for field in (
                                "reason", "topic", "entity", "search_query",
                                "action_target",
                            )
                            if getattr(decision, field, "")
                        },
                    )
                if (
                    decision.intent in ACTION_INTENTS
                    and decision.speech_act == "action_request"
                ):
                    decision = replace(
                        decision,
                        action_requested=True,
                    )
                if decision.intent in {
                    "web_search",
                    "entity_correction",
                    "fact_check",
                    "project_question",
                    "screen_analysis",
                }:
                    # These capabilities only retrieve evidence. Once the
                    # semantic router determines that the user directly needs
                    # them, do not require magic words or a duplicate consent
                    # turn before doing the read-only work.
                    #
                    # Whether the person asked at all is settled once, at the
                    # end of this method, by _withhold_unrequested_research --
                    # there are three separate places that promote a research
                    # intent to action_requested, and guarding them one at a
                    # time left the other two to undo it.
                    decision = replace(
                        decision,
                        action_requested=True,
                    )
                if (
                    decision.intent == "agent_offer"
                    and decision.speech_act == "action_request"
                    and decision.offered_intent in ACTION_INTENTS
                ):
                    direct_intent = decision.offered_intent
                    direct_request = (
                        decision.offered_request
                        or decision.normalized_request
                    )
                    decision = replace(
                        decision,
                        intent=direct_intent,
                        normalized_request=direct_request,
                        reason=(
                            "The user directly requested this agent action; "
                            "the request itself grants permission to start."
                        ),
                        search_query=(
                            decision.search_query or direct_request
                            if direct_intent == "web_search"
                            else decision.search_query
                        ),
                        action_requested=True,
                        offered_intent="",
                        offered_request="",
                    )
                if (
                    decision.intent in {
                        "knowledge_question",
                        "project_question",
                        "project_edit",
                        "agent_offer",
                        "agent_create",
                    }
                    and decision.speech_act == "advice"
                ):
                    decision = replace(
                        decision,
                        intent="conversation",
                        confidence=max(decision.confidence, 0.95),
                        normalized_request=user_input.strip(),
                        reason=(
                            "An opinion or personal-value question belongs in "
                            "conversation, not the factual report path."
                        ),
                        search_query="",
                        speech_act="advice",
                        action_requested=False,
                        action_target="",
                    )
                decision = self._apply_factual_source_policy(decision)
                decision = self._apply_confidence_clarification_policy(
                    decision,
                    medium_confidence_threshold=self.medium_confidence_threshold,
                    clarification_enabled=self.clarification_enabled,
                    print_confidence_log=self.print_confidence_log,
                )
                # Last, so nothing can promote it back afterwards.
                decision = self._withhold_unrequested_research(
                    decision, original_input=user_input,
                )
                return decision
        except Exception as error:
            print(
                f"[Router] Structured classification failed: "
                f"{type(error).__name__}: {error}"
            )

        return self._safe_fallback(
            routed_input,
            has_screen_selection=has_screen_selection,
            has_selected_text=has_selected_text,
        )

    @staticmethod
    def _extract_spelled_entity(user_input: str) -> str:
        match = re.search(
            r"(?<![A-Za-z])(?:[A-Za-z][\s-]+){2,}[A-Za-z](?![A-Za-z])",
            user_input,
        )
        if not match:
            return ""

        letters = re.findall(r"[A-Za-z]", match.group(0))
        if not 3 <= len(letters) <= 16:
            return ""
        return "".join(letters).capitalize()

    @classmethod
    def _apply_scoped_entity_alias(
        cls,
        user_input: str,
        state: dict[str, Any],
    ) -> str:
        canonical = str(state.get("active_entity", "")).strip()
        if not canonical:
            return user_input

        aliases = {
            str(alias).casefold(): str(target)
            for alias, target in dict(
                state.get("entity_aliases", {})
            ).items()
        }
        canonical_soundex = cls._soundex(canonical)

        def replace(match: re.Match) -> str:
            word = match.group(0)
            explicit = aliases.get(word.casefold())
            if explicit:
                return explicit
            if (
                len(word) >= 4
                and word[0].casefold() == canonical[0].casefold()
                and cls._soundex(word) == canonical_soundex
            ):
                return canonical
            return word

        return re.sub(r"\b[A-Za-z][A-Za-z'-]*\b", replace, user_input)

    @staticmethod
    def _soundex(value: str) -> str:
        letters = re.sub(r"[^A-Za-z]", "", value).upper()
        if not letters:
            return ""
        groups = {
            **dict.fromkeys("BFPV", "1"),
            **dict.fromkeys("CGJKQSXZ", "2"),
            **dict.fromkeys("DT", "3"),
            "L": "4",
            **dict.fromkeys("MN", "5"),
            "R": "6",
        }
        encoded = [letters[0]]
        previous = groups.get(letters[0], "")
        for letter in letters[1:]:
            digit = groups.get(letter, "")
            if digit and digit != previous:
                encoded.append(digit)
            previous = digit
        return ("".join(encoded) + "000")[:4]

    @staticmethod
    def _normalize_deterministic_request_shape(
        decision: IntentDecision,
        *,
        original_input: str,
    ) -> IntentDecision:
        """Keep exact, user-spoken targets stable across model phrasing.

        The router still supplies the meaning for open/close/UI work. These
        few request shapes have a safer local truth: a URL, a search query,
        a file/folder name, and a direct question about stored memories. A
        normalizer prevents the model's explanatory words from becoming the
        filesystem or browser target without broadening any capability.
        """
        text = " ".join(str(original_input or "").split())
        if _MEMORY_RECALL_QUESTION.match(text):
            return replace(
                decision,
                intent="conversation",
                normalized_request=text,
                reason="The user asked about their own stored memories.",
                action_requested=False,
                action_target="",
                computer_operation="none",
                memory_relevant=True,
            )

        if decision.intent != "computer_action":
            return decision

        operation = decision.computer_operation
        if operation == "open_url":
            match = _SPOKEN_URL.search(text)
            if match is not None:
                return replace(
                    decision,
                    action_target=match.group(0).rstrip(".,!?)\"]}"),
                )

        if operation == "open_search":
            match = _SPOKEN_SEARCH.search(text)
            if match is not None:
                query = match.group("query").strip(" .!?")
                if query:
                    return replace(decision, action_target=query)

        filesystem = _SPOKEN_FILESYSTEM_ACTION.match(text)
        if filesystem is None:
            return decision

        target = filesystem.group("target").strip(" .,!?'\"")
        target = re.sub(r"^(?:my|the|a|an)\s+", "", target, flags=re.IGNORECASE)
        kind = (filesystem.group("kind") or "").casefold()
        trailing_kind = re.search(
            r"\s+(file|folder|directory)\s*$", target, flags=re.IGNORECASE,
        )
        if trailing_kind is not None:
            kind = kind or trailing_kind.group(1).casefold()
            target = target[:trailing_kind.start()].strip()
        if not target:
            return decision

        verb = filesystem.group("verb").casefold().replace("  ", " ")
        creates = verb in {"create", "make", "add"}
        deletes = verb in {"delete", "remove", "trash", "get rid of", "move"}
        if not kind:
            kind = (
                "folder" if operation in {"create_folder", "delete_folder"}
                else "file" if operation in {"create_file", "delete_file"}
                else ""
            )
        if not kind or not (creates or deletes):
            return decision

        inferred_operation = (
            ("create_" if creates else "delete_")
            + ("folder" if kind in {"folder", "directory"} else "file")
        )
        location = filesystem.group("location").title()
        return replace(
            decision,
            intent="computer_action",
            normalized_request=text,
            speech_act="action_request",
            action_target=target,
            computer_operation=inferred_operation,
            computer_location=location,
        )

    @staticmethod
    def _apply_action_safety_policy(
        decision: IntentDecision,
        *,
        original_input: str,
    ) -> IntentDecision:
        """
        Prevent vague conversation from reaching project write tools.

        The semantic model proposes an intent, but a local policy owns the
        authorization boundary. Only a direct request for a concrete change may
        remain project_edit. Uncertainty falls back to conversation.
        """
        if decision.intent != "project_edit":
            return decision

        if (
            decision.speech_act == "action_request"
            and decision.action_requested
            and decision.action_target.strip()
        ):
            return decision

        return replace(
            decision,
            intent="conversation",
            normalized_request=original_input.strip(),
            reason=(
                "Safety policy downgraded project_edit because the user did "
                "not semantically authorize a concrete file change."
            ),
            search_query="",
            action_requested=False,
            action_target="",
        )

    @staticmethod
    def _apply_computer_control_policy(
        decision: IntentDecision,
        *,
        original_input: str,
        computer_control_enabled: bool = False,
        active_desktop_surface: dict[str, Any] | None = None,
        recently_created_items: tuple[dict[str, Any], ...] = (),
    ) -> IntentDecision:
        """Enforce grounded Phase 4A operations and UI mode authorization."""
        if decision.intent != "computer_action":
            return decision

        target = decision.action_target.strip()
        operation = decision.computer_operation
        surface_kind = str(
            (active_desktop_surface or {}).get("kind", "")
        ).strip().casefold()
        has_deictic_reference = bool(_DEICTIC_SURFACE_REFERENCE.search(original_input))
        implicit_browser_action = bool(_IMPLICIT_BROWSER_ACTION.search(original_input))
        unambiguous_browser_page_action = bool(
            _UNAMBIGUOUS_BROWSER_PAGE_ACTION.search(original_input)
        )
        explicit_native_app = bool(_EXPLICIT_NATIVE_APP_REFERENCE.search(original_input))
        if (
            operation == "open_search"
            and surface_kind == "browser"
            and unambiguous_browser_page_action
            and not explicit_native_app
        ):
            decision = replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "The current browser page supplies the target for this "
                    "unambiguous page action."
                ),
                action_target=original_input.strip(),
                computer_operation="browser_action",
            )
            target = decision.action_target.strip()
            operation = decision.computer_operation
        page_reference = bool(_DEICTIC_PAGE_REFERENCE.search(original_input))
        if (
            operation in {"ui_action", "browser_action"}
            and (
                has_deictic_reference
                or (
                    surface_kind == "browser"
                    and implicit_browser_action
                    and not explicit_native_app
                )
            )
        ):
            # A named page beats an unknown surface. Falling back to
            # ui_action whenever no live surface was known sent every
            # "...on this page" request at the desktop instead.
            #
            # A page reference outranks an app-sounding word, because the
            # sentence says outright which surface it means: "Click Settings
            # on this GitHub page" is a link named Settings on a webpage,
            # not the Windows Settings app.
            if surface_kind == "browser" or page_reference:
                corrected = "browser_action"
            else:
                corrected = "ui_action"
            if corrected != operation:
                decision = replace(decision, computer_operation=corrected)
                operation = corrected
        elif (
            operation == "browser_action"
            and explicit_native_app
            and not page_reference
            and surface_kind != "browser"
        ):
            # The mirror of the rule above, and the direction that had no
            # guard at all: "Can you search for Laufey in Spotify?" names an
            # installed app, names no page, and has no browser in front, yet
            # came back browser_action. Nothing corrected it, so the answer
            # depended on which label the model happened to pick that run.
            decision = replace(
                decision,
                reason=(
                    "The request names an installed desktop application and "
                    "no web page, so it belongs to the native UI planner."
                ),
                computer_operation="ui_action",
            )
            operation = decision.computer_operation
        elif (
            operation == "unsupported"
            and page_reference
            and unambiguous_browser_page_action
            and not explicit_native_app
        ):
            # Acting on the page in front of you is supported, and the model
            # sometimes says otherwise: "fill the search box on this hotel
            # page" came back unsupported. Narrow on purpose -- it needs an
            # explicit page reference *and* a committed page verb, and it
            # only ever refines a decision already routed to computer_action,
            # so a conversational remark can never reach it. The browser
            # planner still verifies the live DOM before touching anything.
            decision = replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "The request names a page and a page action, which the "
                    "browser page driver supports."
                ),
                computer_operation="browser_action",
            )
            operation = decision.computer_operation
        elif (
            operation == "list_windows"
            and _FOCUS_WINDOW_COMMAND.search(original_input)
        ):
            # "Bring VS Code to the front" is an instruction to change which
            # window is focused, not a question about which windows exist.
            decision = replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "Raising a named window is a UI action, not a question "
                    "about which windows are open."
                ),
                computer_operation="ui_action",
            )
            operation = decision.computer_operation

        # The prompt states this contract for both page and app actions --
        # "action_target is the complete request verbatim" -- and the model
        # honours it unevenly, dropping the qualifier that says *where*:
        # "search for Laufey in Spotify" came back as "search for Laufey".
        # The planner reads normalized_request rather than this field, so
        # nothing downstream broke; enforcing it here simply makes the value
        # match its documented meaning instead of varying per turn.
        if operation in {"ui_action", "browser_action"}:
            decision = replace(decision, action_target=original_input.strip())
            target = decision.action_target.strip()
        if (
            operation == "open_app"
            and target
            and transcript_names_target(original_input, target)
            and _open_app_has_unresolved_goal(original_input, target)
        ):
            decision = replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "The request contains an in-app outcome, so local policy "
                    "preserved the complete goal for the verified UI planner."
                ),
                action_target=original_input.strip(),
                computer_operation="ui_action",
            )
            target = decision.action_target.strip()
            operation = decision.computer_operation
        if (
            operation in {"create_file", "create_folder"}
            and _COMPOUND_FILE_CONTENT_REQUEST.search(original_input)
        ):
            return replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "Creating the file is supported; writing content into it "
                    "is not, so the request is refused rather than half done."
                ),
                action_requested=False,
                computer_operation="unsupported",
            )
        if (
            operation in {"delete_file", "delete_folder"}
            and _IRREVERSIBLE_DELETE_REQUEST.search(original_input)
        ):
            return replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "Permanent deletion is outside the recoverable desktop "
                    "control policy and cannot reach a filesystem tool."
                ),
                action_requested=False,
                computer_operation="unsupported",
            )
        # list_windows has no target to name ("what's open?"), and
        # describe_window may reasonably omit one too ("what's on my
        # screen?" defaults to the active window). Grounding a target that
        # was never spoken would reject every legitimate targetless
        # observation request, so only require it when one was provided.
        # ui_action's target is a paraphrase of the whole goal, not one
        # named entity -- exact substring grounding doesn't apply to it,
        # and it doesn't need to: the desktop action planner verifies every
        # window and control it touches against the live UI Automation
        # tree before acting, so grounding is enforced downstream instead.
        target_is_grounded = (
            (operation in OBSERVATION_OPERATIONS and not target)
            or operation in UI_ACTION_OPERATIONS
            or transcript_names_target(original_input, target)
        )

        # A referential delete ("delete the test folder we just made")
        # never repeats the literal name grounded above -- the model must
        # not invent one, but a real, locally-recorded item Elaina herself
        # just created is trustworthy ground truth the same way
        # active_desktop_surface already is for "this window". Only a
        # single unambiguous recent match of the right kind resolves;
        # zero or multiple candidates fall through to the existing refusal.
        resolved_from_session = False
        if (
            not target_is_grounded
            and operation in {"delete_file", "delete_folder"}
            and _REFERENTIAL_ITEM_REFERENCE.search(original_input)
        ):
            wanted_kind = "folder" if operation == "delete_folder" else "file"
            matches = [
                item
                for item in recently_created_items
                if str(item.get("kind", "")) == wanted_kind
            ]
            if len(matches) == 1:
                resolved = matches[0]
                decision = replace(
                    decision,
                    action_target=str(resolved.get("name", "")).strip(),
                    computer_location=str(resolved.get("location", "")).strip(),
                )
                target = decision.action_target
                target_is_grounded = True
                resolved_from_session = True

        location_is_grounded = (
            resolved_from_session
            or not decision.computer_location
            or transcript_names_location(original_input, decision.computer_location)
        )
        # Observation questions are naturally phrased, and correctly
        # classified, as information_request ("what windows are open?"),
        # not action_request. Every other computer operation changes
        # something and rightly needs a command-shaped request; these two
        # have no side effect at all, so there is no safety reason to
        # reject the phrasing the model got right.
        speech_act_is_grounded = decision.speech_act == "action_request" or (
            operation in OBSERVATION_OPERATIONS
            and decision.speech_act == "information_request"
        )
        if (
            not speech_act_is_grounded
            or operation not in COMPUTER_OPERATIONS
            or operation in {"none", "unsupported"}
            or not target_is_grounded
            or not location_is_grounded
        ):
            return replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "The computer request is not a grounded Phase 4A action "
                    "and must not reach a computer tool."
                ),
                action_requested=False,
                computer_operation="unsupported",
            )

        if not computer_control_enabled:
            return replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "The computer action is understood, but Desktop Control "
                    "Mode is off. Elaina may explain the action or recommend "
                    "turning the mode on, but cannot execute it."
                ),
                action_requested=False,
                action_target=target,
            )

        return replace(
            decision,
            action_requested=True,
            action_target=target,
        )

    @staticmethod
    def _withhold_unrequested_research(
        decision: IntentDecision,
        *,
        original_input: str,
    ) -> IntentDecision:
        """Keep the intent, withhold the authority to act, on a remark.

        Three separate rules promote a research intent to
        ``action_requested`` -- the read-only block in ``route``, the
        recommendation escalation, and the knowledge_question escalation --
        and none of them asked whether the person had requested anything.
        Measured live, that turned a complaint into a web search:

            "Spotify won't play anything today."  ->  web search

        Guarding the three sites individually left whichever ran last to
        undo the others, so the question is settled once, here, after every
        one of them has finished.

        The intent is deliberately left alone. Looking it up is still the
        kind of thing that would help; what the turn lacks is a request. The
        interaction layer reads ``request_explicitness`` and offers instead
        of searching, so this produces an offer rather than silence.
        """
        if decision.intent not in _RESEARCH_INTENTS_WITHHELD:
            return decision
        if not decision.action_requested:
            return decision
        if _REQUEST_SHAPE.search(str(original_input or "")):
            return decision
        return replace(
            decision,
            action_requested=False,
            request_explicitness="statement",
            reason=(
                "The turn is a remark rather than a request, so looking it "
                "up is worth offering but not worth doing unasked."
            ),
        )

    @staticmethod
    def _apply_factual_source_policy(
        decision: IntentDecision,
    ) -> IntentDecision:
        """Keep local factual answers behind an explicit stability contract."""
        if decision.intent == "web_search":
            return replace(
                decision,
                requires_external_evidence=True,
                verification_required=(
                    decision.verification_required
                    or decision.information_freshness in {"changing", "live"}
                    or (
                        decision.recommendation_needed
                        and decision.advice_domain
                        in {"health", "financial", "legal"}
                    )
                ),
            )

        if (
            decision.intent == "time_question"
            and decision.requires_external_evidence
            # A clock somewhere else is arithmetic, not evidence. Measured
            # live, "Search me the current time of Seattle" went to the web
            # and came back with a query carrying three unrelated topics;
            # the offset was knowable the whole time.
            and not world_clock.read_place(decision.normalized_request)
        ):
            return replace(
                decision,
                intent="web_search",
                reason=(
                    "The requested live value depends on external evidence, "
                    "not only the user's local clock or calendar."
                ),
                search_query=(
                    decision.search_query or decision.normalized_request
                ),
                action_requested=True,
                verification_required=True,
                requires_external_evidence=True,
            )

        if (
            decision.intent == "conversation"
            and decision.recommendation_needed
            and (
                decision.requires_external_evidence
                or decision.advice_domain in {"health", "financial", "legal"}
            )
            and not decision.urgent_safety
        ):
            return replace(
                decision,
                intent="web_search",
                reason=(
                    "The requested recommendation depends on current external "
                    "evidence rather than model knowledge alone."
                ),
                search_query=(
                    decision.search_query or decision.normalized_request
                ),
                action_requested=True,
                verification_required=(
                    decision.verification_required
                    or decision.information_freshness
                    in {"changing", "live", "unknown"}
                ),
                requires_external_evidence=True,
            )

        if decision.intent != "knowledge_question":
            return decision

        can_use_local_knowledge = (
            decision.information_freshness == "stable"
            and not decision.requires_external_evidence
            and decision.time_scope not in {"current", "future"}
            and not (
                decision.recommendation_needed
                and decision.advice_domain in {"health", "financial", "legal"}
            )
        )
        if can_use_local_knowledge:
            return decision

        return replace(
            decision,
            intent="web_search",
            reason=(
                "Factual source policy requires external evidence because the "
                "answer is not explicitly classified as stable local knowledge."
            ),
            search_query=(
                decision.search_query or decision.normalized_request
            ),
            action_requested=True,
            verification_required=(
                decision.verification_required
                or decision.information_freshness
                in {"changing", "live", "unknown"}
                or decision.time_scope in {"current", "future"}
                or (
                    decision.recommendation_needed
                    and decision.advice_domain
                    in {"health", "financial", "legal"}
                )
            ),
            requires_external_evidence=True,
        )

    @staticmethod
    def _apply_periodic_event_policy(
        decision: IntentDecision,
        *,
        original_input: str,
    ) -> IntentDecision:
        """Resolve an unqualified recent World Cup to a completed tournament.

        This corrects only the information request and query. Search still
        supplies the winner; no champion or edition year is hardcoded here.
        """
        text = " ".join(str(original_input or "").split())
        lowered = text.casefold()
        asks_for_recent_winner = (
            "world cup" in lowered
            and any(word in lowered for word in ("recent", "latest", "last"))
            and any(word in lowered for word in ("won", "winner", "champion"))
        )
        if not asks_for_recent_winner or _OTHER_WORLD_CUP.search(text):
            return decision

        as_of = datetime.now().strftime("%Y-%m-%d")
        return replace(
            decision,
            intent="web_search",
            normalized_request=text,
            reason=(
                "The latest completed FIFA Men's World Cup winner is a "
                "date-dependent tournament result and needs current evidence."
            ),
            search_query=(
                "latest completed edition FIFA Men's World Cup tournament "
                f"champion official as of {as_of}"
            ),
            topic="FIFA Men's World Cup",
            entity="FIFA Men's World Cup",
            speech_act="information_request",
            action_requested=True,
            verification_required=True,
            information_freshness="historical_record",
            requires_external_evidence=True,
            recommendation_needed=False,
            advice_domain="general",
            time_scope="current",
        )

    @staticmethod
    def _apply_optional_capability_policy(
        decision: IntentDecision,
        original_input: str = "",
    ) -> IntentDecision:
        """Turn indirect interest in a read-only capability into an offer."""
        if decision.intent == "project_question" and not _NAMES_THE_PROJECT.search(
            # Both, because the model's paraphrase can drop the very word
            # that made it a project question: "Inspect the codebase and
            # explain how voice input reaches chat" came back normalized as
            # "Explain the voice input flow."
            f"{original_input} {decision.normalized_request or ''}",
        ):
            # "Project" has an everyday sense, and the model reads it.
            # Measured live: "I'm interested in like AI software companies"
            # came back as project_question 0.95 -- "a specific
            # project-related inquiry" -- and the Coding Agent went and
            # searched Elaina's own source tree for internship employers.
            #
            # This intent means one thing: the local codebase. A turn that
            # names nothing in it is not asking about it.
            return replace(
                decision,
                intent="conversation",
                reason=(
                    "The turn names nothing in the local project, so it is "
                    "not a question about the codebase."
                ),
                action_requested=False,
                action_target="",
            )
        if (
            decision.intent == "project_question"
            and decision.recommendation_needed
            and decision.memory_relevant
        ):
            return replace(
                decision,
                intent="conversation",
                reason=(
                    "A personal recommendation based on memory does not require "
                    "inspecting project files."
                ),
                action_requested=False,
                action_target="",
            )

        if (
            decision.intent not in {"screen_analysis", "project_question"}
            or decision.request_explicitness not in {"indirect", "statement"}
        ):
            return decision

        return replace(
            decision,
            intent="agent_offer",
            reason=(
                "The user expressed indirect interest in a capability rather "
                "than directly asking Elaina to run it."
            ),
            action_requested=False,
            action_target="",
            offered_intent=decision.intent,
            offered_request=decision.normalized_request,
        )

    @staticmethod
    def _apply_confidence_clarification_policy(
        decision: IntentDecision,
        *,
        medium_confidence_threshold: float,
        clarification_enabled: bool,
        print_confidence_log: bool = True,
    ) -> IntentDecision:
        """Ask instead of guessing when an ability-boundary call is unsure.

        Runs last, after every deterministic policy above. Those already
        used real local state (active_desktop_surface, transcript
        grounding, recently_created_items) to correct or refuse the
        model's raw guess where possible -- this only catches what
        survives that: a genuinely low-confidence choice between two real
        abilities, never a request a policy already grounded or refused.
        """
        if not clarification_enabled:
            return decision
        if not decision.action_requested:
            return decision
        if decision.intent not in _ABILITY_BOUNDARY_INTENTS:
            return decision
        if decision.confidence >= medium_confidence_threshold:
            return decision
        if print_confidence_log:
            print(
                "[Router Confidence] "
                f"{decision.intent} -> clarification "
                f"({decision.confidence:.2f} < "
                f"{medium_confidence_threshold:.2f}): {decision.reason}"
            )
        return replace(
            decision,
            intent="clarification",
            action_requested=False,
            reason=(
                f"Low routing confidence ({decision.confidence:.2f}): "
                f"{decision.reason}"
            ),
        )

    @staticmethod
    def _value(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    @staticmethod
    def _build_prompt(
        *,
        user_input: str,
        recent_turns: list[dict[str, str]],
        has_screen_selection: bool,
        has_selected_text: bool,
        project_tools_available: bool,
        conversation_state: dict[str, Any],
        pending_action: str,
        computer_control_enabled: bool,
    ) -> str:
        now = datetime.now()
        state = {
            "screen_selection_attached": has_screen_selection,
            "selected_text_attached": has_selected_text,
            "project_tools_available": project_tools_available,
            "computer_control_enabled": computer_control_enabled,
            "pending_action": pending_action,
            "pending_agent_offer": conversation_state.get(
                "pending_agent_offer"
            ),
            "current_date": now.strftime("%A, %B %d, %Y"),
            "current_year": now.year,
        }

        return (
            "You are Elaina's semantic intent router. Choose exactly one "
            "intent from this allowlist:\n"
            "conversation, calculation, agent_offer, agent_consent, web_search, "
            "project_question, project_edit, "
            "git_commit, git_publish, screen_analysis, computer_action, "
            "selected_text_question, knowledge_question, time_question, "
            "pending_approval, agent_create, calendar_action, "
            "entity_correction, fact_check, clarification.\n\n"
            "Infer meaning instead of matching exact phrases; account for "
            "speech-to-text errors. Recent turns outrank an older active_topic "
            "for resolving follow-ups, pronouns, and corrections.\n\n"
            "If the user spells or corrects a name, set entity to the "
            "corrected form and put earlier misheard forms in aliases (e.g. "
            "'Q W E N' means Qwen).\n\n"
            "If the most recent turn was a computer_action that failed "
            "(not found, ambiguous, or similar) and this message is just a "
            "short correction of the same target's name (a likely "
            "mishearing, e.g. the target was 'battle nest' and this message "
            "is 'Battle.net'), that is still computer_action retrying the "
            "same operation with the corrected target, never conversation. "
            "conversation must never claim a specific application opened, "
            "closed, or changed state -- only computer_action can report "
            "that, and only when it actually ran.\n\n"
            "Routing rules:\n"
            "- agent_offer: a concrete problem/desire/dissatisfaction a listed "
            "specialist could solve, without the user asking Elaina to do the "
            "work. Set offered_intent/offered_request; never auto-invoke. "
            "'The buttons look boring' -> agent_offer(project_edit), not "
            "project_edit. Unidentified visual musing with no screen "
            "selection ('I wonder who drew this') -> agent_offer"
            "(screen_analysis). A plain status update ('I'm working on my "
            "project tonight') is not an offer.\n"
            "- agent_consent: only when pending_agent_offer is present and "
            "this message responds to it. Set consent_decision to accept, "
            "reject, modify, or unclear from meaning, not a phrase list; "
            "modify carries the revised task in offered_request. A topic "
            "change routes normally instead.\n"
            "- git_publish: commit/push code to Git or GitHub. git_commit: "
            "commit locally, no push.\n"
            "- project_edit: the user directly delegates a specific project-"
            "file change. A plan or idea-request is project_question/"
            "conversation, not this.\n"
            "- project_question: the user directly asks Elaina to inspect or "
            "read project files -- not an indirect wish ('it would be nice "
            "if something could inspect this error'), which is agent_offer "
            "instead. An opinion or choice (e.g. Live2D vs 3D) is "
            "conversation.\n"
            "- screen_analysis: only with screen_selection_attached true, or "
            "an explicit visual look at a monitor/display right now ('look "
            "across both monitors', 'check my left screen', 'what's on my "
            "main display'). A plain 'what's on my screen'/'what's in this "
            "window' with no monitor/display cue is describe_window instead. "
            "Without an attachment, a vague visual musing is agent_offer. "
            "screen_target is configured, main, left, right, or all -- use "
            "all for both/every/across monitors.\n"
            "- computer_action: a direct request to control this Windows PC, "
            "based on outcome, no magic word needed. When "
            "computer_control_enabled is false, still identify the operation/"
            "target/location -- local policy blocks execution and Elaina "
            "recommends the Computer Control toggle. When true, a grounded "
            "request may execute. Pick one computer_operation: open_app, "
            "close_app, force_quit_app, open_url, open_search, create_file, "
            "create_folder, delete_file, delete_folder, list_windows, "
            "describe_window, ui_action, browser_action, or unsupported.\n"
            "  * open_app/close_app/force_quit_app: 'open', 'launch', "
            "'start', and 'run' all mean open_app -- action_target is only "
            "the bare application name ('Start the Calculator' -> open_app, "
            "target 'Calculator', never intent calculation). close_app is a "
            "normal close/exit/quit, including 'gracefully' ('Exit Steam "
            "gracefully' -> close_app, never force_quit_app); force_quit_app "
            "needs an explicit force/terminate/kill/entirely/completely "
            "word. close_app needs an installed application, never a "
            "browser tab ('Close the github.com browser tab' -> "
            "unsupported, never close_app).\n"
            "  * list_windows: any question about which apps, windows, or "
            "programs are open or running, or which one is currently active/"
            "in front/focused -- 'what apps are open', 'what windows do I "
            "have open', 'what's running', 'show me what's open', 'what "
            "window is in front right now' -- action_target is always "
            "empty. Never mark this unsupported. This is distinct from "
            "screen_analysis, which needs an explicit monitor/display/"
            "visual cue.\n"
            "  * describe_window: the default for 'what's on my screen', "
            "'what's in this window', or naming a window ('what controls "
            "are in Sound Settings', 'what controls are in the Notepad "
            "window') -- reads real accessible controls, not an image. "
            "action_target is the named window or empty for the active "
            "one. Whenever computer_operation is describe_window, intent "
            "must be computer_action, never screen_analysis.\n"
            # Deliberately unchanged. Adding a Windows-settings scope note
            # and a focus-vs-list_windows note here was measured against the
            # matrix and cost two unrelated cases -- "Force close VSCode
            # completely" started copying the whole sentence into
            # action_target, and the compound "create notes.txt ... and write
            # hello inside it" stopped being refused. Both passed before the
            # edit and failed consistently after it, three runs each.
            #
            # A small model reads this prompt as one weighted whole, so text
            # added for one operation shifts calibration for the others. The
            # settings-scope decision is enforced where it cannot regress
            # anything: in policy and in the matrix expectation. Both
            # behaviours this text described are handled deterministically
            # below instead (_FOCUS_WINDOW_COMMAND, and the model already
            # answers "unsupported" for a settings change unaided).
            "  * ui_action: click/type/focus/select/scroll inside a native, "
            "installed desktop application's own window -- desktop Spotify, "
            "Notepad, Settings, VS Code, and similar apps, never a "
            "website ('search for Laufey in Spotify' stays ui_action "
            "because desktop Spotify is an installed app, not a webpage). "
            "If conversation state's active_desktop_surface is a Spotify Web "
            "page, use browser_action instead. "
            "action_target is the complete request verbatim; never name "
            "the exact control yourself. Keep the requested outcome "
            "('play Dynamite in Spotify' stays ui_action, not open_app) "
            "even if the app must open first. Resolve 'this window/here/"
            "in it' against conversation state's active_desktop_surface.\n"
            "  * browser_action: click/fill/select/scroll/navigate/read "
            "content on a specific webpage the user is already looking at "
            "in the browser right now ('click Settings on this GitHub "
            "page', 'fill the search box on this page', 'compare these "
            "hotel listings', 'read me this article', 'book the best "
            "one' referring back to a page of results just discussed -- "
            "committing verbs like book/reserve/buy/purchase are still "
            "browser_action here, never answered as if already done; the "
            "browser page's own commit-element check is what actually "
            "pauses for confirmation before anything happens). Requires an "
            "existing, already-open page -- never a fresh open-ended "
            "'search the web'/'find hotels in Seoul' request with no page "
            "already in view (that is web_search), and never simply "
            "opening a URL in a new tab (that is open_url). "
            "A short imperative after a browser search ('click Images', "
            "'open the first result', 'show pictures') is browser_action "
            "when active_desktop_surface is a browser, even without saying "
            "'this page'. action_target is the complete request verbatim, the same "
            "rule as ui_action. Resolve 'this page/here/in it' against "
            "conversation state's active_desktop_surface.\n"
            "  * open_url: action_target is only the exact site name/address, "
            "never the whole sentence ('Open youtube.com in a new browser "
            "tab' -> target 'youtube.com'), computer_url its https address; "
            "'new tab'/'go to'/'visit'/'navigate to' all qualify, never "
            "web_search merely for being online. Closing/switching/editing "
            "one existing tab is unsupported.\n"
            "  * open_search: the user wants a real browser tab opened and "
            "searched so they can look at and interact with results "
            "themselves, not be told the answer. This is signaled either by "
            "explicitly saying 'browser', or by naming a real website or "
            "search engine as the thing to search (Google, Bing, YouTube, "
            "Amazon, or similar) -- naming a site implies opening it, not "
            "just researching the topic on Elaina's behalf ('search Google "
            "for hotels in Guam', 'google hotels in Guam', 'search Spotify "
            "for BTS in my browser' -> open_search, not open_url or "
            "web_search; 'search on Spotify' alone with no browser named is "
            "ui_action instead, since Spotify is an app). A bare topic with "
            "no named site/engine/app ('what are good hotels in Guam', "
            "'search for hotels in Guam') stays web_search -- Elaina "
            "answers it herself rather than opening a tab. action_target is "
            "the exact search words verbatim from the request, nothing "
            "added or paraphrased; computer_url stays empty -- the search "
            "engine's address is never the model's to choose.\n"
            "  * create_file/create_folder/delete_file/delete_folder: "
            "action_target is only the bare item name ('Delete the Hello "
            "folder' -> target 'Hello', not 'Hello folder'), computer_location "
            "the exact parent folder or empty. Delete means Recycle Bin, "
            "never permanent. A request that also writes content, "
            "overwrites, moves, or renames is entirely unsupported -- never "
            "execute just the safe part ('Create notes.txt in Documents and "
            "write hello inside it' -> unsupported, never create_file).\n"
            "  * unsupported: permanent deletion, moving/renaming, writing "
            "file content, closing one browser tab, shutting down/"
            "restarting/sleeping the PC (never force_quit_app), or inventing "
            "any path/command/PID.\n"
            "  'How do I open Discord myself?' is knowledge_question "
            "(information_request), never computer_action or web_search, "
            "and must not claim Desktop Control Mode is on.\n"
            "- selected_text_question: a substantial pasted passage/code "
            "block is present and the question is about it.\n"
            "- web_search: external evidence is needed for anything tied to "
            "real-world or current state (rates, prices, weather, news, "
            "sports, officeholders, employers, laws, versions, specs, "
            "releases, or any other fact that can change or is dated) -- "
            "EXCEPT when the user named a specific search engine or website "
            "as the thing to search ('search Google for hotels in Guam', "
            "'google hotels in Guam', 'look that up on Bing'), or asked to "
            "use a browser -- naming the site means they want it opened for "
            "them to look at, which is computer_action/open_search instead "
            "-- OR when the user is committing to/acting on a specific "
            "result from a page already in view ('book the best one', "
            "'reserve it', 'buy that one' referring back to results just "
            "shown) -- that is computer_action/browser_action instead, "
            "since the user wants Elaina to act on the specific page "
            "already open, not gather more evidence about it. Both "
            "exceptions apply even though the underlying topic (a price, "
            "availability, or other real-world fact) is exactly the kind "
            "that would otherwise need web_search. A direct "
            "ask is already permission (action_requested true), never "
            "agent_offer. For 'latest/newest' periodic events, resolve "
            "against current_date/current_year rather than training "
            "knowledge -- never assume the nearest scheduled edition already "
            "happened.\n"
            "- time_question: only the user's current clock time, today's "
            "date/day, or year -- never a release/historical date.\n"
            "- fact_check: the user disputes or corrects an earlier answer, "
            "or says they were right/Elaina was wrong. Resolve from grounded "
            "context; give a search_query if unverified.\n"
            "- pending_approval: a proposal is already waiting and this "
            "message responds to, confirms, or repeats it.\n"
            "- agent_create: the user directly asks to create, install, or "
            "configure a new agent. Never for avatars, images, UI assets, "
            "documents, or arbitrary code.\n"
            "- calendar_action: the user asks to add an event/class/"
            "appointment/reminder to Google Calendar. Scheduling advice "
            "alone is conversation/knowledge_question.\n"
            "- knowledge_question: a stable definition/concept/explanation/"
            "settled fact, independent of current state. Its present/"
            "recorded value needs web_search instead. When unsure, prefer "
            "web_search.\n"
            "- calculation: arithmetic, a numeric result, a split/percentage/"
            "price/duration, or a quantitative follow-up. Resolve short "
            "follow-ups from recent turns; put the full self-contained "
            "problem in normalized_request. Never needs permission.\n"
            "- conversation: ordinary dialogue, stable knowledge with no "
            "explanation needed, status updates, opinions/advice/judgment "
            "calls (even about a real product/university/career), or "
            "choosing between options -- answer directly, no agent offer. A "
            "recommendation needing current medical/legal/financial/product "
            "evidence still uses web_search but stays conversational in "
            "tone; any health-related recommendation uses advice_domain "
            "health.\n"
            "- clarification: only a genuinely ambiguous write/action "
            "request. Never execute writes from here.\n"
            "An attached screen selection strongly implies screen_analysis "
            "unless another action is clearly requested.\n\n"
            "A specialist intent may execute only when action_requested is "
            "true (direct request or an accepted offer) -- noticing a "
            "problem is not permission.\n\n"
            "Return one JSON object only, with these keys: intent, "
            "confidence (0-1), normalized_request, reason, search_query, "
            "topic, entity, aliases, is_follow_up, speech_act, "
            "action_requested, action_target, topic_shift, consent_decision, "
            "offered_intent, offered_request, memory_relevant, "
            "memory_candidate, detailed_response, screen_target, "
            "verification_required, information_freshness, "
            "requires_external_evidence, recommendation_needed, "
            "urgent_safety, advice_domain, time_scope, request_explicitness, "
            "computer_operation, computer_location, computer_url.\n"
            "speech_act: social, statement, advice, information_request, "
            "action_request, correction, or approval_response. "
            "action_requested is true only for a direct ask; action_target "
            "is the concrete target. topic_shift is true when this exchange "
            "moves past the stored active_topic. search_query is self-"
            "contained (resolved entity included) for web_search, else "
            "empty. consent_decision/offered_intent/offered_request stay "
            "empty outside their own rule.\n"
            "memory_relevant is true whenever the answer depends on saved "
            "identity/preferences/relationships/experiences/projects/goals "
            "(including 'based on what you know about me'), false for "
            "impersonal facts. memory_candidate is true only when this "
            "message itself states a durable personal fact/preference worth "
            "saving.\n"
            "detailed_response is true for an explicit request for a "
            "thorough/complete/stepwise answer.\n"
            "information_freshness: stable (state-independent), "
            "historical_record (needs a specific past value), changing (can "
            "shift between model updates), live (can shift within hours), "
            "or unknown -- never guess toward stable. "
            "requires_external_evidence is true for anything but stable. "
            "verification_required is true for changing/current/latest/"
            "disputed facts needing a second source.\n"
            "recommendation_needed is true when the user asks what to do/"
            "choose/try/use/take, or describes a problem seeking a next "
            "step; false for social remarks and plain factual questions. "
            "urgent_safety is true only when delay risks immediate serious "
            "harm, and must be answered immediately rather than researched. "
            "advice_domain: general, health, financial, legal, product, "
            "technical, or safety. Whenever advice_domain is health, legal, "
            "or financial, verification_required must also be true.\n"
            "time_scope: timeless, current, historical, future, or unknown "
            "-- a present role/value/status is current even if familiar. "
            "request_explicitness: direct, indirect, statement, or unknown "
            "-- indirect interest in screen/project inspection is "
            "agent_offer, not execution.\n"
            "computer_operation: none, open_app, close_app, force_quit_app, "
            "open_url, open_search, create_file, create_folder, delete_file, "
            "delete_folder, list_windows, describe_window, ui_action, "
            "browser_action, or unsupported. computer_location/computer_url "
            "stay empty unless the chosen operation needs them.\n"
            "Do not answer the user's question.\n\n"
            f"Runtime state: {json.dumps(state)}\n"
            "Conversation state:\n"
            f"{json.dumps(conversation_state, ensure_ascii=False)}\n"
            "Recent turns:\n"
            f"{json.dumps(recent_turns, ensure_ascii=False)}\n"
            f"Current transcript: {user_input}"
        )

    @staticmethod
    def _parse_decision(
        raw_content: str,
        original_input: str,
    ) -> IntentDecision | None:
        try:
            payload = json.loads(raw_content)
        except (TypeError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict):
            return None

        intent = str(payload.get("intent", "")).strip()
        if intent not in ALLOWED_INTENTS:
            # Found live: the model can correctly work out a specific
            # computer_operation (e.g. "book the best one" -> browser_action)
            # and then, exactly because that sub-field is so salient, write
            # its value into the top-level "intent" key instead of
            # "computer_action" -- every other field in the same response is
            # right. Recovering this deterministically here is strictly
            # better than the generic JSON-repair fallback below, which has
            # no domain guidance at all and can land on an unrelated intent.
            if intent in COMPUTER_OPERATIONS and intent not in {"none", "unsupported"}:
                payload = dict(payload)
                payload.setdefault("computer_operation", intent)
                intent = "computer_action"
                payload["intent"] = intent
            else:
                return None

        try:
            confidence = float(payload.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        confidence = max(0.0, min(confidence, 1.0))

        normalized_request = str(
            payload.get("normalized_request") or original_input
        ).strip()
        screen_target = str(
            payload.get("screen_target") or "configured"
        ).strip().lower()
        if screen_target not in {"configured", "main", "left", "right", "all"}:
            screen_target = "configured"

        information_freshness = str(
            payload.get("information_freshness") or "unknown"
        ).strip().lower()
        if information_freshness not in INFORMATION_FRESHNESS_VALUES:
            information_freshness = "unknown"

        external_value = payload.get("requires_external_evidence")
        if isinstance(external_value, bool):
            requires_external_evidence = external_value
        else:
            requires_external_evidence = information_freshness != "stable"

        speech_act = str(payload.get("speech_act", "")).strip()
        recommendation_needed = (
            payload.get("recommendation_needed") is True
            or speech_act == "advice"
        )
        advice_domain = str(
            payload.get("advice_domain") or "general"
        ).strip().lower()
        if advice_domain not in ADVICE_DOMAINS:
            advice_domain = "general"
        time_scope = str(
            payload.get("time_scope") or "unknown"
        ).strip().lower()
        if time_scope not in TIME_SCOPES:
            time_scope = "unknown"
        request_explicitness = str(
            payload.get("request_explicitness") or "unknown"
        ).strip().lower()
        if request_explicitness not in REQUEST_EXPLICITNESS_VALUES:
            request_explicitness = "unknown"
        computer_operation = str(
            payload.get("computer_operation") or "none"
        ).strip().lower()
        if computer_operation not in COMPUTER_OPERATIONS:
            computer_operation = "none"
        computer_location = str(
            payload.get("computer_location") or ""
        ).strip()
        computer_url = str(payload.get("computer_url") or "").strip()

        return IntentDecision(
            intent=intent,
            confidence=confidence,
            normalized_request=normalized_request,
            reason=str(payload.get("reason", "")).strip(),
            search_query=str(payload.get("search_query", "")).strip(),
            topic=str(payload.get("topic", "")).strip(),
            entity=str(payload.get("entity", "")).strip(),
            aliases=tuple(
                str(item).strip()
                for item in payload.get("aliases", [])
                if str(item).strip()
            ) if isinstance(payload.get("aliases", []), list) else (),
            is_follow_up=bool(payload.get("is_follow_up", False)),
            speech_act=speech_act,
            action_requested=bool(payload.get("action_requested", False)),
            action_target=str(payload.get("action_target", "")).strip(),
            topic_shift=bool(payload.get("topic_shift", False)),
            consent_decision=str(
                payload.get("consent_decision", "")
            ).strip().lower(),
            offered_intent=str(
                payload.get("offered_intent", "")
            ).strip(),
            offered_request=str(
                payload.get("offered_request", "")
            ).strip(),
            memory_relevant=bool(payload.get("memory_relevant", False)),
            memory_candidate=bool(payload.get("memory_candidate", False)),
            detailed_response=bool(payload.get("detailed_response", False)),
            screen_target=screen_target,
            verification_required=bool(
                payload.get("verification_required", False)
            ),
            information_freshness=information_freshness,
            requires_external_evidence=requires_external_evidence,
            recommendation_needed=recommendation_needed,
            urgent_safety=payload.get("urgent_safety") is True,
            advice_domain=advice_domain,
            time_scope=time_scope,
            request_explicitness=request_explicitness,
            computer_operation=computer_operation,
            computer_location=computer_location,
            computer_url=computer_url,
        )

    @staticmethod
    def _safe_fallback(
        user_input: str,
        *,
        has_screen_selection: bool,
        has_selected_text: bool,
    ) -> IntentDecision:
        """Preserve safe behavior if Ollama is unavailable or returns bad JSON."""
        if has_screen_selection:
            intent = "screen_analysis"
        elif has_selected_text:
            intent = "selected_text_question"
        else:
            intent = "conversation"

        return IntentDecision(
            intent=intent,
            confidence=0,
            normalized_request=user_input,
            reason="Safe fallback after router failure.",
        )
