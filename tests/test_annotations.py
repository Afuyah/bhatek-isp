"""Guards against annotation-time NameErrors.

Python 3.14 defers annotation evaluation (PEP 649), so a module can reference an
undefined name in an annotation and still import cleanly. Python 3.11 -- which
the production image runs -- evaluates annotations when the function or class is
created, so the same code raises ``NameError`` at import and the container
crash-loops.

That is exactly how a missing ``from datetime import datetime`` shipped: it
passed the whole suite locally on 3.14 and killed the app on Fly.

This test walks every module's annotations and asserts that each name used in
one is actually bound at module level (or is a builtin).
"""

import ast
import builtins
import pathlib

APP = pathlib.Path(__file__).resolve().parents[1] / 'app'
BUILTINS = set(dir(builtins))


def _annotation_names(node):
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            current = sub
            while isinstance(current, ast.Attribute):
                current = current.value
            if isinstance(current, ast.Name):
                names.add(current.id)
    return names


def _module_names(tree):
    defined = set(BUILTINS)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add(alias.asname or alias.name.split('.')[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, ast.Try):
            # imports inside try/except (optional dependencies)
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for alias in sub.names:
                        defined.add(alias.asname or alias.name.split('.')[0])
    return defined


def test_no_unresolvable_names_in_annotations():
    problems = []
    for path in sorted(APP.rglob('*.py')):
        source = path.read_text(encoding='utf-8', errors='replace')
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        defined = _module_names(tree)

        for node in ast.walk(tree):
            annotations = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for arg in list(node.args.args) + list(node.args.kwonlyargs):
                    if arg.annotation is not None:
                        annotations.append(arg.annotation)
                if node.returns is not None:
                    annotations.append(node.returns)
            elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
                annotations.append(node.annotation)

            for annotation in annotations:
                for name in _annotation_names(annotation):
                    if name not in defined:
                        problems.append(f"{path.relative_to(APP.parent)}:{node.lineno} "
                                        f"uses undefined '{name}' in an annotation")

    assert not problems, (
        "annotations reference names that are not imported or defined at module "
        "level; this raises NameError on Python 3.11 (the production image):\n"
        + "\n".join(problems[:20])
    )
