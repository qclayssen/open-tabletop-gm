"""`gm_graph.apply_proposals` and the session path that reaches it.

Three properties, each of which #289 names and each of which is easy to satisfy
by accident:

  * ONE writer. `apply_proposals` is the only thing that writes a graph, and a
    session reaches it through that -- never beside it.
  * THE GM'S WORDS WIN. A session-written summary never overwrites a summary the
    GM typed.
  * NO GRAPH, NO CHANGE. A campaign without a `graph.json` behaves exactly as it
    did before, and nothing here may create one.
"""
from __future__ import annotations

import json
import pathlib
import unittest

import gm_graph
from localdm import graph_writer
from localdm.play import Session
from tests.localdm_fakes import FakeBridge, FakeClient


def _summary(to="velkyn", text="Velkyn wears the harbour seal.", turn=3):
    return {"kind": "node_summary", "to": to, "summary": text,
            "confidence": "medium",
            "source": {"origin": "canon", "turn": turn, "anchor": text}}


class ApplyProposalsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        camp = self.root / "campaigns" / "demo"
        (camp / "notes").mkdir(parents=True)
        (camp / "state.md").write_text("**System:** D&D 5e\n", encoding="utf-8")
        self.camp = camp
        self._env = _campaign_root(self, self.root)

    def _graph(self) -> dict:
        return json.loads((self.camp / "graph.json").read_text(encoding="utf-8"))

    # ---- the writer ----

    def test_a_summary_proposal_creates_the_node_and_writes_the_summary(self):
        counts = gm_graph.apply_proposals("demo", [_summary()])
        self.assertEqual(counts["summaries"], 1)
        self.assertEqual(counts["nodes"], 1)
        node, = self._graph()["nodes"]
        self.assertEqual(node["summary"], "Velkyn wears the harbour seal.")

    def test_the_written_summary_is_marked_provisional_in_the_file(self):
        gm_graph.apply_proposals("demo", [_summary()])
        node, = self._graph()["nodes"]
        self.assertEqual(node["summary_source"]["origin"], "canon")
        self.assertEqual(node["summary_source"]["turn"], 3)

    def test_an_edge_proposal_still_applies_exactly_as_before(self):
        """The refactor moved this code; it must not have changed it."""
        counts = gm_graph.apply_proposals("demo", [
            {"from": "Aldric", "type": "loyal_to", "to": "Selia",
             "since_session": 2, "note": "owes a debt"},
        ])
        self.assertEqual(counts["edges"], 1)
        edge, = self._graph()["edges"]
        self.assertEqual((edge["from"], edge["to"], edge["type"]),
                         ("npc_aldric", "npc_selia", "loyal_to"))
        self.assertEqual(edge["since_session"], 2)
        self.assertEqual(edge["until_session"], None)

    # ---- the GM's words win ----

    def test_a_gm_written_summary_is_never_overwritten_by_a_session(self):
        gm_graph.apply_proposals("demo", [_summary(text="Velkyn wears the seal.")])
        # The GM edits it by hand -- as a GM would, by writing the file.
        data = self._graph()
        data["nodes"][0]["summary"] = "The harbourmaster who owes Velkyn money."
        del data["nodes"][0]["summary_source"]        # no longer a session's text
        (self.camp / "graph.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8")

        counts = gm_graph.apply_proposals("demo", [
            _summary(text="Velkyn wears the harbour seal.")])
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["summaries"], 0)
        node, = self._graph()["nodes"]
        self.assertEqual(node["summary"], "The harbourmaster who owes Velkyn money.",
                         "a session rewrote a summary the GM had written")

    def test_a_session_may_revise_its_own_earlier_summary(self):
        """Otherwise the first proposal would freeze the node forever.

        A second session learning more about the same entity is the normal case,
        not an attack -- so `summary_source` is what distinguishes the GM's words
        from this module's.
        """
        gm_graph.apply_proposals("demo", [_summary(text="Velkyn wears the seal.")])
        counts = gm_graph.apply_proposals("demo", [
            _summary(text="Velkyn wears the harbour seal, forged last winter.")])
        self.assertEqual(counts["summaries"], 1)
        node, = self._graph()["nodes"]
        self.assertEqual(node["summary"],
                         "Velkyn wears the harbour seal, forged last winter.")
        self.assertEqual(len(self._graph()["nodes"]), 1, "a second node appeared")

    def test_no_auto_nodes_refuses_rather_than_inventing_an_entity(self):
        gm_graph.apply_proposals("demo", [_summary()], no_auto_nodes=False)
        (self.camp / "graph.json").unlink()
        counts = gm_graph.apply_proposals("demo", [_summary(to="brand_new")],
                                          no_auto_nodes=True)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["summaries"], 0)
        self.assertFalse((self.camp / "graph.json").exists(),
                         "a refused proposal still wrote a graph file")

    # ---- the review gate ----

    def test_a_decline_writes_nothing_and_leaves_the_graph_absent(self):
        counts = gm_graph.apply_proposals("demo", [_summary()],
                                          decide=lambda i, t, p: "n")
        self.assertEqual(counts["declined"], 1)
        self.assertFalse((self.camp / "graph.json").exists())

    def test_quit_declines_the_rest_too(self):
        counts = gm_graph.apply_proposals(
            "demo", [_summary(to="a"), _summary(to="b"), _summary(to="c")],
            decide=lambda i, t, p: "q")
        self.assertEqual(counts["declined"], 3)
        self.assertEqual(counts["summaries"], 0)

    def test_the_writer_is_the_one_the_cli_uses(self):
        """One writer, asserted structurally.

        `cmd_extract_apply` must delegate rather than keep its own copy of the
        logic -- two copies is exactly the "second writer" #289 forbids, and it
        is invisible to every behavioural test above because both would work.
        """
        src = pathlib.Path(gm_graph.__file__).read_text(encoding="utf-8")
        body = src[src.index("def cmd_extract_apply"):]
        self.assertIn("apply_proposals(", body)
        self.assertNotIn("_save(", body,
                         "cmd_extract_apply writes graph.json itself again")


class SessionProposesTests(unittest.TestCase):
    """A session proposes; it never writes. And it proposes nothing into a
    campaign that has no graph, which is the T2.5 contract verbatim."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.camp = self.root / "campaigns" / "demo"
        (self.camp / "localdm").mkdir(parents=True)
        (self.camp / "state.md").write_text("**System:** D&D 5e\n", encoding="utf-8")
        self._env = _campaign_root(self, self.root)

    def _session(self) -> Session:
        from localdm import llm
        # No responder: nothing in these tests runs a model turn. The graph
        # proposal path is reached directly, so the client is never called and
        # asserting on it would only pin a fake.
        return Session("demo", FakeClient(lambda *a, **k: None),
                       llm.Models("dm-local", "a", "c"),
                       camp_dir=self.camp,
                       bridge=FakeBridge(), status=False)

    def _canon(self, s: Session, records):
        s.canon.records = lambda: records          # the extractor has not run here

    def test_a_campaign_with_no_graph_gets_no_proposals_and_no_file(self):
        s = self._session()
        self.assertFalse(s.has_graph())
        self._canon(s, [{"kind": "reveal", "key": "velkyn", "turn": 1,
                         "text": "Velkyn wears the harbour seal."}])
        self.assertEqual(s.propose_graph_updates(), [])
        self.assertFalse(s._proposals_path().exists(),
                         "proposing must not create a proposals file for a "
                         "campaign that has no graph")

    def test_a_campaign_with_a_graph_gets_proposals_but_no_written_summary(self):
        (self.camp / "graph.json").write_text(
            json.dumps({"version": 1, "nodes": [], "edges": []}), encoding="utf-8")
        s = self._session()
        self._canon(s, [{"kind": "reveal", "key": "velkyn", "turn": 1,
                         "text": "Velkyn wears the harbour seal."}])
        out = s.propose_graph_updates()
        self.assertEqual(len(out), 1)
        self.assertTrue(s._proposals_path().exists())
        # Still untouched: the session wrote proposals, not the graph.
        data = json.loads((self.camp / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(data["nodes"], [],
                         "the session wrote graph.json before the GM reviewed")

    def test_graph_apply_is_reachable_only_through_the_writer(self):
        s = self._session()
        src = pathlib.Path(type(s)._graph_cmd.__code__.co_filename).read_text(
            encoding="utf-8")
        body = src[src.index("def _graph_cmd"):]
        self.assertIn("gm_graph.apply_proposals(", body)
        self.assertNotIn("gm_graph._save(", body)
        self.assertNotIn('open(', body[body.index('"apply"'):],
                         "/graph writes the graph file itself")

    def test_an_absent_graph_is_reported_rather_than_silently_doing_nothing(self):
        s = self._session()
        out = s._graph_cmd("")
        self.assertEqual(len(out), 1)
        self.assertIn("No graph.json", out[0])


def _campaign_root(case: unittest.TestCase, root: pathlib.Path):
    """Point `paths` at `root` for the duration of one test."""
    import os
    old = os.environ.get("GM_CAMPAIGN_ROOT")
    os.environ["GM_CAMPAIGN_ROOT"] = str(root)

    def restore():
        if old is None:
            os.environ.pop("GM_CAMPAIGN_ROOT", None)
        else:
            os.environ["GM_CAMPAIGN_ROOT"] = old

    case.addCleanup(restore)
    return old


if __name__ == "__main__":                        # pragma: no cover
    unittest.main()