import unittest
from scripts.pattern_type_contract_checker import check
class Tests(unittest.TestCase):
 def test_clear_integer_character_comparison(self):
  c='def solve(problem):\n p={};pat=p["pattern"]; alpha=p["alphabet"]\n for ch in alpha:\n  if ch==pat[0]: return 1\n return 0'
  self.assertEqual(check(c)['status'],'WARNING')
 def test_explicit_conversion_pass(self):
  c='def solve(problem):\n p={};pat=tuple(map(int,p["pattern"]));alpha=p["alphabet"]\n for ch in alpha:\n  if ch==pat[0]: return 1\n return 0'
  self.assertEqual(check(c)['status'],'PASS')
 def test_unknown_not_false_certification(self):
  self.assertEqual(check('def solve(problem):\n return 0')['status'],'UNKNOWN')
 def test_unknown_call_propagation(self):
  self.assertEqual(check('def solve(problem):\n pat=opaque();x=other()\n return int(x==pat)')['status'],'UNKNOWN')
 def test_unrelated_numeric_compare_no_warning(self):
  self.assertNotEqual(check('def solve(problem):\n n=3\n return int(n==2)')['status'],'WARNING')
