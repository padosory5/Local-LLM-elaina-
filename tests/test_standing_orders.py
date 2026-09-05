"""What the person tells her once, and never has to tell her again.

Nine dogfooding sessions produced a long tail that is not really bugs:
one word misheard the same way every week, one fact she cannot know until
somebody says it. Each was fixed with code, and each fix cost a session to
find and a session to validate. That is right for a class of problem and
hopeless for one person's tail, because the tail is different for every
person and none of it can be tested in advance.

So the person states the rule once and it holds, in two files they own:

    runtime/data/directives.yaml   what to do differently, always
    runtime/data/about_me.yaml     what she knows about them

The design decision worth testing is that a directive is **executed, not
read**. A `say` rule is applied by code before anything looks at the turn;
it is not advice handed to a model that has nine sessions of evidence
against it honouring advice.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain import standing_orders
from brain.standing_orders import StandingOrders


class ReadingAnInstructionTests(unittest.TestCase):
    """A standing rule is stated on purpose, never inferred."""

    def test_the_forms_a_rule_is_actually_stated_in(self):
        for said, expected in (
            ("always make opennaver.com open naver.com",
             ("repair", "opennaver.com", "naver.com")),
            ("Always make opennaver.com to open naver.com.",
             ("repair", "opennaver.com", "naver.com")),
            ("when I say opennaver.com I mean naver.com",
             ("repair", "opennaver.com", "naver.com")),
            ("from now on, when I say laver I mean naver",
             ("repair", "laver", "naver")),
            ("whenever I say brass control I mean browser control",
             ("repair", "brass control", "browser control")),
        ):
            with self.subTest(said=said):
                self.assertEqual(standing_orders.read_instruction(said), expected)

    def test_a_fact_about_the_person(self):
        kind, fact, _ = standing_orders.read_instruction(
            "Remember that my school is the University of Washington.",
        )

        self.assertEqual(kind, "fact")
        self.assertEqual(fact, "my school is the University of Washington")

    def test_an_ordinary_turn_states_no_rule(self):
        # The over-correction that matters most: this runs before the
        # router on every single turn.
        for said in (
            "open naver.com",
            "what time is it in Seattle?",
            "I meant only one S",
            "Find me an electric guitar under 500,000 won.",
            "forget it",
            "stop it",
            "yeah",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    standing_orders.read_instruction(said), ("", "", ""), said,
                )


class ARuleIsExecutedNotReadTests(unittest.TestCase):

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="elaina-orders-"))
        self.orders = StandingOrders.load(
            directives_path=self.directory / "directives.yaml",
            about_me_path=self.directory / "about_me.yaml",
        )

    def test_the_repair_is_applied_to_the_turn(self):
        self.orders.remember_repair("opennaver.com", "naver.com")

        said, applied = self.orders.heard_as("opennaver.com")

        self.assertEqual(said, "naver.com")
        self.assertTrue(applied)

    def test_a_turn_no_rule_covers_is_untouched(self):
        self.orders.remember_repair("opennaver.com", "naver.com")

        said, applied = self.orders.heard_as("open zillow.com")

        self.assertEqual(said, "open zillow.com")
        self.assertEqual(applied, "")

    def test_a_rule_replaces_the_earlier_one_for_the_same_words(self):
        self.orders.remember_repair("laver", "naver")
        self.orders.remember_repair("laver", "layer")

        self.assertEqual(
            [(rule.heard, rule.means) for rule in self.orders.say],
            [("laver", "layer")],
        )

    def test_a_rule_that_says_nothing_is_refused(self):
        self.assertFalse(self.orders.remember_repair("naver.com", "naver.com"))
        self.assertFalse(self.orders.remember_repair("", "naver.com"))


class BothFilesOutliveARestartTests(unittest.TestCase):

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp(prefix="elaina-orders-"))
        self.paths = {
            "directives_path": self.directory / "directives.yaml",
            "about_me_path": self.directory / "about_me.yaml",
        }

    def test_what_was_written_is_there_next_time(self):
        first = StandingOrders.load(**self.paths)
        first.remember_repair("opennaver.com", "naver.com")
        first.remember_fact("I'm moving to Seattle on September 18")
        first.remember_note("check Naver Maps before Google")

        again = StandingOrders.load(**self.paths)

        self.assertEqual(
            [(rule.heard, rule.means) for rule in again.say],
            [("opennaver.com", "naver.com")],
        )
        self.assertIn("I'm moving to Seattle on September 18", again.facts)
        self.assertIn("check Naver Maps before Google", again.notes)

    def test_a_file_the_person_has_broken_costs_a_rule_not_a_startup(self):
        # These are hand-editable, so a stray tab in one must not stop her
        # from starting.
        self.paths["directives_path"].write_text(
            "say: [ this is not\n  valid: yaml", encoding="utf-8",
        )

        orders = StandingOrders.load(**self.paths)

        self.assertEqual(orders.say, [])

    def test_missing_files_are_simply_empty(self):
        orders = StandingOrders.load(**self.paths)

        self.assertEqual(orders.say, [])
        self.assertEqual(orders.facts, [])
        self.assertEqual(orders.context_text(), "")

    def test_forgetting_removes_it_from_the_file(self):
        first = StandingOrders.load(**self.paths)
        first.remember_repair("opennaver.com", "naver.com")
        first.remember_fact("my school is the University of Washington")

        first.forget("opennaver.com")

        self.assertEqual(StandingOrders.load(**self.paths).say, [])
        self.assertEqual(
            len(StandingOrders.load(**self.paths).facts), 1,
        )


class ThroughAWholeTurnTests(unittest.TestCase):
    """The rule reaches the turn, and the turn that manages rules is safe."""

    def setUp(self):
        from tests.turn_harness import build_engine

        self.engine = build_engine()

    def tearDown(self):
        self.engine.close()

    def _say(self, text):
        return self.engine._route_turn(text, timings={})

    def test_a_rule_stated_out_loud_is_written_and_then_applied(self):
        stated = self._say("Always make opennaver.com open naver.com.")
        self.assertIn("naver.com", stated.locked_response)

        later = self._say("opennaver.com")

        self.assertEqual(later.user_input, "naver.com")

    def test_a_rule_may_not_rewrite_the_turn_that_removes_it(self):
        # Found while building this: the say rule rewrote "forget the rule
        # about opennaver.com" into "...about naver.com", so nothing was
        # dropped and she answered as if asked something else.
        self._say("Always make opennaver.com open naver.com.")

        self._say("Forget the rule about opennaver.com.")

        self.assertEqual(self.engine.standing_orders.say, [])

    def test_a_fact_reaches_the_answering_context(self):
        self._say("Remember that my school is the University of Washington.")

        self.assertIn(
            "University of Washington",
            self.engine.standing_orders.context_text(),
        )

    def test_a_preference_still_belongs_to_the_preference_reader(self):
        # The over-correction to watch: preferences have a typed home with
        # their own standing and confidence, which is strictly better than
        # free prose. This must not be claimed as a standing note.
        reply = self._say(
            "From now on use Spotify whenever I ask you to play music.",
        )

        self.assertIn("Spotify", reply.locked_response)
        self.assertEqual(self.engine.standing_orders.notes, [])


if __name__ == "__main__":
    unittest.main()
