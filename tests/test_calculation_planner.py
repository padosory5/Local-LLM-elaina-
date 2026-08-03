import unittest

from brain.calculation_planner import CalculationPlanner


def _tool_call(label, expression):
    return {"function": {"name": "calculate", "arguments": {
        "label": label,
        "expression": expression,
    }}}


def _message(*, content="", tool_calls=None):
    return {"message": {"content": content, "tool_calls": tool_calls}}


class FakeClient:
    """Returns one queued response per .chat() call, ignoring arguments."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CalculationPlannerTests(unittest.TestCase):
    def test_solves_with_incremental_tool_calls(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[
                    _tool_call("Total contribution", "100 + 100 + 50"),
                ]),
                _message(tool_calls=[
                    _tool_call("Contributor 1 share", "650 * (100 / 250)"),
                ]),
                _message(tool_calls=[
                    _tool_call("Contributor 2 share", "650 * (100 / 250)"),
                ]),
                _message(tool_calls=[
                    _tool_call("Contributor 3 share", "650 * (50 / 250)"),
                ]),
                _message(content=(
                    "Contributor 1 gets 260, contributor 2 gets 260, and "
                    "contributor 3 gets 130."
                )),
            ]),
            "qwen3:8b",
            -1,
        )

        plan = planner.plan("split 650 proportionally among 100, 100, 50")

        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.steps), 4)
        self.assertAlmostEqual(plan.steps[1].value, 260.0)
        self.assertAlmostEqual(plan.steps[2].value, 260.0)
        self.assertAlmostEqual(plan.steps[3].value, 130.0)

    def test_rejects_a_final_answer_backed_by_zero_tool_calls(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(content="The answer is 260, 260, and 130."),
                _message(content="The answer is 260, 260, and 130."),
                _message(content="The answer is 260, 260, and 130."),
            ]),
            "qwen3:8b",
            -1,
        )

        self.assertIsNone(planner.plan("split 650 proportionally"))

    def test_nudges_when_model_narrates_instead_of_calling_the_tool(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[
                    _tool_call("Total contribution", "100 + 100 + 50"),
                ]),
                # The model drops back to narration instead of calling the
                # tool for the next step -- this must be caught and nudged.
                _message(content="Now let's calculate each contributor's share."),
                _message(tool_calls=[
                    _tool_call("Contributor 1 share", "650 * (100 / 250)"),
                ]),
                _message(content="Contributor 1 gets 260."),
            ]),
            "qwen3:8b",
            -1,
        )

        plan = planner.plan("split 650 proportionally")

        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.steps), 2)

    def test_gives_up_after_too_many_nudges(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[_tool_call("Step 1", "1 + 1")]),
                _message(content="Let's figure out the next part."),
                _message(content="Now let's compute the rest."),
                _message(content="Next, let's work out the remainder."),
            ]),
            "qwen3:8b",
            -1,
        )

        self.assertIsNone(planner.plan("some problem"))

    def test_unsafe_expression_is_reported_back_and_recovered(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[
                    _tool_call("Bad step", "__import__('os')"),
                ]),
                _message(tool_calls=[
                    _tool_call("Good step", "2 + 2"),
                ]),
                _message(content="The answer is 4."),
            ]),
            "qwen3:8b",
            -1,
        )

        plan = planner.plan("what is 2 + 2")

        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.total_value, 4.0)

    def test_gives_up_after_too_many_tool_errors(self):
        bad_call = _message(tool_calls=[
            _tool_call("Bad step", "__import__('os')"),
        ])
        planner = CalculationPlanner(
            FakeClient([bad_call, bad_call, bad_call, bad_call]),
            "qwen3:8b",
            -1,
        )

        self.assertIsNone(planner.plan("what is 2 + 2"))

    def test_client_exception_is_handled_safely(self):
        planner = CalculationPlanner(
            FakeClient([RuntimeError("offline")]),
            "qwen3:8b",
            -1,
        )

        self.assertIsNone(planner.plan("what is 2 + 2"))

    def test_trusted_result_text_lists_every_verified_step(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[_tool_call("Total", "2 + 2")]),
                _message(content="The total is 4."),
            ]),
            "qwen3:8b",
            -1,
        )

        plan = planner.plan("what is 2 + 2")

        self.assertIn("Total: 4", plan.as_trusted_result_text())

    def test_trusted_text_sums_dollar_component_steps_in_python(self):
        # This is the exact bug from the proration case: the model verified
        # each range's cost correctly but never made a final tool-verified
        # sum, so the phrasing pass had to add them up itself and got it
        # wrong. The total must now be computed here, not left to the model.
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[
                    _tool_call("days in first range", "11 - 1 + 1"),
                ]),
                _message(tool_calls=[
                    _tool_call("prorated cost for first range", "100/30*11"),
                ]),
                _message(tool_calls=[
                    _tool_call("prorated cost for second range", "145/30*10"),
                ]),
                _message(tool_calls=[
                    _tool_call("prorated cost for third range", "130/30*9"),
                ]),
                _message(content="Here is the full breakdown."),
            ]),
            "qwen3:8b",
            -1,
        )

        plan = planner.plan("prorated billing question")
        trusted_text = plan.as_trusted_result_text()

        self.assertIn("days in first range", trusted_text)
        self.assertIn("Combined total of the amounts above", trusted_text)
        self.assertIn("124", trusted_text)

    def test_does_not_double_count_when_model_already_gave_a_total(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[
                    _tool_call("Contributor 1 share", "260"),
                ]),
                _message(tool_calls=[
                    _tool_call("Contributor 2 share", "260"),
                ]),
                _message(tool_calls=[
                    _tool_call("Grand total", "260 + 260"),
                ]),
                _message(content="Here is the breakdown."),
            ]),
            "qwen3:8b",
            -1,
        )

        plan = planner.plan("split 520 between two people")
        trusted_text = plan.as_trusted_result_text()

        self.assertNotIn("Combined total of the amounts above", trusted_text)

    def test_no_synthetic_total_with_fewer_than_two_components(self):
        planner = CalculationPlanner(
            FakeClient([
                _message(tool_calls=[_tool_call("Total cost", "2 + 2")]),
                _message(content="The total is 4."),
            ]),
            "qwen3:8b",
            -1,
        )

        plan = planner.plan("what is 2 + 2")
        trusted_text = plan.as_trusted_result_text()

        self.assertNotIn("Combined total of the amounts above", trusted_text)


if __name__ == "__main__":
    unittest.main()
