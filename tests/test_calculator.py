import unittest

from tools.calculator import CalculationError, evaluate_expression


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
if __name__ == "__main__":
    unittest.main()
