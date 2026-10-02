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
Strixhaven community artist, and they are NOT in git -- 360 PNGs is 43 MB and
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
    # The artist's own folder names are the grouping, because they are the only
    # division of this set that is not ours: a 'Silverquill student' and a
    # 'first-year' are different pictures of different things, and inventing a
    # scheme on top of the archive's would be a second opinion on what the
    # artist drew. Counts are per folder.

    # Animals of Strixhaven (25)
    "bayou-groff": "bayou-groff",
    "blex-vexing-pest": "blex-vexing-pest",
    "blossoming-bog-beast": "blossoming-bog-beast",
    "bog-leech": "bog-leech",
    "bog-pests": "bog-pests",
    "bog-squirrel": "bog-squirrel",
    "book-devourer": "book-devourer",
    "brackish-trudge": "brackish-trudge",
    "conjured-coala": "conjured-coala",
    "frog-experiment": "frog-experiment",
    "frog-familiar": "frog-familiar",
    "frog-locked-in-cage": "frog-locked-in-cage",
    "griffin": "griffin",
    "kelpie-guide": "kelpie-guide",
    "mage-hunter": "mage-hunter",
    "mage-hunter-attacking": "mage-hunter-attacking",
    "owl-familiar": "owl-familiar",
    "pest": "pest",
    "sloth-transporter": "sloth-transporter",
    "spined-karok": "spined-karok",
    "springmane-cervin": "springmane-cervin",
    "sproutback-trudge": "sproutback-trudge",
    "summoned-cat": "summoned-cat",
    "toothy-pest": "toothy-pest",
    "wolf-elemental": "wolf-elemental",

    # College Mascots (14)
    "elemental-avatar": "elemental-avatar",
    "elemental-prismari-mascot": "elemental-prismari-mascot",
    "elemental-tiger": "elemental-tiger",
    "fractal": "fractal",
    "fractal-cat": "fractal-cat",
    "fractal-dog": "fractal-dog",
    "infernal-elemental": "infernal-elemental",
    "inkling": "inkling",
    "inkling-familiar": "inkling-familiar",
    "inkling-hound": "inkling-hound",
    "malicious-inkling": "malicious-inkling",
    "pest-mascot": "pest-mascot",
    "prismari-elemental": "prismari-elemental",
    "wolf-fractal": "wolf-fractal",

    # Constructs of Strixhaven (10)
    "alibou-ancient-witness": "alibou-ancient-witness",
    "biblioplex-archivist": "biblioplex-archivist",
    "biblioplex-assistant": "biblioplex-assistant",
    "campus-guide": "campus-guide",
    "cogwork-archivist": "cogwork-archivist",
    "cogwork-librarian": "cogwork-librarian",
    "golem": "golem",
    "mighty-construct": "mighty-construct",
    "ox-golem": "ox-golem",
    "titan-construct": "titan-construct",

    # Elder Dragons of Arcavios (9)
    "beledros-witherbloom": "beledros-witherbloom",
    "beledros-witherbloom-alt-art": "beledros-witherbloom-alt-art",
    "galazeth-prismari": "galazeth-prismari",
    "shadrix-silverquill": "shadrix-silverquill",
    "shadrix-silverquill-alt-art": "shadrix-silverquill-alt-art",
    "tanazir-quandrix": "tanazir-quandrix",
    "tanazir-quandrix-alt-art": "tanazir-quandrix-alt-art",
    "velomachus-lorehold": "velomachus-lorehold",
    "velomachus-lorehold-2nd-version": "velomachus-lorehold-2nd-version",

    # Items (7)
    "ancient-artifact-head": "ancient-artifact-head",
    "ancient-statue": "ancient-statue",
    "letter-of-acceptance": "letter-of-acceptance",
    "mana-pulse": "mana-pulse",
    "overgrown-star-arch": "overgrown-star-arch",
    "rare-fungus": "rare-fungus",
    "star-arch": "star-arch",

    # Lorehold (23)
    "augusta-close-up": "augusta-close-up",
    "augusta-dean-of-order": "augusta-dean-of-order",
    "blade-historian": "blade-historian",
    "combat-professor": "combat-professor",
    "conspiracy-theorist": "conspiracy-theorist",
    "digsite-engineer": "digsite-engineer",
    "hofri-ghostforge": "hofri-ghostforge",
    "illustrious-historian": "illustrious-historian",
    "lorehold-apprentice": "lorehold-apprentice",
    "lorehold-cleric": "lorehold-cleric",
    "lorehold-dwarf-student": "lorehold-dwarf-student",
    "lorehold-experiment-conductor": "lorehold-experiment-conductor",
    "lorehold-instructor": "lorehold-instructor",
    "lorehold-relic-reader": "lorehold-relic-reader",
    "lorehold-scroll-keeper": "lorehold-scroll-keeper",
    "lorehold-student": "lorehold-student",
    "losheel-clockwork-scholar": "losheel-clockwork-scholar",
    "loxodon-historian": "loxodon-historian",
    "osgir-the-reconstructor": "osgir-the-reconstructor",
    "plargg-dean-of-chaos": "plargg-dean-of-chaos",
    "pyromancer-adept": "pyromancer-adept",
    "quintorius-field-historian": "quintorius-field-historian",
    "tomewielder": "tomewielder",

    # Miscellaneous tokens (54)
    "amazed-first-year-student": "amazed-first-year-student",
    "angel-of-the-ruins": "angel-of-the-ruins",
    "arcanist-1": "arcanist-1",
    "arcanist-2": "arcanist-2",
    "arcavios-archaic": "arcavios-archaic",
    "archmage-emeritus": "archmage-emeritus",
    "blood-avatar": "blood-avatar",
    "burrog-befuddler": "burrog-befuddler",
    "cheerful-first-year": "cheerful-first-year",
    "codie-vociferous-codex": "codie-vociferous-codex",
    "daemogoth-titan": "daemogoth-titan",
    "daemogoth-woe-eater": "daemogoth-woe-eater",
    "disciplined-student": "disciplined-student",
    "divination-wizard": "divination-wizard",
    "dragonsguard-elite-druid": "dragonsguard-elite-druid",
    "dragonsguard-elite-mage": "dragonsguard-elite-mage",
    "eager-first-year": "eager-first-year",
    "elf-first-year-student": "elf-first-year-student",
    "elite-spellbinder": "elite-spellbinder",
    "encouraging-instructor": "encouraging-instructor",
    "extus-oriq-overlord": "extus-oriq-overlord",
    "ezzaroot-channeller": "ezzaroot-channeller",
    "ghen-arcanum": "ghen-arcanum",
    "gnome-student": "gnome-student",
    "jadzi-oracle-of-arcavios": "jadzi-oracle-of-arcavios",
    "kasmina": "kasmina",
    "kasmina-enigma-sage": "kasmina-enigma-sage",
    "librarian": "librarian",
    "lukka-coppercoat-outcast": "lukka-coppercoat-outcast",
    "lukka-veteran-explorer": "lukka-veteran-explorer",
    "mage-in-fiery-conflux": "mage-in-fiery-conflux",
    "mage-tower-athlete": "mage-tower-athlete",
    "mage-tower-fan": "mage-tower-fan",
    "mavinda-encouraging-professor": "mavinda-encouraging-professor",
    "nils-discipline-enforcer": "nils-discipline-enforcer",
    "orc-mind-mage": "orc-mind-mage",
    "oriq-loremage": "oriq-loremage",
    "oriq-warlock": "oriq-warlock",
    "owlin-student": "owlin-student",
    "professor-onyx": "professor-onyx",
    "snarl-sphinx": "snarl-sphinx",
    "specter-of-the-fells": "specter-of-the-fells",
    "spellcaster-with-volatile-spell": "spellcaster-with-volatile-spell",
    "strixhaven-first-year": "strixhaven-first-year",
    "strixhaven-pupil": "strixhaven-pupil",
    "student-in-ball-1": "student-in-ball-1",
    "student-in-ball-2": "student-in-ball-2",
    "student-polymorphed-into-goat": "student-polymorphed-into-goat",
    "summoned-angel": "summoned-angel",
    "thrilled-apprentice": "thrilled-apprentice",
    "ursine-professor": "ursine-professor",
    "vedalken-mage": "vedalken-mage",
    "wandering-archaic": "wandering-archaic",
    "zoomancy-professor": "zoomancy-professor",

    # Prismari (25)
    "arcane-expressionist": "arcane-expressionist",
    "djinni-water-shaper": "djinni-water-shaper",
    "efreet-flamepainter": "efreet-flamepainter",
    "elemental-expressionist": "elemental-expressionist",
    "elemental-mage": "elemental-mage",
    "hydromancer": "hydromancer",
    "ice-caster": "ice-caster",
    "igneous-sorcerer": "igneous-sorcerer",
    "nassari-dean-of-expression": "nassari-dean-of-expression",
    "orc-pledgemage": "orc-pledgemage",
    "orc-sorcerer": "orc-sorcerer",
    "owlin-mage": "owlin-mage",
    "owlin-prismari-sorcerer": "owlin-prismari-sorcerer",
    "prismari-apprentice": "prismari-apprentice",
    "prismari-element-mage": "prismari-element-mage",
    "prismari-performer": "prismari-performer",
    "rootha-prismari-prodigy": "rootha-prismari-prodigy",
    "rowan-kenrith": "rowan-kenrith",
    "sly-pledgemage": "sly-pledgemage",
    "soothsayer-adept": "soothsayer-adept",
    "storm-kiln-artist": "storm-kiln-artist",
    "torrent-mage": "torrent-mage",
    "uvilda-dean-of-perfection": "uvilda-dean-of-perfection",
    "will-kenrith": "will-kenrith",
    "zaffai-thunder-conductor": "zaffai-thunder-conductor",

    # Quandrix (26)
    "adrix-and-nev": "adrix-and-nev",
    "arithmancy-practitioner": "arithmancy-practitioner",
    "bio-mathematician": "bio-mathematician",
    "deekah-fractal-theorist": "deekah-fractal-theorist",
    "duplicated-student": "duplicated-student",
    "duplicated-student-1": "duplicated-student-1",
    "duplicated-student-2": "duplicated-student-2",
    "elf-amplimancer": "elf-amplimancer",
    "experimenting-arithmancer": "experimenting-arithmancer",
    "imbraham-dean-of-theory": "imbraham-dean-of-theory",
    "kianne-dead-of-substance": "kianne-dead-of-substance",
    "merfolk-illusionist": "merfolk-illusionist",
    "owlin-arithmancer": "owlin-arithmancer",
    "owlin-duplicate": "owlin-duplicate",
    "owlin-fractal-conjurer": "owlin-fractal-conjurer",
    "quandrix-dryad": "quandrix-dryad",
    "quandrix-duelist": "quandrix-duelist",
    "shield-mage": "shield-mage",
    "studious-mage": "studious-mage",
    "symmetry-sage": "symmetry-sage",
    "teleporting-mage": "teleporting-mage",
    "tortle-druid": "tortle-druid",
    "vedalken-arithmancer": "vedalken-arithmancer",
    "zimone-experimenting": "zimone-experimenting",
    "zimone-quandrix-prodigy": "zimone-quandrix-prodigy",
    "zimone-the-arithmancer": "zimone-the-arithmancer",

    # Silverquill (37)
    "arrogant-poet": "arrogant-poet",
    "author-of-shadows": "author-of-shadows",
    "bard-elocutor": "bard-elocutor",
    "bold-plagiarist": "bold-plagiarist",
    "breena-the-demagogue": "breena-the-demagogue",
    "clever-lumimancer": "clever-lumimancer",
    "combat-calligrapher": "combat-calligrapher",
    "decorated-pupil": "decorated-pupil",
    "embrose-master-poet": "embrose-master-poet",
    "embrose-sulking": "embrose-sulking",
    "fain-the-broker": "fain-the-broker",
    "glyphweaver": "glyphweaver",
    "gnome-lumimancer": "gnome-lumimancer",
    "imperious-verse-mage": "imperious-verse-mage",
    "ink-duelist": "ink-duelist",
    "killian-inkcaster-duelist": "killian-inkcaster-duelist",
    "leonin-lightscribe": "leonin-lightscribe",
    "shadewing-laureate": "shadewing-laureate",
    "shaile-dean-of-radiance": "shaile-dean-of-radiance",
    "silverquill-apprentice": "silverquill-apprentice",
    "silverquill-cleric": "silverquill-cleric",
    "silverquill-duelist": "silverquill-duelist",
    "silverquill-explosive-spell": "silverquill-explosive-spell",
    "silverquill-initiate": "silverquill-initiate",
    "silverquill-pledgemage": "silverquill-pledgemage",
    "silverquill-professor": "silverquill-professor",
    "silverquill-student": "silverquill-student",
    "silverquill-student-1": "silverquill-student-1",
    "silverquill-student-2": "silverquill-student-2",
    "silverquill-student-3": "silverquill-student-3",
    "silverquill-student-4": "silverquill-student-4",
    "silverquill-student-5": "silverquill-student-5",
    "spiteful-bard": "spiteful-bard",
    "studious-apprentice": "studious-apprentice",
    "tenured-inkcaster": "tenured-inkcaster",
    "thunderous-orator": "thunderous-orator",
    "unleashed-inkmage": "unleashed-inkmage",

    # Spirits (9)
    "ancient-spirit": "ancient-spirit",
    "laelia-spirit-general-of-the-blood-age": "laelia-spirit-general-of-the-blood-age",
    "lorehold-spirit": "lorehold-spirit",
    "lorehold-statue-spirit-1": "lorehold-statue-spirit-1",
    "lorehold-statue-spirit-2": "lorehold-statue-spirit-2",
    "lorehold-statue-spirit-3": "lorehold-statue-spirit-3",
    "spirit-guardian": "spirit-guardian",
    "spirit-traveller": "spirit-traveller",
    "strict-proctor": "strict-proctor",

    # Witherbloom (28)
    "blood-researcher": "blood-researcher",
    "dina-soul-steeper": "dina-soul-steeper",
    "dina-witherbloom-dryad": "dina-witherbloom-dryad",
    "dryad-alchemist": "dryad-alchemist",
    "elvish-warlock": "elvish-warlock",
    "excited-druid": "excited-druid",
    "gyome-master-chef": "gyome-master-chef",
    "honor-troll": "honor-troll",
    "ingredient-collector": "ingredient-collector",
    "lisette-dean-of-the-root": "lisette-dean-of-the-root",
    "marshland-bloodcaster": "marshland-bloodcaster",
    "orc-dissecter": "orc-dissecter",
    "sedgemoor-witch": "sedgemoor-witch",
    "tivash-gloom-summoner": "tivash-gloom-summoner",
    "treefolk-professor": "treefolk-professor",
    "troll-mage": "troll-mage",
    "valentin-dean-of-the-vein": "valentin-dean-of-the-vein",
    "veinwitch-coven-1": "veinwitch-coven-1",
    "veinwitch-coven-2": "veinwitch-coven-2",
    "veinwitch-coven-3": "veinwitch-coven-3",
    "willowdusk-essence-seer": "willowdusk-essence-seer",
    "witherbloom-alchemist": "witherbloom-alchemist",
    "witherbloom-battlemage": "witherbloom-battlemage",
    "witherbloom-dryad-druid": "witherbloom-dryad-druid",
    "witherbloom-experimant-conductor": "witherbloom-experimant-conductor",
    "witherbloom-initiate": "witherbloom-initiate",
    "witherbloom-ritualist": "witherbloom-ritualist",
    "witherbloom-treefolk-druid": "witherbloom-treefolk-druid",

    # Generated cast portraits (6) -- the campaign's own characters.
    #
    # DIFFERENT PROVENANCE FROM EVERYTHING ABOVE, and the only art in this file
    # the repository could commit if it wanted to. Every other portrait here is
    # a third party's unlicensed or licensed-by-permission work; these were
    # generated from descriptions of characters the campaign owns, so the output
    # is the campaign's. They live in the same gitignored directory anyway, so
    # nothing changes today -- the distinction is recorded so that moving them is
    # a decision somebody can make with the reason to hand.
    #
    # Cut from one 3x2 reference sheet by scripts/cut_portraits.py. `stopped-hand`
    # is not a statblock: it is Ysolde Marrow's mask, kept because it is the face
    # players see for two years and the one a 3.5 spoiler-free table cannot use.
    "dace-orrin": "dace-orrin",
    "mabli-quenn": "mabli-quenn",
    "petra-lune": "petra-lune",
    "stopped-hand": "stopped-hand",
    "tam": "tam",
    "theodric-vane": "theodric-vane",

    # Earlier pack (93) -- the 98-file set this one extended, and the
    # faculty-sheet crops. NOT in the 2026-09-30 archive, so they are listed
    # separately rather than dropped: several are the same person under a
    # different slug (`lorhold-plargg` beside `plargg-dean-of-chaos`), which
    # is the artist's earlier naming, not two pictures of two people.
    "archaic": "archaic",
    "aurora-luna-wynterstarr": "aurora-luna-wynterstarr",
    "bhedum-rampart-soovij": "bhedum-rampart-soovij",
    "cadoras-damellawar": "cadoras-damellawar",
    "daemogoth": "daemogoth",
    "drazhomir-yarnask": "drazhomir-yarnask",
    "embrose-dean-of-shadow": "embrose-dean-of-shadow",
    "first-year-student-1": "first-year-student-1",
    "first-year-student-2": "first-year-student-2",
    "first-year-student-3": "first-year-student-3",
    "first-year-student-4": "first-year-student-4",
    "first-year-student-5": "first-year-student-5",
    "first-year-student-6": "first-year-student-6",
    "grayson-wildemere": "grayson-wildemere",
    "greta-gorunn": "greta-gorunn",
    "groff": "groff",
    "javenesh-stoutclaw": "javenesh-stoutclaw",
    "kianne-dean-of-substance": "kianne-dean-of-substance",
    "larine-arneza": "larine-arneza",
    "lorehold-mascot-spirit-statue": "lorehold-mascot-spirit-statue",
    "lorehold-scholar-1": "lorehold-scholar-1",
    "lorehold-scholar-2": "lorehold-scholar-2",
    "lorehold-scholar-3": "lorehold-scholar-3",
    "lorehold-scholar-4": "lorehold-scholar-4",
    "lorehold-scholar-5": "lorehold-scholar-5",
    "lorehold-scholar-6": "lorehold-scholar-6",
    "lorhold-alibou": "lorhold-alibou",
    "lorhold-augusta": "lorhold-augusta",
    "lorhold-hofri": "lorhold-hofri",
    "lorhold-losheel": "lorhold-losheel",
    "lorhold-osgir": "lorhold-osgir",
    "lorhold-plargg": "lorhold-plargg",
    "melwythorne": "melwythorne",
    "mina-lee": "mina-lee",
    "nora-ann-wu": "nora-ann-wu",
    "oracle-of-strixhaven": "oracle-of-strixhaven",
    "oriq-blood-mage": "oriq-blood-mage",
    "oriq-recruit": "oriq-recruit",
    "oriq-recruiter": "oriq-recruiter",
    "prismari-mascot-art-elemental": "prismari-mascot-art-elemental",
    "prismari-scholar-1": "prismari-scholar-1",
    "prismari-scholar-2": "prismari-scholar-2",
    "prismari-scholar-3": "prismari-scholar-3",
    "prismari-scholar-4": "prismari-scholar-4",
    "prismari-scholar-5": "prismari-scholar-5",
    "prismari-scholar-6": "prismari-scholar-6",
    "prismari-scholar-7": "prismari-scholar-7",
    "prismari-scholar-8": "prismari-scholar-8",
    "quandrix-adrix-nev": "quandrix-adrix-nev",
    "quandrix-deekah": "quandrix-deekah",
    "quandrix-ibrahim": "quandrix-ibrahim",
    "quandrix-kainne": "quandrix-kainne",
    "quandrix-mascot-fractal": "quandrix-mascot-fractal",
    "quandrix-ruxa": "quandrix-ruxa",
    "quandrix-scholar-1": "quandrix-scholar-1",
    "quandrix-scholar-2": "quandrix-scholar-2",
    "quandrix-scholar-3": "quandrix-scholar-3",
    "quandrix-scholar-4": "quandrix-scholar-4",
    "quandrix-scholar-5": "quandrix-scholar-5",
    "quandrix-scholar-6": "quandrix-scholar-6",
    "quentillius-antiphiun-melentor-iii": "quentillius-antiphiun-melentor-iii",
    "relic-sloth": "relic-sloth",
    "rosimyffenbip-rosie-wuzfeddlims": "rosimyffenbip-rosie-wuzfeddlims",
    "rubina-larkingdale": "rubina-larkingdale",
    "ruin-grinder": "ruin-grinder",
    "shadrix-silverquill-alt": "shadrix-silverquill-alt",
    "shuvadri-glintmantle": "shuvadri-glintmantle",
    "silverquill-breena": "silverquill-breena",
    "silverquill-embrose": "silverquill-embrose",
    "silverquill-fain": "silverquill-fain",
    "silverquill-mascot-inkling": "silverquill-mascot-inkling",
    "silverquill-mavinda": "silverquill-mavinda",
    "silverquill-nils": "silverquill-nils",
    "silverquill-scholar-1": "silverquill-scholar-1",
    "silverquill-scholar-2": "silverquill-scholar-2",
    "silverquill-scholar-3": "silverquill-scholar-3",
    "silverquill-scholar-4": "silverquill-scholar-4",
    "silverquill-scholar-5": "silverquill-scholar-5",
    "silverquill-scholar-6": "silverquill-scholar-6",
    "silverquill-scholar-7": "silverquill-scholar-7",
    "silverquill-scholar-8": "silverquill-scholar-8",
    "silverquill-scholar-9": "silverquill-scholar-9",
    "silverquill-shaile": "silverquill-shaile",
    "strixhaven-campus-guide": "strixhaven-campus-guide",
    "tilana-kapule": "tilana-kapule",
    "urzmaktok-grojsh": "urzmaktok-grojsh",
    "witherbloom-apprentice": "witherbloom-apprentice",
    "witherbloom-mascot-pest": "witherbloom-mascot-pest",
    "witherbloom-pledgemage": "witherbloom-pledgemage",
    "witherbloom-scholar-1": "witherbloom-scholar-1",
    "witherbloom-scholar-2": "witherbloom-scholar-2",
    "witherbloom-scholar-3": "witherbloom-scholar-3",
    "zanther-bowen": "zanther-bowen",
}
def _by_person() -> dict[str, str]:
    """`<college>-<person>` slugs -> keyed by the person alone.

    `faculty_sheets.py` names its output `lorhold-osgir`, so the first segment is
    the college and the person is the second. A token on the board is named for
    the person, so `resolve("Breena")` has to be able to find
    `silverquill-breena` -- and it can, because no professor is on two faculty, so
    the college prefix that made the filename unambiguous does not make the lookup
    ambiguous. A name that did appear twice is dropped, and then resolves to
    nothing, which is the correct answer to a question with two answers.

    Only two-segment slugs are considered. `quandrix-scholar-3` has a college and
    a type and a number, and its "person" is a type.
    """
    index: dict[str, str] = {}
    clashes: set[str] = set()
    for slug in PORTRAITS:
        parts = slug.split("-")
        if len(parts) != 2 or parts[0] not in COLLEGE_SLUGS:
            continue
        person = parts[1]
        if person in index:
            clashes.add(person)
        index[person] = slug
    for person in clashes:
        del index[person]
    return index


# The college prefixes `faculty_sheets.py` actually writes, taken from COLLEGES
# and slugified the same way `slug()` there does -- plus `lorhold`, which is how
# Lorehold is spelled on disk. The sheet in ~/Downloads is `lorholdteachers.jpg`,
# `COLLEGES` here spells the college "Lorehold", and `college_of()` derives the
# key from the filename, so the slugs that exist on disk say `lorhold-`. Both
# spellings are in use and neither is being quietly rewritten: the file on the
# user's disk is named one way and the college is named the other, and renaming
# either would move art that is gitignored and therefore not recoverable.
COLLEGE_SLUGS = {slugify(name) for name in COLLEGES if slugify(name)} | {"lorhold"}

_PERSON = _by_person()


def _first_segments() -> dict[str, list[str]]:
    """First segment of every slug -> the slugs starting with it. Built once.

    This is the faculty problem stated as data. A portrait file is named for the
    person AND the office -- `augusta-dean-of-order` -- but a token is named for
    the person, so `resolve("Augusta")` missed on every faculty entry in the set.
    The office is what tells two of them apart, so it cannot simply be dropped
    from the filenames; the mapping has to live somewhere, and a first segment is
    the part of a slug a GM actually types.
    """
    index: dict[str, list[str]] = {}
    for slug in PORTRAITS:
        index.setdefault(slug.split("-", 1)[0], []).append(slug)
    return index


_FIRST_SEGMENT = _first_segments()


def resolve(name: str) -> str | None:
    """A creature name -> the portrait filename to fetch, or None.

    Three passes, most specific first, and none of them guesses:

    1. the whole name, exact -- "Quandrix Scholar 3", "daemogoth";
    2. the same with a trailing counter stripped -- "Archaic 2" -> "archaic";
    3. the first segment alone, but only when exactly one portrait carries it --
       "Augusta" -> "augusta-dean-of-order".

    Pass 3 returns None on a tie rather than picking, which is the same rule pass 1
    obeys and for the same reason. Ten first segments are ambiguous -- the five
    colleges (twice-spelled, see the file names), `first`, and `daemogoth`, which is
    both a creature and a colour variant -- so "Lorehold" resolves to nothing while
    "Lorehold Scholar 4" resolves exactly. A GM who wants a scholar says which one.

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
    # The sheet-cut faculty: the person alone finds `<college>-<person>`.
    person = _PERSON.get(key)
    if person:
        return f"{person}.png"
    # The artist's faculty: their files are named for the office too, so the
    # person's name is a prefix of the key rather than a segment of it.
    candidates = _FIRST_SEGMENT.get(key, ())
    if len(candidates) == 1:
        return f"{candidates[0]}.png"
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
