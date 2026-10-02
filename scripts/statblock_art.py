#!/usr/bin/env python3
"""statblock_art.py: match a campaign's statblock notes to real token portraits.

Run:
    python3 scripts/statblock_art.py --campaign NAME --vault DIR [--stats]
    python3 scripts/statblock_art.py --campaign NAME --vault DIR [--dry-run]
    python3 scripts/statblock_art.py --campaign NAME --vault DIR

    python3 scripts/statblock_art.py --campaign strixhaven-kairos \\
        --vault ~/github/strixhaven-kairos/campaigns/strixhaven-kairos --stats

    python3 scripts/statblock_art.py --campaign strixhaven-kairos \\
        --vault ~/github/strixhaven-kairos/campaigns/strixhaven-kairos \\
        --token-art-zip ~/Downloads/Strixhaven\\ D\\&D\\ tokens-20260930T003040Z-1-001.zip

WHY THIS REFUSES TO BE CLEVER
=============================

`faculty_sheets.py` is the precedent and its refusal is the reason. Four figure
detectors were tried on the Strixhaven faculty sheets and each found the right
number of regions and the wrong ones: the lore panels bridge the figures above
and below them into one band, so a band centred on a bridge crops the gap between
two people. The first honest output was 27 crops of which most were a page of
text. Tightening the crop fixed the labels and lost the faces.

A wrong crop is worse than no crop. It is a token that looks finished, passes every
automated check, and puts the wrong professor at the table mid-fight, and there is
no check for it afterwards because the file is a valid PNG of a person.

That failure is cheap to reach and uncheckable, so nothing here reaches for one:

  * Matching is EXACT on a normalised slug. No prefix, no substring, no edit
    distance, no "closest match". `Dean Moseo (Witherbloom)` does not become
    `beledros-witherbloom.png` because two Witherbloom deans exist and one of them
    happens to have art.
  * The only way past exact is APPROVED below: an explicit, recorded table where
    every line carries the reason it was allowed. It ships nearly empty and grows
    one reviewed line at a time.
  * Anything uncovered is printed BY NAME and left uncovered. This script never
    writes a placeholder, a silhouette, a colour disc, or a generic student or
    scholar token. They all render. That is the problem.

A token showing the wrong face is worse than a token showing nothing, nothing here
can tell the difference once the file exists, so the doubt is resolved in favour
of the gap.

WHAT COUNTS AS A MATCH
======================

Three sources, reported separately and never pooled, because they have different
denominators and a GM reading "72% covered" is owed the breakdown:

    bestiary   the Fantasy Statblocks notes in <vault>/Bestiary/
    npcs       the campaign's npcs-full.md, via npcs_to_statblocks.parse_npcs
    pcs        the campaign's characters/*.md, via pcs_to_statblocks.parse_pc

Both parsers are imported rather than reimplemented. A second copy of the NPC
parser drifts from the notes already in the vault and starts reporting coverage
for creatures nobody has.

Three art pools are searched, in a fixed and printed order, because a slug can be
satisfied by more than one file: `daemogoth-titan.png` is in the vault's monster
collection and again in `display/tokens/`, and those two are not the same picture.
The first pool wins and every shadowed file is reported by name, because a
precedence nobody can see is indistinguishable from a bug.

THE NAMING PROBLEM, AND WHERE IT IS PUT
=======================================

NPC record names carry headings and epithets: "Augusta, Order Returned (Lorehold,
interim dean)", "Jadzi, Steward of Fate (Oracle of Strixhaven)", "Nev, the
Practical Dean", "Rosie Wuzfeddlims / Rosimyffenbip". The art files carry names:
`augusta-dean-of-order.png`, `oracle-of-strixhaven.png`, `adrix-nev.png`,
`rosimyffenbip-rosie-wuzfeddlims.png`.

Which of those pairings is a fact about the campaign and which is a coincidence of
spelling is a judgement call, so the judgement is a table and not a rule:

    APPROVED: record name -> (art filename, one line of reasoning)

Three lines ship, each with its reasoning printed on every run. `Rosie Wuzfeddlims
/ Rosimyffenbip` is approved onto `rosimyffenbip-rosie-wuzfeddlims.png` because
the file names the same person by the same two names in the other order, and the
Bestiary note "Rosimyffenbip Rosie Wuzfeddlims" matches that file exactly, which
is what fixes the identity rather than the resemblance.

`Adrix` is NOT approved onto `adrix-nev.png`, though it is the only Adrix art on
this machine. That file is a two-person crop of the canon deans, and a token for
one dean showing two faces is the same failure as a wrong crop. An empty approval
table is the normal state of this dict; growing it is a decision somebody makes
deliberately and this script will not make it for them.

RECORDED SUBSTITUTIONS ARE HONOURED, NOT REIMPLEMENTED
=======================================================

`<vault>/atlas-vtt/assets/bestiary/_age-substitutions.json` records that seven
dragon age-variants stand in with a base-colour dragon picture, approved by the
GM on a stated date. That file is read and its own note and rule are printed into
this report verbatim. Nothing here re-derives the rule, classifies ages, or
decides which dragons qualify: a second implementation of a GM's ruling is a
second opinion, and this is the one place that must not have one.

Its `file` fields point at base art (`black-dragon.png`) which is not in the pool
any more, because the substitution was already materialised as copies under the
age-variants' own names. So an exact match here is still a substitution, and is
reported as one with the reasoning attached.

REGISTRATION IS `register_tokens.py`'s JOB, AND ONLY ITS
========================================================

A file in the vault is not an Atlas token. Atlas lists a token from an entry in
`atlas-vtt/.atlas-data/assets-metadata.json`, and the thumbnail beside it is named
by the FNV-1a/32 hash of the vault-relative image path, so a portrait copied in
without a correctly named thumbnail renders as a blank swatch. All of that is
`register_tokens.py`'s and none of it is repeated here: this script stages the
MATCHED pictures in a temporary directory and calls `register()`, which copies the
file, writes the entry and writes the thumbnail, atomically.

Only matched pictures are staged. Registering whole pools would put 98 portraits
of students and scholars in Atlas beside 54 monsters, and that is the wrong pile to
build the next token from.

A picture that serves two records is staged once. Atlas's entry is per picture,
not per statblock: the NPC "Rosie Wuzfeddlims / Rosimyffenbip" and the
"Rosimyffenbip Rosie Wuzfeddlims" note are one face and one token, and naming the
staged file after the statblock instead would register that face twice under two
near-identical names.

The staging directory is temporary and outside both the repository and the vault,
and `--stats` never creates one at all. `--dry-run` creates one, hands it to
`register(dry_run=True)`, and deletes it; nothing in the vault changes.

The third-party token zip is never opened unless `--token-art-zip` names it, and
then its member names are read without extracting anything, and only a member
that actually matched is copied out. Nothing third-party is ever written into the
repository.

THE MAIN CAST HAS NO ART AND THE REPORT SAYS SO
===============================================

Kairos, Ysolde Marrow, Hesper Vael, Juno Ashvale, Mabli Quenn, Theodric Vane,
Dace Orrin, Vess, Ninefold, Petra Lune, Ambrin Hollis, Selwyn Pike and Tam are
campaign-original. Nothing on this machine draws them and no creator token pack
contains them, so no amount of matching will produce their faces. The report names
them and counts them every run, because the alternative is a GM reaching for
`first-year-student-3.png` mid-fight, and a plausible wrong face is the single
output this script exists to refuse.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import paths
import register_tokens as rt
from map_to_atlas import slug
from utf8io import write_text

# `slug` here is the matching half's copy; `register_tokens.slug` is the writing
# half's. They must agree. This script stages `young-black-dragon.png` and
# `register()` then names the vault file `slug(stem) + suffix`, so if the two ever
# diverged the staged name and the registered name would be different files and a
# second run would register the picture again under its new name, which is the
# duplication this script is documented not to do. Pinned in the tests.
IMAGE_SUFFIXES = rt.IMAGE_SUFFIXES

# Art pools, in the order they are searched.
#
# The order is printed on every run and every file it shadows is named, because the
# only reason a precedence is safe is that a reader can check what it decided.
# `bestiary` first: it is the campaign's own curated monster collection, it is
# where `_age-substitutions.json` lives, and it is where the already-registered art
# came from. `faculty` next: measured crops off one college sheet, with
# `_extraction.json` recording the boxes. `display-tokens` after that: canon
# Strixhaven faces, correct, but the broadest pool and the most likely to collide.
#
# `srd-art` LAST, and last matters more here than anywhere else in the list. It is
# the only pool that is machine-generated, unlicensed, and of visibly uneven
# quality, so anything a human chose for a creature must beat it. Last place means
# a curated portrait, a reviewed approval, or a recorded substitution all win over
# a downloaded one, and `build_report` prints the pair by name when they collide so
# the choice is visible rather than assumed.
POOLS: tuple[str, ...] = ("bestiary", "faculty", "display-tokens", "srd-art")

# Where each pool lives, relative to the vault or to the engine checkout. A pool
# added by `--token-art-dir` or `--token-art-zip` sorts after these.
POOL_DIRS = {
    "bestiary": ("vault", "atlas-vtt/assets/bestiary"),
    "faculty": ("vault", "atlas-vtt/assets/faculty"),
    "display-tokens": ("engine", "display/tokens"),
    # Installed by scripts/install_srd_art.py, gitignored, absent on a fresh
    # clone -- and `add_pool` reads a missing directory as an empty pool, so its
    # absence costs nothing and is not an error.
    "srd-art": ("engine", "display/srd-art"),
}

# The file the collection uses to record which portraits stand in for which
# creatures. Read whole and quoted whole; never parsed for a rule.
SUBSTITUTION_FILE = "_age-substitutions.json"

# Record name, exactly as `parse_npcs` returns it, -> (art FILENAME, reason).
#
# The default is nearly empty and that is the correct default. This table is the
# only thing that may stand between "no art" and "a face at the table", so each
# line is a claim somebody has to stand behind and the reason is read by the GM,
# not interpreted by this script.
#
# Two rules constrain entries here:
#
#   * An entry names a FILE, not a pool, so a missing file is a loud refusal at
#     run time rather than a silent miss later.
#   * An entry may not name a generic token. `first-year-student-3.png`,
#     `quandrix-scholar-5.png`, `lorehold-apprentice.png` and their neighbours are
#     the art this script exists to refuse, so an approval naming one is a bug in
#     this table and not a decision.
APPROVED: dict[str, tuple[str, str]] = {
    "Augusta, Order Returned (Lorehold, interim dean)": (
        "augusta-dean-of-order.png",
        (
            "Canon Strixhaven's interim Lorehold dean. 'Order' is Lorehold's college "
            "name, and the creator pack files her as Lorehold/Augusta, dean of order, "
            "which is the same office this record gives her."
        )),
    "Jadzi, Steward of Fate (Oracle of Strixhaven)": (
        "oracle-of-strixhaven.png",
        (
            "Canon Strixhaven's Oracle is Jadzi, which this record's own parenthetical "
            "states, and the creator pack files her as Jadzi, oracle of Arcavios."
        )),
    "Rosie Wuzfeddlims / Rosimyffenbip": (
        "rosimyffenbip-rosie-wuzfeddlims.png",
        (
            "One person, two names, and the art file carries both in the other order. "
            "The Bestiary note 'Rosimyffenbip Rosie Wuzfeddlims' matches that file "
            "exactly, which is what fixes the identity rather than the resemblance."
        )),
    "Bhedum Rampart Sooviij": (
        "bhedum-rampart-soovij.png",
        (
            "Same name, one letter apart: the pack file drops the doubled 'i' in "
            "'Soovij'. Identity is fixed by the source, not the spelling: this note "
            "and the pack are both derived from the Strixhaven students roster, which "
            "names exactly one 'Bhedum \"Rampart\" Sooviij'. The picture agrees "
            "independently -- a loxodon, which is a language this statblock lists, in "
            "the gilded plate and greatsword its own Battlefield Conditions describe."
        )),
    "Quentillius Antiphius Melentor III": (
        "quentillius-antiphiun-melentor-iii.png",
        (
            "Same name, one letter apart: the pack file reads 'Antiphiun' for this "
            "record's 'Antiphius'. Both this note and the pack are derived from the "
            "Strixhaven students roster, which names exactly one Quentillius Antiphius "
            "Melentor III, and the picture agrees: a single student mage, which is what "
            "a statblock that casts mage hand and prestidigitation is."
        )),
    "Lorehold Professor of Chaos": (
        "plargg-dean-of-chaos.png",
        (
            "The one retired dean this campaign is allowed to show. The record names an "
            "office, the file names its holder, and the two agree on college AND "
            "discipline from independent sources: the pack files Plargg as Lorehold's "
            "dean of chaos, and maps/art/CREDITS.md:182 records 'Augusta Tullus (Order), "
            "Plargg (Chaos)' for Lorehold. world.md:134 permits him specifically -- "
            "'Kianne, Imbraham and Plargg may appear as cameo professors' -- while "
            "retiring the rest, so this line is a cameo and must not become standing "
            "faculty. The other six named-dean portraits are withheld on purpose: "
            "Kianne and Imbraham are deceased (CREDITS.md:210), and Shaile, Embrose, "
            "Uvilda and Nassari sit behind the unresolved Silverquill/Prismari dean "
            "conflict (CREDITS.md:192)."
        )),
    # ── species-correct art for the campaign-original cast ────────────────────
    #
    # Three of the main cast are creatures, and a picture of the right creature
    # is a better answer than a generic student token: it is the same claim the
    # age-substitutions already make, where the picture is of the right KIND of
    # thing and the notes say not to read further into it.
    #
    # Each of these three was chosen by LOOKING at the file, not by matching its
    # name. That is the whole reason this block is three lines and not six.
    "Ninefold": (
        "wandering-archaic.png",
        (
            "A creature, so the right creature is a better answer than a generic "
            "token, and this one was picked by eye rather than by filename: a "
            "towering, faceless, many-armed robed figure standing in a place whose "
            "geometry is wrong. That is Ninefold -- Gargantuan (Bestiary/Archaic.md:13), "
            "'face is absence where light behaves strangely' "
            "(npc-files/ninefold.md:93), shelves bending and shadows falling at "
            "impossible angles around it (npc-files/ninefold.md:26). The ARC AVIOS "
            "one is also a fair candidate and is left unchosen deliberately. "
            "Species-correct, not individuated: this is not the Biblioplex's Archaic "
            "and the token must not be read as showing the campaign's plot."
        )),
    "Vess the Tallykeeper": (
        "daemogoth-woe-eater.png",
        (
            "A daemogoth, and this is the better of the two daemogoth portraits in "
            "the pack: antlered, lichen-green, many-armed, standing in a bog. The "
            "vault describes Vess as 'huge, antlered, lichen-coated' "
            "(source/2.3.md:10) with antlers that gain weight as it feeds "
            "(npc-files/vess-tallykeeper.md:25). The face-that-is-turning-pages is "
            "NOT depicted, and cannot be at this scale -- so read this as the right "
            "sort of fiend and not as Vess's face. The plain 'daemogoth.png' is "
            "left unchosen; the woe-eater is the greener, wetter, more bog-bound of "
            "the two."
        )),
    "Magister Hesper Vael": (
        "owlin-arithmancer.png",
        (
            "An owlin, and a scholar rather than a duellist: a feathered owl-faced "
            "figure in robes with geometric light across it, which is what an "
            "archaeomancer at a Magister's chain looks like. Hesper is a white-"
            "plumaged owlin (prose/PLUMAGE.md:3) and THIS ONE IS NOT WHITE, so the "
            "plumage is simply wrong and the claim stops at species and occupation. "
            "That is still a better token than a generic Lorehold scholar, which is "
            "not a bird at all. The plumage rule is load-bearing enough that a GM "
            "who cannot live with that should generate her instead of accepting "
            "this line."
        )),
    # Two more, where the portrait was generated for the character and the record
    # carries an office the filename does not. Same shape as Augusta and Jadzi
    # above: one person, two names, the second being the job.
    "Professor Dace Orrin": (
        "dace-orrin.png",
        (
            "Generated for this campaign from his own character file, and named for "
            "the person. The record says 'Professor' because that is his office; "
            "nothing else in the vault calls him anything else, and npcs-full.md "
            "gives no surname. The tiefling, the horns and the brass-and-glass "
            "essence gauge all come from npc-files/dace-orrin.md and "
            "prose/CH-1.5-finals-in-the-fractal-conservatory.md:203, which is what "
            "makes this an identity rather than a resemblance."
        )),
    "Tam, Observant Sequencer (Quandrix)": (
        "tam.png",
        (
            "Generated for this campaign from her own character file. The record "
            "carries the office and the college -- 'Observant Sequencer (Quandrix)' "
            "-- and the only 'Tam' in the vault is the gorgon who holds it "
            "(npcs-full.md:243, source/1.2.md:58). She is the only gorgon in the "
            "campaign, so there is no second Tam for this to be."
        )),
}

# Statblock note name -> art FILENAME, for the creatures where the NOTE and the
# ART are named from two different halves of the same upstream record.
#
# The SRD splits several creatures across one record per form: the record is
# named "Werebear, Bear Form" and its `index` is `werebear-bear`. The exporter
# names a note after the record's NAME and `safe_name` drops the punctuation, so
# the note is "Werebear Bear Form" and its slug is `werebear-bear-form`. The art
# pool is keyed on the INDEX, where the file is `werebear-bear.png`. Neither name
# is wrong; they are two keys for one row of the dataset, and the matcher was
# only ever handed one of them.
#
# This is not a similarity table and there is no scoring in it. Every line is a
# pair the generated dataset itself asserts -- one record carrying both `name`
# and `index`. `test_every_line_is_a_pair_the_dataset_asserts` re-derives all of
# them from the data and fails if a line stops being a fact about the dataset,
# which is what keeps this from rotting into a table of guesses.
#
# Nineteen creatures, each one a single creature wearing two names. Every one of
# these is an exact match in substance: the picture is of the creature the
# statblock is for, not of something near it.
FORM_VARIANT_ART: dict[str, str] = {
    "SuccubusIncubus": "succubus-incubus.png",
    "Vampire Bat Form": "vampire-bat.png",
    "Vampire Mist Form": "vampire-mist.png",
    "Vampire Vampire Form": "vampire-vampire.png",
    "Werebear Bear Form": "werebear-bear.png",
    "Werebear Human Form": "werebear-human.png",
    "Werebear Hybrid Form": "werebear-hybrid.png",
    "Wereboar Boar Form": "wereboar-boar.png",
    "Wereboar Human Form": "wereboar-human.png",
    "Wereboar Hybrid Form": "wereboar-hybrid.png",
    "Wererat Human Form": "wererat-human.png",
    "Wererat Hybrid Form": "wererat-hybrid.png",
    "Wererat Rat Form": "wererat-rat.png",
    "Weretiger Human Form": "weretiger-human.png",
    "Weretiger Hybrid Form": "weretiger-hybrid.png",
    "Weretiger Tiger Form": "weretiger-tiger.png",
    "Werewolf Human Form": "werewolf-human.png",
    "Werewolf Hybrid Form": "werewolf-hybrid.png",
    "Werewolf Wolf Form": "werewolf-wolf.png",
}

# The campaign's own cast: label -> the record name it must be found under.
#
# Held as record names rather than labels so the report ties each claim to
# something actually parsed out of the campaign. A name with no record behind it is
# REPORTED rather than assumed present, so this table cannot quietly rot into
# claiming a character exists when the file no longer has them.
MAIN_CAST: dict[str, str] = {
    "PC Kairos": "Kairos",
    "Ysolde Marrow": "Esteemed Professor Ysolde Marrow (the Stopped Hand)",
    "Hesper Vael": "Magister Hesper Vael",
    "Juno Ashvale": "Juno Ashvale",
    "Mabli Quenn": "Mabli Quenn",
    "Theodric Vane": "Theodric Vane",
    "Dace Orrin": "Professor Dace Orrin",
    "Vess": "Vess the Tallykeeper",
    "Ninefold": "Ninefold",
    "Petra Lune": "Petra Lune",
    "Ambrin Hollis": "Coach Ambrin Hollis",
    "Selwyn Pike": "Selwyn Pike",
    "Tam": "Tam, Observant Sequencer (Quandrix)",
}

# One tag set for one `register()` call, rather than one call per source: a
# picture serving both a note and a record is staged once, so three calls would
# rewrite its tags three times and the last one to run would decide them.
DEFAULT_TAGS = ["open-tabletop-gm", "statblock-art"]
DEFAULT_COLLECTION = rt.DEFAULT_COLLECTION

# An Obsidian wikilink: `[[Bestiary/Saffi Tarn.md|Saffi Tarn]]` or `[[X]]`.
# `parse_npcs` hands these back whole, because `npcs-full.md` writes its roster
# bullets as links to the statblock each person belongs to.
WIKILINK = re.compile(r"^\[\[([^\]|]+?)(?:\|([^\]]*?))?\]\]$")


class Refused(rt.Refused):
    """A condition that stops the run rather than being worked around.

    Subclasses `register_tokens.Refused` rather than duplicating it. The refusals
    that matter most here come out of `register()` itself -- a vault that is not an
    Atlas vault, an index that is not valid JSON, a collection Atlas does not have
    -- and this script's own refusals are about the same subject matter, so one
    `except` has to catch both or a caller that writes `except statblock_art.Refused`
    silently misses the three that keep the shared index intact.
    """


# ── names ───────────────────────────────────────────────────────────────────
#
# `npcs_to_statblocks` is deliberately left alone: it reports what the campaign
# wrote, and these are the names it reports. What belongs to this script is the
# question of which of them a portrait may be attached to.

def wikilink_parts(name: str) -> tuple[str | None, str]:
    """(display text, target stem) for a wikilink, or (None, name) for plain text.

    The target is a vault PATH, `Bestiary/Tilana Kapule.md`, so the name in it is
    the file's stem and not the whole path. Taking the string whole instead is how
    this first shipped: every wikilinked record then compared
    `bestiary-tilana-kapule` against `tilana-kapule`, disagreed with itself, and
    was refused as a contradiction, taking five portraits that were sitting in the
    pool all along out of the coverage.
    """
    match = WIKILINK.match(name.strip())
    if not match:
        return None, name.strip()
    target, display = match.group(1).strip(), (match.group(2) or "").strip()
    return display, pathlib.PurePosixPath(target).stem.strip()


def record_name(name: str) -> str | None:
    """The name to match a portrait on, or None when there is not one.

    A wikilink's display text is not a guess: it is the name the GM wrote, and the
    target's stem is that same name in the same document. When the two DISAGREE
    the record names two different things at once, and there is no honest way to
    pick one. That record is refused and reported rather than matched on whichever
    half was read first, which would put a face on a coin-toss.
    """
    display, stem = wikilink_parts(name)
    if display is None:
        return name.strip() or None
    if not display:
        return stem or None
    if slug(display) != slug(stem):
        return None
    return display


def is_statblock_note(text: str) -> bool:
    """True for a Fantasy Statblocks note.

    Both shapes the plugin accepts: `statblock:` frontmatter, which is what
    `export_bestiary.note_for` writes and what wakes the watcher, and a
    ```statblock fence, which is where the data actually lives. `Bestiary/README.md`
    has neither and is dropped by this test rather than by a filename exclusion
    that would stop working the day the file is renamed.
    """
    return bool(re.search(r"^statblock:\s", text, re.MULTILINE)
                or "```statblock" in text)


# ── the art index ───────────────────────────────────────────────────────────

class ArtFile:
    """One picture, wherever it happens to be.

    A path on disk, or a member of a zip that is never extracted except into a
    staging directory that is deleted afterwards. Both are `copy_to()` because
    registration needs real bytes in a real folder and there is no other caller.
    """

    __slots__ = ("archive", "member", "name", "path", "pool")

    def __init__(self, pool: str, name: str, path: pathlib.Path | None = None,
                 archive: pathlib.Path | None = None, member: str | None = None):
        self.pool = pool
        self.name = name
        self.path = path
        self.archive = archive
        self.member = member

    def copy_to(self, dest: pathlib.Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.archive is not None:
            with zipfile.ZipFile(self.archive) as zf:
                dest.write_bytes(zf.read(self.member))
        else:
            shutil.copyfile(self.path, dest)

    def describe(self) -> str:
        return self.path.name if self.path is not None else f"{self.member} (in zip)"


class ArtIndex:
    """slug -> every file claiming it, best pool first.

    Built once per run and scanned once per pool, not once per note: at 370-odd
    notes and 157 files the cost is trivial, but the reason to build it once is
    that a per-note scan is where a second pool gets added by accident to one
    branch of a conditional and then quietly searched twice.
    """

    def __init__(self) -> None:
        self.by_slug: dict[str, list[ArtFile]] = {}
        self.shadowed: list[tuple[str, ArtFile, ArtFile]] = []
        self.pools: list[tuple[str, int]] = []

    def add_file(self, art: ArtFile) -> None:
        held = self.by_slug.setdefault(slug(pathlib.Path(art.name).stem), [])
        if held and self._differs(art, held[0]):
            # Two pools, two pictures, one name. The earlier pool wins and the
            # loser is named in the report, so the choice is visible rather than a
            # property of directory listing order.
            self.shadowed.append((slug(pathlib.Path(art.name).stem), held[0], art))
        held.append(art)

    @staticmethod
    def _differs(a: ArtFile, b: ArtFile) -> bool:
        """True unless the two files are byte-identical.

        Identical bytes are not a collision, they are the same picture filed twice
        by two tools that both copied it out of the same pack, and reporting those
        as a shadow would bury the three that matter in a list of fifty.
        """
        if (a.path is None) != (b.path is None):
            return True
        if a.path is None:
            return a.member != b.member
        return a.path.read_bytes() != b.path.read_bytes()

    def add_pool(self, pool: str, directory: pathlib.Path) -> None:
        files = sorted(p for p in directory.iterdir()
                       if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES) \
            if directory.is_dir() else []
        for path in files:
            self.add_file(ArtFile(pool, path.name, path=path))
        self.pools.append((pool, len(files)))

    def add_zip(self, pool: str, archive: pathlib.Path) -> None:
        """Read a pack's member NAMES. Nothing is extracted, ever.

        Extracting a 267-file creator pack into the working tree to use four
        pictures out of it is how third-party art ends up committed, so the
        archive is read as a directory here and only a member that actually matched
        is copied out, later, into a temporary staging folder.
        """
        if not archive.is_file():
            raise Refused(f"{archive} does not exist")
        with zipfile.ZipFile(archive) as zf:
            names = sorted(pathlib.PurePosixPath(n).name for n in zf.namelist()
                           if pathlib.PurePosixPath(n).suffix.lower() in IMAGE_SUFFIXES)
        for name in names:
            self.add_file(ArtFile(pool, name, archive=archive, member=name))
        self.pools.append((pool, len(names)))

    def resolve(self, name: str) -> ArtFile | None:
        """The best file for an EXACT slug match, or None."""
        held = self.by_slug.get(slug(name))
        return held[0] if held else None

    def by_file(self, filename: str) -> ArtFile | None:
        """The file an APPROVED entry names, or None."""
        for held in self.by_slug.values():
            for art in held:
                if art.name == filename:
                    return art
        return None

    def all_files(self) -> list[ArtFile]:
        return [art for held in self.by_slug.values() for art in held]


def build_index(vault: pathlib.Path, extra_dirs: list[pathlib.Path],
                archive: pathlib.Path | None) -> ArtIndex:
    index = ArtIndex()
    for pool in POOLS:
        where, rel = POOL_DIRS[pool]
        index.add_pool(pool, vault / rel if where == "vault" else ROOT / rel)
    # Extra folders and the zip sort last: they were added after the curated pools
    # and nothing in the vault depends on them, so nothing may be displaced by one.
    for directory in extra_dirs:
        index.add_pool(str(directory), directory)
    if archive is not None:
        index.add_zip(str(archive), archive)
    return index


# ── the recorded substitutions ──────────────────────────────────────────────

def age_substitutions(vault: pathlib.Path) -> dict:
    """The collection's own substitution record, or `{}` if there is none.

    Returned whole and quoted whole. An absent file is not an error: a collection
    with no recorded substitutions has none to honour, and deriving the rule from
    the file names would be the reimplementation the module docstring refuses.
    A file that is present and unreadable IS an error, because it is where the
    campaign records which portraits stand in for which creatures and quietly
    ignoring it would mean reporting substitutions without their reasoning.
    """
    path = vault / POOL_DIRS["bestiary"][1] / SUBSTITUTION_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise Refused(f"{path} is not valid JSON ({exc}). Refusing to ignore it: "
                      f"it is the record of which portraits stand in for which "
                      f"creatures.")
    if not isinstance(data.get("substitutions"), dict):
        raise Refused(f"{path} has no 'substitutions' mapping, so it is not the "
                      f"file this script was written for.")
    return data


# ── one record, matched ─────────────────────────────────────────────────────

def _unavailable(name: str, target: str, filename: str, table: str,
                 reason: str) -> dict:
    """A reviewed line whose art is named correctly but is not on this machine.

    THIS IS NOT A STALE-LINE ERROR, and treating it as one broke a clone.

    `display/tokens/` and `display/srd-art/` are both gitignored, so a clone that
    has never run an installer has neither. Every reviewed line in APPROVED then
    named a file that was not in any pool, and the original code raised `Refused`
    on the first one -- so `statblock_art.py` could not run at all on a fresh
    checkout. That is the opposite of what the rest of the repository promises:
    `token_portraits.py` says a clone without the art "still knows the set exists,
    still credits the artist, and still draws every token correctly from its side
    colour alone", `install_tokens.py --check` exists to audit exactly this
    absence, and `add_pool` reads a missing directory as an empty pool rather than
    an error. The matcher was the one place that treated absent art as fatal.

    So a named-but-absent file is REPORTED and the record is left uncovered: no
    `art` key, so nothing registers it and no caller mistakes it for a match. A
    typo is still visible, because the report prints the name it wanted, which is
    exactly the information needed to see that the line has gone stale.
    """
    return {"name": name, "slug": target, "how": f"{table.lower()}-unavailable",
            "art_named": filename, "reason": reason,
            "problem": f"{table} names {filename!r} and it is not installed in "
                       f"any art pool; the art is gitignored, so run the "
                       f"installer, or check the line has not gone stale"}


def match_record(name: str, index: ArtIndex, subs: dict) -> dict:
    """One match record for one statblock name.

    Keys:
        name     the name as the source produced it, verbatim
        slug     the normalised name, when there is one to normalise to
        art      the art FILENAME, when something matched
        pool     which pool it came from
        how      "exact" | "substituted" | "approved" | None
        reason   why, for the two cases that are not "exact"
        problem  why the record could not even be named, when that happened

    `how` is None when nothing matched, and in that case there is no art key at
    all rather than a null one: a caller that finds the key has found a claim, and
    a caller that finds None has found a gap it must surface.
    """
    target = record_name(name)
    if target is None:
        return {"name": name, "problem":
                "wikilink display text and target name two different creatures"}

    hit = index.resolve(target)
    if hit is not None:
        recorded = subs.get("substitutions", {}).get(slug(target))
        if recorded:
            return {"name": name, "slug": slug(target), "art": hit.name,
                    "pool": hit.pool, "how": "substituted",
                    "uses": recorded.get("uses", ""), "file": recorded.get("file", ""),
                    "rule": subs.get("rule", ""), "reason": subs.get("note", ""),
                    "base_present": (vault_has(index, hit, recorded.get("file", "")))}
        return {"name": name, "slug": slug(target), "art": hit.name,
                "pool": hit.pool, "how": "exact"}

    approval = APPROVED.get(name.strip())
    if approval:
        filename, reason = approval
        found = index.by_file(filename)
        if found is None:
            return _unavailable(name, slug(target), filename, "APPROVED", reason)
        return {"name": name, "slug": slug(target), "art": found.name,
                "pool": found.pool, "how": "approved", "reason": reason}

    variant = FORM_VARIANT_ART.get(name.strip())
    if variant:
        found = index.by_file(variant)
        if found is None:
            return _unavailable(name, slug(target), variant, "FORM_VARIANT_ART",
                                "one creature under two names in the SRD dataset")
        return {"name": name, "slug": slug(target), "art": found.name,
                "pool": found.pool, "how": "form-variant",
                "reason": ("one creature under two names in the SRD dataset: the "
                           "note is named from the record's `name` and the art from "
                           "its `index`")}

    return {"name": name, "slug": slug(target), "how": None}


def vault_has(index: ArtIndex, art: ArtFile, filename: str) -> bool:
    """Whether a substitution's recorded base art is still in the same pool.

    It usually is not: the substitution was already made, as copies under the
    age-variants' own names, and the base file went away with the copy step. The
    report says which happened, because "Young Black Dragon is standing in for the
    black dragon art" and "Young Black Dragon is the black dragon art" are the same
    picture and different claims about where it came from.
    """
    if not filename:
        return False
    if art.path is not None:
        return (art.path.parent / filename).is_file()
    found = index.by_file(filename)
    return found is not None and found.pool == art.pool


# ── the three sources ───────────────────────────────────────────────────────

def bestiary_notes(vault: pathlib.Path) -> list[str]:
    """The statblock notes in `<vault>/Bestiary/`, by note name.

    The folder is the source of truth rather than a monster list rebuilt from a
    data file, for the reason `map_to_atlas.bestiary_index` gives: a name built
    from data can name a note that was never written, so a coverage report built
    that way claims a gap that does not exist and hides one that does.

    Both spellings are probed, as `map_to_atlas` does, because a vault written on
    macOS and synced to Linux can end up with either. They are DEDUPLICATED by
    (device, inode) before any note is read, and that is not tidiness: macOS
    filesystems are case insensitive by default, so `Bestiary` and `bestiary` are
    one directory there and scanning both appends every note twice. The symptom is
    a coverage line reading `371/742` and a substitution list printing each dragon
    twice, both of which read as data rather than as a doubled scan.

    The key is the inode rather than the resolved path because `Path.resolve()`
    does NOT normalise case on macOS: it resolves symlinks and nothing else, so
    the two spellings come back as two different strings pointing at one
    directory, and the duplicate survives. `os.stat` is the one thing on this
    machine that answers "are these the same directory".
    """
    out: list[str] = []
    seen: set[tuple[int, int]] = set()
    for folder in ("Bestiary", "bestiary"):
        directory = vault / folder
        if not directory.is_dir():
            continue
        info = directory.stat()
        if (info.st_dev, info.st_ino) in seen:
            continue
        seen.add((info.st_dev, info.st_ino))
        for note in sorted(directory.glob("*.md")):
            if is_statblock_note(note.read_text(encoding="utf-8", errors="replace")):
                out.append(note.stem)
    return out


def npc_records(campaign: pathlib.Path) -> list[str]:
    """The NPC names, through the campaign's own parser."""
    import npcs_to_statblocks as ns
    entries = ns.entries_file(campaign)
    if entries is None or not entries.is_file():
        raise Refused(f"no NPC entries file in {campaign}: looked for npcs-full.md "
                      f"and npcs.md, found neither")
    return [r["name"] for r in ns.parse_npcs(entries.read_text(encoding="utf-8"))]


def pc_records(campaign: pathlib.Path) -> list[str]:
    """The PC names, through the campaign's own parser. Every sheet, not one."""
    import pcs_to_statblocks as ps
    sheets = sorted((campaign / "characters").glob("*.md"))
    return [ps.parse_pc(sheet.read_text(encoding="utf-8"))["name"] for sheet in sheets]


def gather(names: list[str], index: ArtIndex, subs: dict) -> list[dict]:
    return [match_record(name, index, subs) for name in names]


def collect(vault: pathlib.Path, campaign: pathlib.Path | None,
            index: ArtIndex, subs: dict) -> list[tuple[str, list[dict]]]:
    """(source name, matches) per source. Never pooled."""
    sources = [("bestiary", gather(bestiary_notes(vault), index, subs))]
    if campaign is not None:
        sources.append(("npcs", gather(npc_records(campaign), index, subs)))
        sources.append(("pcs", gather(pc_records(campaign), index, subs)))
    return sources


# ── registration ────────────────────────────────────────────────────────────

def register_matched(vault: pathlib.Path, matched: list[dict], index: ArtIndex,
                     collection: str, tags: list[str],
                     dry_run: bool) -> dict:
    """Hand the matched pictures to `register_tokens.register()`.

    One staging folder, one call. The folder is temporary and lives outside both
    the repository and the vault, so `--dry-run` leaves nothing anywhere and a
    real run leaves nothing but the files Atlas asked for.

    Staged under the ART filename, not the statblock name, because Atlas's entry is
    per picture. Two records served by one face become one token rather than two
    near-identically named ones, which is what a re-run would then have to clean up.
    """
    wanted: dict[str, ArtFile] = {}
    for record in matched:
        art = record.get("art")
        if art:
            wanted.setdefault(art, index.by_file(art))

    if not wanted:
        raise Refused("nothing matched, so there is nothing to register. Refusing "
                      "to write an empty index change and calling it a successful "
                      "run.")

    with tempfile.TemporaryDirectory(prefix="statblock-art-") as tmp:
        staging = pathlib.Path(tmp)
        for filename, art in sorted(wanted.items()):
            if art is None:
                raise Refused(f"art {filename!r} matched but is in no pool; the "
                              f"index and the match disagree")
            art.copy_to(staging / filename)
        result = rt.register(vault, staging, collection, tags, dry_run=dry_run)
    result["dry_run"] = dry_run
    return result


# ── the report ──────────────────────────────────────────────────────────────

def _pct(have: int, total: int) -> str:
    return f"{have / total:.0%}" if total else "  n/a"


def build_report(sources: list[tuple[str, list[dict]]], index: ArtIndex,
                 vault: pathlib.Path, registration: dict | None) -> str:
    out: list[str] = []
    add = out.append

    add("art pools, in search order")
    for pool, count in index.pools:
        add(f"  {pool:<24} {count:>4} file(s)")
    if index.shadowed:
        add("\n  shadowed: two pools supply the same slug with DIFFERENT pictures.")
        add("  The earlier pool wins. Both are named, so the choice can be checked.")
        for slugged, winner, loser in sorted(index.shadowed,
                                             key=lambda s: s[0]):
            add(f"    {slugged}: using {winner.describe()} "
                f"({winner.pool}), not {loser.describe()} ({loser.pool})")

    add("\ncoverage, reported per source")
    for name, records in sources:
        covered = [r for r in records if r.get("art")]
        tally: dict[str, int] = {}
        for record in covered:
            tally[record["how"]] = tally.get(record["how"], 0) + 1
        detail = ", ".join(f"{tally[h]} {h}" for h in sorted(tally)) or "none"
        add(f"  {name:<9} {len(covered):>3}/{len(records):<4} "
            f"({_pct(len(covered), len(records))})   {detail}")

    substituted = [r for _, records in sources for r in records
                   if r.get("how") == "substituted"]
    if substituted:
        add("\nrecorded substitutions, honoured and reported in the collection's words")
        add(f"  rule: {substituted[0]['rule']}")
        add(f"  {substituted[0]['reason']}")
        for record in substituted:
            add(f"  {record['name']} -> {record['art']} (from {record['pool']})")
            add(f"    recorded as standing in for {record['uses']} via "
                f"{record['file']}; base art "
                f"{'still present' if record['base_present'] else 'no longer in the pool, the substitution was already made as a copy under this name'}")

    approved = [r for _, records in sources for r in records
                if r.get("how") == "approved"]
    if approved:
        add("\napproved past an exact match, with the reason each was allowed")
        for record in approved:
            add(f"  {record['name']} -> {record['art']} (from {record['pool']})")
            add(f"    {record['reason']}")

    form_variants = [r for _, records in sources for r in records
                     if r.get("how") == "form-variant"]
    if form_variants:
        add("\none creature under two names: the note is named from the record's "
            "`name`, the art from its `index`")
        add("  These are the same creature, not a substitute for one. The pairs are "
            "asserted by the SRD dataset and re-derived from it in the tests.")
        for record in form_variants:
            add(f"  {record['name']} -> {record['art']} (from {record['pool']})")

    add("\nthe main cast is campaign-original and no collection here draws them")
    by_slug: dict[str, dict] = {}
    for _, records in sources:
        for record in records:
            if record.get("slug"):
                by_slug.setdefault(record["slug"], record)
    drawn, undrawn = [], []
    for label, wanted in MAIN_CAST.items():
        record = by_slug.get(slug(wanted))
        if record is None:
            undrawn.append(f"{label}: no record named {wanted!r} was parsed at all")
        elif record.get("art"):
            drawn.append(f"{label}: {record['art']} ({record['pool']})")
        else:
            undrawn.append(label)
    add(f"  {len(drawn)}/{len(MAIN_CAST)} have a portrait")
    for line in drawn:
        add(f"    {line}")
    add(f"  {len(undrawn)} have none, and are left uncovered:")
    for line in undrawn:
        add(f"    {line}")
    add("  No generic student, scholar, apprentice or mascot token is standing in "
        "for any of them.")

    unavailable = [(src, r) for src, records in sources for r in records
                   if str(r.get("how", "")).endswith("-unavailable")]
    if unavailable:
        add("\nreviewed lines whose art is named but NOT installed")
        add("  These are not gaps in the tables. The art pools are gitignored, so a "
            "machine that has not run an installer holds none of them and these "
            "creatures are simply uncovered here. No `art` was written and nothing "
            "was registered for any of them.")
        for src, record in unavailable:
            add(f"  {record['name']} wants {record['art_named']} [{src}]")
            add(f"    {record['problem']}")

    for name, records in sources:
        missing = [r for r in records if not r.get("art")]
        if not missing:
            continue
        add(f"\nno portrait, by name: {name} ({len(missing)})")
        add("  This list is the deliverable: it is what art to source.")
        for record in missing:
            suffix = f"   [{record['problem']}]" if record.get("problem") else ""
            add(f"    {record['name']}{suffix}")

    used = {r["art"] for _, records in sources for r in records if r.get("art")}
    orphans = sorted({art.name for art in index.by_slug.get("", [])
                      or [a for held in index.by_slug.values() for a in held]
                      if art.name not in used and art.pool == POOLS[0]})
    if orphans:
        add(f"\nart in the {POOLS[0]} pool matching no statblock ({len(orphans)})")
        add("  Not an error, and not coverage: some of it is for creatures with no "
            "note yet. Listed so it is not mistaken for either.")
        for name in orphans:
            add(f"    {name}")

    if registration is not None:
        verb = "would register" if registration["dry_run"] else "registered"
        add(f"\n{verb} {len(registration['added'])} new, "
            f"{len(registration['updated'])} updated, "
            f"{len(registration['skipped'])} already complete in "
            f"{registration['collection']}")
    return "\n".join(out)


# ── cli ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--campaign", default="",
                        help="campaign holding npcs-full.md and characters/. "
                             "Resolved read-only: a report should never move the "
                             "GM's data between roots.")
    parser.add_argument("--vault", type=pathlib.Path, required=True,
                        help="the Atlas vault: the folder holding atlas-vtt/ and "
                             "Bestiary/")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION,
                        help=f"Atlas collection to register into "
                             f"(default {DEFAULT_COLLECTION})")
    parser.add_argument("--tags", default=",".join(DEFAULT_TAGS),
                        help="comma-separated tags for every registered token")
    parser.add_argument("--token-art-dir", type=pathlib.Path, action="append",
                        default=[], metavar="DIR",
                        help="an extra folder of portraits named after the "
                             "creature (repeatable, lowest precedence)")
    parser.add_argument("--token-art-zip", type=pathlib.Path, default=None,
                        help="a creator token pack. Never opened unless named here, "
                             "and never extracted: only a member that actually "
                             "matched is copied out, into a temporary folder. "
                             "Nothing third-party is written into the repository.")
    parser.add_argument("--stats", action="store_true",
                        help="report coverage and every uncovered name, write "
                             "nothing at all")
    parser.add_argument("--dry-run", action="store_true",
                        help="also report what registration would change, and "
                             "write nothing at all")
    parser.add_argument("--map-out", type=pathlib.Path, default=None,
                        help="write the statblock -> portrait mapping as JSON. "
                             "Default is nowhere: the report is the deliverable, "
                             "and the vault gets only what Atlas needs.")
    args = parser.parse_args(argv)

    if not args.vault.is_dir():
        print(f"refused: vault {args.vault} is not a directory", file=sys.stderr)
        return 1
    if not args.campaign and not (args.stats or args.dry_run):
        print("statblock_art: --campaign is required to report anything but the "
              "Bestiary notes.", file=sys.stderr)
        return 1

    campaign = None
    if args.campaign:
        campaign = paths.find_campaign(args.campaign, migrate=False)
        if not campaign.is_dir():
            print(f"statblock_art: campaign {args.campaign!r} not found (looked in "
                  f"{campaign}). Set GM_CAMPAIGN_ROOT if the campaigns live "
                  f"elsewhere.", file=sys.stderr)
            return 1
    else:
        print("statblock_art: no --campaign, so the npcs and pcs sources are not "
              "reported.", file=sys.stderr)

    for directory in args.token_art_dir:
        if not directory.is_dir():
            print(f"refused: --token-art-dir {directory} is not a directory",
                  file=sys.stderr)
            return 1

    tags = [t for t in (x.strip() for x in args.tags.split(",")) if t]
    read_only = args.stats or args.dry_run

    try:
        subs = age_substitutions(args.vault)
        index = build_index(args.vault, args.token_art_dir, args.token_art_zip)
        sources = collect(args.vault, campaign, index, subs)

        registration = None
        matched = [r for _, records in sources for r in records if r.get("art")]
        # `--stats` never registers at all; `--dry-run` registers nothing and says
        # what it would have; a bare run registers. `args.dry_run` and not an
        # inversion of `--stats` is the flag that belongs here: the first version
        # of this passed `dry_run=not args.stats`, which made a real run a dry run
        # and reported "would register 21 new" on every execution of a loop that
        # is documented as safe to repeat.
        if not read_only or (args.dry_run and matched):
            registration = register_matched(
                args.vault, matched, index, args.collection, tags,
                dry_run=args.dry_run)

        print(f"statblock_art: {args.campaign or '(no campaign)'} -> {args.vault}")
        print(build_report(sources, index, args.vault, registration))

        # Stamped AFTER the report so a run that refuses registration still reports
        # what it found, and stamped in the same pass that resolves the art so the
        # `image:` value and the registered token cannot disagree.
        stamped, unnoted = stamp_notes(args.vault, matched, args.collection,
                                           args.dry_run)
        verb = "would stamp" if args.dry_run else "stamped"
        print(f"\n{verb} image: frontmatter on {stamped} note(s)")
        if unnoted:
            print(f"  {len(unnoted)} matched a picture but have no statblock note "
                  f"to hang it on, and none was invented: "
                  f"{', '.join(sorted(unnoted)[:8])}"
                  f"{' ...' if len(unnoted) > 8 else ''}")
        print("  Atlas reads `image:` from the note; without it every statblock "
              "scans as 'Missing image' even when a portrait exists.")

        if args.map_out:
            write_text(args.map_out, json.dumps({
                "campaign": args.campaign,
                "vault": str(args.vault),
                "pools": [{"pool": pool, "files": count} for pool, count in index.pools],
                "sources": {name: records for name, records in sources},
            }, indent=1, ensure_ascii=False) + "\n")
            print(f"\nwrote the mapping to {args.map_out}")
        elif read_only:
            print("\nReport only. Nothing was written.")
        return 0
    except rt.Refused as exc:
        # The BASE class, deliberately, not this script's own `Refused`. Catching
        # the subclass would let every refusal `register()` raises through
        # uncaught: a directory that is not an Atlas vault, a corrupt
        # `assets-metadata.json`, and a collection Atlas does not have are the three
        # that keep the shared index intact, and they are raised from inside a call
        # this script makes on the GM's behalf. Their wording is already about this
        # situation and is printed rather than reworded.
        print(f"refused: {exc}", file=sys.stderr)
        return 1



# ── the frontmatter Atlas actually reads ────────────────────────────────────
#
# Atlas does not infer a picture from a folder and a name. Its "Create tokens"
# importer reads `image:` or `token-image:` out of the statblock note's own
# frontmatter and resolves that link against the vault; with neither key present
# every note scans as `missing-image`, and the panel reports one row per note as
# "Missing image". That is what 405 rows of it looks like, and it is a wiring
# gap rather than a missing-art problem: the 73 creatures that DO have a portrait
# still scanned as missing, because nothing wrote the key.
#
# The value is a bare vault-relative path, not a wikilink. Atlas accepts both --
# it strips `[[ ]]`, a `|display` suffix and a `#heading` -- but it resolves a
# relative link against the NOTE first and only falls back to `getAbstractFileByPath`.
# A note in `Bestiary/` linking `[[aboleth.png]]` would look for the picture beside
# the note, where it is not; the vault-relative form has one meaning everywhere.
#
# This edits the FRONTMATTER ONLY, and leaves the note body untouched, so it is
# safe on a note that is regenerated on every export -- but it means the exporters
# do not know about it, and re-running an exporter drops the key again until
# statblock_art.py runs after it. That ordering is the pipeline, not a bug, and it
# is the same reason `register_tokens.py` is a separate step.

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)
_IMAGE_KEY = re.compile(r"^(image|token-image):.*$", re.MULTILINE)


def stamp_image(note: pathlib.Path, image_path: str) -> bool:
    """Write `image:` into a note's frontmatter. True if the file changed.

    The key is placed before `statblock:` so the frontmatter reads in the order
    Atlas uses it, and any existing `image:`/`token-image:` is replaced rather
    than duplicated -- a note with two `image:` keys is valid YAML that resolves
    to whichever the parser happens to read last.
    """
    try:
        text = note.read_text(encoding="utf-8")
    except OSError:
        return False
    match = _FRONTMATTER.match(text)
    if not match:
        return False
    block, body = match.group(1), text[match.end():]
    wanted = f"image: {image_path}"
    if re.search(rf"^image:\s*{re.escape(image_path)}\s*$", block, re.MULTILINE):
        return False
    block = _IMAGE_KEY.sub("", block).strip("\n")
    lines = block.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("statblock:"):
            lines.insert(i, wanted)
            break
    else:
        lines.insert(0, wanted)
    note.write_text("---\n" + "\n".join(lines) + f"\n---\n{body}", encoding="utf-8")
    return True


def registered_path(vault: pathlib.Path, art_filename: str,
                    collection: str) -> str | None:
    """The vault-relative path `register_tokens` put this picture at, or None.

    The stamped value has to be that path and not the bare filename. Atlas
    resolves a link by trying `getFirstLinkpathDest` RELATIVE TO THE NOTE first
    and only then `vault.getAbstractFileByPath(normalizePath(s))`, so a bare
    `aboleth.png` in `Bestiary/Aboleth.md` resolves to a note-relative path that
    does not exist and then to a vault-root path that does not exist either. The
    scan then reports `missing-image` for a creature that has a portrait sitting
    in `atlas-vtt/collections/Strixhaven/tokens/` -- which is exactly the state
    this whole addition exists to fix, so the difference matters.

    Taken from the file on disk rather than recomputed, because `register()` also
    SLUGS the stem and lowercases the suffix; guessing that wrong would write a
    path that looks right and 404s.
    """
    stem, dot, ext = art_filename.rpartition(".")
    if not dot:
        return None
    directory = vault / "atlas-vtt" / "collections" / collection / "tokens"
    for suffix in (f".{ext.lower()}", f".{ext}"):
        candidate = directory / f"{slug(stem)}{suffix}"
        if candidate.is_file():
            return rt.vault_relative(vault, candidate)
    return None


def stamp_notes(vault: pathlib.Path, matched: list[dict],
                collection: str, dry_run: bool) -> tuple[int, list[str]]:
    """Stamp every matched note. Returns (notes stamped, names with no note).

    A creature with art but no NOTE is not stamped and not invented: the note is
    what Atlas reads, so a portrait with nothing to hang it on is reported and
    skipped rather than written somewhere Atlas will not look.
    """
    stamped, missing = 0, []
    for record in matched:
        art = record.get("art")
        note = _note_for(vault, record["name"])
        path = registered_path(vault, art, collection) if art else None
        if not path or note is None:
            missing.append(record["name"])
            continue
        if dry_run or stamp_image(note, path):
            stamped += 1
    return stamped, missing


def _note_for(vault: pathlib.Path, name: str) -> pathlib.Path | None:
    """The statblock note that provides this creature, if the vault has one.

    Curated folders win over the derived ones, and that is the GM's recorded
    decision: a person can be in both `Bestiary/` and `NPCs/`, and the curated
    note is the one with real numbers.

    Matching is on the FOLDED stem, not the raw one. `export_bestiary.safe_name`
    strips punctuation from the note's filename while the record keeps it, so
    `Augusta, Order Returned (Lorehold, interim dean)` is written to disk as
    `Augusta Order Returned Lorehold interim dean.md` and an exact lookup finds
    nothing. Three of the nine approved NPC matches are written that way, and
    "matched a picture but has no note" is the same failure with the diagnosis
    reversed.
    """
    key = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    for folder in ("Bestiary", "bestiary", "NPCs", "npcs", "PCs", "pcs",
                   "Characters", "characters"):
        directory = vault / folder
        if not directory.is_dir():
            continue
        for note in sorted(directory.glob("*.md")):
            if re.sub(r"[^a-z0-9]+", " ", note.stem.lower()).strip() == key:
                return note
    return None


if __name__ == "__main__":
    raise SystemExit(main())
