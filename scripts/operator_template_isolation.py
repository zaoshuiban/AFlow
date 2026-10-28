"""Remove installed specialists from a generation template, without editing production."""
import ast
import json

HIDDEN_SPECIALISTS = (
    'ReusableParenthesizationIntervalDPTool',
    'ReusableRestrictedDigitPrimeEnumerator',
)


def isolate_template(sources, names=HIDDEN_SPECIALISTS):
    sources = dict(sources)
    removed = []
    # AST ranges preserve generic source verbatim. No generated/source code is executed.
    for filename in ('operator.py', 'operator_an.py'):
        text = sources[filename]
        lines = text.splitlines(keepends=True)
        nodes = [n for n in ast.parse(text).body
                 if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        for node in reversed(nodes):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
            del lines[start:node.end_lineno]
            removed.append(node.name)
        sources[filename] = ''.join(line for line in lines if not any(name in line for name in names))
        compile(sources[filename], filename, 'exec')
    descriptions = json.loads(sources['operator.json'])
    for name in names:
        descriptions.pop(name, None)
    sources['operator.json'] = json.dumps(descriptions, ensure_ascii=False, indent=2) + '\n'
    # These specialists currently use no prompt constants. Fail closed if a future
    # installation adds references that this narrow remover cannot safely eliminate.
    forbidden = (*names, 'scripts.parameterized_tools', 'scripts.restricted_prime_tool')
    if any(token in source for source in sources.values() for token in forbidden):
        raise ValueError('Specialist implementation remains in isolated template')
    return sources, {'hidden_operators': list(names), 'removed_definitions': sorted(set(removed)),
                     'generator_visibility': 'generic descriptions and approved evidence only',
                     'isolation_limit': 'prompt/data boundary, not an OS sandbox; repository files still exist'}
