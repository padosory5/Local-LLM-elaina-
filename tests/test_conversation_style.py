"""How a reply sounds, and the line between repairing it and re-saying it.

Every case here comes from a measured live turn or from the rule that turn
exposed. The module under test is the one the running system consults
before it speaks *and* the one the quality report scores with, so these
tests are the definition both of them share.
"""

import unittest

from brain import conversation_style as style
from brain.conversation_style import (
    ANSWER,
    ASK,
    CLOSE,
    CONFIRM,
    GREET,
    REACT,
    RECEIPT,
    REPORT,
    RoboticTells,
    act_for_turn,
    contract_for,
    reads_as_receipt,
    repair_structure,
    review,
    speakable_fragment,
    speakable_list,
    style_instruction,
)


def classes(text, **kwargs):
    return {finding.failure for finding in RoboticTells.inspect(text, **kwargs)}


class StyleContractTests(unittest.TestCase):

    def test_every_act_has_a_contract(self):
        for act in style.ACTS:
            self.assertIn(act, style.CONTRACTS)

    def test_an_unknown_act_is_treated_as_an_ordinary_answer(self):
        # Never as unconstrained. A contract that does not exist must not
        # become permission to say anything at any length.
        self.assertEqual(contract_for("nonsense").act, ANSWER)
        self.assertEqual(contract_for("").act, ANSWER)

    def test_a_question_waiting_for_an_answer_may_not_be_reworded(self):
        # The consent classifier reads the reply against the exact words of
        # the question. A reworded question is a different question.
        self.assertFalse(contract_for(ASK).may_reword)
        self.assertFalse(contract_for(CONFIRM).may_reword)

    def test_the_social_acts_may_be_reworded(self):
        for act in (GREET, RECEIPT, ANSWER, REPORT, REACT, CLOSE):
            self.assertTrue(contract_for(act).may_reword, act)

    def test_a_receipt_is_short_and_offers_nothing(self):
        # Bounded in words, not sentences: two clauses is what a person
        # says, and the length that makes a receipt sound like an
        # assistant is measured in words.
        contract = contract_for(RECEIPT)
        self.assertEqual(contract.max_words, 20)
        self.assertEqual(contract.offers_allowed, 0)

    def test_a_greeting_may_not_advertise(self):
        self.assertEqual(contract_for(GREET).offers_allowed, 0)


class ReceiptRecognitionTests(unittest.TestCase):

    def test_bare_acknowledgements_are_receipts(self):
        for said in ("ok", "thanks", "yeah", "nah", "no", "lol", "cool",
                     "that's fine, thanks", "ok i'll try that"):
            self.assertTrue(reads_as_receipt(said), said)

    def test_a_turn_carrying_anything_else_is_not_a_receipt(self):
        # The closed-class test: it is not that some phrase is banned, it
        # is that a word outside the set is present.
        for said in ("ok but which one", "find me a mouse", "morning",
                     "hmm what else is there", "sounds good",
                     "no, the other one"):
            self.assertFalse(reads_as_receipt(said), said)

    def test_a_question_is_never_a_receipt(self):
        # Whatever else it is doing, a turn that asks wants an answer, and
        # the one clause a receipt allows is the wrong kind of brief.
        self.assertFalse(reads_as_receipt("ok?"))
        self.assertFalse(reads_as_receipt("yeah?"))

    def test_korean_acknowledgements_are_receipts(self):
        for said in ("응", "네", "고마워", "알겠어"):
            self.assertTrue(reads_as_receipt(said), said)


class ActClassificationTests(unittest.TestCase):

    def test_a_waiting_question_outranks_everything(self):
        # Ordering is the design. A turn waiting for a missing detail must
        # never be reclassified into an act a rewrite may touch.
        self.assertEqual(
            act_for_turn(
                awaiting_clarification=True,
                user_input="ok",
                action_performed=True,
                is_greeting=True,
            ),
            ASK,
        )

    def test_a_waiting_consent_question_outranks_the_rest(self):
        self.assertEqual(
            act_for_turn(
                awaiting_consent=True, user_input="ok", action_performed=True,
            ),
            CONFIRM,
        )

    def test_an_acknowledgement_is_a_receipt(self):
        self.assertEqual(act_for_turn(user_input="thanks"), RECEIPT)

    def test_something_that_happened_on_the_machine_is_a_report(self):
        self.assertEqual(
            act_for_turn(user_input="open notepad", action_performed=True),
            REPORT,
        )
        self.assertEqual(
            act_for_turn(user_input="what's the weather", has_tool_result=True),
            REPORT,
        )

    def test_a_remark_about_feeling_is_a_reaction(self):
        self.assertEqual(
            act_for_turn(speech_act="social", user_input="i had a rough night"),
            REACT,
        )

    def test_anything_else_is_an_answer(self):
        self.assertEqual(
            act_for_turn(intent="knowledge_question",
                         user_input="how long should cold brew steep?"),
            ANSWER,
        )


class FailureClassTests(unittest.TestCase):

    def test_customer_service_phrasing(self):
        for said in (
            "Is there anything else I can help you with?",
            "I'd be happy to assist you with that.",
            "Please let me know if you need anything.",
            "Let me know what you need.",
            "Thank you for your patience.",
        ):
            self.assertIn(style.SERVICE_PHRASING, classes(said), said)

    def test_a_closing_line_is_filler_in_the_middle_of_a_reply_too(self):
        # Measured live and missed by a tail-only rule: the filler had a
        # real sentence after it, so nothing ever looked at it.
        said = ("Let me know if you need help with anything else. "
                "Enjoy your cold brew!")
        self.assertIn(style.SERVICE_PHRASING, classes(said, act=RECEIPT))

    def test_restating_the_request_back(self):
        self.assertIn(
            style.REQUEST_RESTATED,
            classes("So, you're back from work? How's everything going?",
                    act=REACT),
        )

    def test_a_genuine_clarifying_question_is_not_a_restatement(self):
        self.assertNotIn(
            style.REQUEST_RESTATED,
            classes("Do you mean the 27 inch one?", act=ASK),
        )

    def test_tool_narration(self):
        for said in (
            "The search returned three hotels.",
            "Based on the results I found, it's about 12 hours.",
            "I ran a search and found a few options.",
            "The browser reported that the page had loaded.",
        ):
            self.assertIn(style.TOOL_NARRATION, classes(said, act=REPORT), said)

    def test_unnatural_confirmation(self):
        for said in ("Action completed successfully.",
                     "The operation was successful.",
                     "Successfully opened Notepad."):
            self.assertIn(
                style.UNNATURAL_CONFIRMATION, classes(said, act=REPORT), said)

    def test_internal_identifiers_never_belong_in_speech(self):
        # Live: the offer is appended *after* the speech filter runs, so
        # nothing turned the underscore back into a space.
        found = RoboticTells.inspect(
            "Happy to dig into a product_recommendation if that helps.")
        self.assertIn(style.INTERNAL_LANGUAGE,
                      {finding.failure for finding in found})
        self.assertTrue(
            any(finding.structural for finding in found
                if finding.failure == style.INTERNAL_LANGUAGE)
        )

    def test_an_accessibility_handle_is_internal_language(self):
        self.assertIn(
            style.INTERNAL_LANGUAGE,
            classes("Button: Minimise [id=6e663719-e0]", act=REPORT),
        )

    def test_a_catalogue_read_out_is_a_recital(self):
        said = ("Right now I can use browser control, web search, desktop "
                "control, screen vision, multi-step tasks, memory, calendar, "
                "project access.")
        self.assertIn(style.LIST_RECITAL, classes(said))

    def test_an_ordinary_three_item_list_is_not_a_recital(self):
        # The threshold has to leave normal English alone: this is how
        # anyone describes a product, and condemning it would make the
        # instrument disagree with speech.
        said = ("The Logitech M330 is a great option under $50, it's "
                "ultra-light, has a long battery life, and works with both "
                "Windows and macOS.")
        self.assertNotIn(style.LIST_RECITAL, classes(said))

    def test_a_menu_of_options_is_a_recital(self):
        said = ("Let me know what you need, whether it's someone to talk to, "
                "a distraction, or just a quiet moment.")
        self.assertIn(style.LIST_RECITAL, classes(said, act=REACT))

    def test_a_receipt_may_be_a_stock_line_but_not_twice(self):
        # "Got it." answering "ok" is what a person says. Saying it again
        # later in the same session is the tell that there is a bank
        # behind it.
        self.assertNotIn(
            style.ROBOTIC_ACKNOWLEDGEMENT, classes("Got it.", act=RECEIPT))
        self.assertIn(
            style.SELF_REPETITION,
            classes("Sure thing.", act=RECEIPT,
                    earlier_replies=("Sure thing.", "Anything at all.")),
        )

    def test_a_stock_line_where_an_answer_was_owed_is_a_failure(self):
        self.assertIn(
            style.ROBOTIC_ACKNOWLEDGEMENT, classes("Understood.", act=ANSWER))

    def test_a_whole_sentence_carried_over_is_repetition(self):
        # A canned caution arriving in two consecutive answers.
        caution = "Check the price again before buying to make sure it's in range."
        self.assertIn(
            style.SELF_REPETITION,
            classes(f"The M330 is a solid pick. {caution}", act=ANSWER,
                    previous_reply=f"The Orochi is light. {caution}"),
        )

    def test_length_is_judged_against_the_act(self):
        long_receipt = (
            "Alright, then maybe we'll find something else. Just let me know "
            "when you're ready to chill and I'll suggest something fun."
        )
        self.assertIn(style.TOO_VERBOSE, classes(long_receipt, act=RECEIPT))
        self.assertNotIn(style.TOO_VERBOSE, classes(long_receipt, act=ANSWER))

    def test_two_short_clauses_are_not_too_much_for_a_receipt(self):
        # The correction that mattered most. Counting sentences called
        # this a failure and then rejected every rewrite offered for a
        # real one, because a good short reply is usually two clauses too.
        self.assertNotIn(
            style.TOO_VERBOSE,
            classes("I'm here. You don't have to talk if you don't want to.",
                    act=RECEIPT),
        )

    def test_an_offer_is_counted_against_what_the_act_allows(self):
        said = "Want me to look it up? I can also check the reviews."
        self.assertIn(style.DUPLICATE_OFFER, classes(said, act=ANSWER))
        self.assertIn(style.DUPLICATE_OFFER, classes("Want me to check?",
                                                     act=RECEIPT))

    def test_a_content_free_reply_is_a_failure_except_as_a_receipt(self):
        self.assertIn(style.EMPTY_RESPONSE, classes("All set.", act=REPORT))
        self.assertNotIn(style.EMPTY_RESPONSE, classes("All set.", act=RECEIPT))

    def test_a_clean_reply_has_no_findings(self):
        for said in (
            "It's about ten in the morning there.",
            "Cold brew wants twelve to twenty-four hours in the fridge.",
            "Ah, that's rough. Did you get any sleep at all?",
        ):
            self.assertEqual(classes(said, act=ANSWER), set(), said)


class StructuralRepairTests(unittest.TestCase):

    def test_a_log_marker_is_removed(self):
        self.assertEqual(
            repair_structure("[Task Planner] I opened the page."),
            "I opened the page.",
        )

    def test_a_role_label_is_removed(self):
        self.assertEqual(repair_structure("assistant: Sure."), "Sure.")

    def test_a_semicolon_chain_becomes_sentences(self):
        # Live: merge_extra_sentences hit its target by stitching clauses
        # together, and produced something no one would say.
        said = ("How about The Royal Tenenbaums; It's quirky and heartfelt; "
                "Perfect for a relaxed night in.")
        repaired = repair_structure(said)
        self.assertNotIn(";", repaired)
        self.assertIn("How about The Royal Tenenbaums.", repaired)

    def test_one_semicolon_before_a_capital_is_a_lost_full_stop(self):
        # Live: "just the right amount of light; Just make sure to keep the
        # volume down". A single semicolon is legitimate punctuation, so
        # the capital letter after it is what says this one is not.
        repaired = repair_structure(
            "The Crown is a great pick, it's dramatic and light; Just keep "
            "the volume down."
        )
        self.assertNotIn(";", repaired)
        self.assertIn("light. Just keep", repaired)

    def test_a_real_semicolon_is_left_alone(self):
        said = "It rained all week; the roads were awful."
        self.assertEqual(repair_structure(said), said)

    def test_a_stray_quote_is_closed(self):
        repaired = repair_structure('Perfect for a relaxed night in.".')
        self.assertNotIn('"', repaired)

    def test_a_missing_full_stop_is_added(self):
        self.assertEqual(repair_structure("It's ten in the morning"),
                         "It's ten in the morning.")

    def test_register_is_never_repaired(self):
        # The whole separation. Cutting a customer-service sentence out of
        # a customer-service reply leaves a shorter one that sounds the
        # same; the fix for a register is to say it again.
        said = "I'd be happy to assist you with that."
        self.assertEqual(repair_structure(said), said)

    def test_a_verdict_separates_repair_from_rewrite(self):
        verdict = review("[Router] I'd be happy to assist you.", act=ANSWER)
        self.assertNotIn("[Router]", verdict.repaired)
        self.assertTrue(verdict.needs_rewrite)

    def test_a_locked_act_still_reports_its_findings(self):
        # It is not rewritten, but the failure is not swallowed either: a
        # consent question that keeps reading as a form is a defect in
        # whoever writes it, and it should be visible.
        verdict = review("Please confirm whether you would like me to "
                         "proceed.", act=CONFIRM)
        self.assertTrue(verdict.findings)
        self.assertFalse(contract_for(CONFIRM).may_reword)


class LengthIsStyleForSocialActsTests(unittest.TestCase):
    """Whether running long is worth saying again depends on the act.

    Saying "yeah, rough one" instead of three sentences of sympathy loses
    nothing. Shortening an answer or a tool report risks dropping the value
    the person asked for, which belongs to the condenser and its own
    faithfulness rules -- not to a style rewrite.
    """

    def test_a_long_reaction_is_worth_saying_again(self):
        verdict = review(
            "That sounds rough. I hope you get some rest. Let me know how "
            "it goes.",
            act=REACT,
        )
        self.assertIn(style.TOO_VERBOSE, verdict.classes)
        self.assertTrue(verdict.needs_rewrite)

    def test_a_long_answer_is_not_rewritten_for_length_alone(self):
        verdict = review(
            "It steeps for twelve hours. Longer makes it stronger. Keep it "
            "cold. Strain it before you drink it. Then dilute to taste.",
            act=ANSWER,
        )
        self.assertIn(style.TOO_VERBOSE, verdict.classes)
        self.assertFalse(verdict.needs_rewrite)


class SpeakableFragmentTests(unittest.TestCase):

    def test_a_step_record_is_not_speakable(self):
        self.assertEqual(
            speakable_fragment(
                "Window: ChatGPT Button: Minimise [id=6e663719-e0]"),
            "",
        )

    def test_an_ordinary_step_is_speakable(self):
        self.assertEqual(speakable_fragment("Opened Spotify"), "Opened Spotify")

    def test_a_list_keeps_only_what_can_be_said(self):
        self.assertEqual(
            speakable_list((
                "Opened Spotify",
                "Pane: ChatGPT [id=6e663719-e3]",
            )),
            "Opened Spotify",
        )

    def test_nothing_speakable_gives_nothing(self):
        self.assertEqual(speakable_list(("Pane: X [id=1]",)), "")


class StyleInstructionTests(unittest.TestCase):

    def test_the_instruction_names_the_length_the_act_allows(self):
        # Words for the acts bounded in words, sentences for the rest.
        self.assertIn("At most 20 words", style_instruction(RECEIPT))
        self.assertIn("At most 4 sentences", style_instruction(ANSWER))

    def test_an_act_that_may_not_offer_is_told_not_to(self):
        self.assertIn("Do not offer", style_instruction(GREET))
        self.assertIn("Do not offer", style_instruction(RECEIPT))
        self.assertNotIn("Do not offer", style_instruction(ANSWER))

    def test_the_report_instruction_protects_the_values(self):
        text = style_instruction(REPORT)
        self.assertIn("exactly as given", text)
        self.assertIn("never mention searches, tools", text)

    def test_every_instruction_forbids_the_service_register(self):
        for act in style.ACTS:
            self.assertIn("customer-service", style_instruction(act))


if __name__ == "__main__":
    unittest.main()


class ReceiptsAreExemptFromTheAnswerRepetitionGuardTests(unittest.TestCase):
    """A receipt is not an answer, so it is not judged as one.

    Measured live, twice in one eight-turn conversation:

        User:   yeah
        Elaina: Sorry, I answered the wrong thing there. Say it once more
                and I'll take it properly?

    Two bare acknowledgements in a row legitimately get two short, similar
    replies -- that is what receipting looks like. The answer-repetition
    guard read the similarity as her repeating herself, retried, got
    another short reply, and gave up with an apology asking the person to
    repeat a word there was no point repeating.

    Repetition in receipts is still a real fault. It is caught where it is
    actually visible -- across the session -- by this module.
    """

    def test_the_engine_skips_the_repetition_retry_for_a_receipt(self):
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine._final_response_check)
        guard_at = source.index("conversation_style.RECEIPT")
        retry_at = source.index("strip_current_turn_echo")

        self.assertLess(
            guard_at, retry_at,
            "the receipt exemption must come before the echo check, or a "
            "bare 'yeah' is judged as a repeated answer",
        )

    def test_session_repetition_is_still_caught_for_receipts(self):
        self.assertIn(
            style.SELF_REPETITION,
            classes("Sure thing.", act=RECEIPT,
                    earlier_replies=("Sure thing.",)),
        )


class ThePatternsAreWhatTheyLookLikeTests(unittest.TestCase):
    """A regex source line with a control character in it is not a regex.

    Written after losing an hour to it: a pattern edited through a shell
    heredoc arrived with a literal backspace where its word boundary
    should have been. The module imported, every test passed, and two
    detectors silently matched nothing at all -- which is the worst
    possible failure for an instrument, because a quiet detector and a
    clean conversation look identical in the report.
    """

    def test_no_control_characters_survive_in_the_source(self):
        from pathlib import Path

        source = Path(style.__file__).read_text(encoding="utf-8")
        stray = [
            (source[:index].count("\n") + 1, hex(ord(character)))
            for index, character in enumerate(source)
            if ord(character) < 32 and character not in "\n\t"
        ]

        self.assertEqual(stray, [])

    def test_every_pattern_group_matches_something(self):
        # The other half of the same guarantee: a group that matches
        # nothing is indistinguishable from a group that is never needed.
        for said, expected in (
            ("Is there anything else I can help you with?",
             style.SERVICE_PHRASING),
            ("Understood.", style.ROBOTIC_ACKNOWLEDGEMENT),
            ("So, you're asking about the price?", style.REQUEST_RESTATED),
            ("Shall I proceed?", style.STIFF_FOLLOWUP),
            ("As per your earlier request, here it is.",
             style.REACTIVATION_AWKWARD),
            ("The search returned nothing.", style.TOOL_NARRATION),
            ("[Task Planner] Opened it.", style.INTERNAL_LANGUAGE),
            ("The calculation is done.", style.UNNATURAL_CONFIRMATION),
        ):
            with self.subTest(said=said):
                self.assertIn(expected, classes(said, act=ANSWER), said)


class RestatingIsARelationNotAFrameTests(unittest.TestCase):
    """The pattern list can only catch the frames somebody thought of.

    "So, you're back from work?" was in it. "So, just got back from work
    too?" was not, and is the same failure said differently. What the two
    have in common is not their wording -- it is that they give the
    person's own words back and add nothing.
    """

    def test_a_frame_nobody_listed_is_still_a_restatement(self):
        self.assertIn(
            style.REQUEST_RESTATED,
            classes("So, just got back from work too? How's the rest going?",
                    act=REACT,
                    user_input="not much, just got back from work"),
        )

    def test_an_answer_may_name_what_it_is_about(self):
        # Repeating three of the person's words and then saying something
        # is how answering works.
        self.assertNotIn(
            style.REQUEST_RESTATED,
            classes("I found a good wireless mouse under $50, the Logitech "
                    "G305 LIGHTSPEED.",
                    act=ANSWER,
                    user_input="find me a good wireless mouse under 50 dollars"),
        )

    def test_a_short_turn_cannot_be_restated(self):
        # Two content words is a coincidence in any conversation about one
        # subject, so the rule stands aside rather than guessing.
        self.assertNotIn(
            style.REQUEST_RESTATED,
            classes("Cold brew steeps for twelve hours.", act=ANSWER,
                    user_input="how long should it steep?"),
        )


class InstrumentCorrectionsTests(unittest.TestCase):
    """Three places the instrument was wrong about ordinary speech.

    Found by reading the turns it condemned rather than the ones it let
    through. A detector that flags normal English is not strict, it is
    broken -- every false positive it reports is a model call the system
    spends making a reply worse.
    """

    def test_mixed_doubled_punctuation_is_repaired(self):
        # "!." -- two different marks, which a repeated-character rule
        # left alone.
        self.assertEqual(
            repair_structure("Just open Netflix and start watching!."),
            "Just open Netflix and start watching!",
        )

    def test_an_ellipsis_survives(self):
        self.assertEqual(repair_structure("Wait... really?"),
                         "Wait... really?")

    def test_sympathy_is_not_a_restatement(self):
        # "I hear you." is one of the most human things she says.
        self.assertNotIn(
            style.REQUEST_RESTATED,
            classes("I hear you.", act=RECEIPT, user_input="no"),
        )

    def test_giving_the_request_back_still_is_one(self):
        self.assertIn(
            style.REQUEST_RESTATED,
            classes("I hear you're looking for a monitor.", act=ANSWER),
        )

    def test_a_goodbye_may_be_two_short_clauses(self):
        self.assertNotIn(
            style.TOO_VERBOSE,
            classes("Alright, take care. See you later.", act=CLOSE),
        )


class OneVoiceMeansOneCasingTests(unittest.TestCase):
    """A whole arc came back lowercase while the rest of the session was not.

    Texting register from the model, inconsistent with every other reply in
    the same conversation. One voice is the point, so the first letter is
    repaired -- structural, like a missing full stop.
    """

    def test_a_lowercase_opening_is_capitalised(self):
        self.assertEqual(
            repair_structure("i'm here. you don't have to talk."),
            "I'm here. you don't have to talk.",
        )

    def test_a_deliberately_cased_word_is_left_alone(self):
        self.assertEqual(repair_structure("iPhone 15 is the newer one."),
                         "iPhone 15 is the newer one.")
