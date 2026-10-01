"""token_portraits.py: the token portrait manifest.

WHAT THIS IS
============
A creature on the map is drawn by `tactics.js` as a coloured shape with three
initials in it. That reads at a glance and it is what the whole display was
built on, but it is not a face, and after three sessions a table stops
recognising "the octagon" and starts needing to be told who they are looking at.

So a token may carry a portrait: a PNG drawn inside the existing shape. The
portrait REPLACES the flat fill. The frame stays -- see `drawToken` in
`display/static/tactics.js` for why that is not decoration.

This module is the index from a creature's name to that file. It is data, not
behaviour: `resolve()` is a lookup with a slug fallback, and nothing here is
read by the engine. `sync.snapshot` passes the result to the display and stops.

THE ART IS NOT OURS
===================
The portraits in `display/tokens/` are third-party work by **hearden**, a
Strixhaven community artist, and they are NOT in git -- 98 PNGs is 12 MB and
the map artwork sets the precedent (`display/maps/images/` is gitignored for the
same reason). This file is, because it is the index and the index is what tells
a future reader where the art came from and who to credit.

`art_import.py` writes `credit` and `source` into a map file for the same
reason, and `display/maps/README.md` puts it plainly: free to download is not
free of the artist's claim. A clone without the PNGs still works -- `resolve()`
returns nothing, `drawToken` draws the coloured shape exactly as it always did,
and no test depends on the art being present.

USING IT
========
    from . import token_portraits
    token_portraits.resolve("Ghoul")        # -> "ghoul.png" or None
    token_portraits.resolve("Quandrix Scholar 3")  # -> "quandrix-scholar-3.png"
    token_portraits.url("Daemogoth")        # -> "/tokens/daemogoth.png" or None

The slug is the filename stem, lowercased with every run of non-alphanumerics
collapsed to a single hyphen. A name that does not slugify to a known portrait
returns None rather than guessing at a near-match: a wrong portrait on a
creature is worse than no portrait, because it is a lie the table cannot check.

ONE PICTURE, EVERY SURFACE THAT SHOWS THE CREATURE
=================================================
`resolve()` is the only place a creature is turned into a picture, and every
surface that draws a creature goes through it:

    the board      sync.portrait_for -> snapshot token `portrait` -> drawToken
    the sheet      GET /portrait/<name> -> token_portraits.url
    Atlas          map_to_atlas.py --token-art DIR, same slug rule

That is the whole point of the module existing as an index rather than as a
lookup someone retyped at a call site. A second copy of the slug rule is a
second opinion about who a creature is, and this codebase's stated position is
that it may only have one -- see `statblock_art.py`, which is the same argument
about a different pool. So `url()` below is the display's half of that, and it
is a wrapper on `resolve()` rather than a parallel lookup: a token on the board
and the sheet open beside it cannot show different faces for one name.
"""

from __future__ import annotations

import pathlib
import re

# The directory the display serves portraits from, and the URL it serves it at.
# Both are named here so a caller that needs the file (is_installed) and a
# caller that needs the URL (url) are reading the same two facts the display
# route is built from. Absolute, like every other path in the engine, because
# Flask derives its root from __name__ and only gets that right when run as
# __main__ (see display/gm-display-app.py).
TOKENS_DIR = pathlib.Path(__file__).resolve().parents[2] / "display" / "tokens"

# The URL prefix the display serves that directory at. The route is
# `display/gm-display-app.py` @app.route("/tokens/<path:filename>"), and
# tactics.js drawToken builds the same string. Three places know this prefix,
# which is one too many -- so it is named here, the route asserts against it
# (tests/test_shared_portrait.py), and neither of the other two is free to
# change it without the board 404ing every portrait in the fight.
URL_PREFIX = "/tokens/"

CREDIT = "hearden"
SOURCE = "Strixhaven Tokens, r/StrixhavenDMs"

# College folder -> the `color` value a map's spawns use for that college, from
# display/maps/README.md. Not used for rendering: the colour comes from the
# token's `side` and the player's college is the GM's business. It is here so a
# future token picker can offer the set the way the artist grouped it.
COLLEGES = {
    "Quandrix": "quan",
    "Silverquill": "silv",
    "Prismari": "pris",
    "Lorehold": "lore",
    "Witherbloom": "with",
    "Monsters": "danger",
    "Faculty & Founders": "brass",
    "Fellow Students": None,       # companions and players: side decides
}


def slugify(name: str) -> str:
    """'Quandrix Scholar 3' -> 'quandrix-scholar-3'. The one rule the filenames
    and the manifest share, kept here so nothing else has to re-derive it."""
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


# Every portrait in the set, by the name the artist gave the file. This is the
# index; the PNGs themselves are gitignored, so on a clone without the art
# `resolve` still returns a filename and the display 404s harmlessly, which is
# the same shape as a map whose artwork is absent.
PORTRAITS = {
    # Fellow Students (18) -- the party, companions and students
    "aurora-luna-wynterstarr": "aurora-luna-wynterstarr",
    "bhedum-rampart-soovij": "bhedum-rampart-soovij",
    "cadoras-damellawar": "cadoras-damellawar",
    "drazhomir-yarnask": "drazhomir-yarnask",
    "grayson-wildemere": "grayson-wildemere",
    "greta-gorunn": "greta-gorunn",
    "javenesh-stoutclaw": "javenesh-stoutclaw",
    "larine-arneza": "larine-arneza",
    "melwythorne": "melwythorne",
    "mina-lee": "mina-lee",
    "nora-ann-wu": "nora-ann-wu",
    "quentillius-antiphiun-melentor-iii": "quentillius-antiphiun-melentor-iii",
    "rosimyffenbip-rosie-wuzfeddlims": "rosimyffenbip-rosie-wuzfeddlims",
    "rubina-larkingdale": "rubina-larkingdale",
    "shuvadri-glintmantle": "shuvadri-glintmantle",
    "tilana-kapule": "tilana-kapule",
    "urzmaktok-grojsh": "urzmaktok-grojsh",
    "zanther-bowen": "zanther-bowen",

    # Faculty & Founders (17) -- faculty and the deans
    "augusta-dean-of-order": "augusta-dean-of-order",
    "beledros-witherbloom": "beledros-witherbloom",
    "embrose-dean-of-shadow": "embrose-dean-of-shadow",
    "galazeth-prismari": "galazeth-prismari",
    "hofri-ghostforge": "hofri-ghostforge",
    "imbraham-dean-of-theory": "imbraham-dean-of-theory",
    "kianne-dean-of-substance": "kianne-dean-of-substance",
    "lisette-dean-of-the-root": "lisette-dean-of-the-root",
    "nassari-dean-of-expression": "nassari-dean-of-expression",
    "plargg-dean-of-chaos": "plargg-dean-of-chaos",
    "shadrix-silverquill": "shadrix-silverquill",
    "shadrix-silverquill-alt": "shadrix-silverquill-alt",
    "shaile-dean-of-radiance": "shaile-dean-of-radiance",
    "tanazir-quandrix": "tanazir-quandrix",
    "uvilda-dean-of-perfection": "uvilda-dean-of-perfection",
    "valentin-dean-of-the-vein": "valentin-dean-of-the-vein",
    "velomachus-lorehold": "velomachus-lorehold",

    # Quandrix (7)
    "quandrix-mascot-fractal": "quandrix-mascot-fractal",
    "quandrix-scholar-1": "quandrix-scholar-1",
    "quandrix-scholar-2": "quandrix-scholar-2",
    "quandrix-scholar-3": "quandrix-scholar-3",
    "quandrix-scholar-4": "quandrix-scholar-4",
    "quandrix-scholar-5": "quandrix-scholar-5",
    "quandrix-scholar-6": "quandrix-scholar-6",

    # Silverquill (12)
    "silverquill-apprentice": "silverquill-apprentice",
    "silverquill-mascot-inkling": "silverquill-mascot-inkling",
    "silverquill-pledgemage": "silverquill-pledgemage",
    "silverquill-scholar-1": "silverquill-scholar-1",
    "silverquill-scholar-2": "silverquill-scholar-2",
    "silverquill-scholar-3": "silverquill-scholar-3",
    "silverquill-scholar-4": "silverquill-scholar-4",
    "silverquill-scholar-5": "silverquill-scholar-5",
    "silverquill-scholar-6": "silverquill-scholar-6",
    "silverquill-scholar-7": "silverquill-scholar-7",
    "silverquill-scholar-8": "silverquill-scholar-8",
    "silverquill-scholar-9": "silverquill-scholar-9",

    # Prismari (10)
    "prismari-apprentice": "prismari-apprentice",
    "prismari-mascot-art-elemental": "prismari-mascot-art-elemental",
    "prismari-scholar-1": "prismari-scholar-1",
    "prismari-scholar-2": "prismari-scholar-2",
    "prismari-scholar-3": "prismari-scholar-3",
    "prismari-scholar-4": "prismari-scholar-4",
    "prismari-scholar-5": "prismari-scholar-5",
    "prismari-scholar-6": "prismari-scholar-6",
    "prismari-scholar-7": "prismari-scholar-7",
    "prismari-scholar-8": "prismari-scholar-8",

    # Lorehold (8)
    "lorehold-apprentice": "lorehold-apprentice",
    "lorehold-mascot-spirit-statue": "lorehold-mascot-spirit-statue",
    "lorehold-scholar-1": "lorehold-scholar-1",
    "lorehold-scholar-2": "lorehold-scholar-2",
    "lorehold-scholar-3": "lorehold-scholar-3",
    "lorehold-scholar-4": "lorehold-scholar-4",
    "lorehold-scholar-5": "lorehold-scholar-5",
    "lorehold-scholar-6": "lorehold-scholar-6",

    # Witherbloom (6)
    "witherbloom-apprentice": "witherbloom-apprentice",
    "witherbloom-mascot-pest": "witherbloom-mascot-pest",
    "witherbloom-pledgemage": "witherbloom-pledgemage",
    "witherbloom-scholar-1": "witherbloom-scholar-1",
    "witherbloom-scholar-2": "witherbloom-scholar-2",
    "witherbloom-scholar-3": "witherbloom-scholar-3",

    # Monsters (20)
    "archaic": "archaic",
    "brackish-trudge": "brackish-trudge",
    "cogwork-archivist": "cogwork-archivist",
    "daemogoth": "daemogoth",
    "daemogoth-titan": "daemogoth-titan",
    "first-year-student-1": "first-year-student-1",
    "first-year-student-2": "first-year-student-2",
    "first-year-student-3": "first-year-student-3",
    "first-year-student-4": "first-year-student-4",
    "first-year-student-5": "first-year-student-5",
    "first-year-student-6": "first-year-student-6",
    "groff": "groff",
    "mage-hunter": "mage-hunter",
    "oracle-of-strixhaven": "oracle-of-strixhaven",
    "oriq-blood-mage": "oriq-blood-mage",
    "oriq-recruit": "oriq-recruit",
    "oriq-recruiter": "oriq-recruiter",
    "relic-sloth": "relic-sloth",
    "ruin-grinder": "ruin-grinder",
    "strixhaven-campus-guide": "strixhaven-campus-guide",
}


def resolve(name: str) -> str | None:
    """A creature name -> the portrait filename to fetch, or None.

    None means "draw the coloured shape", which is what every token looked like
    before this and remains correct. It is not an error and it is not a warning:
    most of the SRD's 334 monsters have no portrait, and the engine must not
    care.
    """
    if not name:
        return None
    key = slugify(name)
    hit = PORTRAITS.get(key)
    if hit:
        return f"{hit}.png"
    # A GM who typed "Ghoul 1" for "Ghoul 1" in a group of four should still get
    # the one portrait. Strip a trailing counter only if the stripped form hits.
    stripped = re.sub(r"-\d+$", "", key)
    if stripped != key:
        hit = PORTRAITS.get(stripped)
        if hit:
            return f"{hit}.png"
    return None


def url(name: str) -> str | None:
    """A creature name -> the URL to fetch its portrait from, or None.

    The display's half of `resolve()`. The board gets a bare filename in the
    combat snapshot and builds the URL in `drawToken`; the character sheet has
    no snapshot, so it asks. Both answers come from here, which is what makes
    "the token and the sheet show the same face" a property of the code rather
    than a coincidence of two lookups agreeing.

    A wrapper, not a second lookup. The tempting version of this function is to
    skip `resolve()` and go to `PORTRAITS` directly, which would be faster and
    would reintroduce exactly the drift this exists to remove.
    """
    filename = resolve(name)
    return f"{URL_PREFIX}{filename}" if filename else None


def is_installed(name: str, tokens_dir=None) -> bool:
    """Whether this creature's portrait is actually on disk.

    The index and the art are separate on purpose: the PNGs are third-party and
    gitignored, so `resolve()` names a file that is absent on every fresh clone
    and the display falls back to the coloured shape. That fallback is the right
    behaviour and it is not a failure -- but a GM asking "why is my sheet not
    showing art" deserves a different answer from a GM whose creature has no art
    at all, and only the filesystem can tell those apart.

    `tokens_dir` exists so a caller passes the directory it actually serves from
    rather than the one this module assumes. The Flask app has its own
    `_TOKENS_DIR` and a test may point that at a temporary folder, and an
    existence check against a different directory than the one being served
    answers a question nobody asked.
    """
    filename = resolve(name)
    if not filename:
        return False
    return (pathlib.Path(tokens_dir) if tokens_dir else TOKENS_DIR).joinpath(
        filename).is_file()
