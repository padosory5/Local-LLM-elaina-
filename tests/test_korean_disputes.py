"""Which Korean turns say her last answer is wrong -- and which only ask.

From a replay of a real Korean session. "시애틀 옆에 어떤 섬이 있는데 이름을
까먹었다. 무슨 베인브리지 섬이 였던것 같은데 맞아?" asks her to confirm the
person's own guess. The detector had a bare "맞아?" in it, read the turn as a
dispute of *her* claim, and her next answer opened with an apology for an
earlier statement she had never made.

The other direction was missing entirely. "데이터로 알려줘" -- back it up
with data -- was read as a question about data in general, and she offered
to explain data. Asking for the evidence behind an answer is the same
signal, pointed the same way, as "are you sure?", and it now escalates the
same way: go and check the claim.
"""

from __future__ import annotations

import unittest

from brain.grounded_values import reads_as_dispute


class KoreanDisputesTests(unittest.TestCase):

    def test_a_challenge_to_what_she_said_is_a_dispute(self):
        for said in (
            "그거 맞아?", "이게 맞는 거야?", "진짜 맞아?", "확실해?",
            "그거 틀렸어", "아닌 것 같은데",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_dispute(said))

    def test_asking_for_the_evidence_is_a_dispute(self):
        for said in (
            "데이터로 알려줘", "근거 있어?", "출처가 뭐야",
            "source?", "prove it", "how do you know that",
            "back that up with data",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_dispute(said))

    def test_the_same_challenge_in_either_language(self):
        # Measured on the paired arcs: "확실해?" was a dispute and its
        # translation, "you sure about that?", was not.
        for said in (
            "확실해?", "확실한 거야?", "그거 확실한 거지?",
            "are you sure?", "you sure?", "you sure about that?", "u sure",
        ):
            with self.subTest(said=said):
                self.assertTrue(reads_as_dispute(said))
        for said in (
            "you sure know a lot about headphones",
            "확실한 방법 알려줘",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_dispute(said))

    def test_confirming_their_own_guess_is_not(self):
        # The measured false positive: 맞아? aimed at the person's own words.
        self.assertFalse(reads_as_dispute(
            "시애틀 옆에 어떤 섬이 있는데 이름을 까먹었다. "
            "무슨 베인브리지 섬이 였던것 같은데 맞아?"
        ))

    def test_ordinary_turns_are_not(self):
        for said in (
            "그 섬에 카지노가 있는지 알려줘",
            "김치찌개를 만드는법을 알려줘",
            "근거리 통신이 뭐야",           # 근거리 is "short range"
            "맞아 맞아",
            "데이터 과학 공부하고 싶어",
            "where is the source code",
            "Okay, that's not that much. Thank you, though.",
        ):
            with self.subTest(said=said):
                self.assertFalse(reads_as_dispute(said))


if __name__ == "__main__":
    unittest.main()
