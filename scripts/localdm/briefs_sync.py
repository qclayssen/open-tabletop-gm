#!/usr/bin/env python3
"""briefs_sync.py: the advisor briefs have one source of truth. The other copy is generated.

    python3 scripts/localdm/briefs_sync.py            # copy the briefs into agents/, record hashes
    python3 scripts/localdm/briefs_sync.py --check    # fail if the two copies differ
    python3 scripts/localdm/briefs_sync.py --agents-dir /path/to/dnd-gm/agents

PR #108. The argument below is the one that chose a side; a reader who wants
to disagree with it should find the argument first and the code second.

WHY THIS EXISTS
---------------
The advisor briefs used to exist in two places with nothing between them:
`agents/*.md` in the outer dnd-gm repo, where `scripts/install_agents.py`
installs them as standalone CLI agents, and
`scripts/localdm/prompts/advisors/*.md` here, which `advisor.py` reads at
runtime. Nothing reconciled them, and they had already drifted twice: the
`_shared.md` Mechanics and stall-phrasing rules were added here and never
reached the outer copy, and the two solo-play rules in `tactician.md` were
added in the outer repo and never reached this one. Both copies were live, so
each drift was a rule the GM was silently denied on one side of the table.

WHICH SIDE IS THE SOURCE OF TRUTH
---------------------------------
This repo. The argument is not that the outer repo is unimportant, it is
where the roadmap and the dev-council briefs live and where the sync has to
write. The argument is that `advisor.py` reads the copy here, on the code
path that actually runs at the table, and that this is the repo where the
briefs are tested, reviewed and versioned with the code that ships them. A
rule that is enforced by an import at runtime is authority; a rule that is
remembered by a shell script is a convention. So the briefs live here and
`agents/*.md` is a build artifact, exactly like `display/static/reference/`
being a copied page.

The rejected counter-argument, stated fairly: the briefs are the shared brief
of a council consulted from both repos, and `install_agents.py` is the only
thing that puts them in front of an agent at all, so the outer repo is where
the council is really used. Choosing the outer repo as the source would mean
this repo syncs *inbound* on every run, which inverts the dependency: the
runtime would depend on a directory that is not in this repo, is not in this
repo's history, and is absent from a plain clone or a CI runner. The tests
could then only be written by skipping themselves. That is the losing side of
the trade, and the trade was made knowingly.

THE MECHANISM
-------------
Two layers, because one of them alone does not close the hole.

1. `sync()` generates: it copies each brief over the outer copy, so the outer
   copy cannot be hand-edited without being overwritten.
2. A committed hash manifest, `prompts/advisors.outer.json`, makes the drift
   visible *without* the outer repo. CI clones this repo alone, so a check
   that only works when both checkouts sit side by side on one machine is a
   check that never runs where a mistake would be caught. The manifest
   records the hash of the outer copy as the last sync left it, so editing a
   brief here and forgetting to sync fails the suite in CI. When both
   checkouts are present, `--check` additionally compares the live bytes and
   the live file set.

THE OUTER SIDE, WHICH IS NOT IN THIS REPO
-----------------------------------------
`scripts/install_agents.py` lives in the outer repo and copies `agents/*.md`
into `~/.opencode/agents/` or `~/.claude/agents/`. It is the only path by
which these briefs reach a CLI agent, so the generation has to be invoked
from there for the two-repo workflow to be safe by hand rather than by
memory. It is not edited here, because this repo cannot change the outer
repo, and a script in one repo cannot edit another. The change belongs in the
outer repo, next to `install_agents.py`, and is one line of behaviour:
resolve the sibling checkout, run this module first, then install. Proposed
shape, for whoever takes that half:

    subprocess.run([sys.executable,
                    str(code_repo / "scripts/localdm/briefs_sync.py"),
                    "--agents-dir", str(agents_dir)], check=True)

Without it the mechanism is still sound, because `--check` is a test and a
test is the thing that stops being optional. With it, the outer copy is
regenerated as a side effect of installing, so nobody has to remember.

WHAT IS NOT SYNCHRONISED, AND WHY
---------------------------------
`agents/` also holds the dev-council half: `DEV-ADVISORS.md` plus
architect, engineer, security, steward and verifier. Those advise the
development process, they are not in `advisor.ADVISORS`, and nothing at
runtime reads them. Copying them here would make them look like runtime
advisors, and deleting them from the outer repo would drop content that
`install_agents.py` installs as real CLI agents. They are declared in
`DEV_COUNCIL` instead, and a file in `agents/` that is neither a brief nor a
declared dev-council brief is a failure, so a new play-facing advisor cannot
be added on one side only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys

if __package__ in (None, ""):                        # run as a script
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import localdm                                    # noqa: F401  (puts scripts/ on sys.path)

from localdm import advisor                         # noqa: E402

#: The source of truth. `advisor.brief()` reads this directory at runtime.
BRIEFS = pathlib.Path(__file__).resolve().parent / "prompts" / "advisors"

#: The repo this file lives in, used to find the sibling outer checkout.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Hashes of the outer copy as the last sync left it. Committed so the check
#: runs in CI, which has no outer checkout.
MANIFEST = BRIEFS.parent / "advisors.outer.json"

#: The engineering half of the council, outer-repo only by design (see above).
DEV_COUNCIL = ("DEV-ADVISORS.md", "architect.md", "engineer.md", "security.md",
               "steward.md", "verifier.md")

#: Prepended to every brief by `advisor.brief()`; it drifts like any other.
SHARED = "_shared"

EXT = ".md"


def _stem(name: str) -> str:
    return name[: -len(EXT)] if name.endswith(EXT) else name

#: Overrides discovery of the outer `agents/` directory.
ENV_AGENTS_DIR = "GM_OUTER_AGENTS_DIR"


def brief_names(briefs: pathlib.Path = BRIEFS) -> tuple[str, ...]:
    """Every brief filename, derived from the directory, never hand-kept.

    A hand-kept list of advisor names is the bug this whole module exists to
    fix; a hand-kept list of brief filenames would be the same bug again.
    """
    return tuple(sorted(p.name for p in briefs.glob("*.md")))


def outer_agents_dir(agents_dir: str | pathlib.Path | None = None) -> pathlib.Path | None:
    """The outer repo's `agents/`, or None when it is not there.

    Discovery is a sibling checkout, because that is how the two repos sit on
    a dev machine. A plain clone of this repo has no sibling, which is why the
    manifest exists: CI cannot answer this question at all.
    """
    if agents_dir is not None:
        path = pathlib.Path(agents_dir)
        return path if path.is_dir() else None
    env = os.environ.get(ENV_AGENTS_DIR)
    if env:
        path = pathlib.Path(env)
        return path if path.is_dir() else None
    sibling = REPO_ROOT.parent / "agents"
    return sibling if sibling.is_dir() else None


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(path: pathlib.Path = MANIFEST) -> dict:
    """The recorded outer copy, or {} when nothing has been recorded yet."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(agents_dir: pathlib.Path, path: pathlib.Path = MANIFEST) -> dict:
    """Record the outer copy's hashes so CI can check the sync without it.

    Only files this module understands are recorded: a brief, or a declared
    dev-council brief. Anything else in `agents/` is reported by `check()`
    rather than blessed here.
    """
    names = brief_names()
    data = {
        "agents_dir": "dnd-gm/agents",
        "briefs": {n: sha256(agents_dir / n) for n in names},
        "dev_council": {n: sha256(agents_dir / n) for n in DEV_COUNCIL
                        if (agents_dir / n).exists()},
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def council_problems(briefs: pathlib.Path = BRIEFS) -> list[str]:
    """The brief directory has to be exactly the council `advisor.py` can ask.

    This is the check that survives a clone of this repo on its own, and it is
    the reason the outer copy cannot be the source of truth: the list of who
    exists is in the code, next to the code that reads the files.
    """
    problems = []
    want = set(advisor.ADVISORS) | {SHARED}
    have = {_stem(name) for name in brief_names(briefs)}
    for name in sorted(want - have):
        problems.append(f"{name} is in advisor.ADVISORS but has no {EXT}brief in {briefs}")
    for name in sorted(have - want):
        problems.append(f"{name}{EXT} is a brief nobody can ask for: not in advisor.ADVISORS")
    return problems


def compare(briefs: pathlib.Path, agents_dir: pathlib.Path) -> list[str]:
    """Every way the two copies disagree, as human-readable lines.

    Empty means in sync. Reports a brief the outer copy is missing, a brief
    whose bytes differ, a brief the manifest does not know about, and a file
    in `agents/` that is neither a brief nor declared dev-council.
    """
    problems: list[str] = []
    outer = {p.name for p in agents_dir.glob("*.md")}
    for name in brief_names(briefs):
        target = agents_dir / name
        if not target.exists():
            problems.append(f"missing from {agents_dir}: {name}")
        elif target.read_bytes() != (briefs / name).read_bytes():
            problems.append(f"drifted: {name} differs from the brief in {briefs}")
    for name in sorted(outer - set(brief_names(briefs))):
        if name not in DEV_COUNCIL:
            problems.append(f"unrecognised file in {agents_dir}: {name}")
    for name in DEV_COUNCIL:
        if name not in outer:
            problems.append(f"declared dev-council brief absent from {agents_dir}: {name}")
    return problems


def check_manifest(briefs: pathlib.Path = BRIEFS, path: pathlib.Path = MANIFEST) -> list[str]:
    """Manifest problems, checkable with no outer checkout at all."""
    data = read_manifest(path)
    if not data:
        return [f"no hash manifest at {path}: run scripts/localdm/briefs_sync.py"]
    recorded = data.get("briefs", {})
    problems: list[str] = []
    for name in brief_names(briefs):
        want = sha256(briefs / name)
        got = recorded.get(name)
        if got is None:
            problems.append(f"{name} is not in {path.name}: the outer copy was never synced")
        elif got != want:
            problems.append(f"{name} changed since the last sync: {path.name} records the "
                            "outer copy, run scripts/localdm/briefs_sync.py to refresh it")
    for name in sorted(set(recorded) - set(brief_names(briefs))):
        problems.append(f"{path.name} records {name}, which is not a brief any more")
    for name in sorted(data.get("dev_council", {})):
        if name not in DEV_COUNCIL:
            problems.append(f"{path.name} blesses {name} as dev-council, which is not declared")
    for name in DEV_COUNCIL:
        if name not in data.get("dev_council", {}):
            problems.append(f"{name} is declared dev-council but absent from {path.name}")
    return problems


def sync(briefs: pathlib.Path = BRIEFS, agents_dir: pathlib.Path | None = None,
         manifest: pathlib.Path = MANIFEST) -> list[str]:
    """Generate the outer copy from the briefs, then record what it now holds."""
    # Routed through outer_agents_dir so a path that is not a directory fails
    # the same way an absent one does, instead of half-copying into nothing.
    resolved = outer_agents_dir(agents_dir)
    if resolved is None:
        raise ValueError(f"no outer agents/ directory at {agents_dir!r}; pass --agents-dir "
                         f"or set {ENV_AGENTS_DIR}")
    agents_dir = resolved
    written: list[str] = []
    for name in brief_names(briefs):
        src = briefs / name
        dst = agents_dir / name
        if not dst.exists() or dst.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dst)
            written.append(name)
    write_manifest(agents_dir, manifest)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report drift and exit non-zero; write nothing")
    parser.add_argument("--agents-dir", default=None,
                        help=f"the outer repo's agents/ (default: sibling checkout, or ${ENV_AGENTS_DIR})")
    args = parser.parse_args(argv)

    if not args.check:
        try:
            written = sync(agents_dir=args.agents_dir)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        print(f"synced {len(written)} brief(s) into the outer agents/: "
              f"{', '.join(written) if written else 'already identical'}")

    # Recomputed after the sync, so a successful run reports on the state it
    # left rather than the state it found.
    problems = council_problems() + check_manifest()
    agents_dir = outer_agents_dir(args.agents_dir)
    if agents_dir is None:
        if args.check:
            print("note: no outer agents/ directory found, so the live copies were not "
                  "compared. The manifest check above still applies.")
    else:
        problems += compare(BRIEFS, agents_dir)
        print(f"compared against {agents_dir}")

    for line in problems:
        print(f"  drift: {line}")
    if problems:
        print(f"{len(problems)} problem(s). Run scripts/localdm/briefs_sync.py to fix.")
        return 1
    print("advisor briefs are in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
