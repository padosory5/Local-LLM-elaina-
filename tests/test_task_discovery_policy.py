"""Regression tests for Elaina's user-facing research preflight.

The policy is intentionally deterministic.  These tests keep the four
examples the user uses to judge Phase 4D from drifting back to an immediate,
model-dependent Google search.
"""

import unittest

from brain.task_discovery_policy import TaskDiscoveryPolicy


class DiscoveryCategoryTests(unittest.TestCase):
    def test_user_archetypes_are_recognised_across_common_wording(self):
        cases = (
            ("Give me a shortlist of hotels in Seoul.", "hotel"),
            ("Which hotel should I stay at in Seoul?", "hotel"),
            ("Find the best place to buy a GPU.", "gpu"),
            ("Compare graphics cards under $500.", "gpu"),
            ("Best restaurants to go in Seoul.", "restaurant"),
            ("Recommend cafes near Hongdae.", "restaurant"),
            ("Cars to buy under $10K.", "car"),
            ("Find used vehicles near Seoul.", "car"),
        )

        for request, expected in cases:
            with self.subTest(request=request):
                category = TaskDiscoveryPolicy.category_for(request)
                self.assertIsNotNone(category)
                self.assertEqual(category[0], expected)
                self.assertTrue(TaskDiscoveryPolicy.needs_discovery_conversation(request))

    def test_stable_explanation_does_not_start_a_source_choice(self):
        for request in (
            "What is a GPU?",
            "Explain hotel check-in times.",
            "How does a car engine work?",
        ):
            with self.subTest(request=request):
                self.assertFalse(TaskDiscoveryPolicy.needs_discovery_conversation(request))
                self.assertIsNone(
                    TaskDiscoveryPolicy.advise(request, browser_ready=True),
                )

    def test_explicit_immediate_search_can_skip_the_optional_offer(self):
        self.assertIsNone(TaskDiscoveryPolicy.advise(
            "Search hotels in Seoul right away.", browser_ready=True,
        ))


class DiscoveryOfferTests(unittest.TestCase):
    def test_live_offer_says_why_it_is_useful_without_naming_an_untrusted_url(self):
        offer = TaskDiscoveryPolicy.advise(
            "Give me a shortlist of hotels in Seoul.", browser_ready=True,
        )

        self.assertIsNotNone(offer)
        self.assertEqual(offer.category, "hotel")
        self.assertEqual(offer.source_kind, "booking listings")
        self.assertIn("Live booking listings", offer.offer_text)
        self.assertNotIn("http", offer.offer_text.casefold())

    def test_unavailable_browser_offers_the_truthful_lower_effort_path(self):
        offer = TaskDiscoveryPolicy.advise(
            "Best restaurants to go in Seoul.", browser_ready=False,
        )

        self.assertIsNotNone(offer)
        self.assertIn("Desktop Control Mode is off", offer.offer_text)
        self.assertIn("quick web overview", offer.offer_text)

    def test_prior_candidates_never_trigger_a_second_source_choice(self):
        self.assertIsNone(TaskDiscoveryPolicy.advise(
            "Which of those hotels is cheaper?",
            browser_ready=True,
            has_prior_candidates=True,
        ))


class StrategyReplyTests(unittest.TestCase):
    def test_preference_only_reply_selects_live_research_and_retains_constraints(self):
        reply = TaskDiscoveryPolicy.interpret_reply(
            "Under ₩200,000 near Hongdae this weekend.",
            browser_ready=True,
        )

        self.assertEqual(reply.mode, "specialized")
        self.assertEqual(reply.preferences["budget"], "₩200,000")
        self.assertEqual(reply.preferences["area"], "Hongdae")
        self.assertEqual(reply.preferences["dates"], "this weekend")

    def test_negative_reply_selects_overview_without_browser_work(self):
        reply = TaskDiscoveryPolicy.interpret_reply(
            "No, a quick overview is enough.", browser_ready=True,
        )

        self.assertEqual(reply.mode, "overview")

    def test_positive_reply_with_browser_off_degrades_to_overview(self):
        reply = TaskDiscoveryPolicy.interpret_reply("Yes, go ahead.", browser_ready=False)

        self.assertEqual(reply.mode, "overview")

    def test_unrelated_reply_stays_unapproved(self):
        reply = TaskDiscoveryPolicy.interpret_reply(
            "By the way, how is the weather?", browser_ready=True,
        )

        self.assertEqual(reply.mode, "unclear")


if __name__ == "__main__":
    unittest.main()
