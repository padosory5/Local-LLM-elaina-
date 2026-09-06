"""A sentence about what Elaina is doing has to be checked against what she
is doing.

Phase 4F.1. The guard this replaces could only delete. It knew "I'll check"
was a contract and that an unbacked contract is a lie, so it removed the
sentence -- and because nothing ever backed a sentence the model wrote
itself, it removed the honest ones too. Every "I can pull up a few current
options if you want" died with every broken promise, and Elaina lost the
ability to offer anything at all.

The fix is not a softer guard. It is a record: :class:`ActionLedger` holds
what is actually queued, running, waiting on the user, or called off, and
the words are held against *that*. So the same sentence can be true or false
depending on the state behind it -- which is the point, and is what these
tests pin:

* an offer she phrases herself survives, because something real is parked
  for the user's "yeah" to land on;
* "I'll check" survives while a search is running and never when nothing is;
* a promise to *open* a page when only a lookup ran is still refused;
* a turn the user asked for outright is never asked about again;
* calling it off, or asking for something else, beats what is pending;
* an offer nobody answered in time cannot be answered later.

The ledger cases are offline and need no model. The whole-turn cases run
through ``ChatEngine.chat()`` with a scripted model and tied hands, because
"the guard behaves" and "she behaves" have been different things before.
"""

from __future__ import annotations

import time
import unittest

from brain.action_commitment import (
    ANSWER,
    AWAITING_USER,
    COMMITMENT,
    DISPATCHING,
    EXECUTED,
    OFFER,
    ActionLedger,
    offered_action,
    speech_act_of,
)
from brain.recommendation import RecommendationPolicy
from brain.response_policy import ClosingOfferGuard
from security.capability_offer import CapabilityOfferGate
from tests.turn_harness import build_engine


MUSING = "I'm thinking about getting a new monitor"

MUSING_ROUTE = {
    "intent": "conversation",
    "confidence": 0.9,
    "normalized_request": MUSING,
    "topic": "monitor",
    "speech_act": "statement",
    "request_explicitness": "statement",
    "reason": "The user is musing about a purchase.",
}


def _engine(routes=None, *, reply=""):
    """A whole engine whose search ability is switched on.

    The harness disables the search *section* so no test reaches the
    network. The capability switch is a different thing -- it decides
    whether an offer to search can honestly be made at all -- and every
    case here turns on that distinction.
    """
    engine = build_engine(routes=routes or {})
    engine._web_search_enabled = True
    # Switching the ability on re-opens the one network door the harness
    # closed by disabling the search section. Tie it again: these cases are
    # about what she says, and none of them may reach the internet.
    engine.searched = []

    def _offline(query, max_results=5):
        engine.searched.append(str(query))
        return "No results."

    engine.research_agent._search = _offline
    if reply:
        engine.client.reply = reply
    return engine


# --------------------------------------------------------------- the record


class TheLedgerIsTheAuthorityTests(unittest.TestCase):
    """Structured state decides; the sentence never decides about itself."""

    def test_nothing_running_backs_no_commitment(self):
        ledger = ActionLedger()

        self.assertFalse(ledger.supports_commitment("I'll check."))
        self.assertEqual(ledger.act.speech_act, ANSWER)

    def test_a_running_action_backs_a_commitment(self):
        ledger = ActionLedger()

        ledger.dispatching("web_search", goal="monitor prices")

        self.assertTrue(ledger.supports_commitment("I'll check."))
        act = ledger.act
        self.assertEqual(act.speech_act, COMMITMENT)
        self.assertEqual(act.action_state, DISPATCHING)
        self.assertEqual(act.proposed_action, "web_search")

    def test_dispatch_is_not_completion(self):
        # Rule 7 of the phase brief, as a property rather than a comment.
        ledger = ActionLedger()
        ledger.dispatching("web_search")

        self.assertEqual(ledger.state, DISPATCHING)
        ledger.settled(succeeded=True)
        self.assertEqual(ledger.state, EXECUTED)

    def test_a_failed_action_backs_nothing(self):
        ledger = ActionLedger()
        ledger.dispatching("browser_control")

        ledger.settled(succeeded=False)

        self.assertFalse(ledger.supports_commitment("Let me open that."))

    def test_calling_it_off_backs_nothing(self):
        ledger = ActionLedger()
        ledger.dispatching("web_search")

        ledger.cancelled("the user called it off")

        self.assertFalse(ledger.supports_commitment("I'll check."))

    def test_a_new_turn_forgets_the_last_action(self):
        ledger = ActionLedger()
        ledger.dispatching("web_search")
        ledger.settled()

        ledger.begin_turn()

        self.assertFalse(ledger.supports_commitment("I'll check."))

    def test_promising_to_open_while_only_looking_is_refused(self):
        # The live failure this module was written for: a search ran, and
        # the reply said "let me open the website and find the current
        # rates for you". Nothing opened.
        ledger = ActionLedger()
        ledger.dispatching("web_search")
        ledger.settled()

        self.assertFalse(
            ledger.supports_commitment("Let me open the website for you."),
        )
        self.assertTrue(ledger.supports_commitment("I'll check on that."))

    def test_an_unclassifiable_promise_is_not_treated_as_a_lie(self):
        # Guessing a shape of work and guessing wrong deletes honest
        # sentences, so anything unreadable passes.
        ledger = ActionLedger()
        ledger.dispatching("task_planning")

        self.assertTrue(ledger.supports_commitment("Give me a sec."))

    def test_a_running_action_outranks_a_parked_offer(self):
        gate = CapabilityOfferGate()
        gate.offer(capability_id="web_search", goal="monitor", offer_text="?")
        ledger = ActionLedger(pending_offer=gate.peek)

        ledger.dispatching("web_search")

        self.assertEqual(ledger.act.speech_act, COMMITMENT)


class WhatASentenceClaimsTests(unittest.TestCase):
    """The classifier only reads the words. It never decides if they hold."""

    def test_the_three_acts_are_told_apart(self):
        for sentence, expected in (
            ("I'll check.", COMMITMENT),
            ("Let me pull that up.", COMMITMENT),
            ("Want me to pull it up?", OFFER),
            ("I can pull up a few current options if you want.", OFFER),
            ("I could check that for you -- worth it?", OFFER),
            ("Rooms start at $68 a night.", ANSWER),
            ("", ANSWER),
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(speech_act_of(sentence), expected)

    def test_the_offered_sentence_is_the_one_that_offers(self):
        found = offered_action(
            "Monitors have gotten cheaper lately. "
            "I can pull up a few current options if you want."
        )

        self.assertEqual(
            found, "I can pull up a few current options if you want.",
        )

    def test_a_plain_answer_offers_nothing(self):
        self.assertEqual(offered_action("Rooms start at $68 a night."), "")


class OfferLanguageSurvivesOnTheRecordTests(unittest.TestCase):
    """``keep_offers`` is answered by the ledger, not by the words."""

    def test_an_offer_with_nothing_behind_it_is_still_removed(self):
        ledger = ActionLedger()

        kept = ClosingOfferGuard.strip(
            "Monitors have gotten cheaper lately. "
            "I can pull up a few current options if you want.",
            keep_offers=ledger.offer_pending,
        )

        self.assertNotIn("pull up", kept)

    def test_an_offer_with_a_parked_answer_survives(self):
        gate = CapabilityOfferGate()
        gate.offer(capability_id="web_search", goal="monitor", offer_text="x")
        ledger = ActionLedger(pending_offer=gate.peek)

        kept = ClosingOfferGuard.strip(
            "Monitors have gotten cheaper lately. "
            "I can pull up a few current options if you want.",
            keep_offers=ledger.offer_pending,
        )

        self.assertIn("pull up a few current options", kept)


# ------------------------------------------------------------- whole turns


class SheMayOfferAgainTests(unittest.TestCase):
    """The headline of 4F.1: a valid offer is no longer deleted."""

    def test_her_own_offer_survives_and_becomes_answerable(self):
        engine = _engine(
            routes={MUSING.casefold(): MUSING_ROUTE},
            reply=(
                "Monitors have gotten a lot cheaper lately. "
                "I can pull up a few current options if you want."
            ),
        )

        reply = engine.chat(MUSING)

        self.assertIn("I can pull up a few current options", reply)
        pending = engine.capability_offer.peek()
        self.assertIsNotNone(
            pending, "she offered and left nothing to answer it with",
        )
        self.assertEqual(pending.capability_id, "web_search")
        self.assertIn("pull up a few current options", pending.offer_text)

    def test_the_act_says_she_is_waiting_on_the_user(self):
        engine = _engine(
            routes={MUSING.casefold(): MUSING_ROUTE},
            reply=(
                "Monitors have gotten a lot cheaper lately. "
                "Want me to pull up a few current options?"
            ),
        )

        engine.chat(MUSING)

        act = engine.action_ledger.act
        self.assertEqual(act.speech_act, OFFER)
        self.assertEqual(act.action_state, AWAITING_USER)
        self.assertEqual(act.proposed_action, "web_search")

    def test_yes_runs_the_stored_goal_and_asks_nothing_further(self):
        engine = _engine(
            routes={MUSING.casefold(): MUSING_ROUTE},
            reply=(
                "Monitors have gotten a lot cheaper lately. "
                "I can pull up a few current options if you want."
            ),
        )
        engine.chat(MUSING)

        engine.client.reply = "Here are a few worth a look."
        reply = engine.chat("Yeah")

        self.assertNotIn("want me to", reply.casefold())
        self.assertIsNone(
            engine.capability_offer.peek(),
            "the answered offer is still sitting there",
        )
        act = engine.action_ledger.act
        self.assertEqual(act.speech_act, COMMITMENT)
        self.assertIn("monitor", act.goal.casefold())

    def test_an_offer_with_no_available_ability_is_not_made(self):
        engine = _engine(
            routes={MUSING.casefold(): MUSING_ROUTE},
            reply=(
                "Monitors have gotten a lot cheaper lately. "
                "I can pull up a few current options if you want."
            ),
        )
        engine._web_search_enabled = False
        engine.browser_page_control_enabled = False

        reply = engine.chat(MUSING)

        self.assertNotIn("pull up a few current options", reply)
        self.assertIsNone(engine.capability_offer.peek())

    def test_she_does_not_offer_twice_in_one_reply(self):
        engine = _engine(
            routes={MUSING.casefold(): MUSING_ROUTE},
            reply=(
                "Monitors have gotten a lot cheaper lately. "
                "I can pull up a few current options if you want."
            ),
        )

        reply = engine.chat(MUSING)

        self.assertEqual(reply.casefold().count("pull up"), 1)


class _Stub:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class HerOwnOfferIsStillRationedTests(unittest.TestCase):
    """Grounding must not become a second, uncapped way to offer.

    ``RecommendationPolicy`` stays the single layer deciding how often she
    may raise something unprompted. If an offer she phrased herself skipped
    the cooldown, "she offers every single turn" would come straight back
    through the other door.
    """

    REPLY = (
        "Monitors have gotten cheaper lately. "
        "I can pull up a few current options if you want."
    )

    def _ground(self, engine):
        return engine._ground_offer_language(
            self.REPLY,
            decision=_Stub(mode="recommend", acts=False),
            capability=_Stub(capability="direct_answer"),
            goal=_Stub(subject="monitor"),
            route=_Stub(action_requested=False),
            action_performed=False,
        )

    def test_the_first_offer_is_grounded(self):
        engine = _engine()

        self._ground(engine)

        self.assertIsNotNone(engine.capability_offer.peek())

    def test_a_second_offer_in_the_cooldown_is_not(self):
        engine = _engine()
        self._ground(engine)
        # The first offer lapsed without an answer. The cooldown is what
        # decides whether she may raise another one, not the gate.
        engine.capability_offer.clear()

        self._ground(engine)

        self.assertIsNone(
            engine.capability_offer.peek(),
            "her own offer skipped the cooldown",
        )

    def test_an_outstanding_question_of_her_own_blocks_it(self):
        from brain.deliberation.goal import Goal

        engine = _engine()
        engine.clarification.offer(
            goal=Goal(kind="research", utterance="find a monitor"),
            slot="budget",
            question="Roughly what budget?",
        )

        self._ground(engine)

        self.assertIsNone(engine.capability_offer.peek())


class AnExplicitRequestIsNotQuestionedTests(unittest.TestCase):
    """Exit criterion 6: no redundant permission on a direct command."""

    ROUTE = {
        "intent": "web_search",
        "confidence": 0.95,
        "normalized_request": "look up current monitor prices",
        "topic": "monitor prices",
        "speech_act": "action_request",
        "action_requested": True,
        "requires_external_evidence": True,
        "reason": "The user asked for a lookup outright.",
    }

    def test_an_asked_for_action_parks_no_permission_question(self):
        engine = _engine(
            routes={"look up current monitor prices": self.ROUTE},
            reply=(
                "Prices are down a fair bit. "
                "Want me to look up current monitor prices?"
            ),
        )

        reply = engine.chat("look up current monitor prices")

        self.assertNotIn("want me to", reply.casefold())
        self.assertIsNone(
            engine.capability_offer.peek(),
            "she asked permission for something already requested",
        )

    def test_a_trailing_question_goes_and_the_answer_stays(self):
        engine = _engine(
            routes={"look up current monitor prices": self.ROUTE},
            # Deliberately no numbers: the grounded-value guard retracts an
            # unverified price and parks its own offer, which would be a
            # different mechanism answering this test.
            reply=(
                "Monitor prices move around a lot. "
                "Want me to look up current monitor prices?"
            ),
        )

        reply = engine.chat("look up current monitor prices")

        self.assertIn("move around a lot", reply)
        self.assertNotIn("want me to", reply.casefold())

    def test_a_question_that_is_the_whole_reply_is_replaced_not_emptied(self):
        # ClosingOfferGuard never touches a single sentence, because
        # deleting the only one leaves silence. Repeating the question is
        # not true either, so something true is said instead.
        engine = _engine(
            routes={"look up current monitor prices": self.ROUTE},
            reply="Want me to look up current monitor prices?",
        )

        reply = engine.chat("look up current monitor prices")

        self.assertTrue(reply.strip(), "the reply was emptied")
        self.assertFalse(reply.strip().endswith("?"), reply)

    def test_a_real_open_offer_keeps_its_question(self):
        # The guard must not eat an honest question. When something really
        # is waiting on the user, the question is the point.
        engine = _engine()
        engine.capability_offer.offer(
            capability_id="web_search",
            goal="monitor prices",
            offer_text="Want me to look them up?",
        )

        kept = engine._refuse_redundant_permission(
            "Want me to look them up?",
            decision=None,
            route=None,
            action_performed=True,
        )

        self.assertEqual(kept, "Want me to look them up?")


class CorrectionsBeatWhatIsPendingTests(unittest.TestCase):
    """Exit criterion 5, and dev rules 5 and 6."""

    def _with_an_offer(self):
        engine = _engine(
            routes={MUSING.casefold(): MUSING_ROUTE},
            reply=(
                "Monitors have gotten a lot cheaper lately. "
                "I can pull up a few current options if you want."
            ),
        )
        engine.chat(MUSING)
        self.assertIsNotNone(engine.capability_offer.peek())
        return engine

    def test_calling_it_off_drops_the_offer(self):
        engine = self._with_an_offer()

        engine.chat("never mind")

        self.assertIsNone(engine.capability_offer.peek())
        self.assertFalse(engine.action_ledger.supports_commitment("I'll check."))

    def test_a_new_errand_is_not_read_as_a_yes(self):
        engine = self._with_an_offer()

        engine.chat("open naver.com instead")

        pending = engine.capability_offer.peek()
        self.assertIsNone(
            pending,
            "an instruction of its own was consumed as consent",
        )

    def test_approval_of_the_subject_is_not_consent_to_act(self):
        engine = self._with_an_offer()

        engine.client.reply = "They really have."
        engine.chat("yeah they are getting expensive")

        self.assertIsNone(engine.capability_offer.peek())


class AStaleOfferCannotBeAnsweredTests(unittest.TestCase):
    """Exit criterion: stale state never overrides the current turn."""

    def test_an_expired_offer_is_gone_and_the_act_says_so(self):
        gate = CapabilityOfferGate(expiry_seconds=15)
        gate.offer(capability_id="web_search", goal="monitor", offer_text="x")
        ledger = ActionLedger(pending_offer=gate.peek)
        self.assertTrue(ledger.offer_pending)

        gate._pending = gate._pending.__class__(
            **{**gate._pending.__dict__, "expires_at": time.monotonic() - 1}
        )

        self.assertFalse(ledger.offer_pending)
        self.assertEqual(ledger.act.speech_act, ANSWER)

    def test_a_later_yes_cannot_revive_an_expired_offer(self):
        engine = _engine(
            routes={MUSING.casefold(): MUSING_ROUTE},
            reply=(
                "Monitors have gotten a lot cheaper lately. "
                "I can pull up a few current options if you want."
            ),
        )
        engine.chat(MUSING)
        pending = engine.capability_offer.peek()
        engine.capability_offer._pending = pending.__class__(
            **{**pending.__dict__, "expires_at": time.monotonic() - 1}
        )

        engine.client.reply = "Sure."
        engine.chat("yeah")

        self.assertFalse(engine.action_ledger.offer_pending)
        self.assertEqual(engine.action_ledger.goal, "")


class APromiseNeedsTheActionBehindItTests(unittest.TestCase):
    """The original defect, still fixed -- now by the record, not a bool."""

    LIVE = (
        "I can check prices directly through the browser. "
        "Let me open the website and find the current rates for you."
    )

    def test_a_promise_on_a_turn_that_ran_nothing_does_not_survive(self):
        engine = _engine()

        reply = engine._enforce_action_commitment(
            self.LIVE,
            user_input="check the price on the browser",
            action_performed=False,
        )

        self.assertNotIn("Let me open", reply)

    def test_a_promise_backed_by_the_ledger_is_left_alone(self):
        engine = _engine()
        engine.action_ledger.dispatching("browser_control")

        reply = engine._enforce_action_commitment(
            self.LIVE,
            user_input="check the price on the browser",
            action_performed=True,
        )

        self.assertEqual(reply, self.LIVE)

    def test_a_promise_to_open_survives_no_mere_lookup(self):
        engine = _engine()
        engine.action_ledger.dispatching("web_search")
        engine.action_ledger.settled()

        reply = engine._enforce_action_commitment(
            self.LIVE,
            user_input="check the price on the browser",
            action_performed=True,
        )

        self.assertNotIn("Let me open", reply)


class OnlyOneOfferReachesTheUserTests(unittest.TestCase):
    """The gate holds one thing; the reply may ask one question about it.

    Measured live on the monitor turn:

        Elaina: That sounds fun! Monitors can make a big difference.
                Would you like help finding specific models or prices?
                I don't want to send you somewhere I haven't checked,
                want me to look up real ones?

    Two questions, one answer, and only the second was real -- the entity
    guard had retracted two unverified brands and parked an offer to go and
    check. ``keep_offers`` waved the whole reply through because *an* offer
    was open, without asking whether it was *this* one.
    """

    OFFER = (
        "I don't want to send you somewhere I haven't checked, "
        "want me to look up real ones?"
    )
    REPLY = (
        "That sounds fun! Monitors can make a big difference. "
        "Would you like help finding specific models or prices? "
    ) + OFFER

    def _with_offer(self):
        engine = _engine()
        engine.capability_offer.offer(
            capability_id="web_search", goal="monitor", offer_text=self.OFFER,
        )
        return engine

    def test_the_parked_offer_survives_and_the_other_goes(self):
        engine = self._with_offer()

        kept = engine._one_offer_per_reply(self.REPLY)

        self.assertIn("want me to look up real ones?", kept)
        self.assertNotIn("Would you like help", kept)
        self.assertIn("Monitors can make a big difference", kept)

    def test_nothing_parked_means_nothing_is_dropped(self):
        engine = _engine()

        self.assertEqual(engine._one_offer_per_reply(self.REPLY), self.REPLY)

    def test_a_reply_that_lost_the_parked_offer_is_left_alone(self):
        # A rewrite replaced the offer sentence. Dropping the others would
        # leave the gate holding a question nobody was asked.
        engine = self._with_offer()
        rewritten = (
            "Monitors can make a big difference. "
            "Would you like help finding specific models or prices?"
        )

        self.assertEqual(engine._one_offer_per_reply(rewritten), rewritten)

    def test_an_answer_with_one_offer_is_untouched(self):
        engine = self._with_offer()
        single = f"Monitors have gotten cheaper. {self.OFFER}"

        self.assertEqual(engine._one_offer_per_reply(single), single)


class ASearchThatFoundThingsIsReportedTests(unittest.TestCase):
    """The mirror of the found-claim guard.

    That one stops her claiming a find she cannot name. This one stops her
    burying a find she can. Measured live:

        [Recommendation Reasoning] Candidates: 6 (0 fit, 5 unchecked)
        [Recommendation] Removed the model's own offer: 'Let me check some
                         options for you; Would you like me to search...'
        Elaina: I think you're looking for a monitor that fits your needs.

    Six results were in hand. The model offered to go and search on the
    turn whose search had already run, that offer was stripped -- correctly
    -- and what was left said nothing at all.
    """

    CANDIDATES = (
        'LG UltraGear 27GP850-B 27" QHD Gaming Monitor | Best Buy',
        "Dell S2722DGM - Reviews, Specs and Prices",
        "Samsung Odyssey G5 27-inch",
    )
    EMPTY = "I think you're looking for a monitor that fits your needs."

    def test_names_the_best_one_it_found(self):
        engine = _engine()

        reported = engine._report_what_was_found(
            self.EMPTY, candidates=self.CANDIDATES, searched=True,
        )

        self.assertIn("LG UltraGear", reported)

    def test_it_answers_rather_than_reading_the_results_back(self):
        # Measured live: "What came up was Best Gaming Monitors 2026:
        # Budget, Curved... and Gaming monitor." -- a receipt, appended to
        # a perfectly good recommendation. Someone who asked what to buy
        # wants the thing named, not the result set described.
        engine = _engine()

        reported = engine._report_what_was_found(
            self.EMPTY, candidates=self.CANDIDATES, searched=True,
        )

        self.assertNotIn("what came up", reported.casefold())
        self.assertNotIn("Samsung Odyssey", reported)
        self.assertNotIn("Dell S2722DGM", reported)

    def test_the_site_tail_is_not_read_out(self):
        engine = _engine()

        reported = engine._report_what_was_found(
            self.EMPTY, candidates=self.CANDIDATES, searched=True,
        )

        self.assertNotIn("Best Buy", reported)
        self.assertNotIn("Reviews, Specs", reported)

    def test_a_reply_that_already_names_one_is_left_alone(self):
        engine = _engine()
        named = "The Dell S2722DGM looks like the best of them."

        self.assertEqual(
            engine._report_what_was_found(
                named, candidates=self.CANDIDATES, searched=True,
            ),
            named,
        )

    def test_a_reply_naming_a_different_thing_is_left_alone(self):
        # She named a particular monitor. Whether it is the right one is
        # the grounding guards' question; adding a second product under
        # the first is the "unnecessary last sentence" this was reported
        # for. Measured live, after a perfectly good recommendation:
        # "What came up was Best Gaming Monitors 2026: Budget, Curved...
        # and Gaming monitor."
        engine = _engine()
        named = "The Acer SpatialLabs View 27 is a great pick for gaming."

        self.assertEqual(
            engine._report_what_was_found(
                named, candidates=self.CANDIDATES, searched=True,
            ),
            named,
        )

    def test_no_search_means_no_report(self):
        engine = _engine()

        self.assertEqual(
            engine._report_what_was_found(
                self.EMPTY, candidates=self.CANDIDATES, searched=False,
            ),
            self.EMPTY,
        )

    def test_a_search_with_nothing_to_name_says_nothing(self):
        engine = _engine()

        self.assertEqual(
            engine._report_what_was_found(
                self.EMPTY, candidates=(), searched=True,
            ),
            self.EMPTY,
        )

    def test_an_emptied_reply_becomes_the_report_itself(self):
        engine = _engine()

        reported = engine._report_what_was_found(
            "", candidates=self.CANDIDATES, searched=True,
        )

        self.assertTrue(reported.strip())
        self.assertIn("LG UltraGear", reported)

    def test_the_list_is_said_the_way_a_person_says_one(self):
        engine = _engine()

        self.assertEqual(engine._spoken_list(["a"]), "a")
        self.assertEqual(engine._spoken_list(["a", "b"]), "a and b")
        self.assertEqual(engine._spoken_list(["a", "b", "c"]), "a, b and c")


class TheTwoOfferReadersAgreeTests(unittest.TestCase):
    """A sentence too weak to ground and strong enough to strip is deleted.

    Two classifiers, two files, one concept. ``ClosingOfferGuard`` decides
    what to strip; ``speech_act_of`` decides what is worth grounding. Where
    they disagreed, the disagreement always cost the user a sentence --
    measured live, twice in two turns:

        [Recommendation] Removed the model's own offer: 'I can help you
                         check out some local shops in South Korea that
                         sell monitors.'
        [Recommendation] Removed the model's own offer: 'Let me know if you
                         want help narrowing down options or finding local
                         shops in So'

    Neither was filler. Both named a real thing she would do.
    """

    ACTION_OFFERS = (
        "I can help you check out some local shops that sell monitors.",
        "I can help you find a good one.",
        "Let me know if you want help narrowing down options.",
        "Want me to pull it up?",
        "Would you like help finding specific models?",
    )

    def test_what_one_strips_the_other_calls_an_offer(self):
        for sentence in self.ACTION_OFFERS:
            with self.subTest(sentence=sentence):
                self.assertTrue(
                    ClosingOfferGuard.offers_to_act(sentence),
                    "the strip guard does not see an offer here",
                )
                self.assertEqual(
                    speech_act_of(sentence), OFFER,
                    "so the grounding pass never gets a chance at it",
                )

    def test_a_referent_makes_it_content_rather_than_filler(self):
        self.assertNotIn(
            "Let me know if you want help narrowing down options.",
            ClosingOfferGuard.strip(
                "Curved screens suit long sessions. "
                "Let me know if you want help narrowing down options.",
                keep_offers=True,
            ).replace(
                "Curved screens suit long sessions. "
                "Let me know if you want help narrowing down options.", "",
            ),
        )

    def test_the_offer_with_a_referent_survives_when_one_is_open(self):
        kept = ClosingOfferGuard.strip(
            "Curved screens suit long sessions. "
            "Let me know if you want help narrowing down options.",
            keep_offers=True,
        )

        self.assertIn("narrowing down options", kept)

    def test_the_same_sentence_without_one_is_still_filler(self):
        for filler in (
            "Let me know if you need help.",
            "Let me know if you want help with anything.",
            "Let me know if you need anything else.",
        ):
            with self.subTest(filler=filler):
                kept = ClosingOfferGuard.strip(
                    f"Curved screens suit long sessions. {filler}",
                    keep_offers=True,
                )
                self.assertNotIn("Let me know", kept)


class AnInvitationNeedsNothingParkedTests(unittest.TestCase):
    """The rationing may refuse the parking without deleting the sentence.

    "Want me to?" with nothing parked is the failure this phase exists for:
    the user's yes lands on nothing. "Let me know if you want help
    narrowing down options" leaves nothing pending at all -- there is no
    question waiting on an answer, so there is nothing to be dishonest
    about. Deleting it took the only useful sentence out of the reply.
    """

    ROUTE = {
        "intent": "conversation", "confidence": 0.95,
        "normalized_request": "not sure yet",
        "topic": "monitor purchase consideration",
        "speech_act": "statement", "request_explicitness": "statement",
        "reason": "The user is unsure about specifications.",
    }

    def _cooled_down(self, reply):
        engine = _engine(
            routes={"i'm not really sure yet.": self.ROUTE}, reply=reply,
        )
        # She offered one turn ago; the gap has not passed.
        engine.recommendations._turn = 2
        engine.recommendations._last_offer_turn = 1
        return engine

    def test_an_invitation_survives_the_cooldown(self):
        engine = self._cooled_down(
            "That makes sense, it's easy to feel overwhelmed. "
            "Let me know if you want help narrowing down options."
        )

        reply = engine.chat("I'm not really sure yet.")

        self.assertIn("narrowing down options", reply)
        self.assertIsNone(
            engine.capability_offer.peek(),
            "an invitation must not park a question nobody asked",
        )

    def test_a_question_does_not(self):
        engine = self._cooled_down("That makes sense. Want me to look some up?")

        reply = engine.chat("I'm not really sure yet.")

        self.assertNotIn("want me to", reply.casefold())


class BlessingIsNotProposingTests(unittest.TestCase):
    """The mode gate belongs to raising an offer, not to keeping one.

    ``mode == recommend`` is the strong signal needed before she raises
    something nobody asked about. It is the wrong question for a sentence
    already written into a natural reply: that judgement was made when the
    words were chosen, and only the rationing is left.
    """

    def test_rationing_is_asked_without_the_mode(self):
        policy = RecommendationPolicy()
        policy.begin_turn()

        self.assertTrue(policy.rationing_allows("web_search"))
        self.assertFalse(
            policy.should_offer(_Stub(mode="answer"), "web_search"),
        )

    def test_her_own_words_are_claimable_on_an_answer_turn(self):
        policy = RecommendationPolicy()
        policy.begin_turn()

        self.assertTrue(policy.claim_her_own("web_search"))

    def test_and_still_rationed(self):
        policy = RecommendationPolicy()
        policy.begin_turn()
        policy.claim_her_own("web_search")

        self.assertFalse(policy.claim_her_own("web_search"))

    def test_a_refusal_still_costs_her_the_right_to_ask(self):
        policy = RecommendationPolicy()
        policy.begin_turn()
        policy.note_declined()

        self.assertFalse(policy.claim_her_own("web_search"))


if __name__ == "__main__":
    unittest.main()
