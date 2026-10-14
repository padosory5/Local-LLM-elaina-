"""On a knowledge question or a search, the last thing the writer reads is what the person said.

``build_personality_messages`` put every rule for the turn into one final
message and named the question inside it as the router's reading. On the
English simulated learners (2026-10-13, runtime/evals/sl/tw4_report.md) the
8B answered the reading: a correction ("dividing -1 by 2 is -0.5, not
-128") arrived as a fresh request for the process, and the wrong example
came back. ``their_words_last`` keeps every rule and moves it ahead of the
conversation (brain/response_messages.py). Blind, on 56 recorded follow-ups,
that was the more helpful reply 41 to 15.

The first half pins the function; the second runs whole turns through the
harness, so what is pinned is the prompt the model is really handed.
"""

from __future__ import annotations

import contextlib
import copy
import io
import unittest

from brain.response_messages import (
    CURRENT_MESSAGE,
    MESSAGE_TO_ANSWER,
    THEIR_WORDS_ROUTES,
    build_personality_messages,
    their_words_last,
)

PERSONA = "You are Elaina, the user's assistant."
SAID = "Wait, dividing -1 by 2 is -0.5, not -128. What is actually happening to the number?"
READING = "What is the actual process of converting a float to an 8-bit integer?"
HISTORY = [
    {"role": "user", "content": "How does model quantization work?"},
    {"role": "assistant", "content": "It stores each weight in fewer bits."},
]
VOICE = "\n\nVOICE RESPONSE REQUIREMENTS\nKeep it to 130 spoken words.\n\nHOW TO SOUND\nLead with the answer."


def built(evidence: str = "", said_as: str = READING, language: str = "en") -> list[dict]:
    """The drafting prompt as ``_answer_turn`` finishes it today."""
    sections = [("CURRENTLY AVAILABLE AI AGENTS", "- Web Search: finds current pages.")]
    if evidence:
        sections.insert(0, ("CURRENT RETRIEVED EVIDENCE", evidence))
    messages = build_personality_messages(
        system_prompt=PERSONA, history=copy.deepcopy(HISTORY), user_input=said_as,
        context_sections=sections, response_language=language,
    )
    messages[-1]["content"] += VOICE
    return messages


class TheFunctionTests(unittest.TestCase):

    def test_the_last_message_is_what_they_said(self):
        messages, moved = their_words_last(built(), said=SAID, reading=READING)
        self.assertTrue(moved)
        self.assertEqual(messages[-1], {"role": "user", "content": SAID})

    def test_every_rule_is_kept_and_moved_ahead_of_the_conversation(self):
        before = built()
        rules = before[-1]["content"]
        messages, _moved = their_words_last(before, said=SAID, reading=READING)
        system = messages[0]["content"]
        self.assertTrue(system.startswith(PERSONA))
        for rule in ("CURRENTLY AVAILABLE AI AGENTS", "- Web Search: finds current pages.", "ANSWER LANGUAGE",
                     "Write the entire reply in English", "Answer this current message.",
                     "Do not repeat the previous answer merely because it appears in history.",
                     "VOICE RESPONSE REQUIREMENTS", "Keep it to 130 spoken words.", "HOW TO SOUND",
                     "Lead with the answer."):
            self.assertIn(rule, rules)
            self.assertIn(rule, system, rule)
        # In the order they were built, and the conversation between them and the words.
        self.assertLess(system.index("ANSWER LANGUAGE"), system.index(MESSAGE_TO_ANSWER))
        self.assertLess(system.index(MESSAGE_TO_ANSWER), system.index("VOICE RESPONSE REQUIREMENTS"))
        self.assertEqual(messages[1:-1], HISTORY)
        self.assertEqual(len(messages), len(before))
        self.assertNotIn(CURRENT_MESSAGE, system)

    def test_the_reading_is_kept_as_a_note(self):
        messages, _moved = their_words_last(built(), said=SAID, reading=READING)
        system = messages[0]["content"]
        self.assertIn(f"It was read as: {READING}.", system)
        self.assertIn("answer what they actually said", system)
        # Only as the note: it is no longer the message to answer.
        self.assertEqual(system.count(READING), 1)

    def test_no_note_when_the_reading_is_their_own_words(self):
        messages, moved = their_words_last(built(said_as=SAID), said=SAID, reading=SAID)
        self.assertTrue(moved)
        self.assertNotIn("It was read as", messages[0]["content"])
        self.assertEqual(messages[-1]["content"], SAID)

    def test_the_reading_defaults_to_what_the_prompt_showed(self):
        messages, _moved = their_words_last(built(), said=SAID)
        self.assertIn(f"It was read as: {READING}.", messages[0]["content"])

    def test_evidence_and_its_rules_move_with_the_rest(self):
        messages, _moved = their_words_last(built(evidence="Weights are scaled by max/127."), said=SAID,
                                            reading=READING)
        system = messages[0]["content"]
        self.assertIn("CURRENT RETRIEVED EVIDENCE\nWeights are scaled by max/127.", system)
        self.assertIn("EVIDENCE RULES", system)
        self.assertEqual(messages[-1]["content"], SAID)

    def test_the_answer_language_comes_along(self):
        messages, _moved = their_words_last(built(language="ko"), said="양자화가 뭐야?", reading="What is quantization?")
        self.assertIn("Write the entire reply in Korean", messages[0]["content"])
        self.assertEqual(messages[-1]["content"], "양자화가 뭐야?")

    def test_a_prompt_that_already_ends_on_their_words_is_left_alone(self):
        conversational = [{"role": "system", "content": PERSONA}, *HISTORY,
                          {"role": "user", "content": SAID + VOICE}]
        messages, moved = their_words_last(conversational, said=SAID, reading=READING)
        self.assertFalse(moved)
        self.assertIs(messages, conversational)

    def test_nothing_to_put_last_leaves_it_as_built(self):
        for said in ("", "   "):
            before = built()
            messages, moved = their_words_last(before, said=said, reading=READING)
            self.assertFalse(moved)
            self.assertIs(messages, before)

    def test_a_prompt_without_a_system_message_is_left_alone(self):
        before = built()[1:]
        messages, moved = their_words_last(before, said=SAID, reading=READING)
        self.assertFalse(moved)
        self.assertIs(messages, before)

    def test_what_it_was_given_is_not_changed(self):
        before = built()
        kept = copy.deepcopy(before)
        their_words_last(before, said=SAID, reading=READING)
        self.assertEqual(before, kept)

    def test_the_routes_it_finishes(self):
        self.assertEqual(THEIR_WORDS_ROUTES, ("knowledge_question", "web_search"))


# ----------------------------------------------------------- whole turns

STABLE = {"intent": "knowledge_question", "confidence": 1.0, "speech_act": "information_request",
          "answer_shape": "explain", "topic": "model quantization",
          "information_freshness": "stable", "requires_external_evidence": False, "time_scope": "timeless"}
OPENING = "How does model quantization work?"


class WholeTurnTests(unittest.TestCase):

    def engine(self, routes, *, search=False):
        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine(routes)
        self.addCleanup(engine.close)
        if search:
            engine._web_search_enabled = True
        return engine

    @staticmethod
    def say(engine, said):
        from core import turn_trace

        before = len(engine.client.prompts)
        with contextlib.redirect_stdout(io.StringIO()):
            engine.chat(said)
        drafts = [prompt for prompt in engine.client.prompts[before:]
                  if prompt and any("VOICE RESPONSE REQUIREMENTS\n" in str(m.get("content", ""))
                                    for m in prompt if isinstance(m, dict))
                  and not str(prompt[-1].get("content", "")).startswith("DRAFT ANSWER")]
        return drafts, turn_trace.last().as_record()["context"]

    def test_a_follow_up_question_reaches_the_writer_as_they_said_it(self):
        engine = self.engine({
            "quantization work": {**STABLE, "normalized_request": "How does model quantization work?"},
            "dividing -1": {**STABLE, "is_follow_up": True, "normalized_request": READING},
        })
        engine.client.reply = "It stores each weight in fewer bits."
        self.say(engine, OPENING)
        drafts, context = self.say(engine, SAID)
        self.assertTrue(drafts)
        prompt = drafts[0]
        self.assertEqual(prompt[-1], {"role": "user", "content": SAID})
        system = prompt[0]["content"]
        self.assertIn(MESSAGE_TO_ANSWER, system)
        self.assertIn(f"It was read as: {READING}.", system)
        self.assertIn("VOICE RESPONSE REQUIREMENTS", system)
        self.assertIn("CURRENTLY AVAILABLE AI AGENTS", system)
        self.assertNotIn(CURRENT_MESSAGE, "\n".join(str(m.get("content", "")) for m in prompt))
        # The conversation sits between the rules and their words.
        self.assertIn({"role": "user", "content": OPENING}, prompt[1:-1])
        self.assertEqual(context["question_shown"], "their words, last")
        self.assertEqual(context["route"]["intent"], "knowledge_question")

    def test_a_search_turn_keeps_its_evidence_and_ends_on_their_words(self):
        asked = "What is the latest version of Python?"
        engine = self.engine({"latest version of Python": {
            "intent": "web_search", "confidence": 1.0, "speech_act": "information_request",
            "topic": "Python", "normalized_request": "latest Python release version",
            "search_query": "latest Python release", "information_freshness": "changing",
            "requires_external_evidence": True, "time_scope": "current"}}, search=True)
        engine.web_search_tool.results = [{"title": "Python releases", "url": "https://example.test/python",
                                           "summary": "Python 3.14 is the newest release."}]
        engine.client.reply = "The newest release is Python 3.14."
        drafts, context = self.say(engine, asked)
        self.assertTrue(engine.web_search_tool.queries)
        self.assertTrue(drafts)
        prompt = drafts[0]
        self.assertEqual(prompt[-1], {"role": "user", "content": asked})
        self.assertIn("CURRENT RETRIEVED EVIDENCE", prompt[0]["content"])
        self.assertIn("https://example.test/python", prompt[0]["content"])
        self.assertIn("EVIDENCE RULES", prompt[0]["content"])
        self.assertEqual(context["question_shown"], "their words, last")

    def test_a_korean_question_ends_on_the_korean_words(self):
        asked = "양자화가 뭐야?"
        engine = self.engine({asked: {**STABLE, "normalized_request": "What is model quantization?"}})
        engine.client.reply = "가중치를 더 적은 비트로 저장하는 것입니다."
        drafts, _context = self.say(engine, asked)
        self.assertTrue(drafts)
        self.assertEqual(drafts[0][-1], {"role": "user", "content": asked})
        self.assertIn("Write the entire reply in Korean", drafts[0][0]["content"])

    def test_a_conversational_turn_is_built_as_it_always_was(self):
        remark = "I tried quantizing my own model yesterday."
        engine = self.engine({remark: {"intent": "conversation", "confidence": 1.0, "speech_act": "statement",
                                       "normalized_request": "The user quantized their model yesterday."}})
        engine.client.reply = "How did it go?"
        drafts, context = self.say(engine, remark)
        self.assertTrue(drafts)
        prompt = drafts[0]
        self.assertIn("VOICE RESPONSE REQUIREMENTS", prompt[-1]["content"])
        self.assertNotIn(MESSAGE_TO_ANSWER, prompt[0]["content"])
        self.assertNotIn("question_shown", context)


if __name__ == "__main__":
    unittest.main()
