"""What she says while she works, and what she does not say.

Four things decide whether this system is an improvement on the flat list it
replaced: the line matches the work, it does not repeat, it costs nothing, and
it stays quiet when speaking would only be noise. Each is asserted here.
"""

from __future__ import annotations

import random
import unittest
from pathlib import Path

from brain.action_status import (
    ACTION_BY_INTENT,
    ACTIONS,
    PHASES,
    ActionStatusSelector,
    StatusContext,
    action_for_intent,
    is_continuation,
)
from tests.turn_harness import build_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _selector(**kwargs) -> ActionStatusSelector:
    """A selector whose choices are reproducible, so a failure is debuggable."""
    kwargs.setdefault("rng", random.Random(20260829))
    return ActionStatusSelector(**kwargs)


class BankShapeTests(unittest.TestCase):
    """The banks have to be big enough for the filters to work."""

    def test_every_action_can_be_announced(self):
        selector = _selector()

        for action in ACTIONS:
            with self.subTest(action=action):
                line = selector.select(StatusContext(action=action, force=True))
                self.assertTrue(line, f"{action} produced nothing")

    def test_every_phase_can_be_spoken(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                selector = _selector()
                line = selector.select(StatusContext(phase=phase, force=True))
                self.assertTrue(line, f"{phase} produced nothing")

    def test_each_bank_has_room_to_avoid_repeating(self):
        # The filter bars the last 5 lines and the last 3 openings. A bank
        # smaller than 4 would be forced to repeat immediately.
        selector = _selector()

        for action in ACTIONS:
            with self.subTest(action=action):
                options = selector._options(StatusContext(action=action))
                self.assertGreaterEqual(len(options), 4, f"{action} bank is thin")
                self.assertEqual(len(options), len(set(options)))

    def test_lines_stay_short_and_speakable(self):
        selector = _selector()

        for action in ACTIONS:
            for line in selector._options(StatusContext(action=action)):
                with self.subTest(line=line):
                    self.assertLessEqual(len(line.split()), 9)
                    self.assertNotIn("\n", line)

    def test_no_line_claims_the_work_is_finished(self):
        # The whole point of a status line is that the work is still running.
        # The old shared list let "Got it." start a search and close one.
        selector = _selector()
        forbidden = (
            "done", "finished", "completed", "found it", "here you go",
        )

        for action in ACTIONS:
            for line in selector._options(StatusContext(action=action)):
                with self.subTest(line=line):
                    lowered = line.casefold()
                    for phrase in forbidden:
                        self.assertNotIn(phrase, lowered)

    def test_no_line_tacks_on_an_offer_of_further_help(self):
        selector = _selector()
        forbidden = ("anything else", "let me know", "need help", "how can i")

        for phase in PHASES:
            for line in selector._options(StatusContext(phase=phase)):
                with self.subTest(line=line):
                    lowered = line.casefold()
                    for phrase in forbidden:
                        self.assertNotIn(phrase, lowered)


class ContextTests(unittest.TestCase):
    """The line has to reflect what she is actually about to do."""

    def test_searching_and_opening_do_not_share_a_bank(self):
        selector = _selector()

        searching = set(selector._options(StatusContext(action="searching")))
        opening = set(selector._options(StatusContext(action="opening")))

        self.assertFalse(
            searching & opening,
            "a search and an app launch would sound identical",
        )

    def test_continuing_overrides_the_action_bank(self):
        selector = _selector()

        resumed = selector._options(
            StatusContext(action="searching", continuing=True)
        )

        self.assertEqual(
            set(resumed),
            set(selector._options(StatusContext(action="continuing"))),
        )
        self.assertTrue(
            any("back" in line.casefold() or "continu" in line.casefold()
                for line in resumed),
            "a resumed search should sound resumed",
        )

    def test_low_confidence_hedges_instead_of_promising(self):
        selector = _selector()

        line = selector.select(StatusContext(action="searching", confidence=0.2))

        self.assertTrue(line)
        self.assertNotIn(
            line,
            selector._options(StatusContext(action="searching", confidence=1.0)),
        )

    def test_an_unknown_action_still_produces_something(self):
        selector = _selector()

        line = selector.select(StatusContext(action="teleporting", force=True))

        self.assertTrue(line)

    def test_an_unknown_phase_stays_silent_rather_than_guessing(self):
        selector = _selector()

        self.assertIsNone(
            selector.select(StatusContext(phase="interpretive_dance", force=True))
        )


class RepetitionTests(unittest.TestCase):
    """The complaint that started this phase."""

    def test_the_same_line_is_not_used_twice_in_a_row(self):
        selector = _selector()

        first = selector.select(StatusContext(action="searching"))
        second = selector.select(StatusContext(action="searching"))

        self.assertNotEqual(first, second)

    def test_a_run_of_one_action_never_repeats_within_the_window(self):
        selector = _selector()

        lines = [
            selector.select(StatusContext(action="searching"))
            for _ in range(5)
        ]

        self.assertEqual(len(lines), len(set(lines)), lines)

    def test_openings_vary_across_consecutive_lines(self):
        selector = _selector()

        openings = [
            selector._opening(selector.select(StatusContext(action="searching")))
            for _ in range(3)
        ]

        self.assertEqual(len(openings), len(set(openings)), openings)

    def test_twenty_mixed_actions_do_not_read_as_repetitive(self):
        # The manual bar from the phase brief, asserted rather than eyeballed:
        # no line may appear twice inside any window of five.
        selector = _selector()
        script = [
            "searching", "opening", "reading", "searching", "analyzing",
            "editing", "searching", "comparing", "creating", "opening",
            "checking", "searching", "reading", "executing", "analyzing",
            "searching", "opening", "editing", "checking", "comparing",
        ]

        lines = [
            selector.select(StatusContext(action=action, force=True))
            for action in script
        ]

        self.assertEqual(len(lines), 20)
        for index in range(len(lines)):
            window = lines[max(0, index - 4):index + 1]
            self.assertEqual(
                len(window), len(set(window)),
                f"repeated inside five turns at {index}: {window}",
            )

    def test_a_small_bank_answers_rather_than_going_silent(self):
        # Every filter falls back to the wider pool. Exhausting the fresh
        # options must still return a line, never None.
        selector = _selector()

        lines = [
            selector.select(StatusContext(phase="acknowledgement", force=True))
            for _ in range(12)
        ]

        self.assertTrue(all(lines))

    def test_memory_survives_across_turns_but_can_be_reset(self):
        selector = _selector()

        first = selector.select(StatusContext(action="searching"))
        self.assertIn(first, selector.recent)

        selector.reset()

        self.assertEqual(selector.recent, ())


class SilenceTests(unittest.TestCase):
    """Not every micro-action deserves a sentence."""

    def test_fast_work_is_not_announced(self):
        selector = _selector()

        # Opening an app finishes before the sentence would land.
        self.assertIsNone(selector.select(StatusContext(action="opening")))

    def test_slow_work_is_announced(self):
        selector = _selector()

        self.assertTrue(selector.select(StatusContext(action="searching")))

    def test_an_explicit_duration_overrides_the_typical_one(self):
        selector = _selector()

        self.assertTrue(selector.select(
            StatusContext(action="opening", expected_seconds=9.0)
        ))
        self.assertIsNone(selector.select(
            StatusContext(action="searching", expected_seconds=0.2)
        ))

    def test_a_result_is_always_worth_saying(self):
        selector = _selector()

        for phase in ("success", "failure", "permission_request"):
            with self.subTest(phase=phase):
                self.assertTrue(
                    selector.select(
                        StatusContext(phase=phase, expected_seconds=0.01)
                    )
                )

    def test_silence_does_not_consume_the_repetition_budget(self):
        selector = _selector()

        selector.select(StatusContext(action="opening"))

        self.assertEqual(selector.recent, ())


class LanguageTests(unittest.TestCase):
    def test_korean_produces_korean(self):
        selector = _selector(language="ko")

        line = selector.select(StatusContext(action="searching"))

        self.assertTrue(line)
        self.assertTrue(
            any("가" <= character <= "힣" for character in line),
            f"expected Hangul, got {line!r}",
        )

    def test_an_unsupported_language_falls_back_to_english(self):
        selector = _selector(language="fr")

        self.assertEqual(selector.language, "en")
        self.assertTrue(selector.select(StatusContext(action="searching")))

    def test_korean_banks_cover_every_action_and_phase(self):
        selector = _selector(language="ko")

        for action in ACTIONS:
            with self.subTest(action=action):
                self.assertTrue(selector._options(StatusContext(action=action)))
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.assertTrue(selector._options(StatusContext(phase=phase)))


class IntentMappingTests(unittest.TestCase):
    """Which intents announce, and which stay quiet."""

    def test_the_slow_intents_map_to_the_right_shape_of_work(self):
        self.assertEqual(action_for_intent("web_search"), "searching")
        self.assertEqual(action_for_intent("screen_analysis"), "analyzing")
        self.assertEqual(action_for_intent("project_question"), "reading")
        self.assertEqual(action_for_intent("project_edit"), "editing")
        self.assertEqual(action_for_intent("agent_create"), "creating")

    def test_an_unmapped_intent_announces_nothing(self):
        for intent in ("conversation", "computer_action", "", "fact_check"):
            with self.subTest(intent=intent):
                self.assertIsNone(action_for_intent(intent))

    def test_a_corrected_search_is_a_continuation(self):
        self.assertTrue(is_continuation("entity_correction"))
        self.assertFalse(is_continuation("web_search"))

    def test_every_mapped_action_has_a_bank(self):
        for intent, action in ACTION_BY_INTENT.items():
            with self.subTest(intent=intent):
                self.assertIn(action, ACTIONS)


class CostTests(unittest.TestCase):
    """The line covering a wait must not itself wait."""

    def test_selection_makes_no_model_call(self):
        # The selector is constructed without a client at all, which is the
        # structural version of this assertion: there is nothing to call.
        selector = _selector()

        self.assertFalse(hasattr(selector, "client"))

        for _ in range(50):
            selector.select(StatusContext(action="searching", force=True))


class EngineWiringTests(unittest.TestCase):
    """The real ChatEngine path, not the selector in isolation."""

    @classmethod
    def setUpClass(cls):
        cls.engine = build_engine()

    @classmethod
    def tearDownClass(cls):
        cls.engine.close()

    def setUp(self):
        self.engine.action_status.reset()
        self.statuses: list[str] = []
        self.engine.events.subscribe(
            "assistant_status",
            lambda event: self.statuses.append(event.data.get("text", "")),
        )

    def test_a_slow_intent_announces_a_line_from_the_bank(self):
        self.engine._announce_work_status("web_search", "find me hotels in Seoul")

        self.assertEqual(len(self.statuses), 1)
        self.assertIn(
            self.statuses[0],
            self.engine.action_status._options(StatusContext(action="searching")),
        )

    def test_announcing_costs_no_model_call(self):
        # The scripted client records every call it receives. A status line
        # must not appear among them -- that regression is what this phase
        # was for.
        before = len(self.engine.client.calls)

        self.engine._announce_work_status("web_search", "what's the rate now")

        self.assertEqual(len(self.engine.client.calls), before)

    def test_an_unmapped_intent_says_nothing(self):
        self.engine._announce_work_status("conversation", "how was your day")

        self.assertEqual(self.statuses, [])

    def test_a_corrected_search_sounds_like_a_continuation(self):
        self.engine._announce_work_status("entity_correction", "no, I said Qwen")

        self.assertEqual(len(self.statuses), 1)
        self.assertIn(
            self.statuses[0],
            self.engine.action_status._options(
                StatusContext(action="continuing")
            ),
        )

    def test_consecutive_searches_do_not_repeat(self):
        for _ in range(4):
            self.engine._announce_work_status("web_search", "something")

        self.assertEqual(len(self.statuses), len(set(self.statuses)))

    def test_the_status_is_spoken_and_not_only_displayed(self):
        # It reached the activity pill and nothing else, so a real search
        # turn left 9.5 seconds of silence between "working on it" and the
        # answer. The line exists to cover that wait; it has to be audible.
        self.engine.audio.spoken.clear()

        self.engine._announce_work_status("web_search", "find me hotels")

        self.assertEqual(self.engine.audio.spoken, self.statuses)

    def test_what_is_not_announced_is_not_spoken(self):
        self.engine.audio.spoken.clear()

        self.engine._announce_work_status("conversation", "how was your day")

        self.assertEqual(self.engine.audio.spoken, [])

    def test_the_spoken_status_survives_the_speech_filter(self):
        # AudioManager runs every line through TextFilter before Piper sees
        # it. A bank line that filtered down to nothing would be silently
        # dropped, which looks exactly like the bug this replaced.
        from brain.text_filter import TextFilter

        for action in ACTIONS:
            for line in self.engine.action_status._options(
                StatusContext(action=action)
            ):
                with self.subTest(line=line):
                    self.assertTrue(
                        TextFilter.for_configured_speech(
                            line, response_language="en",
                        ).strip(),
                        f"{line!r} would be filtered to silence",
                    )

    def test_the_dead_repetition_deque_is_gone(self):
        # It was appended to and never read: the anti-repetition this phase
        # asked for, started and never wired up.
        self.assertFalse(hasattr(self.engine, "_recent_work_statuses"))


class RendererContractTests(unittest.TestCase):
    """A status line is visually an ordinary Elaina message."""

    def test_status_event_uses_the_assistant_message_renderer(self):
        source = (
            PROJECT_ROOT / "desktop" / "renderer" / "app.js"
        ).read_text(encoding="utf-8")
        case = source.split('case "assistant_status":', 1)[1].split(
            "break;", 1
        )[0]

        self.assertIn("addAssistantMessage(message.text)", case)
        self.assertNotIn("addStatusMessage", case)

    def test_status_has_no_special_message_bubble_style(self):
        styles = (
            PROJECT_ROOT / "desktop" / "renderer" / "style.css"
        ).read_text(encoding="utf-8")

        self.assertNotIn(".message.status", styles)

if __name__ == "__main__":
    unittest.main()
