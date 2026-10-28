"""Prompt/schema-only normalization. Identity source adapter; frozen runtime unchanged."""
import copy
from scripts.counting_worker import MODULES
from scripts.operator_runtime.executor import execute,Resources

CONTRACT='''PYTHON TOOL CALLING CONTRACT (python-tool-interface-v2):
The tool receives {"code": "Python source"}. Its executor, not your code, invokes solve(problem) exactly once with the task JSON STRING. Decode it using json.loads(problem). Return a nonnegative exact int. The result is returned as tool_result.value/result; stdout is not an answer. Only submit records the final answer.
The module may contain ONLY import statements and function definitions. Put ALL assignments, constants, helper initialization and computation INSIDE solve or helper functions. No top-level print, function call, test, sample data, assignment, or if __name__ guard. Do not invoke solve yourself.
No decorators, including @lru_cache or @cache. No type annotations on ANY function, including nested functions. For caching, use a normal local dict or an explicit assignment INSIDE solve such as helper = lru_cache(maxsize=None)(helper). No classes, global/nonlocal, file/network/process access, dynamic code or introspection. Only the published builtin/attribute allowlists are available.
Allowed import modules: '''+','.join(sorted(MODULES))+'''. Importing sys, os, typing or time is forbidden.
VALID generic interface example (illustrates the protocol only, not the task algorithm):
import json
def solve(problem):
    p = json.loads(problem)
    return p["n"] * 2
INVALID: print(solve(problem)) at module level; def solve(problem: str) -> int; @lru_cache(None); top-level memo = {}; def main(problem).
Review these interface rules before the FIRST tool submission. Do not spend a tool call on a self-contained demo or test invocation. Submit source only, without Markdown fences. The adapter does not rewrite your program or repair algorithmic errors.
'''

def source_adapter(code):
    # Deliberately identity: no AST deletion, annotation stripping, decorator lowering,
    # entrypoint renaming, input conversion, answer lookup, or algorithm repair.
    return code

async def executor(code,problem,wall,**kwargs):
    return await execute(source_adapter(code),problem,wall,resources=Resources(cpu_seconds=5),**kwargs)

class InterfaceTransport:
    def __init__(self,base):
        self.base=base;self.controller=base.controller
    async def call(self,key,messages,output_cap,**kwargs):
        messages=copy.deepcopy(messages);messages[0]['content']+='\n'+CONTRACT
        ts=copy.deepcopy(kwargs.get('tools'))
        for t in ts or []:
            if t['function']['name']=='python':t['function']['description']=CONTRACT
        kwargs['tools']=ts
        return await self.base.call(key,messages,output_cap,**kwargs)
