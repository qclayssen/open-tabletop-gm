"""
gm_graph.py — typed-edge relationship graph over campaign nodes.

Manual + query-only graph for open-tabletop-gm. Subcommands cover initialization,
edge maintenance, and scene-relevant subgraph queries. Auto-pulled at /gm load
(scene-context) and swept at /gm save (relationship-shift extraction).

LLM-agnostic: no Haiku or other model dependency for any subcommand. The upstream
claude-dnd-skill version ships an `extract` / `extract-apply` pair that runs a
Haiku pass over session-log to propose edges with source-anchor provenance; that
path is intentionally omitted here. When the deterministic Phase 2 verb-table
extractor is built (see upstream `docs/research/graph/phase-2-3-plan.md`) it will
land in this file as a fully local replacement.

Supplements markdown — npcs-full.md and session-log.md remain authoritative.
The graph is an *index over* canonical sources used to extract scene-relevant
subgraphs at lower token cost than loading the full npcs.md index at /gm load.

Storage: <campaign-dir>/graph.json
  {
    "version": 1,
    "nodes": [
      {"id": "npc_velkyn", "type": "npc", "name": "Velkyn", "tags": [...], "summary": "..."}
    ],
    "edges": [
      {"id": "e1", "from": "<id>", "to": "<id>", "type": "loyal_to",
       "since_session": 1, "until_session": null, "note": "..."}
    ]
  }

Node types (open vocab, suggested): npc, faction, place, item, thread.
Edge types (open vocab, common): loyal_to, opposes, allied_with, member_of,
  lives_in, controls, knows_about, friends_with, lover_of, owes, rules,
  related_by_blood, advances_thread, blocks_thread.

Edges are time-stamped. An edge is "active at session N" iff:
  since_session <= N AND (until_session is null OR until_session > N).
Use close-edge to set until_session when a relationship ends.

Usage:
  python3 gm_graph.py <subcommand> --campaign <name> [args]

Subcommands:
  add-node       --type T --name N [--id ID] [--tags t1,t2] [--summary S]
  add-edge       --from FROM --to TO --type T [--since N] [--until N] [--note S]
  set-disposition --to NPC_OR_FACTION --level L [--since N] [--note S]
                 (L = allied|friendly|neutral|suspicious|hostile; party stance)
  close-edge     --id EDGE_ID [--at-session N]
  list           [--type T] [--at-session N]
  show           --id ID
  scene-context  --place ID [--present ID,ID] [--threads ID,ID] [--hops H]
                 [--at-session N]
  subgraph       --seed ID [--seed ID ...] [--hops H] [--at-session N]
"""
import argparse
import json
import pathlib
import sys
import threading
from typing import Optional

from paths import find_campaign
from safeio import atomic_write_json


# -------- IO --------

#: Serializes every read-modify-write of `graph.json` inside this process.
#:
#: Needed from #289 on. `graph.json` is written by `_load` -> mutate -> `_save`
#: cycles, so two writers that interleave lose one another's work. Until now
#: every writer was a CLI verb that runs alone, so the hazard did not exist; a
#: play session proposes updates from a background thread while the GM may also
#: run `/graph`, which is exactly the interleaving this rules out.
#:
#: In-process only, and deliberately: a cross-process file lock would be the
#: right tool for two concurrent GMs, and no code path in this repo does that.
#: The other half of the problem -- a process killed mid-write leaving a
#: truncated file -- is `atomic_write_json` below, not the lock.
_GRAPH_WRITE_LOCK = threading.RLock()


def _graph_path(campaign: str):
    return find_campaign(campaign) / "graph.json"


def _load(campaign: str) -> dict:
    p = _graph_path(campaign)
    if not p.exists():
        return {"version": 1, "nodes": [], "edges": []}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("version", 1)
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data


def _save(campaign: str, data: dict) -> None:
    """Write `graph.json` atomically, keeping a `.bak`.

    Was `open(p, "w")` -- a truncating write, the only one in `scripts/` that
    was. A process killed between the truncate and the `json.dump` left a
    half-written file, and `_load` would then raise `ValueError` on it for the
    rest of the campaign's life. `_load`'s own contract already says a broken
    graph must not take a turn down, but a graph this function corrupted was
    not that.

    `ensure_ascii=False` and `indent=2` are kept verbatim from the old
    `json.dump` so the bytes on disk are unchanged apart from being atomic.
    """
    p = _graph_path(campaign)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(p, data, ensure_ascii=False)


# -------- disposition / standing vocabulary --------

# Normalized 5-point scale for how the PARTY stands toward an NPC or faction.
# Mirrors the display's faction `standing` values so the graph and the sidebar
# speak the same language. Ordered best → worst.
DISPOSITION_LEVELS = ["allied", "friendly", "neutral", "suspicious", "hostile"]

# The party's own node — the one endpoint every disposition/standing edge shares.
PARTY_NODE_ID = "party"

# Edge type per target: the party's stance toward an NPC is a `disposition`;
# toward a faction it is a `standing`. Both carry a `level` from the scale above.
_DISPOSITION_EDGE_TYPES = ("disposition", "standing")


# -------- helpers --------

def _slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower()).strip("_")


def _next_edge_id(edges: list) -> str:
    n = 1
    existing = {e["id"] for e in edges if e.get("id", "").startswith("e")}
    while f"e{n}" in existing:
        n += 1
    return f"e{n}"


def _node_by_id(data: dict, node_id: str) -> Optional[dict]:
    for n in data["nodes"]:
        if n["id"] == node_id:
            return n
    return None


def _resolve_node(data: dict, ref: str) -> Optional[str]:
    """Resolve a user-supplied ref to a node id. Tries exact id, then
    case-insensitive name, then name prefix. Raises ValueError on ambiguity."""
    if not ref:
        return None
    if _node_by_id(data, ref):
        return ref
    ref_low = ref.lower()
    exact = [n for n in data["nodes"] if n.get("name", "").lower() == ref_low]
    if len(exact) == 1:
        return exact[0]["id"]
    if len(exact) > 1:
        ids = ", ".join(n["id"] for n in exact)
        raise ValueError(f"name '{ref}' matches multiple nodes: {ids}. use id directly.")
    prefix = [n for n in data["nodes"] if n.get("name", "").lower().startswith(ref_low)]
    if len(prefix) == 1:
        return prefix[0]["id"]
    if len(prefix) > 1:
        ids = ", ".join(n["id"] for n in prefix)
        raise ValueError(f"name prefix '{ref}' matches multiple nodes: {ids}. use id directly.")
    return None


def _resolve_or_die(data: dict, ref: str, label: str) -> str:
    try:
        node_id = _resolve_node(data, ref)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    if node_id is None:
        print(f"error: {label} '{ref}' not found (no id or name match).", file=sys.stderr)
        sys.exit(1)
    return node_id


def _resolve_csv(data: dict, csv: str, label: str) -> list:
    if not csv:
        return []
    return [_resolve_or_die(data, ref.strip(), label) for ref in csv.split(",") if ref.strip()]


def _edge_by_id(data: dict, edge_id: str) -> Optional[dict]:
    for e in data["edges"]:
        if e.get("id") == edge_id:
            return e
    return None


def _edge_active_at(edge: dict, session: Optional[int]) -> bool:
    """Is the edge active at the given session?

    Returns False if the edge was superseded (hard retcon) — superseded edges
    stay in the graph for audit trail but never surface as 'active' state.
    """
    if edge.get("superseded_by"):
        return False
    if session is None:
        return edge.get("until_session") is None
    since = edge.get("since_session", 0) or 0
    until = edge.get("until_session")
    if since > session:
        return False
    if until is not None and until <= session:
        return False
    return True


# -------- subcommands --------

def cmd_add_node(args) -> int:
    data = _load(args.campaign)
    node_id = args.id or f"{args.type}_{_slug(args.name)}"
    if _node_by_id(data, node_id):
        print(f"error: node id '{node_id}' already exists. use --id to override.",
              file=sys.stderr)
        return 1
    node = {
        "id": node_id,
        "type": args.type,
        "name": args.name,
    }
    if args.tags:
        node["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    if args.summary:
        node["summary"] = args.summary
    data["nodes"].append(node)
    _save(args.campaign, data)
    print(f"added node {node_id}  ({args.type}: {args.name})")
    return 0


def cmd_add_edge(args) -> int:
    data = _load(args.campaign)
    from_id = _resolve_or_die(data, args.from_id, "source")
    to_id = _resolve_or_die(data, args.to_id, "target")
    edge = {
        "id": _next_edge_id(data["edges"]),
        "from": from_id,
        "to": to_id,
        "type": args.type,
        "since_session": args.since,
        "until_session": args.until,
    }
    if args.note:
        edge["note"] = args.note
    data["edges"].append(edge)
    _save(args.campaign, data)
    sess = f" since:{args.since}" if args.since is not None else ""
    print(f"added edge {edge['id']}  {from_id} --[{args.type}]--> {to_id}{sess}")
    return 0


def cmd_close_edge(args) -> int:
    data = _load(args.campaign)
    edge = _edge_by_id(data, args.id)
    if not edge:
        print(f"error: edge '{args.id}' not found.", file=sys.stderr)
        return 1
    if edge.get("until_session") is not None:
        print(f"warning: edge {args.id} was already closed at session "
              f"{edge['until_session']}; overwriting.", file=sys.stderr)
    edge["until_session"] = args.at_session
    if getattr(args, "anchor", None):
        edge["closed_anchor"] = args.anchor
    _save(args.campaign, data)
    msg = f"closed edge {args.id} at session {args.at_session}"
    if getattr(args, "anchor", None):
        msg += f' — "{args.anchor[:60]}"'
    print(msg)
    return 0


def cmd_supersede_edge(args) -> int:
    """Mark an edge as superseded by another (hard retcon).

    Use when canon explicitly contradicts a prior extraction — e.g. a session
    log was corrected, or a relationship was misinterpreted. The old edge
    stays in the graph for audit trail; scene-context filters it out, but
    historical / subgraph queries can still surface it.

    Distinct from `close-edge`: close-edge ends a state cleanly (the relationship
    was real, then ended). supersede-edge says the original edge was wrong.
    """
    data = _load(args.campaign)
    wrong = _edge_by_id(data, args.id)
    if not wrong:
        print(f"error: edge '{args.id}' not found.", file=sys.stderr)
        return 1
    correct = _edge_by_id(data, args.by) if args.by else None
    if args.by and not correct:
        print(f"error: superseding edge '{args.by}' not found.", file=sys.stderr)
        return 1
    if args.by:
        wrong["superseded_by"] = args.by
    else:
        wrong["superseded_by"] = True
    if getattr(args, "reason", None):
        wrong["supersede_reason"] = args.reason
    _save(args.campaign, data)
    target = f"by edge {args.by}" if args.by else "(no replacement)"
    print(f"marked edge {args.id} as superseded {target}")
    return 0


def _ensure_party_node(data: dict) -> None:
    """Make sure the shared `party` node exists (created on first disposition set)."""
    if _node_by_id(data, PARTY_NODE_ID) is None:
        data["nodes"].append({
            "id": PARTY_NODE_ID,
            "type": "party",
            "name": "The Party",
        })


def cmd_set_disposition(args) -> int:
    """Type the party's stance toward an NPC or faction on the normalized scale.

    party --[disposition]--> npc   (allied/friendly/neutral/suspicious/hostile)
    party --[standing]-----> faction

    Single-valued and current: any prior active party→target stance edge is
    closed at this session (its arc is preserved for history), then the new
    level is added. The edge type is inferred from the target node's type —
    `standing` for a faction, `disposition` for everything else.
    """
    level = (args.level or "").strip().lower()
    if level not in DISPOSITION_LEVELS:
        print(f"error: --level must be one of {', '.join(DISPOSITION_LEVELS)} (got {args.level!r}).",
              file=sys.stderr)
        return 1

    data = _load(args.campaign)
    to_id = _resolve_or_die(data, args.to_id, "target")
    if to_id == PARTY_NODE_ID:
        print("error: cannot set the party's disposition toward itself.", file=sys.stderr)
        return 1
    _ensure_party_node(data)

    target = _node_by_id(data, to_id)
    edge_type = "standing" if (target and target.get("type") == "faction") else "disposition"

    # Close any prior active party→target stance edge so only the current one is
    # active at this session. History stays queryable via --at-session on old N.
    closed = 0
    for e in data["edges"]:
        if (e.get("from") == PARTY_NODE_ID and e.get("to") == to_id
                and e.get("type") in _DISPOSITION_EDGE_TYPES
                and e.get("until_session") is None and not e.get("superseded_by")):
            e["until_session"] = args.since
            closed += 1

    edge = {
        "id": _next_edge_id(data["edges"]),
        "from": PARTY_NODE_ID,
        "to": to_id,
        "type": edge_type,
        "level": level,
        "since_session": args.since,
        "until_session": None,
    }
    if args.note:
        edge["note"] = args.note
    data["edges"].append(edge)
    _save(args.campaign, data)

    sess = f" since:{args.since}" if args.since is not None else ""
    prior = f" (closed {closed} prior)" if closed else ""
    print(f"set {edge_type} {edge['id']}  party --[{edge_type}:{level}]--> {to_id}{sess}{prior}")
    return 0


def cmd_list(args) -> int:
    data = _load(args.campaign)
    nodes = data["nodes"]
    if args.type:
        nodes = [n for n in nodes if n.get("type") == args.type]
    nodes_sorted = sorted(nodes, key=lambda n: (n.get("type", ""), n.get("name", "")))
    print(f"# {args.campaign} graph — {len(data['nodes'])} nodes, "
          f"{len(data['edges'])} edges")
    if args.at_session is not None:
        active = [e for e in data["edges"] if _edge_active_at(e, args.at_session)]
        print(f"# active edges at session {args.at_session}: {len(active)}")
    print()
    def _plural(t: str) -> str:
        if t and t.endswith("y"):
            return t[:-1] + "ies"
        return (t or "?") + "s"
    cur_type = None
    for n in nodes_sorted:
        if n.get("type") != cur_type:
            cur_type = n.get("type")
            print(f"## {_plural(cur_type)}")
        tags = " [" + ",".join(n.get("tags", [])) + "]" if n.get("tags") else ""
        print(f"  {n['id']}  {n['name']}{tags}")
    return 0


def cmd_show(args) -> int:
    data = _load(args.campaign)
    node_id = _resolve_or_die(data, args.id, "node")
    n = _node_by_id(data, node_id)
    print(f"{n['id']}  ({n.get('type', '?')})  {n.get('name', '')}")
    if n.get("tags"):
        print(f"  tags: {', '.join(n['tags'])}")
    if n.get("summary"):
        print(f"  summary: {n['summary']}")
    print()
    incoming = [e for e in data["edges"] if e["to"] == node_id]
    outgoing = [e for e in data["edges"] if e["from"] == node_id]
    if outgoing:
        print("outgoing:")
        for e in outgoing:
            _print_edge(e, data, direction="out")
    if incoming:
        print("incoming:")
        for e in incoming:
            _print_edge(e, data, direction="in")
    return 0


def _print_edge(e: dict, data: dict, direction: str = "out") -> None:
    other_id = e["to"] if direction == "out" else e["from"]
    other = _node_by_id(data, other_id)
    other_name = other["name"] if other else other_id
    arrow = "-->" if direction == "out" else "<--"
    sess = []
    if e.get("since_session") is not None:
        sess.append(f"since s{e['since_session']}")
    if e.get("until_session") is not None:
        sess.append(f"until s{e['until_session']}")
    sess_str = " (" + ", ".join(sess) + ")" if sess else ""
    note = f"  — {e['note']}" if e.get("note") else ""
    print(f"  [{e.get('id', '?')}] {arrow} {e['type']}: {other_name}{sess_str}{note}")


def cmd_subgraph(args) -> int:
    data = _load(args.campaign)
    if not data["nodes"]:
        print(f"# graph not initialized for campaign '{args.campaign}' — skipping.")
        return 0
    seeds = [_resolve_or_die(data, s, "seed") for s in args.seed if s]
    sub = _expand(data, seeds, args.hops, args.at_session)
    print(render_subgraph(sub, args.at_session))
    return 0


def cmd_scene_context(args) -> int:
    data = _load(args.campaign)
    if not data["nodes"]:
        # Graph not yet initialized for this campaign. Print a brief notice and
        # exit 0 so this is safe to call unconditionally during /gm load.
        print(f"# graph not initialized for campaign '{args.campaign}' — skipping scene-context.")
        return 0
    seeds: list = []
    if args.place:
        seeds.append(_resolve_or_die(data, args.place, "place"))
    if args.present:
        seeds.extend(_resolve_csv(data, args.present, "present"))
    if args.threads:
        seeds.extend(_resolve_csv(data, args.threads, "thread"))
    if not seeds:
        print("error: scene-context needs at least --place, --present, or --threads.",
              file=sys.stderr)
        return 1
    sub = _expand(data, seeds, args.hops, args.at_session)
    print(f"# scene context — seeds: {', '.join(seeds)}, hops: {args.hops}"
          + (f", at session {args.at_session}" if args.at_session is not None else ""))
    print()
    print(render_subgraph(sub, args.at_session))
    return 0


def _expand(data: dict, seeds: list[str], hops: int,
            at_session: Optional[int]) -> dict:
    """BFS from seeds, hops bounded, only traversing edges active at at_session."""
    visited_nodes = set(seeds)
    frontier = set(seeds)
    visited_edges: list[dict] = []
    edges_by_node: dict[str, list[dict]] = {}
    for e in data["edges"]:
        if at_session is not None and not _edge_active_at(e, at_session):
            continue
        edges_by_node.setdefault(e["from"], []).append(e)
        edges_by_node.setdefault(e["to"], []).append(e)
    for _ in range(hops):
        next_frontier = set()
        for node_id in frontier:
            for e in edges_by_node.get(node_id, []):
                if e not in visited_edges:
                    visited_edges.append(e)
                other = e["to"] if e["from"] == node_id else e["from"]
                if other not in visited_nodes:
                    next_frontier.add(other)
                    visited_nodes.add(other)
        frontier = next_frontier
        if not frontier:
            break
    nodes = [n for n in data["nodes"] if n["id"] in visited_nodes]
    return {"nodes": nodes, "edges": visited_edges}


def render_subgraph(sub: dict, at_session: Optional[int]) -> str:
    """The subgraph as markdown TEXT, for callers that embed it in a prompt.

    Split out of `_emit_subgraph` for #276. That function prints to stdout
    because every caller until now was a CLI subcommand, and `play.py` needs
    the same rendering as a string it can put in a prompt -- not a shell it has
    to capture. Capturing stdout would be the smaller diff and the worse one: it
    turns a formatting bug into a subprocess dependency in the hot turn path,
    and it silently captures anything else that writes to stdout meanwhile.

    The CLI wrapper is unchanged in behaviour: it prints exactly what this
    returns, plus the trailing newline `print` supplied before.
    """
    lines: list[str] = []
    by_type: dict[str, list[dict]] = {}
    for n in sub["nodes"]:
        by_type.setdefault(n.get("type", "?"), []).append(n)

    def _label(n: dict) -> str:
        if n.get("category_node"):
            return f"{n['name']} (unnamed)"
        return n["name"]

    def _plural(t: str) -> str:
        if t and t.endswith("y"):
            return t[:-1] + "ies"
        return (t or "?") + "s"

    for t in sorted(by_type):
        lines.append(f"## {_plural(t)} ({len(by_type[t])})")
        for n in sorted(by_type[t], key=lambda x: x.get("name", "")):
            tags = " [" + ",".join(n.get("tags", [])) + "]" if n.get("tags") else ""
            summary = f" — {n['summary']}" if n.get("summary") else ""
            lines.append(f"  {n['id']}  {_label(n)}{tags}{summary}")
        lines.append("")
    if sub["edges"]:
        lines.append(f"## relationships ({len(sub['edges'])})")
        node_label = {n["id"]: _label(n) for n in sub["nodes"]}
        for e in sub["edges"]:
            f_name = node_label.get(e["from"], e["from"])
            t_name = node_label.get(e["to"], e["to"])
            sess = []
            if e.get("since_session") is not None:
                sess.append(f"s{e['since_session']}+")
            if e.get("until_session") is not None:
                sess.append(f"closed s{e['until_session']}")
            if e.get("superseded_by"):
                sess.append(f"superseded by {e['superseded_by']}")
            sess_str = " (" + ", ".join(sess) + ")" if sess else ""
            note = f"  — {e['note']}" if e.get("note") else ""
            # Disposition/standing edges carry a level on the normalized scale;
            # fold it into the type so the party's stance reads at a glance.
            etype = f"{e['type']}:{e['level']}" if e.get("level") else e["type"]
            lines.append(f"  {f_name} --[{etype}]--> {t_name}{sess_str}{note}")
    return "\n".join(lines)


def _existing_edge_match(data: dict, frm_id: str, to_id: str, etype: str) -> bool:
    """True if an active edge with same from/to/type already exists."""
    for e in data.get("edges", []):
        if (e["from"] == frm_id and e["to"] == to_id and e["type"] == etype
                and e.get("until_session") is None
                and not e.get("superseded_by")):
            return True
    return False


def cmd_extract(args) -> int:
    """Pattern-based extraction over the campaign's session logs.

    LLM-free — uses the verb-table seed at data/graph/verb_table_seed.yaml.
    Output format matches the upstream Haiku extractor exactly so that
    extract-apply (here or in claude-dnd-skill) can consume either.
    """
    campaign_dir = find_campaign(args.campaign)
    try:
        from graph_extract_deterministic import extract_proposals as _det_extract
    except ImportError as e:
        print(f"error: deterministic extractor module not available: {e}", file=sys.stderr)
        return 1
    proposals = _det_extract(
        campaign_dir,
        last_session_only=getattr(args, "last_session_only", False),
    )
    out_json = json.dumps(proposals, indent=2, ensure_ascii=False)
    print(f"# Deterministic extraction — {len(proposals)} proposals from "
          f"{campaign_dir.name}", file=sys.stderr)
    if getattr(args, "write", None):
        pathlib.Path(args.write).write_text(out_json, encoding="utf-8")
        print(f"# wrote proposals to {args.write}", file=sys.stderr)
    else:
        print(out_json)
    return 0


def _resolve_or_create(data: dict, name: str, *, is_category: bool = False,
                       no_auto_nodes: bool = False) -> tuple:
    """`name` as a node id, creating the node if it is new. Returns (id, created).

    Was a closure redefined on every loop iteration of `cmd_extract_apply`, which
    is why nothing outside that function could reuse it. #289 needs a second
    caller -- a play session proposing graph updates -- and the acceptance
    criterion is that it go through the existing writer rather than beside it.
    """
    existing_id = _resolve_node(data, name)
    if existing_id:
        return existing_id, False
    if is_category:
        new_id = f"cat_{_slug(name)}"
        data.setdefault("nodes", []).append({
            "id": new_id, "type": "category", "name": name,
            "tags": [], "summary": "",
            "category_node": True,
            "_auto_created_from_extract": True,
        })
        return new_id, True
    if no_auto_nodes:
        raise ValueError(f"node not found and --no-auto-nodes set: {name!r}")
    new_id = f"npc_{_slug(name)}"
    data.setdefault("nodes", []).append({
        "id": new_id, "type": "npc", "name": name, "tags": [], "summary": "",
        "_auto_created_from_extract": True,
    })
    return new_id, True


def _apply_node_summary(data: dict, p: dict, *, no_auto_nodes: bool) -> tuple:
    """Fill one node's `summary` from a `node_summary` proposal.

    `summary` is the only per-node field the turn path actually renders
    (`play.py:522-526` prints `name` and `summary` and ignores `type`, `tags`
    and every edge), so it is the field worth writing and the field that has to
    be written carefully.

    THE GM'S OWN WORDS WIN. A summary that is already filled and carries no
    `summary_source` was typed by the GM, and is never overwritten -- not by a
    longer proposal, not by a more confident one, not by a later session. Only an
    empty summary, or one this function wrote before (identified by its own
    `summary_source`), is fair game. A graph that a session can rewrite is a
    graph the GM stops trusting, and trust is the whole reason this passes a
    review step at all.

    Returns (state, node_id, created) where state is "written", "kept" or
    "skipped".
    """
    name = (p.get("to") or p.get("name") or "").strip()
    summary = (p.get("summary") or "").strip()
    if not name or not summary:
        return "skipped", None, False
    node_id, created = _resolve_or_create(
        data, name, is_category=bool(p.get("category_to")),
        no_auto_nodes=no_auto_nodes)
    node = _node_by_id(data, node_id)
    if node is None:                                   # unreachable, but cheap
        return "skipped", node_id, created
    previous = (node.get("summary") or "").strip()
    if previous and not node.get("summary_source"):
        return "kept", node_id, created
    if previous == summary:
        return "skipped", node_id, created
    node["summary"] = summary
    source = p.get("source") or {}
    if source:
        node["summary_source"] = source
    return "written", node_id, created


def apply_proposals(campaign: str, proposals: list, *, decide=None,
                    no_auto_nodes: bool = False, report=None) -> dict:
    """Apply proposals to `graph.json`. THE writer -- nothing else writes a graph.

    Extracted from `cmd_extract_apply` so a play session can propose updates
    through the same code path rather than beside it. Two things were true of the
    `cmd_*` verbs and are why they could not simply have been called: they take
    an `argparse.Namespace` and return an exit code, and node/edge resolution
    runs through `_resolve_or_die`, which calls `sys.exit(1)` -- which kills a
    play session rather than declining one proposal.

    `decide(i, total, proposal) -> "y" | "n" | "q"` is the review gate. `None`
    means apply everything, which is what `extract-apply` without `--review` has
    always done and what a non-interactive caller gets. `"q"` stops and declines
    the rest. An interactive caller that hits EOF must pass `"q"`, not `"y"`.

    Returns counts: `nodes`, `edges`, `summaries`, `skipped`, `declined`.

    Holding `_GRAPH_WRITE_LOCK` across the whole load-mutate-save is the point:
    `data` is read once and written once, so two interleaved writers would lose
    whichever finished second.
    """
    say = report if report is not None else (lambda m: print(m))
    counts = {"nodes": 0, "edges": 0, "summaries": 0, "skipped": 0, "declined": 0}
    with _GRAPH_WRITE_LOCK:
        data = _load(campaign)
        total = len(proposals)
        quit_review = False
        for i, p in enumerate(proposals, 1):
            if quit_review:
                counts["declined"] += 1
                continue
            if decide is not None:
                decision = decide(i, total, p)
                if decision == "q":
                    quit_review = True
                    counts["declined"] += 1
                    continue
                if decision != "y":
                    counts["declined"] += 1
                    continue

            if p.get("kind") == "node_summary":
                try:
                    state, node_id, created = _apply_node_summary(
                        data, p, no_auto_nodes=no_auto_nodes)
                except ValueError as e:
                    say(f"  skip {i}: {e}")
                    counts["skipped"] += 1
                    continue
                if state == "written":
                    counts["summaries"] += 1
                    counts["nodes"] += int(created)
                    say(f"  summary {node_id}: {p.get('summary', '')[:60]}")
                elif state == "kept":
                    # Counted as skipped, and printed, rather than silently
                    # dropped: a GM who applies three proposals and sees "+0"
                    # has to be able to tell "nothing happened" from "all three
                    # were refused because you wrote those summaries yourself".
                    counts["skipped"] += 1
                    say(f"  keep {node_id}: already has a GM-written summary")
                else:
                    counts["skipped"] += 1
                continue

            etype = p.get("type", "")
            since = p.get("since_session")
            source = p.get("source") or {}
            note = p.get("note") or ""
            try:
                frm_id, made_frm = _resolve_or_create(
                    data, p.get("from", ""), is_category=bool(p.get("category_from")),
                    no_auto_nodes=no_auto_nodes)
                to_id, made_to = _resolve_or_create(
                    data, p.get("to", ""), is_category=bool(p.get("category_to")),
                    no_auto_nodes=no_auto_nodes)
            except ValueError as e:
                say(f"  skip {i}: {e}")
                counts["skipped"] += 1
                continue
            counts["nodes"] += int(made_frm) + int(made_to)

            if _existing_edge_match(data, frm_id, to_id, etype):
                counts["skipped"] += 1
                continue

            edge = {
                "id": _next_edge_id(data["edges"]),
                "from": frm_id,
                "to": to_id,
                "type": etype,
                "since_session": since,
                "until_session": None,
                "note": note,
            }
            if source:
                edge["source"] = source
            data["edges"].append(edge)
            counts["edges"] += 1
            say(f"  applied {edge['id']}  {frm_id} --[{etype}]--> {to_id} (s{since}+)")

        # Only save if something changed. Not an optimisation -- it is the T2.5
        # no-graph contract. A GM who reviews every proposal and declines all of
        # them must not end up with a `graph.json` they did not have before,
        # because "has a graph" is what switches the turn off the full notes
        # digest and onto the scene cull. Creating one as a side effect of
        # declining everything breaks that contract from the least expected
        # direction, and `cmd_extract_apply` inherited it by always saving.
        if counts["nodes"] or counts["edges"] or counts["summaries"]:
            _save(campaign, data)
    return counts


def cmd_extract_apply(args) -> int:
    """Apply edge proposals from a JSON file produced by extract --write."""
    proposals_path = pathlib.Path(args.proposals).expanduser()
    if not proposals_path.exists():
        print(f"error: proposals file not found: {proposals_path}", file=sys.stderr)
        return 1
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    pick = None
    if args.pick:
        pick = set(int(x.strip()) for x in args.pick.split(",") if x.strip())

    review = bool(getattr(args, "review", False))
    if review and pick is not None:
        print("error: --review and --pick are mutually exclusive", file=sys.stderr)
        return 2

    def _decide(i: int, total: int, p: dict) -> str:
        if pick is not None and i not in pick:
            return "n"
        if not review:
            return "y"
        src = p.get("source", {}) or {}
        anchor = (src.get("anchor") or "")[:140]
        conf = p.get("confidence", "?")
        if p.get("kind") == "node_summary":
            print(f"\n[{i}/{total}] summary for {p.get('to') or p.get('name')}"
                  f"  (confidence={conf})")
            print(f"    {p.get('summary', '')[:160]}")
        else:
            print(f"\n[{i}/{total}] {p.get('from','?')} --[{p.get('type','?')}]--> {p.get('to','?')}"
                  f"  (s{p.get('since_session','?')}+, confidence={conf})")
        if anchor:
            print(f"    src: {src.get('file','?')} s{src.get('session','?')} — \"{anchor}\"")
        if p.get("note"):
            print(f"    note: {p['note']}")
        while True:
            try:
                a = input("    apply? [y]es / [n]o / [q]uit: ").strip().lower()
            except EOFError:
                return "q"
            if a in {"y", "yes", ""}:
                return "y"
            if a in {"n", "no", "s", "skip"}:
                return "n"
            if a in {"q", "quit", "exit"}:
                return "q"
            print("    please enter y / n / q")

    counts = apply_proposals(args.campaign, proposals, decide=_decide,
                             no_auto_nodes=bool(getattr(args, "no_auto_nodes", False)))
    msg = (f"# done: +{counts['nodes']} nodes, +{counts['edges']} edges, "
           f"{counts['skipped']} skipped")
    if counts["summaries"]:
        msg += f", {counts['summaries']} summaries written"
    if counts["declined"]:
        msg += f", {counts['declined']} declined in review"
    print(msg)
    return 0


# -------- argparse --------

def main() -> int:
    p = argparse.ArgumentParser(prog="gm_graph")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_camp(sp):
        sp.add_argument("--campaign", required=True)

    sp = sub.add_parser("add-node")
    add_camp(sp)
    sp.add_argument("--type", required=True,
                    help="node type (npc, faction, place, item, thread, ...)")
    sp.add_argument("--name", required=True)
    sp.add_argument("--id", help="explicit id (default: <type>_<name-slug>)")
    sp.add_argument("--tags", help="comma-separated")
    sp.add_argument("--summary", help="one-line summary")
    sp.set_defaults(func=cmd_add_node)

    sp = sub.add_parser("add-edge")
    add_camp(sp)
    sp.add_argument("--from", dest="from_id", required=True)
    sp.add_argument("--to", dest="to_id", required=True)
    sp.add_argument("--type", required=True,
                    help="edge type (loyal_to, opposes, lives_in, ...)")
    sp.add_argument("--since", dest="since", type=int, default=None,
                    help="session number when edge became active")
    sp.add_argument("--until", dest="until", type=int, default=None,
                    help="session number when edge ended (rare on add)")
    sp.add_argument("--note")
    sp.set_defaults(func=cmd_add_edge)

    sp = sub.add_parser("close-edge")
    add_camp(sp)
    sp.add_argument("--id", required=True, help="edge id to close")
    sp.add_argument("--at-session", dest="at_session", type=int, required=True)
    sp.add_argument("--anchor",
        help="verbatim phrase from canon that justifies the closure (recorded as closed_anchor)")
    sp.set_defaults(func=cmd_close_edge)

    sp = sub.add_parser("supersede-edge",
        help="mark an edge as superseded (hard retcon) — preserves audit trail "
             "but excludes from active queries")
    add_camp(sp)
    sp.add_argument("--id", required=True, help="edge id to mark wrong")
    sp.add_argument("--by", help="optional id of the corrected edge that replaces it")
    sp.add_argument("--reason", help="one-line explanation of the retcon")
    sp.set_defaults(func=cmd_supersede_edge)

    sp = sub.add_parser("set-disposition",
        help="type the party's stance toward an NPC (disposition) or faction "
             "(standing) on the allied/friendly/neutral/suspicious/hostile scale")
    add_camp(sp)
    sp.add_argument("--to", dest="to_id", required=True,
                    help="target NPC or faction (node id or name)")
    sp.add_argument("--level", required=True,
                    help="allied | friendly | neutral | suspicious | hostile")
    sp.add_argument("--since", type=int, default=None,
                    help="session number this stance became true")
    sp.add_argument("--note", help="one-line reason for the stance")
    sp.set_defaults(func=cmd_set_disposition)

    sp = sub.add_parser("list")
    add_camp(sp)
    sp.add_argument("--type", help="filter by node type")
    sp.add_argument("--at-session", dest="at_session", type=int, default=None)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show")
    add_camp(sp)
    sp.add_argument("--id", required=True)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("subgraph")
    add_camp(sp)
    sp.add_argument("--seed", action="append", required=True,
                    help="repeat for multiple seeds")
    sp.add_argument("--hops", type=int, default=2)
    sp.add_argument("--at-session", dest="at_session", type=int, default=None)
    sp.set_defaults(func=cmd_subgraph)

    sp = sub.add_parser("scene-context")
    add_camp(sp)
    sp.add_argument("--place")
    sp.add_argument("--present", help="comma-separated node ids in scene")
    sp.add_argument("--threads", help="comma-separated thread node ids")
    sp.add_argument("--hops", type=int, default=2)
    sp.add_argument("--at-session", dest="at_session", type=int, default=None)
    sp.set_defaults(func=cmd_scene_context)

    sp = sub.add_parser("extract",
        help="pattern-match session-log against verb_table_seed.yaml; propose edges "
             "with verbatim source anchors (LLM-free)")
    add_camp(sp)
    sp.add_argument("--write", metavar="FILE",
        help="write proposals as JSON for later --apply review")
    sp.add_argument("--last-session-only", action="store_true",
        help="only scan the last ## Session N block of session-log.md")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("extract-apply",
        help="apply edge proposals from a JSON file produced by extract --write")
    add_camp(sp)
    sp.add_argument("--proposals", required=True, metavar="FILE",
        help="proposals JSON file path")
    sp.add_argument("--pick", metavar="N1,N2,...",
        help="apply only the listed proposal numbers (1-indexed); default: apply all")
    sp.add_argument("--review", action="store_true",
        help="walk proposals one at a time with y/n/q prompts; mutually exclusive with --pick")
    sp.add_argument("--no-auto-nodes", action="store_true",
        help="error on edges referencing unknown nodes instead of auto-creating placeholder npc nodes")
    sp.set_defaults(func=cmd_extract_apply)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())


# -------- scene relevance for the play.py turn path (#276) --------

def _overlap_terms(query: str) -> set:
    """Content words from a scene description, lowercased.

    Deliberately not a stemmer and deliberately not a stopword list. A stopword
    list is a maintenance liability for a marginal gain here: the query is a
    state.md excerpt where the informative words ("cellar", "smuggler") are the
    content anyway, and a wrong stopword silently drops a node the DM needed.
    Short tokens are dropped because they match everything.

    KNOWN LIMIT, and it is a real one: a node whose distinguishing words are all
    <=3 characters ("Al", "Bo", "Mal", "Ash") can never be matched lexically, so
    it survives a cull only if it is also graph-adjacent to a seed. That is the
    right trade -- short tokens match so much that admitting them would flood
    every scene -- but it means an off-screen NPC with a short name and no graph
    edge is culled. Noted rather than papered over: the fix is a name-length
    check on `add-node` or a `graph.json` edge, both of which are campaign-setup
    decisions, not something this ranking should guess at.
    """
    words = {w.strip(".,;:!?'()[]").lower().replace(chr(34), "")
             for w in (query or "").split()}
    return {w for w in words if len(w) > 3}


def scene_nodes(campaign: str, query: str, *, hops: int = 2,
                present: Optional[str] = None, limit: int = 12,
                at_session: Optional[int] = None) -> dict:
    """The nodes worth putting in front of the DM for THIS scene. #276.

    Returns `{"present": bool, "nodes": [...], "names": {slug: name}}`.

    `present` is False when the campaign has no graph, which is the signal for
    the caller to fall back to the full notes digest and behave exactly as it
    does today. That is the whole of the no-graph contract: a campaign without a
    graph must not acquire a new failure mode, so nothing here raises and
    nothing here is required for a turn to happen.

    WHY RELEVANCE AND NOT STRICT ADJACENCY
    ======================================
    Adjacency alone culls by distance, and this repo depends on the DM knowing
    what is OFF-screen, which is the opposite of what distance-culling does:

      * `world_queue.py` exists to hold pressure "waiting to surface". A faction
        two hops away with a clock about to fire is precisely what must survive.
      * `dm.md` requires danger be "signalled before it lands: a rumour, a cost
        someone else already paid." Foreshadowing is by definition knowledge of
        something that has not arrived yet.

    So this is a HYBRID rank, and the two halves are deliberately different
    kinds of evidence:

      * adjacency says a node is structurally connected to the seeds, and
        contributes a decaying bonus by hop distance;
      * lexical overlap says a node is being talked about RIGHT NOW, in the
        scene text itself, regardless of where it sits in the graph.

    A node named in the scene outranks a node merely nearby, which is what makes
    the far-but-live-threat case survive a cull. Adjacency alone would drop it
    and the DM would learn about the threat when it landed -- the exact failure
    `dm.md` forbids.
    """
    try:
        data = _load(campaign)
    except (OSError, ValueError):
        # A malformed graph.json must not take the turn down with it. The
        # campaign falls back to the full digest, which is today's behaviour.
        return {"present": False, "nodes": [], "names": {}}
    if not data["nodes"]:
        return {"present": False, "nodes": [], "names": {}}

    # Seeds: whatever the caller names as present. Location is a hint, not a
    # requirement -- a scene in a car or a dream has no place node, and a
    # missing seed must not disable the whole thing.
    seeds: list = []
    for ref in (present or "").split(","):
        ref = ref.strip()
        if not ref:
            continue
        resolved = _resolve_node(data, ref)
        if resolved:
            seeds.append(resolved)

    by_hop: dict = {}
    if seeds:
        # _expand is hop-bounded BFS; walk it in rings so distance is known.
        ring = list(seeds)
        seen = set(seeds)
        for hop in range(hops + 1):
            for nid in ring:
                by_hop[nid] = hop
            if hop == hops:
                break
            sub = _expand(data, ring, 1, at_session)
            nxt = [n["id"] for n in sub["nodes"] if n["id"] not in seen]
            seen.update(nxt)
            ring = nxt
            if not ring:
                break

    terms = _overlap_terms(query)
    scored = []
    for node in data["nodes"]:
        haystack = " ".join(str(node.get(k) or "")
                           for k in ("name", "summary")).lower()
        hits = sum(1 for term in terms if term in haystack)
        if by_hop.get(node["id"]) is not None:
            # Connected nodes are relevant even when the scene never names them.
            scored.append((hits, -by_hop[node["id"]], node))
        elif hits:
            # Named but unconnected: still relevant. This is the branch that
            # keeps an off-screen threat, and it is the whole reason this
            # function exists instead of a plain _expand call.
            scored.append((hits, -(hops + 1), node))
    scored.sort(key=lambda row: (row[0], row[1], str(row[2].get("name", ""))),
                reverse=True)
    chosen = [row[2] for row in scored[:limit]]
    return {
        "present": True,
        "nodes": chosen,
        "names": {_slug(n.get("name", "")): n.get("name", "") for n in chosen},
    }
