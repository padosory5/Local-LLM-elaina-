"""The voice setting (brain/voice.py): talking as an assistant, or as a person.

What is pinned: how the setting is read; that the person voice is a file
beside the persona, chosen per language, so Korean is untouched; that every
rule outside the voice -- who she is, advice, tools, images, boundaries,
safety -- is word for word the assistant's; that the person file stays
parallel to the Korean one; the explanation goal in each voice; and, through
whole turns, that the prompt she is handed changes only when the setting
says so.
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest import mock

from brain import response_budget as rb
from brain import voice
from brain.personality_loader import PersonalityLoader


class _Config:
    def __init__(self, value=None):
        self.value = value

    def get(self, section, key, default=None, required=False):
        return default if self.value is None else self.value


class SettingTests(unittest.TestCase):

    def setUp(self):
        patched = mock.patch.dict(os.environ, {}, clear=False)
        patched.start()
        self.addCleanup(patched.stop)
        os.environ.pop(voice.ENV, None)

    def test_unset_is_the_assistant(self):
        self.assertEqual(voice.configured(_Config()), voice.ASSISTANT)

    def test_the_config_chooses(self):
        self.assertEqual(voice.configured(_Config("person")), voice.PERSON)
        self.assertEqual(voice.configured(_Config(" Person ")), voice.PERSON)

    def test_the_environment_overrides_the_config(self):
        os.environ[voice.ENV] = "person"
        self.assertEqual(voice.configured(_Config("assistant")), voice.PERSON)

    def test_an_unknown_voice_is_said_out_loud_and_read_as_the_assistant(self):
        with contextlib.redirect_stdout(io.StringIO()) as said:
            self.assertEqual(voice.configured(_Config("pirate")), voice.ASSISTANT)
        self.assertIn("pirate", said.getvalue())

    def test_the_shipped_config_keeps_the_assistant_until_chosen(self):
        from config.loader import Config

        self.assertEqual(voice.configured(Config()), voice.ASSISTANT)


class FileTests(unittest.TestCase):

    # Every section but these is word for word the assistant's.
    REWRITTEN = {"VOICE", "CONVERSATION", "KNOWLEDGE AND CALCULATIONS"}

    def setUp(self):
        self.loader = PersonalityLoader()

    def test_the_person_voice_is_its_own_file_in_english(self):
        person = self.loader.load("en", voice.PERSON)
        assistant = self.loader.load("en")
        self.assertNotEqual(person, assistant)
        self.assertTrue(person.startswith("You are Elaina, the user's assistant. Capable, warm"))
        self.assertIn("Speak like a professional assistant", assistant)
        self.assertNotIn("professional assistant", person)
        self.assertNotIn("never familiar", person)

    def test_korean_keeps_its_own_file(self):
        self.assertEqual(self.loader.load("ko", voice.PERSON), self.loader.load("ko"))

    def test_the_rules_outside_the_voice_are_the_assistants_word_for_word(self):
        person = self.loader.sections("en", voice.PERSON)
        assistant = self.loader.sections("en")
        self.assertEqual(list(person), list(assistant))
        for section, rules in assistant.items():
            if section in self.REWRITTEN:
                continue
            with self.subTest(section=section):
                self.assertEqual(person[section], rules)

    def test_the_rewritten_sections_keep_their_rule_count_and_place(self):
        person = self.loader.sections("en", voice.PERSON)
        assistant = self.loader.sections("en")
        for section in self.REWRITTEN:
            with self.subTest(section=section):
                self.assertEqual(len(person[section]), len(assistant[section]))
        # The explanation contract drops rules by position (brain/explain_contract.py).
        self.assertTrue(person["VOICE"][4].startswith("Say it the short way first"))
        self.assertTrue(person["KNOWLEDGE AND CALCULATIONS"][0].startswith("Lead with the answer."))
        self.assertTrue(person["KNOWLEDGE AND CALCULATIONS"][3].startswith("When you explain"))

    def test_it_stays_parallel_to_the_korean_file(self):
        person = self.loader.sections("en", voice.PERSON)
        korean = self.loader.sections("ko")
        self.assertEqual(list(person), list(korean))
        for section, rules in person.items():
            with self.subTest(section=section):
                self.assertEqual(len(rules), len(korean[section]))
        self.assertEqual(self.loader.example_count("en", voice.PERSON), self.loader.example_count("ko"))

    def test_the_safety_example_is_kept(self):
        person = self.loader.load("en", voice.PERSON)
        self.assertIn("I am not going to move past that.", person)
        self.assertIn("a person you trust or a crisis line", person)

    def test_a_check_of_understanding_starts_with_the_verdict(self):
        rule = self.loader.sections("en", voice.PERSON)["KNOWLEDGE AND CALCULATIONS"][3]
        self.assertIn("start with yes, partly or no", rule)


class GoalTests(unittest.TestCase):

    def budget(self, shape):
        return rb.ResponseBudget(kind="explain", max_words=130, max_sentences=6, shape=shape)

    def test_an_english_explanation_is_asked_for_the_way_a_friend_gives_one(self):
        goal = self.budget(rb.EXPLAIN_ANSWER).goal("en", voice=voice.PERSON)
        self.assertTrue(goal.startswith(rb.EXPLAIN_LIKE_A_FRIEND))
        self.assertNotIn("worked example", goal)
        # Checking their understanding still comes after it.
        self.assertIn(rb.CHECKING_UNDERSTANDING[0], goal)

    def test_the_assistant_keeps_its_goal(self):
        for chosen in ("", voice.ASSISTANT):
            with self.subTest(voice=chosen):
                self.assertIn("Explain it so it lands", self.budget(rb.EXPLAIN_ANSWER).goal("en", voice=chosen))

    def test_korean_and_the_other_shapes_are_unchanged(self):
        explain = self.budget(rb.EXPLAIN_ANSWER)
        self.assertEqual(explain.goal("ko", voice=voice.PERSON), explain.goal("ko"))
        for shape in (rb.VALUE_ANSWER, rb.STATE, rb.EXAMPLE, rb.ELABORATE_ANSWER, rb.OPEN):
            with self.subTest(shape=shape):
                budget = self.budget(shape)
                self.assertEqual(budget.goal("en", voice=voice.PERSON), budget.goal("en"))

    def test_the_voice_names_agree(self):
        self.assertEqual(rb.PERSON_VOICE, voice.PERSON)


class WholeTurnTests(unittest.TestCase):

    ROUTE = {"intent": "knowledge_question", "confidence": 1.0, "speech_act": "information_request",
             "answer_shape": "explain", "topic": "compound interest", "information_freshness": "stable",
             "requires_external_evidence": False, "time_scope": "timeless",
             "normalized_request": "How does compound interest work?"}
    ASKED = "How does compound interest work?"

    def _draft(self, chosen):
        from core import turn_trace
        from tests.turn_harness import build_engine, drafting_parts, drafting_prompts

        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({self.ASKED: self.ROUTE})
        self.addCleanup(engine.close)
        self.assertEqual(engine._voice, voice.ASSISTANT)    # the shipped config
        engine._voice = chosen
        engine.client.reply = "Think of it as interest earning interest."
        with contextlib.redirect_stdout(io.StringIO()):
            engine.chat(self.ASKED)
        drafts = drafting_prompts(engine.client.prompts)
        self.assertTrue(drafts)
        persona, rules, _last = drafting_parts(drafts[0])
        return persona, rules, turn_trace.last().as_record()["context"]

    def test_the_person_voice_reaches_the_writer(self):
        persona, rules, context = self._draft(voice.PERSON)
        self.assertTrue(persona.startswith("You are Elaina, the user's assistant. Capable, warm"))
        self.assertIn(rb.EXPLAIN_LIKE_A_FRIEND, rules)
        self.assertNotIn("Explain it so it lands", rules)
        self.assertEqual(context["voice"], voice.PERSON)

    def test_the_assistant_voice_is_what_it_always_was(self):
        persona, rules, context = self._draft(voice.ASSISTANT)
        self.assertTrue(persona.startswith("You are Elaina, the user's assistant. Capable, direct"))
        self.assertIn("Explain it so it lands", rules)
        self.assertNotIn("voice", context)


if __name__ == "__main__":
    unittest.main()
