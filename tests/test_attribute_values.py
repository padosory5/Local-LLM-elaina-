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


class OptionsAndOtherUnitsAreNotADisagreementTests(unittest.TestCase):
    """What "Sources disagree on that one" was being said about.

    Replayed over every recorded turn that searched
    (evals/disagreement_replay.py, 2026-10-07): 33 replies given the line,
    none to a real disagreement, and in 28 the value it named was in no
    source -- a page's 2TB said back as "another says 2GB".
    """

    # As retrieved for "How much does the iPhone 18 Pro cost?", shortened.
    ONE_PAGE_LISTS_THE_SIZES = (
        "[1] iPhone 18 Pro price\nSource: thepricer.org\nSnippet: The iPhone 18 "
        "Pro costs $1,199 for 256GB, $1,399 for 512GB, $1,599 for 1TB and "
        "$1,999 for 2TB.\n\n"
        "[2] Buy iPhone 18 Pro\nSource: apple.com\nSnippet: From $1,199 with "
        "256GB of storage."
    )
    TWO_PAGES_TWO_UNITS = (
        "[1] iPhone 18 Pro\nSource: macobserver.com\nSnippet: Starts at $1,199 "
        "for 256GB.\n\n"
        "[2] iPhone 18 Pro specs\nSource: example.com\nSnippet: For the first "
        "time, a 2TB option."
    )

    def _others(self, reply, evidence):
        return [other for _, other in attribute_values.conflicting_claims(reply, evidence)]

    def test_the_sizes_one_page_lists_are_options(self):
        self.assertEqual(
            self._others("It starts at $1,199 for 256GB.", self.ONE_PAGE_LISTS_THE_SIZES), [],
        )

    def test_another_unit_is_another_value_not_another_account(self):
        # Recorded as "another says 2GB": the 2 of 2TB, written in her unit.
        self.assertEqual(
            self._others("It starts at $1,199 for 256GB.", self.TWO_PAGES_TWO_UNITS), [],
        )
        # And the other way about, recorded as "another says 256TB".
        self.assertEqual(
            self._others("It includes a 2TB storage option.", self.TWO_PAGES_TWO_UNITS), [],
        )

    def test_memory_is_not_a_second_opinion_on_storage(self):
        # Recorded as "another says 12TB".
        evidence = (
            "[1] Specs\nSource: a.example\nSnippet: 12GB of memory; some models "
            "16GB.\n\n[2] Storage\nSource: b.example\nSnippet: Up to 2TB of storage."
        )
        self.assertEqual(self._others("It includes a 2TB storage option.", evidence), [])

    def test_a_value_one_source_gives_beside_hers_is_an_option(self):
        evidence = (
            "[1] LG\nSource: a.example\nSnippet: 165Hz native, or 180Hz "
            "overclocked.\n\n[2] LG\nSource: b.example\nSnippet: A 165Hz panel."
        )
        self.assertEqual(self._others("It runs at 165Hz.", evidence), [])

    def test_two_sources_one_unit_two_values_is_still_a_disagreement(self):
        for reply, evidence, other in (
            ("It runs at 165Hz.", SourcesDisagreeingIsAStateNotARaceTests.EVIDENCE, "180Hz"),
            ("It is rated 4.8 stars.",
             "[1] Store A\nSource: a.example\nSnippet: Rated 4.8 stars by buyers.\n\n"
             "[2] Store B\nSource: b.example\nSnippet: Rated 4.6 stars overall.",
             "rated 4.6 stars"),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(self._others(reply, evidence), [other])

    def test_one_unit_spelt_two_ways_is_one_unit(self):
        # "Same unit" must not mean "same spelling", or these would be lost.
        for reply, evidence, other in (
            ("It has a 27 inch screen.",
             "[1] Monitor\nSource: a.example\nSnippet: A 27-inch panel.\n\n"
             "[2] Monitor\nSource: b.example\nSnippet: The screen measures 32 inches.",
             "32 inch"),
            ("It has a 48MP camera.",
             "[1] A\nSource: a.example\nSnippet: A 48 megapixel main camera.\n\n"
             "[2] B\nSource: b.example\nSnippet: The main camera is 50MP.",
             "50MP"),
        ):
            with self.subTest(reply=reply):
                self.assertEqual(self._others(reply, evidence), [other])

    def test_every_spelling_that_is_called_the_same_unit_is_a_unit_of_one_kind(self):
        kinds = {}
        for table in (attribute_values._SPEC_UNITS, attribute_values._AMBIENT_UNITS):
            for kind, units in table.items():
                for unit in units:
                    kinds[unit] = kind
        for spelling, unit in attribute_values._SAME_UNIT.items():
            with self.subTest(spelling=spelling):
                self.assertIn(spelling, kinds)
                self.assertIn(unit, kinds)
                self.assertEqual(kinds[spelling], kinds[unit])


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
