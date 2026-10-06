"""`graph_writer` -- what a session may propose, and the two properties #289
called load-bearing: the review gate, and never overwriting the GM.

Every test here is written to fail if the behaviour it names is removed, and the
mutants were actually applied rather than assumed -- see the commit message.
"""
from __future__ import annotations

import ast
import json
import pathlib
import unittest

from localdm import graph_writer


def _reveal(key="velkyn", text="Velkyn wears the harbour seal.", turn=4):
    return {"kind": "reveal", "key": key, "speaker": "narrator",
            "text": text, "turn": turn}


class SummarizeTests(unittest.TestCase):
    def test_the_first_sentence_is_the_summary(self):
        self.assertEqual(
            graph_writer.summarize("The seal is real. Nobody has said so."),
            "The seal is real.")

    def test_a_long_sentence_is_bounded_and_says_that_it_was_cut(self):
        long = "word " * 200
        out = graph_writer.summarize(long, limit=40)
        self.assertLessEqual(len(out), 43)          # 40 + the ellipsis
        self.assertTrue(out.endswith("..."),
                        "a truncated summary that looks complete is the bug")

    def test_newlines_do_not_leak_into_a_one_sentence_summary(self):
        self.assertEqual(
            graph_writer.summarize("The seal\n\nis real.\nAnd it stings."),
            "The seal is real.")

    def test_empty_text_summarises_to_nothing_rather_than_raising(self):
        self.assertEqual(graph_writer.summarize(""), "")
        self.assertEqual(graph_writer.summarize(None), "")


class ProposalTests(unittest.TestCase):
    def test_a_reveal_becomes_a_node_summary_proposal(self):
        [p] = graph_writer.proposals_from_canon([_reveal()])
        self.assertEqual(p["kind"], "node_summary")
        self.assertEqual(p["to"], "velkyn")
        self.assertEqual(p["summary"], "Velkyn wears the harbour seal.")

    def test_the_proposal_carries_the_verbatim_anchor_not_only_the_excerpt(self):
        """The summary is bounded and may be truncated; the anchor is not.

        So the GM reviewing a proposal is shown the sentence it came from. If the
        anchor were dropped the review would be approving an excerpt with no way
        to see what was cut.
        """
        # ONE long sentence, deliberately: joining four sentences would stop the
        # summary at the first full stop and never reach the limit, so the test
        # would pass without ever exercising truncation.
        long = ("Velkyn wore the harbour seal and nobody in the tavern spoke of "
                "it and the harbourmaster called it a forgery and " * 4).strip()
        [p] = graph_writer.proposals_from_canon([_reveal(text=long)])
        self.assertGreater(len(long), graph_writer.MAX_SUMMARY_CHARS)
        self.assertTrue(p["summary"].endswith("..."),
                        f"expected a truncated summary, got {p['summary'][-40:]!r}")
        self.assertEqual(p["source"]["anchor"], long.strip())
        self.assertNotIn("...", p["source"]["anchor"])

    def test_a_second_reveal_of_the_same_key_does_not_overwrite_the_first(self):
        """The introduction is what a graph is for.

        A later restatement is normally longer and would silently replace the
        sentence that introduced the node.
        """
        out = graph_writer.proposals_from_canon([
            _reveal(text="Velkyn wears the harbour seal.", turn=1),
            _reveal(text="Velkyn wears the harbour seal, and it has blood on it.",
                    turn=9),
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["summary"], "Velkyn wears the harbour seal.")
        self.assertEqual(out[0]["source"]["turn"], 1)

    def test_only_reveals_are_proposed(self):
        """`dialogue`/`interaction` are facts about a scene, `death` is engine state.

        Neither names something a graph node can hold, so proposing them would
        invent entities.
        """
        out = graph_writer.proposals_from_canon([
            {"kind": "dialogue", "speaker": "velkyn", "text": "I have seen this seal before.", "turn": 2},
            {"kind": "interaction", "speaker": "", "text": "The lantern gutters as the door swings shut.", "turn": 3},
            {"kind": "death", "speaker": "goblin", "text": "The goblin lies still in the mud.", "turn": 4, "key": "goblin", "dead": True},
        ])
        self.assertEqual(out, [])

    def test_a_too_short_reveal_is_not_proposed_at_all(self):
        """A node with an empty summary renders as a bare name in the DM's digest,
        which reads as the graph asserting this entity has nothing to say."""
        self.assertEqual(graph_writer.proposals_from_canon([_reveal(text="Yes.")]), [])

    def test_no_records_and_no_reveals_both_propose_nothing(self):
        self.assertEqual(graph_writer.proposals_from_canon([]), [])
        self.assertEqual(graph_writer.proposals_from_canon([{"kind": "reveal"}]), [])


class TheReviewGateIsStructuralTests(unittest.TestCase):
    """#289: no auto-write without the GM seeing it first.

    Asserted against the module's surface rather than by running a session, so
    it holds even if a future caller reaches `apply_proposals` some other way:
    this module has no writer and no graph path in it at all.
    """

    def test_this_module_cannot_write_a_graph(self):
        """Executable code only.

        The docstring names `graph.json` and `apply_proposals` throughout --
        precisely because it explains why this module must never touch either --
        so a substring scan over the whole file would fail on the explanation and
        pass on a real `open()`. Strips comments and docstrings via `ast` and
        scans what can actually run, which is also the stronger claim: a writer
        hidden inside a string or comment does not write anything.
        """
        import ast

        src = pathlib.Path(graph_writer.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines()
            if not line.lstrip().startswith("#"))
        tree = ast.parse(code)
        # Drop every docstring, at module level and in every def/class.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body.pop(0)

        executable = ast.unparse(tree)
        for forbidden in ("graph.json", "apply_proposals", "gm_graph",
                          "open(", "_save", "atomic_write"):
            self.assertNotIn(
                forbidden, executable,
                f"graph_writer's executable code must not contain "
                f"{forbidden!r}: it proposes, and gm_graph.apply_proposals is "
                f"the only writer")

    def test_and_it_imports_no_writer_module(self):
        src = pathlib.Path(graph_writer.__file__).read_text(encoding="utf-8")
        imports = [n for n in ast.walk(ast.parse(src))
                   if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = set()
        for node in imports:
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            else:
                names.add((node.module or "").split(".")[0])
        self.assertNotIn("gm_graph", names,
                         "importing gm_graph here would put a graph writer one "
                         "call away from a background thread")

    def test_a_proposal_carries_no_session_number_to_pretend_with(self):
        """`since_session` is inert on this read path.

        `play.py` never passes `at_session`, so a proposal claiming to be
        time-scoped would be decoration that reads as provenance. `turn` is the
        transcript index and is genuinely known, so it is kept -- as a `source`
        field, not as a graph field.
        """
        out = graph_writer.proposals_from_canon([_reveal(turn=7)])
        self.assertNotIn("since_session", out[0])
        self.assertNotIn("at_session", json.dumps(out[0]))
        self.assertEqual(out[0]["source"]["turn"], 7)


if __name__ == "__main__":                        # pragma: no cover
    unittest.main()