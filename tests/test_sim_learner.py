"""The simulated-learner evaluation (evals/sim_learner.py), without a model.

Pinned with a fake Elaina and a fake person: the profiles are sound; the
person's brief carries their hidden knowledge and their language; their
turn is read strictly and understanding only accumulates; the conversation
ends for the right reason; the judge sees the person's profile and never
their own claims; the measures count what they say; and the transcripts
for calibration show neither the learner's nor the judge's view.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals import sim_learner as sl


def _learner(**changes):
    learners, _settings = sl.load_learners()
    found = next(x for x in learners if x.id == "sl_quantization_novice")
    return found if not changes else sl.Learner(**{**found.__dict__, **changes})


class ProfileTests(unittest.TestCase):

    def test_the_profiles_are_sound(self):
        learners, settings = sl.load_learners()
        self.assertEqual(len(learners), 9)
        self.assertEqual({x.language for x in learners}, {"en", "ko"})
        self.assertEqual((settings.max_turns, settings.patience), (8, 3))
        same = [x for x in learners if x.concept == "model quantization"]
        self.assertEqual(len(same), 2)
        self.assertNotEqual(same[0].knows, same[1].knows)

    def test_a_broken_profile_is_refused(self):
        data = json.loads(sl.PROFILES.read_text(encoding="utf-8"))
        data["learners"].append(dict(data["learners"][0]))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "p.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                sl.load_learners(path)


class LearnerTurnTests(unittest.TestCase):

    def test_the_brief_carries_the_hidden_profile_and_the_conversation(self):
        learner = _learner()
        messages = sl.learner_messages(learner, [{"person": "How does model quantization work?",
                                                  "elaina": "It shrinks the weights."}])
        system, user = messages[0]["content"], messages[1]["content"]
        self.assertIn("what a model's parameters or weights are", system)
        self.assertIn("zipping the model file", system)
        self.assertIn("q2: Quantization stores", system)
        self.assertNotIn("Korean", system)
        self.assertTrue(user.startswith("You: How does model quantization work?\nElaina: It shrinks"))
        korean = next(x for x in sl.load_learners()[0] if x.language == "ko")
        self.assertIn("Speak Korean", sl.learner_messages(korean, [])[0]["content"])

    def test_the_turn_is_read_strictly_and_understanding_accumulates(self):
        learner = _learner()
        state = sl.parse_learner(json.dumps({"say": "oh, like numbers?", "understood": ["q1", "zz"],
                                             "confused_by": "bits", "done": "maybe"}),
                                 learner, frozenset({"q3"}))
        self.assertEqual(state["understood"], frozenset({"q1", "q3"}))
        self.assertEqual(state["done"], "continue")
        self.assertEqual(state["confused_by"], "bits")
        full = sl.parse_learner(json.dumps({"say": "got it", "understood": ["q1", "q2", "q3"]}),
                                learner, frozenset())
        self.assertEqual(full["done"], "understood")
        for text in ("not json", "[]", json.dumps({"say": ""})):
            self.assertIsNone(sl.parse_learner(text, learner, frozenset()))


def _fake_learner(script):
    """Each call returns the next scripted state."""
    states = iter(script)

    def respond(learner, transcript, understood):
        step = next(states)
        if step is None:
            return None
        return {"say": step[0], "understood": understood | frozenset(step[1]),
                "confused_by": step[2], "done": step[3]}
    return respond


class PilotFixTests(unittest.TestCase):
    """What the first pilot (2026-10-01) showed the simulator and judge got wrong."""

    def test_giving_up_is_respected_even_with_every_idea_claimed(self):
        state = sl.parse_learner(json.dumps({"say": "포기할래", "understood": ["q1", "q2", "q3"],
                                             "done": "gave_up"}), _learner(), frozenset())
        self.assertEqual(state["done"], "gave_up")

    def test_the_person_may_not_supply_the_answer_or_credit_agreement(self):
        system = sl.learner_messages(_learner(), [])[0]["content"]
        self.assertIn("Never state an explanation yourself", system)
        self.assertIn("only says you are right or close, you still do not understand it", system)

    def test_the_words_they_do_not_know_reach_their_brief(self):
        system = sl.learner_messages(_learner(), [])[0]["content"]
        self.assertIn("Words you do not know: parameter, weight, bit, precision", system)
        cs = next(x for x in sl.load_learners()[0] if x.id == "sl_quantization_cs")
        self.assertNotIn("Words you do not know", sl.learner_messages(cs, [])[0]["content"])

    def test_the_judge_does_not_count_agreement_as_explanation(self):
        prompt = sl.judge_messages(_learner(), JudgeTests()._record())[0]["content"]
        self.assertIn("If the person said the idea and Elaina only agreed", prompt)


class PilotTwoFixTests(unittest.TestCase):
    """The user's ratings of pilot 2 (2026-10-02): the person was too
    sophisticated -- "realistically people will just ask simple questions
    until they figure out" -- and a conversation could end on a question
    she never answered."""

    def test_the_person_asks_one_plain_question_at_a_time(self):
        system = sl.learner_messages(_learner(), [])[0]["content"]
        self.assertIn("Ask about one thing only, in one short sentence.", system)
        self.assertIn("Do not recap or summarise what she said.", system)
        self.assertIn('"what\'s a weight?"', system)
        self.assertIn("Neither is a question.", system)
        korean = next(x for x in sl.load_learners()[0] if x.language == "ko")
        brief = sl.learner_messages(korean, [])[0]["content"]
        self.assertIn('"그 비트 부분 잘 모르겠어"', brief)
        self.assertNotIn('"what\'s a weight?"', brief)

    def test_giving_up_is_said_to_her_too(self):
        said = []

        def elaina(text):
            said.append(text)
            return "reply", 1.0
        record = sl.converse(_learner(), sl.Settings(), elaina,
                             _fake_learner([("huh", [], "all", "continue"), ("I give up", [], "", "gave_up")]))
        self.assertEqual(said[-1], "I give up")
        self.assertEqual((record["outcome"], record["turns"]), ("gave_up", 2))

    def test_the_judge_and_the_rater_see_her_closing_reply(self):
        record = {**JudgeTests()._record(), "closing_reply": "Glad that helped!"}
        prompt = sl.judge_messages(_learner(), record)[0]["content"]
        self.assertIn("Person (closing): ok!\nElaina (closing): Glad that helped!", prompt)
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "run"
            out.mkdir()
            (out / "dialogues.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            (out / "meta.json").write_text(json.dumps({"arm": "current", "learner_model": "m"}), encoding="utf-8")
            with mock.patch.object(sl, "ROOT", Path(folder)):
                sl.export_calibration("run", 5)
            self.assertIn("**Person:** ok!\n\n**Elaina:** Glad that helped!",
                          (out / "calibration.md").read_text(encoding="utf-8"))

    def test_an_ending_the_model_did_not_write_is_counted(self):
        draft = "An API is a set of rules that lets programs talk. A weather app uses one."
        added = {"draft": draft, "changed": [{"name": "grounded_entities", "after":
                 "An API is a set of rules that lets programs talk. I don't want to send you "
                 "somewhere I haven't checked -- want me to look up real ones?"}]}
        reshaped = {"draft": draft, "changed": [{"name": "final_form", "after":
                    "An API is a set of rules that lets programs talk -- a weather app uses one."}]}
        self.assertTrue(sl.ending_not_drafted(added))
        self.assertFalse(sl.ending_not_drafted({"draft": draft, "changed": []}))
        self.assertFalse(sl.ending_not_drafted({"draft": draft, "changed": [{"after": draft + "\n"}]}))
        self.assertIsNone(sl.ending_not_drafted({"draft": "", "changed": []}))
        # Punctuation the display pass changed is not a new ending.
        self.assertFalse(sl.ending_not_drafted(reshaped))
        # Nor is the same sentence in 습니다체.
        self.assertFalse(sl.ending_not_drafted({
            "draft": "양자화는 숫자를 줄이는 거예요. 필요하시면 예시를 알려드릴 수 있어요.",
            "changed": [{"after": "양자화는 숫자를 줄이는 것입니다. 필요하시면 예시를 알려드릴 수 있습니다."}]}))
        korean = {"draft": "배당금은 회사가 번 돈을 나누는 것입니다.",
                  "changed": [{"after": "배당금은 회사가 번 돈을 나누는 것입니다. 찾아봤지만 구체적인 "
                                        "숫자는 확인되지 않아서 빼고 말씀드렸습니다."}]}
        self.assertTrue(sl.ending_not_drafted(korean))


class ConversationTests(unittest.TestCase):

    def _talk(self, script, max_turns=8):
        replies = []

        def elaina(said):
            replies.append(said)
            return f"reply {len(replies)}", 1.0
        record = sl.converse(_learner(), sl.Settings(max_turns=max_turns), elaina, _fake_learner(script))
        return record, replies

    def test_understanding_ends_it(self):
        record, replies = self._talk([("what are weights?", [], "weights", "continue"),
                                      ("ah numbers", ["q1"], "", "continue"),
                                      ("got it!", ["q2", "q3"], "", "understood")])
        self.assertEqual((record["outcome"], record["turns"], record["closing"]), ("understood", 3, "got it!"))
        # The closing line is said to her too, and her answer kept.
        self.assertEqual(replies, ["How does model quantization work?", "what are weights?", "ah numbers",
                                   "got it!"])
        self.assertEqual(record["closing_reply"], "reply 4")
        self.assertEqual(record["transcript"][1]["learner"]["gained"], ["q1"])
        self.assertEqual(record["understood"], ["q1", "q2", "q3"])

    def test_giving_up_errors_and_the_limit_end_it(self):
        self.assertEqual(self._talk([("huh", [], "all", "continue"), ("forget it", [], "", "gave_up")])[0]
                         ["outcome"], "gave_up")
        self.assertEqual(self._talk([None])[0]["outcome"], "simulator_error")
        record = self._talk([("hm", [], "", "continue")] * 3, max_turns=3)[0]
        self.assertEqual((record["outcome"], record["turns"]), ("max_turns", 3))


class JudgeTests(unittest.TestCase):

    def _record(self):
        return {"learner": "sl_quantization_novice", "outcome": "understood", "turns": 2, "closing": "ok!",
                "understood": ["q1", "q2", "q3"], "of": ["q1", "q2", "q3"],
                "transcript": [{"turn": 1, "person": "How?", "elaina": "Weights.", "seconds": 4.0,
                                "learner": {"gained": [], "confused_by": "weights", "done": "continue"}},
                               {"turn": 2, "person": "what are weights", "elaina": "Numbers it learned.",
                                "seconds": 5.0, "learner": {"gained": ["q1"], "confused_by": "", "done": "continue"}}]}

    def test_the_judge_sees_the_profile_and_never_the_learners_claims(self):
        prompt = sl.judge_messages(_learner(), self._record())[0]["content"]
        self.assertIn("They did not know: what a model's parameters or weights are", prompt)
        self.assertIn("[2] Elaina: Numbers it learned.", prompt)
        self.assertIn("Person (closing): ok!", prompt)
        self.assertNotIn("gained", prompt)
        self.assertNotIn("confused_by", prompt)

    def test_a_judgement_must_cover_every_idea_and_every_turn(self):
        good = {"items": [{"id": i, "conveyed": "yes", "turn": 2} for i in ("q1", "q2", "q3")],
                "turns": [{"turn": n, "fits_this_person": "yes", "after_confusion": "n/a",
                           "question": "none", "natural": "yes"} for n in (1, 2)],
                "overall": {"helped_understand": "yes", "natural_conversation": "yes", "why": "w"}}
        parsed = sl.parse_judgement(json.dumps(good), _learner(), self._record())
        self.assertTrue(all(parsed["items"].values()))
        # Her answer to the closing line scored as one more turn is dropped.
        closing = {**good, "turns": good["turns"] + [{**good["turns"][0], "turn": 3}]}
        self.assertEqual(set(sl.parse_judgement(json.dumps(closing), _learner(), self._record())["turns"]),
                         {1, 2})
        good["turns"] = good["turns"][:1]
        self.assertIsNone(sl.parse_judgement(json.dumps(good), _learner(), self._record()))
        self.assertIsNone(sl.parse_judgement("nope", _learner(), self._record()))


class MeasureTests(unittest.TestCase):

    def test_the_measures(self):
        record = JudgeTests()._record()
        other = {**record, "learner": "sl_api_nontech", "outcome": "gave_up", "understood": [],
                 "of": ["a1", "a2", "a3"]}
        judged = {"sl_quantization_novice": {
            "items": {"q1": True, "q2": True, "q3": True},
            "turns": {1: {"fits_this_person": "no", "after_confusion": "n/a", "question": "unneeded", "natural": "yes"},
                      2: {"fits_this_person": "yes", "after_confusion": "changed approach", "question": "none",
                          "natural": "yes"}}}}
        m = sl.measures([record, other], judged)
        self.assertEqual(m["learner_understood"], (1, 2))
        self.assertEqual(m["verified_understood"], (1, 2))
        self.assertEqual(m["gave_up"], (1, 2))
        self.assertEqual(m["turns_to_understanding"], 2)
        self.assertEqual(m["changed_after_confusion"], (1, 1))
        self.assertEqual(m["unneeded_questions"], (1, 2))

    def test_calibration_shows_neither_the_learners_nor_the_judges_view(self):
        record = JudgeTests()._record()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "run"
            out.mkdir()
            (out / "dialogues.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            (out / "meta.json").write_text(json.dumps({"arm": "current", "learner_model": "m"}), encoding="utf-8")
            with mock.patch.object(sl, "ROOT", Path(folder)):
                self.assertEqual(sl.export_calibration("run", 5), 1)
            text = (out / "calibration.md").read_text(encoding="utf-8")
            self.assertIn("**Elaina:** Numbers it learned.", text)
            self.assertNotIn("gained", text)
            self.assertNotIn("confused_by", text)
            ratings = json.loads((out / "calibration.json").read_text(encoding="utf-8"))["ratings"]
            self.assertEqual(set(ratings[0]), {"conversation", "learner", "helped_understand", "natural",
                                               "person_realistic", "notes"})


if __name__ == "__main__":
    unittest.main()
