"""Offering something nobody asked for -- and, mostly, not.

Phase 4E.2 worked out that an action would help and that the user had not
asked for it. Nothing read that, so every offer Elaina made was a repair for
a promise she had already wrongly made. These tests cover the two judgements
this phase adds: whether an offer belongs here at all, and how often it does
not.

The three levels, from the brief:

    1  informational   -- looking it up costs nothing; never ask, just do it
    2  user-visible    -- a window or a tab appears; offer it
    3  consequential   -- keeps the approval wall it already has
"""

from __future__ import annotations

import random
import unittest

from brain.deliberation import goal_intent, interaction
from brain.recommendation import (
    DEFAULT_CAPABILITY_GAP,
    DEFAULT_DECLINED_GAP,
    DEFAULT_TURN_GAP,
    Recommendation,
    RecommendationPolicy,
    reads_as_clear_acceptance,
    subject_is_offerable,
    subject_phrase,
)
from brain.response_policy import ClosingOfferGuard
from brain.intent_router import IntentDecision
from tests.turn_harness import build_engine


def _policy(**kwargs) -> RecommendationPolicy:
    kwargs.setdefault("rng", random.Random(20260830))
    return RecommendationPolicy(**kwargs)


def _decision(mode=interaction.RECOMMEND, level=2) -> interaction.InteractionDecision:
    return interaction.InteractionDecision(
        mode=mode, need=interaction.NEED_MACHINE, permission_level=level,
    )


def _offer(policy, level=2, capability="browser_control", subject="the hotel page"):
    return policy.offer(
        _decision(level=level),
        capability_id=capability,
        capability_name="browser control",
        subject=subject,
    )


class LevelTests(unittest.TestCase):
    """Criterion 8: the three action levels behave differently."""

    def test_a_needed_lookup_is_never_turned_into_a_question(self):
        # "What's the weather tomorrow?" must not become "would you like me
        # to search the weather?" -- the brief names this case. The guard is
        # the *mode*, not the level: a needed lookup is decided `execute` by
        # 4E.2 and never reaches this layer at all.
        route = IntentDecision(
            intent="web_search", confidence=0.95,
            normalized_request="what is the weather tomorrow",
            information_freshness="live", requires_external_evidence=True,
        )
        goal = goal_intent.read(route)
        decision = interaction.decide(route, goal=goal)

        self.assertEqual(decision.mode, interaction.EXECUTE)
        self.assertFalse(_policy().should_offer(decision, "web_search"))

    def test_optional_extra_effort_may_be_offered_even_at_level_one(self):
        # The other half of the brief's rule: level 1 skips permission "if
        # clearly necessary". When it is not necessary -- she has already
        # answered and a source would add something -- offering is the point.
        # "I'm thinking about getting a new monitor" is exactly this.
        route = IntentDecision(
            intent="conversation", confidence=0.95,
            normalized_request="i am thinking about getting a new monitor",
        )
        goal = goal_intent.read(route)
        decision = interaction.decide(route, goal=goal)

        self.assertEqual(decision.mode, interaction.RECOMMEND)
        self.assertEqual(decision.permission_level, 1)
        self.assertTrue(_policy().should_offer(decision, "web_search"))

    def test_musing_is_told_apart_from_talking_about_oneself(self):
        from brain.recommendation import worth_offering

        for request in (
            "i am thinking about getting a new monitor",
            "that restaurant near hongdae looked pretty good",
            "i wonder if this library supports that",
        ):
            with self.subTest(request=request):
                self.assertTrue(worth_offering(request))

        for request in (
            "i keep procrastinating on my project",
            "what is recursion",
            "that movie was much better than i expected",
            "i feel tired today",
        ):
            with self.subTest(request=request):
                self.assertFalse(worth_offering(request))

    def test_level_two_is_where_an_offer_belongs(self):
        policy = _policy()

        offer = _offer(policy, level=2)

        self.assertIsInstance(offer, Recommendation)
        self.assertEqual(offer.capability, "browser_control")

    def test_level_three_is_still_offerable_but_keeps_its_own_wall(self):
        # This layer never stands in for security/. It only decides whether
        # to raise the subject; the approval gate still runs downstream.
        policy = _policy()

        self.assertIsNotNone(_offer(policy, level=3, capability="ui_control"))

    def test_only_a_recommend_decision_offers_at_all(self):
        for mode in (
            interaction.ANSWER, interaction.EXECUTE,
            interaction.ASK_PERMISSION, interaction.CLARIFY,
        ):
            with self.subTest(mode=mode):
                policy = _policy()
                self.assertFalse(
                    policy.should_offer(
                        _decision(mode=mode), "browser_control",
                    )
                )

    def test_an_explicit_request_is_never_turned_into_an_offer(self):
        # Criterion 6: an explicit command must not be followed by a
        # redundant permission question. 4E.2 routes it to execute; this
        # confirms the offer layer agrees.
        route = IntentDecision(
            intent="computer_action", confidence=0.95,
            normalized_request="open the hotel page",
            action_requested=True, computer_operation="open_url",
        )
        goal = goal_intent.read(route)
        decision = interaction.decide(route, goal=goal)

        self.assertEqual(decision.mode, interaction.EXECUTE)
        self.assertFalse(
            _policy().should_offer(decision, "browser_control")
        )


class SubjectExtractionTests(unittest.TestCase):
    """Naming the thing, when the router named nothing.

    ``goal.subject`` falls back to the whole utterance when the router sets
    no topic, and an offer built from that read "Want me to look into i am
    thinking about getting a new monitor?". Heuristic, no model call, and
    silent when it cannot tell -- an awkward offer is worse than none.
    """

    def test_the_head_noun_is_what_comes_out(self):
        for request, expected in (
            ("i am thinking about getting a new monitor", "monitor"),
            ("i might need a new keyboard", "keyboard"),
            ("i've been looking at standing desks", "standing desk"),
            ("i might upgrade my desk too", "desk"),
            ("i am looking for a good mechanical keyboard",
             "mechanical keyboard"),
            ("i am in the market for a used car", "used car"),
        ):
            with self.subTest(request=request):
                self.assertEqual(subject_phrase(request), expected)

    def test_a_noun_phrase_stops_at_a_preposition(self):
        # "restaurant near Hongdae" is about a restaurant; keeping the tail
        # would leave the offer asking about "near Hongdae".
        self.assertEqual(
            subject_phrase("that restaurant near hongdae looked pretty good"),
            "restaurant",
        )

    def test_weak_extraction_stays_quiet(self):
        for request in (
            "yeah they are getting expensive",
            "what is recursion",
            "i keep procrastinating",
            "i might get one",
            "i am thinking about it",
            "",
        ):
            with self.subTest(request=request):
                self.assertEqual(subject_phrase(request), "")

    def test_plurals_are_spoken_as_one_thing(self):
        self.assertEqual(subject_phrase("im looking at cheap monitors"),
                         "monitor")

    def test_it_costs_no_model_call(self):
        # Structural: the module imports nothing that could make one.
        import brain.recommendation as module

        self.assertFalse(hasattr(module, "client"))
        for _ in range(200):
            subject_phrase("i am thinking about getting a new monitor")


class AcceptanceTests(unittest.TestCase):
    """A suggestion is accepted only by a clear yes.

    Live regression: "What should I eat for dinner?" produced an offer, and
    "That sounds good" -- approval of the dinner -- was classified as
    accepting it. The turn became ``computer_action``, browser control ran,
    and it failed. The consent classifier answers "is this positive about
    the offer?", which is the right question for an offer that was *asked*
    and the wrong one for a suggestion that was not.
    """

    def test_a_clear_yes_accepts(self):
        for said in (
            "yes please", "yeah, look it up", "go ahead", "sure, check",
            "search for some", "yes", "ok do it", "sure",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_clear_acceptance(said))

    def test_approval_of_the_topic_is_not_acceptance(self):
        for said in (
            "that sounds good", "yeah they are expensive", "interesting",
            "I might get one", "that's cool", "I like that", "maybe",
            "yeah that sounds good", "that looks nice",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said))

    def test_a_refusal_is_not_acceptance(self):
        for said in ("no thanks", "nah", "not right now"):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said))


class OfferEligibilityTests(unittest.TestCase):
    """What can even be offered.

    "What should I eat for dinner?" is answerable, has no browsing target,
    and produced "Happy to dig into a Dinner if that helps."
    """

    def test_an_abstract_topic_is_not_a_lookup(self):
        for subject in (
            "Dinner", "dinner", "lunch", "Health", "Personal Activities",
            "the weather", "hobbies", "advice",
        ):
            with self.subTest(subject=subject):
                self.assertFalse(subject_is_offerable(subject))

    def test_a_qualifier_makes_it_concrete_again(self):
        for subject in (
            "restaurants near Hongdae", "dinner in Gangnam",
            "best ramen shops", "cafes around Seoul",
        ):
            with self.subTest(subject=subject):
                self.assertTrue(subject_is_offerable(subject))

    def test_a_thing_is_always_offerable(self):
        for subject in ("monitor", "used car", "standing desk"):
            with self.subTest(subject=subject):
                self.assertTrue(subject_is_offerable(subject))


class PersonalSubjectTests(unittest.TestCase):
    """Some subjects are about the person, not about a thing to look up."""

    def test_a_habit_is_never_offered_as_research(self):
        # Live: "i keep procrastinating on my project" produced
        # "I could check a procrastination for you -- worth it?".
        for subject in (
            "procrastination", "motivation", "sleep", "anxiety",
            "feeling tired", "burnout", "focus",
        ):
            with self.subTest(subject=subject):
                self.assertFalse(subject_is_offerable(subject))

    def test_a_thing_still_is(self):
        for subject in (
            "monitor", "standing desk", "used car", "hotels in Seoul",
            "mechanical keyboard", "restaurant",
        ):
            with self.subTest(subject=subject):
                self.assertTrue(subject_is_offerable(subject))


class CooldownTests(unittest.TestCase):
    """Criterion 7: recommendation spam handling."""

    def test_she_does_not_offer_on_every_turn(self):
        policy = _policy()
        policy.begin_turn()

        self.assertIsNotNone(_offer(policy))

        offered = 0
        for _ in range(DEFAULT_TURN_GAP - 1):
            policy.begin_turn()
            if _offer(policy, capability="ui_control") is not None:
                offered += 1

        self.assertEqual(offered, 0, "offered again inside the turn gap")

    def test_the_gap_does_eventually_pass(self):
        policy = _policy()
        for _ in range(DEFAULT_TURN_GAP + 1):
            policy.begin_turn()
        self.assertIsNotNone(_offer(policy))

        for _ in range(DEFAULT_TURN_GAP):
            policy.begin_turn()

        self.assertIsNotNone(_offer(policy, capability="ui_control"))

    def test_the_same_ability_waits_longer_than_a_different_one(self):
        policy = _policy()
        for _ in range(DEFAULT_CAPABILITY_GAP):
            policy.begin_turn()
        self.assertIsNotNone(_offer(policy, capability="browser_control"))

        for _ in range(DEFAULT_TURN_GAP):
            policy.begin_turn()

        self.assertIsNone(
            _offer(policy, capability="browser_control"),
            "re-offered the same ability inside its own gap",
        )
        self.assertIsNotNone(_offer(policy, capability="ui_control"))

    def test_a_refusal_buys_a_longer_silence(self):
        policy = _policy()
        for _ in range(DEFAULT_CAPABILITY_GAP):
            policy.begin_turn()
        self.assertIsNotNone(_offer(policy))

        policy.note_declined()

        offered = 0
        for _ in range(DEFAULT_DECLINED_GAP - 1):
            policy.begin_turn()
            if _offer(policy, capability="ui_control") is not None:
                offered += 1

        self.assertEqual(offered, 0, "kept offering after a no")

    def test_acceptance_costs_nothing(self):
        policy = _policy()
        policy.note_declined()

        policy.note_accepted()

        for _ in range(DEFAULT_CAPABILITY_GAP):
            policy.begin_turn()
        self.assertIsNotNone(_offer(policy))


class PhrasingTests(unittest.TestCase):
    """The complaint that started Phase 4E, applied to offers."""

    def _consecutive(self, count):
        policy = _policy()
        said = []
        for index in range(count):
            for _ in range(DEFAULT_CAPABILITY_GAP):
                policy.begin_turn()
            offer = _offer(policy, capability=f"cap_{index}")
            if offer is not None:
                said.append(offer.text)
        return said

    def test_consecutive_offers_are_worded_differently(self):
        said = self._consecutive(4)

        self.assertEqual(len(said), 4)
        self.assertEqual(len(said), len(set(said)), said)

    def test_an_offer_names_what_it_is_about(self):
        offer = _offer(_policy(), subject="hotels in Seoul")

        self.assertIn("hotels in Seoul", offer.text)

    def test_a_whole_sentence_subject_is_trimmed_to_fit(self):
        offer = _offer(
            _policy(),
            subject="find me some genuinely good and well reviewed hotels "
                    "somewhere in central Seoul please",
        )

        self.assertLessEqual(len(offer.text.split()), 20)

    def test_the_policy_offer_is_protected_by_order_not_by_wording(self):
        # ClosingOfferGuard now strips *any* trailing offer to act, which is
        # what makes this layer the only one deciding whether the user sees
        # one. Its own offers are the same shape, so they are protected by
        # running after the guard rather than by dodging it. Asserting the
        # order is the real invariant; asserting the wording survives would
        # be asserting something false.
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine._answer_turn)

        strip_at = source.index("ClosingOfferGuard.strip(")
        append_at = source.index("self._append_recommendation(")

        self.assertLess(
            strip_at, append_at,
            "the guard must run before the offer is appended, or she would "
            "delete her own offer",
        )

    def test_korean_offers_are_korean(self):
        policy = _policy(language="ko")
        offer = _offer(policy, subject="서울 호텔")

        self.assertTrue(any("가" <= ch <= "힣" for ch in offer.text))

    def test_a_line_that_already_offers_is_recognised(self):
        for text in (
            "Want me to pull it up?",
            "I can check that for you.",
            "Should I open it?",
        ):
            with self.subTest(text=text):
                self.assertTrue(RecommendationPolicy.reads_as_offer(text))

    def test_a_plain_answer_is_not_mistaken_for_an_offer(self):
        for text in (
            "Recursion is a function calling itself.",
            "The exchange rate is 13.5 KRW per yen.",
        ):
            with self.subTest(text=text):
                self.assertFalse(RecommendationPolicy.reads_as_offer(text))


class EngineTests(unittest.TestCase):
    """The offer reaches the reply, and is parked so "ok" resolves it."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.engine.recommendations.reset()
        self.engine.capability_offer.clear()
        for _ in range(DEFAULT_CAPABILITY_GAP):
            self.engine.recommendations.begin_turn()

    def _append(self, reply, level=2, capability="browser_control"):
        class Choice:
            def __init__(self, value):
                self.capability = value

        return self.engine._append_recommendation(
            reply,
            decision=_decision(level=level),
            capability=Choice(capability),
            goal=goal_intent.SemanticGoal(
                intent=goal_intent.RECOMMEND, subject="the hotel page",
            ),
        )

    def test_the_offer_is_appended_to_the_reply(self):
        result = self._append("That one looks pretty nice.")

        self.assertNotEqual(result, "That one looks pretty nice.")
        self.assertTrue(result.startswith("That one looks pretty nice."))

    def test_the_offer_is_parked_so_a_later_yes_resolves_it(self):
        self._append("That one looks pretty nice.")

        pending = self.engine.capability_offer.peek()

        self.assertIsNotNone(pending)
        self.assertEqual(pending.capability_id, "browser_control")

    def test_a_reply_that_already_offers_is_left_alone(self):
        reply = "That one looks nice. Want me to pull it up?"

        self.assertEqual(self._append(reply), reply)

    def test_an_offer_already_waiting_stops_a_second_one(self):
        # Live: the grounded-value guard retracted an unchecked claim and
        # parked its own offer, then this appended a second one to the same
        # reply -- and overwrote the first in the gate.
        self.engine.capability_offer.offer(
            capability_id="browser_control",
            goal="check the price",
            offer_text="I haven't actually checked that -- want me to?",
        )

        self.assertEqual(
            self._append("Monitors are pricey."), "Monitors are pricey.",
        )

    def test_an_empty_reply_is_never_given_an_offer_instead(self):
        self.assertEqual(self._append("   "), "   ")

    def test_an_unavailable_ability_is_never_offered(self):
        # Offering something that is switched off is worse than saying
        # nothing: the user says yes and nothing happens.
        self.engine.computer_control_mode.set_enabled(False)
        try:
            self.assertEqual(
                self._append("That one looks nice."),
                "That one looks nice.",
            )
        finally:
            self.engine.computer_control_mode.set_enabled(True)

    def test_a_level_one_capability_is_never_offered_through_the_engine(self):
        self.assertEqual(
            self._append("Here you go.", level=1, capability="web_search"),
            "Here you go.",
        )

    def test_twenty_turns_do_not_produce_twenty_offers(self):
        # The "interactive without being pushy" bar, asserted rather than
        # eyeballed: an offer on every answer is what makes offers stop
        # meaning anything.
        offers = 0
        for _ in range(20):
            self.engine.recommendations.begin_turn()
            reply = self._append("Sure, that sounds good.")
            if reply != "Sure, that sounds good.":
                offers += 1

        self.assertGreaterEqual(offers, 1, "never offered at all")
        self.assertLessEqual(offers, 4, f"offered {offers} times in 20 turns")


if __name__ == "__main__":
    unittest.main()
