"""The evaluation corpus is well-formed, covers what it promises, and its
deterministic checks measure what they say.

These run without a model. They do not score Elaina -- evals/run.py does,
live -- they make sure the instrument is sound, so a number it reports is a
number about her and not about a typo in a scenario file.
"""

from __future__ import annotations

import unittest

from evals import checks, corpus, modality_pairs


class CorpusTests(unittest.TestCase):

    def test_the_corpus_is_consistent(self):
        self.assertEqual(corpus.problems(), [])

    def test_it_covers_what_the_phase_plan_promised(self):
        scenarios = corpus.load()
        by_suite = {}
        for scenario in scenarios:
            by_suite.setdefault(scenario.suite, []).append(scenario)
        explanation = by_suite["explanation"]
        self.assertGreaterEqual(len(explanation), 15)
        self.assertGreaterEqual(len({s.domain for s in explanation}), 6)
        self.assertEqual({s.language for s in explanation}, {"en", "ko"})
        self.assertGreaterEqual(len(by_suite["relevance"]), 10)
        self.assertGreaterEqual(len(by_suite["modality"]), 5)
        self.assertGreaterEqual(len(by_suite["trajectories"]), 3)
        self.assertGreaterEqual(len(modality_pairs.pairs()), 15)

    def test_the_session_that_prompted_this_is_in_it_word_for_word(self):
        session = next(s for s in corpus.load(["trajectories"])
                       if s.id == "live_session_2026_09_23")
        self.assertEqual(
            session.turns[3].say,
            "Can you give me like an example when Taylor series are used?",
        )

    def test_the_contamination_matrix_is_read_not_copied(self):
        loaded = corpus.load(["contamination"])
        self.assertGreaterEqual(len(loaded), 10)
        for scenario in loaded:
            self.assertTrue(scenario.scored)

    def test_every_defined_property_is_used(self):
        used = {prop for s in corpus.load() for _, t in s.scored for prop in t.rubric}
        self.assertEqual(set(corpus.rubric()) - used, set())

    def test_no_property_is_written_for_one_topic(self):
        # The rubric is general by construction; a property naming a subject
        # would be a rule in disguise.
        for name, spec in corpus.rubric().items():
            for topic in ("taylor", "derivative", "hash", "seattle", "utc"):
                self.assertNotIn(topic, name.casefold())
                self.assertNotIn(topic, spec["definition"].casefold())


class ExampleLeakTests(unittest.TestCase):
    """A definition's examples are invented, never lifted from the corpus.

    Version 2 broke this twice -- "'Yeah.' after rental listings" was a
    contamination scenario, and the year example mirrored a stored reply --
    and both properties then scored perfect agreement on exactly those
    items. An example that is also a calibration item tells the judge the
    answer.
    """

    def test_no_quoted_example_is_something_a_scenario_says(self):
        import json
        import re

        said = [turn.say for scenario in corpus.load() for turn in scenario.turns]
        cases = json.loads(corpus.CONTAMINATION.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            said.extend(case.get("setup", ()))
            said.append(case["turn"])
        folded = [" ".join(line.casefold().split()) for line in said]
        names = set(corpus.rubric())
        for name, spec in corpus.rubric().items():
            for example in re.findall(r"'(.+?)'(?=[\s.,;:)]|$)", spec["definition"]):
                example = " ".join(example.casefold().split())
                # An example is an utterance -- a sentence, or anything
                # ending like one ("Yeah.") -- not a word being discussed
                # ('used', 'which one').
                utterance = len(example.split()) >= 3 or example[-1:] in ".?!…"
                if not utterance or example in names:
                    continue
                for line in folded:
                    with self.subTest(property=name, example=example):
                        self.assertNotIn(example, line)
                        self.assertNotEqual(example.strip(" ."), line.strip(" ."))


class ApplicabilityTests(unittest.TestCase):
    """Properties are scored only where they can mean something."""

    def test_a_turn_that_asks_for_nothing_is_not_asked_whether_it_answered(self):
        for mode in ("receipt", "remark"):
            properties = corpus.properties_for(mode)
            self.assertNotIn("answers_the_ask", properties)
            self.assertIn("takes_the_turn_as_meant", properties)

    def test_a_question_about_a_cause_is_not_asked_what_the_idea_solves(self):
        self.assertNotIn("says_what_it_solves", corpus.properties_for("cause"))
        self.assertIn("says_what_it_solves", corpus.properties_for("purpose"))
        self.assertIn("says_what_it_solves", corpus.properties_for("example"))

    def test_only_a_property_that_depends_on_the_reply_may_be_na(self):
        allowed = {name for name, spec in corpus.rubric().items() if spec.get("allows_na")}
        self.assertEqual(allowed, {"notation_earned"})

    def test_every_scored_turn_has_a_known_goal(self):
        known = set(corpus.modes())
        for scenario in corpus.load():
            for index, turn in scenario.scored:
                with self.subTest(scenario=scenario.id, turn=index):
                    self.assertIn(turn.mode, known)
                    self.assertTrue(turn.rubric)

    def test_every_contamination_case_is_classified(self):
        import json
        cases = json.loads(corpus.CONTAMINATION.read_text(encoding="utf-8"))["cases"]
        self.assertEqual({case["id"] for case in cases}, set(corpus.CONTAMINATION_MODES))

    def test_no_scenario_matches_strings_where_a_behaviour_is_meant(self):
        # Version 1 failed a time answer for containing "UTC"; version 2 asks
        # whether the reply exposes metadata nobody asked for, and checks the
        # offset it states against the real one.
        for scenario in corpus.load():
            for _, turn in scenario.scored:
                self.assertNotIn("must_not_match", turn.checks, scenario.id)


class CorrectnessTests(unittest.TestCase):
    """Stated values checked against the truth at the moment of the turn."""

    def setUp(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        # 00:14 on 24 September 2026 in Seattle, whatever zone runs the test.
        self.when = datetime(2026, 9, 24, 0, 14, 30, tzinfo=ZoneInfo("America/Los_Angeles"))

    def verdicts(self, spec, display):
        turn = checks.TurnResult(said="q", display=display, when=self.when)
        return {r.check: r.passed for r in checks.run(spec, turn)}

    def test_a_second_clock_that_was_never_looked_up_is_caught(self):
        spec = {"clock_times": ["America/Los_Angeles", "Asia/Seoul"],
                "hour_difference": ["America/Los_Angeles", "Asia/Seoul"]}
        wrong = ("It is 12:14 AM in Seattle (PDT) and 1:14 PM in Seoul (KST) today. "
                 "The time difference is 13 hours.")
        right = "It's 12:14 AM in Seattle and 4:14 PM in Seoul, sixteen hours ahead."
        self.assertEqual(self.verdicts(spec, wrong), {"clock_times": False, "hour_difference": False})
        self.assertEqual(self.verdicts(spec, right), {"clock_times": True, "hour_difference": True})

    def test_a_wrong_offset_is_caught_even_when_nobody_asked_for_one(self):
        spec = {"utc_offset": {"zone": "America/Los_Angeles", "required": False}}
        self.assertFalse(self.verdicts(spec, "That's one hour behind Coordinated Universal Time.")["utc_offset"])
        self.assertTrue(self.verdicts(spec, "It's Pacific Time, UTC−7 right now.")["utc_offset"])
        self.assertTrue(self.verdicts(spec, "It's Pacific Time.")["utc_offset"])

    def test_a_korean_clock_reading(self):
        spec = {"clock_times": ["America/Los_Angeles"]}
        self.assertTrue(self.verdicts(spec, "지금은 오전 12시 14분입니다.")["clock_times"])
        self.assertFalse(self.verdicts(spec, "지금은 오후 3시 14분입니다.")["clock_times"])

    def test_dates_years_and_weekdays(self):
        self.assertTrue(self.verdicts({"calendar": {"date": True}},
                                      "Today is Thursday, September 24, 2026.")["calendar"])
        self.assertTrue(self.verdicts({"calendar": {"date": True}}, "오늘은 9월 24일입니다.")["calendar"])
        self.assertFalse(self.verdicts({"calendar": {"year": True}},
                                       "It's 2026, a Wednesday.")["calendar"])

    def test_a_wrong_conversion_beside_a_right_value_is_caught(self):
        spec = {"quantity": [
            {"unit": "km|kilomet(?:er|re)s?", "value": 384400, "tolerance": 0.03},
            {"unit": "miles?", "value": 238900, "tolerance": 0.03}],
            "quantity_required_any": True}
        self.assertEqual(
            self.verdicts(spec, "About 384,400 kilometers -- roughly 2.5 million miles."),
            {"quantity": False, "quantity_required_any": True},
        )
        self.assertEqual(
            self.verdicts(spec, "About 384,400 kilometers, or 239 thousand miles."),
            {"quantity": True, "quantity_required_any": True},
        )


class CheckTests(unittest.TestCase):

    def turn(self, display, **kwargs):
        return checks.TurnResult(said="q", display=display, **kwargs)

    def verdicts(self, spec, turn):
        return {r.check: r.passed for r in checks.run(spec, turn)}

    def test_asking_back_instead_of_answering_is_caught(self):
        spec = {"answers_not_asks": True}
        for asked in ("Which bit lost you? Tell me the part.",
                      "무슨 부분이 어려우셨나요?"):
            self.assertFalse(self.verdicts(spec, self.turn(asked))["answers_not_asks"], asked)
        for answered in (
            "Picture a jar that holds a little of every company. Want another angle?",
            "Want it simpler? Think of two friends trading coins until the prices "
            "settle where both are happy with the deal.",
        ):
            self.assertTrue(self.verdicts(spec, self.turn(answered))["answers_not_asks"], answered)

    def test_the_clock_metadata_is_caught(self):
        spec = {"must_match": [r"\b\d{1,2}:\d{2}"],
                "must_not_match": ["UTC", "Daylight", "time ?zone"]}
        bad = self.turn("The current time is 10:38 PM in Pacific Daylight Time, UTC-0700.")
        good = self.turn("It's 10:38 PM.")
        self.assertEqual(self.verdicts(spec, bad), {"must_match": True, "must_not_match": False})
        self.assertEqual(self.verdicts(spec, good), {"must_match": True, "must_not_match": True})

    def test_speech_with_notation_is_not_speakable(self):
        spoken = self.turn("x", speech="e^x ≈ 1 + x + x²/2! + x³/3!")
        plain = self.turn("x", speech="Yes! About twelve percent, at 10:38 PM.")
        self.assertFalse(self.verdicts({"speakable": True}, spoken)["speakable"])
        self.assertTrue(self.verdicts({"speakable": True}, plain)["speakable"])

    def test_a_minus_turned_into_a_comma_is_caught(self):
        damaged = self.turn("cos(x) ≈ 1, x²/2 + x⁴/24", draft="cos(x) ≈ 1 - x²/2 + x⁴/24")
        intact = self.turn("cos(x) ≈ 1 - x²/2 + x⁴/24", draft="cos(x) ≈ 1 - x²/2 + x⁴/24")
        prose = self.turn("Paris, the capital", draft="Paris - the capital")
        self.assertFalse(self.verdicts({"display_math_intact": True}, damaged)["display_math_intact"])
        self.assertTrue(self.verdicts({"display_math_intact": True}, intact)["display_math_intact"])
        self.assertTrue(self.verdicts({"display_math_intact": True}, prose)["display_math_intact"])

    def test_a_restated_explanation_is_caught(self):
        before = "A Taylor series uses derivatives at one point to build a polynomial."
        again = self.turn("A Taylor series uses the derivatives at one point to build a polynomial.",
                          previous_reply=before)
        new = self.turn("Say your calculator needs e to the 0.1. It cannot look that up.",
                        previous_reply=before)
        self.assertFalse(self.verdicts({"not_repeat_previous": True}, again)["not_repeat_previous"])
        self.assertTrue(self.verdicts({"not_repeat_previous": True}, new)["not_repeat_previous"])

    def test_the_language_of_a_reply(self):
        self.assertEqual(checks.language_of("지금은 오후 10시 38분입니다."), "ko")
        self.assertEqual(checks.language_of("It's 10:38 PM."), "en")

    def test_search_and_cards_are_read_from_the_turn(self):
        spec = {"not_searched": True, "no_cards": True}
        self.assertEqual(self.verdicts(spec, self.turn("x", searched=True, cards=True)),
                         {"not_searched": False, "no_cards": False})


class ModalityPairTests(unittest.TestCase):

    def test_tokens_are_found_in_order_with_alternatives(self):
        ok, missing = modality_pairs.spoken_in_order(
            "e to the x is about one plus x plus x squared over two factorial",
            ["e", "to the x", "approximately|is about", "1|one", "plus", "x squared",
             "over|divided by", "2|two", "factorial"],
        )
        self.assertTrue(ok, missing)

    def test_order_matters(self):
        ok, missing = modality_pairs.spoken_in_order("b is not equal to a",
                                                     ["a", "is not equal to", "b"])
        self.assertFalse(ok)

    def test_the_current_code_can_be_scored(self):
        rows = modality_pairs.score()
        self.assertEqual(len(rows), len(modality_pairs.pairs()))
        for row in rows:
            self.assertIn("preserved", row)


class JudgeCacheTests(unittest.TestCase):
    """A reused verdict must have been given to exactly the same input."""

    def key(self, **change):
        from evals.judge import _cache_key
        parts = {"model": "judge", "fingerprint": "abc", "history": [("hi", "hello")],
                 "said": "what is a derivative?", "reply": "The rate of change.",
                 "names": {"accurate": {}, "concrete_first": {}}}
        parts.update(change)
        return _cache_key(parts["model"], parts["fingerprint"], parts["history"],
                          parts["said"], parts["reply"], parts["names"])

    def test_same_input_same_key(self):
        self.assertEqual(self.key(), self.key(names={"concrete_first": {}, "accurate": {}}))

    def test_anything_the_verdict_depends_on_changes_the_key(self):
        for change in ({"model": "other"}, {"fingerprint": "abd"}, {"history": []},
                       {"said": "what is an integral?"}, {"reply": "The slope."},
                       {"names": {"accurate": {}}}):
            self.assertNotEqual(self.key(), self.key(**change), change)


if __name__ == "__main__":
    unittest.main()
