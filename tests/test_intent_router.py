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

    def test_takeover_authorizes_a_grounded_open_app_action(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Open Spotify.",
                "reason": "The user authorized local computer control.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "spotify",
                "computer_operation": "open_app",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, open Spotify.")

        self.assertEqual(result.intent, "computer_action")
        self.assertTrue(result.action_requested)
        self.assertEqual(result.action_target, "spotify")

    def test_open_app_without_takeover_waits_for_contextual_consent(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Open Spotify.",
                "reason": "The user asked to open an app.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "spotify",
                "computer_operation": "open_app",
            }),
            "qwen3:8b",
        )

        result = router.route("Could you open Spotify?")

        self.assertEqual(result.intent, "computer_action")
        self.assertFalse(result.action_requested)
        self.assertEqual(result.action_target, "spotify")
        self.assertEqual(result.computer_operation, "open_app")

    def test_contextual_consent_authorizes_the_pending_app(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Open Discord.",
                "reason": "The user requested an installed app.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Discord",
                "computer_operation": "open_app",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Open Discord.",
            computer_action_authorized=True,
        )

        self.assertEqual(result.intent, "computer_action")
        self.assertTrue(result.action_requested)
        self.assertEqual(result.action_target, "Discord")

    def test_takeover_authorizes_a_grounded_graceful_close(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Close Discord.",
                "reason": "The user requested a graceful app close.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Discord",
                "computer_operation": "close_app",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, close Discord.")

        self.assertEqual(result.computer_operation, "close_app")
        self.assertTrue(result.action_requested)

    def test_force_quit_is_structured_separately_from_close(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Completely quit VS Code.",
                "reason": "The user requested complete termination.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "VS Code",
                "computer_operation": "force_quit_app",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, completely quit VS Code.")

        self.assertEqual(result.computer_operation, "force_quit_app")
        self.assertTrue(result.action_requested)

    def test_file_creation_keeps_name_and_location_separate(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Create notes.txt in Documents.",
                "reason": "The user requested a new empty file.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "notes.txt",
                "computer_operation": "create_file",
                "computer_location": "Documents",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Takeover, create notes.txt in Documents."
        )

        self.assertEqual(result.computer_operation, "create_file")
        self.assertEqual(result.action_target, "notes.txt")
        self.assertEqual(result.computer_location, "Documents")
        self.assertTrue(result.action_requested)

    def test_model_cannot_invent_a_filesystem_location(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Create notes.txt.",
                "reason": "The user requested a new file.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "notes.txt",
                "computer_operation": "create_file",
                "computer_location": "Documents",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, create notes.txt.")

        self.assertEqual(result.computer_operation, "unsupported")
        self.assertFalse(result.action_requested)

    def test_open_url_keeps_spoken_target_and_validated_url_separate(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Open YouTube in a new tab.",
                "reason": "The user requested browser navigation.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "YouTube",
                "computer_operation": "open_url",
                "computer_url": "https://www.youtube.com",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, open YouTube in a new tab.")

        self.assertEqual(result.computer_operation, "open_url")
        self.assertEqual(result.computer_url, "https://www.youtube.com")
        self.assertTrue(result.action_requested)

    def test_varied_folder_deletion_is_structured_semantically(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Remove the Notes folder from Documents.",
                "reason": "The user wants an existing folder recycled.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Notes",
                "computer_operation": "delete_folder",
                "computer_location": "Documents",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Takeover, get rid of my Notes folder inside Documents."
        )

        self.assertEqual(result.computer_operation, "delete_folder")
        self.assertEqual(result.action_target, "Notes")
        self.assertEqual(result.computer_location, "Documents")
        self.assertTrue(result.action_requested)

    def test_delete_without_takeover_waits_for_contextual_consent(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Trash draft.txt in Downloads.",
                "reason": "The user wants an existing file recycled.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "draft.txt",
                "computer_operation": "delete_file",
                "computer_location": "Downloads",
            }),
            "qwen3:8b",
        )

        result = router.route("Trash draft.txt from my Downloads folder.")

        self.assertEqual(result.computer_operation, "delete_file")
        self.assertFalse(result.action_requested)

    def test_closing_one_browser_tab_remains_unsupported(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Close the github.com browser tab.",
                "reason": "Specific browser-tab control is not available.",
                "speech_act": "action_request",
                "action_requested": False,
                "action_target": "github.com tab",
                "computer_operation": "unsupported",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, close the github.com tab.")

        self.assertEqual(result.computer_operation, "unsupported")
        self.assertFalse(result.action_requested)

    def test_model_cannot_substitute_an_allowed_app_for_the_requested_target(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Open PowerShell.",
                "reason": "The user requested computer control.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "spotify",
                "computer_operation": "open_app",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, open PowerShell.")

        self.assertEqual(result.intent, "computer_action")
        self.assertFalse(result.action_requested)
        self.assertEqual(result.computer_operation, "unsupported")

    def test_non_open_pc_action_stays_in_blocked_computer_path(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "computer_action",
                "confidence": 0.99,
                "normalized_request": "Disable Smart App Control.",
                "reason": "The user requested a Windows settings change.",
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Smart App Control",
                "computer_operation": "unsupported",
            }),
            "qwen3:8b",
        )

        result = router.route("Takeover, disable Smart App Control.")

        self.assertEqual(result.intent, "computer_action")
        self.assertFalse(result.action_requested)
        self.assertEqual(result.computer_operation, "unsupported")

    def test_latest_periodic_event_uses_date_aware_completed_event_search(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "web_search",
                "confidence": 1.0,
                "normalized_request": "latest FIFA World Cup winner",
                "reason": "Current sports result.",
                "search_query": (
                    "latest completed edition FIFA World Cup winner "
                    "as of 2026-08-03"
                ),
                "speech_act": "information_request",
                "verification_required": True,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Can you tell me who won the latest FIFA World Cup?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)
        self.assertIn("latest completed edition", result.search_query)
        self.assertIn("as of", result.search_query)
        self.assertNotEqual(
            result.search_query,
            "winner of the 2026 FIFA World Cup",
        )

    def test_read_only_research_does_not_require_magic_search_words(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "web_search",
                "confidence": 0.96,
                "normalized_request": "Nvidia's current stock price",
                "reason": "The fact changes throughout the day.",
                "search_query": "Nvidia current stock price",
                "speech_act": "information_request",
                "action_requested": False,
                "verification_required": True,
            }),
            "qwen3:8b",
        )

        result = router.route("I wonder what Nvidia is trading at right now.")

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)

    def test_live_exchange_rate_cannot_remain_local_knowledge(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.95,
                "normalized_request": "current USD to KRW exchange rate",
                "reason": "A factual question.",
                "search_query": "",
                "speech_act": "information_request",
                "information_freshness": "live",
                "requires_external_evidence": True,
                "verification_required": False,
            }),
            "qwen3:8b",
        )

        result = router.route("What is the current USD to KRW exchange rate?")

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)
        self.assertTrue(result.verification_required)
        self.assertIn("USD", result.search_query)

    def test_historical_market_value_uses_external_record(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.94,
                "normalized_request": (
                    "USD to KRW exchange rate on January 15, 2024"
                ),
                "reason": "The requested date is in the past.",
                "search_query": "",
                "speech_act": "information_request",
                "information_freshness": "historical_record",
                "requires_external_evidence": True,
                "verification_required": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "What was the USD to KRW exchange rate on January 15, 2024?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)
        self.assertIn("January 15, 2024", result.search_query)

    def test_stable_concept_stays_local_knowledge(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.98,
                "normalized_request": "Explain why exchange rates fluctuate.",
                "reason": "This asks for a stable economic concept.",
                "search_query": "",
                "speech_act": "information_request",
                "information_freshness": "stable",
                "requires_external_evidence": False,
                "verification_required": False,
            }),
            "qwen3:8b",
        )

        result = router.route("Why do exchange rates fluctuate?")

        self.assertEqual(result.intent, "knowledge_question")
        self.assertFalse(result.action_requested)

    def test_medication_recommendation_uses_external_evidence(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.96,
                "normalized_request": "Recommend an option for insomnia.",
                "reason": "The user wants health advice.",
                "speech_act": "advice",
                "recommendation_needed": True,
                "advice_domain": "health",
                "information_freshness": "changing",
                "requires_external_evidence": True,
                "verification_required": True,
                "search_query": "official insomnia treatment options",
            }),
            "qwen3:8b",
        )

        result = router.route("Can you recommend medication for my insomnia?")

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.recommendation_needed)
        self.assertEqual(result.advice_domain, "health")
        self.assertTrue(result.action_requested)
        self.assertTrue(result.verification_required)

    def test_health_web_recommendation_always_requires_verification(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "web_search",
                "confidence": 0.96,
                "normalized_request": "OTC options for occasional heartburn",
                "reason": "The user wants current health advice.",
                "search_query": "official occasional heartburn OTC options",
                "speech_act": "advice",
                "recommendation_needed": True,
                "advice_domain": "health",
                "information_freshness": "stable",
                "requires_external_evidence": True,
                "verification_required": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "What over-the-counter option could help occasional heartburn?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.verification_required)

    def test_low_risk_personal_advice_stays_conversational(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.98,
                "normalized_request": "Recommend an avatar format.",
                "reason": "This is a low-risk personal choice.",
                "speech_act": "advice",
                "recommendation_needed": True,
                "advice_domain": "general",
                "information_freshness": "stable",
                "requires_external_evidence": False,
                "verification_required": False,
            }),
            "qwen3:8b",
        )

        result = router.route("Should I use Live2D or a 3D avatar?")

        self.assertEqual(result.intent, "conversation")
        self.assertTrue(result.recommendation_needed)
        self.assertFalse(result.action_requested)

    def test_urgent_health_advice_does_not_wait_for_web_research(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 1.0,
                "normalized_request": (
                    "Respond immediately to a possible sleep-aid overdose."
                ),
                "reason": "Delay could expose the user to immediate harm.",
                "speech_act": "advice",
                "recommendation_needed": True,
                "advice_domain": "health",
                "urgent_safety": True,
                "information_freshness": "changing",
                "requires_external_evidence": True,
                "verification_required": True,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "I took too many sleep pills and I'm struggling to breathe."
        )

        self.assertEqual(result.intent, "conversation")
        self.assertTrue(result.recommendation_needed)
        self.assertTrue(result.urgent_safety)
        self.assertEqual(result.advice_domain, "health")
        self.assertFalse(result.action_requested)

    def test_unclassified_factual_source_fails_closed_to_web(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.70,
                "normalized_request": "the requested factual value",
                "reason": "The source requirement is uncertain.",
                "search_query": "",
                "speech_act": "information_request",
            }),
            "qwen3:8b",
        )

        result = router.route("Tell me the requested factual value.")

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)
        self.assertTrue(result.verification_required)

    def test_current_role_holder_cannot_remain_local_knowledge(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.95,
                "normalized_request": "Nvidia's current CEO",
                "reason": "A factual identity question.",
                "speech_act": "information_request",
                "time_scope": "current",
                "information_freshness": "stable",
                "requires_external_evidence": False,
            }),
            "qwen3:8b",
        )

        result = router.route("Who is Nvidia's CEO right now?")

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.requires_external_evidence)
        self.assertTrue(result.verification_required)

    def test_external_live_value_cannot_remain_time_question(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "time_question",
                "confidence": 0.94,
                "normalized_request": "Nvidia's current stock price",
                "speech_act": "information_request",
                "time_scope": "current",
                "information_freshness": "live",
                "requires_external_evidence": True,
            }),
            "qwen3:8b",
        )

        result = router.route("What is Nvidia stock trading at now?")

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.verification_required)

    def test_indirect_screen_interest_becomes_optional_offer(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "screen_analysis",
                "confidence": 0.95,
                "normalized_request": "Identify the artist on screen.",
                "speech_act": "information_request",
                "request_explicitness": "indirect",
                "action_requested": False,
            }),
            "qwen3:8b",
        )

        result = router.route("I wonder who drew the picture on my screen.")

        self.assertEqual(result.intent, "agent_offer")
        self.assertEqual(result.offered_intent, "screen_analysis")
        self.assertFalse(result.action_requested)

    def test_health_domain_recommendation_requires_external_evidence(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.92,
                "normalized_request": "Recommend an option for allergies.",
                "speech_act": "information_request",
                "recommendation_needed": True,
                "advice_domain": "health",
                "information_freshness": "stable",
                "requires_external_evidence": False,
            }),
            "qwen3:8b",
        )

        result = router.route("I get seasonal allergies. What could I try?")

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.requires_external_evidence)

    def test_health_recommendation_cannot_hide_as_stable_knowledge(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.93,
                "normalized_request": "Recommend an option for heartburn.",
                "speech_act": "information_request",
                "recommendation_needed": True,
                "advice_domain": "health",
                "time_scope": "timeless",
                "information_freshness": "stable",
                "requires_external_evidence": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "What over-the-counter option could help with occasional heartburn?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.requires_external_evidence)
        self.assertTrue(result.verification_required)

    def test_memory_based_project_choice_does_not_inspect_files(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_question",
                "confidence": 0.91,
                "normalized_request": "Recommend which project to finish.",
                "speech_act": "information_request",
                "recommendation_needed": True,
                "memory_relevant": True,
                "request_explicitness": "direct",
                "information_freshness": "stable",
                "requires_external_evidence": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Based on what you know about me, which project should I finish first?"
        )

        self.assertEqual(result.intent, "conversation")
        self.assertFalse(result.action_requested)

    def test_read_only_project_inspection_is_implicitly_authorized(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "project_question",
                "confidence": 0.97,
                "normalized_request": "Explain the voice input flow.",
                "reason": "The user asked to inspect the configured project.",
                "speech_act": "information_request",
                "action_requested": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Inspect the codebase and explain how voice input reaches chat."
        )

        self.assertEqual(result.intent, "project_question")
        self.assertTrue(result.action_requested)

    def test_latest_release_is_a_semantic_web_search_decision(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "web_search",
                "confidence": 0.98,
                "normalized_request": "When was the latest Qwen series released?",
                "reason": "The latest release requires current evidence.",
                "search_query": "latest Qwen series release official as of 2026-08-03",
                "speech_act": "information_request",
                "verification_required": True,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "When was the latest Qwen series released?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)
        self.assertIn("official as of", result.search_query)

    def test_router_failure_falls_back_to_conversation_without_trigger_lists(self):
        class BrokenClient:
            def chat(self, **_kwargs):
                raise RuntimeError("offline")

        router = SemanticIntentRouter(BrokenClient(), "qwen3:8b")
        result = router.route(
            "Who won the latest FIFA World Cup?"
        )

        self.assertEqual(result.intent, "conversation")
        self.assertFalse(result.action_requested)

    def test_visual_creator_musing_offers_screen_agent(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "agent_offer",
                "confidence": 0.97,
                "normalized_request": "I wonder who drew this picture.",
                "reason": "Screen analysis could identify the visible work.",
                "speech_act": "statement",
                "offered_intent": "screen_analysis",
                "offered_request": "Identify the creator of the selected picture.",
            }),
            "qwen3:8b",
        )

        result = router.route("I wonder who drew this picture.")

        self.assertEqual(result.intent, "agent_offer")
        self.assertEqual(result.offered_intent, "screen_analysis")
        self.assertFalse(result.action_requested)

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

    def test_routes_distribution_math_as_calculation(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "calculation",
                "confidence": 0.99,
                "normalized_request": (
                    "Split a 650 dollar total proportionally among "
                    "contributions of 100, 100, and 50 dollars."
                ),
                "reason": "The user requested a proportional distribution.",
                "topic": "gambling distribution",
                "is_follow_up": False,
                "speech_act": "information_request",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "We put in 100, 100, and 50 and made 650. What's the distribution?"
        )

        self.assertEqual(result.intent, "calculation")
        self.assertIn("650", result.normalized_request)

    def test_semantic_metadata_controls_memory_detail_and_screen_target(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.99,
                "normalized_request": "Explain what you remember in detail.",
                "reason": "Personal-memory follow-up requesting detail.",
                "memory_relevant": True,
                "memory_candidate": False,
                "detailed_response": True,
                "screen_target": "all",
            }),
            "qwen3:8b",
        )

        result = router.route("Walk me through everything you remember.")

        self.assertTrue(result.memory_relevant)
        self.assertFalse(result.memory_candidate)
        self.assertTrue(result.detailed_response)
        self.assertEqual(result.screen_target, "all")

    def test_invalid_screen_target_falls_back_to_configured_monitor(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "screen_analysis",
                "confidence": 0.99,
                "normalized_request": "Look at another monitor.",
                "screen_target": "monitor-99",
                "action_requested": True,
            }),
            "qwen3:8b",
        )

        result = router.route("Look at another monitor.")

        self.assertEqual(result.screen_target, "configured")

    def test_router_failure_uses_safe_conversation_fallback(self):
        class BrokenClient:
            def chat(self, **_kwargs):
                raise RuntimeError("offline")

        router = SemanticIntentRouter(BrokenClient(), "qwen3:8b")
        result = router.route(
            "We put in 100, 100, and 50 and made 650. What's the distribution?"
        )

        self.assertEqual(result.intent, "conversation")

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

    def test_casual_problem_can_become_agent_offer_without_permission(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "agent_offer",
                "confidence": 0.97,
                "normalized_request": "The project buttons look boring.",
                "reason": "A coding agent could help, but no edit was asked.",
                "speech_act": "statement",
                "action_requested": False,
                "offered_intent": "project_edit",
                "offered_request": "Redesign the project's buttons.",
                "consent_decision": "",
            }),
            "qwen3:8b",
        )

        result = router.route("The buttons on this project look boring.")

        self.assertEqual(result.intent, "agent_offer")
        self.assertFalse(result.action_requested)
        self.assertEqual(result.offered_intent, "project_edit")

    def test_varied_acceptance_is_a_semantic_router_decision(self):
        for transcript in (
            "Sure.",
            "Yeah, let's do that.",
            "Let's go for it.",
        ):
            with self.subTest(transcript=transcript):
                router = SemanticIntentRouter(
                    FakeClient({
                        "intent": "agent_consent",
                        "confidence": 0.98,
                        "normalized_request": transcript,
                        "reason": (
                            "The reply accepts the pending offer in context."
                        ),
                        "speech_act": "approval_response",
                        "action_requested": False,
                        "consent_decision": "accept",
                        "offered_intent": "",
                        "offered_request": "",
                    }),
                    "qwen3:8b",
                )

                result = router.route(
                    transcript,
                    conversation_state={
                        "pending_agent_offer": {
                            "intent": "project_edit",
                            "request": "Redesign the project's buttons.",
                        },
                    },
                )

                self.assertEqual(result.intent, "agent_consent")
                self.assertEqual(result.consent_decision, "accept")

    def test_topic_change_does_not_accept_pending_offer(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "conversation",
                "confidence": 0.99,
                "normalized_request": "What should I eat tonight?",
                "reason": "The user changed to an unrelated topic.",
                "topic": "dinner",
                "topic_shift": True,
                "consent_decision": "",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Actually, what should I eat tonight?",
            conversation_state={
                "pending_agent_offer": {
                    "intent": "project_edit",
                    "request": "Redesign the project's buttons.",
                },
            },
        )

        self.assertEqual(result.intent, "conversation")
        self.assertTrue(result.topic_shift)

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

    def test_semantic_router_resolves_example_from_active_topic(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.98,
                "normalized_request": (
                    "Give one example of how mental illness develops."
                ),
                "reason": "Resolved the follow-up from the active topic.",
                "topic": "how mental illness develops",
                "is_follow_up": True,
                "information_freshness": "stable",
                "requires_external_evidence": False,
            }),
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
                "intent": "web_search",
                "confidence": 0.95,
                "normalized_request": "When was Marathon released?",
                "reason": "A release date requires factual lookup.",
                "search_query": "Marathon official release date",
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

    def test_worth_it_question_is_conversation_not_factual_report(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.95,
                "normalized_request": (
                    "Is it worth studying engineering at the "
                    "University of Washington?"
                ),
                "reason": "The user asks for an evaluation.",
                "search_query": "",
                "topic": "University of Washington engineering",
                "entity": "University of Washington",
                "aliases": [],
                "is_follow_up": True,
                "speech_act": "advice",
                "recommendation_needed": True,
                "information_freshness": "stable",
                "requires_external_evidence": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Do you think it's worth it to go there to study engineering?"
        )

        self.assertEqual(result.intent, "conversation")
        self.assertEqual(result.speech_act, "advice")

    def test_current_clock_question_remains_time_question(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "time_question",
                "confidence": 1,
                "normalized_request": "What time is it?",
                "reason": "Current local time requested.",
                "search_query": "",
                "time_scope": "current",
                "information_freshness": "live",
                "requires_external_evidence": False,
            }),
            "qwen3:8b",
        )

        result = router.route("What time is it?")

        self.assertEqual(result.intent, "time_question")

    def test_factual_challenge_uses_grounded_context_and_search(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "fact_check",
                "confidence": 0.95,
                "normalized_request": (
                    "I found that Marathon was a recent game."
                ),
                "reason": "The user contradicts the previous answer.",
                "search_query": "Marathon official current facts verify recent game",
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
                "intent": "fact_check",
                "confidence": 0.95,
                "normalized_request": "I was right about Marathon.",
                "reason": "The user revisits the verified correction.",
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

    def test_button_complaint_becomes_tracked_optional_offer(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "agent_offer",
                "confidence": 0.95,
                "normalized_request": "Improve the project button design.",
                "reason": "The user dislikes the button design.",
                "topic": "project button design",
                "speech_act": "statement",
                "action_requested": False,
                "action_target": "button design",
                "offered_intent": "project_edit",
                "offered_request": "Improve the project button design.",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "I think the buttons on this project look boring."
        )

        self.assertEqual(result.intent, "agent_offer")
        self.assertEqual(result.offered_intent, "project_edit")
        self.assertFalse(result.action_requested)

    def test_direct_search_request_is_already_authorized(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "web_search",
                "confidence": 0.95,
                "normalized_request": "Search for Elon Musk's birth date.",
                "reason": "The user directly asked for a search.",
                "search_query": "Elon Musk birth date",
                "speech_act": "action_request",
                "action_requested": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Can you search for when Elon Musk was born?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)

    def test_mislabeled_direct_search_offer_is_recovered(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "agent_offer",
                "confidence": 0.95,
                "normalized_request": "Search for Elon Musk's birth date.",
                "reason": "Research Agent can search.",
                "speech_act": "action_request",
                "action_requested": False,
                "offered_intent": "web_search",
                "offered_request": "Search for Elon Musk's birth date.",
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Can you search for when Elon Musk was born?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)

    def test_avatar_choice_does_not_offer_agent_builder(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "agent_offer",
                "confidence": 0.95,
                "normalized_request": "Recommend Live2D or a 3D avatar.",
                "reason": "The user wants avatar advice.",
                "speech_act": "advice",
                "action_requested": False,
                "offered_intent": "agent_create",
                "offered_request": "Create a custom avatar agent.",
                "recommendation_needed": True,
                "information_freshness": "stable",
                "requires_external_evidence": False,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "Should I use Live2D or a 3D model for my local LLM avatar?"
        )

        self.assertEqual(result.intent, "conversation")
        self.assertFalse(result.action_requested)

    def test_invalid_json_is_repaired_once(self):
        class RepairClient:
            def __init__(self):
                self.calls = 0

            def chat(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return {"message": {"content": "not json"}}
                return {
                    "message": {
                        "content": json.dumps({
                            "intent": "conversation",
                            "confidence": 0.9,
                            "normalized_request": "Hello",
                            "reason": "Greeting",
                            "search_query": "",
                            "topic": "greeting",
                            "entity": "",
                            "aliases": [],
                            "is_follow_up": False,
                            "speech_act": "social",
                            "action_requested": False,
                            "action_target": "",
                            "topic_shift": True,
                            "consent_decision": "",
                            "offered_intent": "",
                            "offered_request": "",
                        }),
                    },
                }

        client = RepairClient()
        result = SemanticIntentRouter(client, "qwen3:8b").route("Hello")

        self.assertEqual(result.intent, "conversation")
        self.assertEqual(client.calls, 2)

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

    def test_project_idea_request_stays_conversational(self):
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

        self.assertEqual(result.intent, "conversation")
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
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "Screen button controls",
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
                "speech_act": "action_request",
                "action_requested": True,
                "action_target": "chat panel styling",
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

    def test_calendar_advice_does_not_become_calendar_write(self):
        router = SemanticIntentRouter(
            FakeClient({
                "intent": "knowledge_question",
                "confidence": 0.99,
                "normalized_request": (
                    "Explain how to add an event to Google Calendar."
                ),
                "reason": "The user asked for instructions.",
                "search_query": "official Google Calendar add event instructions",
                "information_freshness": "changing",
                "requires_external_evidence": True,
            }),
            "qwen3:8b",
        )

        result = router.route(
            "How do I add an event to Google Calendar?"
        )

        self.assertEqual(result.intent, "web_search")
        self.assertTrue(result.action_requested)
        self.assertNotEqual(result.intent, "calendar_action")


if __name__ == "__main__":
    unittest.main()
