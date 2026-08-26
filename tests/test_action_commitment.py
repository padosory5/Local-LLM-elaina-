import unittest

from brain.action_commitment import ActionCommitmentGuard


# The exact sentence Elaina said twice, live, with nothing ever opening.
LIVE_FAILURE = (
    "I can check prices directly through the browser. Let me open the "
    "website and find the current rates for you."
)


class PromiseDetectionTests(unittest.TestCase):
    def test_the_live_failure_sentence_is_caught(self):
        self.assertTrue(ActionCommitmentGuard.promises_action(LIVE_FAILURE))

    def test_common_promise_phrasings_are_caught(self):
        for text in (
            "I'll check that for you now.",
            "Let me look up the current price.",
            "I'm going to open the site.",
            "I'm searching for it now.",
            "Give me a moment.",
            "Hold on while I pull up the page.",
        ):
            with self.subTest(text=text):
                self.assertTrue(ActionCommitmentGuard.promises_action(text))

    def test_a_plain_answer_is_not_a_promise(self):
        for text in (
            "The Peninsula is the best-known option near the harbour.",
            "I'll tell you what I know: rooms there start around $200.",
            "Let me know if the dates change.",
            "I checked and rooms start at $68.",
        ):
            with self.subTest(text=text):
                self.assertFalse(ActionCommitmentGuard.promises_action(text))

    def test_a_promise_backed_by_a_real_action_is_not_broken(self):
        self.assertFalse(
            ActionCommitmentGuard.broken_promise(LIVE_FAILURE, action_performed=True)
        )
        self.assertTrue(
            ActionCommitmentGuard.broken_promise(LIVE_FAILURE, action_performed=False)
        )


class OfferDetectionTests(unittest.TestCase):
    def test_an_answerable_question_is_an_offer_not_a_promise(self):
        for text in (
            "Want me to check the live prices?",
            "Should I open the booking site?",
            "Would you like me to compare them?",
        ):
            with self.subTest(text=text):
                self.assertTrue(ActionCommitmentGuard.offers_action(text))

    def test_a_statement_of_fact_is_not_an_offer(self):
        self.assertFalse(ActionCommitmentGuard.offers_action("Rooms start at $68."))


class PromisedActionTests(unittest.TestCase):
    """When the user's turn is vague, the promise is the only place the
    real goal is written down."""

    def test_the_promise_sentence_is_returned(self):
        self.assertEqual(
            ActionCommitmentGuard.promised_action(
                "Rooms look cheap. Let me open Trip.com and confirm."
            ),
            "Let me open Trip.com and confirm.",
        )

    def test_a_softer_stated_intention_still_names_the_action(self):
        # Not a broken promise on its own, but it is where the goal is
        # written down when the user's own turn was vague.
        self.assertEqual(
            ActionCommitmentGuard.promised_action(
                "Rooms look cheap. I can check Trip.com prices for you."
            ),
            "I can check Trip.com prices for you.",
        )
        self.assertFalse(
            ActionCommitmentGuard.promises_action("I can check Trip.com prices for you.")
        )

    def test_a_reply_with_no_promise_returns_nothing(self):
        self.assertEqual(
            ActionCommitmentGuard.promised_action("Rooms start at $68."), "",
        )

    def test_an_empty_reply_returns_nothing(self):
        self.assertEqual(ActionCommitmentGuard.promised_action(""), "")


class PromiseRewritingTests(unittest.TestCase):
    def test_stripping_keeps_every_sentence_that_stands_on_its_own(self):
        text = "Rooms start around $68. Let me open the site and confirm."

        self.assertEqual(
            ActionCommitmentGuard.strip_promise(text),
            "Rooms start around $68.",
        )

    def test_stripping_everything_falls_back_to_the_replacement(self):
        result = ActionCommitmentGuard.strip_promise(
            "Let me check that for you.", replacement="I can't do that one.",
        )

        self.assertEqual(result, "I can't do that one.")

    def test_a_promise_becomes_an_answerable_offer(self):
        result = ActionCommitmentGuard.rewrite_promise_as_offer(
            LIVE_FAILURE, "I can use browser control for this -- want me to?",
        )

        self.assertNotIn("Let me open", result)
        self.assertIn("want me to?", result)
        self.assertFalse(ActionCommitmentGuard.promises_action(result))

    def test_content_alongside_the_promise_survives_the_rewrite(self):
        result = ActionCommitmentGuard.rewrite_promise_as_offer(
            "Rooms start around $68. Let me open the site and confirm.",
            "Want me to check it directly?",
        )

        self.assertIn("$68", result)
        self.assertIn("Want me to check it directly?", result)

    def test_an_empty_reply_stays_empty_when_there_is_nothing_to_offer(self):
        self.assertEqual(ActionCommitmentGuard.strip_promise("", replacement=""), "")


if __name__ == "__main__":
    unittest.main()
