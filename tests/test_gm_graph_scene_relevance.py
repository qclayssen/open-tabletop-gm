"""#276 — the scene-context graph, wired into the `play.py` turn path.

`gm_graph.scene-context` already existed and was already documented as an
optimisation ("extract scene-relevant subgraphs at lower token cost than loading
the full npcs.md index at /gm load"). It was wired into `/gm load` and into
nothing in `localdm/`, so `play.py` narrated with the full `notes_digest` every
turn. These tests hold the wiring to its contract.

THE TWO CONTRACTS THAT MATTER
=============================
1. **No graph means no change.** A campaign without `graph.json` must behave
   exactly as today. Not "degrade gracefully" — behave identically, because a
   new failure mode in the hot turn path is the thing this change could most
   easily introduce and least easily notice.
2. **Relevance, never strict adjacency.** `world_queue.py` exists to hold
   off-screen pressure "waiting to surface", and `dm.md` requires danger be
   "signalled before it lands". Both depend on the DM knowing what is off-screen.
   A cull that ranks by distance alone throws exactly that away, so the rank has
   to survive a node that is named in the scene but far away in the graph.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import gm_graph  # noqa: E402

# A two-room campaign: the party is upstairs, the threat is in the cellar, and
# the cellar is one hop from where they are but NOT where the scene is.
CAMPAIGN = {
    "version": 1,
    "nodes": [
        {"id": "party", "type": "party", "name": "The Party"},
        {"id": "inn", "type": "place", "name": "The Gilded Inn",
         "summary": "common room, fire"},
        {"id": "cellar", "type": "place", "name": "Cellar", "summary": "crates"},
        {"id": "aldric", "type": "npc", "name": "Aldric",
         "summary": "innkeeper, owes the party 50 gp"},
        # Three hops from the inn. Named in the scene text below.
        {"id": "sable", "type": "npc", "name": "Sable",
         "summary": "smuggler, counting coin in the cellar"},
        # Far AND unmentioned. This is the node a distance cull is allowed to drop.
        {"id": "wrought", "type": "npc", "name": "The Wrought Court",
         "summary": "a distant power with no business in this scene"},
    ],
    "edges": [
        {"id": "e1", "from": "party", "to": "inn", "type": "located_at",
         "since_session": 1},
        {"id": "e2", "from": "inn", "to": "cellar", "type": "contains",
         "since_session": 1},
        {"id": "e3", "from": "party", "to": "aldric", "type": "disposition",
         "level": "friendly", "since_session": 1},
        {"id": "e4", "from": "sable", "to": "cellar", "type": "located_at",
         "since_session": 1},
    ],
}


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    """A campaign dir with a graph.json, resolved by name through `paths`."""
    root = tmp_path / "campaigns" / "tworoom"
    root.mkdir(parents=True)
    (root / "graph.json").write_text(json.dumps(CAMPAIGN), encoding="utf-8")
    (root / "state.md").write_text(
        "# Campaign: tworoom\n\n## Current Situation\n"
        "The party sits in the common room. Sable has not been seen since dusk.\n",
        encoding="utf-8")
    monkeypatch.setattr(gm_graph, "find_campaign",
                        lambda name: root, raising=False)
    monkeypatch.setattr(gm_graph, "_graph_path",
                        lambda campaign: root / "graph.json", raising=False)
    return root


def _names(selection) -> set:
    return {n["name"] for n in selection["nodes"]}


# ── contract 2: relevance, not adjacency ──────────────────────────────────

def test_an_off_screen_node_named_in_the_scene_survives_the_cull(campaign):
    """The failure this whole design turns on.

    `Sable` is one hop from the inn but is not present, and the party is upstairs.
    A distance-only cull drops her, and the DM then learns about the smuggler
    when she walks in -- which `dm.md` forbids: danger must be "signalled before
    it lands: a rumour, a cost someone else already paid."

    The scene text names her, so the lexical half has to carry her.
    """
    selection = gm_graph.scene_nodes(
        "tworoom", "The party sits in the common room. Sable has not been seen since dusk.",
        hops=2, present="The Gilded Inn")
    assert "Sable" in _names(selection), (
        "an off-screen NPC named in the scene was culled; that is the "
        "foreshadowing failure dm.md forbids")


def test_a_far_unmentioned_node_may_still_be_dropped(campaign):
    """The cull has to be able to drop something, or it is not a cull.

    `The Wrought Court` is far and unmentioned, so a ranked selection is entitled
    to exclude it. Pinned so the test above cannot be satisfied by a selection
    that simply keeps everything.
    """
    selection = gm_graph.scene_nodes(
        "tworoom", "The party sits in the common room. Sable has not been seen since dusk.",
        hops=1, present="The Gilded Inn", limit=4)
    assert "The Wrought Court" not in _names(selection), (
        "the selection kept everything; it is not ranking anything")


def test_a_named_node_outranks_a_merely_connected_one(campaign):
    """Rank, not membership. Aldric is adjacent AND in the seed; Sable is
    adjacent but unmentioned. Both survive here, but the ordering has to put the
    one the scene is about first, or `limit` will cut the wrong one."""
    selection = gm_graph.scene_nodes(
        "tworoom", "Sable has not been seen since dusk.",
        hops=2, present="The Gilded Inn,Aldric")
    order = [n["name"] for n in selection["nodes"]]
    assert order.index("Sable") < order.index("Aldric"), (
        f"the named node did not outrank the connected one: {order}")


def test_limit_is_respected(campaign):
    selection = gm_graph.scene_nodes(
        "tworoom", "Sable cellar Aldric party wrought", hops=2,
        present="The Gilded Inn", limit=2)
    assert len(selection["nodes"]) <= 2


# ── contract 1: no graph, no change ───────────────────────────────────────

def test_no_graph_reports_absent_rather_than_raising(tmp_path, monkeypatch):
    root = tmp_path / "campaigns" / "nograph"
    root.mkdir(parents=True)
    monkeypatch.setattr(gm_graph, "_graph_path", lambda c: root / "graph.json",
                        raising=False)
    selection = gm_graph.scene_nodes("nograph", "anything at all")
    assert selection["present"] is False
    assert selection["nodes"] == []


def test_an_empty_graph_reports_absent(tmp_path, monkeypatch):
    root = tmp_path / "campaigns" / "empty"
    root.mkdir(parents=True)
    (root / "graph.json").write_text(
        json.dumps({"version": 1, "nodes": [], "edges": []}), encoding="utf-8")
    monkeypatch.setattr(gm_graph, "_graph_path", lambda c: root / "graph.json",
                        raising=False)
    assert gm_graph.scene_nodes("empty", "x")["present"] is False


def test_a_corrupt_graph_does_not_raise(tmp_path, monkeypatch):
    """A truncated or hand-edited graph.json must not take the turn down.

    The caller falls back to the full digest, which is today's behaviour. This
    is the test that keeps the broad `except` in play.py honest: without a
    corrupt-graph case, that except looks like paranoia instead of a contract.
    """
    root = tmp_path / "campaigns" / "corrupt"
    root.mkdir(parents=True)
    (root / "graph.json").write_text('{"nodes": [{"id": "x"', encoding="utf-8")
    monkeypatch.setattr(gm_graph, "_graph_path", lambda c: root / "graph.json",
                        raising=False)
    selection = gm_graph.scene_nodes("corrupt", "a scene")
    assert selection["present"] is False


def test_an_unresolvable_present_seed_is_not_fatal(campaign):
    """A scene with no place node -- a carriage, a dream, a fight on a bridge --
    still gets the lexical half. An unresolved seed costs precision, not the
    feature."""
    selection = gm_graph.scene_nodes(
        "tworoom", "Sable has not been seen.", hops=2,
        present="Somewhere That Is Not A Node")
    assert selection["present"] is True
    assert "Sable" in _names(selection)


# ── the CLI must not change behaviour (render_subgraph refactor) ──────────

def test_render_subgraph_matches_what_the_cli_printed(campaign, capsys):
    """`_emit_subgraph` printed; `render_subgraph` returns. Splitting them is
    how `play.py` gets text without capturing stdout, and the CLI's bytes are
    the contract that makes that refactor safe."""
    import argparse
    args = argparse.Namespace(campaign="tworoom", place="The Gilded Inn",
                              present=None, threads=None, hops=2,
                              at_session=None)
    gm_graph.cmd_scene_context(args)
    printed = capsys.readouterr().out
    data = gm_graph._load("tworoom")
    direct = gm_graph.render_subgraph(
        gm_graph._expand(data,
                         [gm_graph._resolve_node(data, "The Gilded Inn")],
                         2, None),
        None)
    assert direct.strip() in printed, (
        "the CLI no longer prints what render_subgraph returns; the refactor "
        "changed user-visible output")


def test_scene_nodes_never_raises_on_any_input(campaign):
    """A property-ish guard over the shapes play.py can hand it."""
    for query in ("", "   ", None, "x" * 5000, "Ünïcödé — cellar", "()[]'\""):
        assert gm_graph.scene_nodes("tworoom", query, present="")["present"] is True