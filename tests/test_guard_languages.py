"""Every text guard says which languages it works in.

Rule 4 of the milestone plan, made enforceable rather than aspirational.

A guard that silently passes everything in Korean is worse than no guard:
a quiet detector and a clean conversation are indistinguishable from the
outside. This session lost an hour to that twice -- once to two regexes
that arrived carrying literal backspaces where their word boundaries should
have been, once to a register rule with two Cyrillic letters in its
character class. Both imported fine and both matched nothing.

The declaration does not make a guard bilingual. It makes the gap *visible*,
so a report can say what is not covered instead of implying everything is,
and so the next guard cannot skip the question by accident.
"""

import ast
import unittest
from pathlib import Path

BRAIN = Path(__file__).resolve().parents[1] / "brain"

# Every module that inspects or rewrites text the user will hear. Adding a
# guard means adding it here and declaring its languages; the last test in
# this file is what makes that unavoidable.
TEXT_GUARDS = (
    "action_commitment.py",
    "attribute_values.py",
    "capability_contract.py",
    "conversation_style.py",
    "grounded_values.py",
    "guard_lines.py",
    "korean_register.py",
    "response_policy.py",
    "response_quality.py",
    "task_progress.py",
    "text_filter.py",
    "turn_language.py",
)

KNOWN = {"en", "ko"}


def declared_languages(name: str) -> tuple[str, ...] | None:
    """The LANGUAGES tuple a module declares, read without importing it."""
    tree = ast.parse((BRAIN / name).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "LANGUAGES":
                return tuple(
                    element.value for element in node.value.elts
                    if isinstance(element, ast.Constant)
                )
    return None


class EveryGuardDeclaresItsLanguagesTests(unittest.TestCase):

    def test_each_text_guard_declares(self):
        for name in TEXT_GUARDS:
            with self.subTest(module=name):
                self.assertIsNotNone(
                    declared_languages(name),
                    f"brain/{name} must declare LANGUAGES = (...) saying "
                    f"which languages its rules actually work in",
                )

    def test_the_declarations_name_known_languages(self):
        for name in TEXT_GUARDS:
            with self.subTest(module=name):
                languages = declared_languages(name) or ()
                self.assertTrue(languages, name)
                self.assertTrue(set(languages) <= KNOWN, languages)

    def test_a_new_guard_class_cannot_skip_the_question(self):
        """Any module defining a ``...Guard`` class is a text guard.

        The enforcement half. A guard added to a module nobody listed
        would otherwise inherit the old behaviour -- English rules, applied
        to Korean, reporting clean.
        """
        missing = []
        for path in sorted(BRAIN.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            has_guard = any(
                isinstance(node, ast.ClassDef) and node.name.endswith("Guard")
                for node in ast.walk(tree)
            )
            if has_guard and path.name not in TEXT_GUARDS:
                missing.append(path.name)

        self.assertEqual(
            missing, [],
            "these modules define a Guard class but are not listed in "
            "TEXT_GUARDS, so nothing checks what languages they work in",
        )


class WhatIsAndIsNotCoveredTests(unittest.TestCase):
    """The state of the audit, written down rather than assumed.

    This test exists to be *read*. When one of these becomes bilingual,
    change it here and the report stops claiming a gap that closed.
    """

    def test_the_bilingual_ones(self):
        for name in ("conversation_style.py", "guard_lines.py",
                     "text_filter.py", "turn_language.py",
                     "grounded_values.py", "action_commitment.py"):
            with self.subTest(module=name):
                self.assertIn("ko", declared_languages(name) or ())

    def test_the_ones_that_are_still_english_only(self):
        # Not a pass mark -- a list of what A2 did not finish. Each of
        # these applies English patterns and reports clean on Korean.
        for name in ("response_policy.py", "response_quality.py"):
            with self.subTest(module=name):
                self.assertEqual(declared_languages(name), ("en",))


if __name__ == "__main__":
    unittest.main()
