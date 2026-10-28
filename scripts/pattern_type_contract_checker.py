"""Conservative bounded static hints. No execution, rewriting, oracle, or correctness claim."""
import ast

def check(code):
 try:
  if not isinstance(code,str) or len(code)>60000:raise ValueError('Invalid source size')
  tree=ast.parse(code)
 except (SyntaxError,ValueError,RecursionError):return dict(status='UNKNOWN',issue_type=None,evidence=[],suggestion='Source could not be analyzed.')
 # Small representation lattice: int, str, seq:int, seq:str, patterns:str.
 types={};pattern_names=set();evidence=[];compares=0;known=0
 def infer(n):
  if isinstance(n,ast.Constant):return 'int' if type(n.value) is int else 'str' if isinstance(n.value,str) else None
  if isinstance(n,ast.Name):return types.get(n.id)
  if isinstance(n,ast.Subscript):
   if isinstance(n.slice,ast.Constant):
    if n.slice.value=='pattern':return 'str'
    if n.slice.value=='patterns':return 'patterns:str'
    if n.slice.value=='alphabet':return 'seq:int'
   base=infer(n.value)
   if isinstance(n.slice,ast.Slice):return base
   return {'seq:int':'int','seq:str':'str','str':'str','patterns:str':'str'}.get(base)
  if isinstance(n,(ast.Tuple,ast.List,ast.Set)):
   ts={infer(x) for x in n.elts}
   return 'seq:int' if ts=={'int'} else 'seq:str' if ts=={'str'} else None
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Name):
   name=n.func.id
   if name=='int':return 'int'
   if name=='str':return 'str'
   if name=='range':return 'seq:int'
   if name in ('tuple','list') and n.args:
    t=infer(n.args[0]);return 'seq:str' if t=='str' else t
   if name=='map' and len(n.args)>1 and isinstance(n.args[0],ast.Name):return {'int':'seq:int','str':'seq:str'}.get(n.args[0].id)
  if isinstance(n,ast.BinOp) and isinstance(n.op,ast.Add):
   a,b=infer(n.left),infer(n.right)
   if a==b:return a
   # Appending a known integer tuple proves element representation of an initially empty tail.
   if a=='seq:int' or b=='seq:int':return 'seq:int'
  return None
 # Only stable single-type assignments: conflicts become unknown, never guessed by variable name.
 conflicts=set()
 def bind(name,t):
  if not t or name in conflicts:return
  if name in types and types[name]!=t:types.pop(name,None);conflicts.add(name)
  else:types[name]=t
 for _ in range(6):
  for n in ast.walk(tree):
   if isinstance(n,(ast.Assign,ast.AnnAssign)):
    value=n.value;t=infer(value) if value else None
    targets=n.targets if isinstance(n,ast.Assign) else [n.target]
    for target in targets:
     if isinstance(target,ast.Name):
      bind(target.id,t)
      if value and any(isinstance(x,ast.Subscript) and isinstance(x.slice,ast.Constant) and x.slice.value in ('pattern','patterns') for x in ast.walk(value)):pattern_names.add(target.id)
   elif isinstance(n,ast.For) and isinstance(n.target,ast.Name):
    t=infer(n.iter);bind(n.target.id,{'seq:int':'int','seq:str':'str','patterns:str':'str','str':'str'}.get(t))
 for n in ast.walk(tree):
  if not isinstance(n,ast.Compare):continue
  allnodes=[n.left]+n.comparators
  # Pattern origin or literal character sequence required; not a general type linter.
  involved=any(isinstance(x,ast.Name) and x.id in pattern_names for y in allnodes for x in ast.walk(y))
  ts=[infer(x) for x in allnodes]
  if not involved:continue
  compares+=1
  for a,b in zip(ts,ts[1:]):
   if a and b:known+=1
   if (a,b) in [('int','str'),('str','int'),('seq:int','seq:str'),('seq:str','seq:int')]:evidence.append(dict(line=n.lineno,comparison=ast.get_source_segment(code,n),left=a,right=b))
 if evidence:return dict(status='WARNING',issue_type='Potential Pattern Type Mismatch',evidence=evidence,suggestion='Check element representations at this comparison. Convert explicitly if appropriate; this checker does not repair code or verify the answer.')
 return dict(status='PASS' if compares and known==compares else 'UNKNOWN',issue_type=None,evidence=[],suggestion='No detected mismatch in resolved comparisons; PASS is not a correctness certificate.' if compares and known==compares else 'Static representation information insufficient or no relevant comparison; absence of warning does not establish safety.')
