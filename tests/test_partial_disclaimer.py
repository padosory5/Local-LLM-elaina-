"""When only the number goes, say only the number went.

From a replay of a real Korean session, after "데이터로 알려줘":

    2026년 한국에서 가장 많이 시청된 드라마는 '죄와 사랑'입니다.
    찾아봤지만 확인되지 않아서, 추측으로 말씀드리지는 않겠습니다.

The value guard had dropped the sentence carrying an unverified number and
kept the rest -- correctly -- then appended the line written for when the
*whole* answer goes: "I looked and couldn't confirm it, so I won't guess."
After a surviving claim that reads as the claim being withdrawn, which it
was not. The partial line says what actually happened.
"""

from __future__ import annotations

import unittest

from brain.grounded_values import GroundedValueGuard

WHOLE = "찾아봤지만 확인되지 않아서, 추측으로 말씀드리지는 않겠습니다."
PARTIAL = "찾아봤지만 구체적인 숫자는 확인되지 않아서 빼고 말씀드렸습니다."


class ThePartialLineTests(unittest.TestCase):

    def test_a_surviving_answer_gets_the_partial_line(self):
        reply = (
            "그 드라마는 2012년에 방영되었습니다. "
            "제작비는 30,000,000원이었습니다."
        )

        corrected = GroundedValueGuard.correct_values(
            reply, evidence="", offer=WHOLE, partial_offer=PARTIAL,
        )

        self.assertIn("2012년에 방영", corrected)
        self.assertNotIn("30,000,000원", corrected)
        self.assertTrue(corrected.endswith(PARTIAL))
        self.assertNotIn(WHOLE, corrected)

    def test_nothing_surviving_still_gets_the_whole_line(self):
        corrected = GroundedValueGuard.correct_values(
            "제작비는 30,000,000원이었습니다.",
            evidence="", offer=WHOLE, partial_offer=PARTIAL,
        )

        self.assertEqual(corrected, WHOLE)

    def test_without_a_partial_line_the_old_behaviour_stands(self):
        # The browser offer is parked and classified against its own words,
        # so that caller passes no partial line and must keep its wording.
        corrected = GroundedValueGuard.correct_values(
            "그 드라마는 2012년에 방영되었습니다. 제작비는 30,000,000원이었습니다.",
            evidence="", offer=WHOLE,
        )

        self.assertTrue(corrected.endswith(WHOLE))


if __name__ == "__main__":
    unittest.main()
