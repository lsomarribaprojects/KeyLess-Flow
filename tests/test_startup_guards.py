"""Startup guards — catch the class of bug that shipped in v1.3.0.

v1.3.0 crashed on launch with `UnboundLocalError: QTimer` because main() had a
LOCAL `from PyQt6.QtCore import QTimer` further down: Python then treats the
name as local for the WHOLE function, so an earlier use blows up. `import main`
and py_compile both pass — only running main() reveals it. These tests scan
the AST so it can never ship again, and confirm the module-level names main()
relies on exist.

Runs without pytest:  venv\\Scripts\\python.exe tests\\test_startup_guards.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _module_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def test_no_function_local_import_shadows_module_name():
    """A local import of a name that ALSO exists at module level makes every
    earlier use in that function an UnboundLocalError. Forbid it in main.py."""
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    module_names = _module_level_names(tree)
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    local = (a.asname or a.name).split(".")[0]
                    if local in module_names:
                        offenders.append(f"{fn.name}(): line {node.lineno} re-imports '{local}'")
    assert not offenders, "local imports shadow module-level names:\n  " + "\n  ".join(offenders)


def test_main_uses_only_bound_names_before_local_imports():
    """Stricter: inside main(), any name used BEFORE a local import/assignment
    of that same name is an UnboundLocalError waiting to happen."""
    src = open(os.path.join(ROOT, "main.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    main_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    # Names assigned/imported anywhere in main() (not nested functions).
    local_defs: dict[str, int] = {}
    for node in ast.walk(main_fn):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                local_defs.setdefault((a.asname or a.name).split(".")[0], node.lineno)
    problems = []
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in local_defs and node.lineno < local_defs[node.id]:
                problems.append(f"'{node.id}' used at line {node.lineno} before local import at {local_defs[node.id]}")
    assert not problems, "\n  ".join(problems)


def test_main_module_imports_qtimer_at_top():
    import main
    assert hasattr(main, "QTimer"), "QTimer must be a module-level import in main.py"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
