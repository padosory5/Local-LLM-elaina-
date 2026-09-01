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


class CurrentTurnEchoTests(unittest.TestCase):

    def test_exact_short_reaction_is_not_echoed_back(self):
        self.assertEqual(
            ResponseQualityGuard.strip_current_turn_echo(
                "I see. Have a great move to Seattle!", "I see",
            ),
            "Have a great move to Seattle!",
        )

    def test_paraphrased_setup_before_dash_is_removed(self):
        self.assertEqual(
            ResponseQualityGuard.strip_current_turn_echo(
                "You're moving to Seattle on 9/13—pack your computer securely "
                "in its original box.",
                "Also I'm moving to Seattle in 9/13. I'm not sure how to bring "
                "my computer with me.",
            ),
            "Pack your computer securely in its original box.",
        )

    def test_an_answer_that_uses_question_words_is_not_removed(self):
        reply = "The capital of France is Paris."
        self.assertEqual(
            ResponseQualityGuard.strip_current_turn_echo(
                reply, "What's the capital of France?",
            ),
            reply,
        )

    def test_a_plain_hyphen_is_a_break_too(self):
        # The reported shape. Only em/en dashes and "--" were recognised,
        # and a single hyphen is what the model actually types, so the
        # echo went out verbatim: "I see" -> "I see- Have a wonderful day!"
        for reply in (
            "I see- Have a wonderful day!",
            "I see - have a wonderful day!",
            "I see -have a wonderful day!",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(
                    ResponseQualityGuard.strip_current_turn_echo(
                        reply, "I see",
                    ),
                    "Have a wonderful day!",
                )

    def test_a_one_word_restatement_is_still_a_restatement(self):
        # The old floor was two content words, and "I see" has one ("i" is
        # a stopword), so the whole reported case fell straight through it.
        for said, reply, expected in (
            ("okay", "Okay - let me know when you decide.",
             "Let me know when you decide."),
            ("thanks", "Thanks - anytime.", "Anytime."),
            ("I see", "I see, have a wonderful day!",
             "Have a wonderful day!"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    ResponseQualityGuard.strip_current_turn_echo(reply, said),
                    expected,
                )

    def test_a_one_word_prefix_that_carries_the_answer_survives(self):
        # The reason the two-word floor existed. Dropping it outright would
        # have deleted the answer here, so a single-word prefix is only an
        # echo when it accounts for the person's *whole* message.
        for said, reply in (
            ("should I use python or rust?",
             "Python - it has the better libraries for this."),
            ("is it seattle or portland?",
             "Seattle - it's closer to your new job."),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    ResponseQualityGuard.strip_current_turn_echo(reply, said),
                    reply,
                )

    def test_hyphenated_words_are_not_treated_as_breaks(self):
        for reply in (
            "That's a well-known state-of-the-art trick.",
            "Rent runs 1800-2400 a month there.",
        ):
            with self.subTest(reply=reply):
                self.assertEqual(
                    ResponseQualityGuard.strip_current_turn_echo(
                        reply, "that's a well-known state-of-the-art trick",
                    ),
                    reply,
                )

    def test_an_echo_hiding_behind_an_acknowledgement_is_removed(self):
        # Measured live, answering "not yet". The restatement is there, one
        # acknowledgement away from the start, so a prefix test anchored at
        # the very beginning walked past it.
        self.assertEqual(
            ResponseQualityGuard.strip_current_turn_echo(
                "Ah, got it. Not yet — that's totally normal! Seattle "
                "has many great neighborhoods.",
                "not yet",
            ),
            "That's totally normal! Seattle has many great neighborhoods.",
        )
        self.assertEqual(
            ResponseQualityGuard.strip_current_turn_echo(
                "Oh, got it. Not yet — that's totally fine. It's a big "
                "move.",
                "not yet",
            ),
            "That's totally fine. It's a big move.",
        )

    def test_a_one_word_answer_before_a_dash_is_never_deleted(self):
        # The regression the filler rule can cause if it asks only whether
        # the prefix came from the person's words: here it did, and it is
        # also the entire answer.
        for said, reply in (
            ("should I use python or rust?",
             "Python — it has the better libraries here."),
            ("is it cheaper downtown?",
             "Yes — rents run about 15% lower in Beacon Hill."),
            ("what should I pack?",
             "Got it — here's the plan for packing."),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    ResponseQualityGuard.strip_current_turn_echo(reply, said),
                    reply,
                )

    def test_an_ordinary_comma_clause_is_not_stripped(self):
        # A comma only counts under the whole-message rule; it is far too
        # common to strip on a partial match.
        reply = "Seattle, which you're moving to, has mild winters."
        self.assertEqual(
            ResponseQualityGuard.strip_current_turn_echo(
                reply, "what are the winters like where I'm moving",
            ),
            reply,
        )


class PureEchoTests(unittest.TestCase):
    """The whole reply is the message, handed back.

    Found in a clean live session: "I see" was answered "I see." Neither
    existing guard could see it -- there is no prefix to strip, because the
    echo *is* the reply, and it looks nothing like the previous answer.
    """

    def test_the_message_handed_straight_back(self):
        for said, reply in (
            ("I see", "I see."),
            ("not yet", "Not yet."),
            ("okay", "Okay!"),
            ("that sounds good", "That sounds good."),
        ):
            with self.subTest(said=said):
                self.assertTrue(
                    ResponseQualityGuard.is_pure_echo(reply, said)
                )

    def test_the_message_back_in_the_mirror(self):
        self.assertTrue(ResponseQualityGuard.is_pure_echo(
            "You're moving to Seattle!", "I'm moving to Seattle",
        ))

    def test_a_real_answer_is_never_a_pure_echo(self):
        for said, reply in (
            ("what's the capital of France?",
             "The capital of France is Paris."),
            ("I'm moving to Seattle",
             "That's exciting! Have you found a place yet?"),
            ("not yet", "No rush. Are you renting or buying?"),
            ("I see", "Let me know if you want help with the search."),
            ("when was OpenAI founded?",
             "OpenAI was founded in December 2015."),
        ):
            with self.subTest(said=said):
                self.assertFalse(
                    ResponseQualityGuard.is_pure_echo(reply, said)
                )

    def test_an_echo_behind_an_acknowledgement(self):
        # Live: "I see" -> "Got it. I see." The filler sentence in front
        # meant neither an exact match nor a first-sentence match fired.
        for said, reply in (
            ("I see", "Got it. I see."),
            ("not yet", "Okay, not yet."),
            ("I see", "Sure. I see!"),
        ):
            with self.subTest(reply=reply):
                self.assertTrue(
                    ResponseQualityGuard.is_pure_echo(reply, said)
                )

    def test_a_bare_acknowledgement_echoes_nothing(self):
        # "Got it." repeats none of the person's words, so discounting
        # filler must not turn it into an echo of whatever they said.
        for said, reply in (
            ("I see", "Got it."),
            ("I see", "Okay."),
            ("I'm moving to Seattle", "Sure thing."),
        ):
            with self.subTest(reply=reply):
                self.assertFalse(
                    ResponseQualityGuard.is_pure_echo(reply, said)
                )

    def test_an_empty_side_is_not_an_echo(self):
        self.assertFalse(ResponseQualityGuard.is_pure_echo("", "I see"))
        self.assertFalse(ResponseQualityGuard.is_pure_echo("I see.", ""))

    def test_a_long_reply_reusing_the_words_is_not_an_echo(self):
        self.assertFalse(ResponseQualityGuard.is_pure_echo(
            "Moving to Seattle in September means you'll land right at the "
            "start of the rainy season, so pack accordingly.",
            "I'm moving to Seattle",
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


class OrdinaryReplyRepeatTests(unittest.TestCase):
    """The reported conversation, and the allowlist that let it through.

        "I'm moving to seattle"
        "That's exciting! Seattle's a great place to start something new.
         Have you figured out where you'll be staying?"
        "not yet"
        "That's exciting! Seattle's a great place to start something new;
         Have you figured out where you'll be staying."

    The guard's shape-of-turn rules were an allowlist, so "not yet" -- which
    is neither a listed reaction word, nor a correction, nor a request for
    reasons -- was allowed to receive the identical answer. Measured against
    twenty-four ordinary replies to that question, eighteen got it back.
    """

    ANSWER = (
        "That's exciting! Seattle's a great place to start something new. "
        "Have you figured out where you'll be staying?"
    )
    NEARLY = (
        "That's exciting! Seattle's a great place to start something new; "
        "Have you figured out where you'll be staying."
    )
    HISTORY = [
        {"role": "user", "content": "I'm moving to seattle"},
        {"role": "assistant", "content": ANSWER},
    ]

    def test_the_reported_turn_is_caught(self):
        self.assertTrue(ResponseQualityGuard.should_retry(
            self.NEARLY, "not yet", self.HISTORY,
        ))

    def test_ordinary_replies_do_not_deserve_the_same_answer_again(self):
        for said in (
            "nothing yet", "not really", "haven't decided", "still looking",
            "idk", "um", "we'll see", "probably an apartment",
            "my company is paying", "it's for work", "in september",
            "true", "for sure", "lol", "that's fair", "makes sense",
        ):
            with self.subTest(said=said):
                self.assertTrue(ResponseQualityGuard.should_retry(
                    self.NEARLY, said, self.HISTORY,
                ))

    def test_a_turn_that_asks_for_something_may_still_repeat(self):
        # The exception the inverted rule keeps: an elliptical request
        # genuinely wants the same words back.
        for said in (
            "say that again", "what did you say", "repeat that",
            "where should I stay?", "tell me the options again",
        ):
            with self.subTest(said=said):
                self.assertFalse(ResponseQualityGuard.should_retry(
                    self.NEARLY, said, self.HISTORY,
                ))

    def test_repeating_the_same_message_still_allows_the_same_answer(self):
        self.assertFalse(ResponseQualityGuard.should_retry(
            self.NEARLY, "I'm moving to seattle", self.HISTORY,
        ))

    def test_a_genuinely_different_answer_is_never_flagged(self):
        self.assertFalse(ResponseQualityGuard.should_retry(
            "No rush. Are you renting or buying?", "not yet", self.HISTORY,
        ))

    def test_an_answer_repeated_two_turns_later_is_caught(self):
        # A plain similarity ratio scored this 0.85 -- under the gate --
        # because the earlier answer had an extra opening clause, and the
        # extra words counted against the score. As a share of the shorter
        # text, every word of it had already been said.
        history = [
            {"role": "user", "content": "I'm moving to seattle"},
            {
                "role": "assistant",
                "content": (
                    "That's exciting! Seattle's a great place to start "
                    "something new."
                ),
            },
            {"role": "user", "content": "not yet"},
            {
                "role": "assistant",
                "content": "Cool, Seattle's a great place to move!",
            },
        ]

        self.assertTrue(ResponseQualityGuard.should_retry(
            "Seattle's a great place to start something new.",
            "I see",
            history,
        ))

    def test_a_short_reply_is_not_judged_by_containment(self):
        # Almost any brief line is "contained in" a long previous answer, so
        # containment is only consulted once both sides are sentence-sized.
        history = [
            {"role": "user", "content": "I'm moving to seattle"},
            {
                "role": "assistant",
                "content": (
                    "That's exciting! Seattle's a great place to start "
                    "something new."
                ),
            },
        ]

        self.assertFalse(ResponseQualityGuard.should_retry(
            "Sure.", "I see", history,
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
            self.assertIn("restating or paraphrasing", prompt)


if __name__ == "__main__":
    unittest.main()
