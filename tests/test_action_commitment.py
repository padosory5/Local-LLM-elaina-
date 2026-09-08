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


class TheHonestyGuardReadsKoreanTests(unittest.TestCase):
    """Korean drops the subject, so a promise has no "I" in it.

    The module carried one Korean alternative and it required 제가. Every
    promise she actually makes -- "확인해 보겠습니다", straight out of her
    own status bank -- has no subject pronoun at all, so none of them were
    ever checked against whether anything ran.

    The endings it looked for were 볼게 / 드릴게, which are 해요체 and
    반말. A2 moved her to 습니다체, so the guard was reading for a register
    she no longer speaks.
    """

    def test_a_korean_promise_is_a_promise(self):
        for said in (
            "확인해 보겠습니다.",
            "바로 찾아보겠습니다.",
            "지금 열겠습니다.",
            "잠시만 기다려 주십시오.",
            "검색 중입니다.",
            "제가 한번 확인해 볼게요.",
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    ActionCommitmentGuard.promises_action(said), said)

    def test_a_korean_offer_is_an_offer(self):
        for said in ("확인해 드릴까요?", "한번 알아볼까요?", "띄워 드릴까요?"):
            with self.subTest(said=said):
                self.assertTrue(
                    ActionCommitmentGuard.offers_action(said), said)

    def test_an_ordinary_korean_answer_is_neither(self):
        # The half that matters most: a guard that fires on plain answers
        # deletes honest sentences.
        for said in (
            "15%의 84는 12.60입니다.",
            "런던 시간은 오후 3시입니다.",
            "콜드브루는 12시간 우려냅니다.",
            "도움이 되었다면 다행입니다.",
            # Conversational verbs are satisfied by the reply itself, the
            # same reason English excludes "tell" and "explain".
            "말씀드리겠습니다.",
            "알려드리겠습니다.",
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    ActionCommitmentGuard.promises_action(said), said)
                self.assertFalse(
                    ActionCommitmentGuard.offers_action(said), said)

    def test_a_korean_promise_with_nothing_run_is_broken(self):
        self.assertTrue(
            ActionCommitmentGuard.broken_promise(
                "확인해 보겠습니다.", action_performed=False,
            )
        )
        self.assertFalse(
            ActionCommitmentGuard.broken_promise(
                "확인해 보겠습니다.", action_performed=True,
            )
        )

    def test_the_promise_sentence_can_be_named_and_stripped(self):
        reply = "런던 시간은 오후 3시입니다. 지금 열겠습니다."

        self.assertEqual(
            ActionCommitmentGuard.promised_action(reply), "지금 열겠습니다.")
        self.assertEqual(
            ActionCommitmentGuard.strip_promise(reply),
            "런던 시간은 오후 3시입니다.",
        )

    def test_the_two_families_of_work_are_told_apart_in_korean(self):
        from brain.action_commitment import LOOK, OPEN, action_family

        self.assertEqual(action_family("지금 열겠습니다."), OPEN)
        self.assertEqual(action_family("검색해 보겠습니다."), LOOK)
