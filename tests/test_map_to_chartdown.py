"""test_map_to_chartdown.py: guard the engine map -> Chartdown export.

Run: python3 -m pytest tests/test_map_to_chartdown.py -q

Three kinds of check. The first re-reads the emitted `area` lines with a tiny
independent expander and compares them square for square with the engine's own grid
(`maps.compile_map`), which is what catches an off-by-one in the addresses. The second
pins the refusals and the loss report, because "unknown terrain is refused, never
guessed" and "the losses are said on every run" are promises, not behaviours. The third
runs every shipped map through `@chartdown/core` `parse()` as an optional dev-time oracle,
skipped cleanly when node or the package is absent (CI installs no npm packages).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("map_to_chartdown", ROOT / "scripts" / "map_to_chartdown.py")
m2c = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m2c)

from tactics.maps import compile_map  # noqa: E402  (scripts/ is on sys.path after the load above)


def _spec_map(**kw) -> dict:
    spec = {"name": "Test Room", "width": 6, "height": 4, "base": "floor", "features": []}
    spec.update(kw)
    return spec


def _col(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _cells(rng: str):
    a, _, b = rng.partition("..")
    b = b or a
    (c1, r1), (c2, r2) = (re.fullmatch(r"([A-Z]+)(\d+)", t).groups() for t in (a, b))
    for y in range(int(r1) - 1, int(r2)):
        for x in range(_col(c1), _col(c2) + 1):
            yield (x, y)


def _painted(text: str) -> dict:
    """{(x, y): word} from the [terrain] and [features] lines, read independently."""
    out, section = {}, None
    for line in text.splitlines():
        if line.startswith("["):
            section = line.strip("[]")
        elif section == "terrain":
            word, _, rest = line.partition(" : area ")
            for c in _cells(rest):
                assert c not in out, f"square {c} painted twice"
                out[c] = word
        elif section == "features":
            word, _, rest = line.partition(" : ")
            out.update({c: word for c in _cells(rest)})
    return out


ENGINE_TO_WORD = {"difficult": "rubble", "water": "water", "hazard": "hazard",
                  "wall": "earth", "void": "void", "feature": "boulder"}


def test_every_builtin_terrain_has_a_word_and_floor_is_silent():
    spec = _spec_map(features=[
        {"type": "wall", "x": 0, "y": 0, "w": 2, "h": 1},
        {"type": "water", "x": 2, "y": 0, "w": 2, "h": 2},
        {"type": "difficult", "x": 4, "y": 0},
        {"type": "hazard", "x": 5, "y": 0},
        {"type": "feature", "x": 0, "y": 3},
        {"type": "void", "x": 3, "y": 3, "w": 3, "h": 1},
    ])
    text, _losses, _stats = m2c.build_document(spec, "test-room")
    grid = compile_map(spec)["grid"]
    legend = {"#": "wall", ".": "floor", ",": "difficult", "~": "water",
              "^": "hazard", "o": "feature", "_": "void"}
    expected = {(x, y): ENGINE_TO_WORD[legend[ch]]
                for y, row in enumerate(grid["rows"]) for x, ch in enumerate(row)
                if legend[ch] != "floor"}
    assert _painted(text) == expected


def test_headers_version_scale_and_grid():
    text, _l, _s = m2c.build_document(_spec_map(), "test-room")
    lines = text.splitlines()
    assert lines[0] == "# Test Room"
    assert lines[1] == "map: battlemap"          # map: must be the first header line
    assert "chartdown: 0.8" in lines
    assert "grid: square 6x4" in lines
    assert "scale: 5ft" in lines


def test_grid_header_is_the_only_place_the_shape_is_spelled():
    assert m2c.grid_header({"width": 20, "height": 15}) == "grid: square 20x15"
    src = (ROOT / "scripts" / "map_to_chartdown.py").read_text(encoding="utf-8")
    assert src.count('f"grid:') == 1


def test_hazard_is_declared_in_vocab_but_stdlib_words_are_not():
    text, _l, _s = m2c.build_document(
        _spec_map(features=[{"type": "hazard", "x": 0, "y": 0}, {"type": "water", "x": 1, "y": 0}]), "t")
    assert "[vocab]\nhazard : terrain" in text
    assert "water : terrain" not in text


def test_rectangles_cover_an_l_shape_exactly():
    cells = {(0, 0), (1, 0), (2, 0), (0, 1), (0, 2)}
    rects = m2c._rectangles(cells)
    covered = {(x + i, y + j) for x, y, w, h in rects for i in range(w) for j in range(h)}
    assert covered == cells and sum(w * h for *_a, w, h in rects) == len(cells)


def test_addresses_past_column_z():
    assert m2c._address(0, 0) == "A1"
    assert m2c._address(26, 9) == "AA10"
    assert m2c._range(0, 0, 1, 1) == "A1" and m2c._range(0, 0, 3, 2) == "A1..C2"


def test_unknown_custom_terrain_is_refused_and_named():
    spec = _spec_map(terrain={"lava": {"cost": 1, "blocks_sight": False, "cover": 0}},
                     features=[{"type": "lava", "x": 0, "y": 0, "w": 2, "h": 2}])
    with pytest.raises(m2c.Refused) as exc:
        m2c.build_document(spec, "t")
    assert "'lava'" in str(exc.value) and "4 squares" in str(exc.value)
    text, _l, stats = m2c.build_document(spec, "t", custom_words={"lava": "mud"})
    assert "mud : area A1..B2" in text and stats["notes"]


def test_a_redefined_builtin_is_not_silently_given_the_stock_word():
    spec = _spec_map(terrain={"water": {"cost": 1, "blocks_sight": False, "cover": 0}},
                     features=[{"type": "water", "x": 0, "y": 0}])
    with pytest.raises(m2c.Refused):
        m2c.build_document(spec, "t")


def test_terrain_arg_parsing():
    assert m2c.parse_terrain_args(["finish=grass"]) == {"finish": "grass"}
    for bad in ("finish", "finish=", "x=Bad Word", "x=token"):
        with pytest.raises(ValueError):
            m2c.parse_terrain_args([bad])


def test_tokens_are_opt_in_and_flagged():
    spec = _spec_map()
    spec["spawns"] = [{"id": "K", "name": "Kairos", "color": "quan", "x": 1, "y": 2},
                      {"id": "f1", "name": "Giant frog", "color": "danger", "x": 0, "y": 0},
                      {"id": "f2", "name": "Giant frog", "color": "danger", "x": 2, "y": 0}]
    off, losses_off, _ = m2c.build_document(spec, "t")
    assert "[tokens]" not in off and any("not exported" in l for l in losses_off)
    on, losses_on, _ = m2c.build_document(spec, "t", tokens=True)
    assert 'kairos k "Kairos" : B3 side=quan' in on
    assert 'giant-frog f1 "Giant frog" : A1 side=danger' in on
    assert any("UVTT" in l for l in losses_on)


def test_token_words_never_collide_with_archetypes_or_each_other():
    spec = _spec_map()
    spec["spawns"] = [{"id": "a", "name": "Token", "x": 0, "y": 0},
                      {"id": "a", "name": "Token", "x": 1, "y": 0},
                      {"id": "goblin", "name": "Goblin", "x": 2, "y": 0}]
    lines = m2c._token_lines(spec["spawns"])
    assert lines[0].startswith("creature-token a ")
    ids = [l.split()[1] for l in lines]
    assert len(set(ids)) == 3 and lines[2].startswith("goblin goblin-1 ")


def test_the_diagonal_rule_loss_is_reported_for_both_rules():
    for rule in ("5", "5-10-5"):
        _t, losses, _s = m2c.build_document(_spec_map(diagonals=rule), "t")
        assert any("diagonal rule" in l and repr(rule) in l for l in losses)


def test_strings_cannot_break_the_line():
    spec = _spec_map(name='Bad "name"\nline', info="two\nlines; with semicolon")
    text, _l, _s = m2c.build_document(spec, "t")
    assert text.splitlines()[0] == "# Bad \"name\" line"
    assert "\n; two lines; with semicolon\n" in text


def test_cli_writes_utf8_file_and_reports_losses_loudly(tmp_path, capsys):
    maps = tmp_path / "maps"
    maps.mkdir()
    (maps / "room.json").write_text(json.dumps(_spec_map(name="Café")), encoding="utf-8")
    out = tmp_path / "out"
    assert m2c.main(["room", "--maps-dir", str(maps), "--out", str(out)]) == 0
    assert (out / "room.cd").read_text(encoding="utf-8").startswith("# Café\n")
    assert "LOSS the diagonal rule" in capsys.readouterr().err


def test_cli_refusal_writes_nothing_and_exits_nonzero(tmp_path, capsys):
    maps = tmp_path / "maps"
    maps.mkdir()
    spec = _spec_map(terrain={"lava": {"cost": 1, "blocks_sight": False, "cover": 0}},
                     features=[{"type": "lava", "x": 0, "y": 0}])
    (maps / "hot.json").write_text(json.dumps(spec), encoding="utf-8")
    assert m2c.main(["hot", "--maps-dir", str(maps), "--out", str(tmp_path / "o")]) == 1
    assert not (tmp_path / "o").exists()
    assert "REFUSED" in capsys.readouterr().err


def test_dry_run_writes_nothing(tmp_path, capsys):
    assert m2c.main(["frog-pond", "--terrain", "finish=grass", "--out", str(tmp_path / "o"),
                     "--dry-run"]) == 0
    assert not (tmp_path / "o").exists()
    assert "chartdown: 0.8" in capsys.readouterr().out


# --- the shipped maps -------------------------------------------------------------------

SHIPPED = sorted(p.stem for p in (ROOT / "display" / "maps").glob("*.json"))
CUSTOM = {"wood": "grass", "finish": "grass"}


@pytest.mark.parametrize("name", SHIPPED)
def test_every_shipped_map_exports_and_matches_its_grid(name):
    spec = json.loads((ROOT / "display" / "maps" / f"{name}.json").read_text(encoding="utf-8"))
    text, _l, _s = m2c.build_document(spec, name, tokens=True, custom_words=CUSTOM)
    grid = compile_map(spec)["grid"]
    painted = _painted(text)
    legend = {"#": "wall", ".": "floor", ",": "difficult", "~": "water", "^": "hazard",
              "o": "feature", "_": "void", **grid.get("legend", {})}
    words = dict(ENGINE_TO_WORD, **CUSTOM)
    for y, row in enumerate(grid["rows"]):
        for x, ch in enumerate(row):
            assert painted.get((x, y)) == (None if legend[ch] == "floor" else words[legend[ch]])


# --- optional oracle --------------------------------------------------------------------

def _oracle_available() -> bool:
    if not shutil.which("node"):
        return False
    probe = subprocess.run(["node", "-e", "import('@chartdown/core').then(()=>process.exit(0),()=>process.exit(1))"],
                           cwd=ROOT, capture_output=True, check=False)
    return probe.returncode == 0


requires_oracle = pytest.mark.skipif(
    not _oracle_available(), reason="dev-time oracle absent: needs node and `npm install` (@chartdown/core)")

_PARSE = ("import {parse} from '@chartdown/core'; import {readFileSync} from 'node:fs';"
          "const r=parse(readFileSync(0,'utf8')); console.log(JSON.stringify(r.diagnostics));")


def _diagnostics(text: str) -> list:
    done = subprocess.run(["node", "--input-type=module", "-e", _PARSE], cwd=ROOT, input=text.encode("utf-8"),
                          capture_output=True, check=True)
    return json.loads(done.stdout)


@requires_oracle
def test_oracle_rejects_a_bad_document_so_a_clean_result_means_something():
    assert _diagnostics("map: battlemap\ngrid: square 4x4\n[tokens]\ntoken : A1\n")


@requires_oracle
@pytest.mark.parametrize("name", SHIPPED)
def test_oracle_parses_every_shipped_map_without_diagnostics(name):
    spec = json.loads((ROOT / "display" / "maps" / f"{name}.json").read_text(encoding="utf-8"))
    text, _l, _s = m2c.build_document(spec, name, tokens=True, custom_words=CUSTOM)
    assert _diagnostics(text) == []
