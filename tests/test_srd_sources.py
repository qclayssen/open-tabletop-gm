"""The SRD dataset builder must fetch from a path that still exists.

WHY
===
`5e-bits/5e-database` added a language directory — `src/2014/en/` rather than
`src/2014/` — and every configured source URL began returning 404. Because the
generated dataset is gitignored, an existing checkout kept working off data it
had built earlier, and the breakage only reached someone cloning fresh.

The failure was also soft: a fetch error printed to stderr and became an empty
list, the build carried on, wrote a dataset containing nothing, and exited 0.
So the two things worth guarding are the path shape and the refusal.
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "systems" / "dnd5e" / "build_srd.py"


def _const(name: str):
    """Read a module-level string constant without importing (no network)."""
    tree = ast.parse(BUILD.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == name:
                    if isinstance(node.value, ast.Constant):
                        return node.value.value
    raise AssertionError(f"{name} not found in {BUILD}")


def _files() -> dict:
    tree = ast.parse(BUILD.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "BITS_FILES":
                    return ast.literal_eval(node.value)
    raise AssertionError("BITS_FILES not found")


class SrdSourceShapeTests(unittest.TestCase):
    """Always run. No network."""

    def test_the_source_base_carries_a_language_segment(self):
        base = _const("RAW_5EBITS")
        tail = base.rstrip("/").rsplit("/", 2)[-2:]
        self.assertEqual(
            len(tail[-1]), 2,
            f"RAW_5EBITS is {base!r} — upstream nests by language "
            "(src/<ruleset>/<lang>/), so a path ending at the ruleset 404s",
        )

    def test_a_failed_fetch_is_not_an_empty_category(self):
        """`SourceUnavailable` must exist and be raised, not swallowed.

        If a fetch failure can turn into `[]`, the build writes an empty
        dataset over a good one and reports success — which is how this broke
        without anyone noticing.
        """
        src = BUILD.read_text(encoding="utf-8")
        self.assertIn("class SourceUnavailable", src)
        self.assertIn("raise SourceUnavailable", src)
        self.assertIn(
            "refusing to overwrite", src,
            "the builder must refuse to write a dataset whose categories all "
            "came back empty",
        )


class SrdEditionTests(unittest.TestCase):
    """The dataset must be 2014, and the engine is 2014. Always run. No network.

    `foundryvtt/dnd5e` ships BOTH editions in one repository: `classes` +
    `classfeatures` + `races` are 2014, `classes24` + `spells24` + `origins24` +
    `feats24` are 2024. The build read the 2024 class packs, so a campaign got
    2014 spells and monsters beside 2024 class features, and `lookup.py feature
    sneak-attack` could answer a 2014 question with 2024 text. Nothing caught
    it, because the build succeeded and the dataset was well formed.

    These tests fail loudly if the edition is ever swapped again, whether by
    editing a constant or by editing a path match.
    """

    # Real upstream paths, both editions, as they exist today.
    TREE = [
        # 2014
        {"path": "packs/_source/classes/wizard.yml"},
        {"path": "packs/_source/classes/fighter.yml"},
        {"path": "packs/_source/classfeatures/rogue/rogue-features/sneak-attack.yml"},
        {"path": "packs/_source/classfeatures/wizard/wizard-features/sculpt-spells.yml"},
        {"path": "packs/_source/classfeatures/shared-features/extra-attack.yml"},
        {"path": "packs/_source/classfeatures/shared-features/fighting-styles/archery.yml"},
        {"path": "packs/_source/races/elf/elf-features/fey-ancestry.yml"},
        # 2024
        {"path": "packs/_source/classes24/wizard/wizard.yml"},
        {"path": "packs/_source/classes24/rogue/class-features/sneak-attack.yml"},
        {"path": "packs/_source/origins24/elf/elf-features/fey-ancestry.yml"},
        {"path": "packs/_source/spells24/spell-fireball.yml"},
        # noise
        {"path": "packs/_source/classfeatures/grappler.yml"},
        {"path": "packs/_source/classes/wizard/_folder.yml"},
    ]

    def _partition(self):
        sys.path.insert(0, str(ROOT / "systems" / "dnd5e"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("otgm_build_srd", BUILD)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)      # no network: everything is under main()
        return mod._partition_fvtt_tree(self.TREE), mod

    def test_the_configured_packs_are_the_2014_ones(self):
        for name, expected in (("FVTT_CLASS_PACK", "classes"),
                               ("FVTT_CLASS_FEATURES", "classfeatures"),
                               ("FVTT_RACES", "races")):
            self.assertEqual(_const(name), expected, f"{name} is not the 2014 pack")

    def test_no_2024_pack_is_referenced_anywhere_in_the_builder(self):
        src = BUILD.read_text(encoding="utf-8")
        for pack in ("classes24", "spells24", "origins24", "feats24", "equipment24"):
            self.assertNotIn(
                f'"{pack}"', src,
                f"the build must not read the 2024 pack {pack!r}: the engine is "
                "2014, and 2024 class text in a 2014 dataset is a wrong answer "
                "presented as a right one",
            )

    def test_the_bits_data_is_the_2014_dataset(self):
        self.assertIn("/src/2014/", _const("RAW_5EBITS"),
                      "5e-bits nests by ruleset then language; this is the 2014 tree")

    def test_the_tree_partition_selects_2014_paths_and_no_2024_ones(self):
        (features, classes), _ = self._partition()
        self.assertEqual(
            classes,
            ["packs/_source/classes/wizard.yml", "packs/_source/classes/fighter.yml"],
            "the 2014 class documents are flat, one YAML per class",
        )
        self.assertIn("packs/_source/classfeatures/rogue/rogue-features/sneak-attack.yml",
                      features)
        self.assertIn("packs/_source/classfeatures/shared-features/extra-attack.yml",
                      features, "a feature that belongs to no one class is still a feature")
        self.assertIn("packs/_source/races/elf/elf-features/fey-ancestry.yml", features)
        for path in features + classes:
            self.assertNotRegex(
                path, r"(classes24|spells24|origins24|feats24|equipment24)",
                f"{path} is 2024 content and must never reach a 2014 dataset",
            )

    def test_a_class_document_loose_in_the_features_pack_is_not_a_feature(self):
        """`classfeatures/grappler.yml` is a class document, not a feature. A
        depth floor on the features pack is what keeps it out."""
        features, classes = self._partition()[0]
        self.assertNotIn("packs/_source/classfeatures/grappler.yml", features)
        self.assertNotIn("packs/_source/classes/wizard/_folder.yml", features + classes,
                         "folder markers are not content")

    def test_features_shared_by_every_class_are_kept(self):
        """Extra Attack and Ability Score Improvement sit one folder deep, at the
        same depth as the pack-root class documents a floor has to exclude. Too
        deep a floor and a core 2014 feature silently vanishes from the dataset."""
        features, _ = self._partition()[0]
        self.assertIn("packs/_source/classfeatures/shared-features/extra-attack.yml", features)


@unittest.skipUnless(
    os.environ.get("OTGM_NETWORK_TESTS") == "1",
    "network test — set OTGM_NETWORK_TESTS=1 to run",
)
class SrdSourceLiveTests(unittest.TestCase):
    """Opt-in. Proves the configured URLs actually resolve TODAY.

    Kept out of the default run because a green suite should not depend on
    GitHub being reachable. Worth running before a release: it is the only
    check that catches upstream moving the files again.
    """

    def test_every_configured_source_responds(self):
        base = _const("RAW_5EBITS")
        for key, filename in _files().items():
            url = f"{base}/{filename}"
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "otgm-tests/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                self.assertEqual(resp.status, 200, f"{key}: {url}")


class SrdFeatureShapeTests(unittest.TestCase):
    """How a feature is labelled. Always run. No network.

    `lookup.py` prints `r.get("class", "")` in the header, so the value this
    puts in `class` is user-visible: None renders as the literal word "None".
    """

    def _norm(self, path, name="Feature", desc="Some text."):
        sys.path.insert(0, str(ROOT / "systems" / "dnd5e"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("otgm_build_srd_norm", BUILD)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._norm_feature(
            {"name": name, "system": {"description": {"value": desc}}}, path)

    def test_a_class_feature_is_labelled_with_its_class(self):
        got = self._norm("packs/_source/classfeatures/rogue/rogue-features/sneak-attack.yml")
        self.assertEqual(got["class"], "rogue")
        self.assertEqual(got["type"], "class")

    def test_a_race_feature_has_no_class_and_says_so_by_type(self):
        got = self._norm("packs/_source/races/elf/elf-features/fey-ancestry.yml")
        self.assertEqual(got["class"], "", "an empty string, not None: the header "
                                           "would print the word None")
        self.assertEqual(got["type"], "race")

    def test_a_feature_shared_by_every_class_is_not_labelled_shared_features(self):
        got = self._norm("packs/_source/classfeatures/shared-features/extra-attack.yml")
        self.assertEqual(got["class"], "", '"[shared-features]" reads as a class name')


if __name__ == "__main__":
    unittest.main()
