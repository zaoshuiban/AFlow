import unittest

from benchmarks.math import MATHBenchmark


class MathBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.benchmark = MATHBenchmark("MATH", "unused", "unused")

    def test_dfrac_and_frac_are_equivalent_without_antlr(self):
        self.assertTrue(
            self.benchmark.math_equal(r"\frac{39}{64}", r"\dfrac{39}{64}")
        )

    def test_latex_fraction_and_decimal_are_equivalent_without_antlr(self):
        self.assertTrue(self.benchmark.math_equal(r"\frac{175}{2}", "87.5"))
        self.assertTrue(self.benchmark.math_equal(r"-\frac{1}{2}", "-0.5"))

    def test_multiple_boxed_answers_match_one_unordered_list(self):
        expected = r"The possible values are \boxed{-1} and \boxed{2}."
        prediction = r"Therefore \boxed{2, -1}."
        score, extracted = self.benchmark.calculate_score(expected, prediction)
        self.assertEqual(score, 1)
        self.assertEqual(extracted, "2, -1")

    def test_unordered_answer_comparison_preserves_multiplicity(self):
        expected = r"\boxed{1} and \boxed{1}"
        prediction = r"\boxed{1,2}"
        score, _ = self.benchmark.calculate_score(expected, prediction)
        self.assertEqual(score, 0)

    def test_coordinates_are_not_treated_as_unordered_answer_lists(self):
        expected = r"\boxed{(1,2)}"
        prediction = r"\boxed{(2,1)}"
        score, _ = self.benchmark.calculate_score(expected, prediction)
        self.assertEqual(score, 0)

    def test_boxed_extraction_supports_arbitrary_nesting(self):
        text = (
            r"Work gives \boxed{\frac{6\sqrt{10}}{7}}. "
            r"Final \boxed{\begin{pmatrix}\frac{1}{5}\\-\frac{18}{5}"
            r"\end{pmatrix}}."
        )
        self.assertEqual(
            self.benchmark.extract_boxed_answers(text),
            [
                r"\frac{6\sqrt{10}}{7}",
                r"\begin{pmatrix}\frac{1}{5}\\-\frac{18}{5}\end{pmatrix}",
            ],
        )

    def test_terminal_units_and_degrees_are_ignored(self):
        equivalent = [
            (r"135^\circ", "135"),
            (r"100\text{ square units}", "100"),
            (r"5.4\text{ cents}", "5.4"),
            (r"9\text{ meters}", "9"),
            (r"800\text{ ft}^2", "800"),
            (r"28\%", "28"),
            (r"36\sqrt{3}\text{ square inches}", r"36\sqrt{3}"),
        ]
        for prediction, reference in equivalent:
            with self.subTest(prediction=prediction):
                self.assertTrue(self.benchmark.math_equal(prediction, reference))

    def test_text_wrappers_are_equivalent_but_text_content_is_preserved(self):
        self.assertTrue(self.benchmark.math_equal(r"\text{MAKE}", "MAKE"))
        self.assertTrue(self.benchmark.math_equal(r"\text{C,E}", "C,E"))
        self.assertFalse(self.benchmark.math_equal(r"\text{MAKE}", "MADE"))

    def test_ordered_matrices_compare_entries_semantically(self):
        prediction = (
            r"\begin{pmatrix}\frac{1}{5}\\-\frac{18}{5}\end{pmatrix}"
        )
        reference = r"\begin{pmatrix}1/5\\-18/5\end{pmatrix}"
        reversed_reference = r"\begin{pmatrix}-18/5\\1/5\end{pmatrix}"
        self.assertTrue(self.benchmark.math_equal(prediction, reference))
        self.assertFalse(
            self.benchmark.math_equal(prediction, reversed_reference)
        )

    def test_latex_spacing_commands_do_not_change_coordinates(self):
        prediction = r"\left(\frac{7}{45},\ \frac{4}{45}\right)"
        reference = r"\left( \frac{7}{45}, \frac{4}{45} \right)"
        self.assertTrue(self.benchmark.math_equal(prediction, reference))

    def test_nested_radicals_are_compared_symbolically_without_antlr(self):
        self.assertTrue(
            self.benchmark.math_equal(
                r"5\sqrt{\frac{2}{3}}", r"\frac{5\sqrt{6}}{3}"
            )
        )

    def test_unbraced_square_root_tokens_are_canonicalized(self):
        equivalent = [
            (r"121\sqrt2", r"121\sqrt{2}"),
            (r"49\sqrt 5", r"49\sqrt{5}"),
            (r"\sqrt\frac{2}{3}", r"\sqrt{\frac{2}{3}}"),
            (r"\sqrt\pi", r"\sqrt{\pi}"),
        ]
        for prediction, reference in equivalent:
            with self.subTest(prediction=prediction):
                self.assertTrue(self.benchmark.math_equal(prediction, reference))

    def test_grouped_and_optional_index_roots_are_left_intact(self):
        self.assertEqual(
            self.benchmark.normalize_answer(r"\sqrt{12}"),
            r"\sqrt{12}",
        )
        self.assertEqual(
            self.benchmark.normalize_answer(r"\sqrt[3]{8}"),
            r"\sqrt[3]{8}",
        )

    def test_unbraced_root_scores_as_the_same_boxed_answer(self):
        score, extracted = self.benchmark.calculate_score(
            r"Therefore \boxed{121\sqrt{2}}.",
            r"Therefore \boxed{121\sqrt2}.",
        )
        self.assertEqual(score, 1)
        self.assertEqual(extracted, r"121\sqrt2")

    def test_braced_thousands_separators_are_equivalent(self):
        self.assertTrue(
            self.benchmark.math_equal(r"2{,}875{,}125", "2875125")
        )
        score, extracted = self.benchmark.calculate_score(
            r"Therefore \boxed{2875125}.",
            r"Therefore \boxed{2{,}875{,}125}.",
        )
        self.assertEqual(score, 1)
        self.assertEqual(extracted, r"2{,}875{,}125")

    def test_factorial_expressions_are_compared_without_antlr(self):
        self.assertTrue(
            self.benchmark.math_equal(r"\frac{15!}{4}", "326918592000")
        )
        self.assertTrue(
            self.benchmark.math_equal(r"2\cdot15!", "2615348736000")
        )

    def test_boxed_arithmetic_equality_chain_uses_final_value(self):
        score, extracted = self.benchmark.calculate_score(
            r"Therefore \boxed{326918592000}.",
            r"Therefore \boxed{\frac{15!}{4}=326{,}918{,}592{,}000}.",
        )
        self.assertEqual(score, 1)
        self.assertEqual(
            extracted, r"\frac{15!}{4}=326{,}918{,}592{,}000"
        )
        self.assertFalse(
            self.benchmark.math_equal(r"15!=42", "1307674368000")
        )

    def test_antlr_supported_exact_values_and_pi_constant(self):
        for prediction, reference in [
            (r"\sqrt[3]{8}", "2"), (r"\sqrt[4]{16}", "2"),
            (r"\sin\left(\frac{\pi}{6}\right)", r"\frac{1}{2}"),
            (r"\cos(\pi)", "-1"), (r"\log_{2}8", "3"),
            (r"\binom{5}{2}", "10"),
        ]:
            with self.subTest(prediction=prediction):
                self.assertTrue(self.benchmark.math_equal(prediction, reference))
                self.assertTrue(self.benchmark.math_equal(reference, prediction))
        self.assertFalse(self.benchmark.math_equal(r"\sin(\pi/6)", r"\sqrt{3}/2"))
        self.assertFalse(self.benchmark.math_equal(r"\cos(\pi)", "1"))

    def test_malformed_expressions_cannot_score_via_partial_latex_parse(self):
        for prediction, reference in [("1+", "1"), ("2*", "2"), ("2^", "2")]:
            with self.subTest(prediction=prediction):
                score, _ = self.benchmark.calculate_score(
                    r"\boxed{" + reference + "}", r"\boxed{" + prediction + "}",
                )
                self.assertEqual(score, 0)

    def test_multiple_choice_letter_resolves_only_from_explicit_problem_options(self):
        problem = (
            r"What is the radius? $\text{(A) } 2\quad \text{(B) } 3\quad "
            r"\text{(C) } 4\quad \text{(D) } 5\quad \text{(E) } 6$"
        )
        score, extracted = self.benchmark.calculate_score(
            r"Therefore \boxed{2}", r"Therefore \boxed{A}", problem=problem
        )
        self.assertEqual((score, extracted), (1, "A"))
        self.assertEqual(
            self.benchmark.calculate_score(r"\boxed{2}", r"\boxed{B}", problem=problem)[0], 0
        )
        self.assertEqual(self.benchmark.extract_choice_options(problem)["C"], "4")

    def test_choice_parser_handles_textbf_and_fails_closed_on_bad_blocks(self):
        problem = (
            r"Choose: $\textbf{(A)}\ 7\qquad\textbf{(B)}\ 8\qquad"
            r"\textbf{(C)}\ 16\qquad\textbf{(D)}\ 17$"
        )
        self.assertEqual(
            self.benchmark.calculate_score(r"\boxed{16}", r"\boxed{C}", problem=problem)[0], 1
        )
        malformed = [
            r"Only (A) 2 is mentioned.",
            r"(A) 2 (A) 3", r"(A) 2 (C) 3", r"(A) (B) 3",
        ]
        for text in malformed:
            with self.subTest(text=text):
                self.assertEqual(self.benchmark.extract_choice_options(text), {})
                self.assertEqual(
                    self.benchmark.calculate_score(r"\boxed{2}", r"\boxed{A}", problem=text)[0], 0
                )

    def test_v10_format_coverage_and_negative_controls(self):
        cases = [
            (r"\$1,657", "1657", 1), (r"\$251.60", "251.60", 1),
            (r"\$251.60", "251.06", 0),
            (r"54\text{ gallons}", "54", 1),
            (r"54\text{ gallons}", "55", 0),
            ("1061_{9}", "1061_9", 1),
            ("1061_{9}", "1061_8", 0),
            ("1061_{9}", "1061", 0),
            (r"\text{H, E}", "E, H", 1),
            (r"\text{H, E}", "E, P", 0),
            ("(1,2)", "(2,1)", 0),
            ("1,234", "234,1", 0),
            ("A", "a", 0), ("x", "X", 0),
        ]
        for reference, prediction, expected in cases:
            with self.subTest(reference=reference, prediction=prediction):
                self.assertEqual(self.benchmark.calculate_score(
                    rf"\boxed{{{reference}}}", rf"\boxed{{{prediction}}}")[0], expected)
        for token in ("2", "7", "x"):
            self.assertEqual(self.benchmark.extract_model_answer(r"Answer is $\boxed " + token + "$.") , token)
        for expression in ("12", "2+3", "x+y"):
            self.assertEqual(self.benchmark.extract_boxed_answers(r"$\boxed " + expression + "$"), [])
        problem = "Select: (A) 2 (B) 3"
        self.assertEqual(self.benchmark.calculate_score(r"\boxed{\text{(A)}}", r"\boxed{A}", problem)[0], 1)
        self.assertEqual(self.benchmark.calculate_score(r"\boxed{\text{(A)}}", r"\boxed{B}", problem)[0], 0)

    def test_plain_text_casefold_does_not_merge_mathematical_variables(self):
        self.assertTrue(self.benchmark.math_equal("Orange", "orange"))
        self.assertTrue(self.benchmark.math_equal(r"\text{ORANGE}", "orange"))
        self.assertTrue(self.benchmark.math_equal("MAKE", "make"))
        self.assertFalse(self.benchmark.math_equal("MAKE", "MADE"))
        self.assertFalse(self.benchmark.math_equal("A", "a"))
        self.assertFalse(self.benchmark.math_equal("x", "X"))
        self.assertFalse(self.benchmark.math_equal("Area=R", "area=r"))


if __name__ == "__main__":
    unittest.main()
