"""What happens to an offer between making it and forgetting it.

The Phase 4F.1 acceptance suite. The mechanism -- offer, accept, resume,
act -- is proved elsewhere; this is the other half, the half that decides
whether the mechanism is *safe*. Every case here is a way for a promise or
a consent to go wrong, and each one is a property rather than a story:

    a valid offer            she may say it, and it leaves something real
    an accepted offer        runs the stored goal, and asks nothing again
    a declined offer         clears, and costs her the right to re-ask
    a corrected offer        the new subject wins; the old one cannot return
    an explicit command      executes; no permission is asked for it
    an ambiguous reply       authorises nothing
    an unrelated turn        is answered on its own terms
    stale consent            cannot reach an offer that has lapsed
    a failed tool            is not reported as a result
    a second offer           does not survive beside a real one

Whole turns through ``ChatEngine.chat()``, with a scripted model and tied
hands: "the guard behaves" and "she behaves" have been different things
before, and every one of these is about what actually reaches the person.
"""

from __future__ import annotations

import time
import unittest

from tests.turn_harness import build_engine, machine_actions


MUSING = "I'm thinking about getting a new monitor."

MUSING_ROUTE = {
    "intent": "conversation",
    "confidence": 0.95,
    "normalized_request": "thinking about getting a new monitor",
    "topic": "monitor",
    "speech_act": "statement",
    "request_explicitness": "statement",
    "reason": "The user is musing about a monitor.",
}

COMMAND = "Can you check current monitors?"

COMMAND_ROUTE = {
    "intent": "web_search",
    "confidence": 0.95,
    "normalized_request": "check current monitors",
    "topic": "monitor",
    "speech_act": "action_request",
    "action_requested": True,
    "requires_external_evidence": True,
    "information_freshness": "current",
    "reason": "The user asked for a lookup outright.",
}

OFFER_REPLY = (
    "Yeah, a monitor upgrade could be worth it. "
    "Want me to pull up a few current ones?"
)


def _engine(routes=None, *, failing=False):
    """A whole engine whose search ability is on and whose hands are tied.

    The harness disables the search *section* so nothing reaches the
    network. The capability switch is a different thing -- it decides
    whether an offer to search can honestly be made -- so it is turned on
    and the one door that reopens is tied again here.
    """
    engine = build_engine(
        routes={MUSING.casefold(): MUSING_ROUTE, **(routes or {})},
    )
    engine._web_search_enabled = True
    engine.searched = []

    def _offline(query, max_results=5):
        engine.searched.append(str(query))
        if failing:
            raise RuntimeError("the search backend is down")
        return "LG UltraGear 27GP850-B, 165Hz."

    engine.research_agent._search = _offline
    return engine


def _offered(routes=None):
    """An engine that has just made a real, parked offer."""
    engine = _engine(routes)
    engine.client.reply = OFFER_REPLY
    engine.chat(MUSING)
    return engine


class AValidOfferTests(unittest.TestCase):
    """She may say it, and it has to leave something real behind."""

    def test_the_offer_survives_and_is_answerable(self):
        engine = _offered()

        pending = engine.capability_offer.peek()

        self.assertIsNotNone(pending, "she offered and parked nothing")
        self.assertEqual(pending.capability_id, "web_search")

    def test_nothing_ran_on_the_offering_turn(self):
        engine = _offered()

        self.assertEqual(engine.searched, [])
        self.assertEqual(machine_actions(engine), [])

    def test_one_actionable_offer_at_most(self):
        # Measured live: "Let me know if you'd like help finding something
        # specific!" and "want me to look up real ones?" in one reply. Two
        # calls to action is one too many.
        engine = _engine()
        engine.client.reply = (
            "Yeah, a monitor upgrade could be worth it. "
            "Let me know if you'd like help finding something specific! "
            "Want me to pull up a few current ones?"
        )

        reply = engine.chat(MUSING)

        self.assertEqual(reply.count("?"), 1, reply)
        self.assertNotIn("let me know", reply.casefold())
        self.assertIn("pull up a few current ones", reply)


class AnAcceptedOfferTests(unittest.TestCase):
    """Yes runs the stored goal, and nothing is asked twice."""

    def test_the_stored_goal_runs(self):
        engine = _offered()
        engine.client.reply = "Here's a good one."

        engine.chat("Yeah.")

        self.assertTrue(engine.searched, "the accepted offer ran nothing")
        self.assertIn("monitor", " ".join(engine.searched).casefold())

    def test_permission_is_not_asked_again(self):
        engine = _offered()
        engine.client.reply = "Here's a good one."

        reply = engine.chat("Yeah.")

        self.assertNotIn("want me to", reply.casefold())
        self.assertIsNone(engine.capability_offer.peek())

    def test_the_record_says_an_action_ran(self):
        engine = _offered()
        engine.client.reply = "Here's a good one."

        engine.chat("Yeah.")

        act = engine.action_ledger.act
        self.assertEqual(act.speech_act, "commitment")
        self.assertIn(act.action_state, {"dispatching", "executed"})


class ADeclinedOfferTests(unittest.TestCase):
    """No clears it, and costs her the right to re-ask for a while."""

    def test_nothing_runs(self):
        engine = _offered()
        engine.client.reply = "No problem."

        engine.chat("No thanks.")

        self.assertEqual(engine.searched, [])
        self.assertEqual(machine_actions(engine), [])

    def test_the_offer_is_gone(self):
        engine = _offered()
        engine.client.reply = "No problem."

        engine.chat("No thanks.")

        self.assertIsNone(engine.capability_offer.peek())

    def test_calling_it_off_is_recorded_as_called_off(self):
        engine = _offered()
        engine.client.reply = "Okay."

        engine.chat("Actually never mind.")

        self.assertEqual(engine.action_ledger.state, "cancelled")
        self.assertFalse(
            engine.action_ledger.supports_commitment("I'll check."),
        )

    def test_nothing_stale_fires_on_the_next_turn(self):
        engine = _offered()
        engine.client.reply = "Okay."
        engine.chat("Actually never mind.")

        engine.client.reply = "Sure."
        engine.chat("So anyway.")

        self.assertEqual(engine.searched, [])
        self.assertIsNone(engine.capability_offer.peek())


class ACorrectedOfferTests(unittest.TestCase):
    """The current turn beats what is pending. Always."""

    KEYBOARDS = "Actually let's look at keyboards instead."
    ROUTE = {
        "intent": "conversation", "confidence": 0.95,
        "normalized_request": "look at keyboards instead",
        "topic": "keyboard", "speech_act": "statement",
        "request_explicitness": "statement",
        "reason": "The user changed the subject.",
    }

    def _corrected(self):
        engine = _offered({self.KEYBOARDS.casefold(): self.ROUTE})
        engine.client.reply = "Keyboards are fun too."
        engine.chat(self.KEYBOARDS)
        return engine

    def test_the_monitor_offer_is_invalidated(self):
        engine = self._corrected()

        self.assertIsNone(engine.capability_offer.peek())

    def test_nothing_ran_for_the_old_subject(self):
        engine = self._corrected()

        self.assertEqual(engine.searched, [])

    def test_the_new_subject_is_what_is_held(self):
        engine = self._corrected()
        problem = engine.task_sessions.active_recommendation()

        self.assertIsNotNone(problem)
        self.assertIn("keyboard", problem.subject.casefold())

    def test_the_old_task_cannot_resume_later(self):
        engine = self._corrected()

        engine.client.reply = "Sure."
        engine.chat("Yeah.")

        self.assertNotIn("monitor", " ".join(engine.searched).casefold())


class AnExplicitCommandTests(unittest.TestCase):
    """What was asked for outright is not asked about again."""

    def _commanded(self, reply):
        engine = _engine({COMMAND.casefold(): COMMAND_ROUTE})
        engine.client.reply = reply
        return engine, engine.chat(COMMAND)

    def test_it_executes(self):
        engine, _reply = self._commanded("The LG UltraGear is a good one.")

        self.assertTrue(engine.searched, "an outright request ran nothing")

    def test_no_permission_is_asked(self):
        engine, reply = self._commanded(
            "Want me to search for current monitors?",
        )

        self.assertNotIn("want me to", reply.casefold())
        self.assertIsNone(
            engine.capability_offer.peek(),
            "she asked permission for something already requested",
        )

    def test_the_reply_is_never_emptied_to_achieve_that(self):
        _engine_, reply = self._commanded(
            "Want me to search for current monitors?",
        )

        self.assertTrue(reply.strip())
        self.assertFalse(reply.strip().endswith("?"))


class AnAmbiguousAcknowledgementTests(unittest.TestCase):
    """"Maybe" is not a yes, and the cost of guessing wrong is a tool run."""

    def _answered(self, said):
        engine = _offered()
        engine.client.reply = "No rush."
        return engine, engine.chat(said)

    def test_maybe_authorises_nothing(self):
        engine, _reply = self._answered("Maybe.")

        self.assertEqual(engine.searched, [])
        self.assertEqual(machine_actions(engine), [])

    def test_approval_of_the_subject_is_not_consent_to_act(self):
        engine, _reply = self._answered("Yeah, monitors are expensive lately.")

        self.assertEqual(engine.searched, [])

    def test_an_unclear_reply_leaves_no_offer_hanging(self):
        # She raised it herself, so anything short of a clear yes drops it
        # rather than sitting there waiting to catch a later word.
        engine, _reply = self._answered("Maybe.")

        self.assertIsNone(engine.capability_offer.peek())


class AnUnrelatedNextTurnTests(unittest.TestCase):
    """A question is not an answer to a question."""

    ASIDE = "By the way, what does OLED mean?"
    ROUTE = {
        "intent": "knowledge_question", "confidence": 0.95,
        "normalized_request": "what does OLED mean",
        "topic": "OLED", "speech_act": "information_request",
        "reason": "The user asked what a term means.",
    }

    def _aside(self):
        engine = _offered({self.ASIDE.casefold(): self.ROUTE})
        engine.client.reply = "OLED means organic light-emitting diode."
        return engine, engine.chat(self.ASIDE)

    def test_the_new_question_is_what_gets_answered(self):
        _engine_, reply = self._aside()

        self.assertIn("organic light-emitting diode", reply)

    def test_the_old_offer_does_not_resume(self):
        engine, _reply = self._aside()

        self.assertNotIn("monitor", " ".join(engine.searched).casefold())

    def test_the_offer_is_not_answered_by_a_question(self):
        # Deliberate: an unanswered offer stays answerable for its expiry
        # rather than being consumed by a turn that was not a reply to it.
        # What matters is that it did not override this turn.
        engine, reply = self._aside()

        self.assertNotIn("want me to", reply.casefold())


class StaleConsentTests(unittest.TestCase):
    """Consent belongs to the offer that was actually on the table."""

    def _expire(self, engine):
        pending = engine.capability_offer.peek()
        engine.capability_offer._pending = pending.__class__(
            **{**pending.__dict__, "expires_at": time.monotonic() - 1}
        )

    def test_a_lapsed_offer_cannot_be_accepted(self):
        engine = _offered()
        self._expire(engine)
        engine.client.reply = "Sure thing."

        engine.chat("Yeah.")

        self.assertEqual(engine.searched, [])
        self.assertFalse(engine.action_ledger.offer_pending)

    def test_an_acknowledgement_before_any_offer_authorises_nothing(self):
        engine = _engine()
        engine.client.reply = "Mm-hm."

        engine.chat("Yeah.")

        self.assertEqual(engine.searched, [])
        self.assertEqual(machine_actions(engine), [])

    def test_a_cleared_offer_cannot_be_revived_by_a_later_yes(self):
        engine = _offered()
        engine.client.reply = "Okay."
        engine.chat("Actually never mind.")

        engine.client.reply = "Sure."
        engine.chat("Yeah.")

        self.assertEqual(engine.searched, [])


class AFailedToolTests(unittest.TestCase):
    """A commitment says she went and looked, never that she found it."""

    def _failed(self, reply):
        engine = _engine({COMMAND.casefold(): COMMAND_ROUTE}, failing=True)
        engine.client.reply = reply
        return engine, engine.chat(COMMAND)

    def test_the_record_says_it_failed(self):
        engine, _reply = self._failed("I couldn't get that.")

        self.assertEqual(engine.action_ledger.state, "failed")

    def test_a_failed_action_backs_no_commitment(self):
        engine, _reply = self._failed("I couldn't get that.")

        self.assertFalse(
            engine.action_ledger.supports_commitment("I'll check."),
            "a failed lookup still licensed a promise",
        )

    def test_the_invented_result_does_not_survive(self):
        _engine_, reply = self._failed(
            "I found the LG UltraGear 27GP850-B at 144Hz for 350,000 won.",
        )

        self.assertNotIn("144Hz", reply)
        self.assertNotIn("350,000", reply)

    def test_the_failure_is_not_silence(self):
        _engine_, reply = self._failed(
            "I found the LG UltraGear 27GP850-B at 144Hz.",
        )

        self.assertTrue(reply.strip(), "a failed search said nothing at all")


class DuplicateOfferSuppressionTests(unittest.TestCase):
    """The gate holds one thing; the reply makes one call to action."""

    GENERIC = (
        "Let me know if you'd like help finding something specific!",
        "I can help with that if you'd like.",
        "I can look it up for you.",
        "Want me to have a look?",
    )

    def test_a_generic_offer_does_not_survive_beside_a_real_one(self):
        real = (
            "I don't want to send you somewhere I haven't checked, "
            "want me to look up real ones?"
        )
        for extra in self.GENERIC:
            with self.subTest(extra=extra):
                engine = _engine()
                engine.capability_offer.offer(
                    capability_id="web_search", goal="monitor",
                    offer_text=real,
                )

                kept = engine._one_offer_per_reply(
                    f"Monitors have gotten cheaper. {extra} {real}"
                )

                self.assertIn("look up real ones", kept)
                self.assertNotIn(extra, kept)

    def test_the_answer_itself_is_never_dropped(self):
        real = "Want me to look up real ones?"
        engine = _engine()
        engine.capability_offer.offer(
            capability_id="web_search", goal="monitor", offer_text=real,
        )

        kept = engine._one_offer_per_reply(
            f"Monitors have gotten cheaper lately. I can look it up. {real}"
        )

        self.assertIn("Monitors have gotten cheaper lately.", kept)


if __name__ == "__main__":
    unittest.main()
