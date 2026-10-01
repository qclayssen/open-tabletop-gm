"""The SRD dataset must carry the upstream art ADDRESS without claiming the art.

WHY
===
Upstream ships an `image` path on every monster and `_norm_monster` threw it
away, so 264 of the SRD creatures read as having no portrait anywhere when the
address of one was sitting in the payload the whole time. Carrying it is right.
What it must NOT become is a claim that a file exists locally: the art is
AI-generated with no published licence, it is not in this repository, and
`display/tokens/` is gitignored for exactly that reason.

So this file pins three things: the field is captured, it is resolved to a
usable absolute URL, and the two shapes that must be refused -- an absent
`image` and a relative path that cannot be resolved to a host -- both produce
empty rather than a broken link. An `image:` that 403s at the table is worse
than no `image:` at all, because it looks like art and is not.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "systems" / "dnd5e"))

import build_srd


def _raw(**over):
    """A monster record as upstream sends it, with the fields _norm_monster reads."""
    base = {
        "name": "Aboleth", "index": "aboleth",
        "armor_class": [{"value": 17}], "speed": {"walk": "10 ft."},
        "challenge_rating": "10", "hit_points": 135, "hit_dice": "18d10",
        "strength": 21, "dexterity": 9, "constitution": 15,
        "intelligence": 18, "wisdom": 15, "charisma": 18,
        "alignment": "lawful evil", "size": "Large", "type": "aberration",
        "languages": "Deep Speech, telepathy 120 ft.",
        "special_abilities": [], "actions": [], "legendary_actions": [],
        "damage_resistances": [], "damage_immunities": [],
        "damage_vulnerabilities": [], "condition_immunities": [],
    }
    base.update(over)
    return base


class ImageAddressIsCaptured(unittest.TestCase):
    def test_a_root_relative_path_is_hosted(self):
        """Upstream stores it root-relative because it was written to be served by
        the API that ships it. That is not a URL a note can render, so the host is
        applied here rather than left to whoever reads the field later."""
        rec = build_srd._norm_monster(_raw(image="/api/images/monsters/aboleth.png"))
        self.assertEqual(rec["image"],
                         build_srd.IMAGE_BASE + "/api/images/monsters/aboleth.png")

    def test_an_absolute_url_is_left_alone(self):
        rec = build_srd._norm_monster(
            _raw(image="https://example.invalid/other/aboleth.png"))
        self.assertEqual(rec["image"], "https://example.invalid/other/aboleth.png")

    def test_an_absent_image_is_empty_rather_than_a_broken_link(self):
        self.assertEqual(build_srd._norm_monster(_raw())["image"], "")

    def test_an_unresolvable_relative_path_is_refused(self):
        """A bare relative path names a location only relative to a server we
        have not chosen. Emitting it would put `images/aboleth.png` in a note,
        which resolves against nothing and renders as a missing image."""
        rec = build_srd._norm_monster(_raw(image="images/monsters/aboleth.png"))
        self.assertEqual(rec["image"], "")

    def test_a_non_string_image_is_refused_rather_than_stringified(self):
        """`str()` on a dict would produce a truthy value that is not an address,
        and a note would carry `image: "{'src': ...}"` as if it were a URL."""
        for junk in ({"src": "x"}, 42, ["a.png"]):
            self.assertEqual(build_srd._norm_monster(_raw(image=junk))["image"], "",
                             junk)

    def test_the_host_is_https(self):
        """Not a style point. The same PNGs are served over plain http from the
        bucket, and a note rendered in Obsidian would fetch a creature portrait
        without encryption."""
        self.assertTrue(build_srd.IMAGE_BASE.startswith("https://"))


class ArtIsNeverFetchedByTheBuilder(unittest.TestCase):
    def test_the_builder_does_not_download_the_art(self):
        """The builder writes a dataset. Downloading 575 MB of unlicensed art to
        annotate it would put a download inside a step that is supposed to be
        safe to re-run, and would make `build_srd.py` a fetcher of third-party
        art. The address is the whole deliverable; fetching is a separate,
        deliberate act with its own credit and its own ignore rule."""
        source = (ROOT / "systems" / "dnd5e" / "build_srd.py").read_text(encoding="utf-8")
        self.assertNotIn("urllib.request.urlretrieve", source)
        self.assertNotIn("shutil.copyfileobj", source)


if __name__ == "__main__":
    unittest.main()
