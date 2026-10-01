"""The optional-dependency split: what the display needs, and what it survives without.

`display/requirements.txt` used to be the only manifest, with numpy sitting in it
commented out and a note explaining why. That is a fine note and a bad manifest:
a commented-out requirement is not a requirement, so the audio path had no
installable name anywhere, and a reader could not tell from the file which
packages were load-bearing. The heavy optional pair now lives in
`display/requirements-audio.txt`, where it is a real requirement you can install,
and the base file is the always-needed set.

Two invariants are pinned here, and both are the ones that rot silently.

The first is about the files. A heavy optional package must not reappear in the
base manifest, live or commented out, because the moment it is live every
install pays for audio synthesis and portrait cropping whether or not the GM
wants either. A *commented* line counts, and not as a formality: the file this
replaces carried exactly that, and a comment is one keystroke from being live.

The second is about the code, and it is the one that matters more, because a
package in the wrong file is a packaging nit while an unguarded import is a
display that will not start on the machine it was written on. Every import of a
listed optional package must be inside a `try` that catches ImportError, or
inside a function body, which defers it until the feature is actually used.
A bare module-level import of any of them is a failure, whether or not the
machine running the suite happens to have it installed. The third test then
proves the claim rather than asserting the shape: with numpy blocked for real,
the display app still imports and the SFX route answers 404, which is what
"degrades rather than crashes" means.

`pygame` is asserted absent rather than optional. The 2026-09-30 webdev audit
opened this item with "requirements.txt lists pygame and numpy for audio", and
pygame was removed in ff59a75: it is imported nowhere, so there is no optional
path for it to guard. Reading the audit is what this file is for; believing it
without checking would have added a dependency to a repo whose hard rule is
that it ships as a Skill.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re
import sys
import unittest
from pathlib import Path

REPO = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = REPO / "display"
BASE = DISPLAY / "requirements.txt"
OPTIONAL = DISPLAY / "requirements-audio.txt"

#: The always-needed set. One package, and asserting the exact list is the point:
#: a check that only looked for known-bad names would not notice a fourth.
BASE_REQUIREMENTS = ["flask"]

#: Optional heavy packages, keyed by the lowercase name a requirements file
#: would use, with the top-level module each one is imported as. The import name
#: differs from the distribution name for Pillow, which is the case a naive
#: grep-for-the-package-name check gets wrong.
OPTIONAL_PACKAGES = {
    "numpy": "numpy",
    "pillow": "PIL",
    "pygame": "pygame",
}

#: A complete requirement line, which is what a disabled requirement looks like.
#: Anchored at both ends and whitespace-free after the version, so `# numpy` and
#: `# numpy>=2` are read as requirements while `# numpy is only needed for audio`
#: is read as prose. Getting this the wrong way round is the whole failure mode:
#: a parser loose enough to accept prose cannot tell a commented-out requirement
#: from a comment that happens to mention one, which is the thing being pinned.
#: Environment markers are deliberately not modelled. Nothing in this repo pins
#: one, and `; <anything>` is indistinguishable from the second half of a
#: sentence, which is what made an earlier draft of this read a prose comment as
#: a requirement.
_SPEC = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)"          # name
    r"(\[[^\]]*\])?"                         # optional extras
    r"\s*(==|>=|<=|~=|!=|>|<)?"              # optional operator
    r"\s*([0-9][A-Za-z0-9.*+!-]*)?"          # optional version
)

_IMPORT_ERRORS = ("ImportError", "ModuleNotFoundError")


def _lines(path: pathlib.Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _requirement_names(text: str) -> list[str]:
    """Every name pip would install from `text`, commented-out ones included.

    A live line is one that is not a comment. A commented-out line is a comment
    whose whole body is a requirement spec, which is what `# numpy` is and what
    `# numpy is only needed for audio` is not.
    """
    names = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        elif "#" in line:
            line = line.split("#", 1)[0].strip()
        match = _SPEC.fullmatch(line)
        if match:
            names.append(match.group(1).lower())
    return names


def _python_files() -> list[pathlib.Path]:
    return [p for p in sorted(REPO.rglob("*.py"))
            if not ({"node_modules", ".git", "__pycache__", ".venv"} & set(p.parts))]


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    exc = handler.type
    if exc is None:                                      # bare except:
        return True
    named = exc.id if isinstance(exc, ast.Name) else getattr(exc, "attr", None)
    if named in _IMPORT_ERRORS or named == "Exception":
        return True
    return any(isinstance(e, ast.Name) and e.id in _IMPORT_ERRORS
               for e in ast.walk(exc))


class _ImportFinder(ast.NodeVisitor):
    """Every import of `module`, recording whether it is guarded or deferred.

    Guarded means lexically inside a `try` whose handlers catch ImportError, so
    a missing module is absorbed. Deferred means inside a function body, so
    importing this file cannot fail; whether the call then degrades is a
    separate question that the display test below answers for real.
    """

    def __init__(self, module: str) -> None:
        self.module = module
        self.sites: list[tuple[int, bool, bool]] = []     # lineno, guarded, deferred
        self._guard = 0
        self._func = 0

    def _record(self, node: ast.AST) -> None:
        self.sites.append((node.lineno, self._guard > 0, self._func > 0))

    def _matches(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Import):
            return any(a.name.split(".")[0] == self.module for a in node.names)
        return isinstance(node, ast.ImportFrom) and \
            (node.module or "").split(".")[0] == self.module

    def visit_Import(self, node: ast.Import) -> None:
        if self._matches(node):
            self._record(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._matches(node):
            self._record(node)

    def visit_Try(self, node: ast.Try) -> None:
        guarded = any(_catches_import_error(h) for h in node.handlers)
        for stmt in node.body:
            if guarded:
                self._guard += 1
                self.visit(stmt)
                self._guard -= 1
            else:
                self.visit(stmt)
        # orelse, finalbody and the handlers themselves are not covered by the
        # try, so an ImportError raised there still propagates.
        for group in (node.orelse, node.finalbody):
            for stmt in group:
                self.visit(stmt)
        for handler in node.handlers:
            for stmt in handler.body:
                self.visit(stmt)

    def _visit_function(self, node) -> None:
        for deco in node.decorator_list:
            self.visit(deco)
        for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
            self.visit(default)
        self._func += 1
        for stmt in node.body:
            self.visit(stmt)
        self._func -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


class _NoModule:
    """Meta path finder that makes `import <name>` fail, the way a bare machine sees it.

    Raises from find_spec rather than returning None, so a cached or partially
    imported package cannot sneak past and make these tests vacuous. A legacy
    find_module/load_module blocker is ignored on modern Python, which would
    leave the guard inert while everything it guards passed.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.hits = 0

    def find_spec(self, name, path=None, target=None):
        if name == self.name or name.startswith(self.name + "."):
            self.hits += 1
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


def _without_module(name: str, fn, also_evict: tuple[str, ...] = ()):
    """Run `fn` with `name` unimportable, then put the process back as it was.

    `also_evict` names modules that import `name` and would otherwise be served
    from sys.modules. Evicting the target alone is not enough: `gm-display-app.py`
    does `import audio as _audio`, so if a sibling test in the same xdist worker
    already imported `audio`, the app gets that cached, numpy-enabled module and
    reports itself available no matter what the blocker says. The bug this
    guards against is the reason this helper restores state at all, and it showed
    up first as two tests that passed serially and failed under -n 4, which is
    the least legible way a test can be wrong.
    """
    blocker = _NoModule(name)
    stale = (name,) + tuple(also_evict)
    saved = {k: v for k, v in sys.modules.items()
             if any(k == s or k.startswith(s + ".") for s in stale)}
    for k in saved:
        del sys.modules[k]
    sys.meta_path.insert(0, blocker)
    try:
        return fn()
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(saved)


class BaseManifest(unittest.TestCase):
    def test_the_base_manifest_is_exactly_the_always_needed_set(self):
        self.assertEqual(_requirement_names(BASE.read_text(encoding="utf-8")),
                         BASE_REQUIREMENTS)

    def test_no_heavy_optional_package_is_in_the_base_manifest_at_all(self):
        """Live or commented. The commented case is the one that bit."""
        found = sorted(set(_requirement_names(BASE.read_text(encoding="utf-8")))
                       & set(OPTIONAL_PACKAGES))
        self.assertEqual(
            found, [],
            f"{found} in {BASE.name}: a heavy optional listed there is installed by "
            f"everyone, whether or not they want the feature. It belongs in "
            f"{OPTIONAL.name}.")

    def test_the_base_manifest_points_at_the_optional_one(self):
        """Otherwise the base file no longer says where the optional packages went."""
        self.assertIn(OPTIONAL.name, BASE.read_text(encoding="utf-8"))


class OptionalManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.names = _requirement_names(OPTIONAL.read_text(encoding="utf-8"))

    def test_it_lists_the_optional_packages(self):
        self.assertEqual(sorted(self.names), ["numpy", "pillow"])

    def test_it_explains_what_each_one_buys(self):
        """A requirements file that only names a package is a file nobody can act on.

        The comment has to carry the package name and say what losing it costs,
        because the whole point of the file is that a reader is choosing.
        """
        text = OPTIONAL.read_text(encoding="utf-8")
        for name in self.names:
            self.assertRegex(text, rf"(?im)^#.*{re.escape(name)}.*$")
        self.assertIn("without", text.lower())

    def test_pygame_is_not_an_optional_package(self):
        """It is imported nowhere, so there is no feature to lose and nothing to guard."""
        self.assertNotIn("pygame", self.names)


class EveryOptionalImportIsGuarded(unittest.TestCase):
    def test_no_module_level_unguarded_import_of_an_optional_package(self):
        unchecked = []
        for path in _python_files():
            if path.name == Path(__file__).name:
                continue                        # this file names them to check them
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for pkg, module in OPTIONAL_PACKAGES.items():
                finder = _ImportFinder(module)
                finder.visit(tree)
                for lineno, guarded, deferred in finder.sites:
                    if not guarded and not deferred:
                        rel = path.relative_to(REPO)
                        unchecked.append(f"{rel}:{lineno} {module} (for {pkg})")
        self.assertEqual(
            unchecked, [],
            "these imports are at module level with no try/except, so a machine "
            "without the package cannot even import the file: " + "; ".join(unchecked))

    def test_every_listed_package_is_imported_somewhere(self):
        """A package nothing imports is a package that should be deleted, not shipped."""
        all_sites = {}
        for path in _python_files():
            if path.name == pathlib.Path(__file__).name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module in OPTIONAL_PACKAGES.values():
                finder = _ImportFinder(module)
                finder.visit(tree)
                for site in finder.sites:
                    all_sites.setdefault(module, []).append(f"{path}:{site[0]}")
        for name in _requirement_names(OPTIONAL.read_text(encoding="utf-8")):
            module = OPTIONAL_PACKAGES[name]
            self.assertTrue(all_sites.get(module),
                            f"{name} is in {OPTIONAL.name} but nothing imports {module}")


class TheDisplayDegradesWithoutThem(unittest.TestCase):
    """The claim the packaging exists to make, checked by doing it rather than by shape."""

    @classmethod
    def setUpClass(cls) -> None:
        def load():
            spec = importlib.util.spec_from_file_location(
                "gm_display_app_without_numpy",
                str(DISPLAY / "gm-display-app.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        # `audio` goes too: it is the module that imports numpy, and a cached
        # copy from an earlier test in this worker would defeat the blocker.
        cls.mod = _without_module("numpy", load, also_evict=("audio",))
        cls.client = cls.mod.app.test_client()

    def test_the_blocker_actually_blocked(self):
        """Otherwise everything below is measuring a machine that had numpy."""
        def go():
            import numpy  # noqa: F401
        with self.assertRaises(ModuleNotFoundError):
            _without_module("numpy", go)

    def test_the_app_really_loaded_a_numpy_free_audio_module(self):
        """The failure mode `_without_module` exists to prevent, asserted directly.

        A cached `audio` from a sibling test would leave `_HAS_NUMPY` true and
        make the two tests below pass for the wrong reason, or fail for one.
        """
        self.assertFalse(self.mod._audio.get_state()["available"],
                         "the app imported a cached audio module that had numpy")

    def test_the_app_still_imports_and_serves_the_page(self):
        self.assertEqual(self.client.get("/ping").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_the_sfx_route_answers_404_rather_than_raising(self):
        self.assertEqual(self.client.get("/audio/sfx/sword").status_code, 404)

    def test_audio_reports_itself_unavailable(self):
        self.assertFalse(self.mod._audio.init())
        self.assertFalse(self.mod._audio.get_state()["available"])


class PortraitCroppingRefusesWithoutNumpy(unittest.TestCase):
    """The one optional import in the tree that was genuinely unguarded.

    `trim_panels` imported numpy bare in the function body, so the module still
    imported and the failure arrived later, from the middle of a crop loop, as a
    ModuleNotFoundError. It cannot fall back to the untrimmed box, because
    trimming is what stops a page of lore becoming a portrait, so the options
    were a traceback or a wrong crop. It refuses, which is what main() already
    does with every other Refused.
    """

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(REPO / "scripts"))
        import faculty_sheets
        cls.fs = faculty_sheets

    def test_a_missing_numpy_is_a_refusal_naming_the_fix(self):
        def go():
            return self.fs.trim_panels(object(), (0, 0, 10, 10))
        with self.assertRaises(self.fs.Refused) as caught:
            _without_module("numpy", go)
        self.assertIn("numpy", str(caught.exception))
        self.assertIn(OPTIONAL.name, str(caught.exception))

    def test_it_does_not_silently_return_the_untrimmed_box(self):
        """The wrong crop is the failure this script exists to refuse, so it is
        worth stating: a bare fallback to `box` would pass every other test here
        and ship a page of prose as a professor."""
        try:
            _without_module("numpy", lambda: self.fs.trim_panels(object(), (1, 2, 3, 4)))
        except self.fs.Refused:
            return
        raise AssertionError("trim_panels returned a box without numpy, which is "
                             "the untrimmed crop this script refuses to guess")


if __name__ == "__main__":
    unittest.main()
