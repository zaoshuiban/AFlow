"""Common review-stage calling contract; no source rewriting or extra permissions."""
import copy
from scripts.python_tool_interface_v2 import CONTRACT as BASE_CONTRACT,executor,source_adapter
VERSION='python-tool-interface-v3'
REVIEW_CONTRACT='''COMMON CODE REVIEW / RE-EXECUTION CONTRACT:
Every python call, including review, testing, and correction, submits a COMPLETE MODULE defining solve(problem). The executor injects the SAME ORIGINAL task JSON string on every invocation. It does not run a notebook cell or retain previous function definitions.
To review: inspect the previous source/result and the public constraints; if another execution is useful, submit the complete reviewed/revised solve module again. Keep helper definitions and all initialization inside solve. A helper definition inside solve is allowed; classes, global/nonlocal and introspection are not.
To run auxiliary checks, put their code INSIDE a function and call it INSIDE solve. Still return the answer for the ORIGINAL input, not a reduced test problem, boolean, assertion outcome or diagnostic dictionary. Do not put assert/test loops/assignments/print/function calls at module level. Do not call solve yourself at module level. Do not import a testing framework, sys/os/typing/time, or inspect function internals.
No decorators or function type annotations, even when rewriting previously valid code. Repeating an execution does not require adding an entrypoint, top-level print, or an if __name__ guard. You may perform conceptual review without spending another tool call; do not feel required to construct an exhaustive validator.
After review use submit for final delivery; a Python result alone is not a final answer. Review shares the ORIGINAL common four-call/8192-output-token/10-tool-second/180-second budget, with the last model call reserved for submit. Additional checks must fit the remaining budget. Never add tools on the final delivery turn.
'''
CONTRACT=BASE_CONTRACT.replace('python-tool-interface-v2',VERSION)+'\n'+REVIEW_CONTRACT
SCHEMA_DESCRIPTION='Submit a complete imports/functions-only module defining solve(problem), including on every review/rewrite. JSON string input injected automatically. No top-level tests/calls/assignments, decorators or annotations. Return original-task exact int; submit separately. See common system contract.'
class InterfaceTransport:
 def __init__(self,base,extra=''):
  self.base=base;self.controller=getattr(base,'controller',None);self.extra=extra
 async def call(self,key,messages,output_cap,**kwargs):
  messages=copy.deepcopy(messages);messages[0]['content']+='\n'+CONTRACT+('\n'+self.extra if self.extra else '')
  # Repeat a compact reminder near the end of the system message, common to both arms.
  messages[0]['content']+='\nBefore any re-execution: submit the complete solve module again; no module-level test statements. Final delivery remains submit.'
  tools=copy.deepcopy(kwargs.get('tools'))
  for t in tools or []:
   if t['function']['name']=='python':t['function']['description']=SCHEMA_DESCRIPTION
  kwargs['tools']=tools
  return await self.base.call(key,messages,output_cap,**kwargs)
