import unittest

from brain.response_quality import ResponseQualityGuard


class ResponseQualityGuardTests(unittest.TestCase):
    def test_retries_old_greeting_for_new_current_message(self):
        history = [
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hey there. What's up?"},
        ]

        self.assertTrue(ResponseQualityGuard.should_retry(
            "Hey there. What's up?",
            "The buttons on my project look boring.",
            history,
        ))

    def test_allows_same_answer_when_user_repeats_same_message(self):
        history = [
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hey there. What's up?"},
        ]

        self.assertFalse(ResponseQualityGuard.should_retry(
            "Hey there. What's up?",
            "Hello.",
            history,
        ))

    def test_allows_repeating_a_fact_when_user_asks_for_it_again(self):
        history = [
            {"role": "user", "content": "When was OpenAI founded?"},
            {
                "role": "assistant",
                "content": "OpenAI was founded in December 2015.",
            },
        ]

        self.assertFalse(ResponseQualityGuard.should_retry(
            "OpenAI was founded in December 2015.",
            "Yeah, tell me when.",
            history,
        ))


class RepeatedAnswerTests(unittest.TestCase):
    """Reported three times across two phases, in three different shapes.

    Routing, goal and capability were all correct each time; generation
    handed back the previous assistant turn. The guard existed and did not
    fire, because it required the reply to be a *greeting* before checking
    anything else -- so it only ever caught the one case it was written for.
    """

    DINNER = (
        "You could try grilled salmon with steamed broccoli and brown rice."
    )
    HISTORY = [
        {"role": "user", "content": "what should i eat for dinner"},
        {"role": "assistant", "content": DINNER},
    ]

    def test_a_reaction_does_not_deserve_the_same_answer_again(self):
        # "That sounds good" is approval of the dinner, not a request for it.
        for said in ("that sounds good", "no thanks", "okay", "nice",
                     "yeah", "cool", "that's cool", "i like that"):
            with self.subTest(said=said):
                self.assertTrue(ResponseQualityGuard.should_retry(
                    self.DINNER, said, self.HISTORY,
                ))

    def test_a_different_answer_to_a_reaction_is_fine(self):
        self.assertFalse(ResponseQualityGuard.should_retry(
            "Glad you like it. Want me to write the recipe down?",
            "that sounds good",
            self.HISTORY,
        ))

    def test_asking_for_it_again_is_allowed_to_repeat(self):
        for said in ("tell me again", "say that again", "repeat that",
                     "one more time", "go over it again"):
            with self.subTest(said=said):
                self.assertFalse(ResponseQualityGuard.should_retry(
                    self.DINNER, said, self.HISTORY,
                ))

    def test_asking_the_same_question_is_allowed_to_repeat(self):
        self.assertFalse(ResponseQualityGuard.should_retry(
            self.DINNER, "what should i eat for dinner", self.HISTORY,
        ))

    def test_an_elliptical_request_for_the_same_fact_is_allowed(self):
        # The case a broader rule broke: "Yeah, tell me when" wants that
        # exact date again, and repeating it is the correct answer.
        history = [
            {"role": "user", "content": "When was OpenAI founded?"},
            {"role": "assistant", "content": "OpenAI was founded in December 2015."},
        ]

        self.assertFalse(ResponseQualityGuard.should_retry(
            "OpenAI was founded in December 2015.", "Yeah, tell me when.",
            history,
        ))

    def test_the_original_stale_greeting_case_still_fires(self):
        history = [
            {"role": "user", "content": "Hello."},
            {"role": "assistant", "content": "Hey there. What's up?"},
        ]

        self.assertTrue(ResponseQualityGuard.should_retry(
            "Hey there. What's up?",
            "The buttons on my project look boring.",
            history,
        ))


class StaleCourtesyTests(unittest.TestCase):
    """A thank-you answer carried forward to turns that were not thank-yous.

    Live: "thanks" -> "You're welcome.", and then the next three turns all
    opened with it, including "no thanks" and "I watched a good film last
    night". Similarity could not see either one -- "no thanks" is 0.80
    similar to "thanks" so it read as the person repeating themselves, and
    "You're welcome. Enjoy the film!" is only 0.50 similar to "You're
    welcome." so it was not a repeat. What matters is the reply's opening
    against *this* turn.
    """

    HISTORY = [
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "You're welcome."},
    ]

    def test_a_courtesy_opener_on_a_turn_that_did_not_thank_her(self):
        for reply, said in (
            ("You're welcome.", "no thanks"),
            ("You're welcome. Enjoy the film!", "i watched a good film last night"),
            ("You're welcome. Enjoy your dinner!", "tell me again"),
            ("No problem. Anything else on your mind?", "the weather is nice"),
        ):
            with self.subTest(said=said):
                self.assertTrue(ResponseQualityGuard.should_retry(
                    reply, said, self.HISTORY,
                ))

    def test_a_real_thank_you_still_gets_a_courtesy_answer(self):
        for said in ("thanks", "thank you so much", "thanks a lot",
                     "appreciate it"):
            with self.subTest(said=said):
                self.assertFalse(ResponseQualityGuard.should_retry(
                    "You're welcome.", said, self.HISTORY,
                ))

    def test_no_thanks_is_a_refusal_not_gratitude(self):
        # A bare keyword test read the refusal as thanks, which is exactly
        # how the courtesy answer survived onto that turn.
        self.assertFalse(ResponseQualityGuard._is_thanking("no thanks"))
        self.assertTrue(ResponseQualityGuard._is_thanking("thanks"))


class ConversationPromptTests(unittest.TestCase):
    """The prompt has to say the newest message is the instruction."""

    def test_the_conversation_path_forbids_repeating_the_last_answer(self):
        # The factual path has carried this clause all along; the
        # conversation path -- which is the one a short reaction takes --
        # did not, and that is where the repeats came from.
        from brain.prompt_builder import PromptBuilder

        prompt = PromptBuilder().build(
            memory_text="", user_input="that sounds good",
        )

        self.assertIn("Current user message", prompt)
        self.assertIn("that sounds good", prompt)
        self.assertIn("Do not repeat your previous answer", prompt)

    def test_both_paths_now_carry_the_same_rule(self):
        from brain.prompt_builder import PromptBuilder
        from brain.response_messages import build_personality_messages

        conversation = PromptBuilder().build(memory_text="", user_input="hi")
        factual = build_personality_messages(
            system_prompt="P", history=[], user_input="hi",
        )[-1]["content"]

        for prompt in (conversation.casefold(), factual.casefold()):
            self.assertIn("do not repeat", prompt)


if __name__ == "__main__":
    unittest.main()
