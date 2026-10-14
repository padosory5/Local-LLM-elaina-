"""The everyday-English check (evals/sim_everyday.py), without any model.

What is pinned is the machinery: the people load and are English, the person
is briefed but never told what the judge looks for, what they got only
accumulates, the goodbye is said to her and judged like any other reply, an
unreadable judgement is dropped rather than half-read, and the measures
count what they say they count.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals import sim_everyday as ev


def _person(**overrides) -> ev.Person:
    base = dict(id="p", language="en", situation="Hungry after work.", opening="What should I make?",
                persona="You are casual.", facts=("you are vegetarian", "you have 20 minutes"),
                twist="Say you are out of eggs.", wants=(("d1", "a dish"), ("d2", "how to make it")))
    base.update(overrides)
    return ev.Person(**base)


class PeopleTests(unittest.TestCase):

    def test_the_shipped_people_load_and_are_english(self):
        people, settings = ev.load_people()
        self.assertGreaterEqual(len(people), 8)
        self.assertEqual({p.language for p in people}, {"en"})
        self.assertEqual(len({p.id for p in people}), len(people))
        self.assertGreater(settings.max_turns, 2)
        for person in people:
            with self.subTest(person=person.id):
                self.assertTrue(person.wants and person.twist and person.opening)

    def test_a_broken_profile_is_refused(self):
        good = {"id": "x", "language": "en", "situation": "s", "opening": "o", "persona": "p",
                "facts": [], "twist": "t", "wants": [{"id": "a", "need": "n"}]}
        cases = {
            "duplicate": [good, good],
            "korean": [{**good, "language": "ko"}],
            "no wants": [{**good, "wants": []}],
            "repeated want": [{**good, "wants": [{"id": "a", "need": "n"}, {"id": "a", "need": "m"}]}],
            "no twist": [{**good, "twist": " "}],
        }
        for why, people in cases.items():
            with self.subTest(why=why), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "people.json"
                path.write_text(json.dumps({"people": people}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    ev.load_people(path)


class PersonTests(unittest.TestCase):

    def test_the_person_is_briefed_and_sees_the_conversation(self):
        person = _person()
        messages = ev.person_messages(person, [{"person": "What should I make?", "elaina": "Try an omelette."}])
        system, user = messages[0]["content"], messages[1]["content"]
        for part in ("you are vegetarian", "Say you are out of eggs.", "d1: a dish", "Hungry after work."):
            self.assertIn(part, system)
        self.assertIn("Elaina: Try an omelette.", user)
        # What the judge looks for is not theirs to play to.
        for judged in ('"answered"', '"remembered"', '"extra"'):
            self.assertNotIn(judged, system)

    def test_what_they_got_only_accumulates_and_unknown_ids_are_dropped(self):
        person = _person()
        state = ev.parse_person(json.dumps({"say": "ok", "got": ["d2", "zz"], "done": "continue"}),
                                person, frozenset({"d1"}))
        self.assertEqual(state["got"], frozenset({"d1", "d2"}))
        self.assertEqual(state["done"], "continue")

    def test_an_unknown_ending_is_read_as_continuing(self):
        state = ev.parse_person(json.dumps({"say": "hm", "got": [], "done": "bored"}), _person(), frozenset())
        self.assertEqual(state["done"], "continue")

    def test_an_unreadable_turn_is_none(self):
        for text in ("not json", "[]", json.dumps({"say": " "}), ""):
            with self.subTest(text=text):
                self.assertIsNone(ev.parse_person(text, _person(), frozenset()))


class ConversationTests(unittest.TestCase):

    @staticmethod
    def _elaina(replies):
        said = []

        def reply(text):
            said.append(text)
            return replies[len(said) - 1], 1.0

        return reply, said

    @staticmethod
    def _person_fn(states):
        def respond(person, transcript, got):
            state = states[len(transcript) - 1]
            return None if state is None else {"bothered_by": "", **state,
                                               "got": frozenset(state.get("got", ())) | got}
        return respond

    def test_the_goodbye_is_said_to_her_and_kept_as_a_reply(self):
        reply, said = self._elaina(["Try fried rice.", "Bye, enjoy!"])
        record = ev.converse(_person(), ev.Settings(max_turns=6), reply, self._person_fn([
            {"say": "great, thanks!", "got": ["d1", "d2"], "done": "satisfied"}]))
        self.assertEqual(record["outcome"], "satisfied")
        self.assertEqual(said, ["What should I make?", "great, thanks!"])
        self.assertEqual(len(record["transcript"]), 2)
        closing = record["transcript"][-1]
        self.assertTrue(closing["closing"])
        self.assertEqual((closing["turn"], closing["elaina"]), (2, "Bye, enjoy!"))
        self.assertEqual(record["got"], ["d1", "d2"])

    def test_a_person_who_stops_making_sense_ends_it(self):
        reply, _said = self._elaina(["Try fried rice."])
        record = ev.converse(_person(), ev.Settings(max_turns=6), reply, self._person_fn([None]))
        self.assertEqual(record["outcome"], "simulator_error")
        self.assertEqual(record["turns"], 1)

    def test_the_turn_limit_ends_it_without_a_goodbye(self):
        reply, said = self._elaina(["a", "b", "c"])
        record = ev.converse(_person(), ev.Settings(max_turns=3), reply, self._person_fn(
            [{"say": f"more {i}", "done": "continue"} for i in range(3)]))
        self.assertEqual((record["outcome"], record["turns"]), ("max_turns", 3))
        self.assertFalse(any(t.get("closing") for t in record["transcript"]))
        self.assertEqual(said, ["What should I make?", "more 0", "more 1"])


class JudgeTests(unittest.TestCase):

    RECORD = {"transcript": [{"turn": 1, "person": "What should I make?", "elaina": "Fried rice."},
                             {"turn": 2, "person": "thanks!", "elaina": "Enjoy!", "closing": True}]}

    def _verdict(self, **overrides):
        data = {"wants": [{"id": "d1", "met": "yes", "turn": 1}, {"id": "d2", "met": "no", "turn": 0}],
                "turns": [{"turn": 1, "answered": "yes", "wrong": False, "wrong_what": "", "remembered": "n/a",
                           "length": "right", "extra": "offer", "natural": "yes"},
                          {"turn": 2, "answered": "yes", "wrong": "true", "wrong_what": "said eggs",
                           "remembered": "no", "length": "too long", "extra": "none", "natural": "no"},
                          {"turn": 3, "answered": "yes"}],
                "overall": {"fluent": "partly", "why": "w"}}
        data.update(overrides)
        return json.dumps(data)

    def test_the_goodbye_is_numbered_and_marked_for_the_judge(self):
        text = ev.judge_messages(_person(), self.RECORD)[0]["content"]
        self.assertIn("[2] Person (closing): thanks!", text)
        self.assertIn("[2] Elaina: Enjoy!", text)
        self.assertIn("you are vegetarian", text)

    def test_a_judgement_is_read_and_anything_past_her_replies_dropped(self):
        verdict = ev.parse_judgement(self._verdict(), _person(), self.RECORD)
        self.assertEqual(verdict["wants"], {"d1": True, "d2": False})
        self.assertEqual(set(verdict["turns"]), {1, 2})
        self.assertEqual(verdict["turns"][1]["extra"], "offer")
        self.assertTrue(verdict["turns"][2]["wrong"])
        self.assertEqual(verdict["turns"][2]["wrong_what"], "said eggs")
        self.assertEqual(verdict["turns"][2]["length"], "too long")

    def test_a_judgement_missing_a_want_or_a_reply_is_unreadable(self):
        missing_want = self._verdict(wants=[{"id": "d1", "met": "yes"}])
        missing_turn = self._verdict(turns=[{"turn": 1, "answered": "yes"}])
        self.assertIsNone(ev.parse_judgement(missing_want, _person(), self.RECORD))
        self.assertIsNone(ev.parse_judgement(missing_turn, _person(), self.RECORD))
        self.assertIsNone(ev.parse_judgement("not json", _person(), self.RECORD))

    def test_values_outside_the_scale_fall_back_to_the_cautious_reading(self):
        verdict = ev.parse_judgement(self._verdict(turns=[
            {"turn": 1, "answered": "sort of", "natural": "maybe", "extra": "a joke", "length": "fine"},
            {"turn": 2}]), _person(), self.RECORD)
        tags = verdict["turns"][1]
        self.assertEqual((tags["answered"], tags["natural"], tags["extra"], tags["length"]),
                         ("no", "no", "none", "right"))
        self.assertFalse(tags["wrong"])


class MeasureTests(unittest.TestCase):

    def test_got_it_needs_both_the_person_and_the_judge(self):
        records = [
            {"person": "a", "outcome": "satisfied", "transcript": [{"elaina": "x", "seconds": 1.0}]},
            {"person": "b", "outcome": "satisfied", "transcript": [{"elaina": "y", "seconds": 2.0}]},
            {"person": "c", "outcome": "gave_up", "transcript": [{"elaina": "z", "seconds": 3.0}]},
        ]
        tag = {"answered": "yes", "wrong": False, "wrong_what": "", "remembered": "yes", "length": "right",
               "extra": "none", "natural": "yes"}
        judged = {"a": {"wants": {"w": True}, "turns": {1: tag}},
                  "b": {"wants": {"w": False}, "turns": {1: {**tag, "wrong": True, "extra": "offer"}}},
                  "c": {"wants": {"w": True}, "turns": {1: {**tag, "remembered": "n/a", "natural": "no"}}}}
        m = ev.measures(records, judged)
        self.assertEqual(m["person_satisfied"], (2, 3))
        self.assertEqual(m["got_what_they_came_for"], (1, 3))
        self.assertEqual(m["wants_met"], (2, 3))
        self.assertEqual(m["wrong"], (1, 3))
        self.assertEqual(m["remembered"], (2, 2))
        self.assertEqual(m["natural"], (2, 3))
        self.assertEqual(m["extras"]["offer"], 1)
        self.assertEqual(m["reply_seconds_p50"], 2.0)

    def test_repeats_and_stage_offers_are_counted_from_the_record(self):
        record = {"transcript": [{"elaina": "Fried rice with spinach is quick."},
                                 {"elaina": "Fried rice with spinach is quick!"},
                                 {"elaina": "Something else entirely."}],
                  "traces": [{"changed": [{"name": "append_recommendation"}]}, {"changed": [{"name": "her_voice"}]}]}
        self.assertEqual(ev.repeats(record), 1)
        self.assertEqual(ev.stage_offers(record), 1)


if __name__ == "__main__":
    unittest.main()
