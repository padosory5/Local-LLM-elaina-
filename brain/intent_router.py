from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


ALLOWED_INTENTS = {
    "conversation",
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
    "entity_correction",
    "fact_check",
    "clarification",
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


class SemanticIntentRouter:
    """Use a small structured LLM call to select exactly one Elaina feature."""

    def __init__(
        self,
        client: Any,
        model: str,
        keep_alive: int | str = -1,
    ) -> None:
        self.client = client
        self.model = model
        self.keep_alive = keep_alive

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
            if decision is not None:
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
        }

        return (
            "You are Elaina's semantic intent router. Choose exactly one "
            "intent from this allowlist:\n"
            "conversation, web_search, project_question, project_edit, "
            "git_commit, git_publish, screen_analysis, "
            "selected_text_question, knowledge_question, time_question, "
            "pending_approval, entity_correction, fact_check, clarification.\n\n"
            "Infer meaning instead of matching exact phrases. Account for "
            "speech-to-text mistakes and similar-sounding words. For example, "
            "'push my changes to get' usually means git_publish. Use recent "
            "turns to resolve short follow-ups, pronouns, and corrections.\n\n"
            "Preserve corrected named entities. If the user spells or corrects "
            "a name, make entity the corrected canonical form and put earlier "
            "misheard forms in aliases. Resolve words such as it, that, them, "
            "the model, the person, and short follow-ups from conversation "
            "state. A correction such as 'Q W E N' means Qwen.\n\n"
            "Routing rules:\n"
            "- git_publish: commit/upload/push current code to Git or GitHub.\n"
            "- git_commit: commit locally without requesting a push.\n"
            "- project_edit: create, edit, fix, delete, or modify project files.\n"
            "- project_question: inspect or explain the user's local project.\n"
            "- screen_analysis: answer from an attached selection or inspect "
            "visible screen content.\n"
            "- selected_text_question: answer about copied/highlighted text.\n"
            "  If the transcript contains a substantial pasted passage or code "
            "block and asks about that content, use selected_text_question.\n"
            "- web_search: the user asks to search/look something up, asks for "
            "current information, or asks about a specific person/entity that "
            "may require factual lookup. Also use it for release dates and "
            "similarly time-sensitive product or media facts.\n"
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
            "- knowledge_question: a factual how/why/what question that can be "
            "answered from stable general knowledge without a tool.\n"
            "- conversation: ordinary dialogue or stable knowledge that needs "
            "no factual explanation.\n"
            "- clarification: only when a write/action request is genuinely "
            "ambiguous. Never execute writes from this router.\n"
            "An attached screen selection strongly implies screen_analysis "
            "unless the user clearly asks for another action. A request to add "
            "a UI control beside the Screen button is project_edit, not vision.\n\n"
            "Return one JSON object only with: intent, confidence from 0 to 1, "
            "normalized_request, reason, search_query, topic, entity, aliases, "
            "and is_follow_up. search_query must be self-contained for "
            "web_search and include the resolved canonical entity; otherwise "
            "use an empty string. Do not answer the user's question.\n\n"
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
