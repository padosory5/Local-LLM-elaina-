from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any


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


class SemanticIntentRouter:
    """Use a small structured LLM call to select exactly one Elaina feature."""

    def __init__(
        self,
        client: Any,
        model: str,
        keep_alive: int | str = -1,
        safety_mode: str = "enforce",
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive
        self.safety_mode = (
            safety_mode
            if safety_mode in {"enforce", "shadow", "off"}
            else "enforce"
        )

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

        normalized_follow_up = re.sub(
            r"[\s,.!?]+$",
            "",
            user_input.strip().lower(),
        )
        active_topic = str(state.get("active_topic", "")).strip()
        if normalized_follow_up in {"for example", "give me an example"} and active_topic:
            return IntentDecision(
                intent="knowledge_question",
                confidence=1.0,
                normalized_request=(
                    f"Give one concrete example related to {active_topic}."
                ),
                reason="Resolved a short example request from the active topic.",
                topic=active_topic,
                entity=str(state.get("active_entity", "")).strip(),
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
        )

        try:
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
                    "num_predict": 180,
                },
                keep_alive=self.keep_alive,
                think=False,
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
                                "offered_intent, offered_request. Intent must "
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
                    options={"temperature": 0, "num_predict": 180},
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
                if (
                    decision.intent not in {"agent_create", "pending_approval"}
                    and self._is_direct_agent_creation_request(user_input)
                ):
                    decision = replace(
                        decision,
                        intent="agent_create",
                        confidence=max(decision.confidence, 0.98),
                        normalized_request=user_input.strip(),
                        reason=(
                            "Local action policy recognized a direct request "
                            "to create an agent."
                        ),
                        speech_act="action_request",
                        action_requested=True,
                        action_target="new agent",
                    )
                elif (
                    decision.intent not in {
                        "calendar_action",
                        "agent_create",
                        "pending_approval",
                    }
                    and self._is_direct_calendar_write_request(user_input)
                ):
                    decision = replace(
                        decision,
                        intent="calendar_action",
                        confidence=max(decision.confidence, 0.98),
                        normalized_request=user_input.strip(),
                        reason=(
                            "Local action policy recognized a direct calendar "
                            "write request."
                        ),
                        speech_act="action_request",
                        action_requested=True,
                        action_target="calendar event",
                    )
                safe_decision = self._apply_action_safety_policy(
                    decision,
                    original_input=user_input,
                    conversation_state=state,
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
                if (
                    decision.intent in ACTION_INTENTS
                    and decision.speech_act == "action_request"
                ):
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
                    and (
                        decision.speech_act == "advice"
                        or self._is_subjective_advice_request(routed_input)
                    )
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
                grounded = dict(state.get("grounded_context", {}))
                if (
                    grounded.get("statement")
                    and decision.intent in {"conversation", "clarification"}
                    and self._looks_like_fact_challenge(routed_input)
                ):
                    subject = str(
                        grounded.get("subject")
                        or state.get("active_entity")
                        or decision.entity
                    ).strip()
                    simple_acknowledgment = bool(re.search(
                        r"\b(?:i was right|you were wrong|told you)\b",
                        routed_input,
                        flags=re.IGNORECASE,
                    ))
                    return IntentDecision(
                        intent="fact_check",
                        confidence=max(decision.confidence, 0.95),
                        normalized_request=decision.normalized_request,
                        reason=(
                            "The user is challenging or revisiting a recent "
                            "grounded factual result."
                        ),
                        search_query=(
                            ""
                            if simple_acknowledgment
                            else (
                                f"{subject} official current facts verify "
                                f"{routed_input}"
                            ).strip()
                        ),
                        topic=decision.topic or subject,
                        entity=decision.entity or subject,
                        aliases=decision.aliases,
                        is_follow_up=True,
                    )
                # Current clock/calendar questions are different from release
                # or publication dates, which may require current web facts.
                if (
                    decision.intent == "time_question"
                    and not self._asks_current_clock_or_calendar(routed_input)
                ):
                    grounded = dict(state.get("grounded_context", {}))
                    subject = str(
                        grounded.get("subject")
                        or decision.entity
                        or state.get("active_entity")
                        or decision.topic
                    ).strip()
                    query = decision.search_query or (
                        f"{subject} official release date"
                        if subject
                        else f"{routed_input} official"
                    )
                    return IntentDecision(
                        intent="web_search",
                        confidence=max(decision.confidence, 0.95),
                        normalized_request=decision.normalized_request,
                        reason=(
                            "Release and historical dates are factual lookup "
                            "questions, not current clock/calendar questions."
                        ),
                        search_query=query,
                        topic=decision.topic or subject,
                        entity=decision.entity or subject,
                        aliases=decision.aliases,
                        is_follow_up=decision.is_follow_up,
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
    def _asks_current_clock_or_calendar(user_input: str) -> bool:
        normalized = " ".join(user_input.lower().split())
        current_phrases = (
            "what time is it",
            "what's the time",
            "current time",
            "time right now",
            "what day is it",
            "what's the date",
            "what is the date",
            "today's date",
            "date today",
            "what year is it",
        )
        return any(phrase in normalized for phrase in current_phrases)

    @staticmethod
    def _looks_like_fact_challenge(user_input: str) -> bool:
        return bool(re.search(
            r"\b(?:i was right|you were wrong|told you|"
            r"after (?:my )?research|i found out|but you said|"
            r"that(?:'s| is) (?:not right|wrong)|"
            r"you got that wrong)\b",
            user_input,
            flags=re.IGNORECASE,
        ))

    @staticmethod
    def _is_subjective_advice_request(user_input: str) -> bool:
        """Recognize requests for Elaina's judgment rather than a fact report."""
        normalized = " ".join(user_input.lower().split())
        return bool(re.search(
            r"\b(?:do you think|what do you think|in your opinion)\b|"
            r"\b(?:is|are|was|were)\s+.+\s+worth\s+(?:it|doing|going|"
            r"studying|buying|trying)\b|"
            r"\b(?:would|should)\s+i\b",
            normalized,
        ))

    @classmethod
    def _apply_action_safety_policy(
        cls,
        decision: IntentDecision,
        *,
        original_input: str,
        conversation_state: dict[str, Any],
    ) -> IntentDecision:
        """
        Prevent vague conversation from reaching project write tools.

        The semantic model proposes an intent, but a local policy owns the
        authorization boundary. Only a direct request for a concrete change may
        remain project_edit. Uncertainty falls back to a read-only or
        conversational intent.
        """
        if decision.intent != "project_edit":
            return decision

        if cls._is_direct_project_change_request(original_input):
            return replace(
                decision,
                speech_act=decision.speech_act or "action_request",
                action_requested=True,
            )

        normalized = " ".join(original_input.lower().split())
        active_topic = str(
            conversation_state.get("active_topic", "")
        ).lower()
        asks_for_advice = (
            decision.speech_act == "advice"
            or bool(re.search(
            r"\b(?:what|which)\s+should\s+(?:i|we)\b|"
            r"\bwhat\s+should\s+(?:be\s+)?(?:add|change|improve)|"
            r"\b(?:recommend|suggest|any ideas)\b",
            normalized,
            ))
        )
        personal_work_status = bool(re.search(
            r"\b(?:i am|i'm|im|i was|i'll|i will|i'm gonna|"
            r"i am going to)\s+(?:keep\s+)?(?:work|working|edit|editing|"
            r"build|building|continue|continuing)\b|"
            r"\b(?:back on|continue|continuing)\b.*\bproject\b|"
            r"\bproject\b.*\b(?:continue|continuing)\b",
            normalized,
        ))
        project_context = bool(re.search(
            r"\b(project|code|codebase|repository|repo|app|ui|feature)\b",
            normalized,
        )) or "project" in active_topic

        if project_context and not asks_for_advice and not personal_work_status:
            return replace(
                decision,
                intent="agent_offer",
                normalized_request=original_input.strip(),
                reason=(
                    "The user described a concrete project problem but did "
                    "not authorize an edit, so Coding Agent help is optional."
                ),
                search_query="",
                action_requested=False,
                action_target="",
                offered_intent="project_edit",
                offered_request=(
                    decision.offered_request
                    or decision.normalized_request
                    or original_input.strip()
                ),
            )

        return replace(
            decision,
            intent="conversation",
            normalized_request=original_input.strip(),
            reason=(
                "Safety policy downgraded project_edit because the user did "
                "not directly request a concrete file change."
            ),
            search_query="",
            action_requested=False,
            action_target="",
        )

    @staticmethod
    def _is_direct_project_change_request(user_input: str) -> bool:
        normalized = " ".join(user_input.lower().split())

        advice_or_status = bool(re.search(
            r"\b(?:what|which)\s+should\s+(?:i|we)\b|"
            r"\bwhat\s+should\s+(?:be\s+)?(?:add|change|improve)|"
            r"\b(?:i am|i'm|im|i was|i'll|i will|i'm gonna|"
            r"i am going to)\s+(?:keep\s+)?(?:work|working|edit|editing|"
            r"build|building|continue|continuing)\b",
            normalized,
        ))
        explicit_delegation = bool(re.search(
            r"\b(?:can|could|would|will)\s+you\b|"
            r"\bi\s+want\s+you\s+to\b|\bplease\b",
            normalized,
        ))
        imperative = bool(re.match(
            r"^(?:add|create|edit|change|modify|fix|delete|remove|rename|"
            r"move|implement|refactor|update|replace)\b",
            normalized,
        ))
        delegated_mutation = bool(re.search(
            r"\b(?:add|create|edit|change|modify|fix|delete|remove|rename|"
            r"move|implement|refactor|update|replace)\b",
            normalized,
        ))

        if advice_or_status and not explicit_delegation:
            return False
        return imperative or (
            explicit_delegation and delegated_mutation
        )

    @staticmethod
    def _is_direct_agent_creation_request(user_input: str) -> bool:
        normalized = " ".join(user_input.lower().split())
        mentions_agent = bool(re.search(
            r"\b(?:ai\s+)?agents?\b",
            normalized,
        ))
        requests_creation = bool(re.search(
            r"\b(?:create|build|make|add|install|configure|set up)\b",
            normalized,
        ))
        direct = bool(re.match(
            r"^(?:create|build|make|add|install|configure|set up)\b",
            normalized,
        )) or bool(re.search(
            r"\b(?:can|could|would|will)\s+you\b|"
            r"\bi\s+want\s+you\s+to\b|\bplease\b",
            normalized,
        ))
        return mentions_agent and requests_creation and direct

    @staticmethod
    def _is_direct_calendar_write_request(user_input: str) -> bool:
        normalized = " ".join(user_input.lower().split())
        mentions_calendar = bool(re.search(
            r"\b(?:calendar|schedule|appointment|event)\b",
            normalized,
        ))
        mutation = bool(re.search(
            r"\b(?:add|create|put|schedule|write|book)\b",
            normalized,
        ))
        direct = bool(re.match(
            r"^(?:add|create|put|schedule|write|book)\b",
            normalized,
        )) or bool(re.search(
            r"\b(?:can|could|would|will)\s+you\b|"
            r"\bi\s+want\s+you\s+to\b|\bplease\b",
            normalized,
        ))
        return mentions_calendar and mutation and direct

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
    ) -> str:
        state = {
            "screen_selection_attached": has_screen_selection,
            "selected_text_attached": has_selected_text,
            "project_tools_available": project_tools_available,
            "pending_action": pending_action,
            "pending_agent_offer": conversation_state.get(
                "pending_agent_offer"
            ),
        }

        return (
            "You are Elaina's semantic intent router. Choose exactly one "
            "intent from this allowlist:\n"
            "conversation, calculation, agent_offer, agent_consent, web_search, "
            "project_question, project_edit, "
            "git_commit, git_publish, screen_analysis, "
            "selected_text_question, knowledge_question, time_question, "
            "pending_approval, agent_create, calendar_action, "
            "entity_correction, fact_check, clarification.\n\n"
            "Infer meaning instead of matching exact phrases. Account for "
            "speech-to-text mistakes and similar-sounding words. For example, "
            "'push my changes to get' usually means git_publish. Use recent "
            "turns to resolve short follow-ups, pronouns, and corrections. "
            "The immediately previous user/assistant exchange has higher "
            "priority than an older active_topic. Treat active_topic only as a "
            "fallback when recent turns do not establish the subject.\n\n"
            "Preserve corrected named entities. If the user spells or corrects "
            "a name, make entity the corrected canonical form and put earlier "
            "misheard forms in aliases. Resolve words such as it, that, them, "
            "the model, the person, and short follow-ups from conversation "
            "state. A correction such as 'Q W E N' means Qwen.\n\n"
            "Routing rules:\n"
            "- agent_offer: the user expresses a concrete problem, desire, or "
            "dissatisfaction that an available specialist could help with, "
            "but does not ask Elaina to perform the work. Do not invoke the "
            "agent. Set offered_intent to the relevant specialist intent and "
            "offered_request to a concrete proposed task. Example: 'The "
            "buttons in this project look boring' may offer project_edit. A "
            "general life update such as 'I'm working on my project tonight' "
            "does not justify an offer.\n"
            "- agent_consent: use only when pending_agent_offer is present and "
            "the new message responds to that exact offer. Decide its meaning "
            "semantically from the offer and recent exchange, not from a list "
            "of yes/no phrases. Set consent_decision to accept, reject, "
            "modify, or unclear. For modify, put the complete revised task in "
            "offered_request. Replies such as 'sure', 'yeah let's do that', "
            "and 'let's go for it' often accept in the right context, but the "
            "same words may mean something else in another context. If the "
            "user changes topics, route the new topic normally instead.\n"
            "- git_publish: commit/upload/push current code to Git or GitHub.\n"
            "- git_commit: commit locally without requesting a push.\n"
            "- project_edit: create, edit, fix, delete, or modify project files.\n"
            "  Use project_edit only when the user directly delegates a "
            "specific change to Elaina. A statement about what the user plans "
            "to work on is conversation. Asking what they should add or asking "
            "for ideas is project_question, not project_edit.\n"
            "- project_question: inspect or explain the user's local project.\n"
            "  Use it only when the user directly asks Elaina to inspect or "
            "read project files. Asking for an opinion, recommendation, or a "
            "choice such as Live2D versus 3D is conversation.\n"
            "- screen_analysis: answer from an attached selection or inspect "
            "visible screen content.\n"
            "- selected_text_question: answer about copied/highlighted text.\n"
            "  If the transcript contains a substantial pasted passage or code "
            "block and asks about that content, use selected_text_question.\n"
            "- web_search: the user asks to search/look something up, asks for "
            "current information, or asks about a specific person/entity that "
            "may require factual lookup. Also use it for release dates and "
            "similarly time-sensitive product or media facts.\n"
            "  A direct request such as 'Can you search for when Elon Musk "
            "was born?' is already permission: use web_search with "
            "speech_act action_request and action_requested true. Do not ask "
            "whether to use Research Agent a second time.\n"
            "- time_question: only asks for the user's current local clock "
            "time, today's date/day, or current year. Never use it for a game "
            "release, publication, launch, historical event, or product date.\n"
            "- fact_check: the user challenges or corrects an earlier factual "
            "answer, says they were right or Elaina was wrong, or asks to "
            "reconcile contradictory claims. Resolve it from grounded context. "
            "If the correction is not already verified, provide a self-contained "
            "search_query so it can be checked on the web.\n"
            "- pending_approval: a proposal is already waiting and the user "
            "asks about it, confirms it verbally, repeats the same action, or "
            "says a short response such as yes/no. Approval still happens only "
            "in Electron.\n"
            "- agent_create: the user directly asks Elaina to create, install, "
            "or configure a new AI agent or capability. Do not use this merely "
            "because the user discusses agents. Never suggest Agent Builder "
            "for creating avatars, images, UI assets, documents, or arbitrary "
            "code; it only supports reviewed agent blueprints.\n"
            "- calendar_action: the user asks Elaina to create or add an event, "
            "class, appointment, reminder, or other schedule entry in Google "
            "Calendar. Asking for scheduling advice without requesting a "
            "calendar change is conversation or knowledge_question.\n"
            "- knowledge_question: a factual how/why/what question that can be "
            "answered from stable general knowledge without a tool.\n"
            "- calculation: the user asks for arithmetic, a numerical result, "
            "a proportional split, a percentage, a price, a duration, or a "
            "quantitative follow-up to an earlier calculation. Resolve short "
            "follow-ups such as 'how much did I make?' from recent turns and "
            "put the complete self-contained problem in normalized_request. "
            "Calculation is a normal answer mode, not an action agent, and it "
            "never needs permission.\n"
            "- conversation: ordinary dialogue or stable knowledge that needs "
            "no factual explanation. This includes statements such as 'I'm "
            "continuing my project tonight' because they describe the user's "
            "activity rather than delegating an edit. Questions asking for "
            "Elaina's opinion, personal judgment, advice, or whether something "
            "is worth doing also use conversation, even when a university, "
            "product, career, or other factual entity is mentioned.\n"
            "  If the user is choosing between options or asking what you "
            "recommend, answer conversationally without offering an agent "
            "unless they explicitly ask you to inspect external information.\n"
            "- clarification: only when a write/action request is genuinely "
            "ambiguous. Never execute writes from this router.\n"
            "An attached screen selection strongly implies screen_analysis "
            "unless the user clearly asks for another action. A request to add "
            "a UI control beside the Screen button is project_edit, not vision.\n\n"
            "A specialist intent may execute only when action_requested is "
            "true because the user directly requested it, or after a pending "
            "offer is semantically accepted. Merely noticing a problem is not "
            "permission.\n\n"
            "Return one JSON object only with: intent, confidence from 0 to 1, "
            "normalized_request, reason, search_query, topic, entity, aliases, "
            "is_follow_up, speech_act, action_requested, action_target, and "
            "topic_shift, consent_decision, offered_intent, and "
            "offered_request. speech_act is one of social, statement, advice, "
            "information_request, action_request, correction, or "
            "approval_response. Always provide the current conversational "
            "topic, including for ordinary conversation. action_requested is "
            "true only when the user directly asks Elaina to perform an action; "
            "action_target names the concrete requested target. topic_shift is "
            "true when the latest exchange establishes a newer topic than the "
            "stored active topic. search_query must be self-contained for "
            "web_search and include the resolved canonical entity; otherwise "
            "use an empty string. consent_decision, offered_intent, and "
            "offered_request must be empty unless their agent routing rule "
            "requires them. Do not answer the user's question.\n\n"
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
            return None

        try:
            confidence = float(payload.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        confidence = max(0.0, min(confidence, 1.0))

        normalized_request = str(
            payload.get("normalized_request") or original_input
        ).strip()

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
            speech_act=str(payload.get("speech_act", "")).strip(),
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
        elif SemanticIntentRouter._looks_like_calculation_request(user_input):
            # This is used only after both semantic JSON attempts fail. The
            # normal route remains model-based, but an obvious numeric request
            # should not lose its answer-first policy because of bad JSON.
            intent = "calculation"
        else:
            intent = "conversation"

        return IntentDecision(
            intent=intent,
            confidence=0,
            normalized_request=user_input,
            reason="Safe fallback after router failure.",
        )

    @staticmethod
    def _looks_like_calculation_request(user_input: str) -> bool:
        normalized = " ".join(user_input.lower().split())
        has_number = bool(re.search(r"\d", normalized))
        quantitative_request = bool(re.search(
            r"\b(?:calculate|math|total|split|distribution|percentage|"
            r"percent|profit|how much|how many|each person|each of us)\b",
            normalized,
        ))
        return has_number and quantitative_request
