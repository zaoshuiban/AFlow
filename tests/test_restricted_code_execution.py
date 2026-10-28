import unittest

from scripts.restricted_code_execution import execute_restricted_code


class RestrictedCodeExecutionTests(unittest.TestCase):
    def test_calls_solve_and_returns_exact_result(self):
        status, output = execute_restricted_code(
            "def solve():\n"
            "    values = {1, 2, 2, 3}\n"
            "    return len(values)\n"
        )
        self.assertEqual(status, "Success")
        self.assertEqual(output, "3")

    def test_allows_small_exact_math_imports(self):
        status, output = execute_restricted_code(
            "from fractions import Fraction\n"
            "def solve():\n"
            "    return Fraction(1, 3) + Fraction(1, 6)\n"
        )
        self.assertEqual(status, "Success")
        self.assertEqual(output, "1/2")

    def test_rejects_file_access(self):
        status, output = execute_restricted_code(
            "def solve():\n"
            "    return open('hidden.json').read()\n"
        )
        self.assertEqual(status, "Error")
        self.assertIn("prohibited name: open", output)

    def test_rejects_network_or_process_imports(self):
        for module in ("socket", "requests", "subprocess", "pathlib"):
            with self.subTest(module=module):
                status, output = execute_restricted_code(
                    f"import {module}\n"
                    "def solve():\n"
                    "    return 1\n"
                )
                self.assertEqual(status, "Error")
                self.assertIn("prohibited import", output)


if __name__ == "__main__":
    unittest.main()
