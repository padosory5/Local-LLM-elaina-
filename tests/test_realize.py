"""Phase 3A: one reply, two realizations.

The display keeps what was written: notation, dashes, line breaks, list
structure. The voice gets the same substance said for the ear. These are
properties, checked on expressions of their own -- not the modality pairs in
evals/modality_pairs.json, which stay a measurement.
"""

import unittest

from brain import conversation_style, realize
from brain.spoken_notation import verbalize


class DisplayKeepsWhatWasWrittenTests(unittest.TestCase):

    def test_operators_survive_on_screen(self):
        for written in (
            "The slope is (y - b) / m here.",
            "Expand it as (a - b)² = a² - 2ab + b².",
            "Then 7 - 4 = 3, as expected.",
            "Use g(t) - g(0) for the change.",
            "Values run 1-5 on the scale.",
            "The ratio a/b stays fixed.",
        ):
            with self.subTest(written=written):
                self.assertIn(written.rstrip("."), realize.display(written))

    def test_notation_at_the_start_is_not_capitalised(self):
        for opening in ("g(t) = 3t + 1", "k! grows fast", "y' = 2y", "z ≥ 0 always", "tan(θ) is odd"):
            with self.subTest(opening=opening):
                self.assertTrue(realize.display(opening).startswith(opening))

    def test_prose_at_the_start_still_is(self):
        self.assertTrue(realize.display("i think so. a lot of it.").startswith("I think"))
        self.assertTrue(realize.display("a lot of people do.").startswith("A lot"))

    def test_line_breaks_and_lists_are_kept(self):
        shown = realize.display("Two options:\n- the train\n- the bus")
        self.assertEqual(shown, "Two options:\n• the train\n• the bus.")

    def test_markup_the_window_cannot_draw_is_removed(self):
        shown = realize.display("## Result\n**Bold** and *soft* text, `code` too.")
        self.assertEqual(shown, "Result\nBold and soft text, code too.")

    def test_multiplication_stars_are_not_emphasis(self):
        self.assertIn("3*4", realize.display("It is 3*4 here."))
        self.assertIn("p * q", realize.display("It is p * q here."))

    def test_internals_never_reach_the_screen(self):
        self.assertEqual(
            realize.display("Happy to dig into a travel_itinerary if that helps."),
            "Happy to dig into a travel itinerary if that helps.",
        )
        self.assertNotIn("ようで", realize.display("도움이 되었ようで 다행입니다. 좋습니다."))

    def test_a_web_address_is_shown_as_written(self):
        shown = realize.display("See https://example.com/some_path for more.")
        self.assertIn("https://example.com/some_path", shown)


class SpeechSaysTheSameThingTests(unittest.TestCase):

    def test_arithmetic_and_algebra(self):
        self.assertEqual(verbalize("9 - 2 = 7"), "9 minus 2 equals 7")
        self.assertEqual(verbalize("y = mx + b"), "y equals mx plus b")
        self.assertIn("a squared minus 2ab plus b squared",
                      verbalize("(a - b)² = a² - 2ab + b²"))

    def test_functions_primes_and_factorials(self):
        self.assertEqual(verbalize("g'(t)"), "g prime of t")
        self.assertIn("k factorial", verbalize("k! = k × (k - 1)!"))
        self.assertIn("tangent of", verbalize("tan(y)"))

    def test_powers(self):
        self.assertIn("to the fifth", verbalize("t⁵"))
        self.assertIn("to the power of 12", verbalize("3^12"))
        self.assertIn("to the power of n plus 1", verbalize("2^(n+1)"))

    def test_prose_punctuation_stays_prose(self):
        for prose in (
            "It is a well-known, up-to-date fact.",
            "Open 24/7 and/or by appointment.",
            "That's wonderful!",
            "Call 206-555-0142 on 2026-10-01.",
        ):
            with self.subTest(prose=prose):
                self.assertEqual(verbalize(prose), prose)

    def test_units_and_symbols(self):
        self.assertIn("5 kilograms", verbalize("5 kg"))
        self.assertIn("30 degrees Celsius", verbalize("30°C"))
        self.assertIn("15 percent", verbalize("15%"))
        self.assertIn("for example,", verbalize("e.g. this"))

    def test_korean_readings(self):
        self.assertIn("빼기", verbalize("7 - 4", "ko"))
        self.assertIn("5분의 2", verbalize("2/5", "ko"))
        self.assertIn("제곱", verbalize("y²", "ko"))

    def test_unknown_latex_is_pointed_at_not_guessed(self):
        self.assertIn("the formula shown on screen", verbalize(r"$\mathcal{L}(x)$"))
        self.assertIn("over", verbalize(r"$\frac{a}{b}$"))

    def test_money_is_not_latex(self):
        self.assertIn("$5", verbalize("It costs $5-$9 today."))

    def test_speech_drops_visual_only_formatting(self):
        spoken = realize.speech("Steps:\n• boil water\n• add tea\nSee https://x.test/a_b.")
        self.assertNotIn("•", spoken)
        self.assertNotIn("https", spoken)
        self.assertIn("boil water", spoken)

    def test_speech_keeps_the_substance(self):
        for shown in (
            "The train leaves at 7:45 from platform 3.",
            "g(t) = 3t + 1, so g(2) = 7.",
            "Options:\n• Tokyo\n• Osaka",
        ):
            with self.subTest(shown=shown):
                self.assertGreaterEqual(realize.coverage(shown, realize.speech(shown)), 0.9)

    def test_coverage_notices_a_dropped_value(self):
        self.assertLess(realize.coverage("It is 42 km to Kyoto.", "It is far to Kyoto."), 0.9)


class DisplayRepairTests(unittest.TestCase):

    def test_opens_with_notation(self):
        for text in ("f(x) = 1", "n! grows", "x ≤ 2", "e^x", "x₁ + x₂", "cos(t)", "d/dx x²"):
            self.assertTrue(conversation_style.opens_with_notation(text), text)
        for text in ("a lot of it", "i'm fine", "ok then", "it is 5"):
            self.assertFalse(conversation_style.opens_with_notation(text), text)


class WholeTurnTests(unittest.TestCase):
    """Through ChatEngine.chat(): what the screen and the voice actually get."""

    def test_notation_reaches_the_screen_as_written(self):
        from core import turn_trace
        from tests.turn_harness import build_engine

        engine = build_engine({
            "general form of a Taylor": {
                "intent": "knowledge_question", "confidence": 1.0,
                "normalized_request": "What is the general form of a Taylor series?",
                "speech_act": "information_request",
                "information_freshness": "stable",
                "requires_external_evidence": False,
            },
        })
        engine.client.reply = (
            "It starts f(x) ≈ f(a) + f'(a)(x - a), and each term adds a "
            "higher power of (x - a)."
        )
        shown = engine.chat("What's the general form of a Taylor series?")
        self.assertIn("(x - a)", shown)
        record = turn_trace.last().as_record()
        self.assertEqual(record["display"], shown)
        self.assertFalse(
            [step["name"] for step in record["steps"]
             if step["name"] in {"speech_filter", "natural_dashes", "final_speech_filter"}],
        )
        # And the voice gets it said.
        self.assertIn("x minus a", realize.speech(shown))
        self.assertIn("f prime of a", realize.speech(shown))


if __name__ == "__main__":
    unittest.main()
