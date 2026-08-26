import unittest

from brain.capabilities import CapabilityRegistry


ALL_ON = {
    "computer_control_mode": True,
    "browser_control_enabled": True,
    "web_search_enabled": True,
    "screen_vision_enabled": True,
    "project_access": True,
}
MODE_OFF = {**ALL_ON, "computer_control_mode": False}


class CapabilityAvailabilityTests(unittest.TestCase):
    def test_browser_control_needs_both_the_mode_and_the_config_switch(self):
        self.assertTrue(CapabilityRegistry.is_available("browser_control", ALL_ON))
        self.assertFalse(CapabilityRegistry.is_available("browser_control", MODE_OFF))
        self.assertFalse(CapabilityRegistry.is_available(
            "browser_control", {**ALL_ON, "browser_control_enabled": False},
        ))

    def test_a_blocked_capability_names_the_switch_that_turns_it_on(self):
        capability = CapabilityRegistry.get("browser_control")

        reason = CapabilityRegistry.blocked_reason(capability, MODE_OFF)
        fix = CapabilityRegistry.fix_for(capability, MODE_OFF)

        self.assertIn("Desktop Control Mode is off", reason)
        self.assertIn("Computer Control toggle", fix)

    def test_an_available_capability_has_no_blocked_reason(self):
        capability = CapabilityRegistry.get("browser_control")

        self.assertEqual(CapabilityRegistry.blocked_reason(capability, ALL_ON), "")

    def test_an_unknown_capability_is_never_reported_as_available(self):
        self.assertFalse(CapabilityRegistry.is_available("teleportation", ALL_ON))
        self.assertIsNone(CapabilityRegistry.get("teleportation"))


class CapabilityMatchingTests(unittest.TestCase):
    """The deterministic fallback used exactly where the model's own
    routing already failed -- so it must not depend on the model."""

    def _match(self, text: str) -> str:
        match = CapabilityRegistry.match(text)
        return match.capability.id if match.matched else ""

    def test_naming_the_browser_selects_browser_control(self):
        self.assertEqual(self._match("check the price on the browser"), "browser_control")
        self.assertEqual(self._match("open trip.com and look at the rooms"), "browser_control")
        self.assertEqual(self._match("click the first result on the page"), "browser_control")

    def test_asking_to_confirm_something_directly_selects_browser_control(self):
        # The transcript case: the user doubts a search snippet and wants
        # the real page checked.
        self.assertEqual(
            self._match("can you actually check that for real"), "browser_control",
        )

    def test_naming_a_native_app_or_file_selects_desktop_control(self):
        self.assertEqual(self._match("open Spotify"), "ui_control")
        self.assertEqual(self._match("create a folder on my Desktop"), "ui_control")

    def test_pointing_at_the_screen_selects_screen_vision(self):
        self.assertEqual(self._match("what's on my screen"), "screen_analysis")

    def test_opening_a_site_to_read_it_is_browser_not_desktop_control(self):
        # Found live: "open wikipedia and tell me what the article says"
        # matched desktop control, which would have hunted for an installed
        # application called "wikipedia". Asking to be told what something
        # *says* is a content request; the signal is the reporting verb,
        # not a hardcoded list of website names.
        self.assertEqual(
            self._match("Open wikipedia and tell me what the article on Busan says"),
            "browser_control",
        )
        self.assertEqual(
            self._match("check youtube for python tutorials and tell me what comes up"),
            "browser_control",
        )

    def test_acting_inside_a_native_app_is_still_desktop_control(self):
        # The other half: "open X and do something in it" is an app action
        # and must not be dragged into the browser.
        for text in ("open Spotify and play a song", "open Notepad", "close Discord"):
            with self.subTest(text=text):
                self.assertEqual(self._match(text), "ui_control")

    def test_nothing_matches_rather_than_guessing(self):
        self.assertEqual(self._match("how are you feeling today"), "")
        self.assertEqual(self._match(""), "")

    def test_an_ability_question_is_recognised_as_a_question(self):
        self.assertTrue(CapabilityRegistry.is_ability_question("can you control my browser?"))
        self.assertTrue(CapabilityRegistry.is_ability_question("what can you do"))
        self.assertFalse(CapabilityRegistry.is_ability_question("open the browser"))


class CapabilityExampleTests(unittest.TestCase):
    """Each capability's ``examples`` are the phrasings its match pattern
    is meant to catch, so they double as the pattern's regression net --
    edit a pattern and break an example, and this fails."""

    def test_every_example_resolves_to_its_own_capability(self):
        from brain.capabilities import CAPABILITIES

        for capability in CAPABILITIES:
            for example in capability.examples:
                with self.subTest(capability=capability.id, example=example):
                    match = CapabilityRegistry.match(example)
                    if capability.id in {
                        "browser_control", "ui_control", "screen_analysis",
                        "web_search",
                    }:
                        self.assertTrue(match.matched, "no capability matched")
                        self.assertEqual(match.capability.id, capability.id)

    def test_every_capability_is_described_and_reachable_by_id(self):
        from brain.capabilities import CAPABILITIES

        for capability in CAPABILITIES:
            with self.subTest(capability=capability.id):
                self.assertTrue(capability.name)
                self.assertTrue(capability.summary)
                self.assertTrue(capability.offer_when)
                self.assertIs(CapabilityRegistry.get(capability.id), capability)


class CapabilityContextTests(unittest.TestCase):
    def test_the_prompt_block_marks_unavailable_abilities_with_their_fix(self):
        text = CapabilityRegistry.context_text(MODE_OFF)

        self.assertIn("browser control", text)
        self.assertIn("unavailable: Desktop Control Mode is off", text)
        self.assertIn("fix: turn on the Computer Control toggle", text)

    def test_the_prompt_block_forbids_calling_a_real_ability_unsupported(self):
        # The exact drift the user heard live -- stated as a rule the model
        # is given every turn, backed by the registry it is generated from.
        text = CapabilityRegistry.context_text(ALL_ON)

        self.assertIn("Never say an ability listed here is unsupported", text)
        self.assertIn("Never promise to do something in a later turn", text)

    def test_the_inventory_sentence_separates_ready_from_switched_off(self):
        sentence = CapabilityRegistry.inventory_sentence(MODE_OFF)

        self.assertIn("Right now I can use", sentence)
        self.assertIn("Currently off:", sentence)
        self.assertIn("browser control", sentence)

    def test_a_recommendation_offers_an_available_ability(self):
        offer = CapabilityRegistry.recommendation_for("browser_control", ALL_ON)

        self.assertIn("browser control", offer)
        self.assertTrue(offer.endswith("?"))

    def test_a_recommendation_for_a_blocked_ability_states_the_fix_instead(self):
        offer = CapabilityRegistry.recommendation_for("browser_control", MODE_OFF)

        self.assertIn("Desktop Control Mode is off", offer)
        self.assertIn("Turn it on", offer)
        self.assertFalse(offer.endswith("?"))


if __name__ == "__main__":
    unittest.main()
