"""What happens to an offer between making it and acting on it.

An offer is the one place where the thing Elaina will execute is *not* the
sentence the person just said. "Yeah" has to become "find restaurants
nearby", or the whole mechanism is theatre. These are the properties that
make that safe, each one stated as its own test:

* accepting restores the stored, concrete goal -- never the word "yeah";
* rejecting cancels it, and costs her the right to re-ask for a while;
* an ambiguous reply executes nothing;
* a suggestion she raised herself may not consume the next thing said;
* an expired offer is not answerable;
* she does not ask the same thing twice in a row.

Offline on purpose: none of it needs a model, so none of it may quietly stop
being true between live runs.
"""

import time
import unittest

from brain.deliberation.interaction import RECOMMEND, InteractionDecision
from brain.recommendation import (
    RecommendationPolicy,
    reads_as_clear_acceptance,
)
from security.capability_offer import CapabilityOfferGate


GOAL = "find restaurants nearby"


def _offered(gate: CapabilityOfferGate, **kwargs):
    return gate.offer(
        capability_id=kwargs.pop("capability_id", "web_search"),
        goal=kwargs.pop("goal", GOAL),
        offer_text="Want me to find restaurants nearby?",
        **kwargs,
    )


class AcceptedOfferTests(unittest.TestCase):
    """The executable task must be the goal, not the acknowledgement."""

    def test_the_stored_goal_survives_a_bare_yes(self):
        gate = CapabilityOfferGate()
        _offered(gate)

        pending = gate.peek()

        self.assertEqual(pending.goal, GOAL)
        self.assertNotIn("yeah", pending.goal.casefold())
        self.assertIn(GOAL, pending.request)

    def test_every_bare_acceptance_form_reads_as_a_yes(self):
        for said in (
            "yes", "yeah", "sure", "okay", "ok", "yep", "please do",
            "go ahead", "do it", "why not", "yes please", "go for it",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_clear_acceptance(said))

    def test_subject_approval_does_not_pass_the_proactive_gate(self):
        """"Sounds good" is held back here, and that is deliberate.

        There are two acceptance paths, and this function is the strict one.
        It guards a suggestion *Elaina raised herself*, where the person was
        not asked a question and owes no answer -- so approval of the topic
        must not be mistaken for permission. Measured live, "yeah they are
        getting expensive" was read as accepting a monitor search.

        A direct "Want me to X?" is not gated by this at all: it goes to
        SemanticConsentClassifier, which judges the reply against the
        pending offer in context, and there "sounds good" does accept. That
        asymmetry is the design, not an oversight -- see the live consent
        section of scripts/live_agency_check.py, which covers the other path.
        """
        for said in (
            "sounds good",
            "that sounds good",
            "yeah they are getting expensive",
            "sure, they look nice",
            "that looks nice",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said))

    def test_a_task_payload_is_carried_for_resumption(self):
        # Acceptance resumes the original task rather than routing the
        # generated offer sentence as if it were a fresh request.
        gate = CapabilityOfferGate()
        _offered(gate, task_id="abc123", task_query="ramen near the U District")

        pending = gate.peek()

        self.assertEqual(pending.task_id, "abc123")
        self.assertEqual(pending.task_query, "ramen near the U District")


class RejectedOfferTests(unittest.TestCase):

    def test_rejection_forms_are_not_acceptance(self):
        for said in ("no", "nah", "not now", "never mind", "no thanks",
                     "nope", "don't", "maybe later"):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said))

    def test_clearing_the_gate_leaves_nothing_to_execute(self):
        gate = CapabilityOfferGate()
        _offered(gate)

        gate.clear()

        self.assertIsNone(gate.peek())

    def test_a_refusal_buys_silence(self):
        # A refusal is information, and re-asking spends it.
        policy = RecommendationPolicy()
        decision = InteractionDecision(mode=RECOMMEND, need="fresh_information")
        policy.begin_turn()
        self.assertTrue(policy.should_offer(decision, "web_search"))

        policy.note_declined()
        policy.begin_turn()

        self.assertFalse(policy.should_offer(decision, "web_search"))


class AmbiguousReplyTests(unittest.TestCase):
    """Nothing runs on a maybe."""

    def test_an_ambiguous_reply_is_never_a_yes(self):
        for said in ("maybe", "I don't know", "depends", "hmm", "not sure",
                     "I guess", "we'll see", "possibly"):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said))

    def test_a_continuing_remark_is_not_a_yes(self):
        # Opening with an affirmative does not make a remark into consent.
        for said in (
            "yeah I've been meaning to",
            "yeah, they are pretty expensive these days",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_clear_acceptance(said))


class ProactiveOfferTests(unittest.TestCase):
    """A suggestion is not a question, and owes no answer."""

    def test_a_proactive_offer_is_marked_as_one(self):
        gate = CapabilityOfferGate()
        _offered(gate, proactive=True)

        self.assertTrue(gate.peek().proactive)

    def test_an_asked_for_offer_is_not_proactive(self):
        gate = CapabilityOfferGate()
        _offered(gate)

        self.assertFalse(gate.peek().proactive)


class ExpiryTests(unittest.TestCase):

    def test_a_stale_offer_cannot_be_answered(self):
        # A question left sitting is no longer what the person is replying to.
        gate = CapabilityOfferGate(expiry_seconds=15)
        _offered(gate)
        gate._pending = gate._pending.__class__(
            **{**gate._pending.__dict__, "expires_at": time.monotonic() - 1}
        )

        self.assertIsNone(gate.peek())

    def test_expiry_has_a_floor(self):
        self.assertGreaterEqual(CapabilityOfferGate(expiry_seconds=1).expiry_seconds, 15)


class OfferSpamTests(unittest.TestCase):
    """The same suggestion twice in a row is nagging, not helping."""

    def test_the_same_capability_is_not_offered_back_to_back(self):
        policy = RecommendationPolicy()
        decision = InteractionDecision(mode=RECOMMEND, need="fresh_information")
        policy.begin_turn()
        self.assertTrue(policy.should_offer(decision, "web_search"))
        policy.offer(decision, capability_id="web_search", capability_name="a search")

        policy.begin_turn()

        self.assertFalse(policy.should_offer(decision, "web_search"))

    def test_nothing_is_offered_when_the_turn_is_not_a_recommendation(self):
        policy = RecommendationPolicy()
        policy.begin_turn()

        for mode in ("answer", "execute", "ask_permission", "clarify"):
            with self.subTest(mode=mode):
                decision = InteractionDecision(mode=mode, need="none")
                self.assertFalse(policy.should_offer(decision, "web_search"))

    def test_an_offer_needs_a_capability_to_offer(self):
        policy = RecommendationPolicy()
        policy.begin_turn()
        decision = InteractionDecision(mode=RECOMMEND, need="fresh_information")

        self.assertFalse(policy.should_offer(decision, ""))


if __name__ == "__main__":
    unittest.main()
