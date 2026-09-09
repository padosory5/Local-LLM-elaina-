"""A6: a spec is checked the way a price already was.

The class that matters most is
:class:`OrdinarySpeechSurvivesTheGuardTests`. This guard **removes text
from replies**, so its dangerous failure is not "missed a spec" but
"deleted a true sentence", and the negatives are what stop the widened
vocabulary from turning into a disclaimer generator.
"""

import json
import unittest
from pathlib import Path

from brain import attribute_values, guard_lines
from brain.grounded_values import GroundedValueGuard

MATRIX_PATH = Path(__file__).with_name("attribute_matrix.json")

MONITOR = (
    "[1] LG UltraGear 27GP850-B\n"
    "Source: bestbuy.com\n"
    "Snippet: 27-inch QHD (2560x1440) Nano IPS gaming monitor, 165Hz native, "
    "1ms response time. Weighs 6.1 kg. Currently $396.99."
)


class TheMatrixTests(unittest.TestCase):
    """Every case in tests/attribute_matrix.json."""

    @classmethod
    def setUpClass(cls):
        cls.matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    def test_half_the_matrix_is_negatives(self):
        """The proportion is the design, not an accident.

        A guard that strips content needs at least as much evidence that
        it leaves true sentences alone as that it catches invented ones.
        """
        kinds = [case["kind"] for case in self.matrix["cases"]]
        negatives = sum(
            1 for kind in kinds if kind in ("grounded", "not_a_claim")
        )
        self.assertGreaterEqual(negatives, len(kinds) // 2)

    def test_every_case_lands_where_the_matrix_says(self):
        for case in self.matrix["cases"]:
            if case["kind"] == "conflict":
                continue
            with self.subTest(case=case["id"]):
                found = attribute_values.unsupported(
                    case["reply"], case["evidence"],
                )
                def key(text):
                    return "".join(text.split()).casefold().rstrip(".")
                self.assertEqual(
                    {key(claim.text) for claim in found},
                    {key(item) for item in case.get("expect", ())},
                    f"{case['id']}: {case['reply']!r}",
                )

    def test_the_conflict_case_reports_its_conflict(self):
        case = next(
            item for item in self.matrix["cases"] if item["kind"] == "conflict"
        )
        found = attribute_values.disagreements(
            case["evidence"], case["second_source"],
        )
        self.assertEqual(set(found), set(case["expect_conflict"]))


class AUnitIsWhatMakesItAClaimTests(unittest.TestCase):

    def test_a_bare_number_is_never_a_claim(self):
        """Inherited from grounded_values, and the reason it works.

        A year, a count and a duration all look the same as a spec, and
        treating them as values is how a guard starts eating conversation.
        """
        self.assertEqual(attribute_values.claims("I found 3 in 2026."), ())

    def test_a_spec_unit_needs_no_context(self):
        found = attribute_values.claims("It has a 240Hz panel.")
        self.assertEqual([claim.token for claim in found], ["refresh_rate:240"])

    def test_an_ambient_unit_needs_a_measured_thing(self):
        self.assertEqual(
            attribute_values.claims("Let it steep for 14 hours."), (),
        )
        found = attribute_values.claims("Battery life is about 14 hours.")
        self.assertEqual([claim.token for claim in found], ["duration_h:14"])

    def test_spellings_of_the_same_value_compare_equal(self):
        for written in ("27-inch", "27 inches", "27 inch"):
            with self.subTest(written=written):
                self.assertEqual(
                    attribute_values.tokens(f"a {written} screen"),
                    {"length:27"},
                )

    def test_a_thousands_separator_does_not_make_a_new_value(self):
        self.assertEqual(
            attribute_values.tokens("2,560x1440", spoken=False),
            attribute_values.tokens("2560x1440", spoken=False),
        )


class EvidenceIsReadPermissivelyTests(unittest.TestCase):
    """The asymmetry, and the false positive that found it.

    The evidence read "27-inch QHD gaming monitor" and the reply said "a
    27 inch screen". The reply's clause named a measured thing and the
    evidence's did not, so the evidence produced no length claim at all
    and the reply's matched nothing -- the guard flagging a correct
    answer. A rule written to protect replies from over-flagging must not
    be applied to the evidence.
    """

    def test_a_measurement_in_a_document_counts_whatever_surrounds_it(self):
        self.assertEqual(
            attribute_values.tokens("27-inch QHD gaming monitor", spoken=False),
            {"length:27"},
        )
        self.assertEqual(
            attribute_values.tokens("27-inch QHD gaming monitor", spoken=True),
            set(),
        )

    def test_the_correct_answer_is_not_stripped(self):
        self.assertEqual(
            attribute_values.unsupported("It is a 27 inch screen.", MONITOR), (),
        )


class OrdinarySpeechSurvivesTheGuardTests(unittest.TestCase):
    """The half that matters. Every one of these is a true sentence."""

    def test_the_cold_brew_turn(self):
        """A real turn from the everyday dogfood arc."""
        self.assertEqual(
            GroundedValueGuard.unsupported_values(
                "Let it steep for 14 hours in the fridge.", MONITOR,
            ),
            set(),
        )

    def test_a_walk_a_count_and_a_year(self):
        for sentence in (
            "I walked 6 km this morning.",
            "I found three hotels and the building went up in 2026.",
            "Give it about 20 minutes and try again.",
        ):
            with self.subTest(sentence=sentence):
                self.assertEqual(
                    GroundedValueGuard.unsupported_values(sentence, MONITOR),
                    set(),
                )

    def test_a_grounded_spec_survives(self):
        self.assertEqual(
            GroundedValueGuard.unsupported_values(
                "It runs at 165Hz and weighs 6.1 kg.", MONITOR,
            ),
            set(),
        )


class TheWidenedGuardCatchesWhatItMissedTests(unittest.TestCase):
    """Six invented replies against a real listing. It used to catch one."""

    def test_it_catches_the_five_it_used_to_miss(self):
        for name, reply in (
            ("refresh rate", "It has a 240Hz refresh rate."),
            ("response time", "It has a 0.5ms response time."),
            ("rating", "It is rated 4.8 stars."),
            ("battery life", "Battery life is about 14 hours."),
            ("weight", "It weighs 4.2 kg."),
        ):
            with self.subTest(attribute=name):
                self.assertTrue(
                    GroundedValueGuard.unsupported_values(reply, MONITOR),
                    f"{name} went unchecked: {reply!r}",
                )

    def test_it_still_catches_the_one_it_always_did(self):
        """A6 widens the vocabulary and may not narrow it."""
        self.assertTrue(
            GroundedValueGuard.unsupported_values(
                "The LG UltraGear is $249.99.", MONITOR,
            )
        )

    def test_a_changed_value_is_reported_as_contradicted(self):
        found = attribute_values.contradicted("It runs at 144Hz.", MONITOR)
        self.assertEqual([claim.text for claim in found], ["144Hz"])


class SourcesDisagreeingIsAStateNotARaceTests(unittest.TestCase):

    EVIDENCE = (
        "[1] LG UltraGear\nSource: bestbuy.com\nSnippet: 165Hz native, 1ms.\n\n"
        "[2] LG UltraGear\nSource: lg.com\nSnippet: 180Hz overclocked, 1ms."
    )

    def test_the_numbered_blocks_recover_the_sources(self):
        self.assertEqual(len(attribute_values.sources(self.EVIDENCE)), 2)

    def test_it_finds_the_attribute_they_disagree_on(self):
        self.assertEqual(
            attribute_values.conflicts(self.EVIDENCE),
            {"refresh_rate": {"165", "180"}},
        )

    def test_it_says_nothing_about_the_one_they_agree_on(self):
        noted = attribute_values.conflicting_claims(
            "Response time is 1ms.", self.EVIDENCE,
        )
        self.assertEqual(noted, ())

    def test_it_says_nothing_about_an_attribute_she_never_mentioned(self):
        """Otherwise honesty becomes the disclaimer footer A1 removed."""
        self.assertEqual(
            attribute_values.conflicting_claims(
                "It is a solid pick for the money.", self.EVIDENCE,
            ),
            (),
        )

    def test_the_other_value_keeps_its_unit(self):
        noted = attribute_values.conflicting_claims(
            "It runs at 165Hz.", self.EVIDENCE,
        )
        self.assertEqual([other for _, other in noted], ["180Hz"])

    def test_one_source_alone_never_disagrees_with_itself(self):
        self.assertEqual(attribute_values.conflicts(MONITOR), {})

    def test_the_sentence_exists_in_both_languages(self):
        for language in ("en", "ko"):
            with self.subTest(language=language):
                said = guard_lines.say("sources_disagree", language)
                self.assertTrue(said.strip())
                self.assertIn("{other}", said)


class ThePatternsAreWhatTheyLookLikeTests(unittest.TestCase):
    """Two corrupted regex tables have cost this project an hour each.

    Both imported cleanly and matched nothing, which is indistinguishable
    from a codebase with no problems.
    """

    def test_the_source_carries_no_control_characters(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "brain" / "attribute_values.py"
        ).read_text(encoding="utf-8")
        stray = [
            (index, repr(char)) for index, char in enumerate(source)
            if ord(char) < 32 and char not in "\n\t"
        ]
        self.assertEqual(stray, [])

    def test_the_module_declares_its_languages(self):
        self.assertEqual(attribute_values.LANGUAGES, ("en", "ko"))


if __name__ == "__main__":
    unittest.main()


class InternalSourceAttributionTests(unittest.TestCase):
    """Which source carried a value. For the log, not for the reply.

    "According to bestbuy.com" in every sentence is the disclaimer
    register A1 spent effort removing, so this is deliberately internal:
    it makes a disagreement diagnosable rather than merely reported.
    """

    EVIDENCE = (
        "[1] LG UltraGear\nSource: bestbuy.com\nSnippet: 165Hz native, 1ms.\n\n"
        "[2] LG UltraGear\nSource: lg.com\nSnippet: 180Hz overclocked."
    )

    def test_each_value_is_traced_to_the_source_that_carried_it(self):
        for stated, expected in (("165Hz", "bestbuy.com"), ("180Hz", "lg.com")):
            with self.subTest(stated=stated):
                claim = attribute_values.claims(f"It runs at {stated}.")[0]
                self.assertEqual(
                    attribute_values.source_of(claim, self.EVIDENCE), expected,
                )

    def test_a_value_no_source_carried_has_no_attribution(self):
        claim = attribute_values.claims("It runs at 240Hz.")[0]
        self.assertEqual(attribute_values.source_of(claim, self.EVIDENCE), "")
