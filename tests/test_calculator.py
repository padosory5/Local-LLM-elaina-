import unittest

from tools.calculator import CalculationError, compute_steps, evaluate_expression


class CalculatorEvaluatorTests(unittest.TestCase):
    def test_evaluates_plain_arithmetic(self):
        self.assertAlmostEqual(
            evaluate_expression("100/30*11 + (100+15*3)/30*10 + (100+15*2)/30*9"),
            124.0,
            places=6,
        )

    def test_evaluates_unary_and_parentheses(self):
        self.assertAlmostEqual(evaluate_expression("-(2 + 3) * 4"), -20.0)

    def test_rejects_function_calls(self):
        with self.assertRaises(CalculationError):
            evaluate_expression("__import__('os').system('echo hi')")

    def test_rejects_names(self):
        with self.assertRaises(CalculationError):
            evaluate_expression("os.getcwd()")

    def test_rejects_division_by_zero(self):
        with self.assertRaises(CalculationError):
            evaluate_expression("1/0")

    def test_rejects_huge_exponent(self):
        with self.assertRaises(CalculationError):
            evaluate_expression("2 ** 999999999")

    def test_rejects_oversized_expression(self):
        with self.assertRaises(CalculationError):
            evaluate_expression("1+" * 400 + "1")


class ComputeStepsTests(unittest.TestCase):
    def test_computes_each_step_and_preserves_order(self):
        steps = compute_steps([
            {"label": "Days 1-11 at 5 users", "expression": "100/30*11"},
            {"label": "Days 12-21 at 8 users", "expression": "(100+15*3)/30*10"},
            {"label": "Days 22-30 at 7 users", "expression": "(100+15*2)/30*9"},
            {
                "label": "Total",
                "expression": (
                    "100/30*11 + (100+15*3)/30*10 + (100+15*2)/30*9"
                ),
            },
        ])

        self.assertEqual(len(steps), 4)
        self.assertAlmostEqual(steps[-1].value, 124.0, places=6)

    def test_rejects_missing_label_or_expression(self):
        with self.assertRaises(CalculationError):
            compute_steps([{"label": "", "expression": "1+1"}])
        with self.assertRaises(CalculationError):
            compute_steps([{"label": "Total", "expression": ""}])

    def test_rejects_empty_step_list(self):
        with self.assertRaises(CalculationError):
            compute_steps([])


if __name__ == "__main__":
    unittest.main()
