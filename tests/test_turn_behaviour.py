"""What she does when you say things -- through the whole turn.

This is the suite that was missing. 1,197 unit tests were green on the day
a single regex answered every politely-phrased instruction with a feature
list, because not one of them ran `ChatEngine.chat()`. Each case here says
one thing: *when I say this, she does that*, with the router, the front
door, the guards and the handlers all real and only the model and the
machine replaced.

Four behaviours are worth distinguishing, and nothing else is asserted:

    chat    -- she answers, and touches nothing
    asks    -- she asks one question, and touches nothing
    acts    -- she instructs the machine, and what she asked for is right
    refuses -- she declines, and touches nothing

Adding a case is one line. That is the point: this is the net that catches
a new feature breaking an old behaviour, so it has to be cheap to extend.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

from tests.turn_harness import build_engine, machine_actions, reset

_ENGINE = None


def setUpModule():
    global _ENGINE
    _ENGINE = build_engine(routes=_ROUTES)


def tearDownModule():
    if _ENGINE is not None:
        _ENGINE.close()


def _route(operation="none", intent="computer_action", target="", **extra):
    decision = {
        "intent": intent,
        "confidence": 0.95,
        "normalized_request": target or "the request",
        "reason": "scripted for the turn suite",
        "computer_operation": operation,
        "action_target": target,
        "speech_act": "action_request" if operation != "none" else "information_request",
        "action_requested": operation != "none",
    }
    decision.update(extra)
    return decision


# How the router answers, for the cases where the router is what decides.
# Cases the front door claims need no entry here -- which is itself worth
# reading: those are the ones that no longer depend on a model at all.
_ROUTES = {
    "close spotify": _route("close_app", target="Spotify"),
    "open discord": _route("open_app", target="Discord"),
    "force quit": _route("force_quit_app", target="Discord"),
    "delete the folder": _route("delete_folder", target="old stuff"),
    "make a folder": _route("create_folder", target="Trip"),
    "find hotels in guam": _route(intent="web_search"),
    # The router grounding nothing: what the rescue layer exists for.
    "make the text bigger": _route("none", target=""),
    # What the live router really answers for this one -- see KnownGapTests.
    "open my documents folder": _route("create_folder", target="Documents"),
    "bang bang by ive": _route("ui_action", target="play Bang Bang by IVE"),
    "how has your day": _route(intent="conversation"),
    "what do you think": _route(intent="conversation"),
    "tallest building": _route(intent="knowledge_question"),
    "thanks": _route(intent="conversation"),
}


@dataclass(frozen=True)
class Turn:
    """One thing said, and what should happen because of it."""

    said: str
    expect: str
    touches: tuple[str, ...] = ()
    says: tuple[str, ...] = ()
    note: str = ""
    routed_by_model: bool = field(default=False)


CASES: tuple[Turn, ...] = (
    # -- ordinary conversation stays ordinary --------------------------
    Turn("how has your day been?", "chat", routed_by_model=True),
    Turn("what do you think about that?", "chat", routed_by_model=True),
    Turn("thanks, that helped", "chat", routed_by_model=True),

    # -- questions about herself are answered, not acted on ------------
    Turn("what can you do", "chat",
         note="the inventory, from the registry rather than the model"),
    Turn("can you control my browser", "chat",
         note="names no target, so it is a real question"),

    # -- a request phrased as a question is a request ------------------
    Turn("can you close spotify", "acts", touches=("close_app",),
         routed_by_model=True,
         note="the bug that made her unusable for a day"),
    Turn("could you open discord", "acts", touches=("open_app",),
         routed_by_model=True),

    # -- media: the requests this system was built for -----------------
    Turn("play bang bang by ive", "acts", touches=("type", "click")),
    Turn("play my liked songs", "acts", touches=("click",),
         says=("liked songs",),
         note="named a place, so the collection skill runs"),
    Turn("play my liked songs in spotify", "acts", touches=("click",)),
    Turn("play some music", "asks", says=("Which song",),
         note="names nothing, and nothing has been played yet"),
    Turn("play something from my playlist", "asks",
         says=("Which song",),
         note="'my playlist' is not a playlist"),

    # -- and the ones she should not treat as media --------------------
    Turn("play chess", "chat", routed_by_model=True,
         note="not music; the front door leaves it alone"),

    # -- committing to something needs its inputs first ----------------
    Turn("book me a hotel in guam", "asks",
         says=("check-in and check-out",),
         note="asked before anything is opened"),

    # -- high-risk operations still stop for a separate yes ------------
    Turn("force quit discord", "asks", routed_by_model=True,
         note="confirmation, not a refusal"),
    Turn("delete the folder called old stuff", "asks", routed_by_model=True),

    # -- ordinary structured operations still work ---------------------
    Turn("make a folder called Trip on my desktop", "acts",
         touches=("create_folder",), routed_by_model=True),

    # -- knowledge questions do not touch the machine ------------------
    Turn("what is the tallest building in seoul", "chat", routed_by_model=True),

    # -- one gate, on paths that never had one -------------------------
    Turn("type in Notepad", "asks", says=("What would you like me to type",),
         note="a task-shaped request with nothing to type, asked before "
              "any planner is chosen"),
    Turn("write to my document", "asks",
         note="same: read, incomplete, and stopped at the gate"),
)


class TurnBehaviourTests(unittest.TestCase):
    """One test per thing you might say."""

    def _run(self, case: Turn) -> tuple[str, list[tuple[str, str]]]:
        reset(_ENGINE)
        reply = _ENGINE.chat(case.said)
        return reply, machine_actions(_ENGINE)

    def test_every_case(self):
        failures: list[str] = []
        for case in CASES:
            with self.subTest(said=case.said, expect=case.expect):
                reply, actions = self._run(case)
                problem = _check(case, reply, actions)
                if problem:
                    failures.append(f"{case.said!r}: {problem}")
                    self.fail(f"{case.said!r}: {problem}")
        self.assertEqual(failures, [])


def _check(case: Turn, reply: str, actions: list[tuple[str, str]]) -> str:
    """What is wrong with this turn, or an empty string if nothing is."""
    kinds = [kind for kind, _ in actions]
    # Resolving a target is not doing anything: a confirmation question
    # prepares an action and waits. Only what ran counts as touching.
    performed = [kind for kind in kinds if not kind.startswith("prepare:")]
    text = str(reply or "")

    if case.expect in {"chat", "asks", "refuses"} and performed:
        return f"expected {case.expect} but it did: {performed}"
    if not text.strip():
        return "said nothing at all"

    if case.expect == "asks" and "?" not in text:
        return f"expected a question, got {text[:70]!r}"
    if case.expect == "acts" and not performed:
        return f"expected an action, got only {text[:70]!r}"
    if case.expect == "acts":
        for wanted in case.touches:
            if not any(wanted in kind for kind in performed):
                return f"expected {wanted!r} among {performed}"
    for phrase in case.says:
        if phrase.casefold() not in text.casefold():
            return f"expected {phrase!r} in {text[:80]!r}"
    return ""


class SequenceTests(unittest.TestCase):
    """Turns that only go wrong when they follow another turn."""

    def _say(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        _ENGINE.desktop_action_planner.control.actions.clear()
        _ENGINE.computer_control.operations.clear()
        _ENGINE.browser_control.actions.clear()
        return _ENGINE.chat(text), machine_actions(_ENGINE)

    def setUp(self):
        reset(_ENGINE)

    def test_an_unanswered_offer_does_not_swallow_the_next_request(self):
        # Found by this suite on its first run, and it is the shape of the
        # complaint that started the rebuild: an offer left pending
        # answered every following turn with itself.
        first, _ = self._say("can you control my browser")
        self.assertIn("?", first)

        reply, actions = self._say("play my liked songs")

        self.assertNotIn("Want me to use it now", reply)
        self.assertTrue(actions, f"it did nothing and said {reply[:70]!r}")

    def test_answering_a_question_continues_that_request(self):
        asked, actions = self._say("play some music")
        self.assertIn("Which song", asked)
        self.assertEqual(actions, [])

        reply, actions = self._say("Bang Bang by IVE")

        self.assertTrue(actions, f"the answer did nothing: {reply[:70]!r}")
        self.assertIn("type", [kind for kind, _ in actions])

    def test_a_new_instruction_is_not_taken_as_an_answer(self):
        self._say("play some music")

        reply, actions = self._say("close spotify")

        self.assertIn(
            "close_app:executed", [kind for kind, _ in actions],
            f"the new instruction was swallowed: {reply[:70]!r}",
        )

    def test_the_gate_asks_before_any_path_is_chosen(self):
        # F-1 in the audit: the gate guarded two doors of eight, so "ask
        # when unclear" was a property of desktop and browser actions
        # rather than of her. Nothing is dispatched now until it is settled.
        reply, actions = self._say("type in Notepad")

        self.assertIn("?", reply)
        self.assertEqual(actions, [])
        self.assertIsNotNone(_ENGINE.clarification.peek())

    def test_a_research_request_is_read_but_not_interrupted(self):
        # The task planner already runs the dates/area conversation, so the
        # gate must not ask a second time for the same request.
        reply, _actions = self._say("find hotels in guam")

        self.assertNotIn("check-in and check-out", reply)

    def test_asked_vaguely_after_playing_she_acts_and_says_what_she_assumed(self):
        self._say("play bang bang by ive")

        reply, actions = self._say("play some music")

        self.assertTrue(actions)
        self.assertIn("say the word", reply)

    def test_thanks_closes_old_task_context_instead_of_restarting_it(self):
        _ENGINE.task_sessions._context = object()
        _ENGINE._grounded_context = {"statement": "old hotel results"}

        reply, actions = self._say("thanks")

        self.assertEqual(reply, "You're welcome.")
        self.assertEqual(actions, [])
        self.assertIsNone(_ENGINE.task_sessions.current())
        self.assertEqual(_ENGINE._grounded_context, {})


class RescueLayerTests(unittest.TestCase):
    """The repair layer, pinned so retiring it cannot happen by accident.

    It exists because the router dead-ends real requests against abilities
    she has. Move 3 was going to delete it; measured, none of its branches
    is dead yet -- the front door claims media and bookings, and everything
    else can still arrive with no grounded operation. So it stays, with its
    behaviour written down and a log line that shows how often it fires.
    """

    def setUp(self):
        reset(_ENGINE)

    def test_a_request_she_cannot_ground_gets_an_honest_answer(self):
        reply = _ENGINE.chat("make the text bigger")
        performed = [
            kind for kind, _ in machine_actions(_ENGINE)
            if not kind.startswith("prepare:")
        ]

        self.assertEqual(performed, [])
        self.assertNotIn("unsupported", reply.casefold())
        self.assertTrue(reply.strip())

    def test_a_question_about_an_ability_never_reaches_a_planner(self):
        reply = _ENGINE.chat("can you control my browser")
        performed = [
            kind for kind, _ in machine_actions(_ENGINE)
            if not kind.startswith("prepare:")
        ]

        self.assertEqual(performed, [])
        self.assertIn("?", reply)


class KnownGapTests(unittest.TestCase):
    """Behaviour that is wrong today, written down so it cannot be forgotten."""

    @unittest.expectedFailure
    def test_opening_a_folder_does_not_create_one(self):
        # Measured on the live router, three runs out of three:
        # "open my documents folder" -> create_folder. There is no
        # open_folder operation in the vocabulary at all, so the router
        # picks the nearest folder-shaped thing. This flips to a pass when
        # opening a folder becomes a real capability.
        reset(_ENGINE)
        _ENGINE.chat("open my documents folder")
        created = [
            kind for kind, _ in machine_actions(_ENGINE)
            if "create_folder" in kind and "prepare" not in kind
        ]

        self.assertEqual(created, [], "opening a folder created one")


class SuiteShapeTests(unittest.TestCase):
    """The suite's own promises."""

    def test_the_requests_this_system_exists_for_need_no_model_to_route(self):
        # The point of Move 1. If one of these starts depending on the
        # router again, understanding has stopped happening first -- which
        # is precisely how they ended up misrouted before.
        deterministic = {
            case.said for case in CASES if not case.routed_by_model
        }
        for said in (
            "play bang bang by ive",
            "play my liked songs",
            "play some music",
            "book me a hotel in guam",
        ):
            with self.subTest(said=said):
                self.assertIn(said, deterministic)

    def test_every_case_states_what_should_happen(self):
        for case in CASES:
            with self.subTest(said=case.said):
                self.assertIn(case.expect, {"chat", "asks", "acts", "refuses"})


if __name__ == "__main__":
    unittest.main()
