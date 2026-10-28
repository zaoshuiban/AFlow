import unittest

from benchmarks.math import MATHBenchmark
from scripts.operator_library import ProblemFeatureExtractor
from scripts.restricted_prime_tool import (
    RESTRICTED_DIGIT_PRIME_MECHANISM,
    RestrictedDigitPrimeEnumerationTool,
)
from scripts.baseline_failure_mechanisms_v2 import _prime_case
from scripts.restricted_prime_v2_confirmation import _near_misses, _render_positive


class RestrictedDigitPrimeToolTests(unittest.TestCase):
    def test_original_math_119_natural_wording(self):
        problem = (
            "Pick two or fewer different digits from the set $\\{1, 3, 6, 7\\}$ "
            "and arrange them to form a number. How many prime numbers can we "
            "create in this manner?"
        )
        result = RestrictedDigitPrimeEnumerationTool.solve(problem)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.parameters["prime_count"], 10)

    def test_exact_enumeration_and_feature_use_same_detector(self):
        problem = (
            "Choose between one and 3 distinct digits from the set {0, 1, 3, 7} "
            "and arrange the chosen digits without repetition to form a positive "
            "integer (a leading zero is not allowed). How many distinct prime "
            "numbers can be formed?"
        )
        result = RestrictedDigitPrimeEnumerationTool.solve(problem)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.parameters["prime_count"], 15)
        score, _ = MATHBenchmark("MATH", "", "").calculate_score(
            "Therefore $\\boxed{15}$.", result.response
        )
        self.assertEqual(score, 1)
        context = ProblemFeatureExtractor().extract(problem)
        self.assertIn(RESTRICTED_DIGIT_PRIME_MECHANISM, context.features)

    def test_near_misses_do_not_match(self):
        near_misses = (
            "List all prime numbers formed from the set {0, 1, 3, 7} without repetition and no leading zero.",
            "What is the probability that an arrangement from the set {0, 1, 3, 7} is prime?",
            "How many integers can be arranged from the set {0, 1, 3, 7} without repetition and no leading zero?",
        )
        for problem in near_misses:
            with self.subTest(problem=problem):
                self.assertEqual(
                    RestrictedDigitPrimeEnumerationTool.solve(problem).status,
                    "no_match",
                )

    def test_unparseable_length_refuses_after_semantic_match(self):
        problem = (
            "Arrange digits from the set {0, 1, 3, 7} without repetition to form "
            "positive integers; a leading zero is not allowed. How many distinct "
            "prime numbers can be formed?"
        )
        result = RestrictedDigitPrimeEnumerationTool.solve(problem)
        self.assertEqual(result.status, "refused")

    def test_v2_hidden_surface_family_and_near_miss_boundaries(self):
        for variant in range(4):
            row = _render_positive(
                _prime_case(150013, variant, difficulty="hard"), variant
            )
            with self.subTest(surface=variant):
                self.assertTrue(
                    RestrictedDigitPrimeEnumerationTool.detect(row["problem"])
                )
                self.assertEqual(
                    RestrictedDigitPrimeEnumerationTool.solve(row["problem"]).status,
                    "success",
                )
        for row in _near_misses(seed=450020, count=8):
            with self.subTest(kind=row["mechanism_metadata"]["near_miss_kind"]):
                self.assertFalse(
                    RestrictedDigitPrimeEnumerationTool.detect(row["problem"])
                )


if __name__ == "__main__":
    unittest.main()
