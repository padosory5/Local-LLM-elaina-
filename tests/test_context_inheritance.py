"""What a turn inherits from the turns before it.

A3. The rule under test is small and the reason for it is not: measured
across three sessions, a turn about something else kept being answered from
the conversation it interrupted.

    User:   i had a rough night / just couldn't sleep / yeah
    User:   what's 2+2
    Elaina: That's straightforward.

``reset_history`` already existed and was wired to ``route.topic_shift``,
which the *model* decides and frequently does not set. The deterministic
answer to the same question was already in the codebase --
``context_policy.subjects_agree`` decides whether stored evidence is about
the current subject -- so history now asks it too.

**Honest limitation, recorded here rather than in a report nobody reads:**
this rule did not fire on any of the twelve cases in
`tests/contamination_matrix.json`. The live failure it was written for
takes a different path through ``_answer_turn`` (a successful calculation
plan builds trusted-result messages, which never consult this at all). The
logic below is correct and covered; whether it is *reached* on the turn
that motivated it is not yet demonstrated.
"""

import unittest
from types import SimpleNamespace

from brain.chat_engine import ChatEngine


class Store:
    """Just enough task-session store to answer focus()."""

    def __init__(self, subject: str = ""):
        self._subject = subject

    def focus(self):
        if not self._subject:
            return None
        return SimpleNamespace(subject=self._subject)


def resets(held: str, current: str, *, topic_shift: bool = False) -> bool:
    engine = object.__new__(ChatEngine)
    engine.task_sessions = Store(held)
    return engine._should_reset_history(
        SimpleNamespace(topic_shift=topic_shift, topic=current),
        SimpleNamespace(subject=current),
    )


class HistoryFollowsTheSubjectTests(unittest.TestCase):

    def test_a_turn_about_something_else_starts_clean(self):
        self.assertTrue(resets("emotions", "mathematics"))
        self.assertTrue(resets("travel preparation", "coffee"))

    def test_a_turn_about_the_same_thing_keeps_the_conversation(self):
        self.assertFalse(resets("coffee", "coffee brewing"))
        self.assertFalse(resets("entertainment", "entertainment"))
        self.assertFalse(resets("hotels in Seoul", "Seoul hotels"))

    def test_nothing_is_reset_on_a_guess(self):
        # Either subject being unknown is not a disagreement. The old
        # behaviour is what happens when the comparison cannot be made.
        self.assertFalse(resets("", "mathematics"))
        self.assertFalse(resets("emotions", ""))

    def test_the_model_can_still_say_so_itself(self):
        # topic_shift keeps its meaning; this only adds a second reason.
        self.assertTrue(resets("coffee", "coffee", topic_shift=True))


class OneDecisionForEveryBuilderTests(unittest.TestCase):
    """Three prompt builders, one answer to what the turn may carry.

    The reason this is asserted on the source rather than on behaviour:
    the fault was never that a rule was wrong, it was that a builder never
    asked. A test of the rule cannot catch a caller that skips it.
    """

    def test_no_builder_decides_for_itself_any_more(self):
        import inspect

        source = inspect.getsource(ChatEngine._answer_turn)

        # The two shapes that each used to answer separately.
        self.assertNotIn("history=[] if route.topic_shift else None", source)
        self.assertNotIn("reset_history=route.topic_shift", source)
        # And the one they all read now.
        self.assertIn("turn_context = self._context_for_turn(", source)
        self.assertIn("history=turn_context.history_for_builder", source)
        self.assertEqual(
            source.count("reset_history=not turn_context.inherit_history"), 3,
        )

    def test_the_trusted_result_path_can_now_drop_history(self):
        # The path a successful calculation plan takes, and the one that
        # had no history control at all. "what's 2+2" after four sympathy
        # turns came back "That's straightforward."
        import inspect

        builder = inspect.getsource(ChatEngine._build_tool_result_messages)
        self.assertIn("inherit_history", builder)
        self.assertIn("if inherit_history else []", builder)

        source = inspect.getsource(ChatEngine._answer_turn)
        self.assertEqual(
            source.count("inherit_history=turn_context.inherit_history"), 2,
        )

    def test_the_context_is_built_before_anything_branches(self):
        import inspect

        source = inspect.getsource(ChatEngine._answer_turn)
        decided = source.index("turn_context = self._context_for_turn(")
        for site in ("_build_factual_messages", "_build_tool_result_messages"):
            with self.subTest(site=site):
                self.assertLess(
                    decided, source.index(site),
                    "the context has to be decided before a builder reads it",
                )


if __name__ == "__main__":
    unittest.main()


class ATopicChangeMovesALineTests(unittest.TestCase):
    """Starting clean has to outlive the turn that started it.

    The trace that found this, on "which graphics card is better" → "what
    should I eat for dinner" → "which one would you choose?":

        [Context] Starting clean: the router says the topic moved
        [Context] Inheriting the conversation (was dinner, now dinner)
        reply: I'd go with the RTX 4080 ...

    Every decision in that trace is correct. The subject was dinner, the
    topic change was seen, and the next turn genuinely did belong to the
    dinner conversation. The scope was wrong: "starting clean" emptied only
    that one prompt, so the exchange it walked away from was still in the
    manager for the very next turn to inherit.
    """

    def setUp(self):
        from brain.conversation_manager import ConversationManager

        self.conversation = ConversationManager()

    def _say(self, *pairs):
        for user, assistant in pairs:
            self.conversation.add("user", user)
            self.conversation.add("assistant", assistant)

    def test_history_starts_at_the_last_boundary(self):
        self._say(("which gpu is better", "the 4080"))
        self.conversation.start_new_subject()
        self._say(("what should I eat", "pasta"))

        said = [m["content"] for m in self.conversation.get_history()]
        self.assertEqual(said, ["what should I eat", "pasta"])

    def test_the_transcript_is_still_the_transcript(self):
        # Counted, not deleted: other layers audit the whole session.
        self._say(("which gpu is better", "the 4080"))
        self.conversation.start_new_subject()

        self.assertEqual(len(self.conversation.full_history()), 2)
        self.assertEqual(self.conversation.get_history(), [])

    def test_the_boundary_moves_with_the_deque(self):
        # The deque drops from the front when full, so a boundary counted
        # in messages has to move with it or it drifts backwards into
        # turns it was never meant to cover.
        from brain.conversation_manager import ConversationManager

        conversation = ConversationManager(max_messages=4)
        conversation.add("user", "one")
        conversation.add("assistant", "two")
        conversation.start_new_subject()
        conversation.add("user", "three")
        conversation.add("user", "four")

        # Nothing evicted yet: the deque just reached its limit.
        self.assertEqual(
            [m["content"] for m in conversation.get_history()],
            ["three", "four"],
        )

        # Now each append evicts one, and the boundary has to follow --
        # "one" and "two" leaving must not drag the line onto "three".
        conversation.add("user", "five")
        conversation.add("user", "six")

        self.assertEqual(
            [m["content"] for m in conversation.get_history()],
            ["three", "four", "five", "six"],
        )

    def test_clearing_forgets_the_boundary_too(self):
        self._say(("which gpu is better", "the 4080"))
        self.conversation.start_new_subject()
        self.conversation.clear()
        self._say(("hello", "hi"))

        self.assertEqual(len(self.conversation.get_history()), 2)

    def test_the_engine_moves_it_when_a_turn_starts_clean(self):
        import inspect

        source = inspect.getsource(ChatEngine._answer_turn)
        moved = source.index("self.conversation.start_new_subject()")
        decided = source.index("turn_context = self._context_for_turn(")

        self.assertLess(decided, moved)
        self.assertIn("if not turn_context.inherit_history:", source)

    def test_held_results_follow_the_same_line(self):
        """Moving the history line without moving this one, measured live:

            User:   actually forget the mouse, what's a good film for tonight?
            Elaina: A perfect pick for tonight? The one I actually found is
                    AmazonBasics Wireless Mouse.

        The reply was assembled by the guards that name what a search
        found, reading a result set that belonged to the subject the turn
        had just walked away from. Held results are as inheritable as held
        turns, and have to be retired by the same decision.
        """
        import inspect

        source = inspect.getsource(ChatEngine._answer_turn)
        retired = source.index(
            "if not turn_context.inherit_history and active_problem is not None:"
        )
        named = source.index("_enforce_named_recommendation")

        self.assertLess(
            retired, named,
            "the old subject's candidates have to be dropped before the "
            "guards that name what was found read them",
        )
