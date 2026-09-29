"""The stage registry says what every recorded stage is, and nothing else.

brain/response_stages.py holds the judgments about each stage of the reply
pipeline -- hard or soft, why it exists, what it must follow. A stage that is
recorded but not in the registry is a stage nobody has classified; an entry
whose stage is gone is a classification of nothing. Either way the inventory
in docs/RESPONSE_PIPELINE.md would be wrong, so both fail here.
"""

from __future__ import annotations

import ast
import importlib
import unittest
from pathlib import Path

from brain import response_stages as registry


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def recorded_stage_names() -> set[str]:
    source = (PROJECT_ROOT / "brain" / "chat_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "step"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "turn_trace"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.add(node.args[0].value)
    return names


def resolve(dotted: str):
    """Import a dotted name that may end in a class attribute."""
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            target = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for attribute in parts[split:]:
            target = getattr(target, attribute)
        return target
    raise ImportError(dotted)


class RegistryTests(unittest.TestCase):

    def test_every_recorded_stage_is_classified_and_nothing_else_is(self):
        recorded = recorded_stage_names()
        classified = set(registry.BY_NAME)
        self.assertEqual(
            sorted(recorded - classified), [],
            "recorded in _answer_turn but missing from brain/response_stages.py",
        )
        self.assertEqual(
            sorted(classified - recorded), [],
            "in brain/response_stages.py but no longer recorded anywhere",
        )

    def test_names_are_unique(self):
        names = [stage.name for stage in registry.STAGES]
        self.assertEqual(len(names), len(set(names)))

    def test_every_entry_uses_the_stated_vocabulary(self):
        for stage in registry.STAGES:
            with self.subTest(stage=stage.name):
                self.assertIn(stage.kind, registry.CLASSES)
                self.assertIn(stage.exists_because, registry.REASONS)
                self.assertIn(stage.action, registry.ACTIONS)
                self.assertTrue(stage.purpose.strip())

    def test_a_hard_rule_says_what_it_holds(self):
        for stage in registry.STAGES:
            if stage.kind in (registry.HARD, registry.MIXED):
                with self.subTest(stage=stage.name):
                    self.assertTrue(stage.protects.strip())

    def test_a_mixed_stage_says_which_part_is_which(self):
        for stage in registry.STAGES:
            if stage.kind == registry.MIXED:
                with self.subTest(stage=stage.name):
                    self.assertTrue(stage.split.strip())

    def test_a_stage_that_regenerates_declares_a_model_call(self):
        for stage in registry.STAGES:
            if stage.action == registry.REGENERATES:
                with self.subTest(stage=stage.name):
                    self.assertTrue(stage.model_call)

    def test_what_a_stage_runs_after_is_a_stage(self):
        for stage in registry.STAGES:
            for earlier in stage.runs_after:
                with self.subTest(stage=stage.name, earlier=earlier):
                    self.assertIn(earlier, registry.BY_NAME)

    def test_the_code_it_names_exists(self):
        for stage in registry.STAGES:
            for dotted in stage.implemented_by:
                with self.subTest(stage=stage.name, code=dotted):
                    resolve(dotted)


class SoftSwitchTests(unittest.TestCase):
    """ELAINA_SOFT_STAGES=off: the pipeline without its soft stages.

    The "soft guards off" arm of the model experiment, and the way a soft
    stage is retired behind a flag before it is deleted. Hard and mixed
    stages never switch off.
    """

    def test_everything_runs_unless_asked_otherwise(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"ELAINA_SOFT_STAGES": ""}):
            self.assertTrue(all(registry.active(stage.name) for stage in registry.STAGES))

    def test_off_stops_the_soft_stages_and_nothing_else(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"ELAINA_SOFT_STAGES": "off"}):
            for stage in registry.STAGES:
                with self.subTest(stage=stage.name):
                    self.assertEqual(registry.active(stage.name), stage.kind != registry.SOFT)

    def test_every_soft_stage_is_behind_the_switch(self):
        source = (PROJECT_ROOT / "brain" / "chat_engine.py").read_text(encoding="utf-8")
        body = source[source.index("    def _answer_turn("):source.index("\n    def _dispatch_turn(")]
        for stage in registry.STAGES:
            if stage.kind == registry.SOFT:
                with self.subTest(stage=stage.name):
                    self.assertIn(f'response_stages.active("{stage.name}")', body)

    def test_a_turn_with_the_soft_stages_off_runs_none_of_them(self):
        import os
        from unittest.mock import patch

        from core import turn_trace
        from tests.turn_harness import build_engine

        soft = {stage.name for stage in registry.STAGES if stage.kind == registry.SOFT}
        with patch.dict(os.environ, {"ELAINA_SOFT_STAGES": "off"}):
            engine = build_engine()
            engine.client.reply = (
                "Paris is the capital of France. Anything else I can help with?"
            )
            engine.chat("what is the capital of france?")
            trace = turn_trace.last()
        self.assertTrue(trace.chain_ok)
        self.assertEqual(soft & set(trace.runs), set())
        self.assertIn("grounded_values", trace.runs)


if __name__ == "__main__":
    unittest.main()
