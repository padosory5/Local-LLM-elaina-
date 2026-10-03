"""The turn-move owner, in shadow (brain/turn_move.py).

The embedder is faked here: a sentence's vector is chosen by the test, so
what is pinned is the logic -- the threshold, what is read before the
budget, how long the thread lives, what is recorded -- not the model. The
model itself is measured on the simulated learners (docs/TURN_MOVE_SHADOW.md).
"""

from __future__ import annotations

import contextlib
import io
import math
import unittest

from brain import turn_move as tm


class FakeEmbedder:
    """Each sentence's vector is set by the test; anything else gets an axis of its own."""

    def __init__(self, vectors=None, dims=64):
        self.vectors = dict(vectors or {})
        self.dims = dims
        self.calls = 0

    def __call__(self, text):
        self.calls += 1
        if text in self.vectors:
            return self.vectors[text]
        axis = len(self.vectors) % self.dims
        vector = [0.0] * self.dims
        vector[axis] = 1.0
        self.vectors[text] = vector
        return vector


def _at(similarity, dims=64):
    """A vector whose cosine with axis 0 is ``similarity``."""
    vector = [0.0] * dims
    vector[0], vector[dims - 1] = similarity, math.sqrt(1 - similarity ** 2)
    return vector


AXIS0 = [1.0] + [0.0] * 63


def _talk(lines, embed, *, follow_up=True, budget="explain", speech_act="information_request",
          replies=None):
    record = tm.Record(embed=embed)
    moves = []
    for number, said in enumerate(lines):
        moves.append(record.decide(said, is_follow_up=follow_up and number > 0, topic_shift=False,
                                   speech_act=speech_act, budget=budget))
        record.record(said, (replies or {}).get(number, f"reply {number + 1}"))
    return record, moves


class AskedAgainTests(unittest.TestCase):

    def test_a_question_close_in_meaning_to_an_earlier_one(self):
        embed = FakeEmbedder({"why doesn't the wind take the heat?": AXIS0,
                              "but why won't wind carry it off?": _at(0.83)})
        _record, moves = _talk(["what is the greenhouse effect?", "why doesn't the wind take the heat?",
                                "what is a greenhouse gas?", "but why won't wind carry it off?"], embed)
        self.assertEqual([m.move for m in moves], [tm.FIRST, tm.CONTINUE, tm.CONTINUE, tm.DIFFERENTLY])
        self.assertEqual(moves[3].asked_before, (2,))
        self.assertAlmostEqual(moves[3].similarity, 0.83, places=3)
        self.assertEqual(moves[3].quoted, "reply 2")

    def test_the_threshold_is_fixed_at_0_80(self):
        self.assertEqual(tm.ASKED_AGAIN_SIMILARITY, 0.80)
        for similarity, expected in ((0.79, tm.CONTINUE), (0.81, tm.DIFFERENTLY)):
            embed = FakeEmbedder({"first question": AXIS0, "second question": _at(similarity)})
            _record, moves = _talk(["first question", "second question"], embed)
            self.assertEqual(moves[1].move, expected, similarity)

    def test_every_earlier_asking_is_counted(self):
        embed = FakeEmbedder({"q": AXIS0, "q again": AXIS0, "q a third time": _at(0.9)})
        _record, moves = _talk(["q", "q again", "q a third time"], embed)
        self.assertEqual((moves[2].asked_before, moves[2].attempts), ((1, 2), 2))
        self.assertIn("3rd time", moves[2].instruction("en"))

    def test_without_an_embedder_it_is_recorded_as_unavailable(self):
        _record, moves = _talk(["q", "q"], None)
        self.assertEqual((moves[1].move, moves[1].asked_signal), (tm.CONTINUE, tm.UNAVAILABLE))

    def test_an_embedder_that_fails_takes_nothing_down(self):
        def broken(text):
            raise RuntimeError("no GPU")
        with contextlib.redirect_stdout(io.StringIO()):
            _record, moves = _talk(["q", "q"], broken)
        self.assertEqual(moves[1].move, tm.CONTINUE)

    def test_each_sentence_is_embedded_once(self):
        embed = FakeEmbedder()
        _talk(["a", "b", "c"], embed)
        self.assertEqual(embed.calls, 3)


class ChallengeTests(unittest.TestCase):

    REPLY = ("Yes, that's right. A 32-bit float is scaled down to fit into fewer bits, like 8-bit "
             "integers, using a scale factor. For example, 0.0023 might become 23 when scaled by 1000, "
             "and then divided by 1000 to get back the original value.")

    def test_her_number_set_against_theirs(self):
        _record, moves = _talk(["How does model quantization work?",
                                "Wait, if 0.0023 times 1000 is 2.3, how does that become the integer 23?"],
                               FakeEmbedder(), replies={0: self.REPLY})
        move = moves[1]
        self.assertEqual((move.move, move.challenged, move.their_numbers), (tm.CHECK, "number", ("2.3",)))
        self.assertIn("0.0023 might become 23 when scaled by 1000", move.quoted)

    def test_one_of_her_numbers_in_a_how_question_is_not_a_challenge(self):
        _record, moves = _talk(["How does model quantization work?",
                                "Okay, but how do you map a 32-bit float to an 8-bit integer?"],
                               FakeEmbedder(), replies={0: "It turns a 32-bit model into a 16-bit one."})
        self.assertEqual(moves[1].move, tm.CONTINUE)

    def test_a_correction_is_a_challenge_whatever_the_budget(self):
        # Pilot 5: "그거 답이 아니잖아" read as a correction and given a value budget.
        record = tm.Record(embed=FakeEmbedder())
        record.decide("배당금이 뭐야?", is_follow_up=False, topic_shift=False,
                      speech_act="information_request", budget="explain")
        record.record("배당금이 뭐야?", "배당금은 회사가 이익을 나누는 방식입니다. 확인했습니다.")
        move = record.decide("아니 근데 내 주식 수만큼 오는 거 맞냐고 했잖아, 그거 답이 아니잖아",
                             is_follow_up=True, topic_shift=False, speech_act="correction", budget="value")
        self.assertEqual((move.move, move.challenged), (tm.CHECK, "correction"))


class OrderAndThreadTests(unittest.TestCase):

    def test_a_re_ask_the_router_gives_a_short_budget_is_still_a_re_ask(self):
        embed = FakeEmbedder({"how does it work?": AXIS0, "so how does it work??": AXIS0})
        record = tm.Record(embed=embed)
        record.decide("how does it work?", is_follow_up=False, topic_shift=False,
                      speech_act="information_request", budget="explain")
        record.record("how does it work?", "It rounds.")
        move = record.decide("so how does it work??", is_follow_up=True, topic_shift=False,
                             speech_act="action_request", budget="value")
        self.assertEqual(move.move, tm.DIFFERENTLY)

    def test_not_a_follow_up_keeps_the_thread_and_a_topic_shift_clears_it(self):
        # Pilot 5: the router called a re-ask "not a follow-up" and the v1 thread was lost.
        embed = FakeEmbedder({"how are small numbers kept?": AXIS0, "the ruler is vague, how?": _at(0.85)})
        record = tm.Record(embed=embed)
        for said, follow_up in (("how does quantization work?", False), ("how are small numbers kept?", True)):
            record.decide(said, is_follow_up=follow_up, topic_shift=False, speech_act="", budget="explain")
            record.record(said, "reply")
        move = record.decide("the ruler is vague, how?", is_follow_up=False, topic_shift=False,
                             speech_act="", budget="explain")
        self.assertEqual((move.move, move.thread_turns), (tm.DIFFERENTLY, 2))
        record.record("the ruler is vague, how?", "reply")
        moved = record.decide("play some music", is_follow_up=False, topic_shift=True, speech_act="",
                              budget="value")
        self.assertEqual((moved.move, moved.thread_turns), (tm.NOT_EXPLAINING, 0))

    def test_a_new_question_that_is_not_a_follow_up_is_first(self):
        _record, moves = _talk(["a", "b"], FakeEmbedder(), follow_up=False)
        self.assertEqual([m.move for m in moves], [tm.FIRST, tm.FIRST])

    def test_the_thread_is_bounded(self):
        record, _moves = _talk([f"q{n}" for n in range(20)], FakeEmbedder())
        self.assertEqual(len(record.thread), tm.THREAD_LIMIT)
        self.assertEqual(record.thread[-1].said, "q19")


class InstructionTests(unittest.TestCase):

    def test_what_each_move_would_say(self):
        again = tm.Move(tm.DIFFERENTLY, thread_turns=3, asked_before=(2, 3), quoted="Heat is reflected.")
        self.assertIn("3rd time", again.instruction("en"))
        self.assertIn('"Heat is reflected."', again.instruction("en"))
        self.assertIn("3번째", again.instruction("ko"))
        check = tm.Move(tm.CHECK, thread_turns=1, challenged="number", quoted="0.0023 becomes 23.")
        self.assertIn("Check it before anything else", check.instruction("en"))
        self.assertIn("먼저 그것이 맞는지 확인", check.instruction("ko"))
        for move in (tm.FIRST, tm.CONTINUE, tm.NOT_EXPLAINING):
            self.assertEqual(tm.Move(move).instruction("en"), "")

    def test_the_trace_record(self):
        record = tm.Move(tm.DIFFERENTLY, thread_turns=4, asked_before=(4,), similarity=0.8312,
                         quoted="x").trace(language="en", production_goal="explain", history_inherited=False)
        self.assertEqual((record["move"], record["attempts"], record["similarity"], record["asked_signal"],
                          record["production_goal"], record["history_inherited"]),
                         (tm.DIFFERENTLY, 1, 0.831, tm.EMBEDDING, "explain", False))
        self.assertIn("2nd time", record["instruction"])


class InTheEngineTests(unittest.TestCase):
    """Recorded on every answered turn; nothing the model is shown changes."""

    # Stable and needing no lookup: without these the engine chose a web
    # search for the follow-up, and the harness reached the real network.
    ROUTE = {"intent": "knowledge_question", "confidence": 1.0, "speech_act": "information_request",
             "answer_shape": "explain", "topic": "greenhouse effect",
             "information_freshness": "stable", "requires_external_evidence": False}

    def test_the_move_is_traced_and_the_prompt_is_untouched(self):
        from core import turn_trace
        from tests.turn_harness import build_engine

        second = "Wait, why can't the heat get out if sunlight comes in?"
        third = "But why can't the heat get out if the sunlight comes in?"
        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({
                "greenhouse effect?": {**self.ROUTE, "normalized_request": "What is the greenhouse effect?"},
                "heat": {**self.ROUTE, "normalized_request": "Why can't the heat get out?", "is_follow_up": True},
            })
            engine._turn_moves = tm.Record(embed=FakeEmbedder({second: AXIS0, third: _at(0.9)}))
            engine.client.reply = "Greenhouse gases absorb the heat the ground gives off and send some back."
            engine.chat("What is the greenhouse effect?")
            first = turn_trace.last().as_record()["context"]["turn_move"]
            engine.chat(second)
            middle = turn_trace.last().as_record()["context"]["turn_move"]
            engine.chat(third)
            last = turn_trace.last().as_record()["context"]["turn_move"]
        self.assertEqual(first["move"], tm.FIRST)
        self.assertEqual(middle["move"], tm.CONTINUE)
        self.assertEqual((last["move"], last["asked_before"]), (tm.DIFFERENTLY, [2]))
        self.assertIn("2nd time", last["instruction"])
        shown = " ".join(str(m.get("content", "")) for prompt in engine.client.prompts for m in prompt)
        self.assertNotIn("time they have asked this", shown)
        self.assertEqual(len(engine._move_record().thread), 3)
        engine.close()

    def test_it_uses_memorys_embedder_and_records_when_there_is_none(self):
        from types import SimpleNamespace

        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({})
        try:
            self.assertIsNone(engine.memory_manager)
            self.assertIsNone(engine._move_record().embed)
            encoder = SimpleNamespace(encode=lambda text: [1.0, 0.0])
            engine.memory_manager, engine._turn_moves = SimpleNamespace(embedder=encoder), None
            self.assertIs(engine._move_record().embed, encoder.encode)
        finally:
            engine.memory_manager = None
            engine.close()


class TheirWordsTests(unittest.TestCase):
    """What the writer is shown on a turn the move owns."""

    SAID = "그거 그냥 인터넷에서 실시간으로 검색해온 거잖아?"
    READING = "Are training data just scraped internet content?"

    def _prompt(self, question):
        from brain.response_messages import build_personality_messages

        return build_personality_messages(
            system_prompt="persona", history=[], user_input=question,
            context_sections=(("CURRENTLY AVAILABLE AI AGENTS", "none"),),
        )[-1]["content"]

    def test_their_words_take_the_place_of_the_reading_in_a_real_prompt(self):
        prompt = self._prompt(self.READING)
        changed, did = tm.with_their_words(prompt, shown=self.READING, said=self.SAID)
        self.assertTrue(did)
        self.assertIn(f"CURRENT USER MESSAGE\n{self.SAID}\nAnswer this current message.", changed)
        self.assertNotIn(self.READING, changed)
        # Everything else in the prompt is as it was.
        self.assertEqual(changed.replace(self.SAID, self.READING), prompt)

    def test_nothing_changes_when_there_is_nothing_to_change(self):
        prompt = self._prompt(self.SAID)
        for shown, said in ((self.SAID, self.SAID),          # already their words
                            (self.READING, self.SAID),       # the reading is not in this prompt
                            ("", self.SAID), (self.SAID, "")):
            with self.subTest(shown=shown, said=said):
                self.assertEqual(tm.with_their_words(prompt, shown=shown, said=said), (prompt, False))
        conversational = "Current user message:\n" + self.SAID + "\nRespond to this message."
        self.assertEqual(tm.with_their_words(conversational, shown=self.READING, said=self.SAID),
                         (conversational, False))


class ModeTests(unittest.TestCase):

    class Config:
        def __init__(self, value, model=""):
            self.values = {"turn_move": value, "turn_move_model": model}

        def get(self, *keys, default=None, required=False):
            return self.values.get(keys[-1], default)

    def test_env_then_config_then_off(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tm.MODE_ENV, None)
            self.assertEqual(tm.mode(self.Config("on")), tm.ON)
            self.assertEqual(tm.mode(self.Config(True)), tm.ON)    # YAML's bare on
            self.assertEqual(tm.mode(self.Config(False)), tm.OFF)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(tm.mode(self.Config("sometimes")), tm.OFF)
            os.environ[tm.MODE_ENV] = "on"
            self.assertEqual(tm.mode(self.Config("off")), tm.ON)

    def test_only_differently_is_ever_acted_on(self):
        self.assertEqual(tm.ACTED_ON, (tm.DIFFERENTLY,))

    def test_deep_names_the_model_that_writes_the_turn(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(tm.MODE_ENV, None)
            os.environ.pop(tm.MODEL_ENV, None)
            self.assertEqual(tm.settings(self.Config("deep", "big-model")), (tm.DEEP, "big-model"))
            # The model is only named when it will be used.
            self.assertEqual(tm.settings(self.Config("on", "big-model")), (tm.ON, ""))
            self.assertEqual(tm.settings(self.Config("off", "big-model")), (tm.OFF, ""))
            # "deep" with no model is off, said out loud: never the 8B under the 27B's name.
            with contextlib.redirect_stdout(io.StringIO()) as said:
                self.assertEqual(tm.settings(self.Config("deep", "")), (tm.OFF, ""))
            self.assertIn("no model is named", said.getvalue())
            os.environ[tm.MODEL_ENV] = "from-env"
            self.assertEqual(tm.settings(self.Config("deep", "big-model")), (tm.DEEP, "from-env"))


class ActingTests(unittest.TestCase):
    """With responses.turn_move on, a "differently" move owns its turn."""

    # Stable and needing no lookup: without these the engine chose a web
    # search for the follow-up, and the harness reached the real network.
    ROUTE = {"intent": "knowledge_question", "confidence": 1.0, "speech_act": "information_request",
             "answer_shape": "explain", "topic": "greenhouse effect",
             "information_freshness": "stable", "requires_external_evidence": False}
    SECOND = "Wait, why can't the heat get out if sunlight comes in?"
    THIRD = "But why can't the heat get out if the sunlight comes in?"

    def _run(self, third_route=None, *, mode=tm.ON, model=""):
        from core import turn_trace
        from tests.turn_harness import build_engine

        with contextlib.redirect_stdout(io.StringIO()):
            engine = build_engine({
                "greenhouse effect?": {**self.ROUTE, "normalized_request": "What is the greenhouse effect?"},
                "Wait, why": {**self.ROUTE, "normalized_request": "Why can't the heat get out?",
                              "is_follow_up": True},
                "But why": third_route or {**self.ROUTE, "normalized_request": "Why can't the heat get out?",
                                           "is_follow_up": True},
            })
            self.assertEqual(engine._turn_move_mode, tm.OFF)     # the harness pins it off
            engine._turn_move_mode = mode
            engine._turn_move_model = model
            self.default_model = engine.conversation_model
            engine._turn_moves = tm.Record(embed=FakeEmbedder({self.SECOND: AXIS0, self.THIRD: _at(0.9)}))
            engine.client.reply = "Greenhouse gases absorb the heat the ground gives off and send some back."
            records = []
            self.written_by = []
            for said in ("What is the greenhouse effect?", self.SECOND, self.THIRD):
                before = len(engine.client.prompts)
                engine.chat(said)
                drafts = [(str(p[-1].get("content", "")), engine.client.models[before + index])
                          for index, p in enumerate(engine.client.prompts[before:])
                          if p and "VOICE RESPONSE REQUIREMENTS\n" in str(p[-1].get("content", ""))
                          and not str(p[-1].get("content", "")).startswith("DRAFT ANSWER")]
                self.assertTrue(drafts, said)
                self.assertNotIn("CURRENT RETRIEVED EVIDENCE", drafts[0][0])
                self.written_by.append(drafts[0][1])
                records.append((turn_trace.last().as_record()["context"], drafts[0][0]))
        engine.close()
        return records

    def test_a_re_ask_gets_the_moves_line_instead_of_the_goal(self):
        records = self._run()
        (_c1, _p1), (c2, p2), (c3, p3) = records
        self.assertNotIn("turn_move_applied", c2)
        self.assertIn("Explain it so it lands", p2)
        self.assertEqual(c3["turn_move"]["move"], tm.DIFFERENTLY)
        self.assertIn("This is the 2nd time they have asked this", p3)
        self.assertNotIn("Explain it so it lands", p3)
        applied = c3["turn_move_applied"]
        self.assertEqual((applied["move"], applied["was"]["goal"], applied["was"]["act"]),
                         (tm.DIFFERENTLY, "explain", "answer"))

    def test_the_writer_answers_what_they_said_not_the_routers_reading(self):
        # A/B 2: a question asked for the third time reached the writer as the
        # router's rewording of it, and the rewording is what got answered.
        records = self._run()
        (_c1, _p1), (_c2, p2), (c3, p3) = records
        reading = "CURRENT USER MESSAGE\nWhy can't the heat get out?\n"
        self.assertIn(f"CURRENT USER MESSAGE\n{self.THIRD}\n", p3)
        self.assertNotIn(reading, p3)
        self.assertEqual(c3["turn_move_applied"]["question_shown"], "their words")
        # A turn the move does not own is built as it always was.
        self.assertIn(reading, p2)

    def test_with_the_flag_off_the_writer_is_shown_what_it_always_was(self):
        _context, prompt = self._run(mode=tm.OFF)[2]
        self.assertIn("CURRENT USER MESSAGE\nWhy can't the heat get out?\n", prompt)
        self.assertNotIn(self.THIRD, prompt.split("CURRENT USER MESSAGE\n", 1)[1].split("\n", 1)[0])

    def test_off_records_and_changes_nothing(self):
        records = self._run(mode=tm.OFF)
        context, prompt = records[2]
        self.assertEqual(context["turn_move"]["move"], tm.DIFFERENTLY)
        self.assertNotIn("turn_move_applied", context)
        self.assertNotIn("time they have asked this", prompt)

    def test_a_re_ask_filed_as_a_short_answer_gets_an_explanations_room(self):
        from brain import response_budget

        short = {"intent": "conversation", "confidence": 1.0, "speech_act": "statement",
                 "normalized_request": "Why can't the heat get out?", "is_follow_up": True,
                 "topic": "greenhouse effect", "information_freshness": "stable",
                 "requires_external_evidence": False}
        context, prompt = self._run(short)[2]
        applied = context["turn_move_applied"]
        self.assertEqual(applied["was"]["budget"], response_budget.VALUE)
        self.assertGreater(applied["room"][0], response_budget.DEFAULTS[response_budget.VALUE][0])
        self.assertIn("This is the 2nd time", prompt)

    def test_deep_has_the_larger_model_write_that_turn_and_only_that_turn(self):
        records = self._run(mode=tm.DEEP, model="the-27b")
        context, prompt = records[2]
        self.assertNotEqual(self.default_model, "the-27b")
        self.assertEqual(self.written_by, [self.default_model, self.default_model, "the-27b"])
        self.assertIn("This is the 2nd time they have asked this", prompt)
        applied = context["turn_move_applied"]
        self.assertEqual((applied["mode"], applied["written_by"]), (tm.DEEP, "the-27b"))
        self.assertEqual(context["model"], "the-27b")
        self.assertNotIn("turn_move_applied", records[1][0])

    def test_on_keeps_the_8b_writing(self):
        records = self._run(mode=tm.ON)
        self.assertEqual(self.written_by[2], self.default_model)
        self.assertEqual(records[2][0]["turn_move_applied"]["written_by"], "")

    def test_a_check_is_never_acted_on(self):
        correction = {**self.ROUTE, "speech_act": "correction", "normalized_request": "That's wrong.",
                      "is_follow_up": True}
        context, prompt = self._run(correction)[2]
        self.assertEqual(context["turn_move"]["move"], tm.CHECK)
        self.assertNotIn("turn_move_applied", context)
        self.assertNotIn("questioning something you said", prompt)


if __name__ == "__main__":
    unittest.main()
