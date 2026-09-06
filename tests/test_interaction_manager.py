"""What should happen about this request, decided once and said out loud.

The Phase 4F.2 acceptance suite. 4E.2 built the layer and 4F.2 finishes the
job it was left half-doing: a decision that could tell "reply from what she
knows" apart from "work on what we already found", that carries what the
person is actually after rather than the label a classifier filed it under,
and that states -- in one place -- whether this turn replaces what was
outstanding.

That last one is the reason this phase exists. "Does the current turn beat
the pending one" had five separate answers living in five separate modules,
and over five rounds of live testing each of them was wrong about a
different case. Consolidating the *conclusion* does not make any of them
smarter; it makes the disagreement visible.

Every case here is offline. ``decide`` makes no model call, which is what
makes it safe to consult as often as a turn likes -- and what makes this
suite fast enough to run on every change.
"""

from __future__ import annotations

import unittest

from brain.deliberation import goal_intent, supersession
from brain.deliberation.interaction import (
    ANSWER,
    ASK_PERMISSION,
    CLARIFY,
    CONTINUE,
    EXECUTE,
    MODES,
    NEED_FRESH,
    NEED_MACHINE,
    NEED_NONE,
    NEED_RECALLED,
    RECOMMEND,
    decide,
)
from brain.intent_router import IntentDecision
from tests.turn_harness import build_engine


def _route(intent: str, **fields):
    return IntentDecision(
        intent=intent,
        confidence=float(fields.pop("confidence", 0.95)),
        normalized_request=fields.pop("normalized_request", "something"),
        reason="",
        **fields,
    )


class TheLayerCanChooseAmongAllOfThemTests(unittest.TestCase):
    """Seven jobs, seven answers. The brief's list, in this vocabulary."""

    def test_every_mode_is_reachable(self):
        reached = {
            # answer -- she knows it
            decide(_route("knowledge_question")).mode,
            # continue -- this session already found it
            decide(
                _route("web_search", is_follow_up=True,
                       requires_external_evidence=True),
                has_usable_context=True,
            ).mode,
            # execute -- asked for outright, needs a lookup
            decide(
                _route("web_search", action_requested=True,
                       requires_external_evidence=True),
            ).mode,
            # recommend -- would help, nobody asked
            decide(
                _route("computer_action", action_requested=False),
            ).mode,
            # ask_permission -- it changes something
            decide(
                _route("computer_action", action_requested=True,
                       computer_operation="delete_folder"),
            ).mode,
            # clarify -- a question is outstanding
            decide(_route("clarification")).mode,
        }

        self.assertEqual(
            reached,
            {ANSWER, CONTINUE, EXECUTE, RECOMMEND, ASK_PERMISSION, CLARIFY},
        )

    def test_the_vocabulary_is_declared(self):
        for mode in (
            ANSWER, CONTINUE, EXECUTE, RECOMMEND, ASK_PERMISSION, CLARIFY,
        ):
            with self.subTest(mode=mode):
                self.assertIn(mode, MODES)


class DirectConversationTests(unittest.TestCase):
    """She knows it. Nothing runs, nothing is asked."""

    def test_a_plain_question_is_answered(self):
        decision = decide(_route("knowledge_question"))

        self.assertEqual(decision.mode, ANSWER)
        self.assertEqual(decision.need, NEED_NONE)
        self.assertTrue(decision.can_answer_directly)
        self.assertFalse(decision.acts)
        self.assertFalse(decision.permission_required)

    def test_answering_proposes_no_action(self):
        self.assertEqual(decide(_route("knowledge_question")).proposed_action, "")


class CurrentInformationTests(unittest.TestCase):
    """A fact that moves is looked up, and looking costs no permission."""

    def test_a_request_for_something_current_executes(self):
        decision = decide(
            _route("web_search", action_requested=True,
                   requires_external_evidence=True),
        )

        self.assertEqual(decision.mode, EXECUTE)
        self.assertEqual(decision.need, NEED_FRESH)
        self.assertTrue(decision.acts)

    def test_it_proposes_the_lookup(self):
        decision = decide(
            _route("web_search", action_requested=True,
                   requires_external_evidence=True),
        )

        self.assertEqual(decision.proposed_action, "web_search")

    def test_a_remark_is_not_a_request_for_one(self):
        # The 4E rule this whole phase is built on: musing is not asking.
        decision = decide(
            _route("web_search", action_requested=False,
                   requires_external_evidence=True,
                   request_explicitness="statement"),
        )

        self.assertEqual(decision.mode, RECOMMEND)
        self.assertFalse(decision.acts)


class FollowUpTests(unittest.TestCase):
    """The turn works on a result set that already exists."""

    def _followed_up(self, said="anything cheaper"):
        return decide(
            _route("web_search", is_follow_up=True,
                   requires_external_evidence=True,
                   normalized_request=said),
            has_usable_context=True,
        )

    def test_it_continues_rather_than_answering(self):
        # The distinction 4F.2 adds. Both touch nothing; only one of them
        # is about a result set, and a consumer has to be able to tell.
        decision = self._followed_up()

        self.assertEqual(decision.mode, CONTINUE)
        self.assertTrue(decision.continues)
        self.assertEqual(decision.need, NEED_RECALLED)

    def test_it_does_not_run_the_tool_again(self):
        decision = self._followed_up()

        self.assertFalse(decision.acts)
        self.assertTrue(decision.reuses_existing_results)
        self.assertEqual(decision.proposed_action, "recall")

    def test_every_shape_of_follow_up_continues(self):
        for said in (
            "anything cheaper",
            "which one would you choose",
            "maybe somewhere quieter",
            "is it actually good",
            "what about the second one",
        ):
            with self.subTest(said=said):
                self.assertEqual(self._followed_up(said).mode, CONTINUE)

    def test_without_a_result_set_there_is_nothing_to_continue(self):
        decision = decide(
            _route("web_search", is_follow_up=True,
                   requires_external_evidence=True),
            has_usable_context=False,
        )

        self.assertNotEqual(decision.mode, CONTINUE)


class ActionRequestTests(unittest.TestCase):
    """Something has to be done, and how much friction it meets."""

    def test_an_outright_request_executes(self):
        decision = decide(
            _route("computer_action", action_requested=True,
                   computer_operation="open_url"),
        )

        self.assertEqual(decision.mode, EXECUTE)
        self.assertEqual(decision.need, NEED_MACHINE)
        self.assertFalse(decision.permission_required)

    def test_something_destructive_is_agreed_first(self):
        decision = decide(
            _route("computer_action", action_requested=True,
                   computer_operation="delete_folder"),
        )

        self.assertEqual(decision.mode, ASK_PERMISSION)
        self.assertTrue(decision.permission_required)
        self.assertFalse(decision.acts)

    def test_an_action_nobody_asked_for_is_offered(self):
        decision = decide(_route("computer_action", action_requested=False))

        self.assertEqual(decision.mode, RECOMMEND)
        self.assertTrue(decision.permission_required)


class TheDecisionIsMachineReadableTests(unittest.TestCase):
    """Structured, not prose. A test reads it the way a log does."""

    def test_it_reads_as_a_record(self):
        decision = decide(
            _route("web_search", action_requested=True,
                   requires_external_evidence=True,
                   normalized_request="find a good monitor for coding"),
        )

        record = decision.as_dict()

        self.assertEqual(record["interaction_mode"], EXECUTE)
        self.assertEqual(record["user_goal"], "find a good monitor for coding")
        self.assertEqual(record["proposed_action"], "web_search")
        self.assertFalse(record["permission_required"])
        self.assertEqual(record["missing_information"], [])

    def test_the_goal_is_theirs_not_the_classifiers_label(self):
        # "monitor purchase consideration" is how a classifier files a
        # topic. It is not what anyone is trying to do, and reading it back
        # as the goal is how a filing label reached a search box.
        decision = decide(
            _route("conversation", topic="monitor purchase consideration",
                   normalized_request="thinking about getting a new monitor"),
        )

        self.assertEqual(
            decision.user_goal, "thinking about getting a new monitor",
        )

    def test_the_log_block_names_the_new_facts(self):
        block = decide(
            _route("web_search", action_requested=True,
                   requires_external_evidence=True),
        ).log_block()

        self.assertIn("Goal:", block)
        self.assertIn("Decision:", block)
        self.assertIn("Missing:", block)


class CorrectionsBeatStaleStateTests(unittest.TestCase):
    """One reading of it, with a reason, instead of five opinions."""

    def test_a_correction_supersedes_and_says_what_it_meant(self):
        read = supersession.read("Actually let's look at keyboards instead.")

        self.assertTrue(read)
        self.assertEqual(read.reason, supersession.CORRECTION)
        self.assertIn("keyboard", read.corrected_subject.casefold())

    def test_calling_it_off_supersedes_without_a_subject(self):
        # "Actually never mind" satisfies the correction patterns too, and
        # taking "never mind" as the corrected subject is how a
        # cancellation became a thing to search for.
        read = supersession.read("Actually never mind.")

        self.assertTrue(read)
        self.assertEqual(read.reason, supersession.CANCELLED)
        self.assertEqual(read.corrected_subject, "")

    def test_naming_an_errand_supersedes(self):
        read = supersession.read("open naver.com instead")

        self.assertTrue(read)
        self.assertEqual(read.reason, supersession.ERRAND)

    def test_a_follow_up_does_not_supersede_what_it_asks_about(self):
        # The trap: reading "is this a question?" as supersession made
        # "Anything cheaper?" replace the very result set it referred to.
        for said in (
            "Anything cheaper?",
            "Which one would you choose?",
            "Is it actually good?",
        ):
            with self.subTest(said=said):
                self.assertFalse(supersession.read(said))

    def test_an_answer_to_an_offer_does_not_supersede_it(self):
        for said in ("Yeah.", "No thanks.", "Maybe.", "sure"):
            with self.subTest(said=said):
                self.assertFalse(supersession.read(said))

    def test_nothing_said_supersedes_nothing(self):
        self.assertFalse(supersession.read(""))
        self.assertFalse(supersession.read("   "))

    def test_the_decision_carries_the_conclusion(self):
        decision = decide(_route("conversation"), supersedes_pending=True)

        self.assertTrue(decision.supersedes_pending)
        self.assertIn("Supersedes what was pending", decision.log_block())


class AmbiguityFailsConservativelyTests(unittest.TestCase):
    """An unread request answers. It never invents an action."""

    def test_an_unknown_intent_touches_nothing(self):
        decision = decide(_route("something_new_entirely"))

        self.assertEqual(decision.mode, ANSWER)
        self.assertFalse(decision.acts)
        self.assertIn("no interaction rule", decision.reason)

    def test_an_outstanding_question_outranks_everything(self):
        decision = decide(
            _route("computer_action", action_requested=True,
                   computer_operation="open_url"),
            goal=goal_intent.SemanticGoal(intent=goal_intent.CLARIFY),
        )

        self.assertEqual(decision.mode, CLARIFY)
        self.assertFalse(decision.acts)

    def test_it_costs_no_model_call(self):
        # The property that makes it safe to consult as often as a turn
        # likes: a scripted client that raises if it is ever used.
        class _Never:
            def chat(self, **kwargs):  # pragma: no cover - must not run
                raise AssertionError("the interaction layer called a model")

        engine = build_engine()
        engine.client = _Never()

        decide(_route("web_search", action_requested=True))


class WholeTurnTests(unittest.TestCase):
    """The decision reaches the turn, and the turn reports it."""

    def test_a_correction_turn_reports_that_it_supersedes(self):
        said = "Actually let's look at keyboards instead."
        engine = build_engine(routes={said.casefold(): {
            "intent": "conversation", "confidence": 0.95,
            "normalized_request": "look at keyboards instead",
            "topic": "keyboard", "speech_act": "statement",
            "request_explicitness": "statement", "reason": "changed subject",
        }})
        engine._web_search_enabled = True
        engine.research_agent._search = lambda q, m=5: "No results."
        engine.client.reply = "Keyboards are fun too."

        engine.chat(said)

        self.assertTrue(engine._supersedes)
        self.assertEqual(engine._supersedes.reason, supersession.CORRECTION)

    def test_an_ordinary_turn_supersedes_nothing(self):
        said = "What's the capital of France?"
        engine = build_engine(routes={said.casefold(): {
            "intent": "knowledge_question", "confidence": 0.95,
            "normalized_request": "capital of France", "topic": "France",
            "speech_act": "information_request", "reason": "a fact question",
        }})
        engine.client.reply = "Paris."

        engine.chat(said)

        self.assertFalse(engine._supersedes)


class TheSessionsOwnResultsAreReusedTests(unittest.TestCase):
    """Two turns, one search. The point of `continue`, end to end."""

    ROUTES = {
        "find me a good monitor": {
            "intent": "web_search", "confidence": 0.95,
            "normalized_request": "find a good monitor", "topic": "monitor",
            "speech_act": "action_request", "action_requested": True,
            "requires_external_evidence": True,
            "recommendation_needed": True,
            "reason": "The user asked for options outright.",
        },
        "which one would you choose?": {
            "intent": "conversation", "confidence": 0.95,
            "normalized_request": "which one would you choose",
            "topic": "monitor", "speech_act": "information_request",
            "is_follow_up": True,
            "reason": "A follow-up about what was found.",
        },
    }

    def _two_turns(self):
        engine = build_engine(routes=self.ROUTES)
        engine._web_search_enabled = True
        engine.searched = []
        engine.research_agent._search = (
            lambda q, m=5: engine.searched.append(q)
            or "LG UltraGear 27GP850-B, 165Hz IPS."
        )
        engine.client.reply = "The LG UltraGear 27GP850-B looks good."
        engine.chat("Find me a good monitor")
        first = len(engine.searched)
        engine.client.reply = "I'd take the LG UltraGear."
        engine.chat("Which one would you choose?")
        return engine, first

    def test_the_first_turn_searches(self):
        engine, first = self._two_turns()

        self.assertEqual(first, 1)

    def test_the_follow_up_does_not_search_again(self):
        # Criterion 7, as the only thing that really proves it: the count.
        engine, _first = self._two_turns()

        self.assertEqual(
            len(engine.searched), 1,
            "the follow-up ran the search a second time",
        )

    def test_and_the_decision_says_why(self):
        engine, _first = self._two_turns()

        self.assertTrue(engine._last_interaction.continues)
        self.assertEqual(engine._last_interaction.need, NEED_RECALLED)


if __name__ == "__main__":
    unittest.main()
