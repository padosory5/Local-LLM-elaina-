"""A pointer is not a target, and a surface is not a destination.

Three issues from the first dogfooding session, one cause. What the user
said, and what was opened:

    "search it up on Zelo"                  -> searched for "it up on Zelo"
                                               "Got it, it up on Zelo is open."
    "show it off on my browser"             -> open_search target=browser
                                               "Sure, new tab opened."
    "Can you show me that on my browser?"   -> target="me that on my browser"

Every one of them reported success. The first two opened a tab, and the
third failed looking for a live element called "me that on my browser".

Two rules, and neither is about these sentences:

* a request whose target has no content word in it names nothing -- "my
  browser" and "me that on my browser" are grammar and a surface, and
  opening a blank tab for them is worse than saying so;
* "search it up" is a phrasal verb with a pronoun object. The thing to
  search for is whatever "it" refers to, which is in the conversation --
  never the letters i and t.
"""

import unittest

from brain.intent_router import _SPOKEN_SEARCH
from tools.browser_control.safe_browser import SafeBrowserControl


class TargetNamesNothingTests(unittest.TestCase):

    def _browser(self):
        return SafeBrowserControl(opener=lambda url: None)

    def test_a_surface_is_not_something_to_search_for(self):
        for target in (
            "browser",
            "my browser",
            "the browser",
            "a new tab",
            "this page",
            "me that on my browser",
            "it",
            "that",
            "them on my screen",
        ):
            with self.subTest(target=target):
                resolution = self._browser().resolve_search(target)
                self.assertEqual(
                    resolution.status, "invalid_target",
                    f"{target!r} was opened as a search",
                )

    def test_a_real_query_still_resolves(self):
        for target in (
            "wireless keyboards",
            "best hotels in Guam",
            "packing peanuts",
            "University of Washington international student office",
            "Zillow",
            "studio apartments in Seattle",
        ):
            with self.subTest(target=target):
                resolution = self._browser().resolve_search(target)
                self.assertEqual(resolution.status, "resolved")
                self.assertIn("google.com/search", resolution.url)

    def test_an_empty_target_is_unchanged(self):
        self.assertEqual(
            self._browser().resolve_search("   ").status, "invalid_target",
        )


class PhrasalSearchTests(unittest.TestCase):
    """"search it up" points at the conversation, not at a query."""

    def test_a_pronoun_object_is_not_extracted_as_the_query(self):
        for said in (
            "Yeah, can you use my browser control and then search it up on Zelo?",
            "can you look it up for me",
            "search that up",
            "find them on Google",
        ):
            with self.subTest(said=said):
                match = _SPOKEN_SEARCH.search(said)
                query = match.group("query").strip(" .!?") if match else ""
                self.assertFalse(
                    query and query.split()[0].casefold() in {
                        "it", "that", "this", "them", "those", "these",
                    },
                    f"a pointer was extracted as a query: {query!r}",
                )

    def test_ordinary_spoken_searches_are_unchanged(self):
        for said, query in (
            ("Search for wireless keyboards in a new browser tab.",
             "wireless keyboards"),
            ("Open the browser and search for best hotels in Guam",
             "best hotels in Guam"),
            ("Open my browser and look up BTS tour dates.",
             "BTS tour dates"),
            ("Search for wireless keyboards in my browser",
             "wireless keyboards"),
        ):
            with self.subTest(said=said):
                match = _SPOKEN_SEARCH.search(said)
                self.assertIsNotNone(match)
                self.assertEqual(
                    match.group("query").strip(" .!?"), query,
                )


if __name__ == "__main__":
    unittest.main()
