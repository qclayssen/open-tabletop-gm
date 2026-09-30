#!/usr/bin/env python3
"""register_tokens.py: make a folder of portraits importable by Atlas VTT.

Why this exists
---------------
Dropping PNGs into the Atlas vault is not enough. Atlas lists a token in its
asset library, and in the "Create tokens" panel that
session-workflow.md points at, from an entry in

    atlas-vtt/.atlas-data/assets-metadata.json

and a file on disk is not an entry. A vault full of correct, correctly named
portraits still shows every token as "Missing image", which is the exact
symptom the Bestiary Folder bug produced and the reason that one was worth
chasing rather than guessing at.

So there are two halves and both are required:

  1. the picture, copied somewhere inside the vault, and
  2. a metadata entry naming it, with a thumbnail beside it.

map_to_atlas.py deliberately writes neither half of (2): it writes only scene
files and never touches assets-metadata.json, so a half-finished run cannot
corrupt an index Atlas owns. That is right for a map export, which has no art
to register. It is not a gap here, where the art is the point.

The thumbnail is not optional decoration. Atlas's own picker is
`atlas-vtt/assets/thumbnails/<basename>-<fnv1a(imagePath)>.webp`, where the
hash is over the *vault-relative imagePath string*. A thumbnail at any other
name is not found, and the token renders with a blank swatch. The hash here is
FNV-1a/32, verified against the five thumbnails a working vault already had.

Usage
-----
    python3 scripts/register_tokens.py --vault ~/vault --art DIR [--collection NAME]
    python3 scripts/register_tokens.py --vault ~/vault --list
    python3 scripts/register_tokens.py --vault ~/vault --art DIR --dry-run

Idempotent: re-running updates entries in place and never duplicates them, so it
is safe in a loop. assets-metadata.json is written atomically, because it is a
shared document and a half-written index costs the whole asset library.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from utf8io import read_text, write_text

IMAGE_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")
METADATA_REL = "atlas-vtt/.atlas-data/assets-metadata.json"
THUMBNAIL_DIR = "atlas-vtt/assets/thumbnails"
# Atlas's own thumbnail box (T3e in its bundle). Square art is not resized,
# which is why the 256px portraits this project generates pass through intact.
THUMBNAIL_MAX = 256
DEFAULT_COLLECTION = "Strixhaven"
# The tag group the vault already uses for class tokens, so registered art
# lands beside them in the picker instead of in an untagged pile.
DEFAULT_TAGS = ["open-tabletop-gm", "bestiary"]


class Refused(Exception):
    """A condition that stops the run rather than being worked around."""


def fnv1a_32(text: str) -> str:
    """The 8-hex-digit suffix Atlas uses for a thumbnail filename.

    FNV-1a, 32-bit, over the *vault-relative* imagePath. JS `Math.imul` is a
    32-bit multiply, so the intermediate product must be masked to 32 bits here
    too or Python's unbounded ints give a different hash.
    """
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return format(h, "08x")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def vault_relative(vault: pathlib.Path, path: pathlib.Path) -> str:
    """The path string Atlas stores, e.g. atlas-vtt/collections/X/tokens/y.png.

    Atlas's vault root is the *parent* of the atlas-vtt directory, which is why
    the stored path keeps the atlas-vtt/ prefix even though the vault argument
    points inside it.
    """
    return path.resolve().relative_to(vault.resolve()).as_posix()


try:  # Pillow is optional, and register() needs to know that without asking per image.
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def resize_for_thumbnail(src: pathlib.Path, dest: pathlib.Path) -> bool:
    """Write a webp thumbnail beside the original, Atlas's way.

    Returns True when a resizer was available. A missing one is reported, not
    fatal: Atlas regenerates a missing thumbnail on its own next time it sees
    the asset, so the token still works without it.
    """
    if not HAVE_PIL:
        return False
    with Image.open(src) as im:
        w, h = im.size
        if max(w, h) > THUMBNAIL_MAX:
            scale = THUMBNAIL_MAX / max(w, h)
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                           Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # quality=high matches the `resizeQuality: "high"` in Atlas's own
        # createImageBitmap call, so our file and a regenerated one agree.
        im.save(dest, "WEBP", quality=90, method=4)
    return True


def load_metadata(vault: pathlib.Path) -> dict:
    f = vault / METADATA_REL
    if not f.is_file():
        raise Refused(
            f"{METADATA_REL} is missing, so this is not an Atlas vault, or Atlas "
            f"has not been opened in it yet. Open the vault in Obsidian once "
            f"(Atlas writes the file on first run) and try again."
        )
    try:
        data = json.loads(read_text(f))
    except ValueError as e:
        raise Refused(f"{METADATA_REL} is not valid JSON ({e}). Refusing to "
                      f"overwrite it: this file is the whole asset library.")
    for key in ("assets", "collections"):
        if key not in data:
            raise Refused(f"{METADATA_REL} has no {key!r} key; this is not the "
                          f"Atlas index this script was written for.")
    return data


def write_metadata(vault: pathlib.Path, data: dict) -> None:
    """Atomic. The index is a shared document and a torn write loses it all."""
    f = vault / METADATA_REL
    write_text(f, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def register(vault: pathlib.Path, art_dir: pathlib.Path, collection: str,
             tags: list[str], dry_run: bool = False, force: bool = False) -> dict:
    if not vault.is_dir():
        raise Refused(f"vault {vault} is not a directory")
    if not art_dir.is_dir():
        raise Refused(f"art directory {art_dir} is not a directory")
    data = load_metadata(vault)
    if collection not in data["collections"]:
        known = ", ".join(sorted(data["collections"])) or "(none)"
        raise Refused(f"no collection named {collection!r} in this vault. "
                      f"Known: {known}. Registering into a collection Atlas does "
                      f"not have would put the tokens somewhere invisible.")

    pictures = sorted(p for p in art_dir.iterdir()
                      if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    if not pictures:
        raise Refused(f"no images in {art_dir} (looked for {', '.join(IMAGE_SUFFIXES)})")

    tokens_dir = vault / "atlas-vtt" / "collections" / collection / "tokens"
    now = int(time.time() * 1000)
    added, updated, skipped = [], [], []
    resized = 0

    for src in pictures:
        name = src.stem
        dest = tokens_dir / f"{slug(name)}{src.suffix.lower()}"
        image_path = vault_relative(vault, dest)
        thumb_rel = f"{THUMBNAIL_DIR}/{dest.stem}-{fnv1a_32(image_path)}.webp"

        # Idempotence: one entry per image, found by the path it would point at.
        existing_id = next(
            (i for i, a in data["assets"].items()
             if a.get("type") == "token" and a.get("imagePath") == image_path),
            None)
        if existing_id and not force:
            entry = data["assets"][existing_id]
            thumb = entry.get("thumbnailPath")
            if thumb and (vault / thumb).exists():
                skipped.append(name)
                continue
            # Already registered, and the only thing outstanding is a thumbnail this
            # machine cannot produce: resize_for_thumbnail returns False when Pillow
            # is absent, so the completeness check above can never be satisfied and
            # EVERY run re-registered the whole folder, reporting it as `updated` --
            # false, since nothing changed, and unbounded work for a loop that is
            # documented as safe to run repeatedly. It surfaced only in CI, because CI
            # is the one place Pillow is missing. The image bytes are still compared,
            # so a genuinely new or changed portrait is not skipped: it has a
            # different imagePath, or different bytes, and reaches the write path.
            if not HAVE_PIL and dest.exists() and src.exists() \
                    and dest.read_bytes() == src.read_bytes():
                skipped.append(name)
                continue
        aid = existing_id or f"token-{slug(name)}"
        entry = {
            "id": aid,
            "type": "token",
            "name": name,
            "imagePath": image_path,
            "tags": list(tags),
            "collection": collection,
            "createdAt": (data["assets"].get(aid, {}) or {}).get("createdAt", now),
            "modifiedAt": now,
            "thumbnailPath": thumb_rel,
        }

        if dry_run:
            (updated if existing_id else added).append(name)
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.read_bytes() != src.read_bytes():
            shutil.copyfile(src, dest)
        if resize_for_thumbnail(dest, vault / thumb_rel):
            resized += 1
        (updated if existing_id else added).append(name)
        data["assets"][aid] = entry

    if not dry_run:
        write_metadata(vault, data)
    return {"added": added, "updated": updated, "skipped": skipped,
            "thumbnails": resized, "collection": collection}


def list_tokens(vault: pathlib.Path) -> int:
    data = load_metadata(vault)
    tokens = {i: a for i, a in data["assets"].items() if a.get("type") == "token"}
    print(f"{vault}: {len(tokens)} registered token(s)")
    missing = 0
    for i, a in sorted(tokens.items(), key=lambda kv: kv[1].get("name", "")):
        img = a.get("imagePath", "")
        thumb = a.get("thumbnailPath", "")
        ok_img = bool(img) and (vault / img).exists()
        ok_thumb = bool(thumb) and (vault / thumb).exists()
        if not ok_img:
            missing += 1
        flag = "ok " if ok_img else "MISSING IMAGE"
        if ok_img and not ok_thumb:
            flag = "no thumbnail (Atlas will regenerate)"
        print(f"  [{flag:32s}] {a.get('name', i)}  -> {img}")
    return missing


def main(argv: list) -> int:
    p = argparse.ArgumentParser(
        description="register a folder of portraits as importable Atlas tokens")
    p.add_argument("--vault", type=pathlib.Path,
                   help="the Atlas vault directory (the one containing atlas-vtt/)")
    p.add_argument("--art", type=pathlib.Path,
                   help="folder of images named after the creature")
    p.add_argument("--collection", default=DEFAULT_COLLECTION,
                   help=f"Atlas collection to register into (default {DEFAULT_COLLECTION})")
    p.add_argument("--tags", default=",".join(DEFAULT_TAGS),
                   help="comma-separated tags applied to every entry")
    p.add_argument("--list", action="store_true",
                   help="report registered tokens and any with a missing image")
    p.add_argument("--dry-run", action="store_true",
                   help="report what would change; write nothing")
    p.add_argument("--force", action="store_true",
                   help="re-register entries that already look complete")
    args = p.parse_args(argv)

    try:
        if args.list:
            if not args.vault:
                p.error("--list needs --vault")
            return 1 if list_tokens(args.vault) else 0
        if not args.vault or not args.art:
            p.error("--vault and --art are both required")
        res = register(args.vault, args.art, args.collection,
                       [t for t in (x.strip() for x in args.tags.split(",")) if t],
                       dry_run=args.dry_run, force=args.force)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1

    verb = "would register" if args.dry_run else "registered"
    print(f"{verb} {len(res['added'])} new, {len(res['updated'])} updated, "
          f"{len(res['skipped'])} already complete in {res['collection']}")
    if not args.dry_run:
        print(f"wrote {res['thumbnails']} thumbnail(s)")
    for n in (res["added"] + res["updated"])[:12]:
        print(f"  {n}")
    if len(res["added"] + res["updated"]) > 12:
        print(f"  ... and {len(res['added'] + res['updated']) - 12} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
