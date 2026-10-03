"""A creature's defenses must survive the build and reach the table.

WHY
===
Resistance, immunity and vulnerability decide how much damage a hit actually
does. The upstream SRD carries all four fields; the normaliser kept `hp`, `ac`
and `speed` and dropped them, so nothing downstream could show or apply them.

A party pouring fire into something immune to fire and watching full damage
land is being told something false about the world — and with no record for the
GM to consult, the same creature gets adjudicated differently from one turn to
the next.

These tests run against the GENERATED dataset and always test the normaliser
itself, which is the part that regressed. The dataset is gitignored, so an
unprovisioned checkout fails rather than skipping: `scripts/provision_srd.py`
builds it, and CI runs that before the suite (dnd-gm#289).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest

from tests.conftest import require_srd_dataset

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"

_spec = importlib.util.spec_from_file_location(
    "build_srd", ROOT / "systems" / "dnd5e" / "build_srd.py"
)
build_srd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_srd)

#: Shaped exactly like an upstream record: damage_* are plain strings,
#: condition_immunities are {index,name,url} objects. They need different
#: flattening, which is the part most likely to be got wrong.
UPSTREAM_SAMPLE = {
    "name": "Air Elemental",
    "index": "air-elemental",
    "damage_resistances": ["lightning", "thunder"],
    "damage_immunities": ["poison"],
    "damage_vulnerabilities": [],
    "condition_immunities": [
        {"index": "exhaustion", "name": "Exhaustion", "url": "/api/x"},
        {"index": "prone", "name": "Prone", "url": "/api/y"},
    ],
}


class NormaliserTests(unittest.TestCase):
    def test_all_four_defense_fields_survive_normalisation(self):
        out = build_srd._norm_monster(UPSTREAM_SAMPLE)
        self.assertEqual(out["resistances"], "lightning, thunder")
        self.assertEqual(out["immunities"], "poison")
        self.assertEqual(out["condition_immunities"], "Exhaustion, Prone")

    def test_an_object_shaped_field_is_flattened_by_name_not_repr(self):
        """condition_immunities are objects. Joining them raw yields dict reprs."""
        out = build_srd._norm_monster(UPSTREAM_SAMPLE)
        self.assertNotIn("{", out["condition_immunities"])
        self.assertNotIn("index", out["condition_immunities"])

    def test_empty_stays_empty_rather_than_becoming_a_placeholder(self):
        """An absent defense must read as 'none', never as 'unknown'."""
        out = build_srd._norm_monster(UPSTREAM_SAMPLE)
        self.assertEqual(out["vulnerabilities"], "")

    def test_a_creature_with_no_defenses_at_all_normalises_cleanly(self):
        out = build_srd._norm_monster({"name": "Commoner"})
        for k in ("resistances", "immunities", "vulnerabilities",
                  "condition_immunities"):
            self.assertEqual(out[k], "", k)


class FormatterTests(unittest.TestCase):
    def test_the_lookup_block_shows_defenses(self):
        import sys
        sys.path.insert(0, str(ROOT / "systems" / "dnd5e"))
        import lookup

        text = lookup._fmt_monster(build_srd._norm_monster(UPSTREAM_SAMPLE))
        self.assertIn("Resistant: lightning, thunder", text)
        self.assertIn("Immune: poison", text)
        self.assertIn("Condition Immune: Exhaustion, Prone", text)
        # absent, not printed as an empty label
        self.assertNotIn("Vulnerable:", text)


class BuiltDatasetTests(unittest.TestCase):
    """The same four fields, counted across the whole built dataset.

    Was `@unittest.skipUnless(DATA.exists(), ...)`. That is two tests reporting as
    *skipped* on every fresh clone and every CI run, which is two tests the suite
    claims to have checked and has not. `scripts/provision_srd.py` runs before the
    suite and CI provisions the dataset, so absence is now a failure that names
    the command. See `tests/conftest.py::require_srd_dataset` for why this is a
    failure and not a skip.

    `setUp` and not `setUpClass`, deliberately: a `pytest.fail` raised in
    `setUpClass` is reported as an ERROR at setup, while the same call in `setUp`
    lands in the call phase and is reported as FAILED. Both are red and both are
    impossible to mistake for a pass, but only one of them is a failed assertion,
    and `agents/dev/verifier.md` asks for that distinction by name.
    """

    def setUp(self):
        require_srd_dataset()
        self.monsters = json.loads(DATA.read_text(encoding="utf-8"))["monsters"]

    def test_defenses_are_populated_across_the_dataset(self):
        """Counts, not presence. A field present on 0 creatures is the bug."""
        counts = {
            k: sum(1 for m in self.monsters if m.get(k))
            for k in ("resistances", "immunities", "vulnerabilities",
                      "condition_immunities")
        }
        for k, n in counts.items():
            self.assertGreater(n, 0, f"{k} populated on no creature: {counts}")

    def test_a_known_creature_has_its_known_immunity(self):
        ae = next((m for m in self.monsters if m["name"] == "Air Elemental"), None)
        self.assertIsNotNone(ae, "Air Elemental missing from the dataset")
        self.assertIn("poison", ae["immunities"])


if __name__ == "__main__":
    unittest.main()
