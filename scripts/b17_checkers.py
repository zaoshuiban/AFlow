"""Bounded runtime contract checking via semantics-preserving comparison probes.
No oracle, solver generation or production program rewriting. Existing native executor only.
"""
import ast,copy,json,time
from scripts.operator_runtime import execute,Resources

PREFIX='_b17_contract_'
def analyze(code):
 from scripts.operator_runtime.executor import static_check
 tree=static_check(code)
 if any(isinstance(n,ast.Name) and (n.id.startswith(PREFIX) or n.id=='solve') for n in ast.walk(tree)):raise ValueError('Reserved names or recursive module entrypoint outside supported class')
 taint=set()
 def origin(expr):
  return any(isinstance(n,ast.Subscript) and isinstance(n.slice,ast.Constant) and n.slice.value in ('pattern','patterns') or isinstance(n,ast.Name) and n.id in taint for n in ast.walk(expr))
 for _ in range(12):
  for n in ast.walk(tree):
   if isinstance(n,ast.Assign) and origin(n.value):
    for t in n.targets:
     if isinstance(t,ast.Name):taint.add(t.id)
   if isinstance(n,(ast.For,ast.comprehension)) and origin(n.iter) and isinstance(n.target,ast.Name):taint.add(n.target.id)
 sites=[]
 for n in ast.walk(tree):
  if isinstance(n,ast.Compare) and len(n.ops)==1 and isinstance(n.ops[0],(ast.Eq,ast.NotEq)) and (origin(n.left) or origin(n.comparators[0])):
   sites.append(dict(line=n.lineno,source=ast.get_source_segment(code,n),node=n))
 if len(sites)>100:raise ValueError('Too many comparison sites')
 return tree,sites

HELPERS='''
def _b17_contract_normal(value):
    if type(value) is int and 0 <= value <= 3:
        return (value,)
    if type(value) is str and value and all(ch in '0123' for ch in value):
        return tuple(map(int, value))
    if type(value) in (tuple, list) and value:
        if all(type(x) is int and 0 <= x <= 3 for x in value):
            return tuple(value)
        if all(type(x) is str and len(x) == 1 and x in '0123' for x in value):
            return tuple(map(int, value))
    return None

def _b17_contract_kind(value):
    if type(value) is int: return 1
    if type(value) is str: return 2
    if type(value) in (tuple, list) and value:
        if all(type(x) is int for x in value): return 1
        if all(type(x) is str for x in value): return 2
    return 0
'''

def instrument(code,tree,sites,mode):
 ids={id(s['node']):i for i,s in enumerate(sites)}
 class Rewrite(ast.NodeTransformer):
  def visit_Compare(self,node):
   index=ids.get(id(node));node=self.generic_visit(node)
   if index is None:return node
   return ast.copy_location(ast.Call(func=ast.Name(id=PREFIX+'compare',ctx=ast.Load()),args=[node.left,node.comparators[0],ast.Constant(index),ast.Constant(isinstance(node.ops[0],ast.NotEq))],keywords=[]),node)
 t=Rewrite().visit(tree)
 for n in t.body:
  if isinstance(n,ast.FunctionDef) and n.name=='solve':n.name=PREFIX+'user'
 body=ast.unparse(ast.fix_missing_locations(t))
 helper='''
def _b17_contract_compare(left, right, index, negate):
    _b17_contract_seen[0] = 1
    a = _b17_contract_normal(left)
    b = _b17_contract_normal(right)
    mismatch = (_b17_contract_kind(left) * _b17_contract_kind(right) == 2 and a is not None and b is not None and a == b)
    if mismatch:
        _b17_contract_seen[1] = _b17_contract_seen[1] | (1 << index)
    if mismatch and MODE:
        return False if negate else True
    return (left != right) if negate else (left == right)
'''.replace('MODE',str(mode==2))
 nested='\n'.join('    '+line for line in (HELPERS+helper+body).splitlines())
 tail='\n    return _b17_contract_seen[0] + 2 * _b17_contract_seen[1]' if mode==1 else '\n    return _b17_contract_result'
 return 'def solve(problem):\n    _b17_contract_seen = [0, 0]\n'+nested+'\n    _b17_contract_result = _b17_contract_user(problem)'+tail+'\n'

async def check(code,problem,wall=3,kind='mechanism'):
 start=time.monotonic();base=dict(checker=kind,status='ABSTAIN',evidence=[],scope='Only observed, pattern-origin binary ==/!= over public digit representations on this supplied input; no full correctness claim',uses_reference=False)
 try:tree,sites=analyze(code)
 except (ValueError,SyntaxError,RecursionError) as exc:return dict(base,reason='unsupported_source: '+str(exc),elapsed_seconds=time.monotonic()-start)
 if not sites:return dict(base,reason='no_supported_pattern_comparison',elapsed_seconds=time.monotonic()-start)
 async def run(src):
  remaining=wall-(time.monotonic()-start)
  if remaining<=.02:return dict(status='error',error='checker_wall_budget',error_type='CODE_RESOURCE_LIMIT')
  return await execute(src,problem,min(remaining,6),resources=Resources(cpu_seconds=5))
 original=await run(code)
 if original.get('status')!='success':return dict(base,reason='original_not_completed',original=original,elapsed_seconds=time.monotonic()-start)
 probe=await run(instrument(code,tree,sites,1))
 if probe.get('status')!='success':return dict(base,reason='probe_not_completed',probe=probe,elapsed_seconds=time.monotonic()-start)
 mask=probe['value']//2;observed=probe['value']%2
 if not observed:return dict(base,reason='comparison_not_reached',elapsed_seconds=time.monotonic()-start)
 if not mask:return dict(base,status='PASS',reason='no_observed_representation_violation_within_scope',elapsed_seconds=time.monotonic()-start)
 # Re-parse: the first transformation consumed nodes; keep the original program unchanged.
 tree,sites=analyze(code);counter=await run(instrument(code,tree,sites,2))
 if counter.get('status')!='success':return dict(base,reason='counterfactual_not_completed',elapsed_seconds=time.monotonic()-start)
 if counter['value']==original['value']:return dict(base,reason='no_observed_return_effect',elapsed_seconds=time.monotonic()-start)
 evidence=[dict(line=s['line'],comparison=s['source'],actually_executed=True,inconsistent_digit_representation=True) for i,s in enumerate(sites) if mask & (1<<i)]
 # Generic uses the SAME public-schema contract and dynamic engine, with no historical
 # failure IDs/examples. It intentionally is not weakened: the observable predicates coincide.
 return dict(base,status='VIOLATION',reason='observed_contract_mismatch_with_counterfactual_return_effect',evidence=evidence,elapsed_seconds=time.monotonic()-start,warning='Normalize compared digit element representations. This evidence does not supply an answer or prove all other logic correct.')
