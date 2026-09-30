"""reply.py: a module-level name defined twice is a silent behaviour change.

Python has no namespaces, so a second `NAME = ...` at module level quietly
replaces the first for every reader, including the ones defined above it. Here
it replaced `_SENTENCE`: the injection scrub splits a turn on the gap AFTER a
terminator and needs the terminator left in each piece, while NameLedger splits
on the terminator itself. Sharing one name between two regexes with the same
intent and different behaviour made the scrub drop sentence-final punctuation
from the prose the player was shown, and it reached main because the failure
looks like a content bug in a test about injection rather than a shadowed
binding.
"""
from __future__ import annotations

import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "scripts" / "localdm" / "reply.py"


def _module_level_bindings(path: pathlib.Path) -> dict[str, int]:
    """Every module-level name assigned in `path`, with the line it is bound on."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bound: dict[str, int] = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                # First binding wins for the report; a second one is the defect.
                bound.setdefault(target.id, node.lineno)
    return bound


def _rebound_names(path: pathlib.Path) -> dict[str, list[int]]:
    """Names bound more than once at module level: name -> every line."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    seen: dict[str, list[int]] = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                seen.setdefault(target.id, []).append(node.lineno)
    return {n: lines for n, lines in seen.items() if len(lines) > 1}


class TestModuleLevelNames(unittest.TestCase):
    def test_no_module_level_name_is_bound_twice(self):
        """The defect class, on the file where it actually happened."""
        rebound = _rebound_names(TARGET)
        self.assertEqual(
            rebound, {},
            f"{TARGET.name} rebinds a module-level name, so the later binding "
            f"silently replaces the earlier one for every reader: {rebound}")

    def test_the_two_sentence_splitters_have_distinct_names(self):
        """Named specifically, so a rename that reunites them is caught here.

        The scrub keeps the terminator in each piece; the name ledger wants to
        split on it. They are not interchangeable, and this assertion says so
        without depending on the reader of the source noticing.
        """
        names = _module_level_bindings(TARGET)
        self.assertIn("_SENTENCE", names, "the scrub's splitter should still exist")
        self.assertIn(
            "_BOUNDARY", names,
            "the name ledger's splitter should be _BOUNDARY, distinct from the "
            "scrub's _SENTENCE, which keeps the terminator in each piece")

    def test_the_scrub_keeps_punctuation_in_the_pieces_it_splits(self):
        """Behavioural, so the names above are not the only thing holding it up."""
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        from localdm import reply

        line = "The vault is quiet. A lamp gutters. Nobody moves."
        pieces = reply._SENTENCE.split(line)
        self.assertEqual(
            len(pieces), 3,
            f"expected three sentences, split on the gap after each terminator, "
            f"got {pieces!r}")
        for piece in pieces[:-1]:
            self.assertTrue(
                piece.endswith((".", "!", "?")),
                f"every piece but the last must keep its terminator, got {piece!r}")


if __name__ == "__main__":
    unittest.main()
