import unittest
from scripts.python_tool_interface_v2 import source_adapter,executor
from scripts.operator_runtime.executor import static_check

class InterfaceTests(unittest.IsolatedAsyncioTestCase):
 async def test_valid(self):
  r=await executor('import json\ndef solve(problem):\n return json.loads(problem)["n"]*2','{"n":3}',3)
  self.assertEqual(r['value'],6)
 async def test_wrong_return(self):
  r=await executor('def solve(problem):\n return "6"','{}',3)
  self.assertEqual(r['execution_status'],'ERROR')
 async def test_math_error_not_repaired(self):
  code='import json\ndef solve(problem):\n return json.loads(problem)["n"]+2'
  self.assertEqual(source_adapter(code),code)
  r=await executor(code,'{"n":3}',3)
  self.assertEqual(r['value'],5);self.assertNotEqual(r['value'],6)
 def test_reject_interface_and_security(self):
  for code in ['def solve(problem: str) -> int:\n return 0', 'import os\ndef solve(problem):\n return 0', 'def solve(problem):\n return 0\nprint(solve("{}"))','from functools import lru_cache\n@lru_cache(None)\ndef solve(problem):\n return 0','def main(problem):\n return 0']:
   with self.subTest(code=code):
    self.assertEqual(source_adapter(code),code)
    with self.assertRaises(ValueError):static_check(source_adapter(code))
