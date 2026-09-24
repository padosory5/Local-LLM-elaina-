"""Noticing that the person moved, and asking rather than deciding.

The machine knew before anyone looked: the clock was Pacific while Windows
still said 한국, so she went on pricing things in won. The only visible sign
was four clock tests failing, which is not a sign anybody reads.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from brain import where_we_are

SEOUL = where_we_are.Place(
    country="South Korea", currency="KRW",
    timezone="대한민국 표준시", offset_hours=9.0,
)
SEATTLE = where_we_are.Place(
    country="South Korea", currency="KRW",
    timezone="태평양 일광 절약 시간", offset_hours=-7.0,
)


class RememberingWhereWeWereTests(unittest.TestCase):

    def test_a_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "where_we_are.json"
            where_we_are.write(path, SEOUL)

            self.assertEqual(where_we_are.read(path), SEOUL)

    def test_nothing_remembered_yet(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(where_we_are.read(Path(folder) / "absent.json"))

    def test_a_damaged_file_is_not_a_place(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "where_we_are.json"
            path.write_text("not json", encoding="utf-8")

            self.assertIsNone(where_we_are.read(path))


class WhetherToSayAnythingTests(unittest.TestCase):

    def test_a_move(self):
        self.assertTrue(where_we_are.moved(SEOUL, SEATTLE))

    def test_the_first_start_up_is_not_a_move(self):
        self.assertFalse(where_we_are.moved(None, SEATTLE))

    def test_the_same_place_is_not(self):
        self.assertFalse(where_we_are.moved(SEOUL, SEOUL))

    def test_a_renamed_zone_at_the_same_offset_is_not(self):
        # Daylight saving renames a zone without moving anybody.
        summer = where_we_are.Place(
            country="South Korea", currency="KRW",
            timezone="대한민국 서머타임", offset_hours=9.0,
        )

        self.assertFalse(where_we_are.moved(SEOUL, summer))


class WhatSheSaysTests(unittest.TestCase):

    def test_it_asks_and_says_what_is_still_set(self):
        said = where_we_are.sentence(SEOUL, SEATTLE, "en")

        self.assertIn("UTC+09:00", said)
        self.assertIn("UTC-07:00", said)
        self.assertIn("16 hours", said)
        self.assertIn("Did you move?", said)
        # The consequence, not the setting's name.
        self.assertIn("South Korea", said)
        self.assertIn("KRW", said)
        self.assertNotIn("user.country", said)

    def test_the_machines_own_zone_names_are_never_said(self):
        # Windows reports them in the system language; they are for telling
        # two zones apart, not for saying out loud.
        for language in ("en", "ko"):
            said = where_we_are.sentence(SEOUL, SEATTLE, language)
            with self.subTest(language=language):
                self.assertNotIn("태평양", said)
                self.assertNotIn("표준시", said)

    def test_in_korean(self):
        said = where_we_are.sentence(SEOUL, SEATTLE, "ko")

        self.assertTrue(any("가" <= ch <= "힣" for ch in said))
        self.assertIn("16시간", said)
        self.assertIn("UTC-07:00", said)

    def test_the_hours_between(self):
        self.assertEqual(where_we_are.hours_between(SEOUL, SEATTLE), -16)
        self.assertEqual(where_we_are.hours_between(SEATTLE, SEOUL), 16)


if __name__ == "__main__":
    unittest.main()
