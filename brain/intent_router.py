from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from tools.computer_control import (
    COMPUTER_OPERATIONS,
    takeover_authorized,
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
        computer_action_authorized: bool = False,
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
                    "num_predict": 320,
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
                    options={"temperature": 0, "num_predict": 320},
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
                decision = self._apply_computer_control_policy(
                    decision,
                    original_input=user_input,
                    context_authorized=computer_action_authorized,
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
                decision = self._apply_optional_capability_policy(decision)
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
        context_authorized: bool = False,
    ) -> IntentDecision:
        """Enforce grounded Phase 3A operations and takeover consent."""
        if decision.intent != "computer_action":
            return decision

        target = decision.action_target.strip()
        operation = decision.computer_operation
        location_is_grounded = (
            not decision.computer_location
            or transcript_names_location(original_input, decision.computer_location)
        )
        if (
            decision.speech_act != "action_request"
            or operation not in COMPUTER_OPERATIONS
            or operation in {"none", "unsupported"}
            or not transcript_names_target(original_input, target)
            or not location_is_grounded
        ):
            return replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "The computer request is not a grounded Phase 3A action "
                    "and must not reach a computer tool."
                ),
                action_requested=False,
                computer_operation="unsupported",
            )

        if not (context_authorized or takeover_authorized(original_input)):
            return replace(
                decision,
                normalized_request=original_input.strip(),
                reason=(
                    "The computer action is understood but still needs "
                    "takeover consent."
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
    def _apply_optional_capability_policy(
        decision: IntentDecision,
    ) -> IntentDecision:
        """Turn indirect interest in a read-only capability into an offer."""
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
        now = datetime.now()
        state = {
            "screen_selection_attached": has_screen_selection,
            "selected_text_attached": has_selected_text,
            "project_tools_available": project_tools_available,
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
            "Infer meaning instead of matching exact phrases. Account for "
            "speech-to-text mistakes and similar-sounding words by selecting "
            "the intended capability rather than literal wording. Use recent "
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
            "agent. Prefer agent_offer over conversation when a listed "
            "specialist can directly solve that concrete dissatisfaction. "
            "Set offered_intent to the relevant specialist intent and "
            "offered_request to a concrete proposed task. Example: 'The "
            "buttons in this project look boring' may offer project_edit. "
            "Example: musing about something visible but unidentified with "
            "no screen selection attached ('I wonder who drew this') may "
            "offer screen_analysis -- suggest selecting the area first. A "
            "general life update such as 'I'm working on my project tonight' "
            "does not justify an offer.\n"
            "- agent_consent: use only when pending_agent_offer is present and "
            "the new message responds to that exact offer. Decide its meaning "
            "semantically from the offer and recent exchange, not from a list "
            "of yes/no phrases. Set consent_decision to accept, reject, "
            "modify, or unclear. For modify, put the complete revised task in "
            "offered_request. Interpret acceptance or rejection from the "
            "meaning of the reply in context, not a phrase list. If the "
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
            "- screen_analysis: use only when screen_selection_attached is "
            "true, or the user explicitly asks Elaina to look at the "
            "screen right now. Without an attachment, a vague musing about "
            "something visible is agent_offer instead (see example below), "
            "never screen_analysis. Set screen_target to configured, main, "
            "left, right, or all from the user's meaning. Requests covering "
            "both monitors, every monitor, or across monitors use all.\n"
            "- computer_action: a direct request to control this Windows PC. "
            "Use it even when the user does not say takeover; local policy "
            "will request contextual consent before execution. Select one "
            "computer_operation: open_app, close_app, force_quit_app, open_url, "
            "create_file, create_folder, delete_file, delete_folder, or "
            "unsupported. Use close_app for a "
            "normal or graceful close, exit, or quit. The word exit or quit by "
            "itself never means force termination. Use force_quit_app only "
            "when the user explicitly says force, terminate, kill, entirely, "
            "or completely and means bypassing the normal close flow. For app "
            "operations, copy only the requested application name into "
            "action_target. For open_url, action_target is the exact spoken "
            "website name or address and computer_url is its HTTP(S) address. "
            "For filesystem operations, action_target is only the exact item "
            "name and computer_location is the exact spoken parent location. "
            "Use delete_file when the user means an existing file and "
            "delete_folder when they mean an existing folder or directory. "
            "Delete means move to the Windows Recycle Bin, not permanent "
            "erasure. Leave computer_location empty if none was provided. "
            "Creation supports only one new empty item. If the same request "
            "also asks to write content, overwrite, move, rename, or perform "
            "another operation, "
            "the entire operation is unsupported; never execute only the safe "
            "part of a compound request. Deleting an existing folder is "
            "delete_folder, never create_folder. Permanent deletion that "
            "bypasses the Recycle Bin is unsupported. Shutting down, "
            "restarting, or sleeping the PC, "
            "computer, Windows, or machine is never force_quit_app; it is "
            "unsupported because force_quit_app requires a named application. "
            "Never invent a filesystem location, executable path, shell "
            "command, process ID, or command argument. Use unsupported for "
            "settings changes, clicking, typing, permanent deletion, moving, "
            "renaming, file content writing, closing a particular browser tab, "
            "system shutdown, or other PC operations. "
            "A question asking for instructions so the user can do an action "
            "themselves, such as 'How do I open Discord myself?', is always "
            "knowledge_question with "
            "speech_act information_request, never computer_action, and must "
            "not create a takeover offer.\n"
            "Critical computer-action contrasts:\n"
            "  'Create notes.txt in Documents' -> create_file.\n"
            "  'Create notes.txt and write hello in it' -> unsupported because "
            "content writing is a second unsupported operation.\n"
            "  'Create a Receipts folder' -> create_folder.\n"
            "  'Delete or remove the old Receipts folder from Documents' -> "
            "delete_folder with target Receipts and location Documents.\n"
            "  'Trash notes.txt in Downloads' -> delete_file with target "
            "notes.txt and location Downloads.\n"
            "  'Permanently erase notes.txt without using Recycle Bin' -> "
            "unsupported, never delete_file or delete_folder.\n"
            "  'Close Discord' -> close_app.\n"
            "  'Close the github.com browser tab' -> unsupported, never "
            "close_app; close_app requires an installed application target.\n"
            "  'Shut down the computer' -> unsupported, never force_quit_app.\n"
            "- selected_text_question: answer about copied/highlighted text.\n"
            "  If the transcript contains a substantial pasted passage or code "
            "block and asks about that content, use selected_text_question.\n"
            "- web_search: external evidence is required for any answer that "
            "depends on real-world state, a recorded value, or information "
            "that may differ from model training. This includes live or dated "
            "exchange and market rates, prices, availability, weather, news, "
            "sports, schedules, officeholders, current CEOs or executives, "
            "employers and other current role occupants, laws, policies, statistics, "
            "software versions and documentation, product specifications, "
            "release information, and other changing external facts. It also "
            "applies when the user asks to search or when stability is "
            "uncertain. A direct request for the information is already permission: web_search "
            "with action_requested true, never agent_offer.\n"
            "  Runtime state has current_date/current_year. For 'latest/"
            "newest/most recent' editions of a periodic event (a World "
            "Cup, an Olympics, a model release), ask for the latest completed "
            "event or actually released product as of current_date. Do not "
            "assume the nearest scheduled edition has already finished. "
            "Never answer 'latest' from training knowledge -- it is likely "
            "stale.\n"
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
            "- knowledge_question: only a definition, concept, mathematical or "
            "logical explanation, established scientific principle, broad "
            "settled historical fact, or other answer explicitly known to be "
            "stable and independent of current external state. Asking how a "
            "changing system works may be stable knowledge; asking for its "
            "present or recorded value requires web_search. When uncertain, "
            "choose web_search.\n"
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
            "recommend, answer conversationally without offering an agent. "
            "Recommendations that depend on current medical, legal, financial, "
            "product, or other external evidence use web_search while keeping "
            "a conversational final response.\n"
            "  Any recommendation about a symptom, health condition, medicine, "
            "supplement, allergy, pain, sleep problem, or bodily effect uses "
            "advice_domain health, never general.\n"
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
            "offered_request, memory_relevant, memory_candidate, "
            "detailed_response, screen_target, verification_required, "
            "information_freshness, requires_external_evidence, and "
            "recommendation_needed, urgent_safety, advice_domain, time_scope, "
            "request_explicitness, computer_operation, computer_location, and "
            "computer_url. "
            "speech_act is one of "
            "social, statement, advice, "
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
            "requires them. memory_relevant is true whenever answering depends "
            "on the user's saved identity, preferences, relationships, past "
            "experiences, projects, or goals, including requests about what "
            "Elaina remembers. A request beginning 'based on what you know "
            "about me' always has memory_relevant true, including when it asks "
            "for a recommendation. It is false for impersonal factual answers. "
            "memory_candidate is true only when the current message contains "
            "a durable personal fact or preference worth considering for "
            "storage; questions and temporary states are false. "
            "detailed_response is true when the user asks for a thorough, "
            "stepwise, comprehensive, or complete answer, regardless of exact "
            "wording. information_freshness is exactly one of stable, "
            "historical_record, changing, live, or unknown. stable means the "
            "answer is independent of real-world state. historical_record "
            "means a value must be retrieved for a specified past time. "
            "changing means external information can change between model "
            "updates. live means it may change within hours or minutes. Use "
            "unknown rather than guessing stability. "
            "requires_external_evidence is false only when local model "
            "knowledge is explicitly sufficient; it is true for "
            "historical_record, changing, live, unknown, or any requested web "
            "lookup. verification_required is true for changing, current, "
            "latest, externally disputed, or otherwise time-sensitive facts "
            "that need an independent second source. recommendation_needed is "
            "true when the user asks what they should do, choose, try, use, "
            "take, or change, or describes a problem while seeking a practical "
            "next step. It is false for social remarks and purely descriptive "
            "factual questions. Medical, legal, and financial recommendations "
            "normally require external evidence. "
            "urgent_safety is true only when delay could expose the user or "
            "someone else to immediate serious harm. Urgent safety advice must "
            "be answered immediately instead of waiting for web research. "
            "advice_domain is exactly one of general, health, financial, legal, "
            "product, technical, or safety. Use health for medicines, "
            "supplements, symptoms, sleep disorders, and other health choices. "
            "time_scope is exactly one of timeless, current, historical, future, "
            "or unknown. Questions about who currently holds a role, live "
            "values, present status, or what is true now use current even when "
            "the fact seems familiar. request_explicitness is exactly one of "
            "direct, indirect, statement, or unknown. direct means the user "
            "plainly asks Elaina to answer or run a capability. indirect means "
            "they express curiosity, a wish, or say a capability would be nice "
            "without directly delegating it. Indirect interest in screen or "
            "project inspection must be agent_offer, not immediate execution. "
            "computer_operation is exactly none, open_app, close_app, "
            "force_quit_app, open_url, create_file, create_folder, delete_file, "
            "delete_folder, or unsupported. computer_location and computer_url "
            "are strings and "
            "must be empty unless required by the selected computer operation. "
            "Do not answer the user's "
            "question.\n\n"
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
