"""Small deterministic sandbox guard for generated mathematical Python.

This is not a general security boundary. It deliberately accepts only the
language features needed by finite exact-computation operators and rejects file,
network, process, reflection, and dynamic-code access before execution in the
existing isolated worker process.
"""

from __future__ import annotations

import ast
import builtins
import traceback
from typing import Any, Tuple


ALLOWED_IMPORT_ROOTS = {
    "collections",
    "decimal",
    "fractions",
    "functools",
    "itertools",
    "math",
    "statistics",
}

BLOCKED_NAMES = {
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
    "__import__",
}

SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "ArithmeticError",
        "Exception",
        "ValueError",
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "enumerate",
        "filter",
        "float",
        "int",
        "len",
        "list",
        "map",
        "max",
        "min",
        "pow",
        "print",
        "range",
        "reversed",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    )
}


def _safe_import(
    name: str,
    globals: Any = None,
    locals: Any = None,
    fromlist: Any = (),
    level: int = 0,
) -> Any:
    root = str(name).split(".", 1)[0]
    if level or root not in ALLOWED_IMPORT_ROOTS:
        raise ImportError(f"import is not allowed: {name}")
    return builtins.__import__(name, globals, locals, fromlist, level)


def validate_generated_code(code: str) -> None:
    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"prohibited import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = str(node.module or "").split(".", 1)[0]
            if node.level or root not in ALLOWED_IMPORT_ROOTS:
                raise ValueError(f"prohibited import: {node.module}")
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES or node.id.startswith("__"):
                raise ValueError(f"prohibited name: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(f"prohibited attribute: {node.attr}")


def execute_restricted_code(code: str) -> Tuple[str, str]:
    try:
        validate_generated_code(code)
        safe_builtins = dict(SAFE_BUILTINS)
        safe_builtins["__import__"] = _safe_import
        namespace = {
            "__builtins__": safe_builtins,
            "__name__": "__generated_math_operator__",
        }
        exec(compile(code, "<generated-math-code>", "exec"), namespace)
        if "solve" not in namespace:
            return "Error", "Function or value 'solve' not found"
        solver = namespace["solve"]
        result = solver() if callable(solver) else solver
        return "Success", str(result)
    except Exception as exc:
        return "Error", (
            f"Execution error: {exc}\n"
            + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
