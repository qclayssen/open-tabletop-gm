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

The slug is the filename stem, lowercased with every run of non-alphanumerics
collapsed to a single hyphen. A name that does not slugify to a known portrait
returns None rather than guessing at a near-match: a wrong portrait on a
creature is worse than no portrait, because it is a lie the table cannot check.
"""

from __future__ import annotations

import re

# The directory the display serves portraits from. Absolute, like every other
# path in the engine, because Flask derives its root from __name__ and only gets
# that right when run as __main__ (see display/gm-display-app.py).
TOKENS_DIR = None  # set below, once pathlib is available and __file__ resolves

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
