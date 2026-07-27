import json
import unittest

try:
    from brain.intent_router import SemanticIntentRouter
except ModuleNotFoundError:
    # Allows this changed-files bundle to be tested before it is copied into
    # the project's normal brain/ directory.
    from intent_router import SemanticIntentRouter


class FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, **_kwargs):
        return {
            "message": {
                "content": json.dumps(self.payload),
            },
        }


class SemanticIntentRouterTests(unittest.TestCase):
    class MustNotRunClient:
        def chat(self, **_kwargs):
            raise AssertionError("The LLM router should have been bypassed.")

    def test_accepts_git_publish_decision(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "git_publish",
                "confidence": 0.97,
                "normalized_request": "Push my changes to Git.",
                "reason": "Likely STT substitution.",
                "search_query": "",
            }),
            "qwen3:8b",
        )

        result = router.route("Push my changes to get")

        self.assertEqual(result.intent, "git_publish")
        self.assertEqual(result.normalized_request, "Push my changes to Git.")

    def test_rejects_unknown_intent_safely(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "delete_computer",
                "confidence": 1,
            }),
            "qwen3:8b",
        )

        result = router.route("hello")

        self.assertEqual(result.intent, "conversation")
        self.assertEqual(result.confidence, 0)

    def test_attached_screen_is_safe_fallback(self):
        class BrokenClient:
            def chat(self, **_kwargs):
                raise RuntimeError("offline")

        router = SemanticIntentRouter(BrokenClient(), "qwen3:8b")
        result = router.route(
            "Explain this",
            has_screen_selection=True,
        )

        self.assertEqual(result.intent, "screen_analysis")

    def test_attached_screen_overrides_classifier(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.99,
                "normalized_request": "What game is this?",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "What game is this?",
            has_screen_selection=True,
        )

        self.assertEqual(result.intent, "screen_analysis")
        self.assertEqual(result.confidence, 1.0)

    def test_preserves_corrected_entity(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.99,
                "normalized_request": "Qwen",
                "reason": "The user corrected the entity spelling.",
                "search_query": "",
                "topic": "Qwen model releases",
                "entity": "Qwen",
                "aliases": ["Quen", "Quinn", "Q W E N"],
                "is_follow_up": True,
            }),
            "qwen3:8b",
        )

        result = router.route("Q W E N")

        self.assertEqual(result.entity, "Qwen")
        self.assertTrue(result.is_follow_up)

    def test_builds_self_contained_follow_up_search(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "web_search",
                "confidence": 0.98,
                "normalized_request": "Find the latest Qwen model releases.",
                "reason": "Resolved 'it' from active entity.",
                "search_query": "latest Qwen model releases official",
                "topic": "Qwen model releases",
                "entity": "Qwen",
                "aliases": [],
                "is_follow_up": True,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Can you search it up?",
            conversation_state={
                "active_topic": "Qwen model releases",
                "active_entity": "Qwen",
                "entity_aliases": {"Quinn": "Qwen"},
            },
        )

        self.assertEqual(result.intent, "web_search")
        self.assertIn("Qwen", result.search_query)

    def test_pending_proposal_routes_to_approval_status(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "pending_approval",
                "confidence": 0.99,
                "normalized_request": "Approve the pending Git proposal.",
                "reason": "A Git proposal is already waiting.",
                "search_query": "",
                "topic": "",
                "entity": "",
                "aliases": [],
                "is_follow_up": True,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Yeah",
            pending_action="Git",
        )

        self.assertEqual(result.intent, "pending_approval")

    def test_explicit_spelling_is_authoritative(self):
        router = SemanticIntentRouter(
            self.MustNotRunClient(),
            "qwen3:8b",
        )

        result = router.route(
            "No, I said Gwynn. Q-W-E-N.",
            conversation_state={
                "active_topic": "recent Quen releases",
                "active_entity": "Quen",
                "entity_aliases": {},
            },
        )

        self.assertEqual(result.intent, "entity_correction")
        self.assertEqual(result.entity, "Qwen")
        self.assertIn("Gwynn", result.aliases)
        self.assertIn("Quen", result.aliases)

    def test_scoped_phonetic_alias_rewrites_quinn_to_qwen(self):
        captured = {}

        class CaptureClient:
            def chat(self, **kwargs):
                captured["messages"] = kwargs["messages"]
                return {
                    "message": {
                        "content": json.dumps({
                            "intent": "web_search",
                            "confidence": 1,
                            "normalized_request": (
                                "search latest releases about Qwen"
                            ),
                            "reason": "Resolved active entity.",
                            "search_query": "latest Qwen model releases",
                            "topic": "Qwen releases",
                            "entity": "Qwen",
                            "aliases": [],
                            "is_follow_up": True,
                        }),
                    },
                }

        router = SemanticIntentRouter(CaptureClient(), "qwen3:8b")
        result = router.route(
            "search the latest releases about Quinn",
            conversation_state={
                "active_topic": "Qwen releases",
                "active_entity": "Qwen",
                "entity_aliases": {"Gwynn": "Qwen"},
            },
        )

        self.assertEqual(result.intent, "web_search")
        self.assertIn(
            "Qwen",
            captured["messages"][-1]["content"],
        )
        self.assertNotIn(
            "Quinn",
            captured["messages"][-1]["content"],
        )

    def test_for_example_uses_active_topic_without_llm(self):
        router = SemanticIntentRouter(
            self.MustNotRunClient(),
            "qwen3:8b",
        )

        result = router.route(
            "For example,",
            conversation_state={
                "active_topic": "how mental illness develops",
                "active_entity": "",
                "entity_aliases": {},
            },
        )

        self.assertEqual(result.intent, "knowledge_question")
        self.assertIn(
            "mental illness",
            result.normalized_request,
        )

    def test_release_date_is_not_current_time_intent(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "time_question",
                "confidence": 0.95,
                "normalized_request": "When was Marathon released?",
                "reason": "The user asked for a date.",
                "search_query": "",
                "topic": "Marathon",
                "entity": "Marathon",
                "aliases": [],
                "is_follow_up": True,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "When was it released?",
            conversation_state={
                "active_topic": "Marathon",
                "active_entity": "Marathon",
                "entity_aliases": {},
                "grounded_context": {
                    "subject": "Marathon",
                    "statement": (
                        "This is Bungie's new extraction shooter Marathon."
                    ),
                    "source": "Visual web verification",
                },
            },
        )

        self.assertEqual(result.intent, "web_search")
        self.assertIn("Marathon", result.search_query)
        self.assertIn("release date", result.search_query)

    def test_current_clock_question_remains_time_question(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "time_question",
                "confidence": 1,
                "normalized_request": "What time is it?",
                "reason": "Current local time requested.",
                "search_query": "",
            }),
            "qwen3:8b",
        )

        result = router.route("What time is it?")

        self.assertEqual(result.intent, "time_question")

    def test_factual_challenge_uses_grounded_context_and_search(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "clarification",
                "confidence": 0.95,
                "normalized_request": (
                    "I found that Marathon was a recent game."
                ),
                "reason": "The user contradicts the previous answer.",
                "search_query": "",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "But after my research, I found out Marathon was a recent game.",
            conversation_state={
                "active_topic": "Marathon",
                "active_entity": "Marathon",
                "entity_aliases": {},
                "grounded_context": {
                    "subject": "Marathon",
                    "statement": "The selected game was Marathon.",
                    "source": "Visual web verification",
                },
            },
        )

        self.assertEqual(result.intent, "fact_check")
        self.assertIn("Marathon", result.search_query)

    def test_i_was_right_uses_verified_fact_without_another_search(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.95,
                "normalized_request": "I was right about Marathon.",
                "reason": "Conversational follow-up.",
                "search_query": "",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Look, I was right about Marathon.",
            conversation_state={
                "active_topic": "Marathon",
                "active_entity": "Marathon",
                "entity_aliases": {},
                "grounded_context": {
                    "subject": "Marathon",
                    "statement": (
                        "The new Marathon released on March 5, 2026."
                    ),
                    "source": "Current web search",
                },
            },
        )

        self.assertEqual(result.intent, "fact_check")
        self.assertEqual(result.search_query, "")

    def test_project_status_statement_cannot_trigger_edit(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.95,
                "normalized_request": "continue editing the project",
                "reason": "The project was mentioned.",
                "topic": "Elaina project",
                "speech_act": "statement",
                "action_requested": False,
                "action_target": "",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "I'm gonna continue editing my project tonight.",
            conversation_state={
                "active_topic": "Elaina project",
            },
        )

        self.assertEqual(result.intent, "conversation")
        self.assertFalse(result.action_requested)

    def test_exact_continue_project_transcript_is_conversation(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.95,
                "normalized_request": "continue on the project",
                "reason": "The project was mentioned.",
                "topic": "Elaina project",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Yeah, back on this project's thing to continue on.",
            conversation_state={
                "active_topic": "Elaina project",
            },
        )

        self.assertEqual(result.intent, "conversation")
        self.assertFalse(result.action_requested)

    def test_explicit_non_action_clarification_is_conversation(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.95,
                "normalized_request": "continue editing a project",
                "reason": "The user said editing.",
                "topic": "Elaina project",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "No, I'm just talking about that I'm gonna continue on "
            "editing a project.",
            conversation_state={
                "active_topic": "Elaina project",
            },
        )

        self.assertEqual(result.intent, "conversation")
        self.assertFalse(result.action_requested)

    def test_project_idea_request_is_read_only_question(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.95,
                "normalized_request": "add something to the project",
                "reason": "The user said add.",
                "topic": "Elaina project",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "What should I add to my project next?",
            conversation_state={
                "active_topic": "Elaina project",
            },
        )

        self.assertEqual(result.intent, "project_question")
        self.assertFalse(result.action_requested)

    def test_direct_project_change_remains_project_edit(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.99,
                "normalized_request": (
                    "Add a settings button next to the Screen button."
                ),
                "reason": "Direct concrete edit request.",
                "topic": "Elaina project UI",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Screen button controls",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Add a settings button next to the Screen button."
        )

        self.assertEqual(result.intent, "project_edit")
        self.assertTrue(result.action_requested)

    def test_polite_direct_project_change_remains_project_edit(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.99,
                "normalized_request": (
                    "Add a random button next to the Screen button."
                ),
                "reason": "Direct concrete edit request.",
                "topic": "Elaina project UI",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Can you add a random button next to the screen button?"
        )

        self.assertEqual(result.intent, "project_edit")
        self.assertTrue(result.action_requested)

    def test_food_follow_up_cannot_become_project_style_edit(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.95,
                "normalized_request": (
                    "add Japanese style to the project"
                ),
                "reason": "Incorrectly relied on old project context.",
                "topic": "Japanese food",
                "speech_act": "statement",
                "action_requested": False,
                "action_target": "",
                "topic_shift": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "I want something Japanese style.",
            recent_turns=[
                {
                    "role": "user",
                    "content": "What should I eat for a midnight snack?",
                },
                {
                    "role": "assistant",
                    "content": "What kind of food are you in the mood for?",
                },
            ],
            conversation_state={
                "active_topic": "midnight snacks",
            },
        )

        self.assertEqual(result.intent, "conversation")
        self.assertEqual(
            result.normalized_request,
            "I want something Japanese style.",
        )

    def test_explicit_japanese_ui_request_can_edit(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_edit",
                "confidence": 0.99,
                "normalized_request": (
                    "Add Japanese styling to the chat panel."
                ),
                "reason": "Direct concrete UI request.",
                "topic": "Elaina project UI",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Add Japanese styling to the chat panel."
        )

        self.assertEqual(result.intent, "project_edit")

    def test_agent_creation_intent_is_accepted(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "agent_create",
                "confidence": 0.99,
                "normalized_request": (
                    "Create an agent that can manage Google Calendar events."
                ),
                "reason": "Direct request to create a new agent.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Google Calendar Agent",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Create an agent that can manage my Google Calendar."
        )

        self.assertEqual(result.intent, "agent_create")
        self.assertTrue(result.action_requested)

    def test_calendar_write_intent_is_accepted(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "calendar_action",
                "confidence": 0.99,
                "normalized_request": (
                    "Add a math class tomorrow at 3 PM to Google Calendar."
                ),
                "reason": "Direct calendar write request.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Math class calendar event",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Add my math class tomorrow at 3 to my calendar."
        )

        self.assertEqual(result.intent, "calendar_action")
        self.assertTrue(result.action_requested)

    def test_direct_agent_request_recovers_from_bad_model_route(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.95,
                "normalized_request": "Discuss calendar agents.",
                "reason": "Incorrect model route.",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Can you create an agent that adds Google Calendar events?"
        )

        self.assertEqual(result.intent, "agent_create")
        self.assertTrue(result.action_requested)

    def test_calendar_advice_does_not_become_calendar_write(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.99,
                "normalized_request": (
                    "Explain how to add an event to Google Calendar."
                ),
                "reason": "The user asked for instructions.",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "How do I add an event to Google Calendar?"
        )

        self.assertEqual(result.intent, "knowledge_question")
        self.assertFalse(result.action_requested)


if __name__ == "__main__":
    unittest.main()
