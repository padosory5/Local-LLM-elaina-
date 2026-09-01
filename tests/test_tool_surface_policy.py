"""Which of the four surfaces a request lands on, and why.

Every case here was a live tool-selection failure first. They are offline
because each fix is a pure function of the request and the router's own
fields -- no model needed to prove any of it, and a live-only check cannot
fail the build when someone edits a table.

The rule they exist to protect is the one the brief states outright:
**browser control is not warranted just because a request mentions a
website.** Two of these fixes pull in opposite directions around that line,
which is why both are pinned here.
"""

import unittest

from brain import capability_selection as caps
from brain.capability_selection import _is_live_state
from brain.deliberation import goal_intent


class HostNameTests(unittest.TestCase):
    """A host name says *where*, never *what*."""

    def test_a_domain_is_not_an_availability_request(self):
        # "booking.com" contains "booking", which the availability pattern
        # matched, so a research question was scored as a live availability
        # check and sent to browser control. General, not one site: expedia,
        # hotels and any other domain that is also a verb had the same
        # problem waiting.
        for request in (
            "what do reviews on booking.com say about the Peninsula",
            "prices on expedia.com",
            "what do people say about hotels.com",
        ):
            with self.subTest(request=request):
                self.assertFalse(_is_live_state(request))

    def test_a_real_availability_question_is_still_live_state(self):
        for request in (
            "is the Peninsula available for the 18th",
            "are there rooms available tonight",
            "room rates for tomorrow",
            "book a room for Friday",
        ):
            with self.subTest(request=request):
                self.assertTrue(_is_live_state(request))


class NamesASurfaceTests(unittest.TestCase):
    """Mentioning a site is not asking for one to be driven."""

    def test_a_bare_mention_does_not_name_a_surface(self):
        for request in (
            "what do reviews on booking.com say about the Peninsula",
            "prices on expedia.com",
            "I was reading something on booking.com last night",
        ):
            with self.subTest(request=request):
                self.assertFalse(goal_intent.names_a_surface(request))

    def test_a_verb_with_the_domain_still_names_one(self):
        for request in (
            "open booking.com and check the price",
            "go to wikipedia.org",
            "visit github.com",
            "click the sign in button on this page",
            "scroll down on this page",
        ):
            with self.subTest(request=request):
                self.assertTrue(goal_intent.names_a_surface(request))


def _factors(**kwargs) -> caps.Factors:
    return caps.Factors(**kwargs)


class InteractionDominanceTests(unittest.TestCase):
    """A lookup cannot operate a page -- but only when one must be operated."""

    def test_naming_a_page_to_operate_beats_a_cheaper_lookup(self):
        # Raising the browser's fit was not enough on its own: a search still
        # outscored it on cost, so "open booking.com and check the price"
        # came back as a search.
        factors = _factors(
            verification_required=True,
            freshness_required=True,
            interaction_required=True,
        )

        browser = caps._score(caps.BROWSER_CONTROL, factors, 0)
        search = caps._score(caps.WEB_SEARCH, factors, 0)

        self.assertGreater(browser, search)

    def test_a_surface_named_inside_a_plain_question_does_not_win(self):
        # "How do I open Spotify myself?" names a surface and needs nothing
        # opened -- it is a question *about* an action. Holding the direct
        # answer down for it sent the turn to the browser.
        factors = _factors(interaction_required=True)

        direct = caps._score(caps.DIRECT_ANSWER, factors, 0)
        browser = caps._score(caps.BROWSER_CONTROL, factors, 0)

        self.assertGreater(direct, browser)

    def test_research_still_prefers_a_search(self):
        factors = _factors(freshness_required=True, verification_required=True)

        search = caps._score(caps.WEB_SEARCH, factors, 0)
        browser = caps._score(caps.BROWSER_CONTROL, factors, 0)

        self.assertGreater(search, browser)

    def test_live_state_still_prefers_the_browser(self):
        factors = _factors(
            freshness_required=True,
            verification_required=True,
            live_state_required=True,
        )

        browser = caps._score(caps.BROWSER_CONTROL, factors, 0)
        search = caps._score(caps.WEB_SEARCH, factors, 0)

        self.assertGreater(browser, search)


class SurfaceMapTests(unittest.TestCase):
    """The operation names the surface; the intent names only the family."""

    def test_every_executable_operation_maps_to_a_surface(self):
        from tools.computer_control.computer_control import COMPUTER_OPERATIONS

        executable = COMPUTER_OPERATIONS - {"none", "unsupported"}

        self.assertEqual(executable - set(caps._SURFACE_BY_OPERATION), set())

    def test_page_operations_go_to_the_browser(self):
        for operation in ("browser_action", "open_url", "open_search"):
            with self.subTest(operation=operation):
                self.assertEqual(
                    caps._SURFACE_BY_OPERATION[operation],
                    caps.BROWSER_CONTROL,
                )

    def test_application_operations_go_to_the_desktop(self):
        for operation in (
            "ui_action", "open_app", "close_app", "force_quit_app",
            "list_windows", "describe_window",
            "create_file", "create_folder", "delete_file", "delete_folder",
        ):
            with self.subTest(operation=operation):
                self.assertEqual(
                    caps._SURFACE_BY_OPERATION[operation], caps.UI_CONTROL,
                )

    def test_no_browser_intent_survives_in_the_label_map(self):
        # The router has never emitted these as intents; carrying them here
        # made three branches unreachable and hid the real bug.
        for dead in ("browser_action", "browser_tab", "browser_search"):
            with self.subTest(label=dead):
                self.assertNotIn(dead, caps._MACHINE_CAPABILITY)


if __name__ == "__main__":
    unittest.main()
