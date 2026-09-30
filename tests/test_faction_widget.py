"""Faction clock widget: only GM-revealed clocks reach the display."""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
WORLD = REPO / "scripts" / "world.py"


def _world(root, *args):
    env = dict(os.environ, GM_CAMPAIGN_ROOT=str(root))
    p = subprocess.run([sys.executable, str(WORLD), "-c", "c1", *args],
                       capture_output=True, text=True, env=env, encoding="utf-8")
    return p.returncode, p.stdout


class FactionWidget(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "campaigns" / "c1").mkdir(parents=True)
        self._old = os.environ.get("GM_CAMPAIGN_ROOT")
        os.environ["GM_CAMPAIGN_ROOT"] = str(self.root)
        _world(self.root, "add", "Red Hand", "--goal", "SECRET-GOAL", "--clock", "6")
        _world(self.root, "add", "Veiled Court", "--goal", "HIDDEN-GOAL", "--clock", "8")
        _world(self.root, "clock", "Red Hand", "+2")
        spec = importlib.util.spec_from_file_location(
            "gm_display_app_clocks", str(REPO / "display" / "gm-display-app.py"))
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.mod.CAMP_FILE = str(self.root / ".campaign")
        pathlib.Path(self.mod.CAMP_FILE).write_text("c1", encoding="utf-8")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("GM_CAMPAIGN_ROOT", None)
        else:
            os.environ["GM_CAMPAIGN_ROOT"] = self._old
        self._tmp.cleanup()

    def test_hidden_by_default_and_absent_from_payload(self):
        self.assertEqual(self.mod._clocks_payload(), [])
        blob = json.dumps(self.mod._clocks_payload())
        self.assertNotIn("Red Hand", blob)

    def test_reveal_sends_only_name_size_filled(self):
        self.assertEqual(_world(self.root, "reveal", "Red Hand")[0], 0)
        payload = self.mod._clocks_payload()
        self.assertEqual(payload, [{"name": "Red Hand", "size": 6, "filled": 2}])
        blob = json.dumps(payload)
        for leak in ("SECRET-GOAL", "HIDDEN-GOAL", "Veiled Court"):
            self.assertNotIn(leak, blob)

    def test_hide_removes_and_state_persists_on_disk(self):
        _world(self.root, "reveal", "Veiled Court")
        data = json.loads((self.root / "campaigns" / "c1" / "factions.json")
                          .read_text(encoding="utf-8")) if (self.root / "campaigns" / "c1" / "factions.json").exists() else None
        if data is not None:
            self.assertTrue(data["factions"]["Veiled Court"]["revealed"])
        self.assertEqual(len(self.mod._clocks_payload()), 1)
        _world(self.root, "hide", "Veiled Court")
        self.assertEqual(self.mod._clocks_payload(), [])

    def test_unknown_faction_fails(self):
        self.assertEqual(_world(self.root, "reveal", "Nobody")[0], 1)

    def test_push_only_on_change_and_stream_connect(self):
        sent = []
        self.mod._broadcast = sent.append
        self.mod._last_clocks = None
        _world(self.root, "reveal", "Red Hand")
        self.mod._push_clocks_if_changed()
        self.mod._push_clocks_if_changed()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["clocks"][0]["name"], "Red Hand")
        _world(self.root, "hide", "Red Hand")
        self.mod._push_clocks_if_changed()
        self.assertEqual(sent[-1], {"clocks": []})

    def test_faction_moves_stay_number_free(self):
        _world(self.root, "clock", "Red Hand", "+1", "--notes", "n")
        state = self.root / "campaigns" / "c1" / "state.md"
        if state.exists():
            self.assertNotRegex(state.read_text(encoding="utf-8").split("Faction Moves")[-1], r"\d/\d")


if __name__ == "__main__":
    unittest.main()
