"""The router must be able to finish its own answer.

One turn in 55 of the first dogfooding session ended like this:

    [Router] Invalid structured output; retrying once in JSON repair mode.
    [Router] conversation (0.00): Safe fallback after router failure.

Everything downstream then ran on a fallback: the goal layer read the
subject as "Can you find some", the capability layer picked web_search
for it, and the query was built from an unrelated open task.

Phase 4E-I measured this schema at a median of 268 output tokens across
31 fields. The cap was 320. A turn whose free-text "reason" ran long was
truncated mid-JSON -- and truncation is not something the repair pass can
fix, because the missing half was never generated.

A cap is not a target: decode stops at the stop token, so headroom above
the median is free. This asserts the relationship rather than the number,
so a schema that grows fails here instead of failing live.
"""

import ast
import unittest
from pathlib import Path

from brain.intent_router import ROUTER_OUTPUT_CAP

# Measured, 4E-I: scripts/router_latency_check.py, qwen3:8b.
MEASURED_MEDIAN_OUTPUT_TOKENS = 268


class OutputCapTests(unittest.TestCase):

    def test_the_cap_leaves_real_headroom_over_the_measured_output(self):
        self.assertGreaterEqual(
            ROUTER_OUTPUT_CAP, MEASURED_MEDIAN_OUTPUT_TOKENS * 1.75,
            "the router can run out of tokens mid-JSON again",
        )

    def test_every_router_call_uses_the_same_cap(self):
        # The drift guard. The repair pass had its own copy of the number,
        # so raising one and not the other would leave the retry -- the
        # one call that only happens when something already went wrong --
        # still truncating.
        source = Path(__file__).resolve().parents[1] / "brain" / "intent_router.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))

        literals = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "num_predict":
                    literals.append(value)

        self.assertTrue(literals, "no router num_predict found at all")
        for value in literals:
            self.assertIsInstance(
                value, ast.Name,
                "a router call hard-codes its own token cap",
            )
            self.assertEqual(value.id, "ROUTER_OUTPUT_CAP")


if __name__ == "__main__":
    unittest.main()
