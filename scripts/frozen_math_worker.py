"""Isolated bounded worker for a narrow generated solve(problem) function.

AST allowlists are defense in depth, not a general-purpose hostile-code sandbox.
No repository modules, files, network, dynamic code or reflection are exposed.
"""
import ast
import builtins
import json
import sys

MODULES = {'math', 'itertools', 're', 'collections', 'functools', 'fractions'}
BUILTINS = ('abs all any bool dict enumerate filter float int len list map max min pow range reversed round set sorted str sum tuple zip isinstance ValueError Exception ArithmeticError'.split())
ATTRIBUTES = set(('I S M IGNORECASE DOTALL MULTILINE compile sqrt isqrt gcd lcm ceil floor factorial prod permutations combinations product chain findall finditer search match fullmatch sub split strip lower upper startswith endswith replace join group groups append extend add update discard remove pop count index get items keys values copy sort reverse union intersection difference setdefault Counter defaultdict deque Fraction reduce lru_cache').split())


def validate(code):
    if not isinstance(code, str) or len(code) > 60000: raise ValueError('Invalid code length')
    tree = ast.parse(code)
    if not any(isinstance(n, ast.FunctionDef) and n.name == 'solve' for n in tree.body):
        raise ValueError('Define solve(problem)')
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.With, ast.AsyncWith, ast.Global, ast.Nonlocal)):
            raise ValueError('Unsupported language feature')
        if isinstance(node, ast.Name) and (node.id.startswith('__') or node.id in {'open','exec','eval','compile','getattr','setattr','globals','locals','vars','__import__','input','breakpoint'}):
            raise ValueError('Prohibited name')
        if isinstance(node, ast.Attribute) and node.attr not in ATTRIBUTES:
            raise ValueError('Attribute not allowed: '+node.attr)
        if isinstance(node, ast.Import):
            if any(a.name not in MODULES for a in node.names): raise ValueError('Import not allowed')
        if isinstance(node, ast.ImportFrom):
            if node.level or node.module not in MODULES or any(a.name not in ATTRIBUTES for a in node.names):
                raise ValueError('Import not allowed')
    return tree


def main():
    # Set limits before compiling/executing generated text. Only trusted worker IO
    # runs outside the generated namespace.
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (5,5))
    # macOS may reject RLIMIT_DATA/RLIMIT_AS. RSS monitoring is explicit and
    # best-effort there; do not advertise it as a hard address-space sandbox.
    import signal
    def memory_check(*_):
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        size=peak if sys.platform=='darwin' else peak*1024
        if size > 256*1024**2: raise SystemExit('candidate_memory_threshold')
    signal.signal(signal.SIGALRM,memory_check)
    signal.setitimer(signal.ITIMER_REAL,0.05,0.05)
    resource.setrlimit(resource.RLIMIT_FSIZE, (0,0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16,16))
    try:
        data=json.loads(sys.stdin.read(100000))
        validate(data['code'])
        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level or name not in MODULES: raise ImportError('Blocked import')
            return builtins.__import__(name, globals, locals, fromlist, level)
        namespace={'__builtins__':{**{n:getattr(builtins,n) for n in BUILTINS},'__import__':safe_import}}
        exec(compile(data['code'],'<frozen-candidate>','exec'),namespace)
        value=namespace['solve'](data['problem'])
        if value is not None and (type(value) is not int or value < 0 or value.bit_length()>128):
            raise ValueError('Return a nonnegative integer or None for refusal')
        print(json.dumps({'status':'declined' if value is None else 'success','value':value}))
    except BaseException as exc:
        print(json.dumps({'status':'error','error':type(exc).__name__+': '+str(exc)[:500]}))

if __name__=='__main__':main()
