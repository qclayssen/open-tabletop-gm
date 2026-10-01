#!/usr/bin/env python3
"""campaign_lint.py: check a campaign's markdown files before a session.

Usage:
    python3 scripts/campaign_lint.py <campaign> [--json] [--strict] [--all]
    python3 scripts/campaign_lint.py --all          # every campaign in the root
    python3 scripts/campaign_lint.py <campaign> --json   # for CI

state.md, world.md, npcs.md and the character sheets are markdown with exact
section headings the code greps for, and they are written by hand and by model.
Nothing checked them, so the failure mode is silent: a renamed heading means
the DM prompt quietly loses a section, and an unfilled <placeholder> means a
campaign reads as finished while the digest drops the line before the model
ever sees it (context.notes_digest). This reports both.

What it checks:
  - required files exist (state.md, world.md, npcs.md; characters/ optional)
  - required "## " sections are present, spelled exactly as the code expects
  - the machine-parsed state.md header fields (session count, last session,
    system module/version) are there and numeric/known
  - unfilled template lines, per file, with the reason and the line number
  - a character sheet's Identity / Combat Stats fields are filled
  - a campaign arc YAML block parses and names a current beat
  - the Fantasy Statblocks plugin is pointed at the exported Bestiary/ folder

Exit codes:  0 clean (or warnings only)   1 problems found   2 campaign not found
With --strict, warnings also exit 1.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm import context
from map_to_atlas import IMAGE_SUFFIXES, slug
from paths import campaigns_dir, find_campaign

# The headings the code greps for, and why. DIGEST_SECTIONS goes into the DM
# prompt every turn (context.state_digest); the rest are read by the display or
# by sync. A missing one is not a style problem: the section is simply gone.
REQUIRED_STATE_SECTIONS = {
    **{s: "read into the DM prompt every turn (context.state_digest)"
       for s in context.DIGEST_SECTIONS if s not in context.OPTIONAL_DIGEST_SECTIONS},
    # Last, so it wins: World State is in DIGEST_SECTIONS and is also read by the
    # display sidebar. Listed first it was overwritten by the comprehension above,
    # so the reason a GM sees named only the sidebar and never the DM prompt.
    "World State": "read into the DM prompt every turn (context.state_digest), and by the display sidebar (display/dm_help.py)",
    "Session Flags": "roll_mode / council / tts settings",
}
WORLD_SECTIONS = ("Campaign Tone & Genre", "World Foundations", "Factions",
                  "Quest Seed Bank")
NPC_INDEX_HEADER = "| Name | Role | Faction | Location | Attitude | Notes |"

SHEET_SECTIONS = ("Identity", "Combat Stats")
_HEAD2 = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"^```ya?ml[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
# The state.md header is one line of "**Label:** value  **Label:** value" pairs, so
# a value stops at the next label as well as at a pipe - a greedy value swallows
# every field after the first and reports the campaign as having none.
_HEADER_FIELD = re.compile(r"\*\*([A-Za-z][A-Za-z ]*):\*\*\s*([^|\n]*?)(?=\s{2,}\*\*|\s*\||\s*$)",
                           re.MULTILINE)

# A <placeholder> inside a code fence or a table separator is not unfilled
# prose; the arc's YAML block is full of them by design (templates/state.md).
_FENCE_SPAN = re.compile(r"^```[^\n]*\n.*?^```", re.MULTILINE | re.DOTALL)


class Report:
    """Findings for one campaign. `level` is "error" (something is missing or
    broken) or "warn" (present, but still blank template)."""

    def __init__(self, campaign: str, path: pathlib.Path):
        self.campaign = campaign
        self.path = path
        self.findings: list[dict] = []

    def add(self, level: str, file: str, message: str, line: int | None = None,
            hint: str = "") -> None:
        self.findings.append({"level": level, "file": file, "line": line,
                              "message": message, "hint": hint})

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f["level"] == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f["level"] == "warn")

    def as_dict(self) -> dict:
        return {"campaign": self.campaign, "path": str(self.path),
                "errors": self.errors, "warnings": self.warnings,
                "ok": self.errors == 0, "findings": self.findings}


def _outside_fences(text: str, index: int) -> bool:
    """True when the character offset is not inside a ``` fenced block."""
    for m in _FENCE_SPAN.finditer(text):
        if m.start() <= index < m.end():
            return False
    return True


def lint_placeholders(rep: Report, name: str, text: str, *, skip_fences: bool = True) -> None:
    """Every line the DM prompt would silently drop, reported with its reason.

    Helper lines and empty tables are exempt. The digest drops them on purpose  - 
    they are the italic instructions to the GM and the pipes of a table - so
    flagging them would bury the real gaps under the structure of the template.
    What is left is content the GM wrote the heading for and then never filled.
    """
    offsets, pos = [], 0
    for line in text.splitlines():
        offsets.append(pos)
        pos += len(line) + 1
    for i, line in enumerate(text.splitlines(), start=1):
        reason = context.is_template_line(line)
        if not reason or reason.startswith("helper text") or reason == "empty table":
            continue
        if skip_fences and not _outside_fences(text, offsets[i - 1]):
            continue
        rep.add("warn", name, f"{reason}: {line.strip()[:70]}", line=i,
                hint="fill it, or delete the line if it does not apply")


def lint_state(rep: Report, text: str) -> None:
    present = {m.group(1).strip() for m in _HEAD2.finditer(text)}
    for section, why in REQUIRED_STATE_SECTIONS.items():
        if section not in present:
            rep.add("error", "state.md", f"missing section '## {section}'",
                    hint=f"{why} - add the heading back exactly")

    # The header line is regex-parsed by name_registry and paths; a field that
    # lost its label is a field nothing can read any more.
    head = text.split("\n## ", 1)[0]
    fields = {k.strip(): v.strip() for k, v in _HEADER_FIELD.findall(head)}
    for label in ("Created", "Last session", "Session count", "System Module",
                  "System Version"):
        if label not in fields:
            rep.add("error", "state.md", f"header field '**{label}:**' is missing or unlabelled",
                    line=2,
                    hint="the header is one line: **Created:** ...  **Session count:** N  ...")
    count = fields.get("Session count", "")
    if count and not re.fullmatch(r"\d+", count):
        rep.add("error", "state.md", f"session count {count!r} is not a number", line=2,
                hint="/gm save increments it; keep it an integer")
    for key in ("Created", "Last session", "Session count"):
        if re.search(rf"\*\*{key}:\*\*\s*<[^>]+>", text):
            rep.add("warn", "state.md", f"header field '**{key}:**' is still a placeholder",
                    line=2, hint="fill it in, or the name registry cannot date the campaign")
    # The System Version field is an opaque ruleset string the GM stamps ("2014",
    # "1e") - nothing here can judge the value, only that it was left as the
    # template's placeholder. Absent is migrate_system_version.py's business:
    # `python3 scripts/migrate_system_version.py <campaign> --check`.
    if fields.get("System Version", "").startswith("<"):
        rep.add("warn", "state.md", "**System Version:** is still the template placeholder",
                line=2,
                hint=f"python3 scripts/migrate_system_version.py {rep.campaign} "
                     "--version 2014")

    lint_placeholders(rep, "state.md", text)

    # The arc is the one place a YAML block is parsed by a human, so it is the
    # one place a syntax error is silent until the GM reads it mid-session.
    # Scoped to the Campaign Arc section: other sections (World Queue) carry
    # their own yaml fence, and the first fence in the file is not the arc.
    arc_at = re.search(r"^## Campaign Arc[ \t]*$", text, re.M)
    fence = _FENCE.search(text, arc_at.end() if arc_at else 0)
    if not fence:
        rep.add("warn", "state.md", "no ```yaml block under ## Campaign Arc",
                hint="/gm new writes one; a sandbox campaign sets `type: sandbox`")
    else:
        lint_arc(rep, fence.group(1), text[:fence.start()].count("\n") + 1)


def lint_arc(rep: Report, body: str, line: int) -> None:
    try:
        import yaml
    except ImportError:
        rep.add("warn", "state.md", "Campaign Arc YAML check SKIPPED: PyYAML not installed",
                line=line, hint="pip3 install pyyaml")
        return
    try:
        doc = yaml.safe_load(body)
    except yaml.YAMLError as e:
        # The arc block is hand-written and nobody parses it until the GM reads it
        # mid-session. A syntax error found by a linter is worth several found at
        # the table, so it is reported rather than raised.
        problem = str(getattr(e, "problem", e)).split("\n")[0]
        mark = getattr(e, "problem_mark", None)
        rep.add("error", "state.md", f"Campaign Arc YAML does not parse: {problem}",
                line=line + (mark.line if mark else 0),
                hint="the block is read by eye only, so this fails silently in play")
        return
    if not isinstance(doc, dict):
        rep.add("error", "state.md", f"Campaign Arc is {type(doc).__name__}, not a mapping",
                line=line, hint="the fenced block must be a YAML mapping")
        return
    kind = doc.get("type")
    if kind not in ("dynamic", "structured", "sandbox"):
        rep.add("warn", "state.md",
                f"arc type is {kind!r}, not dynamic | structured | sandbox", line=line,
                hint="the GM cannot tell which arc format this is")
    if kind != "sandbox":
        beat = doc.get("current_beat")
        if not beat:
            rep.add("warn", "state.md", "arc has no current_beat", line=line,
                    hint="without it /gm arc cannot advance")
        elif doc.get("outstanding_beats") and beat not in (doc.get("outstanding_beats") or []):
            rep.add("warn", "state.md",
                    f"current_beat {beat!r} is not in outstanding_beats", line=line,
                    hint="a beat that is both current and outstanding is ambiguous")
    for act in doc.get("acts") or []:
        if isinstance(act, dict) and not act.get("beats"):
            rep.add("warn", "state.md", f"act {act.get('act')!r} has no beats", line=line,
                    hint="an empty act never fires")


def lint_world(rep: Report, text: str) -> None:
    present = {m.group(1).strip() for m in _HEAD2.finditer(text)}
    for section in WORLD_SECTIONS:
        if section not in present:
            rep.add("warn", "world.md", f"missing section '## {section}'",
                    hint="world.md is what the DM checks facts against; "
                         "a missing one is a hole in its memory")
    lint_placeholders(rep, "world.md", text)


def lint_npcs(rep: Report, text: str) -> None:
    if NPC_INDEX_HEADER not in text:
        rep.add("warn", "npcs.md", "no NPC index table",
                hint=f"the index is the one-line-per-NPC table: {NPC_INDEX_HEADER}")
    entries = [m.group(1).strip() for m in re.finditer(r"^### +(.+?)\s*$", text, re.MULTILINE)]
    named = [e for e in entries if not e.startswith("Personality")
             and not e.startswith("Relationships") and not e.startswith("Notes")]
    if not named:
        rep.add("warn", "npcs.md", "no ### NPC entries",
                hint="one '### <Name>' block per NPC")
    # A table that disagrees with the entries below it is the failure that
    # matters: /gm npc adds to one and forgets the other.
    rows = [ln for ln in text.splitlines() if ln.strip().startswith("|")
            and NPC_INDEX_HEADER.split("|")[1].strip() not in ln
            and not re.fullmatch(r"\|[\s|:-]*\|", ln.strip())]
    indexed = {ln.split("|")[1].strip() for ln in rows if len(ln.split("|")) > 2}
    indexed = {i for i in indexed if i and not i.startswith("<")}
    for name in indexed - set(named):
        rep.add("warn", "npcs.md", f"index row {name!r} has no '### {name}' entry",
                hint="the index and the entries drifted apart")
    lint_placeholders(rep, "npcs.md", text)


def lint_sheet(rep: Report, path: pathlib.Path, text: str) -> None:
    name = f"characters/{path.name}"
    present = {m.group(1).strip() for m in re.finditer(r"^#{1,3} +(.+?)\s*$", text, re.MULTILINE)}
    for section in SHEET_SECTIONS:
        if section not in present:
            rep.add("error", name, f"missing section '## {section}'",
                    hint="context.sheet_digest and the display sidebar read these")
    fields = dict(_HEADER_FIELD.findall(text))
    for label in ("HP", "AC", "Level", "Class", "Race"):
        value = (fields.get(label) or "").strip()
        if not value or value.startswith("<"):
            rep.add("warn", name, f"**{label}:** is {'empty' if not value else 'a placeholder'}",
                    hint="party_stats() in context.py reads this for the sidebar")
    m = re.search(r"\*\*HP:\*\*\s*(\d+)\s*/\s*(\d+)", text)
    if not m:
        rep.add("warn", name, "no '**HP:** current / max' pair",
                hint="the sidebar parses HP as two integers")
    elif int(m.group(1)) > int(m.group(2)):
        rep.add("error", name, f"HP {m.group(1)}/{m.group(2)}: current exceeds max", line=None)
    lint_placeholders(rep, name, text, skip_fences=False)


# Fantasy Statblocks ships as the community plugin id "obsidian-5e-statblocks",
# whatever the marketplace calls it. 4.x moved the bestiary location out of the
# note body and into this setting, so the export is worthless without it.
_STATBLOCK_PLUGIN = "obsidian-5e-statblocks"
_STATBLOCK_SETTINGS = f".obsidian/plugins/{_STATBLOCK_PLUGIN}/data.json"
# Where export_bestiary.py writes, and what session-workflow.md tells you to set
# as the plugin's Bestiary Folder.
_STATBLOCK_BESTIARY = "Bestiary"
_STATBLOCK_FENCE = re.compile(r"^```statblock\b", re.MULTILINE)
# The plugin's own default. It means "every folder in the vault", which is the
# bug this check exists for: session-workflow.md says to set Bestiary Folder to
# Bestiary/, nothing read the setting back, and the shipped config kept the
# default. The plugin then parsed world.md, arc.md and the session log as
# creatures, so the Bestiary view was 40 non-statblocks around 371 real ones.
_STATBLOCK_DEFAULT_PATH = "/"


def lint_statblocks(rep: Report, path: pathlib.Path) -> None:
    """The Fantasy Statblocks plugin config, read the way the plugin reads it.

    export_bestiary.py writing 334 correct notes proves nothing: the plugin only
    parses notes inside its configured Bestiary Folder, and that setting lives in
    the vault, written by a human, in a plugin folder the export never touches.
    """
    settings_file = path / _STATBLOCK_SETTINGS
    if not settings_file.is_file():
        # Only worth saying when there is a bestiary to render. A campaign that
        # never ran the export has nothing to point the plugin at.
        if (path / _STATBLOCK_BESTIARY).is_dir():
            rep.add("warn", _STATBLOCK_SETTINGS, "Bestiary/ exists but Fantasy Statblocks is not installed",
                    hint="install the plugin to read the bestiary in Obsidian; "
                         "nothing at the table depends on it")
        return
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        rep.add("error", _STATBLOCK_SETTINGS, f"unreadable plugin settings: {e}",
                hint="Obsidian rewrites this file on quit; if it is corrupt, delete it "
                     "and set the plugin's options again")
        return

    paths = [str(p).strip("/") for p in settings.get("paths") or []]
    if not paths:
        rep.add("error", _STATBLOCK_SETTINGS, "Bestiary Folder is empty",
                hint="the plugin then parses no notes at all")
        return
    if any(p == "" for p in paths):
        rep.add("error", _STATBLOCK_SETTINGS,
                f'Bestiary Folder is "{_STATBLOCK_DEFAULT_PATH}" (the plugin default)',
                hint='that is the whole vault, so the plugin parses world.md, arc.md and '
                     'the session log as creatures and buries the real bestiary; '
                     'set it to "Bestiary/"')
        return

    # Each configured folder must exist and actually hold statblocks, otherwise
    # the setting is a typo that renders as an empty bestiary with no error.
    for folder in paths:
        d = path / folder
        if not d.is_dir():
            rep.add("error", f"{_STATBLOCK_SETTINGS} -> {folder}",
                    "Bestiary Folder does not exist in the vault",
                    hint="run scripts/export_bestiary.py --out <campaign> to create it")
            continue
        notes = sorted(d.glob("*.md"))
        if not notes:
            rep.add("warn", folder, "Bestiary Folder is empty",
                    hint="python3 scripts/export_bestiary.py --out <campaign>")
            continue
        without = [n.name for n in notes if not _STATBLOCK_FENCE.search(n.read_text(encoding="utf-8"))]
        if without:
            shown = ", ".join(without[:5]) + ("..." if len(without) > 5 else "")
            rep.add("warn", folder,
                    f"{len(without)} of {len(notes)} notes have no ```statblock block: {shown}",
                    hint="the plugin lists them as creatures with nothing to render")
        _lint_token_art(rep, path, folder, notes)


def _lint_token_art(rep: Report, path: pathlib.Path, folder: str,
                    notes: list[pathlib.Path]) -> None:
    """How much of the bestiary has a portrait in the Atlas vault.

    Atlas wants one real image per token, so a parsed statblock with no picture
    still shows as "needs attention" in Create tokens. The bestiary is 371 notes
    and the art packs that exist cover a small fraction of it, so the number is
    reported as a ratio rather than left to be rediscovered by counting.

    Deliberately not an error. A creature with no portrait still plays; the
    engine's colour disc is the honest fallback, and a missing picture is not a
    broken campaign. Only a spawn the *maps* actually use could be an error, and
    that check belongs with the maps, not here.
    """
    art_dir = path / "atlas-vtt" / "assets" / "bestiary"
    if not art_dir.is_dir():
        return
    creatures = [n for n in notes if n.stem.lower() != "readme"]
    if not creatures:
        return
    have = [n for n in creatures
            if any((art_dir / f"{slug(n.stem)}{s}").exists() for s in IMAGE_SUFFIXES)]
    pct = round(100 * len(have) / len(creatures))
    rep.add("warn", f"{folder}/ (art)",
            f"{len(have)} of {len(creatures)} creatures have a portrait ({pct}%)",
            hint=f"{art_dir.relative_to(path)} holds the matched art; the rest render "
                 "as a colour disc, which is playable and not a broken image")


def lint_campaign(name: str, path: pathlib.Path | None = None) -> Report:
    path = pathlib.Path(path) if path else find_campaign(name, migrate=False)
    rep = Report(name, path)
    if not path.is_dir():
        rep.add("error", str(path), "campaign folder not found")
        return rep

    for required in ("state.md", "world.md", "npcs.md"):
        f = path / required
        if not f.exists():
            rep.add("error", required, "missing",
                    hint="/gm new writes it; /gm load reads it")
    for required in ("state.md", "world.md", "npcs.md"):
        f = path / required
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except OSError as e:
            rep.add("error", required, f"unreadable: {e}")
            continue
        if not text.strip():
            rep.add("error", required, "file is empty")
            continue
        {"state.md": lint_state, "world.md": lint_world, "npcs.md": lint_npcs}[required](rep, text)

    sheets = sorted((path / "characters").glob("*.md")) if (path / "characters").is_dir() else []
    if not sheets:
        rep.add("warn", "characters/", "no character sheet",
                hint="/gm character new writes one; the sidebar has nothing to show without it")
    for sheet in sheets:
        try:
            lint_sheet(rep, sheet, sheet.read_text(encoding="utf-8"))
        except OSError as e:
            rep.add("error", f"characters/{sheet.name}", f"unreadable: {e}")
    lint_statblocks(rep, path)
    return rep


def _print(rep: Report, strict: bool) -> None:
    if not rep.findings:
        print(f"{rep.campaign}: clean ({rep.path})")
        return
    print(f"{rep.campaign}: {rep.errors} error(s), {rep.warnings} warning(s)  [{rep.path}]")
    for f in rep.findings:
        where = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
        print(f"  [{f['level']:5s}] {where}  {f['message']}")
        if f["hint"]:
            print(f"            -> {f['hint']}")


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        prog="campaign_lint.py", description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("campaign", nargs="?", help="campaign name (omit with --all)")
    ap.add_argument("--all", action="store_true", help="lint every campaign in the root")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    args = ap.parse_args(argv)

    if not args.campaign and not args.all:
        ap.error("give a campaign name, or --all")

    if args.campaign and args.all:
        ap.error("give a campaign name or --all, not both")
    if args.campaign and (".." in args.campaign or "/" in args.campaign
                          or "\\" in args.campaign):
        ap.error("campaign name must not contain path separators or '..'")

    if args.campaign:
        names = [args.campaign]
    else:
        root = campaigns_dir()
        if not root.is_dir():
            print(f"campaigns directory not found: {root}", file=sys.stderr)
            return 2
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
    reports = [lint_campaign(n) for n in names]
    missing = [r for r in reports if not r.path.is_dir()]

    if args.json:
        print(json.dumps([r.as_dict() for r in reports], indent=1, ensure_ascii=False))
    else:
        for r in reports:
            _print(r, args.strict)

    if missing:
        return 2
    worst = any(r.errors or (args.strict and r.warnings) for r in reports)
    return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
