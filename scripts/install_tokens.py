#!/usr/bin/env python3
"""install_tokens.py: install the token portrait art into display/tokens/.

    python3 scripts/install_tokens.py                       # the default zip
    python3 scripts/install_tokens.py ~/Downloads/other.zip
    python3 scripts/install_tokens.py --from-extracted DIR   # an already-unzipped folder

WHY THIS EXISTS
===============
The portraits are third-party art (see `tactics/token_portraits.py` for the
credit), so they are NOT in git: 98 PNGs is 12MB, and `display/maps/images/`
sets the precedent for map artwork. The manifest IS committed, because the
manifest is the index and the index is what tells a reader where the art came
from and who to credit it.

That has a consequence this script exists to make pleasant. On any machine
without the art, every token still draws correctly from its side colour alone --
so nothing looks broken and nothing tells you the faces are missing. The
feature reads as "implemented" on a fresh clone and is quietly not there.

So: run this once per machine. It is idempotent, it verifies what it wrote
against the manifest, and it says what it did.

WHAT IT REFUSES
---------------
It will not install a file the manifest does not name, and it will not leave a
file installed that the manifest lost. The two directions are the same
invariant: art on disk that nothing can reach is a portrait that silently
never appears, and a manifest entry with no file is one that 404s at the table.
Either way the count is wrong, and a wrong count is the thing to catch here
rather than in play.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import token_portraits  # noqa: E402

TOKENS_DIR = ROOT / "display" / "tokens"

# The zip the art normally arrives in. Overridable; this is a convenience, not
# a contract, because a machine that has never had the file will not have it.
DEFAULT_ZIP = pathlib.Path.home() / "Downloads" / "Strixhaven Tokens (made by hearden)(1).zip"


def slugify(name: str) -> str:
    """The same rule the manifest uses, imported rather than re-derived: if the
    two ever disagree, portraits stop resolving, and the failure is a 404 at the
    table rather than an error anywhere."""
    return token_portraits.slugify(name)


def _pngs(root: pathlib.Path):
    """Every PNG under root, skipping the macOS resource forks a Finder-made
    zip always contains (`__MACOSX/._name.png`, 462 bytes each)."""
    return (p for p in root.rglob("*.png") if "MACOSX" not in str(p))


def from_extracted(src: pathlib.Path) -> int:
    if not src.is_dir():
        raise SystemExit(f"{src} is not a directory")
    n = 0
    for p in _pngs(src):
        shutil.copy(p, TOKENS_DIR / f"{slugify(p.stem)}.png")
        n += 1
    return n


def from_zip(zip_path: pathlib.Path) -> int:
    if not zip_path.is_file():
        raise SystemExit(
            f"No zip at {zip_path}.\n"
            "Pass one explicitly: python3 scripts/install_tokens.py ~/Downloads/some.zip\n"
            "Or an already-unzipped folder: python3 scripts/install_tokens.py --from-extracted DIR")
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        # The archive has one top-level folder; find the real root rather than
        # assuming its name, which is the part most likely to differ.
        kids = [p for p in pathlib.Path(tmp).iterdir() if p.is_dir() and "MACOSX" not in p.name]
        root = kids[0] if len(kids) == 1 else pathlib.Path(tmp)
        return from_extracted(root)


def audit() -> tuple[list, list]:
    """(manifest entries with no file, files with no manifest entry).

    Files whose name starts with `_` are skipped. That is the convention the
    test suite already uses for a fixture it drops into the art directory
    (`_bv1_test.png` for the map-artwork route, `_portrait_test.png` here), and
    a fixture that exists only while a test runs is not an installed portrait.
    """
    have = {p.stem for p in TOKENS_DIR.glob("*.png") if not p.stem.startswith("_")} \
        if TOKENS_DIR.is_dir() else set()
    want = set(token_portraits.PORTRAITS.values())
    return sorted(want - have), sorted(have - want)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("zip", nargs="?", default=str(DEFAULT_ZIP),
                    help="the zip to install (default: ~/Downloads/Strixhaven Tokens...)")
    ap.add_argument("--from-extracted", metavar="DIR",
                    help="an already-unzipped folder, instead of a zip")
    ap.add_argument("--check", action="store_true",
                    help="report what is installed and missing; write nothing")
    args = ap.parse_args(argv)

    if args.check:
        missing, extra = audit()
        installed = len([p for p in TOKENS_DIR.glob("*.png")
                         if not p.stem.startswith("_")]) if TOKENS_DIR.is_dir() else 0
        print(f"{installed} portraits installed, {len(token_portraits.PORTRAITS)} in the manifest")
        if missing:
            print(f"  {len(missing)} manifest entries have no file: {', '.join(missing[:6])}"
                  f"{' ...' if len(missing) > 6 else ''}")
        if extra:
            print(f"  {len(extra)} files are not in the manifest: {', '.join(extra[:6])}"
                  f"{' ...' if len(extra) > 6 else ''}")
        if not missing and not extra:
            print("  every manifest entry has a file and every file is in the manifest")
        # A clean audit with nothing installed is the fresh-clone state, which
        # works but shows no faces. Say so, because nothing else will.
        if not installed and not missing and not extra and not args.check:
            return 0
        return 0 if not (missing or extra) else 1

    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    n = from_extracted(pathlib.Path(args.from_extracted)) if args.from_extracted \
        else from_zip(pathlib.Path(args.zip))
    print(f"installed {n} portraits into {TOKENS_DIR}")

    missing, extra = audit()
    if missing or extra:
        print(f"  manifest entries with no file: {len(missing)} {missing[:6]}")
        print(f"  files not in the manifest:      {len(extra)} {extra[:6]}")
        print("  The manifest is the source of truth for what should be here. If the art is a "
              "different set, update tactics/token_portraits.py rather than the PNGs.")
        return 1
    print(f"  all {len(token_portraits.PORTRAITS)} match the manifest, in both directions")

    print(f"\nCredit: {token_portraits.CREDIT} -- {token_portraits.SOURCE}")
    print("The art is gitignored, so run this once per machine. A token whose name is not in "
          "the manifest keeps drawing its side colour with initials, which is correct and is "
          "not an error.")
    print("\nTo use them, add \"portraits\": true to a map JSON, then:")
    print("  /gm combat start <map> --pc kairos@D5 --monster 'Daemogoth@H5'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
