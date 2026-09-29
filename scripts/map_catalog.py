#!/usr/bin/env python3
"""map_catalog.py: a contact sheet of every installed battle map, in a browser.

    python3 scripts/map_catalog.py                    # write docs/MAP-CATALOG.html
    python3 scripts/map_catalog.py --open             # ...and open it
    python3 scripts/map_catalog.py --only-imported     # only maps with artwork

WHY THIS EXISTS
---------------
A creator collection is large. Three maps are a folder; a hundred and fifty is a
filing problem, and it is the filing that decides whether the art is usable: the
GM needs to *see* the map to know which encounter it wants, and scrolling a
directory listing of 12MB JPEGs does not tell you that.

So this renders every map that loads into one HTML page, each with a thumbnail,
its real size in feet, and its terrain. Clicking one shows the picture full size.
No server, no build step: it is a static file that opens from disk, with the
thumbnails written next to it as data URIs, so it works from a USB stick and
survives being emailed.

The thumbnails are the point. 150 full-size JPEGs is 1.8 GB and will not fit in
a browser tab; thumbnails are a few hundred KB total and do.

WHAT IT DOES NOT DO
-------------------
It does not fetch anything. Maps come from `display/maps/*.json` and their
`image`, both of which are local. A map whose artwork is missing (images/ is
gitignored, so this is the normal state on a fresh clone) is listed as
unavailable rather than silently drawn blank -- a blank tile and a genuinely
empty map look identical otherwise, and only one of them is a bug.
"""
from __future__ import annotations

import argparse
import base64
import html
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import maps as engine_maps

# 4 GB is a browser's practical ceiling for one page of inline images. A map
# that will not fit is downscaled further rather than dropped, and a map that
# still will not fit is listed without a thumbnail and said so.
MAX_IMAGE_BYTES = 4 * 1024 * 1024
THUMB_WIDTH = 420


# Image resizers, best first. None of these is a dependency of the engine, and a
# contact sheet is a GM convenience, not a runtime path -- so the catalog is a
# generated artefact and a missing resizer must degrade to "no thumbnail" rather
# than fail. Windows is third because it is both cross-platform-wrapped and
# inconsistent across installs; ffmpeg is a common third-party install, ImageMagick
# a common package-manager one, and sips ships with every macOS.
_RESIZERS = (
    ("magick", ["magick", "{src}", "-resize", "{width}x", "-quality", "82", "{dest}"]),
    ("convert", ["convert", "{src}", "-resize", "{width}x", "-quality", "82", "{dest}"]),
    ("ffmpeg", ["ffmpeg", "-v", "error", "-y", "-i", "{src}", "-vf",
                "scale=w='min({width},iw)':h=-2", "-q:v", "4", "{dest}"]),
    ("sips", ["sips", "-Z", "{width}", "-s", "format", "jpeg", "{src}", "--out", "{dest}"]),
)


def _resizer() -> tuple[str, list[str]] | None:
    """The first resizer actually present on this machine, or None."""
    import shutil as _shutil
    for name, argv in _RESIZERS:
        found = _shutil.which(name)
        if found:
            return found, argv
    return None


def downscale(path: pathlib.Path, width: int, dest: pathlib.Path) -> pathlib.Path | None:
    """A JPEG thumbnail, or None when this machine has no image resizer.

    Pillow is not a dependency of the engine and adding one to build a contact
    sheet would be silly, so this shells out -- which means the tool is
    platform-dependent, and `sips` alone made the catalog fail outright on Linux
    and Windows. Every failure mode here returns None instead of raising: a map
    with no thumbnail still belongs in the catalog, and the page says which
    artwork it could not render.
    """
    import subprocess
    tool = _resizer()
    if tool is None:
        return None
    found, argv = tool
    argv = [a.replace("{src}", str(path)).replace("{dest}", str(dest))
            .replace("{width}", str(width)) for a in argv]
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run([found] + argv[1:], capture_output=True,
                                timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return None
    return dest


def data_uri(path: pathlib.Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def describe(name: str) -> dict | None:
    """One map as the catalog shows it, or None if it does not load."""
    try:
        loaded = engine_maps.load(name)
    except (ValueError, OSError) as exc:
        return {"name": name, "error": str(exc)}
    spec = loaded["meta"]
    grid = loaded["grid"]
    return {
        "name": name,
        "title": spec.get("name") or name,
        # `rows` is a list of strings, one per row, one character per square.
        "width": len(grid["rows"][0]),
        "height": len(grid["rows"]),
        "info": spec.get("info", ""),
        "image": spec.get("image"),
        "art": (engine_maps.MAPS_DIR / spec["image"]) if spec.get("image") else None,
        "terrain": sorted({ch for row in grid["rows"] for ch in row}),
        "features": len(spec.get("labels", [])),
        "has_credit": "credit" in spec,
    }


def collect(only_imported: bool) -> list[dict]:
    out = []
    for name in engine_maps.available():
        d = describe(name)
        if d is None:
            continue
        # `--only-imported` means "has artwork *on this machine*", not merely
        # "names an image". A map JSON naming a picture is the normal state of a
        # clone, because display/maps/images/ is gitignored -- so filtering on the
        # field alone would report every map as installed and draw blank tiles.
        if only_imported and not (d.get("art") and d["art"].exists()):
            continue
        out.append(d)
    return out


def build_html(entries: list[dict], cache: pathlib.Path) -> str:
    cards, missing, no_resizer, total_bytes = [], [], [], 0
    for d in entries:
        if d.get("error"):
            missing.append(f"{d['name']}: {d['error']}")
            continue
        art = d["art"]
        if not art or not art.exists():
            missing.append(
                f"{d['name']}: artwork missing ({art.name if art else 'no image'})")
        thumb_html = '<div class="noart">no artwork installed</div>'
        full_html = ""
        if art and art.exists():
            thumb = downscale(art, THUMB_WIDTH, cache / f"{d['name']}.jpg")
            if thumb:
                total_bytes += thumb.stat().st_size
                uri = data_uri(thumb)
                thumb_html = f'<img loading="lazy" src="{uri}" alt="{html.escape(d["title"])}">'
                if thumb.stat().st_size <= MAX_IMAGE_BYTES:
                    full_html = (f'<a href="{html.escape(d["title"])}.jpg" '
                                 f'download="{html.escape(d["title"])}.jpg">full size</a>')
            else:
                # The picture is installed but this machine could not resize it.
                # Saying "no artwork installed" here would be a lie that costs an
                # hour of debugging, so it is named separately.
                no_resizer.append(f"{d['name']}: artwork present but not resized "
                                  f"(no image resizer found)")

        badge = ' <span class="credit">credited</span>' if d["has_credit"] else ""
        terrain = " ".join(f'<code>{html.escape(t)}</code>' for t in d["terrain"])
        cards.append(f"""
    <figure class="card">
      {thumb_html}
      <figcaption>
        <h2>{html.escape(d['title'])} <span class="slug">{html.escape(d['name'])}</span>{badge}</h2>
        <p class="dims">{d['width']}&times;{d['height']} squares
           = {d['width'] * 5}&times;{d['height'] * 5} ft
           {'' if d['features'] else '&middot; <em>terrain unpainted</em>'}</p>
        <p class="info">{html.escape(d['info'])}</p>
        <p class="terrain">{terrain} {full_html}</p>
      </figcaption>
    </figure>""")

    notes = missing + no_resizer
    tool = _resizer()
    resizer_note = ("" if tool is not None else
                    "<strong>No image resizer found on this machine</strong> "
                    "(looked for magick, convert, ffmpeg, sips), so maps with "
                    "artwork are listed without a thumbnail. Install one and "
                    "re-run, or open the picture in "
                    "<code>display/maps/images/</code> directly.")
    note = ""
    if notes:
        note = ("<details class='missing'><summary>"
                f"{len(notes)} note(s)</summary><ul>"
                + "".join(f"<li>{html.escape(m)}</li>" for m in notes)
                + "</ul></details>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Battle map catalog</title>
<style>
 :root {{ --paper:#E8EDEE; --panel:#F4F7F7; --ink:#1B2830; --muted:#5A6A72;
          --line:#C3CDD0; --accent:#1E7F74; --brass:#A87A26; }}
 @media (prefers-color-scheme:dark) {{ :root {{ --paper:#0F171B; --panel:#162127;
          --ink:#DCE6E8; --muted:#93A5AC; --line:#2C3B42; --accent:#4FB8A8;
          --brass:#D2A24A; }} }}
 * {{ box-sizing:border-box; }}
 body {{ background:var(--paper); color:var(--ink); margin:0; padding:24px;
         font:15px/1.55 ui-sans-serif,system-ui,sans-serif; }}
 h1 {{ font-size:26px; margin:0 0 4px; }}
 .sub {{ color:var(--muted); margin:0 0 8px; max-width:70ch; }}
 .grid {{ display:grid; gap:18px; margin-top:20px;
          grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); }}
 .card {{ margin:0; background:var(--panel); border:1px solid var(--line);
          border-radius:10px; overflow:hidden; display:flex; flex-direction:column; }}
 .card img {{ width:100%; display:block; background:var(--paper); }}
 .noart {{ padding:34px 16px; text-align:center; color:var(--muted);
           background:repeating-linear-gradient(45deg,transparent,transparent 9px,
           color-mix(in srgb,var(--muted) 12%,transparent) 9px,
           color-mix(in srgb,var(--muted) 12%,transparent) 10px); }}
 figcaption {{ padding:12px 14px 16px; }}
 h2 {{ font-size:16px; margin:0 0 2px; }}
 .slug {{ font:11px ui-monospace,monospace; color:var(--muted); font-weight:400; }}
 .credit {{ font:10px ui-sans-serif; color:var(--brass); border:1px solid var(--brass);
            border-radius:4px; padding:0 4px; vertical-align:middle; }}
 .dims {{ color:var(--muted); margin:4px 0; font-size:13px; }}
 .info {{ margin:6px 0; font-size:13px; }}
 .terrain {{ margin:8px 0 0; font-size:12px; color:var(--muted); }}
 code {{ background:var(--paper); border:1px solid var(--line); border-radius:4px;
         padding:0 4px; }}
 a {{ color:var(--accent); }}
 .missing {{ margin-top:20px; color:var(--muted); font-size:13px; }}
</style></head><body>
<h1>Battle map catalog</h1>
<p class="sub">{len(cards)} map(s) available. Thumbnail payload {total_bytes / 1024:.0f} KB.
Built by <code>scripts/map_catalog.py</code> from <code>display/maps/*.json</code> and
the artwork in <code>display/maps/images/</code>. Nothing here is fetched.
{resizer_note}</p>
<div class="grid">{''.join(cards)}
</div>
{note}
</body></html>
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "docs" / "MAP-CATALOG.html",
                    help="where to write the catalog (default: %(default)s)")
    ap.add_argument("--only-imported", action="store_true",
                    help="only maps whose artwork is installed")
    ap.add_argument("--open", action="store_true", help="open it when done")
    args = ap.parse_args(argv)

    entries = collect(args.only_imported)
    if not entries:
        print("no maps found in display/maps/", file=sys.stderr)
        return 2

    cache = ROOT / "display" / "maps" / "images" / ".thumbs"
    page = build_html(entries, cache)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")

    with_art = sum(1 for d in entries if d["art"] and d["art"].exists())
    try:                                       # --out may point outside the repo
        shown = args.out.relative_to(ROOT)
    except ValueError:
        shown = args.out
    print(f"wrote {shown}: {len(entries)} map(s), "
          f"{with_art} with artwork, {len(page) / 1024:.0f} KB")
    if with_art < len(entries):
        print(f"  {len(entries) - with_art} map(s) have no artwork installed -- "
              f"run scripts/art_import.py, see display/maps/README.md")
    if with_art and _resizer() is None:
        # Not an error: the page is still useful, and the page says so itself.
        print("  note: no image resizer found (looked for magick, convert, ffmpeg, "
              "sips) -- artwork is listed without thumbnails")
    if args.open:
        import subprocess
        try:
            subprocess.run(["open", str(args.out)], check=False)
        except OSError:                       # not macOS
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
