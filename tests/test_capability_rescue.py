"""The dead-end that made Elaina say "That PC action isn't supported yet"
about browser control -- an ability she has had since Phase 4C.

The router refuses any ``computer_action`` it cannot ground to one narrow
Phase-4A operation. That refusal is correct about the *operation*; it was
wrong as a final answer, because the goal-driven planners exist precisely
for requests that don't fit one structured operation. These tests pin the
rescue: re-aim at the capability the request actually names, or say
honestly what is possible instead -- never a canned refusal.
"""

import threading
import unittest

from brain.chat_engine import ChatEngine
from brain.intent_router import IntentDecision
from security.capability_offer import CapabilityOfferGate


class _Mode:
    def __init__(self, enabled):
        self.enabled = enabled


class _Screen:
    enabled = True


def _engine(*, control_mode=True, browser=True):
    engine = ChatEngine.__new__(ChatEngine)
    engine.computer_control_mode = _Mode(control_mode)
    engine.browser_page_control_enabled = browser
    engine._web_search_enabled = True
    engine.screen_monitor = _Screen()
    engine.project_mcp = None
    engine.capability_offer = CapabilityOfferGate()
    engine._desktop_surface_lock = threading.Lock()
    return engine


def _dead_end(request):
    """Exactly what SemanticIntentRouter returns for an ungrounded
    computer request -- the shape observed live in the transcript."""
    return IntentDecision(
        intent="computer_action",
        confidence=0.95,
        normalized_request=request,
        reason=(
            "The computer request is not a grounded Phase 4A action "
            "and must not reach a computer tool."
        ),
        action_requested=False,
        computer_operation="unsupported",
    )


class DeadEndRescueTests(unittest.TestCase):
    def test_an_ungrounded_browser_request_reaches_the_browser_planner(self):
        engine = _engine()

        route, note = engine._rescue_capability_route(
            _dead_end("check the price on the browser"),
            "check the price on the browser",
        )

        self.assertEqual(note, "")
        self.assertEqual(route.intent, "computer_action")
        self.assertEqual(route.computer_operation, "browser_action")
        self.assertTrue(route.action_requested)
        self.assertEqual(route.action_target, "check the price on the browser")

    def test_an_ungrounded_app_request_reaches_the_desktop_planner(self):
        engine = _engine()

        route, note = engine._rescue_capability_route(
            _dead_end("open Spotify and start my playlist"),
            "open Spotify and start my playlist",
        )

        self.assertEqual(note, "")
        self.assertEqual(route.computer_operation, "ui_action")

    def test_a_switched_off_ability_explains_the_switch_instead_of_refusing(self):
        engine = _engine(control_mode=False)

        route, note = engine._rescue_capability_route(
            _dead_end("check the price on the browser"),
            "check the price on the browser",
        )

        self.assertIn("browser control", note)
        self.assertIn("Desktop Control Mode is off", note)
        self.assertIn("Computer Control toggle", note)
        # The route is left alone: nothing may run while the switch is off.
        self.assertEqual(route.computer_operation, "unsupported")

    def test_a_request_no_ability_covers_says_what_is_possible(self):
        engine = _engine()

        _route, note = engine._rescue_capability_route(
            _dead_end("solder the loose wire on my headset"),
            "solder the loose wire on my headset",
        )

        self.assertIn("I can't do that one", note)
        self.assertIn("Right now I can use", note)
        self.assertNotIn("isn't supported yet", note)


class ConversationalActionTests(unittest.TestCase):
    """The other half of the live transcript: a real action request that
    the router labelled plain ``conversation``."""

    @staticmethod
    def _conversation(request):
        return IntentDecision(
            intent="conversation",
            confidence=0.95,
            normalized_request=request,
            reason="Chatting.",
        )

    def test_an_imperative_browser_request_is_escalated(self):
        engine = _engine()

        route, note = engine._rescue_capability_route(
            self._conversation("open trip.com and check the rooms"),
            "open trip.com and check the rooms",
        )

        self.assertEqual(note, "")
        self.assertEqual(route.intent, "computer_action")
        self.assertEqual(route.computer_operation, "browser_action")

    def test_merely_mentioning_a_browser_is_left_as_conversation(self):
        engine = _engine()

        for text in (
            "I like using Chrome more than Edge",
            "my browser has been slow lately",
        ):
            with self.subTest(text=text):
                route, note = engine._rescue_capability_route(
                    self._conversation(text), text,
                )

                self.assertEqual(route.intent, "conversation")
                self.assertEqual(note, "")

    def test_a_bare_ability_question_is_answered_from_the_registry(self):
        # Live, the model answered this one "I cannot control your
        # browser" -- a flat lie about a Phase 4C ability. It is no longer
        # asked: the answer comes from the capability table.
        engine = _engine()

        route, note = engine._rescue_capability_route(
            self._conversation("can you control my browser?"),
            "can you control my browser?",
        )

        self.assertEqual(route.intent, "conversation")
        self.assertTrue(note.startswith("Yes"))
        self.assertIn("browser", note)
        # And it leaves an answerable offer behind, not a dead statement.
        self.assertEqual(engine.capability_offer.peek().capability_id, "browser_control")

    def test_a_polite_request_is_acted_on_rather_than_described(self):
        # "Can you check X?" names a real thing to do; answering with a
        # description of the ability would be a worse answer than doing it.
        engine = _engine()

        route, _note = engine._rescue_capability_route(
            self._conversation("can you check prices in the browser?"),
            "can you check prices in the browser?",
        )

        self.assertEqual(route.intent, "computer_action")
        self.assertEqual(route.computer_operation, "browser_action")

    def test_the_whole_inventory_is_listed_without_a_model_call(self):
        engine = _engine(control_mode=False)

        _route, note = engine._rescue_capability_route(
            self._conversation("what can you do?"), "what can you do?",
        )

        self.assertIn("Right now I can use", note)
        self.assertIn("Currently off:", note)
        self.assertIn("browser control", note)


class CommitmentEnforcementTests(unittest.TestCase):
    """The promise Elaina made twice, live, and never kept."""

    LIVE = (
        "I can check prices directly through the browser. Let me open the "
        "website and find the current rates for you."
    )

    def test_an_unkept_promise_becomes_an_answerable_offer(self):
        engine = _engine()

        reply = engine._enforce_action_commitment(
            self.LIVE,
            user_input="check the price on the browser",
            action_performed=False,
        )

        self.assertNotIn("Let me open", reply)
        self.assertIn("want me to?", reply)
        # And the offer is now resolvable, so the user's next "ok" has
        # something real to attach to.
        pending = engine.capability_offer.peek()
        self.assertIsNotNone(pending)
        self.assertEqual(pending.capability_id, "browser_control")

    def test_a_promise_backed_by_a_real_action_is_left_alone(self):
        engine = _engine()

        reply = engine._enforce_action_commitment(
            self.LIVE,
            user_input="check the price on the browser",
            action_performed=True,
        )

        self.assertEqual(reply, self.LIVE)
        self.assertIsNone(engine.capability_offer.peek())

    def test_a_promise_for_a_switched_off_ability_states_the_fix(self):
        engine = _engine(control_mode=False)

        reply = engine._enforce_action_commitment(
            self.LIVE,
            user_input="check the price on the browser",
            action_performed=False,
        )

        self.assertIn("Desktop Control Mode is off", reply)
        self.assertNotIn("Let me open", reply)
        self.assertIsNone(engine.capability_offer.peek())

    def test_an_ordinary_answer_is_never_touched(self):
        engine = _engine()
        answer = "Rooms at The Peninsula start around $600 a night."

        reply = engine._enforce_action_commitment(
            answer, user_input="how much is the peninsula", action_performed=False,
        )

        self.assertEqual(reply, answer)


if __name__ == "__main__":
    unittest.main()
