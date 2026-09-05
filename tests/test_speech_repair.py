"""Some turns have a vocabulary, and a transcriber does not know it.

Session 9. Normal speech kept producing targets nobody could act on:

    "browser control"   -> "brass control"
    "open host.example" -> "openhost.example"
    "naver.com"         -> "laver.com"

and each one failed differently. She said "I've opened the brass
control", inventing an ability she does not have. The fused address was
routed to a web search and then described as having been opened. The
misheard domain was corrected by the person -- "it's not an L, it's an
N" -- and the correction became a web search for the phrase "correct
entity from L to N".

The answer is not a homophone table. It is that two of the things being
misheard are *closed vocabularies*: Elaina's own abilities are a list of
eight, and an address is a string with a grammar. A near-miss inside a
closed set can be repaired; a near-miss of nothing must be asked about,
never invented.
"""

from __future__ import annotations

import unittest

from brain import browser_progress
from brain.capabilities import CapabilityRegistry


class HerOwnAbilitiesAreAClosedListTests(unittest.TestCase):
    """S9-02. Vowels are what a transcriber loses, so consonants decide."""

    def test_a_near_miss_of_a_real_ability_is_repaired(self):
        for said, meant in (
            ("Yeah, I'm talking about the brass control.", "browser control"),
            ("use my desk control", "desktop control"),
            ("the scream vision thing", "screen vision"),
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    CapabilityRegistry.repair_spoken_name(said)[1], meant, said,
                )

    def test_a_real_request_that_is_not_one_of_hers_is_left_alone(self):
        # The over-correction to watch, and the reason edit distance on the
        # whole phrase is not enough: "mouse control" scores as highly
        # against "browser control" as "brass control" does, because the
        # head noun is shared. On consonants alone, mouse/browser is 0.29
        # and brass/browser is 0.67.
        for said in (
            "can you do mouse control",
            "turn up the volume control",
            "use the remote control",
            "keyboard control please",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    CapabilityRegistry.repair_spoken_name(said), ("", ""), said,
                )

    def test_an_ability_she_really_has_is_not_rewritten(self):
        for said in (
            "use the browser control", "use web search", "desktop control",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    CapabilityRegistry.repair_spoken_name(said), ("", ""), said,
                )

    def test_the_repair_reaches_the_turn_before_anything_reads_it(self):
        from tests.turn_harness import build_engine

        engine = build_engine()
        try:
            routing = engine._route_turn(
                "Yeah, I'm talking about the brass control.", timings={},
            )
        finally:
            engine.close()

        self.assertIn("browser control", routing.user_input)
        self.assertNotIn("brass", routing.user_input)


class SheNeverInventsAnAbilityTests(unittest.TestCase):
    """S9-02, the half the repair cannot reach.

        You said: Yeah, I'm talking about the brass control.
        Elaina:   I've opened the brass control.

    Being told a thing exists is worse than being told she cannot do it.
    """

    def _engine(self):
        from tests.turn_harness import build_engine

        return build_engine()

    def test_claiming_an_ability_she_does_not_have_is_refused(self):
        engine = self._engine()
        try:
            reply = engine._refuse_invented_capability(
                "I've opened the mouse control.", "can you do mouse control",
            )
        finally:
            engine.close()

        self.assertNotIn("I've opened", reply)
        self.assertIn("don't have", reply)

    def test_talking_about_one_is_not_claiming_it(self):
        # The over-correction: she must still be able to say she cannot.
        engine = self._engine()
        try:
            for reply in (
                "Mouse control isn't something I can do.",
                "I don't have a mouse control, but I can drive the browser.",
            ):
                with self.subTest(reply=reply):
                    self.assertEqual(
                        engine._refuse_invented_capability(
                            reply, "can you do mouse control",
                        ),
                        reply,
                    )
        finally:
            engine.close()

    def test_an_ability_she_has_is_reported_normally(self):
        engine = self._engine()
        try:
            reply = "I've opened the browser control."
            self.assertEqual(
                engine._refuse_invented_capability(
                    reply, "use the browser control",
                ),
                reply,
            )
        finally:
            engine.close()


class AnAddressIsAStringWithAGrammarTests(unittest.TestCase):
    """S9-03. A correction to a letter is an edit, not a research topic.

        [Router] Interpreted transcript as: correct entity from L to N
        [Tool] Searching web for: correct entity from L to N
    """

    def test_a_letter_is_swapped_in_the_site_name(self):
        for said in (
            "It's not an L, it's an N.",
            "not L, N",
            "no, it's not an L, it's an N",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_progress.resubstituted_address("laver.com", said),
                    "naver.com", said,
                )

    def test_only_the_site_name_is_edited(self):
        # Nobody respells the registry. "not a C, an M" on .com is not a
        # correction anybody makes.
        self.assertEqual(
            browser_progress.resubstituted_address(
                "laver.com", "not a C, an M",
            ),
            "",
        )

    def test_an_ambiguous_swap_is_refused(self):
        # Two of the letter in the name means either could be meant.
        self.assertEqual(
            browser_progress.resubstituted_address(
                "assess.com", "not an S, an X",
            ),
            "",
        )

    def test_an_ordinary_sentence_is_not_a_letter_swap(self):
        for said in (
            "I want a studio", "only one S", "that's not it",
            "Bro, I told you it's an N.",
        ):
            with self.subTest(said=said):
                self.assertEqual(
                    browser_progress.resubstituted_address("laver.com", said),
                    "", said,
                )

    def test_the_correction_goes_back_to_the_browser(self):
        from brain.intent_router import IntentDecision
        from tests.turn_harness import build_engine

        engine = build_engine()
        engine._last_computer_action = "open_url"
        engine._last_computer_goal = "laver.com"
        engine._turn_points_at_the_last_action = False
        try:
            route, _ = engine._rescue_capability_route(
                IntentDecision(
                    intent="entity_correction", confidence=0.9,
                    normalized_request="correct entity from L to N",
                    reason="t",
                ),
                "It's not an L, it's an N.",
            )
        finally:
            engine.close()

        self.assertEqual(route.computer_operation, "open_url")
        self.assertEqual(route.action_target, "naver.com")


class AnAddressAndNothingElseIsARequestToGoThereTests(unittest.TestCase):
    """S9-08.

        You said: openhost.example
        [Router] Interpreted transcript as: search for openhost.example
        Elaina: I've checked openhost.example
        Elaina: I've opened openhost.example

    A web search, described afterwards as an opening. Nobody says a bare
    domain to mean "tell me about this website". Sending it to the browser
    is also what puts it in front of the navigation lifecycle, which is
    the layer that can find out the address does not resolve.
    """

    class _Scripted:
        def __init__(self, payload):
            self.payload = payload

        def chat(self, **_kwargs):
            import json

            return {"message": {"content": json.dumps(self.payload)}}

    def _route(self, said):
        from brain.intent_router import SemanticIntentRouter

        return SemanticIntentRouter(
            self._Scripted({
                "intent": "web_search", "confidence": 0.95,
                "normalized_request": "search for it",
                "reason": "a website lookup", "computer_operation": "none",
            }),
            "qwen3:8b",
        ).route(said, computer_control_enabled=True)

    def test_a_bare_address_opens(self):
        for said in ("openhost.example", "naver.com", "iss.washington.edu"):
            with self.subTest(said=said):
                route = self._route(said)
                self.assertEqual(route.computer_operation, "open_url", said)
                self.assertEqual(route.action_target, said, said)

    def test_a_sentence_about_a_site_is_still_a_question(self):
        # The over-correction: mentioning a site is not asking to go there.
        for said in (
            "what is naver.com about?",
            "is naver.com any good",
            "I read that on naver.com",
        ):
            with self.subTest(said=said):
                self.assertNotEqual(
                    self._route(said).computer_operation, "open_url", said,
                )


if __name__ == "__main__":
    unittest.main()
