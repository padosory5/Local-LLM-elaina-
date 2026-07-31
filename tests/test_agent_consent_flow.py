import unittest

import json

from agents.consent import (
    AgentConsentGate,
    SemanticConsentClassifier,
    apply_agent_permission,
)
from brain.intent_router import IntentDecision


class AgentConsentFlowTests(unittest.TestCase):
    def setUp(self):
        self.gate = AgentConsentGate(expiry_seconds=300)

    def test_offer_then_accept_restores_original_agent_task(self):
        offered_route = IntentDecision(
            intent="agent_offer",
            confidence=0.98,
            normalized_request="The project buttons look boring.",
            reason="A coding agent could help if the user wants it.",
            offered_intent="project_edit",
            offered_request="Redesign the project's buttons.",
        )

        route, context = apply_agent_permission(
            self.gate,
            offered_route,
            user_input="The project buttons look boring.",
            has_explicit_attachment=False,
            continuing_agent_flow=False,
        )

        self.assertEqual(route.intent, "agent_offer")
        self.assertIn("No specialist agent has been invoked", context)
        self.assertIsNotNone(self.gate.peek())

        accepted_route = IntentDecision(
            intent="agent_consent",
            confidence=0.99,
            normalized_request="Yeah, let's do that.",
            reason="The reply accepts the pending offer.",
            consent_decision="accept",
        )
        route, context = apply_agent_permission(
            self.gate,
            accepted_route,
            user_input="Yeah, let's do that.",
            has_explicit_attachment=False,
            continuing_agent_flow=False,
        )

        self.assertEqual(route.intent, "project_edit")
        self.assertTrue(route.action_requested)
        self.assertEqual(
            route.normalized_request,
            "Redesign the project's buttons.",
        )
        self.assertEqual(context, "")
        self.assertIsNone(self.gate.peek())

    def test_unrelated_topic_clears_pending_offer(self):
        self.gate.offer(
            intent="project_edit",
            request="Redesign the project's buttons.",
        )
        unrelated_route = IntentDecision(
            intent="conversation",
            confidence=0.99,
            normalized_request="What should I eat tonight?",
            reason="The user changed topics.",
            topic="dinner",
            topic_shift=True,
        )

        route, _context = apply_agent_permission(
            self.gate,
            unrelated_route,
            user_input="Actually, what should I eat tonight?",
            has_explicit_attachment=False,
            continuing_agent_flow=False,
        )

        self.assertEqual(route.intent, "conversation")
        self.assertIsNone(self.gate.peek())

    def test_rejection_never_restores_agent_task(self):
        self.gate.offer(
            intent="web_search",
            request="Search for the current Nvidia stock price.",
        )
        rejected_route = IntentDecision(
            intent="agent_consent",
            confidence=0.99,
            normalized_request="No, I was only wondering.",
            reason="The user declined the offer.",
            consent_decision="reject",
        )

        route, context = apply_agent_permission(
            self.gate,
            rejected_route,
            user_input="No, I was only wondering.",
            has_explicit_attachment=False,
            continuing_agent_flow=False,
        )

        self.assertEqual(route.intent, "conversation")
        self.assertFalse(route.action_requested)
        self.assertIn("declined", context)
        self.assertIsNone(self.gate.peek())

    def test_agent_builder_cannot_be_offered_for_an_unrelated_asset(self):
        route = IntentDecision(
            intent="agent_offer",
            confidence=0.95,
            normalized_request="Help me choose an avatar.",
            reason="Incorrect agent suggestion.",
            offered_intent="agent_create",
            offered_request="Create a custom avatar.",
        )

        route, _context = apply_agent_permission(
            self.gate,
            route,
            user_input="I don't know which avatar to use.",
            has_explicit_attachment=False,
            continuing_agent_flow=False,
        )

        self.assertEqual(route.intent, "conversation")
        self.assertIsNone(self.gate.peek())

    def test_dedicated_classifier_handles_contextual_acceptance(self):
        class FakeClient:
            def chat(self, **_kwargs):
                return {
                    "message": {
                        "content": json.dumps({
                            "decision": "accept",
                            "confidence": 0.99,
                            "reason": "The reply accepts this exact offer.",
                            "modified_request": "",
                        }),
                    },
                }

        classifier = SemanticConsentClassifier(FakeClient(), "test")
        offer = self.gate.offer(
            intent="project_edit",
            request="Redesign the project's buttons.",
        )

        for reply in ("Sure.", "Yeah, let's do that.", "Let's go for it."):
            with self.subTest(reply=reply):
                result = classifier.classify(reply, offer)
                self.assertEqual(result.decision, "accept")

    def test_failed_consent_classification_is_never_permission(self):
        class BrokenClient:
            def chat(self, **_kwargs):
                raise RuntimeError("offline")

        classifier = SemanticConsentClassifier(BrokenClient(), "test")
        offer = self.gate.offer(
            intent="web_search",
            request="Search for current information.",
        )

        result = classifier.classify("Yeah", offer)

        self.assertEqual(result.decision, "unclear")
        self.assertEqual(result.confidence, 0)


if __name__ == "__main__":
    unittest.main()
