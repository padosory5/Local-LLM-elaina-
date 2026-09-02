"""An anchor is context for the subject it was set with.

B-28 and B-42, from the second dogfooding session. A correction early in
the conversation set the conversational anchor:

    Background:
      about: look at Zillow for rental options near University of Washington

and it was still there an hour later, riding into every query built after
it:

    [Tool] Searching web for: Where can I get an international driving
           permit? look at Zillow for rental options near University of
           Washington Seattle

    [Query] text: AI like software companies look at Zillow for rental
            options near University of Washington Seattle

    [Tool] Searching web for: Where to sell second-handed stuff online in
           Korea Selling Second-Hand Items look at Zillow for rental
           options near University of Washington

The anchor exists for a real reason -- "rent near my school" three turns
later still means near UW -- so it deliberately outlives the turn that set
it. What it had no way to do was stop: nothing retired it when the
conversation moved on, and ``query_context()`` appends it to everything.

It survives while turns keep pointing back at it, and goes when one
establishes a new subject and points at nothing.
"""

import time
import unittest

from brain import conversation_focus as focus_module


def run(*turns):
    """Walk a conversation and hand back the focus at the end.

    Turns are ``(said, subject)`` the way the engine supplies them -- the
    goal layer names a subject every turn, and the live log shows the
    subject moving on ("Internship Preparation") while the anchor stayed.
    """
    focus = focus_module.start(now=time.monotonic())
    for turn in turns:
        said, subject = turn if isinstance(turn, tuple) else (turn, "")
        focus = focus_module.update(
            focus, said, subject=subject, now=time.monotonic(),
        )
    return focus


class AnAnchorStopsTests(unittest.TestCase):

    def test_the_live_leak_does_not_survive_the_topic_change(self):
        focus = run(
            ("I'm moving to Seattle for university", "moving to Seattle"),
            ("I mean look at Zillow for rental options near University of Washington",
             "rental options"),
            ("Also, I'm trying to get some internships in 2027 summer",
             "Internship Preparation"),
            ("Where can I get an international driving permit?",
             "International Driving Permit"),
        )

        self.assertNotIn(
            "zillow",
            " ".join(focus.query_context()).casefold(),
            "the rental anchor rode into an unrelated turn",
        )

    def test_an_anchor_survives_a_turn_that_points_back_at_it(self):
        # The case it exists for, and which must not be lost.
        focus = run(
            ("I'm going to UW", "UW"),
            ("I mean University of Washington", "University of Washington"),
            ("what's the rent near my school", "rent"),
        )

        self.assertIn(
            "washington", " ".join(focus.query_context()).casefold(),
        )

    def test_an_anchor_survives_a_deictic_follow_up(self):
        focus = run(
            ("I mean University of Washington", "University of Washington"),
            ("how expensive is it there", "cost of living"),
        )

        self.assertIn(
            "washington", " ".join(focus.query_context()).casefold(),
        )

    def test_an_anchor_is_a_phrase_not_a_request(self):
        # "look at Zillow for rental options near University of Washington"
        # is a task description. An anchor is the thing being talked about.
        focus = run(
            "I mean look at Zillow for rental options near University of "
            "Washington in Seattle please",
        )

        anchor = focus.background.get("about", "")
        self.assertLessEqual(len(anchor.split()), 8, anchor)


class QueriesBuiltFromItTests(unittest.TestCase):
    """The consequence: what actually reaches the search box."""

    def test_an_unrelated_query_carries_none_of_it(self):
        focus = run(
            ("I mean look at Zillow for rental options near University of Washington",
             "rental options"),
            ("Also, I'm trying to get some internships in 2027 summer",
             "Internship Preparation"),
        )
        context = " ".join(focus.query_context()).casefold()

        for word in ("zillow", "rental"):
            self.assertNotIn(word, context, context)


if __name__ == "__main__":
    unittest.main()
