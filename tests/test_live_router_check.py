import unittest
from types import SimpleNamespace

from scripts.live_router_check import mismatches


class LiveRouterComparisonTests(unittest.TestCase):
    def test_action_target_ignores_punctuation_and_polite_wrapper(self):
        result = SimpleNamespace(
            action_target="Play Dynamite in Spotify",
        )

        self.assertEqual(
            mismatches(
                result,
                {"action_target": "Play Dynamite in Spotify for me."},
            ),
            [],
        )

    def test_action_target_still_rejects_a_substituted_target(self):
        result = SimpleNamespace(action_target="Open Spotify")

        differences = mismatches(
            result,
            {"action_target": "Open PowerShell."},
        )

        self.assertEqual(len(differences), 1)


if __name__ == "__main__":
    unittest.main()
