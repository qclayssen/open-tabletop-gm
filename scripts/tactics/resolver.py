"""Resolve explicit movement references against the current encounter.

This module recognizes exact token ids/names, exact landmark handles, and
square labels only when the caller marks them as typed square input. It does
not infer directions or calculate a destination. Movement legality remains in
engine.preview_move() and engine.move().
"""

from __future__ import annotations

from . import sight as sight_mod
from .grid import parse_square

NONE = "NONE"
AMBIGUOUS = "AMBIGUOUS"
RESOLVED = "RESOLVED"


def resolve(enc, actor_ref, reference, *, kind=None, players=False) -> dict:
    """Return a typed resolution and candidates without changing encounter state.

    ``kind`` may be ``"square"`` to declare that ``reference`` is a square
    label supplied explicitly by the player, or ``"token"`` / ``"landmark"``
    to constrain exact-reference lookup. With no kind, exact token and
    landmark handles are both considered. No substring or fuzzy matching is
    performed. In players mode, every candidate is filtered through the same
    sight policy as the player-facing state card.
    """
    if kind not in (None, "square", "token", "landmark"):
        raise ValueError("kind must be square, token, landmark, or None")
    if not isinstance(reference, str) or not reference.strip():
        return _result(NONE, [])

    text = reference.strip()
    actor = enc.token(actor_ref)
    grid = enc.board()
    if kind == "square":
        try:
            pos = parse_square(text)
        except (TypeError, ValueError):
            return _result(NONE, [])
        if not grid.in_bounds(pos):
            return _result(NONE, [])
        from .grid import label
        return _result(RESOLVED, [{"kind": "square", "square": label(pos)}])

    visible_squares = None
    visible_tokens = None
    if players:
        view = sight_mod.sight(enc, actor.id, players=True)
        visible_squares = set(view["visible"])
        visible_tokens = {c["id"] for c in view["creatures"]}

    folded = text.casefold()
    candidates = []
    if kind in (None, "token"):
        for token in enc.tokens.values():
            if not token.active or token.id == actor.id:
                continue
            if token.id.casefold() != folded and token.name.casefold() != folded:
                continue
            if visible_tokens is not None and token.id not in visible_tokens:
                continue
            candidates.append({"kind": "token", "id": token.id, "name": token.name,
                               "square": token.square})

    if kind in (None, "landmark"):
        for mark in (enc.meta or {}).get("landmarks", []):
            handle = str(mark.get("name", ""))
            if handle.casefold() != folded:
                continue
            squares = list(mark.get("squares", []))
            if visible_squares is not None:
                squares = [square for square in squares if square in visible_squares]
                if not squares:
                    continue
            candidates.append({"kind": "landmark", "id": handle,
                               "name": handle, "squares": squares})

    status = NONE if not candidates else RESOLVED if len(candidates) == 1 else AMBIGUOUS
    return _result(status, candidates)


def _result(status: str, candidates: list) -> dict:
    """Make the resolution shape uniform for callers and action contracts."""
    return {"status": status, "candidates": candidates}
