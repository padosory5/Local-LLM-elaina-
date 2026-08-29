"""Keep the one test entry point honest.

``tests/run_tests.py`` filters the unit tests into categories. A filter that
silently drifts out of date is worse than no filter -- someone runs
``run_tests.py planning``, sees green, and never learns that the planner test
they added an hour ago is not in that category. So the map is checked here:
every module on disk belongs to exactly one category, and every category names
only modules that exist.

``unit`` still runs everything discovered, so a gap here never means a test
went unrun -- only that a category lies about its coverage.
"""

from __future__ import annotations

import unittest

from tests.run_tests import (
    CATEGORIES,
    LIVE_CHECKS,
    LIVE_TIERS,
    PROJECT_ROOT,
    SUITES,
    categorized_modules,
    discovered_modules,
)


class CategoryMapTests(unittest.TestCase):
    def test_every_test_module_has_a_category(self):
        owner = categorized_modules()

        missing = sorted(set(discovered_modules()) - set(owner))

        self.assertEqual(
            missing,
            [],
            "add these to CATEGORIES in tests/run_tests.py: "
            + ", ".join(missing),
        )

    def test_no_category_names_a_module_that_is_gone(self):
        on_disk = set(discovered_modules())

        for category, (_summary, modules) in CATEGORIES.items():
            with self.subTest(category=category):
                stale = sorted(set(modules) - on_disk)
                self.assertEqual(
                    stale,
                    [],
                    f"{category} names modules that no longer exist: {stale}",
                )

    def test_no_module_is_claimed_by_two_categories(self):
        seen: dict[str, str] = {}
        clashes: list[str] = []

        for category, (_summary, modules) in CATEGORIES.items():
            for module in modules:
                if module in seen:
                    clashes.append(f"{module} in {seen[module]} and {category}")
                seen[module] = category

        self.assertEqual(clashes, [])

    def test_category_names_do_not_collide_with_suite_names(self):
        collisions = sorted(set(CATEGORIES) & set(SUITES))

        self.assertEqual(collisions, [])


class LiveCheckRegistryTests(unittest.TestCase):
    def test_every_registered_live_check_exists_on_disk(self):
        for check in LIVE_CHECKS:
            with self.subTest(check=check.name):
                self.assertTrue(
                    (PROJECT_ROOT / "scripts" / check.script).is_file(),
                    f"{check.script} is registered but not on disk",
                )

    def test_every_live_script_on_disk_is_registered(self):
        on_disk = {
            path.name
            for path in (PROJECT_ROOT / "scripts").glob("live_*_check.py")
        }
        registered = {check.script for check in LIVE_CHECKS}

        unregistered = sorted(on_disk - registered)

        self.assertEqual(
            unregistered,
            [],
            "add these to LIVE_CHECKS in tests/run_tests.py: "
            + ", ".join(unregistered),
        )

    def test_names_and_tiers_are_usable(self):
        names = [check.name for check in LIVE_CHECKS]

        self.assertEqual(len(names), len(set(names)), f"duplicate: {names}")
        for check in LIVE_CHECKS:
            with self.subTest(check=check.name):
                self.assertIn(check.tier, LIVE_TIERS)


if __name__ == "__main__":
    unittest.main()
