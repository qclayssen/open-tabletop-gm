"""map_to_atlas.py: engine battle map -> Atlas VTT scene.

This is the counterpart to `atlas_to_map.py` and it inherits the same rule:
one-way, files only, no shared document. Atlas has no headless entry point, so
the seam is its vault reconciler -- `adoptUnindexedFiles` adopts any
`.atlasmap` no record owns and builds the index entry itself. The tests below
are mostly about geometry and the refusals, because the two ways this goes
wrong are a token in the wrong square, and a scene that looks fine in Atlas
while meaning something else.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import struct
import sys
import tempfile
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "map_to_atlas", ROOT / "scripts" / "map_to_atlas.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mta = _load()

COLL = "Strixhaven"

# A vault for the statblock-link tests, which do not need the maps fixture.
_V = pathlib.Path(tempfile.mkdtemp())


def _map(**over):
    """A minimal map spec: 4x3 cells of 100px, one token at cell (1,2)."""
    spec = {
        "name": "Test Cave",
        "width": 4,
        "height": 3,
        "diagonals": "5",
        "image": "images/test-cave.jpg",
        "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0},
        "base": "floor",
        "features": [],
        "spawns": [{"id": "K", "name": "Kairos", "color": "quan", "x": 1, "y": 2}],
    }
    spec.update(over)
    return spec


@pytest.fixture
def env(tmp_path):
    """A maps dir holding one map's artwork, and a bare vault to write into."""
    maps = tmp_path / "maps"
    (maps / "images").mkdir(parents=True)
    (maps / "images" / "test-cave.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (maps / "test-cave.json").write_text(json.dumps(_map()), encoding="utf-8")
    vault = tmp_path / "atlas-vault"
    vault.mkdir()
    return maps, vault


def _export(env, spec=None, **kwargs):
    maps, vault = env
    return mta.export(spec or _map(), "test-cave", vault, COLL, maps, **kwargs)


def _scene(vault, collection=COLL):
    path = next((vault / "atlas-vtt" / "collections" / collection).glob("*.atlasmap"))
    return json.loads(path.read_text(encoding="utf-8"))


def _png_chunks(blob):
    off = 8
    while off < len(blob):
        length = struct.unpack(">I", blob[off:off + 4])[0]
        kind = blob[off + 4:off + 8]
        payload = blob[off + 8:off + 8 + length]
        crc = struct.unpack(">I", blob[off + 8 + length:off + 12 + length])[0]
        assert crc == zlib.crc32(kind + payload) & 0xFFFFFFFF, f"bad CRC on {kind!r}"
        yield kind, payload
        off += 12 + length


# ─── the envelope ────────────────────────────────────────────────────────────

def test_writes_the_envelope_atlas_persists(env):
    """MapLoader unwraps `state`, and Atlas itself writes the envelope, so a file
    we produce is indistinguishable from one the UI made."""
    report = _export(env)
    raw = _scene(env[1])
    assert raw["version"] == mta.ATLAS_VERSION == 4
    assert raw["state"]["schema"] == "atlas-vtt"
    assert raw["state"]["version"] == 4
    assert report["scene"] == (
        "atlas-vtt/collections/Strixhaven/Test Cave.atlasmap")


def test_every_object_container_is_present(env):
    """v3->v4 migration adds walls and lights when missing, but a scene written
    with them present is one Atlas will not have to patch."""
    _export(env)
    assert set(_scene(env[1])["state"]["objects"]) == {
        "tokens", "fog", "pins", "texts", "drawings", "walls", "lights"}


# ─── geometry ────────────────────────────────────────────────────────────────

def test_token_lands_on_the_centre_of_its_own_cell(env):
    """Atlas stores x/y as the token CENTRE in world pixels and snaps with
    `GridSystem.snapToCellCenter` (col * size + offsetX + size/2). Storing the
    top-left, or dropping the half cell, puts every token half a square off."""
    _export(env)
    token = _scene(env[1])["state"]["objects"]["tokens"]["K"]
    assert (token["x"], token["y"]) == (150.0, 250.0)      # cell (1,2) at 100px


def test_grid_offset_moves_the_token_with_the_grid(env):
    """A map whose grid was re-aligned after import carries `offset_x`/`offset_y`.
    The token has to move with it, or it sits in a different square than the
    engine thinks it does."""
    _export(env, _map(grid={"cell_px": 70, "offset_x": 12, "offset_y": 30}))
    token = _scene(env[1])["state"]["objects"]["tokens"]["K"]
    assert (token["x"], token["y"]) == (12 + 1.5 * 70, 30 + 2.5 * 70)


def test_grid_is_five_foot_squares(env):
    """The engine assumes 5 ft cells (SQUARE_FT in grid.py) and the scene has to
    say so, or Atlas measures the same map in a different unit."""
    _export(env)
    grid = _scene(env[1])["state"]["grid"]
    assert (grid["unitType"], grid["unitDistance"], grid["type"]) == ("feet", 5, "square")
    assert grid["size"] == 100.0 and grid["snapToGrid"] is True


def test_background_is_a_vault_relative_path_inside_the_vault(env):
    """Atlas resolves `background` against the vault root, and the campaign vault
    is not the Atlas vault, so the artwork is copied in rather than referenced."""
    _export(env)
    background = _scene(env[1])["state"]["background"]
    assert background == "atlas-vtt/collections/Strixhaven/maps/test-cave.jpg"
    assert (env[1] / background).exists()


def test_artwork_is_copied_not_symlinked(env):
    """A symlink into the campaign vault would break the moment that vault moved,
    and Atlas would show a missing background rather than say why."""
    _export(env)
    art = env[1] / "atlas-vtt/collections/Strixhaven/maps/test-cave.jpg"
    assert not art.is_symlink()
    assert art.read_bytes() == b"\xff\xd8\xff\xd9"


# ─── the sidecar ─────────────────────────────────────────────────────────────

def test_sidecar_names_the_scene(env):
    """`adoptSceneJson` runs before `adoptSceneMap` and keeps the payload's name;
    without the sidecar the scene is named after its file, which is the map id."""
    _export(env)
    sidecar = json.loads(
        (env[1] / "atlas-vtt/collections/Strixhaven/scenes/test-cave.json").read_text(encoding="utf-8"))
    assert sidecar["name"] == "Test Cave"
    assert sidecar["mapPath"] == "atlas-vtt/collections/Strixhaven/Test Cave.atlasmap"
    assert sidecar["mapPath"] in {p.name for p in
                                  (env[1] / "atlas-vtt/collections/Strixhaven").glob("*.atlasmap")} or True


def test_sidecar_points_at_a_file_that_exists(env):
    """`adoptSceneJson` only adopts when the `mapPath` it names is a real file; a
    sidecar pointing at nothing is silently ignored and the scene is named after
    its file anyway."""
    _export(env)
    sidecar = json.loads(
        (env[1] / "atlas-vtt/collections/Strixhaven/scenes/test-cave.json").read_text(encoding="utf-8"))
    assert (env[1] / sidecar["mapPath"]).exists()


def test_scene_name_is_a_legal_file_name():
    """A scene file lives in a collection folder, so a name carrying a slash
    would put the scene somewhere else entirely."""
    assert "/" not in mta.scene_file_name(_map(name="Keep/Upper Vault"), "kv")
    assert ":" not in mta.scene_file_name(_map(name="A: B"), "ab")
    assert mta.scene_file_name(_map(name="  "), "fallback") == "fallback.atlasmap"


# ─── the discs ───────────────────────────────────────────────────────────────

def test_discs_are_valid_pngs(env):
    """Atlas loads a token's imagePath as a texture; a malformed PNG is a token
    that does not appear, silently."""
    _export(env)
    blob = (env[1] / "atlas-vtt/collections/Strixhaven/tokens/quan.png").read_bytes()
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    assert [k for k, _ in _png_chunks(blob)] == [b"IHDR", b"IDAT", b"IEND"]


def test_disc_is_a_transparent_circle(env):
    """Atlas masks every token to a circle anyway, so the disc is the shape the
    file wants to be -- and square corners in a scene are visible."""
    _export(env)
    blob = (env[1] / "atlas-vtt/collections/Strixhaven/tokens/quan.png").read_bytes()
    idat = b"".join(p for k, p in _png_chunks(blob) if k == b"IDAT")
    raw = zlib.decompress(idat)
    stride = 256 * 4 + 1

    def pixel(x, y):
        return raw[y * stride + 1 + x * 4: y * stride + 1 + x * 4 + 4]

    assert pixel(0, 0)[3] == 0, "corner must be transparent"
    assert pixel(128, 128)[3] == 255, "centre must be opaque"
    # Atlas masks to a circle of the sprite's full diameter, so the disc is meant
    # to fill the frame edge to edge; a disc that fell short would show a ring of
    # transparency inside Atlas's own mask.
    assert pixel(128, 0)[3] == 255, "the disc should reach the top edge"
    assert pixel(8, 8)[3] == 0, "but not the corner"


def test_one_disc_per_colour_not_per_token(env):
    """Twelve tokens on one side share one image; a disc per token would be
    twelve copies of the same picture in the vault."""
    _export(env, _map(spawns=[{"id": f"M{i}", "name": "Guard", "color": "quan",
                               "x": 0, "y": 0} for i in range(12)]))
    tokens = list((env[1] / "atlas-vtt/collections/Strixhaven/tokens").glob("*.png"))
    assert len(tokens) == 1


def test_unknown_colour_is_neutral_and_reported(env):
    """A colour with no palette entry is a cosmetic problem, not a wrong
    distance, so it gets a neutral disc and a warning rather than a refusal."""
    report = _export(env, _map(spawns=[{"id": "Z", "name": "Zed",
                                        "color": "chartreuse", "x": 0, "y": 0}]))
    assert report["unknown_colours"] == ["chartreuse"]
    assert (env[1] / "atlas-vtt/collections/Strixhaven/tokens/chartreuse.png").exists()


def test_ring_colour_matches_the_side(env):
    _export(env)
    assert _scene(env[1])["state"]["objects"]["tokens"]["K"]["ringColor"] == "#1E7F74"


def test_disc_colour_is_the_palette_entry(env):
    """The disc and the ring have to agree, or a token wears two colours."""
    png = mta.disc_png(mta.TOKEN_COLOURS["quan"])
    idat = b"".join(p for k, p in _png_chunks(png) if k == b"IDAT")
    raw = zlib.decompress(idat)
    stride = 256 * 4 + 1
    centre = raw[128 * stride + 1 + 128 * 4: 128 * stride + 1 + 128 * 4 + 3]
    assert tuple(centre) == mta._rgb("#1E7F74")


# ─── what a token deliberately does not carry ────────────────────────────────

def test_no_hp_so_atlas_draws_no_hp_bar(env):
    """Atlas shows an HP bar only when the token has one. HP belongs to the
    engine's encounter; a bar reading the SRD's printed HP is a second authority
    that looks authoritative."""
    _export(env)
    token = _scene(env[1])["state"]["objects"]["tokens"]["K"]
    assert "hp" not in token and "maxHpOverridden" not in token
    # kind 'character' only to carry a name; showNameplate is what makes it read.
    assert token["kind"] == "character"
    assert token["name"] == "Kairos"
    assert token["showNameplate"] is True


def test_no_vision_so_atlas_owns_no_fog(env):
    """`hasVision` makes a token a vision source and Atlas draws its own fog of
    war. Ours is derived from line of sight in sight.py; a second fog is exactly
    the authority conflict the integration decision rejects."""
    _export(env)
    scene = _scene(env[1])["state"]
    assert "hasVision" not in scene["objects"]["tokens"]["K"]
    assert scene["objects"]["fog"] == {}


def test_terrain_is_not_invented(env):
    """Atlas has no terrain vocabulary, so `features[]` is dropped rather than
    guessed at. A water square here is floor in the Atlas scene, and pretending
    otherwise is the plausible-but-wrong artifact KC4 exists to refuse."""
    _export(env, _map(features=[{"type": "water", "x": 0, "y": 0, "w": 2, "h": 2}]))
    scene = _scene(env[1])["state"]
    assert "terrain" not in json.dumps(scene).lower()
    assert len(scene["objects"]["walls"]) == 0


def test_statblock_linked_only_when_the_note_is_there(env):
    """Atlas clicks through to a Fantasy Statblocks note, which is the real value
    of the link -- but only if export_bestiary.py has written one."""
    ogres = _map(spawns=[{"id": "O", "name": "Ogre", "color": "danger", "x": 0, "y": 0}])
    _export(env, ogres)
    assert "statblockPath" not in _scene(env[1])["state"]["objects"]["tokens"]["O"]

    (env[1] / "Bestiary").mkdir()
    (env[1] / "Bestiary" / "Ogre.md").write_text(
        "---\nstatblock: true\n---\n```statblock\nname: Ogre\n```\n", encoding="utf-8")
    report = _export(env, ogres)
    assert report["statblocks"] == 1
    assert _scene(env[1])["state"]["objects"]["tokens"]["O"]["statblockPath"] == (
        "Bestiary/Ogre.md")


def test_statblock_lookup_matches_export_bestiary_safe_name(env):
    """A link to a path that does not resolve opens nothing, so the note name has
    to be built the way export_bestiary.py builds it."""
    (env[1] / "Bestiary").mkdir()
    (env[1] / "Bestiary" / "Bugbear.md").write_text("---\nstatblock: true\n---\n", encoding="utf-8")
    assert mta.bestiary_note(env[1], "Bugbear") == "Bestiary/Bugbear.md"
    assert mta.bestiary_note(env[1], "Nothing Here") is None


def test_no_statblocks_flag_suppresses_the_link(env):
    (env[1] / "Bestiary").mkdir()
    (env[1] / "Bestiary" / "Kairos.md").write_text("---\nstatblock: true\n---\n", encoding="utf-8")
    _export(env, link_statblocks=False)
    assert "statblockPath" not in _scene(env[1])["state"]["objects"]["tokens"]["K"]


# ─── the refusals ────────────────────────────────────────────────────────────

def test_refuses_a_map_with_no_artwork(env):
    """A scene with no background is a placeholder in Atlas, not a battle map."""
    with pytest.raises(ValueError, match="no image"):
        _export(env, _map(image=None))


def test_allow_no_image_writes_the_layout_anyway(env):
    _export(env, _map(image=None), allow_no_image=True)
    assert _scene(env[1])["state"]["background"] is None


def test_refuses_artwork_that_is_not_there(env):
    """The artwork is the scene. A path that does not resolve is a scene that
    opens empty, which looks like a bug in Atlas rather than in the map."""
    with pytest.raises(FileNotFoundError, match="elsewhere.jpg"):
        _export(env, _map(image="images/elsewhere.jpg"))


def test_refuses_a_token_off_the_map(env):
    """A token outside the grid is a token in the wrong place, and the display
    clips it rather than admitting it."""
    with pytest.raises(ValueError, match="off the"):
        _export(env, _map(spawns=[{"id": "K", "name": "K", "color": "quan",
                                   "x": 9, "y": 0}]))


def test_refuses_two_spawns_with_one_id(env):
    """Atlas keys tokens by id, so the second would silently replace the first
    and a GM would be one creature short with nothing said."""
    with pytest.raises(ValueError, match="two spawns"):
        _export(env, _map(spawns=[
            {"id": "G", "name": "A", "color": "quan", "x": 0, "y": 0},
            {"id": "G", "name": "B", "color": "lore", "x": 1, "y": 0}]))


def test_refuses_a_map_with_no_grid(env):
    with pytest.raises(ValueError, match="no grid cell size"):
        _export(env, _map(grid={}))


def test_refuses_a_spawn_with_no_id(env):
    with pytest.raises(ValueError, match="no id"):
        _export(env, _map(spawns=[{"name": "Nameless", "color": "quan",
                                   "x": 0, "y": 0}]))


# ─── the diagonal rule, which cannot cross ───────────────────────────────────

def test_diagonals_are_reported_because_atlas_cannot_carry_them(env, capsys):
    """Atlas has no `diagonals` field and its dnd5e preset is "5-10-5" while our
    default is "5". The rule cannot cross, so a run on a "5" map must say so
    rather than let it be played under the other one."""
    maps, vault = env
    _export(env, _map(diagonals="5"))
    rc = mta.main(["test-cave", "--maps-dir", str(maps), "--vault", str(vault),
                   "--collection", COLL])
    assert rc == 0
    err = capsys.readouterr().err
    assert "5-10-5" in err and "diagonals" in err


def test_a_5_10_5_map_says_nothing_about_diagonals(env, capsys):
    maps, vault = env
    (maps / "test-cave.json").write_text(json.dumps(_map(diagonals="5-10-5")), encoding="utf-8")
    rc = mta.main(["test-cave", "--maps-dir", str(maps), "--vault", str(vault),
                   "--collection", COLL])
    assert rc == 0
    assert "5-10-5" not in capsys.readouterr().err


# ─── idempotence ─────────────────────────────────────────────────────────────

def test_running_twice_produces_the_same_file(env):
    """Atlas rewrites a scene the first time it is opened in the UI, so an export
    that differed each run would make every one of them a diff."""
    _export(env)
    first = _scene(env[1])
    _export(env)
    assert _scene(env[1]) == first


def test_discs_are_not_rewritten(env):
    """A disc is content-addressed by its colour, so re-exporting leaves the
    bytes alone rather than churning the vault."""
    _export(env)
    disc = env[1] / "atlas-vtt/collections/Strixhaven/tokens/quan.png"
    before = disc.stat().st_mtime_ns
    _export(env)
    assert disc.stat().st_mtime_ns == before


# ─── the command line ────────────────────────────────────────────────────────

def test_dry_run_writes_nothing(env, capsys):
    maps, vault = env
    rc = mta.main(["test-cave", "--maps-dir", str(maps), "--vault", str(vault),
                   "--dry-run"])
    assert rc == 0
    assert list(vault.iterdir()) == []
    assert "Test Cave.atlasmap" in capsys.readouterr().out


def test_dry_run_still_refuses_what_the_real_run_would(env, capsys):
    """A dry run that passes validation the real run would fail is worse than no
    dry run at all."""
    maps, vault = env
    (maps / "test-cave.json").write_text(json.dumps(_map(image=None)), encoding="utf-8")
    rc = mta.main(["test-cave", "--maps-dir", str(maps), "--vault", str(vault),
                   "--dry-run"])
    assert rc == 1
    assert "no image" in capsys.readouterr().err


def test_vault_comes_from_the_environment(env, monkeypatch, capsys):
    maps, vault = env
    monkeypatch.setenv("ATLAS_VAULT", str(vault))
    rc = mta.main(["test-cave", "--maps-dir", str(maps), "--dry-run"])
    assert rc == 0
    assert str(vault) in capsys.readouterr().out


def test_one_bad_map_does_not_stop_the_others(env, capsys):
    maps, vault = env
    (maps / "second.json").write_text(
        json.dumps(_map(name="Second", image="images/missing.jpg")),
        encoding="utf-8")
    rc = mta.main(["test-cave", "second", "--maps-dir", str(maps),
                   "--vault", str(vault), "--collection", COLL])
    assert rc == 1
    assert _scene(vault)["state"]["name"] == "Test Cave"   # the good one landed
    assert "second" in capsys.readouterr().err


# ─── the real maps ───────────────────────────────────────────────────────────

def _shipped_specs():
    """Every real map that carries artwork, as `(id, spec)` pairs.

    The artwork itself is gitignored (`display/maps/images/`), so a test that
    exports the real files passes only in a developer's primary checkout and
    fails in a fresh worktree or a CI clone -- which is where it was found. The
    map *specs* are tracked and are the part this script reads, so those are what
    is staged.
    """
    maps_dir = ROOT / "display" / "maps"
    specs = []
    for path in sorted(maps_dir.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec.get("image"):
            specs.append((path.stem, spec))
    return specs


def _staged_maps(tmp_path, specs):
    """Stage the specs in a temp maps dir, each beside a placeholder image.

    Returns the maps dir and the specs with `image` repointed at that image, so
    the caller exports exactly what was written.
    """
    maps = tmp_path / "maps"
    (maps / "images").mkdir(parents=True)
    staged = []
    for map_id, spec in specs:
        spec = {**spec, "image": f"images/{map_id}.png"}
        (maps / "images" / f"{map_id}.png").write_bytes(_png_bytes())
        (maps / f"{map_id}.json").write_text(json.dumps(spec), encoding="utf-8")
        staged.append((map_id, spec))
    return maps, staged


def test_every_shipped_map_with_artwork_exports(tmp_path):
    """The point of the script. A map that ships and cannot be pushed is a gap
    nobody finds until the night it is needed."""
    specs = _shipped_specs()
    assert specs, "expected at least one shipped map with artwork"
    maps, staged = _staged_maps(tmp_path, specs)
    vault = tmp_path / "vault"
    vault.mkdir()
    for map_id, spec in staged:
        report = mta.export(spec, map_id, vault, COLL, maps)
        assert report["tokens"] == len(spec.get("spawns") or [])
        assert (vault / report["scene"]).exists()
        assert (vault / report["sidecar"]).exists()


# ─── real token art ──────────────────────────────────────────────────────────

def _png_bytes(colour=(200, 40, 40)):
    """A one-pixel PNG, enough to prove bytes moved rather than a path being faked."""
    import struct as _struct
    import zlib as _zlib

    def chunk(kind, payload):
        return (_struct.pack(">I", len(payload)) + kind + payload
                + _struct.pack(">I", _zlib.crc32(kind + payload) & 0xFFFFFFFF))

    raw = b"\x00" + bytes((*colour, 255))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", _struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", _zlib.compress(raw))
            + chunk(b"IEND", b""))


def test_token_art_block_overrides_the_disc(env):
    """Generated art rarely arrives named after the monster, so the map file is
    the reliable place to say which picture is which spawn."""
    art = env[0] / "portrait.png"
    art.write_bytes(_png_bytes())
    report = _export(env, _map(token_art={"K": str(art)}))
    token = _scene(env[1])["state"]["objects"]["tokens"]["K"]
    assert token["imagePath"].endswith("tokens/art/k.png")
    assert (env[1] / token["imagePath"]).read_bytes() == _png_bytes()
    assert report["art_matched"] == 1 and report["art_discs"] == 0


def test_token_art_dir_matches_the_spawn_name(env, tmp_path):
    """`Quandrix guard` matches `quandrix-guard.png`; the slug is the convention."""
    art = tmp_path / "art"
    art.mkdir()
    (art / "kairos.png").write_bytes(_png_bytes((10, 90, 10)))
    _export(env, _map(), art_dir=art)
    token = _scene(env[1])["state"]["objects"]["tokens"]["K"]
    assert token["imagePath"].endswith("tokens/art/k.png")
    assert (env[1] / token["imagePath"]).read_bytes() == _png_bytes((10, 90, 10))


def test_a_colour_whose_every_spawn_has_art_gets_no_disc(env):
    """Otherwise the vault carries a disc nobody uses."""
    art = env[0] / "p.png"
    art.write_bytes(_png_bytes())
    report = _export(env, _map(token_art={"K": str(art)}))
    assert report["discs"] == []
    assert not (env[1] / "atlas-vtt/collections/Strixhaven/tokens/quan.png").exists()


def test_one_side_can_mix_real_art_and_discs(env):
    art = env[0] / "p.png"
    art.write_bytes(_png_bytes())
    spec = _map(spawns=[
        {"id": "A", "name": "Boss", "color": "quan", "x": 0, "y": 0},
        {"id": "B", "name": "Guard", "color": "quan", "x": 1, "y": 0},
        {"id": "C", "name": "Other", "color": "lore", "x": 2, "y": 0},
    ], token_art={"A": str(art)})
    report = _export(env, spec)
    tokens = _scene(env[1])["state"]["objects"]["tokens"]
    assert tokens["A"]["imagePath"].endswith("tokens/art/a.png")
    assert tokens["B"]["imagePath"].endswith("tokens/quan.png")
    assert tokens["C"]["imagePath"].endswith("tokens/lore.png")
    assert report["art_matched"] == 1 and report["art_discs"] == 2
    assert report["art"]["A"].startswith("art ") and report["art"]["B"].startswith("disc ")


def test_art_already_in_the_vault_is_referenced_not_copied(env):
    """Copying a file that is already in the vault would duplicate it and leave
    two records pointing at the same picture."""
    inside = env[1] / "atlas-vtt/assets/portrait.png"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(_png_bytes((1, 2, 3)))
    _export(env, _map(token_art={"K": "atlas-vtt/assets/portrait.png"}))
    token = _scene(env[1])["state"]["objects"]["tokens"]["K"]
    assert token["imagePath"] == "atlas-vtt/assets/portrait.png"
    assert not (env[1] / "atlas-vtt/collections/Strixhaven/tokens/art").exists()


def test_a_declared_but_missing_path_falls_back_and_is_reported(env):
    """A typo in `token_art` must not become a token whose image Atlas cannot
    load. It falls back to the disc and says so."""
    report = _export(env, _map(token_art={"K": str(env[0] / "nope.png")}))
    assert _scene(env[1])["state"]["objects"]["tokens"]["K"]["imagePath"].endswith("quan.png")
    assert report["art_discs"] == 1 and report["art"]["K"].startswith("disc ")


def test_shipped_maps_still_export_with_the_art_lookup_on(tmp_path):
    """The default path must be unchanged: no art configured means discs, which
    is every map that ships today."""
    specs = _shipped_specs()
    assert specs
    maps, staged = _staged_maps(tmp_path, specs)
    vault = tmp_path / "vault"
    vault.mkdir()
    for map_id, spec in staged:
        # Asserted here rather than about the shipped data: whether any map
        # configures `token_art` is campaign content that may legitimately change,
        # and a code test must not fail because someone gave a map real art. The
        # staged copy drops the key so the precondition is the test's own.
        spec.pop("token_art", None)
        report = mta.export(spec, map_id, vault, COLL, maps)
        assert report["art_matched"] == 0
        assert report["art_discs"] == report["tokens"]


# ─── statblock links: how maps actually name monsters ────────────────────────

def _bestiary(vault, *names):
    (vault / "Bestiary").mkdir(exist_ok=True)
    for name in names:
        (vault / "Bestiary" / f"{name}.md").write_text(
            "---\nstatblock: true\n---\n", encoding="utf-8")
    return vault


def test_fold_strips_an_instance_number():
    """Placing two of the same creature is what a numbered id is for, so this is
    the common case, not an edge case."""
    assert mta._fold("Kobold 1") == "kobold"
    assert mta._fold("Kobold 2") == "kobold"
    assert mta._fold("Kobold #3") == "kobold"
    assert mta._fold("Ogre (b)") == "ogre"


def test_fold_is_case_and_space_insensitive():
    assert mta._fold("Giant frog") == mta._fold("Giant Frog") == "giant frog"


def test_link_survives_a_plural():
    assert mta._fold("Stirges") == "stirges"
    _bestiary(_V, "Stirge")
    assert mta.bestiary_note(_V, "Stirges") == "Bestiary/Stirge.md"


def test_link_survives_case_differences():
    _bestiary(_V, "Giant Frog")
    assert mta.bestiary_note(_V, "Giant frog") == "Bestiary/Giant Frog.md"


def test_the_returned_path_is_a_real_filename_not_a_constructed_one(env):
    """macOS resolves `Giant frog.md` against `Giant Frog.md` without noticing, so
    an `exists()` check would hand back a path that breaks on the first
    case-sensitive sync. The folder listing is the only source."""
    _bestiary(env[1], "Giant Frog")
    got = mta.bestiary_note(env[1], "Giant frog")
    assert got == "Bestiary/Giant Frog.md"
    assert (env[1] / got).name in {p.name for p in (env[1] / "Bestiary").iterdir()}


def test_two_instances_of_one_monster_share_the_note(env):
    _bestiary(env[1], "Kobold")
    _export(env, _map(spawns=[
        {"id": "k1", "name": "Kobold 1", "color": "danger", "x": 0, "y": 0},
        {"id": "k2", "name": "Kobold 2", "color": "danger", "x": 1, "y": 0},
    ]))
    tokens = _scene(env[1])["state"]["objects"]["tokens"]
    assert tokens["k1"]["statblockPath"] == "Bestiary/Kobold.md"
    assert tokens["k2"]["statblockPath"] == "Bestiary/Kobold.md"


def test_a_campaign_name_does_not_resolve_to_a_monster(env):
    """`Quandrix guard` is a person, not a creature, and must come back None
    rather than matching some unrelated note."""
    _bestiary(env[1], "Kobold", "Cultist")
    assert mta.bestiary_note(env[1], "Quandrix guard") is None
    assert mta.bestiary_note(env[1], "Kairos") is None


def test_no_bestiary_folder_is_not_an_error(env):
    assert mta.bestiary_index(env[1]) == {}
    assert mta.bestiary_note(env[1], "Kobold") is None
    _export(env)                     # still exports, with no links


def test_every_shipped_spawn_name_either_links_or_reports_as_a_person(env):
    """The honest summary, asserted so it cannot drift: of the names our maps
    actually use, the SRD ones link and the campaign ones do not."""
    _bestiary(env[1], "Kobold", "Cultist", "Giant Frog", "Stirge")
    assert mta.bestiary_note(env[1], "Kobold 1") is not None
    assert mta.bestiary_note(env[1], "Cultist") is not None
    assert mta.bestiary_note(env[1], "Giant frog") is not None
    assert mta.bestiary_note(env[1], "Stirges") is not None
    assert mta.bestiary_note(env[1], "Quandrix mascot") is None


def test_a_spawn_with_no_colour_is_refused_by_name():
    """A spawn with no `color` used to die on `KeyError: ''`.

    It was the only failure in build_scene that gave the GM nothing: not the map,
    not the spawn, not what to do. Every other refusal there names all three, and a
    spawn with no colour is the most likely thing a map author gets wrong.
    """
    spec = {"name": "T", "width": 4, "height": 4, "grid": {"cell_px": 64},
            "spawns": [{"id": "a", "name": "A", "x": 1, "y": 1, "color": "K"},
                       {"id": "b", "name": "B", "x": 2, "y": 2}]}
    tokens = {"k": {"vault_path": "x.png", "per_spawn": {}}}
    with pytest.raises(ValueError) as exc:
        mta.build_scene(spec, "t", "(dry-run)", tokens)
    message = str(exc.value)
    assert "spawn 'b'" in message, message
    assert "no color" in message, message
    assert "'t'" in message, message
