"""A rewrite may not hand back something she has just said.

B-56, from session 3, and it is the founding complaint of this project
surviving in a path nobody had checked:

    You said: nice
    [Response Rewrite] The first rewrite was not complete; applied the
                       advice fallback when valid.
    You're welcome! Kiwis are also good for heart health and can help with
    constipation. Want to try one?

    You said: Are you gonna feed me?
    [Response Rewrite] The first rewrite was not complete; applied the
                       advice fallback when valid.
    You're welcome! Kiwis are also good for heart health and can help with
    constipation. Want to try one?

    You said: you're repeating yourself

Word for word, twice, and the second is not an answer to the question. The
repetition guard runs on the draft; the rewrite replaces the draft two
hundred lines later and nothing re-checks it. So the one path that
regenerates an answer was the one path exempt from the rule about not
repeating one.

The same fallback produced B-18's confrontational reply in session 1. It
is a second model call whose output was trusted because the first one's
was checked.
"""

import unittest

from brain.response_quality import ResponseQualityGuard


KIWI = (
    "You're welcome! Kiwis are also good for heart health and can help "
    "with constipation. Want to try one?"
)


def history(*pairs):
    out = []
    for said, answered in pairs:
        out.append({"role": "user", "content": said})
        out.append({"role": "assistant", "content": answered})
    return out


class ARewriteIsCheckedLikeADraftTests(unittest.TestCase):

    def test_the_live_repeat_is_caught(self):
        past = history(
            ("My mom just cut me some kiwis, what are they good for",
             "Kiwis are packed with vitamin C, fiber, and antioxidants."),
            ("nice", KIWI),
        )

        self.assertTrue(
            ResponseQualityGuard.should_retry(
                KIWI, "Are you gonna feed me?", past,
            ),
            "she said the same sentence twice in a row",
        )

    def test_a_fresh_answer_is_not_flagged(self):
        past = history(("nice", KIWI))

        self.assertFalse(
            ResponseQualityGuard.should_retry(
                "I can't cook, but I can find you somewhere that does.",
                "Are you gonna feed me?",
                past,
            )
        )


class TheEngineRefusesARepeatingRewriteTests(unittest.TestCase):
    """The ordering bug itself: a rewrite must pass the same check."""

    def _engine(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine.conversation.add("user", "what are kiwis good for")
        engine.conversation.add("assistant", "Kiwis are packed with vitamin C.")
        engine.conversation.add("user", "nice")
        engine.conversation.add("assistant", KIWI)
        return engine

    def test_a_rewrite_that_repeats_is_rejected(self):
        engine = self._engine()

        self.assertFalse(
            engine._rewrite_is_usable(KIWI, user_input="Are you gonna feed me?"),
        )

    def test_a_rewrite_that_answers_is_accepted(self):
        engine = self._engine()

        self.assertTrue(
            engine._rewrite_is_usable(
                "I can't cook, but I can find you somewhere that does.",
                user_input="Are you gonna feed me?",
            )
        )

    def test_an_empty_rewrite_is_rejected(self):
        engine = self._engine()

        self.assertFalse(
            engine._rewrite_is_usable("", user_input="anything"),
        )


if __name__ == "__main__":
    unittest.main()
