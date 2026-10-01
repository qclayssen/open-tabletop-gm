"""pins.py: note pins and nested-map pins on a battle map.

A pin is a marker a GM puts on a map that carries a *target*: a campaign note to
open, or another map to navigate to. It is the cheapest thing in this project
that the table actually feels, and it is cheap because the repo already owns
markdown campaign files and already owns maps.

Where pins live, and why not somewhere else
------------------------------------------

``<campaign>/pins/<map-slug>.json``. Not in ``display/maps/`` (those are shared
across campaigns), and not in ``encounter.json`` (a pin outlives a fight, and
putting it there would drag a rendering concern into ``SCHEMA_VERSION``). The
precedent is ``world.revealed_clocks``: campaign-scoped state the display reads
by name, in the display's own Flask layer. **This module is not imported by
``scripts/tactics/``** and should not be, for the same reason the redaction
lives in the display and not in ``sync.snapshot()``.

The two halves of "who may read this"
-------------------------------------

``allowed_dirs`` and ``revealed()`` are the whole policy, and they answer
different questions:

* **Can this path be read at all?** ``allowed_dirs`` -- a pin may only open
  markdown under ``notes/``, ``locations/`` or ``handouts/``. This is an
  *allow*-list, not a deny-list of sealed names, because the failure we care
  about is a file someone adds later that turns out to be a spoiler. A deny-list
  has to be updated every time that happens; an allow-list is already correct.
  This is what makes CLAUDE.md's hard rule 1 a code guarantee rather than a
  prompt instruction.

* **Should this pin be on the players' screen at all?** ``revealed()``. A pin
  that is not revealed never enters a browser payload. It is a *table* boundary,
  not an access-control bit: the display has one audience and no way to tell a GM
  from a player, so this flag does not say "the GM may see it", it says "nobody
  at the table sees it, the GM reads it from the editor or the terminal". See
  the Q4 note in docs/specs/SPEC-grid-and-map.md. The sealed-name guard above is
  what actually protects spoiler files; this is a convenience.

Both are enforced unconditionally, with no "is this requester the GM" branch,
because there is no such branch to write.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import stat
import uuid

from paths import campaign_path

# Folders a note pin may read. A pin target must be inside one of these, and be
# a .md file. Anything else -- including a file added next year that nobody
# classified -- is refused by default.
ALLOWED_DIRS = ("notes", "locations", "handouts")

# Fields a pin may carry. Anything else in a submitted record is dropped rather
# than stored, so a crafted POST cannot plant a key the reader would later trust.
_FIELDS = ("id", "x", "y", "label", "kind", "target", "revealed")

MAX_LABEL = 120
MAX_TARGET = 300
MAX_PINS = 200
# The largest note in the live campaign is 118 KB, so this is room to spare
# rather than a limit that bites. It exists so a hostile or accidental 400 MB
# file in notes/ cannot be turned into a browser-sized response.
MAX_NOTE_BYTES = 256 * 1024


class PinError(ValueError):
    """A pin record or a pin file that this module refuses.

    One exception for every refusal, on purpose: the caller turns it into a 400
    and the message is written for the GM, not for an attacker's oracle.
    """


# ── the policy ──────────────────────────────────────────────────────────────

def allowed_dirs() -> tuple:
    """The folders a note pin may read. See ALLOWED_DIRS."""
    return ALLOWED_DIRS


def _require_allowed(rel: str) -> str:
    """Raise PinError unless `rel` is a .md file under an allowed folder.

    One implementation, called twice: once for the path that was requested and
    once for the path the filesystem resolved it to. Keeping them on the same
    function is the point -- two copies of this check is how one of them ends up
    applied to the wrong string.
    """
    if not rel.lower().endswith(".md"):
        raise PinError("a note target must be a .md file")
    first = rel.split("/", 1)[0]
    if first not in ALLOWED_DIRS:
        raise PinError(
            f"'{first}' is not a folder a pin may read. "
            f"Allowed: {', '.join(ALLOWED_DIRS)}/.")
    return rel


def check_note_target(target: str) -> str:
    """Return `target` if a pin may open it, else raise PinError.

    Two gates, in order, and the order matters: the cheap textual ones run before
    anything touches the filesystem, so a traversal attempt never causes a stat,
    and the caller cannot use response timing to tell a sealed name from a
    missing one.

    1. shape -- non-empty, relative, forward slashes only, ``.md``, no ``.`` or
       ``..`` segment. ``campaign_path`` enforces all of this too, but doing it
       here means a traversal is refused when the pin is *written*, where the
       GM can be told, rather than silently stored and refused on every click.
    2. allow-list -- the first segment must be one of ``ALLOWED_DIRS``

    This is not the containment guard. It is ``paths.campaign_path``, and
    ``tests/test_campaign_path.py`` owns that.
    """
    if not isinstance(target, str) or not target.strip():
        raise PinError("a note pin needs a target")
    rel = target.strip()
    if len(rel) > MAX_TARGET:
        raise PinError(f"a note target may be at most {MAX_TARGET} characters")
    if "\\" in rel:
        raise PinError("a note target uses forward slashes")
    if rel.startswith("/"):
        raise PinError("a note target is relative to the campaign")
    for seg in rel.split("/"):
        if seg == "":
            raise PinError("a note target has an empty segment")
        if seg in (".", ".."):
            raise PinError("a note target has no '.' or '..' segments")
    return _require_allowed(rel)


def revealed(pins: list) -> list:
    """The pins that belong on a players' screen. Everything else is absent.

    Unconditional, and the only redaction this module does. A pin is not
    revealed unless it says so, so authoring one is safe by default: the failure
    mode of forgetting the flag is an invisible pin, not a spoiler on a phone.

    Deliberately *not* a function of who is asking. There is one browser
    audience here and no way to tell a GM from a player, so a signature that
    took a requester would be a promise the caller cannot keep.
    """
    return [p for p in pins if p.get("revealed") is True]


# ── the record ──────────────────────────────────────────────────────────────

def _clean_label(value) -> str:
    """A label is display text: trimmed, length-capped, control chars dropped.

    It is escaped on the way out (``display/static/display.js``'s shared
    ``esc``), so this is hygiene and not the security boundary.
    """
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    # Drop unprintable characters rather than replacing them with a space: a tab
    # between two words is dropped by the point `isprintable()` is consulted, so
    # "Inn\t\tRoom" becomes "InnRoom" and the label reads as one nonsense word.
    # The browser collapses runs of whitespace when it draws it, so there is
    # nothing to normalise here beyond this.
    text = "".join(ch for ch in text if ch == " " or ch.isprintable())
    return re.sub(r"\s+", " ", text).strip()[:MAX_LABEL]


def _number(value, field) -> float:
    """A cell coordinate. Floats, because a pin is placed by a pointer.

    bool is refused explicitly: ``True`` is an ``int`` in Python, and a pin that
    silently lands at 1.0 because somebody sent ``true`` is the kind of thing
    that is only noticed on the table.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PinError(f"pin {field} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise PinError(f"pin {field} must be a finite number")
    return number


def validate(raw, known_maps=None) -> dict:
    """Return one clean pin record, or raise PinError.

    `known_maps`, when given, is the set of slugs a ``kind: "map"`` pin may
    name. A map target is a *slug*, never a path: it is looked up in
    ``display/maps/`` and nowhere else, so a map pin cannot be used to read a
    file. Unknown slugs are refused here rather than at render time, so a typo
    is reported to the GM who made it.
    """
    if not isinstance(raw, dict):
        raise PinError("a pin must be an object")

    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in ("note", "map"):
        raise PinError("a pin kind is 'note' or 'map'")

    target = raw.get("target")
    if not isinstance(target, str) or not target.strip():
        raise PinError("a pin needs a target")
    target = target.strip()
    if len(target) > MAX_TARGET:
        raise PinError(f"a pin target may be at most {MAX_TARGET} characters")

    if kind == "note":
        target = check_note_target(target)
    else:
        # A slug: no separators, no traversal, and it must be a map that exists.
        if "/" in target or "\\" in target or ".." in target:
            raise PinError("a map pin names a map, not a path")
        if known_maps is not None and target not in known_maps:
            raise PinError(f"no map named {target!r}")

    pin = {
        "id": str(raw.get("id") or uuid.uuid4().hex[:12]),
        "x": _number(raw.get("x", 0), "x"),
        "y": _number(raw.get("y", 0), "y"),
        "label": _clean_label(raw.get("label")) or _default_label(kind, target),
        "kind": kind,
        "target": target,
        # Fail closed. Absent means not on the players' screen.
        "revealed": raw.get("revealed") is True,
    }
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", pin["id"]):
        raise PinError("a pin id must be letters, digits, dash or underscore")
    return pin


def _default_label(kind: str, target: str) -> str:
    """A label the GM did not type: the note's file name, or the map slug."""
    base = target.rsplit("/", 1)[-1]
    if base.lower().endswith(".md"):
        base = base[:-3]
    return _clean_label(base.replace("-", " ").replace("_", " ") or target)


# ── the store ───────────────────────────────────────────────────────────────

def pins_path(camp_dir, map_slug: str) -> pathlib.Path:
    """Where one map's pins live.

    The slug is refused rather than sanitised, which is the opposite of what this
    function looked like it should do and is deliberate. Stripping the
    characters it does not like turns ``../../escape`` into ``escape.json`` and
    ``with space`` into ``withspace.json``: two different inputs quietly
    becoming the same file, so a pin written for one map lands in another and
    the GM cannot tell from the board. A slug that needs cleaning is a slug that
    is wrong, and a real one (``maps.available()`` yields only
    ``[A-Za-z0-9_-]``) passes through untouched.
    """
    slug = str(map_slug).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", slug):
        raise PinError("a pin file needs a plain map slug")
    return pathlib.Path(camp_dir) / "pins" / f"{slug}.json"


def load(camp_dir, map_slug: str) -> list:
    """The pins on one map. A map with no pins has an empty list, not an error.

    A pins file that is corrupt is also an empty list. Pins are annotations: a
    GM who hand-edited one badly should still get a working board, and the write
    route reports the parse error when they next save.
    """
    path = pins_path(camp_dir, map_slug)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        try:
            out.append(validate(entry))
        except PinError:
            continue          # one bad pin does not lose the others
    return out


def save(camp_dir, map_slug: str, pins: list, known_maps=None) -> list:
    """Write one map's pins, validating every record first. Returns what was written.

    Validation happens before the directory is created, so a refused write leaves
    the filesystem exactly as it found it -- including the pins directory, which
    is not created for a request that was going to be refused anyway.
    """
    if not isinstance(pins, list):
        raise PinError("pins must be a list")
    if len(pins) > MAX_PINS:
        raise PinError(f"a map may carry at most {MAX_PINS} pins")

    clean = [validate(p, known_maps) for p in pins]

    # Two pins on the same square is a board the GM cannot read: one is always
    # underneath. Last write wins, matching how the editor draws them.
    seen, deduped = set(), []
    for pin in clean:
        key = (pin["x"], pin["y"])
        deduped = [p for p in deduped if (p["x"], p["y"]) != key]
        seen.add(key)
        deduped.append(pin)

    path = pins_path(camp_dir, map_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(deduped, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return deduped


def note_body(camp_dir, pin: dict) -> str:
    """The markdown a note pin opens, or raise PinError.

    The read path re-runs every gate ``validate`` ran. That is deliberate and it
    is the point: a pin file can be edited by hand, copied between campaigns, or
    written before the allow-list was this strict, so the list endpoint's answer
    is not an answer the read endpoint can rely on. Re-checking here is what
    makes a guessed URL a 404 rather than a file.

    The allow-list is applied **twice**, to the requested path and to the path
    the filesystem actually resolved to, and the second check is the one that
    matters. Containment only proves the file is inside the campaign, which a
    symlink *inside* the campaign satisfies:

        notes/harbour.md -> answer-key.md

    Both segments of the request pass every other gate. The deny-list matched
    ``harbour.md``, not the file it points at, so the allow-list is re-applied to
    ``resolved.relative_to(campaign)`` and the pin is refused for what it
    actually opens. Without this the one file in the campaign that must never be
    readable is reachable through a link a GM made for an ordinary reason.
    """
    if pin.get("kind") != "note":
        raise PinError("that pin does not open a note")
    if pin.get("revealed") is not True:
        raise PinError("that pin is not on the players' board")

    # Shape only, with the allow-list re-checked below on the *resolved* path.
    # The first call is here to reject `..`, a NUL and an absolute path before
    # anything touches the filesystem; its allow-list refusal is deliberately
    # re-raised as "no such note" so the two reasons read identically.
    try:
        rel = check_note_target(pin["target"])
    except PinError as exc:
        raise PinError("no such note") from exc
    resolved = campaign_path(camp_dir, rel)         # containment, incl. symlinks

    # Re-apply the policy to the file we are actually going to open.
    #
    # Every refusal below raises the SAME message, deliberately. A message that
    # distinguishes "'DM_SEALED' is not a folder a pin may read" from "no such
    # note" is an existence oracle over the campaign's sealed files: the caller
    # learns which ones exist by asking. The GM's own mistakes (a typo in the
    # allow-list, a missing folder) are still legible from the CLI, which
    # validates at authoring time with the specific message -- this read path is
    # for a browser, and a browser gets one answer to every "no".
    try:
        inside = resolved.relative_to(pathlib.Path(camp_dir).expanduser().resolve())
    except ValueError as exc:                      # campaign_path already refused
        raise PinError("no such note") from exc
    try:
        _require_allowed(str(inside).replace("\\", "/"))
    except PinError as exc:
        raise PinError("no such note") from exc

    # A regular file only. `notes.md` being a FIFO passes every other check and
    # blocks forever on open; a device node behaves the same way. Cheap, and it
    # closes a hang that no other rule here would catch.
    try:
        if not stat.S_ISREG(os.stat(resolved).st_mode):
            raise PinError("no such note")
    except FileNotFoundError as exc:
        raise PinError("no such note") from exc
    except OSError as exc:
        raise PinError("no such note") from exc

    try:
        body = resolved.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise PinError("no such note") from exc
    if len(body) > MAX_NOTE_BYTES:
        raise PinError("no such note")
    return body
