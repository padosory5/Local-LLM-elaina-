"""A picture on a card, and what must happen when there isn't one.

The Phase 4F.7 acceptance suite for illustration. The rule that matters is
not "cards have images" -- it is that **nothing here may cost a turn**. An
image index is a third-party service reached over the network while a person
is waiting, so every test below is really the same question asked from a
different direction: when the pictures do not arrive, is the reply still
exactly what it would have been?
"""

from __future__ import annotations

import contextlib
import io
import unittest

from brain import response_surface as surfaces
from brain import result_state as rs
from brain import surface_images


HOTELS = (
    rs.Candidate(name="Lotte Hotel World",
                 url="https://lottehotel.com/world",
                 why="great reviews", summary="Jamsil, 5 star"),
    rs.Candidate(name="Hotel Skypark Myeongdong",
                 url="https://skyparkhotel.com/myeongdong",
                 why="good price", summary="Myeongdong, 3 star"),
)


def _surface():
    return surfaces.from_candidates(HOTELS, title="good hotels")


@contextlib.contextmanager
def _lookup(answer):
    """Stand in for the image index, and put the real one back after."""
    surface_images._CACHE.clear()
    original = surface_images._search_one
    surface_images._search_one = answer
    quiet = io.StringIO()
    try:
        with contextlib.redirect_stdout(quiet):
            yield
    finally:
        surface_images._search_one = original
        surface_images._CACHE.clear()


class APictureIsAddedTests(unittest.TestCase):

    def test_every_card_gets_one(self):
        with _lookup(lambda name: f"https://pics.example.com/{len(name)}.jpg"):
            illustrated = surface_images.illustrate(_surface())

        self.assertEqual(len(illustrated.items), 2)
        for item in illustrated.items:
            with self.subTest(item=item.name):
                self.assertTrue(item.image.startswith("https://"))

    def test_nothing_else_about_the_card_changes(self):
        plain = _surface()
        with _lookup(lambda name: "https://pics.example.com/a.jpg"):
            illustrated = surface_images.illustrate(plain)

        self.assertEqual(
            [(i.id, i.name, i.url, i.actions) for i in illustrated.items],
            [(i.id, i.name, i.url, i.actions) for i in plain.items],
        )

    def test_the_picture_reaches_the_wire(self):
        with _lookup(lambda name: "https://pics.example.com/a.jpg"):
            illustrated = surface_images.illustrate(_surface())

        payload = illustrated.payload()

        self.assertEqual(
            [item["image"] for item in payload["items"]],
            ["https://pics.example.com/a.jpg"] * 2,
        )
        # And survives the contract Electron checks it against.
        self.assertTrue(surfaces.validate(payload))


class NoPictureIsNotAFailureTests(unittest.TestCase):
    """The name is the part that matters. Everything here still renders."""

    def test_a_lookup_that_finds_nothing_leaves_the_surface_whole(self):
        with _lookup(lambda name: ""):
            illustrated = surface_images.illustrate(_surface())

        self.assertEqual(illustrated, _surface())

    def test_a_lookup_that_raises_leaves_the_surface_whole(self):
        def explode(name):
            raise RuntimeError("the image index is down")

        with _lookup(explode):
            illustrated = surface_images.illustrate(_surface())

        self.assertEqual(illustrated, _surface())

    def test_one_picture_missing_does_not_lose_the_other(self):
        with _lookup(lambda name: "" if "Lotte" in name else "https://p/x.jpg"):
            illustrated = surface_images.illustrate(_surface())

        self.assertEqual(
            [bool(item.image) for item in illustrated.items], [False, True],
        )

    def test_a_url_that_is_not_a_web_address_is_dropped(self):
        # Whatever an index hands back is untrusted, exactly like every
        # other field in the payload.
        for bad in ("javascript:alert(1)", "file:///etc/passwd", "data:x", ""):
            with self.subTest(url=bad):
                self.assertEqual(surface_images._clean_link(bad), "")

    def test_nothing_to_illustrate_is_returned_untouched(self):
        self.assertEqual(
            surface_images.illustrate(surfaces.NOTHING), surfaces.NOTHING,
        )


class WhatTheIndexHandsBackTests(unittest.TestCase):
    """Driving the real lookup with a stand-in for the network."""

    @contextlib.contextmanager
    def _index(self, results):
        import sys
        import types

        calls = []

        class FakeDDGS:
            def __init__(self, timeout=None):
                pass

            def images(self, name, max_results=2):
                calls.append(name)
                return results

        module = types.ModuleType("ddgs")
        module.DDGS = FakeDDGS
        existing = sys.modules.get("ddgs")
        sys.modules["ddgs"] = module
        surface_images._CACHE.clear()
        try:
            yield calls
        finally:
            if existing is not None:
                sys.modules["ddgs"] = existing
            else:
                sys.modules.pop("ddgs", None)
            surface_images._CACHE.clear()

    def test_the_thumbnail_is_preferred_over_the_original(self):
        # A card is about a hundred pixels wide. The full-size image behind
        # a search result is routinely a megabyte nobody will see at size.
        with self._index([{
            "thumbnail": "https://pics.example.com/small.jpg",
            "image": "https://pics.example.com/enormous.jpg",
        }]):
            self.assertEqual(
                surface_images._search_one("Lotte Hotel World"),
                "https://pics.example.com/small.jpg",
            )

    def test_the_original_is_used_when_there_is_no_thumbnail(self):
        with self._index([{"image": "https://pics.example.com/only.jpg"}]):
            self.assertEqual(
                surface_images._search_one("Lotte Hotel World"),
                "https://pics.example.com/only.jpg",
            )

    def test_a_result_with_no_usable_address_finds_nothing(self):
        with self._index([{"thumbnail": "javascript:alert(1)"}]):
            self.assertEqual(surface_images._search_one("Lotte"), "")

    def test_an_empty_index_finds_nothing(self):
        with self._index([]):
            self.assertEqual(surface_images._search_one("Lotte"), "")

    def test_the_same_name_is_only_searched_once(self):
        # Rebuilding the same shortlist -- which "which one would you
        # choose?" does -- must not re-pay for pictures already in hand.
        with self._index(
            [{"thumbnail": "https://pics.example.com/a.jpg"}],
        ) as calls:
            first = surface_images._search_one("Lotte Hotel World")
            second = surface_images._search_one("lotte hotel world")

            self.assertEqual(first, "https://pics.example.com/a.jpg")
            self.assertEqual(second, first)
            self.assertEqual(len(calls), 1)

    def test_a_name_that_found_nothing_is_not_retried_either(self):
        with self._index([]) as calls:
            surface_images._search_one("Nowhere Hotel")
            surface_images._search_one("Nowhere Hotel")

            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
