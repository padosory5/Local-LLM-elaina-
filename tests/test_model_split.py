"""Two models, split by job, and the check that says when that cannot work.

The split exists because language competence and structured reliability
came apart when measured -- a 27B scored 83% on the Korean dogfood arc
against qwen3:8b's 58%, and produced two dangerous false positives on the
router matrix plus 13 JSON repair retries. Better words, worse judgement.

The class that matters most here is
:class:`AConfigurationThatCannotWorkSaysSoTests`. Both models stay
resident because every turn uses both; if they do not fit together,
Ollama evicts and reloads one each turn, and the split becomes far worse
than either model alone -- about 24s per turn when that last happened.
Nothing in code can make them fit. It can refuse to be quiet about it.
"""

import unittest

from brain import model_split


class FakeClient:
    def __init__(self, models=None, fail=False):
        self._models = models or {}
        self._fail = fail

    def list(self):
        if self._fail:
            raise RuntimeError("ollama is not running")
        return {
            "models": [
                {"model": name, "size": int(gb * 1024 ** 3)}
                for name, gb in self._models.items()
            ]
        }


TWO_EIGHTS = FakeClient({"qwen3:8b": 4.9, "ko-qwen3:8b": 5.2})
EIGHT_AND_27 = FakeClient({"qwen3:8b": 4.9, "big:27b": 12.6})


class OneModelForBothIsSilentTests(unittest.TestCase):
    """The previous behaviour, and it deserves no log line."""

    def test_an_empty_conversation_model_says_nothing(self):
        self.assertEqual(
            model_split.report(TWO_EIGHTS, "qwen3:8b", "", vram_gb=16.0), "",
        )

    def test_the_same_model_twice_says_nothing(self):
        self.assertEqual(
            model_split.report(
                TWO_EIGHTS, "qwen3:8b", "qwen3:8b", vram_gb=16.0,
            ),
            "",
        )


class ASplitThatFitsIsReportedPlainlyTests(unittest.TestCase):

    def test_it_names_both_jobs_and_both_sizes(self):
        said = model_split.report(
            TWO_EIGHTS, "qwen3:8b", "ko-qwen3:8b", vram_gb=16.0,
        )
        self.assertIn("decisions", said)
        self.assertIn("speech", said)
        self.assertIn("qwen3:8b", said)
        self.assertIn("ko-qwen3:8b", said)
        self.assertNotIn("WARNING", said)

    def test_two_eight_billion_models_fit_a_sixteen_gig_card(self):
        """The configuration this whole split was built to enable."""
        self.assertTrue(model_split.fits(4.9, 5.2, 16.0))


class AConfigurationThatCannotWorkSaysSoTests(unittest.TestCase):
    """The reason this module exists."""

    def test_a_thirteen_gig_model_beside_an_eight_does_not_fit(self):
        self.assertFalse(model_split.fits(4.9, 12.6, 16.0))

    def test_the_warning_is_loud_and_says_what_to_do(self):
        said = model_split.report(
            EIGHT_AND_27, "qwen3:8b", "big:27b", vram_gb=16.0,
        )
        self.assertIn("WARNING", said)
        # The cost, so it reads as a real consequence rather than a nag.
        self.assertIn("24s", said)
        # And a way out.
        self.assertIn("conversation_model", said)

    def test_a_bigger_card_makes_the_same_pair_fine(self):
        said = model_split.report(
            EIGHT_AND_27, "qwen3:8b", "big:27b", vram_gb=32.0,
        )
        self.assertNotIn("WARNING", said)


class ItNeverGuessesWhatItCannotMeasureTests(unittest.TestCase):
    """A fit check that invents a number is worse than none.

    It would either cry wolf or hand a machine a clean bill of health it
    never earned.
    """

    def test_an_unreadable_card_is_reported_as_unverified(self):
        said = model_split.report(
            TWO_EIGHTS, "qwen3:8b", "ko-qwen3:8b", vram_gb=0.0,
        )
        self.assertIn("unverified", said)
        self.assertNotIn("WARNING", said)

    def test_an_unknown_model_size_is_reported_as_unverified(self):
        said = model_split.report(
            TWO_EIGHTS, "qwen3:8b", "never-pulled:8b", vram_gb=16.0,
        )
        self.assertIn("unverified", said)

    def test_a_missing_ollama_does_not_raise(self):
        said = model_split.report(
            FakeClient(fail=True), "qwen3:8b", "other:8b", vram_gb=16.0,
        )
        self.assertIn("unverified", said)

    def test_zero_sizes_are_treated_as_fitting(self):
        """Unknown must not be reported as broken."""
        self.assertTrue(model_split.fits(0.0, 5.2, 16.0))


class TheEngineReadsTheConfigTests(unittest.TestCase):

    def test_the_conversation_model_defaults_to_the_decision_model(self):
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine.__init__)
        self.assertIn("conversation_model", source)
        # Empty must fall back, so an untouched config behaves as before.
        self.assertIn("or self.model", source)

    def test_only_the_speaking_surface_moved(self):
        """Routing, consent and planning must stay on the decision model.

        The 27B measurement is the reason: it read "Disable Smart App
        Control" as an action to perform.
        """
        import inspect

        from brain.chat_engine import ChatEngine

        source = inspect.getsource(ChatEngine.__init__)
        for structured in (
            "SemanticIntentRouter", "SemanticConsentClassifier",
            "DesktopActionPlanner", "BrowserActionPlanner", "TaskPlanner",
        ):
            with self.subTest(component=structured):
                start = source.index(structured)
                window = source[start:start + 400]
                self.assertIn("model=self.model", window, structured)
                self.assertNotIn("model=self.conversation_model", window)


if __name__ == "__main__":
    unittest.main()
