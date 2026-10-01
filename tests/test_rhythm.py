"""rhythm.py: tempo x pressure, three tiers, resolved with provenance.

Run: PYTHONPATH=. pytest tests/test_rhythm.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "scripts" / "localdm"))

import rhythm  # noqa: E402

WORLD_HEAD = "# World: t\n\n## Campaign Tone & Genre\n- **Tone:** dark\n\n"


def _world(tempo=None, pressure=None, extra=""):
    lines = ["## Campaign Rhythm", "```yaml"]
    if tempo is not None:
        lines.append(f"tempo: {tempo}      # brisk | measured | calm")
    if pressure is not None:
        lines.append(f"pressure: {pressure}")
    if extra:
        lines.append(extra)
    lines += ["```", ""]
    return WORLD_HEAD + "\n".join(lines)


PLAN = """## Session Plan
```yaml
session: 7
in_world: "22 Harvestmoon 1247"
tempo: brisk
pressure: null
scenes:
  - id: s1
    label: "Salt Ledger"
    tempo: null
    pressure: urgent     # local
    pressure_point: false
  - id: s2
    label: "Council Room"
    tempo: calm
    pressure: none
    pressure_point: true
    stall_after: 2
  - id: s3
    label: "Docks"
current_scene: s1
last_emitted: s1:brisk:urgent
```

"""
STATE_HEAD = "# Campaign: t\n\n## Current Situation\n- **Location:** x\n\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = os.environ.get("GM_CAMPAIGN_ROOT")
        os.environ["GM_CAMPAIGN_ROOT"] = self.tmp.name
        self.addCleanup(self._restore)
        self.dir = pathlib.Path(self.tmp.name) / "campaigns" / "t"
        self.dir.mkdir(parents=True)

    def _restore(self):
        if self.old is None:
            os.environ.pop("GM_CAMPAIGN_ROOT", None)
        else:
            os.environ["GM_CAMPAIGN_ROOT"] = self.old

    def write(self, world="", state=""):
        (self.dir / "world.md").write_text(world or WORLD_HEAD, encoding="utf-8")
        (self.dir / "state.md").write_text(state or STATE_HEAD, encoding="utf-8")

    def cli(self, *args):
        env = dict(os.environ)
        p = subprocess.run([sys.executable, str(SCRIPTS / "rhythm.py"), "-c", "t", *args],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        return p.returncode, p.stdout, p.stderr


class Presets(unittest.TestCase):
    def test_seven_presets_cover_both_axes(self):
        self.assertEqual(len(rhythm.PRESETS), 7)
        for name, (tempo, pressure) in rhythm.PRESETS.items():
            self.assertIn(tempo, rhythm.TEMPO, name)
            self.assertIn(pressure, rhythm.PRESSURE, name)
        self.assertEqual(rhythm.PRESETS["dread"], ("calm", "urgent"))
        self.assertEqual(rhythm.PRESETS["caper"], ("brisk", "none"))


class Resolution(Base):
    def test_defaults_when_nothing_set(self):
        self.write()
        d = rhythm.load("t")
        self.assertEqual((d["campaign_tempo"], d["campaign_pressure"]), ("measured", "ambient"))
        self.assertEqual(d["scenes"], [])
        self.assertEqual(d["warnings"], [])

    def test_no_session_plan_exports_empty_scenes(self):
        self.write(_world("calm", "none"))
        d = json.loads(rhythm.export_json("t"))
        self.assertEqual(d["scenes"], [])
        self.assertEqual(d["campaign_tempo"], "calm")

    def test_all_inheritance_depths_and_independent_axes(self):
        self.write(_world("calm", "none"), STATE_HEAD + PLAN)
        sc = {s["id"]: s for s in rhythm.load("t")["scenes"]}
        # s1: tempo from session (scene null), pressure local: axes resolve independently
        self.assertEqual((sc["s1"]["tempo_resolved"], sc["s1"]["tempo_from"]), ("brisk", "session"))
        self.assertEqual((sc["s1"]["pressure_resolved"], sc["s1"]["pressure_from"]), ("urgent", "scene"))
        # s2: both local
        self.assertEqual((sc["s2"]["tempo_from"], sc["s2"]["pressure_from"]), ("scene", "scene"))
        # s3: tempo from session, pressure skips session (null) to campaign
        self.assertEqual((sc["s3"]["tempo_resolved"], sc["s3"]["tempo_from"]), ("brisk", "session"))
        self.assertEqual((sc["s3"]["pressure_resolved"], sc["s3"]["pressure_from"]), ("none", "campaign"))

    def test_default_tier_when_no_ancestor_sets_a_value(self):
        self.write(state=STATE_HEAD + "## Session Plan\n```yaml\nscenes:\n  - id: a\n```\n")
        sc = rhythm.load("t")["scenes"][0]
        self.assertEqual((sc["tempo_resolved"], sc["tempo_from"]), ("measured", "default"))
        self.assertEqual((sc["pressure_resolved"], sc["pressure_from"]), ("ambient", "default"))

    def test_every_preset_resolves_on_a_scene(self):
        for name, (tempo, pressure) in rhythm.PRESETS.items():
            block = f"## Session Plan\n```yaml\nscenes:\n  - id: a\n    tempo: {tempo}\n    pressure: {pressure}\n```\n"
            self.write(state=STATE_HEAD + block)
            sc = rhythm.load("t")["scenes"][0]
            self.assertEqual((sc["tempo_resolved"], sc["pressure_resolved"]), (tempo, pressure), name)

    def test_export_is_byte_deterministic(self):
        self.write(_world("calm", "urgent"), STATE_HEAD + PLAN)
        a, b = rhythm.export_json("t"), rhythm.export_json("t")
        self.assertEqual(a, b)
        self.assertEqual(list(json.loads(a))[:3], ["campaign", "campaign_tempo", "campaign_pressure"])


class Legacy(Base):
    def _flags(self, value):
        return STATE_HEAD + f"## Session Flags\n- **pacing:** {value}\n\n"

    def test_three_legacy_values_map(self):
        for old, want in (("adventure", ("brisk", "ambient")),   # adventure sets tempo only
                          ("mixed", ("measured", "ambient")),
                          ("downtime", ("calm", "ambient"))):
            self.write(state=self._flags(old))
            d = rhythm.load("t")
            self.assertEqual((d["campaign_tempo"], d["campaign_pressure"]), want, old)
            self.assertEqual(d["warnings"], [])

    def test_adventure_leaves_pressure_unset_so_it_can_inherit_nothing(self):
        self.write(state=self._flags("adventure") + "## Session Plan\n```yaml\nscenes:\n  - id: a\n```\n")
        sc = rhythm.load("t")["scenes"][0]
        self.assertEqual((sc["tempo_resolved"], sc["tempo_from"]), ("brisk", "campaign"))
        self.assertEqual(sc["pressure_from"], "default")

    def test_pacing_key_in_rhythm_block(self):
        self.write(_world(extra="pacing: downtime"))
        self.assertEqual(rhythm.load("t")["campaign_tempo"], "calm")

    def test_explicit_axis_beats_legacy(self):
        self.write(_world("brisk"), self._flags("downtime"))
        d = rhythm.load("t")
        self.assertEqual((d["campaign_tempo"], d["campaign_pressure"]), ("brisk", "ambient"))

    def test_unset_stays_unset(self):
        self.write()
        d = rhythm.load("t")
        self.assertEqual((d["campaign_tempo"], d["campaign_pressure"]), ("measured", "ambient"))

    def test_invalid_legacy_value_warns(self):
        self.write(state=self._flags("frantic"))
        w = rhythm.load("t")["warnings"]
        self.assertEqual(len(w), 1)
        self.assertIn("state.md:", w[0])
        self.assertIn("frantic", w[0])


class Invalid(Base):
    def test_invalid_value_warns_with_file_line_value_and_falls_back(self):
        self.write(_world("dred", "urgent"), STATE_HEAD + PLAN.replace("tempo: calm", "tempo: fast"))
        d = rhythm.load("t")
        self.assertEqual(d["campaign_tempo"], "measured")          # fell back to default
        self.assertEqual(d["campaign_pressure"], "urgent")
        self.assertEqual(len(d["warnings"]), 2)
        world_w = next(w for w in d["warnings"] if w.startswith("world.md"))
        text = (self.dir / "world.md").read_text(encoding="utf-8").splitlines()
        line = int(world_w.split(":")[1])
        self.assertIn("dred", text[line - 1])
        self.assertIn("'dred'", world_w)
        s2 = {s["id"]: s for s in d["scenes"]}["s2"]
        self.assertEqual((s2["tempo_resolved"], s2["tempo_from"]), ("brisk", "session"))  # next tier up

    def test_unfilled_template_placeholders_are_silent(self):
        tpl = (REPO / "templates" / "world.md").read_text(encoding="utf-8")
        self.write(tpl)
        d = rhythm.load("t")
        self.assertEqual(d["warnings"], [])
        self.assertEqual(d["campaign_tempo"], "measured")

    def test_validate_exit_code(self):
        self.write(_world("dred"))
        rc, out, _ = self.cli("validate")
        self.assertEqual(rc, 1)
        self.assertIn("dred", out)
        self.write(_world("calm"))
        self.assertEqual(self.cli("validate")[0], 0)

    def test_calendar_drift_is_a_warning(self):
        self.write(_world("calm"), STATE_HEAD + PLAN)
        (self.dir / "calendar.json").write_text(json.dumps(
            {"day": 25, "month": 1, "year": 1247, "months": ["Harvestmoon"]}), encoding="utf-8")
        w = rhythm.load("t")["warnings"]
        self.assertTrue(any("calendar.json" in x for x in w))
        (self.dir / "calendar.json").write_text(json.dumps(
            {"day": 22, "month": 1, "year": 1247, "months": ["Harvestmoon"]}), encoding="utf-8")
        self.assertEqual(rhythm.load("t")["warnings"], [])


class Malformed(Base):
    def test_bad_yaml_is_a_hard_error_with_file_and_line(self):
        bad = "## Session Plan\n```yaml\nsession: 1\nscenes:\n  - id: a\n   tempo: calm\n```\n"
        self.write(state=STATE_HEAD + bad)
        with self.assertRaises(rhythm.RhythmError) as cm:
            rhythm.load("t")
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("state.md:"), msg)
        line = int(msg.split(":")[1])
        state = (self.dir / "state.md").read_text(encoding="utf-8").splitlines()
        self.assertIn("tempo: calm", state[line - 1])

    def test_cli_exits_nonzero_and_prints_no_json(self):
        self.write(state=STATE_HEAD + "## Session Plan\n```yaml\nscenes: [\n```\n")
        rc, out, err = self.cli("export", "--json")
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("state.md:", err)

    def test_scenes_not_a_list_is_an_error(self):
        self.write(state=STATE_HEAD + "## Session Plan\n```yaml\nscenes: nope\n```\n")
        with self.assertRaises(rhythm.RhythmError):
            rhythm.load("t")


class Mutation(Base):
    def test_set_scene_axis_keeps_comments_and_prose(self):
        state = STATE_HEAD + "Prose before.\n\n" + PLAN + "Prose after.\n"
        self.write(state=state)
        rc, out, _ = self.cli("set", "s1", "pressure", "none")
        self.assertEqual(rc, 0, out)
        new = (self.dir / "state.md").read_text(encoding="utf-8")
        self.assertIn("pressure: none     # local", new)
        self.assertIn("Prose before.", new)
        self.assertIn("Prose after.", new)
        self.assertEqual(new.replace("pressure: none     # local", "pressure: urgent     # local"), state)

    def test_set_inserts_missing_key_in_the_right_scene(self):
        self.write(state=STATE_HEAD + PLAN)
        self.assertEqual(self.cli("set", "s3", "tempo", "calm")[0], 0)
        sc = {s["id"]: s for s in rhythm.load("t")["scenes"]}
        self.assertEqual((sc["s3"]["tempo"], sc["s2"]["tempo"], sc["s1"]["tempo"]), ("calm", "calm", None))

    def test_set_preset_sets_both_axes(self):
        self.write(state=STATE_HEAD + PLAN)
        self.assertEqual(self.cli("set", "s3", "preset", "dread")[0], 0)
        sc = {s["id"]: s for s in rhythm.load("t")["scenes"]}["s3"]
        self.assertEqual((sc["tempo_resolved"], sc["pressure_resolved"]), ("calm", "urgent"))

    def test_set_null_returns_to_inheritance(self):
        self.write(state=STATE_HEAD + PLAN)
        self.assertEqual(self.cli("set", "s2", "tempo", "null")[0], 0)
        sc = {s["id"]: s for s in rhythm.load("t")["scenes"]}["s2"]
        self.assertEqual((sc["tempo_resolved"], sc["tempo_from"]), ("brisk", "session"))

    def test_set_session_and_campaign(self):
        self.write(_world("brisk"), STATE_HEAD + PLAN)
        self.assertEqual(self.cli("set", "session", "pressure", "urgent")[0], 0)
        self.assertEqual(self.cli("set", "campaign", "tempo", "calm")[0], 0)
        d = rhythm.load("t")
        self.assertEqual((d["session_pressure"], d["campaign_tempo"]), ("urgent", "calm"))
        self.assertIn("# brisk | measured | calm", (self.dir / "world.md").read_text(encoding="utf-8"))

    def test_set_campaign_in_unfilled_template(self):
        self.write((REPO / "templates" / "world.md").read_text(encoding="utf-8"))
        self.assertEqual(self.cli("set", "campaign", "preset", "caper")[0], 0)
        d = rhythm.load("t")
        self.assertEqual((d["campaign_tempo"], d["campaign_pressure"]), ("brisk", "none"))

    def test_dry_run_writes_nothing(self):
        self.write(_world("calm"), STATE_HEAD + PLAN)
        before = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        rc, out, _ = self.cli("set", "s1", "tempo", "calm", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("would set", out)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.dir.iterdir()})

    def test_invalid_value_and_unknown_scene_refused(self):
        self.write(state=STATE_HEAD + PLAN)
        self.assertEqual(self.cli("set", "s1", "tempo", "fast")[0], 1)
        self.assertEqual(self.cli("set", "nope", "tempo", "calm")[0], 1)


class DigestAndBoundaries(Base):
    def test_unfilled_rhythm_costs_zero_context(self):
        from localdm import context
        self.write((REPO / "templates" / "world.md").read_text(encoding="utf-8"))
        digest = context.notes_digest(self.dir)
        for word in ("Rhythm", "tempo", "pressure", "yaml", "session_shape", "```"):
            self.assertNotIn(word, digest)

    def test_filled_rhythm_reaches_digest_without_comments(self):
        from localdm import context
        self.write(_world("calm", "urgent"))
        digest = context.notes_digest(self.dir)
        self.assertIn("tempo: calm", digest)
        self.assertNotIn("brisk | measured", digest)

    def test_state_digest_sections_unchanged_by_rhythm(self):
        from localdm import context
        self.assertNotIn("Campaign Rhythm", context.DIGEST_SECTIONS)
        self.assertNotIn("Session Plan", context.DIGEST_SECTIONS)

    def test_tactics_never_reads_tempo(self):
        for p in (REPO / "scripts" / "tactics").rglob("*.py"):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("rhythm", text, p.name)


if __name__ == "__main__":
    unittest.main()
