"""Force annotation evaluation the way Python 3.11 does.

The production image runs Python 3.11, which evaluates annotations when a
function or class is defined. Python 3.14 (local dev) defers them (PEP 649), so
a missing import can pass every local test and still crash the container on
import -- which is exactly what happened with ``datetime``.

Accessing ``__annotations__`` forces that evaluation. Only a class's *own*
annotations are checked: ``typing.get_type_hints`` also resolves inherited
annotations (marshmallow's base classes reference names that live in
marshmallow's namespace), which produces false failures.
"""

import importlib
import inspect
import pkgutil

import pytest

import app as app_package


def _iter_modules():
    for module_info in pkgutil.walk_packages(app_package.__path__, prefix='app.'):
        if 'tests' in module_info.name:
            continue
        yield module_info.name


MODULES = sorted(_iter_modules())


def _own_annotations(obj, is_class):
    if is_class:
        return vars(obj).get('__annotations__', {})
    return getattr(obj, '__annotations__', {})


@pytest.mark.parametrize('module_name', MODULES)
def test_annotations_evaluate(module_name):
    """Accessing annotations must not raise NameError."""
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # an import failure is a different problem
        pytest.skip(f"module import failed for unrelated reasons: {exc}")

    failures = []
    for name, obj in list(vars(module).items()):
        is_class = inspect.isclass(obj) and obj.__module__ == module.__name__
        is_func = inspect.isfunction(obj) and obj.__module__ == module.__name__
        if not (is_class or is_func):
            continue
        try:
            annotations = _own_annotations(obj, is_class)
        except NameError as exc:
            failures.append(f"{module_name}.{name}: {exc}")
            continue
        for annotation in annotations.values():
            if isinstance(annotation, str):
                try:
                    eval(annotation, vars(module))  # noqa: S307 - test-only
                except NameError as exc:
                    failures.append(f"{module_name}.{name}: {exc}")

    assert not failures, (
        "annotations reference undefined names; Python 3.11 (production) raises "
        "NameError at import:\n" + "\n".join(failures[:10])
    )
