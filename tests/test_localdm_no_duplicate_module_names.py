"""test_localdm_no_duplicate_module_names.py: one name, one meaning, per module.

This exists because of a bug that no per-PR check could have caught.

Two PRs both added a module-level `_SENTENCE` to `scripts/localdm/reply.py` and
both were green on their own, because each was correct in isolation. Rebasing one
onto the other was clean too: they touch different regions of the file, so git saw
no conflict. But Python does not have regions. The later definition simply won at
import time, and the injection scrubber inherited the wrong pattern.

The two were opposites. The scrubber wants to split AFTER sentence punctuation and
keep it; the name ledger wants to split ON the punctuation and consume it. With one
name, the scrubber consumed every full stop, so every DM turn it touched came back
as "The door swings open on a hall of dust  The lantern gutters" with the sentence
endings gone. The game's prose was quietly mangled, in a file nobody had edited in
weeks.

So: parse the module, and refuse a name that is bound twice at module level. That is
a cheap AST walk, it cannot rot, and it turns a silent runtime collision into a
loud test failure. It also covers the next collision, which nobody has written yet.
"""
from __future__ import annotations

import ast
import pathlib

PKG = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "localdm"


def _module_level_bindings(path: pathlib.Path) -> dict:
    """name -> line, for every plain module-level assignment and def/class."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound = {}
    for node in tree.body:
        names = []
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        for name in names:
            bound.setdefault(name, []).append(node.lineno)
    return bound


def test_reply_has_no_name_bound_twice_at_module_level():
    """The regression itself, named: two _SENTENCE definitions in one module."""
    duplicated = {n: lines for n, lines in _module_level_bindings(
        PKG / "reply.py").items() if len(lines) > 1}
    assert not duplicated, (
        f"reply.py binds these names more than once at module level: {duplicated}. "
        "The last definition wins at import time, so whichever function uses the "
        "other one silently gets the wrong object.")


def test_no_localdm_module_binds_a_name_twice_at_module_level():
    """Generalised, because the collision was not specific to reply.py and the
    next one will not be either."""
    offenders = {}
    for path in sorted(PKG.glob("*.py")):
        duplicated = {n: lines for n, lines in _module_level_bindings(path).items()
                      if len(lines) > 1}
        if duplicated:
            offenders[path.name] = duplicated
    assert not offenders, offenders
