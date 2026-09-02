"""What counts as asking her to play something.

Every case here comes from one live turn in the first dogfooding session.
The user asked, in one breath, when to start applying for a 2027
internship. A saved "music -> Spotify" preference from an earlier session
made the media reader willing to treat a bare title as a request, and the
word "start" in "when should I start applying" was enough:

    [Front Door] play_track -> ui_action without the router.
    title: "applying? And then do you know when applications are open"

That is a machine action, on the wrong thing, chosen without the router
ever being consulted. It reached a clarifying question by luck rather than
by design.

Two grammatical facts separate a request from a mention, and neither is a
list of words:

* a request is addressed to her -- "play X", "can you play X". A clause
  whose own subject is the speaker ("when should *I* start applying",
  "*I* want to listen to music later") is a statement about them;
* a title is a phrase, not a paragraph. The subject ends where the
  sentence does, so a following sentence cannot be absorbed into it.
"""

import unittest

from brain.deliberation.front_door import read
from brain.media_target import classify_media_request


def played(text: str, *, preferred: bool = True):
    return classify_media_request(
        text, application="Spotify", preferred_provider=preferred,
    )


class NotAddressedToHerTests(unittest.TestCase):
    """The play verb has its own subject, and it is not her."""

    def test_the_live_internship_turn_is_not_a_play_request(self):
        said = (
            "Okay, thank you. Also, I want to get an internship in summer "
            "2027. When should I start applying? And then do you know when "
            "applications are open?"
        )

        self.assertEqual(played(said).kind, "none")

    def test_the_internship_turn_never_reaches_the_desktop(self):
        # The half that matters: with a standing Spotify preference this
        # went straight past the router to ui_action.
        route = read(said_internship := (
            "Okay, thank you. Also, I want to get an internship in summer "
            "2027. When should I start applying? And then do you know when "
            "applications are open?"
        ), media_application="Spotify")

        self.assertNotEqual(getattr(route, "operation", ""), "ui_action")
        self.assertNotIn("play", getattr(getattr(route, "goal", None), "kind", ""))
        del said_internship

    def test_a_statement_about_oneself_is_not_an_instruction(self):
        for said in (
            "I want to listen to music later",
            "I usually play Attention when I study",
            "we listen to jazz on the way home",
            "they started playing Attention",
            "he put on some jazz earlier",
            "when should I start applying",
        ):
            with self.subTest(said=said):
                self.assertEqual(played(said).kind, "none")

    def test_asking_her_directly_still_plays(self):
        # The negative half. Nothing above may cost an ordinary request.
        for said in (
            "play blinding lights",
            "Play Bang Bang by IVE",
            "can you play Attention",
            "could you put on Blinding Lights",
            "please play Bang Bang",
            "put on Attention for me",
        ):
            with self.subTest(said=said):
                self.assertEqual(played(said).kind, "track")


class TitleEndsWithTheSentenceTests(unittest.TestCase):
    """A title is a phrase; the sentence after it is a separate request."""

    def test_a_following_sentence_is_not_part_of_the_title(self):
        request = played("Play Bang Bang. What is the weather?")

        self.assertEqual(request.kind, "track")
        self.assertEqual(request.target.title, "Bang Bang")

    def test_a_question_mark_ends_the_title(self):
        request = played("Can you play Attention? Thanks.")

        self.assertEqual(request.kind, "track")
        self.assertEqual(request.target.title, "Attention")

    def test_a_title_that_contains_no_boundary_is_untouched(self):
        request = played("Play Bang Bang by IVE in Spotify for me.")

        self.assertEqual(request.target.title, "Bang Bang")
        self.assertEqual(request.target.artist, "IVE")


if __name__ == "__main__":
    unittest.main()
