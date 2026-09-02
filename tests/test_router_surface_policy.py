"""Deterministic corrections for the ui_action / browser_action boundary.

Every case here was a live routing failure first. They are unit tests rather
than live checks because the corrections are pure functions of the request
text and the known surface -- no model needed to prove them, and a live-only
check cannot fail the build when someone edits the patterns.

The boundary is genuinely hard from wording alone ("click X on this page" vs
"click X in this app"), which is why the router corrects it in code instead
of relying on the prompt: measured against the matrix, adding prompt text for
this cost two unrelated cases while changing nothing here.
"""

import unittest

from brain.intent_router import (
    IntentDecision,
    SemanticIntentRouter,
    _DEICTIC_PAGE_REFERENCE,
    _DEICTIC_SURFACE_REFERENCE,
    _FOCUS_WINDOW_COMMAND,
    _SPOKEN_SEARCH,
)


def route(
    text: str,
    operation: str,
    *,
    surface: str = "",
    enabled: bool = True,
    speech_act: str = "action_request",
    target: str | None = None,
) -> IntentDecision:
    """Run one raw model decision through the computer-control policy."""
    decision = IntentDecision(
        intent="computer_action",
        confidence=0.9,
        normalized_request=text,
        reason="test",
        action_requested=True,
        action_target=text if target is None else target,
        computer_operation=operation,
        speech_act=speech_act,
    )
    return SemanticIntentRouter._apply_computer_control_policy(
        decision,
        original_input=text,
        computer_control_enabled=enabled,
        active_desktop_surface={"kind": surface} if surface else None,
    )


class PageReferenceTests(unittest.TestCase):
    """"this page" had to be adjacent, so "this hotel page" never matched."""

    def test_a_modifier_between_this_and_page_still_reads_as_a_page(self):
        for text in (
            "Fill the search box on this hotel page with rooms in Seoul.",
            "Click Settings on this GitHub page.",
            "Read me this news article.",
            "Compare the prices in these hotel listings on this page.",
        ):
            with self.subTest(text=text):
                self.assertTrue(_DEICTIC_SURFACE_REFERENCE.search(text))
                self.assertTrue(_DEICTIC_PAGE_REFERENCE.search(text))

    def test_a_native_window_is_not_a_page(self):
        for text in (
            "Click the submit button in the Checkout window",
            "What controls are in the Notepad window",
        ):
            with self.subTest(text=text):
                self.assertIsNone(_DEICTIC_PAGE_REFERENCE.search(text))


class SurfaceCorrectionTests(unittest.TestCase):

    def test_a_named_page_beats_an_unknown_surface(self):
        # Defaulting to ui_action whenever no live surface was known aimed
        # every "...on this page" request at whatever desktop app happened
        # to be in front.
        result = route(
            "Compare the prices in these hotel listings on this page.",
            "ui_action",
        )

        self.assertEqual(result.computer_operation, "browser_action")

    def test_a_page_reference_outranks_an_app_sounding_word(self):
        # "Settings" here is a link on a webpage, not the Windows app.
        result = route("Click Settings on this GitHub page.", "browser_action")

        self.assertEqual(result.computer_operation, "browser_action")

    def test_a_named_app_with_no_page_goes_to_the_native_planner(self):
        # The direction that had no guard at all, so the answer depended on
        # which label the model happened to pick that run.
        result = route(
            "Can you search for Laufey in Spotify?", "browser_action",
        )

        self.assertEqual(result.computer_operation, "ui_action")

    def test_a_live_browser_surface_still_wins_for_a_deictic_request(self):
        result = route("Click the first result here", "ui_action",
                       surface="browser")

        self.assertEqual(result.computer_operation, "browser_action")

    def test_an_unsupported_page_action_is_recovered(self):
        # "fill the search box on this hotel page" came back unsupported.
        result = route(
            "Fill the search box on this hotel page with rooms in Seoul.",
            "unsupported",
        )

        self.assertEqual(result.computer_operation, "browser_action")

    def test_recovery_needs_both_a_page_and_a_page_verb(self):
        # Narrow on purpose: this is the one correction that turns a refusal
        # into an action, so neither half alone may unlock it.
        for text, operation in (
            ("I should probably close some of these tabs.", "unsupported"),
            ("Chrome keeps crashing on me lately.", "unsupported"),
            ("Tell me about this page.", "unsupported"),
        ):
            with self.subTest(text=text):
                result = route(text, operation)
                self.assertNotEqual(
                    result.computer_operation, "browser_action",
                )


class FocusWindowTests(unittest.TestCase):
    """Raising a window is an instruction; listing them is a question."""

    def test_a_focus_command_is_a_ui_action(self):
        result = route("Bring VS Code to the front for me.", "list_windows")

        self.assertEqual(result.computer_operation, "ui_action")

    def test_questions_about_open_windows_stay_list_windows(self):
        for text in (
            "What window is in front right now",
            "what apps are open",
            "show me what is open",
            "what windows do I have open",
        ):
            with self.subTest(text=text):
                self.assertIsNone(_FOCUS_WINDOW_COMMAND.search(text))
                result = route(text, "list_windows",
                               speech_act="information_request")
                self.assertEqual(result.computer_operation, "list_windows")


class ActionTargetContractTests(unittest.TestCase):
    """The prompt states this; the model honoured it unevenly."""

    def test_a_page_or_app_action_keeps_the_whole_request(self):
        # "search for Laufey in Spotify" came back as "search for Laufey" --
        # dropping the qualifier that says where.
        text = "Can you search for Laufey in Spotify?"

        result = route(text, "ui_action", target="search for Laufey")

        self.assertEqual(result.action_target, text)

    def test_other_operations_keep_their_extracted_target(self):
        result = route("Open Discord for me.", "open_app", target="Discord")

        self.assertEqual(result.action_target, "Discord")


class SpokenSearchTests(unittest.TestCase):
    """The destination is never part of what to search for."""

    def test_the_article_a_is_stripped_with_the_surface(self):
        # "(?:my|the)" did not cover "a", so "in a new browser tab" stayed
        # inside the query and was searched for verbatim.
        match = _SPOKEN_SEARCH.search(
            "Search for wireless keyboards in a new browser tab.",
        )

        self.assertEqual(match.group("query").strip(" .!?"), "wireless keyboards")

    def test_existing_phrasings_are_unchanged(self):
        for text, query in (
            ("Open the browser and search for best hotels in Guam",
             "best hotels in Guam"),
            ("Open my browser and look up BTS tour dates.",
             "BTS tour dates"),
            ("Search for wireless keyboards in my browser",
             "wireless keyboards"),
        ):
            with self.subTest(text=text):
                match = _SPOKEN_SEARCH.search(text)
                self.assertEqual(
                    match.group("query").strip(" .!?"), query,
                )


class CompoundFileRequestTests(unittest.TestCase):
    """Creating a file and putting something in it are two requests.

    Only the first is in scope: the Phase 4A command set creates an empty
    file and has no way to write content. The model dropped the second half
    and reported create_file, so "create notes.txt and write hello inside it"
    produced an empty notes.txt -- a wrong outcome reported as success, and
    the one dangerous false positive standing between this phase and zero.

    Measured five consecutive times before the guard, and identically at the
    previous checkpoint, so it was behaviour rather than variance.
    """

    def test_a_compound_create_and_write_is_refused(self):
        for text in (
            "Create notes.txt in Documents and write hello inside it.",
            "Make a file called log.txt and put today in it",
            "Create notes.txt then write hello into it",
        ):
            with self.subTest(text=text):
                result = route(text, "create_file")
                self.assertEqual(result.computer_operation, "unsupported")
                self.assertFalse(result.action_requested)

    def test_a_plain_create_is_still_supported(self):
        for text, operation in (
            ("Create an empty file named notes.txt in Documents.",
             "create_file"),
            ("Create a folder named Trip in Documents.", "create_folder"),
            ("Make a folder called Trip in Documents", "create_folder"),
        ):
            with self.subTest(text=text):
                result = route(text, operation)
                self.assertEqual(result.computer_operation, operation)

    def test_the_guard_does_not_reach_other_operations(self):
        # "and write" in a delete or open request is not this case.
        result = route("Delete notes.txt from Documents", "delete_file")

        self.assertEqual(result.computer_operation, "delete_file")


if __name__ == "__main__":
    unittest.main()
